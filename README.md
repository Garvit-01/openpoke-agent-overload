# OpenPoke: fixing agent overload

This is a fork of [OpenPoke](https://github.com/shlokkhemani/openpoke) by Shlok Khemani, an open source take on the Poke assistant. I changed how the main agent finds and reuses its helper agents, so it keeps routing to the right one as helpers pile up. There are tests, an evaluator that runs the original code and my version on the same data, and a live demo that runs both apps side by side.

The original is on branch `upstream-main` (upstream commit `5b5f635`). Everything behind the numbers below, run by run, is in [docs/EVALUATION.md](docs/EVALUATION.md).

> **This is branch `approach-past-requests`:** `main` plus search over each agent's last 20 requests, so a follow-up about something asked mid-history still finds the agent. Design and results: [docs/APPROACH_PAST_REQUESTS.md](docs/APPROACH_PAST_REQUESTS.md). The approaches each have their own branch: `fix-plurals-typos`, `approach-past-requests` and `approach-embeddings`. Branch `all-approaches` has all three behind settings and compares them side by side (`python -m evals.compare`).

## In short

**The problem.** OpenPoke has one interaction agent (the one you talk to) and many execution agents, one per task, like "Email to Sarah". Agents are kept forever so they can own a thread for follow-ups. After a few weeks there are hundreds. The original puts every agent's name into every prompt, and reuses an agent only on an exact name match. With four contacts called Sarah and names like "Email to Sarah", the model can't tell which thread a follow-up belongs to. So it guesses, or creates a duplicate.

**The fix.** Treat "which agent is this about?" as a retrieval problem. An SQLite registry keeps a summary, contacts, thread ids and status for each agent. Each turn the prompt shows a fixed shortlist of 12 (8 found for the message, 4 recently used), plus the other agents of any person the message names. An exact email or thread match always wins. A resolver stops duplicates when an agent is created, and idle agents are retired but stay searchable.

**The result.** On the same data and the same model, routing goes from about 58% right to 99%, duplicates mostly disappear, and the prompt stops growing with the roster.

| | Original | This branch |
|---|---|---|
| **Real model** (Claude Haiku 5.5), 300 agents, 40 cases × 3 runs: runs routed right | 58.3% | **99.2%** |
| Cases right (2 of 3 runs), with 95% interval | 24/40 (45% to 74%) | **40/40** (91% to 100%) |
| Real model, **800 agents**: runs routed right | 54.2% | **100%** |
| 800 agents: duplicate agent created when the right one existed | **44.8%** | 0.0% |
| 800 agents: input tokens per routing decision | 19,471 | 7,287 |
| 24 **hand-written paraphrases** (no shared words with their agent): cases right | 15/24 | **24/24** |
| same, right on all 3 runs (pass^3) | 54.2% | 83.3% |
| **Second model** (Gemma 4 26B), 40 cases: cases right | 19/40 | **29/40** |
| Simulated router, 185 labelled cases: right | 57.8% | 93.0% |
| Simulated, 26 multi-turn conversations: every turn right | 34.6% | 100% |

**Is the difference real?** On the 40 real-model cases this branch fixed 16 and broke 0 at 300 agents (McNemar's exact test, p = 3e-5), and 19 and 0 at 800 agents (p = 4e-6). On the 185 simulated cases it fixed 65 and broke 0 (p = 5e-20). The data is synthetic, so read [Limitations](#limitations) before quoting any of this.

## What was wrong in the original

1. **The roster is only names.** `roster.py` stored a JSON list of strings: no summary, contacts, timestamps or status.
2. **Every name goes into every prompt.** The prompt grows forever, and most entries look alike.
3. **Reuse needs an exact string match.** "Email to Sarah Chen" and "Sarah Chen thread" become two agents, and the thread's context splits.
4. **Nothing is retired.** The only cleanup is `clear()`, which deletes everything.
5. **Each agent's own history is unbounded.** An execution agent got its whole log on every run.
6. **Agents could share a history.** Log files were named by a slug, so "Email to Sarah" and "email to sarah" read and wrote the same file.
7. **The same agent could run twice at once**, for example a reminder firing while you're talking to it. Both runs read the same history and both could act.

The author names the first problems himself in his [explainer](https://www.shloked.com/openpoke).

## What I changed

| # | Change | Where |
|---|---|---|
| 1 | SQLite agent registry: summary, entities (emails, thread ids, people, subjects), times, run count, status. `roster.json` is migrated once, not deleted | `registry.py`, `roster.py` |
| 2 | Fixed shortlist per turn: 8 retrieved + 4 most recently used, each with status, last use, summary and why it matched | `retrieval.py`, `interaction_agent/agent.py` |
| 3 | Ranking: an exact email or thread match first, BM25 keyword search within that, status and recency only break ties | `retrieval.py` |
| 4 | `search_agents` tool, so the model can look past the shortlist, including retired agents | `interaction_agent/tools.py` |
| 5 | Dedup on create: reuse on the same name, thread, email or normalised name. Similar wording alone never merges. A `new_task` flag for "something new with a known contact" | `resolver.py` |
| 6 | Profiles rebuilt from the agent's log after each run | `digest.py` |
| 7 | Lifecycle: dormant after 14 idle days, archived after 45, never while it owns a live reminder. Nothing is deleted | `lifecycle.py` |
| 8 | Routing log: every decision with its reason and look-alikes | `routing.py` |
| 9 | Execution agents see their last 10 requests plus a summary | `execution_agent/` |
| 10 | Stable agent ids, so different names never share a log file | `ids.py` |
| 11 | One run at a time per agent, shared by chat and the reminder scheduler | `batch_manager.py` |
| 12 | When the message names a person, list that person's other agents by name and topic | `retrieval.py` |
| 13 | Prompt: search for the name a task was probably filed under, not the user's words | `system_prompt.md` |
| 14 | Search phrases: after an agent's first run, a cheap model writes about 10 ways a user might refer to it, and search indexes them | `phrases.py` |

Changes 12 to 14 were each added after an eval showed the original doing better on some cases. [docs/EVALUATION.md](docs/EVALUATION.md) shows the before and after of each.

## What the live demo showed

`tools/demo.py` loads the same 300 synthetic agents into both apps and sends each the same message (see [Running it](#running-it)). Both apps ran on Haiku 5.5. Gmail wasn't connected, so neither app could send anything. Which agent each one picked is real. Among the 300 agents are four Sarahs: Okafor (job offer), Murphy (tax documents), Dubois and Evans.

| You type | Original | This branch |
|---|---|---|
| "Could you send the updated grocery to **Sarah**" | Went straight to "Email to Sarah", which is **Sarah Murphy's** tax agent | Asked "Which Sarah? Okafor, Dubois, Evans or Murphy?" |
| "**Sarah Okafor**" | Stayed on Murphy's agent. It couldn't find Okafor's address, and its draft left out one of the three items | Created "Grocery list email to Sarah Okafor" with her right address and all three items |
| "message sarah regarding the movie..." | Murphy's agent again, without asking | Asked which Sarah |

For the movie message, measured on each app's real model calls:

| | Original | This branch |
|---|---|---|
| Model calls | 6 (4 routing, 2 in Murphy's agent) | 1 |
| Input tokens | 70,233 | 10,760 |
| Cost | $0.0078 | $0.0013 |
| Time to final reply | 17.1 s | 8.6 s |
| Routing input tokens **per call** | 14,219 | 10,760 |

Most of that gap is because this branch asked a question instead of doing the wrong work. It isn't a like-for-like saving. Per routing call the original used about 3,500 more input tokens, roughly the size of its 300-name list. At 800 agents the eval measured 19,471 vs 7,287 input tokens per decision.

## Design choices

**One agent per task, not one per person.** The explainer's example has one agent own everything about Alice. I kept the original's per-task agents and added `new_task`. Three reasons:
- The original prompt already says to start a new agent when tasks can run in parallel.
- With the run lock, one agent per person would make Emma's new audit email wait behind her lease work.
- An agent about one topic gets a summary that search can match.

The cost is more agents, which retrieval and lifecycle have to carry.

**Hard matches before similarity.** An email address or Gmail thread id says which thread a message belongs to. Keyword or meaning similarity only says what it's about. So exact matches decide, similarity only ranks, and similarity never merges two agents.

**SQLite and in-process BM25, not Elasticsearch or a vector database.** One user has hundreds of agents, maybe a few thousand. The registry needs exact lookups ("who owns this thread?") and safe writes. SQLite does both and ships with Python. All of this branch's routing work (search, shortlist, prompt) takes about 26 ms per turn at 300 agents (70 ms at p95), in plain Python. One model call takes about 3 seconds.

I looked at the alternatives:
- **Elasticsearch / OpenSearch.** Running either locally on Windows needs Docker and WSL and about 1 to 4 GB of memory. Elasticsearch also ranks with BM25, so it wouldn't change the core ranking. Its extras (stemming, typo matching, hybrid search) each have an in-process equivalent: SQLite FTS5, `difflib`, numpy.
- **Embeddings.** On a 43,000-tool retrieval benchmark, plain BM25 beat small embedders such as all-MiniLM and e5-small ([ToolRet, 2025](https://arxiv.org/abs/2503.01763)). The gain over BM25 with written search phrases is unproven at this scale.

A search server would make sense with many users on one deployment. The [comparison branch](#next-steps) tests the cheap alternatives on the same eval.

**How this lines up with the author's suggestions:**

| Author's suggestion | Here |
|---|---|
| Semantic search over agent descriptions | Mostly: summaries, entities and search phrases, searched with BM25 plus exact matches, not embeddings |
| Archive dormant agents | Yes (change 7) |
| Cluster related agents | No. Duplicates are prevented on create, but existing ones aren't merged |
| Hot cache of recent agents | Yes, the 4 most recent are always shown (change 2) |

## Other gaps I found

Fixed:
- **The server didn't start on Windows.** `roster.py` imported `fcntl`, which is Unix only.
- **The default model no longer existed.** `anthropic/claude-sonnet-4` is retired on OpenRouter, so a fresh install failed on the first message. The default is now `anthropic/claude-sonnet-5.5`, and each role has its own env var.
- **Send confirmation was a prompt instruction, not a rule.** Execution agents read inbound email and can reply or forward, so one injected line in an email only had to convince one model. `services/safety/outbound.py` now blocks a send unless every recipient was in a draft the user saw and answered. It can't judge whether the reply meant yes.
- **No record of routing decisions.** There's now a JSONL log, which is how bad decisions can become eval cases.

Not fixed, worth knowing:
- Gmail tools aren't scoped per agent, so any agent can act on any thread.
- All results from one batch wait for the slowest agent (up to 90 s) before the user hears anything.

## Limitations

- **The data is synthetic.** I generated the 300 agents and labelled messages to contain known failure modes. The 24 hand-written paraphrases avoid the generator's wording, but one author wrote them with Claude's help. Real users will be messier.
- **One model family does most of the work.** Haiku routes, Haiku writes the search phrases, and a Claude model helped write the test messages. Gemma checks the routing side only.
- **The sample is small.** 40 main cases and 24 paraphrases. The headline difference is significant, but per-category numbers move 20 points when one case flips.
- **Paraphrases are still the weak spot.** For 3 of the 24 the right agent isn't in the shortlist, and the model only finds it by searching. 4 cases were right on 2 of 3 runs, not all 3.
- **No stemming or typo matching yet.** "grocery" doesn't match "groceries", and "Sarha" doesn't match "Sarah". In the demo the model asked who "Sarha" was, which is safe but not smart.
- **It asks "which Sarah?" even right after you named one.** One turn after "Sarah Okafor", it asked again for a new topic. That's a deliberate lean towards asking before an email goes out, but a person would guess Okafor.
- **Gmail isn't tested live.** Routing never needs Gmail, so none of the numbers above depend on it. Incoming-email routing by thread id, drafting and the send guard are unit-tested with fakes, not run against a real inbox.
- **Prompt caching isn't measured.** The original's list changes only when an agent is created, so with a warm cache it costs less than the token counts suggest. Caching doesn't change its accuracy.

## Next steps

1. **A comparison branch** with three cheap alternatives behind one setting, on the same eval plus a frozen set of hard cases (typos, plurals, topic-only paraphrases, shared first names):
   - SQLite FTS5 with stemming plus typo matching on names;
   - the router seeing 30 candidates instead of 12;
   - the original full list with prompt caching on, to answer "with caching, why retrieve?".
2. Run the real-model eval on the multi-turn conversations, mainly to see how often the model sets `new_task`.
3. Use recent context for first names: prefer the Sarah from the last few turns, but still ask before sending.
4. Turn routing-log mistakes into eval cases.
5. A merge tool for duplicates that already exist.

## Running it

```bash
pip install -r server/requirements.txt pytest
python -m pytest                                  # 146 tests, about a minute, no keys needed
python -m evals.run_eval                          # simulated eval, about 2 minutes, writes evals/results/
python -m evals.run_eval --router llm --phrases   # real model, needs OPENROUTER_API_KEY, about $0.30 on Haiku 5.5
python -m evals.run_eval --set paraphrase --router llm --phrases   # the 24 hand-written paraphrases
python -m evals.run_eval --router llm --agents 800 --phrases       # the overload at 800 agents
python -m evals.run_eval --router llm --rescore   # re-report a saved run, no model calls
```

The real-model eval uses `anthropic/claude-haiku-5.5` unless you pass `--model`. More options are in [docs/EVALUATION.md](docs/EVALUATION.md).

### Live demo: both apps side by side

```bash
git worktree add ../openpoke-original 5b5f635   # the original (branch upstream-main), in its own folder
python tools/demo.py seed                    # the same 300 agents into both apps (moves existing data aside)
python tools/run_mine.py                     # this branch on port 8001, in its own terminal
python tools/run_original.py                 # the original on port 8002, in another terminal
python tools/demo.py ask "Could you send the updated grocery to Sarah"
python tools/demo.py status
```

`ask` prints, for each app:
- its replies, and which agent it used (new or existing, and for this branch the reason);
- how many agents were in its prompt;
- its model calls, tokens, cost and time.

For a labelled eval case it also prints the right answer. The launchers record each model call by wrapping the one function both apps call the model through, in memory. `run_original.py` runs the original's code unchanged, with two runtime-only patches it needs to start today: a stand-in for `fcntl` on Windows, and a current model name. Both apps run on Haiku 5.5, and a message costs under a cent per app.

### New settings (env vars, all optional)

| Variable | Default | What it does |
|---|---|---|
| `OPENPOKE_AGENT_SHORTLIST` / `OPENPOKE_AGENT_RECENT` | 8 / 4 | Agents retrieved / recently used per turn |
| `OPENPOKE_AGENT_SUMMARY_CHARS` | 200 | Summary length shown per agent |
| `OPENPOKE_AGENT_NAMED_PEOPLE` | 12 | A named person's other agents listed (0 = off) |
| `OPENPOKE_AGENT_PHRASES` / `OPENPOKE_PHRASES_MODEL` | 1 / `anthropic/claude-haiku-5.5` | Search phrases after an agent's first run (0 = off) |
| `OPENPOKE_AGENT_DORMANT_DAYS` / `OPENPOKE_AGENT_ARCHIVE_DAYS` | 14 / 45 | Idle days before demotion |
| `OPENPOKE_EXECUTION_HISTORY_LIMIT` | 10 | Past requests an execution agent sees (0 = all) |
| `OPENPOKE_OUTBOUND_GUARD` | enforce | `off` disables the send guard |
| `OPENPOKE_INTERACTION_MODEL`, `_EXECUTION_MODEL`, `_SEARCH_MODEL`, `_SUMMARIZER_MODEL`, `_CLASSIFIER_MODEL` | `anthropic/claude-sonnet-5.5` | Model per role |

### What's new in the repo

```
server/services/execution/   registry, ids, entities, retrieval, resolver, phrases, digest, lifecycle, routing, roster (migration)
server/agents/execution_agent/batch_manager.py   per-agent run lock
server/services/safety/outbound.py               send confirmation guard
evals/     scenario generator, hand-written paraphrase set, copy of the original, harness, stats, real-model mode, results/
tests/     146 tests
tools/     run_original.py, run_mine.py, demo.py, usage_meter.py
docs/      EVALUATION.md
```

---

## Original OpenPoke setup

OpenPoke is a simplified, open source take on [Interaction Company's](https://interaction.co/about) [Poke](https://poke.com/) assistant. It covers email triage, reminders and persistent agents, with a FastAPI backend, models through [OpenRouter](https://openrouter.ai/), Gmail through [Composio](https://composio.dev/), and a Next.js web UI.

Requirements: Python 3.10+, Node.js 18+, npm 9+.

1. Copy `.env.example` to `.env`, then add an OpenRouter API key, a Composio API key and your Gmail auth config id.
2. Create and activate a virtualenv, then run `pip install -r server/requirements.txt`.
3. Run `npm install --prefix web`.
4. Start the server: `python -m server.server --reload`.
5. In a second terminal, start the web app: `npm run dev --prefix web`.
6. Open http://localhost:3000, go to Settings → Gmail and complete the Composio OAuth flow.

Runtime data lives in `server/data/`, which git ignores.

## License

MIT, same as the original. See [LICENSE](LICENSE).
