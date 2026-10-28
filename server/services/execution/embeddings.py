"""Optional search by meaning: a small local embedding model, used next to keyword search.

BM25 can't link "replacing everyone's old computers" to "Ironbridge Laptop Refresh": they share
no word. A sentence embedding model can. Each agent's text (name, summary, search phrases) is
turned into a vector once and stored in SQLite next to the registry, with a hash of the text,
so a vector is only recomputed when that text changes. A message is embedded per turn and
compared with every agent by cosine similarity (brute force with numpy; a few hundred to a few
thousand agents fit in memory easily).

The dependency is optional. Without `fastembed` (pip install -r server/requirements-embeddings.txt),
or with OPENPOKE_AGENT_RETRIEVER=bm25, `get_embedder()` returns None and retrieval is keyword only,
exactly as before. Similarity only helps an agent get shown to the model. It never merges agents:
the resolver uses keyword search and exact entities only.
"""

from __future__ import annotations

import functools
import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from ...logging_config import logger
from .registry import AgentRecord

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# bge's recommended instruction for short queries against longer passages (fastembed doesn't add it)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
PHRASE_KIND = "phrase"  # same value as phrases.PHRASE_KIND; not imported to keep this module standalone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_embeddings (
    agent_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL
);
"""


class Embedder(Protocol):
    """Anything that turns texts into unit-length float32 vectors (rows of a numpy array)."""

    model_name: str

    def embed_documents(self, texts: Sequence[str]): ...

    def embed_query(self, text: str): ...


def agent_text(record: AgentRecord) -> str:
    """What gets embedded for an agent: its name, its summary and its search phrases."""

    parts = [record.name, record.summary]
    phrases = record.entities.get(PHRASE_KIND, [])
    if phrases:
        parts.append("; ".join(phrases))
    return ". ".join(part.strip() for part in parts if part and part.strip())


def text_hash(model_name: str, text: str) -> str:
    # The model is part of the hash, so switching models recomputes every vector
    return hashlib.sha256(f"{model_name}\n{text}".encode("utf-8")).hexdigest()[:32]


class FastEmbedEmbedder:
    """bge-small through fastembed (ONNX Runtime on CPU, no torch). Loaded on first use."""

    def __init__(self, model_name: str = DEFAULT_MODEL, *, cache_dir: Optional[str] = None,
                 local_files_only: bool = False, threads: Optional[int] = None, query_prefix: str = QUERY_PREFIX):
        from fastembed import TextEmbedding  # ImportError here means the optional extra isn't installed

        self.query_prefix = query_prefix
        started = time.perf_counter()
        kwargs = {"cache_dir": cache_dir, "threads": threads}
        if local_files_only:
            kwargs["local_files_only"] = True
        self._model = TextEmbedding(model_name, **kwargs)
        self.model_name = model_name
        self.load_seconds = time.perf_counter() - started

    def embed_documents(self, texts: Sequence[str]):
        import numpy as np

        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return _normalise(np.asarray(list(self._model.passage_embed(list(texts))), dtype=np.float32))

    def embed_query(self, text: str):
        # The shortlist and search_agents often embed the same message in one turn
        return self._cached_query(text)

    @functools.lru_cache(maxsize=256)
    def _cached_query(self, text: str):
        import numpy as np

        return _normalise(np.asarray(list(self._model.query_embed([self.query_prefix + text])), dtype=np.float32))[0]


def _normalise(matrix):
    import numpy as np

    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


_embedder_lock = threading.Lock()
_embedder: Optional[Embedder] = None
_embedder_failed = False


def get_embedder() -> Optional[Embedder]:
    """The shared embedder, or None when fastembed or the model can't load.

    The retriever only calls this in hybrid mode, so with OPENPOKE_AGENT_RETRIEVER=bm25 nothing loads.

    A failure is logged once and not retried, so a missing package or model never slows replies.
    """

    global _embedder, _embedder_failed
    from ...config import get_settings

    if _embedder is not None or _embedder_failed:
        return _embedder
    settings = get_settings()
    with _embedder_lock:
        if _embedder is None and not _embedder_failed:
            try:
                _embedder = FastEmbedEmbedder(
                    settings.agent_embedding_model, cache_dir=settings.agent_embedding_cache_dir or None
                )
                logger.info(f"Loaded embedding model {settings.agent_embedding_model} in {_embedder.load_seconds:.1f}s")
            except ImportError:
                _embedder_failed = True
                logger.info("fastembed is not installed; agent search is keyword only")
            except Exception as exc:  # no network on first use, bad cache, ...
                _embedder_failed = True
                logger.warning(f"Could not load embedding model, agent search is keyword only: {exc}")
    return _embedder


def set_embedder(embedder: Optional[Embedder]) -> None:
    """Replace the shared embedder (tests, evals). None resets it so the next call loads again."""

    global _embedder, _embedder_failed
    with _embedder_lock:
        _embedder = embedder
        _embedder_failed = False


class EmbeddingStore:
    """Agent vectors in the registry's SQLite file, keyed by agent id, reused while the text is unchanged."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self.embedded = 0  # vectors computed by this store (tests and timing use it)
        self._memory: Dict[str, Tuple[str, object]] = {}  # agent_id -> (text hash, vector), saves a read per turn
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=30, isolation_level=None)

    def vectors(self, records: Sequence[AgentRecord], embedder: Embedder):
        """One unit vector per record, in order. Embeds only agents whose text is new or changed."""

        import numpy as np

        model = embedder.model_name
        wanted: List[Tuple[str, str, str]] = []  # (agent_id, hash, text)
        for record in records:
            text = agent_text(record)
            wanted.append((record.agent_id, text_hash(model, text), text))

        vectors: Dict[str, "np.ndarray"] = {}
        unknown = []
        for agent_id, digest, text in wanted:
            hit = self._memory.get(agent_id)
            if hit is not None and hit[0] == digest:
                vectors[agent_id] = hit[1]
            else:
                unknown.append((agent_id, digest, text))

        missing: List[Tuple[str, str, str]] = []
        if unknown:
            # Vectors written earlier (another process, or before a restart) are read, not recomputed
            stored: Dict[str, Tuple[str, bytes, int]] = {}
            with self._lock, closing(self._connect()) as conn:
                for row in conn.execute(
                    "SELECT agent_id, text_hash, vector, dim FROM agent_embeddings WHERE model = ?", (model,)
                ):
                    stored[row[0]] = (row[1], row[2], row[3])
            for agent_id, digest, text in unknown:
                hit = stored.get(agent_id)
                if hit and hit[0] == digest:
                    vectors[agent_id] = np.frombuffer(hit[1], dtype=np.float32, count=hit[2])
                    self._memory[agent_id] = (digest, vectors[agent_id])
                else:
                    missing.append((agent_id, digest, text))

        if missing:
            fresh = embedder.embed_documents([text for _, _, text in missing])
            self.embedded += len(missing)
            rows = []
            for (agent_id, digest, _text), vector in zip(missing, fresh):
                vector = np.asarray(vector, dtype=np.float32)
                vectors[agent_id] = vector
                self._memory[agent_id] = (digest, vector)
                rows.append((agent_id, model, digest, int(vector.shape[0]), vector.tobytes()))
            with self._lock, closing(self._connect()) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO agent_embeddings (agent_id, model, text_hash, dim, vector)"
                    " VALUES (?, ?, ?, ?, ?)",
                    rows,
                )

        if not wanted:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack([vectors[agent_id] for agent_id, _, _ in wanted])

    def similarities(self, query: str, records: Sequence[AgentRecord], embedder: Embedder) -> Dict[str, float]:
        """Cosine similarity of the query with each record, by agent name."""

        if not records or not (query or "").strip():
            return {}
        matrix = self.vectors(records, embedder)
        scores = matrix @ embedder.embed_query(query)
        return {record.name: float(score) for record, score in zip(records, scores)}


_stores: Dict[str, EmbeddingStore] = {}
_stores_lock = threading.Lock()


def store_for(db_path: Path) -> EmbeddingStore:
    """One store per registry file, so the schema check runs once per process."""

    key = str(Path(db_path).resolve())
    with _stores_lock:
        if key not in _stores:
            _stores[key] = EmbeddingStore(Path(db_path))
        return _stores[key]


__all__ = [
    "DEFAULT_MODEL",
    "Embedder",
    "EmbeddingStore",
    "FastEmbedEmbedder",
    "agent_text",
    "get_embedder",
    "set_embedder",
    "store_for",
    "text_hash",
]
