# Approach: search agents by meaning, next to keyword search

Branch `approach-embeddings`, cut from `agent-overload` at `104bc97`.

## What it does

Keyword search (BM25) can't link "replacing everyone's old computers" to "Ironbridge Laptop Refresh". They share no word. In the 24 hand-written paraphrases, 3 agents were not in the 12-agent shortlist for that reason. This branch adds a small local embedding model that compares meaning.

- **Model.** `BAAI/bge-small-en-v1.5` through `fastembed`, which runs it with ONNX Runtime on the CPU. fastembed actually downloads `Qdrant/bge-small-en-v1.5-onnx-Q`, a quantized ONNX copy of that model. No torch, no GPU, no API calls.
- **What gets embedded.** Each agent's name, summary and search phrases (`embeddings.agent_text`). Email addresses and thread ids are left out. They are hard matches, not meaning.
- **Storage.** A new table `agent_embeddings` in the registry's SQLite file, keyed by agent id. Each row keeps a hash of the model name and the text, so a vector is only recomputed when that text changes. Vectors are also kept in memory, so a turn reads nothing from disk.
- **Search.** The message is embedded, then compared with every agent by cosine similarity, brute force with numpy.
- **Combining the two (the fusion rule).** Out of 8 retrieved places, keyword search keeps its top 5. The last 3 go to the agents closest in meaning that keyword search didn't already pick. If fewer than 3 pass a similarity floor of 0.5, keyword results take the places back. Exact email and thread matches always stay. The 4 recent agents and the named-people list are unchanged. `search_agents` uses the same rule.
- **Never merges.** The resolver now calls `keyword_search`, which never looks at meaning. A test checks that the embedder is not even called while resolving.
- **Optional.** `OPENPOKE_AGENT_RETRIEVER=bm25` gives exactly the old results. `hybrid` is the default on this branch. Without `fastembed` installed, `hybrid` quietly behaves like `bm25`. Install it with `pip install -r server/requirements-embeddings.txt`.
- **Off the reply path.** At start-up the app loads the model and embeds the agents in a background thread. During a turn, at most 8 new or changed agents are embedded inline (normally 0 to 2). A bigger backlog goes to the background thread, and until it is done those agents are found by keywords only.

### Why "fill the last 3 places" and not RRF or a weighted sum

- Plain reciprocal rank fusion (RRF, k=60) mixes both lists. A name-exact keyword hit can then drop below agents that are only similar in meaning, and fall out of a 12-place prompt. Filling places can't do that. The keyword top 5 is never touched.
- A weighted sum needs BM25 and cosine on one scale. BM25 scores grow with the number of agents. Cosine doesn't. A weight that works at 300 agents may not work at 800.
- Why 5 + 3: on the 185 generated cases, the right agent was keyword rank 1 for 132 of 145 existing-agent cases, ranks 2 to 5 for 6, rank 6 for 1, and rank 9 or lower for 6 (those reach the prompt as recent agents). So keyword ranks 1 to 5 hold almost all of what keywords find. On the paraphrase set with phrases, meaning put the right agent at rank 1 for 23 of 24 messages and rank 2 for the last one, so 3 places is enough room.
- Why the floor doesn't do much: bge-small gives high scores to almost anything. Real paraphrase matches scored 0.52 to 0.76, but "ok send it" also scored 0.65 against some agents. No floor separates the two. The fixed count of 3 is what bounds the cost of a bad meaning match: at most 3 agents in the prompt that keywords wouldn't have put there. On average the roster section grew by 19 to 66 tokens per turn.

## Results, simulated router (free)

The simulated router matches words, so it can't use meaning when it picks. What moves is whether the right agent is in front of the model at all (recall). "bm25" below is the committed result, which this branch reproduces exactly with `--retriever bm25` except for timing.

### 24 hand-written paraphrases, with search phrases

`python -m evals.run_eval --set paraphrase --phrases --retriever bm25|hybrid`

| | bm25 | hybrid |
|---|---|---|
| Right agent in the prompt (recall@12) | 87.5% (21/24) | **100% (24/24)** |
| Topic-only messages: right agent in the prompt | 62.5% (5/8) | **100% (8/8)** |
| Right agent in `search_agents` top 8 | 83.3% (20/24) | **100% (24/24)** |
| Simulated routing accuracy | 54.2% | 54.2% |
| Same cases: fixed / broke | | 0 / 0 (p = 1) |
| Roster tokens per turn (approx.) | 860 | 926 |
| Routing overhead per turn, p50 / p95, same machine and day | 60.0 / 116.1 ms | 159.7 / 219.7 ms |

All three misses are now in the prompt: the academy, the Japanese manual and the old computers. "Has Emma locked in who's taking pictures at the ceremony?" was in the prompt before but not in search results. Now it is in both.

### 185 generated cases, 26 conversations

`python -m evals.run_eval --retriever bm25|hybrid` (3 seeds, 50 to 800 agents, 3 router thresholds)

| | bm25 | hybrid |
|---|---|---|
| Routing accuracy | 93.0% (172/185) | 93.0% (172/185) |
| Same cases: fixed / broke | | 0 / 0 (p = 1) |
| Right agent in the prompt (recall@12) | 100% | 100% |
| Right agent in `search_agents` top 8 | 95.9% | **98.6%** |
| Conversations with every turn right | 100% | 100% |
| Accuracy at 50 / 100 / 200 / 400 / 800 agents | 90.8 / 92.5 / 92.4 / 91.9 / 91.9% | the same |
| Roster tokens per turn at 300 / 800 agents | 978 / 994 | 997 / 1,021 |
| Routing overhead per turn, p50 / p95, same machine and day | 23.7 / 59.6 ms | 68.8 / 119.0 ms |

Nothing got worse in accuracy, recall, the seeds, the roster sizes, the threshold sweep or the conversations. The 12 `followup_paraphrase` failures stay, because the simulated router still picks by words.

Result files: `evals/results/results_hybrid.md` and `results_paraphrase_phrases_hybrid.md`.

## Results, real model (Claude Haiku 5.5)

Two runs, after more credit was added on 2026-10-28:

| Run | Original | `main` (keywords only, same day) | Hybrid |
|---|---|---|---|
| 24 hand-written paraphrases, cases right by majority | 12 to 15 | 23/24 | 24/24 |
| same, right on all 3 runs (pass^3) | 41.7% to 58.3% | 87.5% | 95.8% to 100% |
| Usual 40 cases, runs right | 66 to 70 /120 | 117/120 | 120/120 |
| Usual 40 cases, cases right | 22 to 24 | 39/40 | 40/40 |

- The paraphrase run on this branch (`evals/results/llm_results_anthropic-claude-haiku-5-5_paraphrase_phrases_hybrid.md`, $0.16) got 24/24, right on all 3 runs for every case. Against the original it fixed 9 and broke 0 (p = 0.0039).
- The rest come from the same-day comparison on branch `all-approaches` (`python -m evals.compare_llm`, see its `docs/COMPARISON.md`). There, hybrid on its own fixed 2 cases against keyword search and broke none:
  - `hw17`, a paraphrase;
  - `new_same_first_name-156`, a new contact who shares a first name with an existing one.
- Before the runs I expected a gain in stability (pass^3), not in majority accuracy, and worried that 3 agents matched only by meaning would be 3 more ways to pick wrong. The first part held. The worry didn't show: no wrong picks in these runs.

## Install size and CPU cost

Measured on this machine: Windows 10, 4 CPU cores, Python 3.12. Other evals ran on the same machine at the same time, so the timings are noisy and probably pessimistic.

| | Measured |
|---|---|
| New packages (fastembed 0.9.1, onnxruntime 1.31.1, flatbuffers, loguru, mmh3, py-rust-stemmers) | 47.8 MB, of which onnxruntime is 45.5 MB |
| Model files, downloaded once | 64.1 MB (took 18 s here) |
| Already installed here, so not counted (assumption: a fresh environment adds them) | numpy, tokenizers, huggingface-hub, pillow, requests, tqdm |
| Import fastembed and load the model | 1.6 s (load alone 0.8 s) |
| Embed one message | 18 ms p50, 31 ms p95 (about 50 ms while the evals were running) |
| Embed 300 agents, name + summary | 10 s |
| Embed 324 agents, name + summary + phrases | 28 to 42 s |
| Embed 800 agents, name + summary | 31 s |
| Embed 800 agents, name + summary + phrases | 107 s |
| Read all vectors back after a restart | 6 ms (324 agents), 24 ms (800) |
| One search, bm25 vs hybrid, 300 agents (p50) | 37 vs 77 ms |
| One search, bm25 vs hybrid, 800 agents (p50) | 85 vs 159 ms |
| Registry file with vectors | 1.7 MB (324 agents), 4.1 MB (800) |

Most of the extra time per turn is embedding the message. One model call takes about 3 seconds, so 45 to 100 ms more per turn is small, but it isn't free. Embedding every agent takes minutes at 800 agents, which is why it runs in the background. In normal use only the agent that just ran needs a new vector.

**A side effect of the install.** On this machine, `pip install fastembed` upgraded protobuf from 4.25.4 to 7.36.2. That broke the version ranges of unrelated packages already installed (streamlit, Google API libraries). I pinned protobuf to 4.25.8, which onnxruntime accepts (it needs 4.25.8 or newer) and which those packages accept too. A clean virtualenv wouldn't have this problem.

## Tests

`python -m pytest`: 143 tests (128 before). 11 new tests use a tiny fake embedder and need nothing installed. They check that `bm25` gives today's results, that hybrid without fastembed is keyword search, that exact email and name hits stay first, that hard matches are never pushed out, that vectors are reused while the text is unchanged (also after a restart), that a big backlog goes to the background, and that similarity never merges agents. 4 more use the real model: the three paraphrase misses are found in a 43-agent registry, and exact hits still come first. They load the model with `local_files_only`, so they never download it, and they skip cleanly if fastembed or the model is missing. The suite still never calls an LLM. Tests use `bm25` unless they ask for hybrid, through an autouse fixture.

## What it fixes and what it doesn't

Fixes:
- The 3 paraphrases whose agent wasn't in the prompt: now all 24 are in the prompt, and all 24 are in search results.
- Search misses on the generated set: 95.9% to 98.6% of right agents in the `search_agents` top 8.

Doesn't fix:
- **Picking.** Getting an agent into the prompt is not the same as choosing it. The simulated accuracy didn't move, and the real-model effect is unmeasured.
- **Typos and plurals.** "Sarha" and "groceries" are a tokenizer problem, handled on another branch.
- **Which Sarah.** Meaning can't tell two people with the same first name apart. Exact emails and the named-people list still do that.
- **Noise.** The model scores almost anything 0.5 or higher, so for "ok send it" it still adds 3 agents. That costs tokens and adds distractors.

## Merge verdict

Merge it as an **opt-in setting, with `bm25` as the default**, not as the default yet.

For it:
- It closes the retrieval gap it was built for: 24/24 paraphrases in the prompt, against 21/24.
- No simulated regression anywhere: 0 fixed, 0 broke on 185 + 24 cases, same accuracy at every roster size.
- It's fully optional. Without the package, or with `bm25`, nothing changes.
- It can't merge agents.

Against making it the default now:
- The real-model gain is small in absolute terms: 1 or 2 cases per set, on one run per row. The clear part is stability: no case got a wrong pick, and pass^3 went up.
- It adds about 110 MB on disk and a model download on first use, and on this machine the install changed a shared package (protobuf).
- It adds 45 to 100 ms per turn and minutes of background CPU for a large registry.
- On a 43,000-tool benchmark, plain BM25 beat small embedders ([ToolRet, 2025](https://arxiv.org/abs/2503.01763)). Here it helped recall at a few hundred agents, but on synthetic data and 24 hand-written messages from one author.

Update after the paid runs: both conditions I set were met. Paraphrase pass^3 went up (87.5% to 95.8% same day, 100% on this branch's run), and the main 40 lost nothing (40/40). So `hybrid` is a reasonable default wherever the 110 MB install is acceptable. I still left `bm25` as the default here, so a plain install needs no extra download.
