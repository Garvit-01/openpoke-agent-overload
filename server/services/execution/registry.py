"""Structured registry of execution agents backed by SQLite.

The original roster was a JSON list of names. The registry keeps, per agent, a short
summary, the entities it has touched (emails, thread ids, people, subjects), when it
was last active, and a lifecycle status. That is what makes retrieval possible.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ...logging_config import logger
from .entities import merge_entities, normalize_agent_name
from .ids import agent_id_for

STATUS_ACTIVE = "active"
STATUS_DORMANT = "dormant"
STATUS_ARCHIVED = "archived"
ALL_STATUSES = (STATUS_ACTIVE, STATUS_DORMANT, STATUS_ARCHIVED)

Clock = Callable[[], datetime]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    name_key TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agents_name_key ON agents (name_key);
CREATE INDEX IF NOT EXISTS idx_agents_last_active ON agents (last_active_at);
CREATE TABLE IF NOT EXISTS agent_entities (
    agent_name TEXT NOT NULL REFERENCES agents (name) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (agent_name, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_agent_entities_lookup ON agent_entities (kind, value);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Serialise a datetime as an ISO string in UTC (naive values are treated as UTC)."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_timestamp(value: str) -> datetime:
    """Parse a stored ISO timestamp into an aware UTC datetime."""

    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


@dataclass
class AgentRecord:
    """Everything the router needs to know about one execution agent."""

    name: str
    summary: str = ""
    entities: Dict[str, List[str]] = field(default_factory=dict)
    status: str = STATUS_ACTIVE
    created_at: str = ""
    last_active_at: str = ""
    run_count: int = 0

    @property
    def last_active(self) -> datetime:
        return parse_timestamp(self.last_active_at)

    @property
    def agent_id(self) -> str:
        """Stable id derived from the exact name; it keys the agent's log file."""
        return agent_id_for(self.name)


class AgentRegistry:
    """SQLite store for agent records. Safe across threads and works on Windows."""

    def __init__(self, db_path: Path, *, clock: Optional[Clock] = None):
        self._db_path = Path(db_path)
        self._clock: Clock = clock or _utc_now
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            self._refresh_name_keys(conn)

    # Keys saved by an older normalize_agent_name (before plurals were folded) would miss matches
    @staticmethod
    def _refresh_name_keys(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name, name_key FROM agents").fetchall()
        stale = [(key, row["name"]) for row in rows if (key := normalize_agent_name(row["name"])) != row["name_key"]]
        if stale:
            conn.executemany("UPDATE agents SET name_key = ? WHERE name = ?", stale)
            logger.info("agent name keys refreshed", extra={"count": len(stale)})

    @property
    def db_path(self) -> Path:
        return self._db_path

    def now(self) -> datetime:
        return self._clock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # Writes ------------------------------------------------------------------

    # Create the agent if missing, otherwise mark it active again (this is also "revive")
    def ensure_agent(self, name: str, *, at: Optional[datetime] = None) -> Tuple[AgentRecord, bool]:
        """Create the agent if missing; otherwise mark it active and touch last_active.

        Returns the record and whether it was created. Reusing an archived agent revives it.
        """

        stamp = to_iso(at or self.now())
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT name FROM agents WHERE name = ?", (name,)).fetchone()
            created = row is None
            if created:
                conn.execute(
                    "INSERT INTO agents (name, name_key, status, created_at, last_active_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, normalize_agent_name(name), STATUS_ACTIVE, stamp, stamp),
                )
            else:
                conn.execute(
                    "UPDATE agents SET status = ?, last_active_at = MAX(last_active_at, ?)"
                    " WHERE name = ?",
                    (STATUS_ACTIVE, stamp, name),
                )
        record = self.get(name)
        assert record is not None
        return record, created

    # Replace the summary and merge newly seen entities into an agent's profile
    def update_profile(
        self,
        name: str,
        *,
        summary: Optional[str] = None,
        entities: Optional[Dict[str, List[str]]] = None,
        count_run: bool = False,
    ) -> None:
        """Set the summary and merge new entities into an existing agent."""

        with self._lock, closing(self._connect()) as conn:
            if summary is not None:
                conn.execute("UPDATE agents SET summary = ? WHERE name = ?", (summary, name))
            if count_run:
                conn.execute("UPDATE agents SET run_count = run_count + 1 WHERE name = ?", (name,))
            for kind, values in (entities or {}).items():
                conn.executemany(
                    "INSERT OR IGNORE INTO agent_entities (agent_name, kind, value) VALUES (?, ?, ?)",
                    [(name, kind, value) for value in values if value],
                )

    def set_status(self, name: str, status: str) -> None:
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown status: {status}")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("UPDATE agents SET status = ? WHERE name = ?", (status, name))

    # Bulk insert full records (used by migration and the evaluator); existing names are skipped
    def import_records(self, records: Iterable[AgentRecord]) -> int:
        """Bulk insert records as-is. Returns how many were inserted."""

        inserted = 0
        default_stamp = to_iso(self.now())
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN")
            try:
                for record in records:
                    created_at = record.created_at or default_stamp
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO agents"
                        " (name, name_key, summary, status, created_at, last_active_at, run_count)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.name,
                            normalize_agent_name(record.name),
                            record.summary,
                            record.status,
                            created_at,
                            record.last_active_at or created_at,
                            record.run_count,
                        ),
                    )
                    if not cursor.rowcount:
                        continue
                    inserted += 1
                    for kind, values in record.entities.items():
                        conn.executemany(
                            "INSERT OR IGNORE INTO agent_entities (agent_name, kind, value)"
                            " VALUES (?, ?, ?)",
                            [(record.name, kind, value) for value in values if value],
                        )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return inserted

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM agent_entities")
            conn.execute("DELETE FROM agents")
        logger.info("Cleared agent registry")

    # Reads -------------------------------------------------------------------

    def get(self, name: str) -> Optional[AgentRecord]:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
            if row is None:
                return None
            entity_rows = conn.execute(
                "SELECT agent_name, kind, value FROM agent_entities WHERE agent_name = ?",
                (name,),
            ).fetchall()
        return self._build_records([row], entity_rows)[0]

    def exists(self, name: str) -> bool:
        with self._lock, closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone() is not None

    # All agents, oldest first (the same order the old roster used)
    def list_agents(self, statuses: Optional[Sequence[str]] = None) -> List[AgentRecord]:
        where, params = self._status_filter(statuses)
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM agents {where} ORDER BY created_at, rowid", params
            ).fetchall()
            entity_rows = conn.execute("SELECT agent_name, kind, value FROM agent_entities").fetchall()
        return self._build_records(rows, entity_rows)

    def names(self) -> List[str]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT name FROM agents ORDER BY created_at, rowid").fetchall()
        return [row["name"] for row in rows]

    def count(self, statuses: Optional[Sequence[str]] = None) -> int:
        where, params = self._status_filter(statuses)
        with self._lock, closing(self._connect()) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM agents {where}", params).fetchone()[0])

    # Agents whose normalised name matches, e.g. "Email to Sarah Chen" ~ "Sarah Chen thread"
    def find_by_name_key(self, name: str) -> List[str]:
        key = normalize_agent_name(name)
        if not key:
            return []
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT name FROM agents WHERE name_key = ? ORDER BY last_active_at DESC", (key,)
            ).fetchall()
        return [row["name"] for row in rows]

    # Agents that touched exactly this entity, most recently active first
    def find_by_entity(self, kind: str, value: str) -> List[str]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT a.name FROM agent_entities e JOIN agents a ON a.name = e.agent_name"
                " WHERE e.kind = ? AND e.value = ? ORDER BY a.last_active_at DESC",
                (kind, value.lower()),
            ).fetchall()
        return [row["name"] for row in rows]

    # Every value of one entity kind (e.g. all person names), most recently active agent first
    def entity_values(self, kind: str) -> List[str]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT e.value FROM agent_entities e JOIN agents a ON a.name = e.agent_name"
                " WHERE e.kind = ? ORDER BY a.last_active_at DESC, e.rowid",
                (kind,),
            ).fetchall()
        return list(dict.fromkeys(row["value"] for row in rows))

    # Helpers -----------------------------------------------------------------

    @staticmethod
    def _status_filter(statuses: Optional[Sequence[str]]) -> Tuple[str, List[str]]:
        if not statuses:
            return "", []
        placeholders = ", ".join("?" for _ in statuses)
        return f"WHERE status IN ({placeholders})", list(statuses)

    @staticmethod
    def _build_records(
        rows: Sequence[sqlite3.Row], entity_rows: Sequence[sqlite3.Row]
    ) -> List[AgentRecord]:
        entities: Dict[str, Dict[str, List[str]]] = {}
        for entity in entity_rows:
            entities.setdefault(entity["agent_name"], {}).setdefault(entity["kind"], []).append(
                entity["value"]
            )
        return [
            AgentRecord(
                name=row["name"],
                summary=row["summary"],
                entities=merge_entities(entities.get(row["name"], {})),
                status=row["status"],
                created_at=row["created_at"],
                last_active_at=row["last_active_at"],
                run_count=row["run_count"],
            )
            for row in rows
        ]


__all__ = [
    "ALL_STATUSES",
    "AgentRecord",
    "AgentRegistry",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "STATUS_DORMANT",
    "parse_timestamp",
    "to_iso",
]
