# Three alternative approaches, compared

After the main fix (branch `main`, which is `agent-overload`), I tried three more ideas, each on its own branch:

| Branch | What it adds | Write-up |
|---|---|---|
| `fix-plurals-typos` | Plural folding ("groceries" finds "Grocery list") and "did you mean ...?" hints for misspelt contact names ("Sarha" lists every Sarah) | [EVALUATION.md](EVALUATION.md), changes 15 and 16 in the README |
| `approach-past-requests` | Keyword search also reads each agent's last 20 requests, at half weight | [APPROACH_PAST_REQUESTS.md](APPROACH_PAST_REQUESTS.md) |
| `approach-embeddings` | Optional search by meaning with a small local embedding model (bge-small), next to keyword search | [APPROACH_EMBEDDINGS.md](APPROACH_EMBEDDINGS.md) |

`main` now has all three, each behind its own setting (meaning search off by default), so one command can measure them on the same cases:

```bash
python -m evals.compare                 # about 12 minutes, free, no API key
python -m evals.compare --skip-hybrid   # without the embedding model installed
```

It prints the tables below and writes them to [evals/results/comparison.md](../evals/results/comparison.md).

| Setting | Off | On |
|---|---|---|
| `OPENPOKE_FOLD_PLURALS` | words as written | plurals folded (default) |
| `OPENPOKE_AGENT_NAME_HINTS` | no hints | "did you mean" hints (default) |
| `OPENPOKE_AGENT_INDEX_REQUESTS` | past requests not searched | searched at half weight (default) |
| `OPENPOKE_AGENT_RETRIEVER` | `bm25`: keywords only (default) | `hybrid`: keywords plus meaning |

Meaning search is off by default only because it needs an extra 110 MB install. On the real model it helped and broke nothing (see below).

## Simulated results (free, run on 2026-10-29)

All rows use the same seed and cases. Each row turns one approach on over the core fix (`main` before the merge). "plurals, typos and past requests" is what `main` now ships, and the last row turns on all three. "Fixed / broke" counts cases against `main`, with McNemar's exact p.

### Generated follow-ups

197 cases, 300 agents. That's the 185 cases from the main evaluation, plus 12 cases that ask about something requested mid-history (`followup_past_request`, added on the past-requests branch).

| Version | Right | Duplicates | Wrong agent | Right agent in prompt | In search top 8 | Past-request cases right | Fixed / broke vs main | Roster tokens | Routing p50 |
|---|---|---|---|---|---|---|---|---|---|
| Original OpenPoke | 108/197 | 17.2% | 36.9% | all shown | n/a | 8.3% | | 3,135 | 1 ms |
| agent-overload (main) | 172/197 | 3.8% | 11.5% | 96.2% | 92.4% | 0.0% |  | 957 | 24 ms |
| + plurals and typo hints | 172/197 | 3.8% | 11.5% | 96.2% | 92.4% | 0.0% | 0 / 0 | 959 | 32 ms |
| + past-request search | 185/197 | 0.0% | 7.0% | 100.0% | 96.8% | 100.0% | 13 / 0 (p = 0.00024) | 964 | 28 ms |
| + meaning search (hybrid) | 172/197 | 3.8% | 11.5% | 96.2% | 93.6% | 0.0% | 0 / 0 | 983 | 106 ms |
| plurals, typos and past requests (meaning search off) | 185/197 | 0.0% | 7.0% | 100.0% | 96.8% | 100.0% | 13 / 0 (p = 0.00024) | 966 | 39 ms |
| all three together | 185/197 | 0.0% | 7.0% | 100.0% | 98.7% | 100.0% | 13 / 0 (p = 0.00024) | 991 | 39 ms |

### Hand-written paraphrases, with search phrases

24 cases, 324 agents. These messages share no content words with their agent. The simulated router matches words, so it can't solve them by design. Read the recall columns: they say whether a real model would even see the right agent.

| Version | Right | Right agent in prompt | In search top 8 | Topic-only messages in prompt | Fixed / broke vs main | Roster tokens | Routing p50 |
|---|---|---|---|---|---|---|---|
| Original OpenPoke | 4/24 | all shown | n/a | n/a | | 3,409 | 1 ms |
| agent-overload (main) | 13/24 | 87.5% | 83.3% | 62.5% |  | 860 | 59 ms |
| + plurals and typo hints | 14/24 | 95.8% | 91.7% | 87.5% | 1 / 0 (p = 1) | 873 | 65 ms |
| + past-request search | 13/24 | 87.5% | 83.3% | 62.5% | 0 / 0 | 866 | 61 ms |
| + meaning search (hybrid) | 13/24 | 100.0% | 100.0% | 100.0% | 0 / 0 | 926 | 111 ms |
| plurals, typos and past requests (meaning search off) | 14/24 | 95.8% | 91.7% | 87.5% | 1 / 0 (p = 1) | 876 | 75 ms |
| all three together | 14/24 | 100.0% | 100.0% | 100.0% | 1 / 0 (p = 1) | 934 | 86 ms |

### What this shows

- **Nothing breaks anything.** In both sets, no approach, and not all three together, gets a case wrong that `main` got right.
- **Each approach fixes a different gap:**
  - Past requests fix the 12 mid-history follow-ups, which no other approach reaches.
  - Plurals put 2 of the 3 paraphrase misses into the prompt ("kids" now finds "kid's registration", and "computers" finds "computer upgrade").
  - Meaning search puts all 3 there, including "the Japanese version of the instructions booklet" for "Orchard Manual Translation".
- **The costs are small.** The shipped default (plurals, typos and past requests) adds 9 to 16 roster tokens per turn and about 15 ms of routing time. All three together add 34 to 74 tokens and 15 to 27 ms. Meaning search alone adds about 50 to 80 ms, mostly to embed the message.
- **The paraphrase "Right" column barely moves**, because the simulated router can't read meaning. The real-model runs below are the ones that count for accuracy.

## Real-model results (Claude Haiku 5.5)

```bash
python -m evals.compare_llm          # about $2.40 and an hour; needs OPENROUTER_API_KEY
python -m evals.compare_llm --reuse  # rebuild the tables from the saved runs, no API calls
```

Each row is one real-model run in its own process with that row's settings, on the same cases, seed and model. The rows ran on 2026-10-28, except the shipped default's, which I added on 2026-10-29 once the other rows showed it was the one to check. The original runs in every row too, so its range shows how much the model varies between identical runs. Full tables: [evals/results/comparison_llm.md](../evals/results/comparison_llm.md). The first ten runs cost $2.36 together, and the added row $0.46.

![Cases right per version, real model, 300 agents](charts/alternatives.svg)

| Version | Usual 40 cases, runs right | Usual 40, cases right | 6 mid-history cases | Paraphrases, cases right | Paraphrases, right on all 3 runs | Input tokens per decision (generated set) |
|---|---|---|---|---|---|---|
| Original OpenPoke (lowest to highest of the 5 runs on 2026-10-28) | 66 to 70 /120 | 22 to 24 /40 | 0/6 | 12 to 15 /24 | 41.7% to 54.2% | 9,853 |
| `main` | 117/120 | 39/40 | 0/6 | 23/24 | 87.5% | 8,666 |
| + plurals and typo hints | 116/120 | 39/40 | 0/6 | 24/24 | 95.8% | 8,356 |
| + past-request search | 114/120 | 38/40 | 6/6 | 22/24 | 87.5% | 7,655 |
| + meaning search | **120/120** | **40/40** | 0/6 | 24/24 | 95.8% | 8,775 |
| **plurals, typos and past requests (what `main` ships; run 2026-10-29)** | 117/120 | 39/40 | **6/6** | **24/24** | **100%** | 7,483 |
| **all three together** | **120/120** | **40/40** | **6/6** | **24/24** | **100%** | **7,322** |

On all 46 generated cases together, all three get 46/46, right on every one of the 3 runs (pass^3 100%), against 39/46 for `main`: fixed 7, broke 0 (McNemar's exact p = 0.016). The original got 22 to 24 of 46 across its five runs.

The shipped default (plurals, typos and past requests, meaning search off) gets 45/46, right on all 3 runs for 95.7%: fixed 7, broke 1 (p = 0.07). The break is `new_same_first_name-152` again, right on 1 of 3 runs; in the other 2 the model asked instead of creating a new agent. Past-request search puts 10 "launch timeline" agents next to another Ibrahim, and the model gets cautious. On the paraphrases it gets 24/24, right on every run. Against the original in the same run: fixed 23, broke 0 (p = 2e-7) on the 46 cases, and fixed 11, broke 0 (p = 0.001) on the paraphrases.

**Every case that moved, against `main`:**
- **Plurals and typos:** fixed `hw17` ("kids" now finds the "kid's registration" agent, right on 3 of 3 runs, was 1 of 3). Nothing broke.
- **Meaning search:** fixed `hw17` and `new_same_first_name-156` (a new contact who shares a first name with an existing one: now a new agent on 3 of 3 runs, was 0 of 3). Nothing broke.
- **Past requests:** fixed the 6 mid-history cases (0 to 3 of 3 runs each). It broke 2:
  - `new_same_first_name-152` ("Write to Ibrahim Holloway about the launch timeline": 3 of 3 runs right before, 0 of 3 now);
  - `hw05` ("Alex Moreau": 2 of 3 before, 0 of 3 now).

  In all six runs the model didn't route at all: no duplicate and no wrong agent. For 152 the prompt is identical with the setting off and on, but the model searched, and search reads past requests when they're on. `hw05` used no search and its prompt is identical, so that one is noise; the past-requests branch saw the same case fail the same way. **With all three on, past requests included, both cases are right on all 3 runs.** The rerun on 2026-10-29 (the shipped default) broke 152 again, so that one is a real cost of past-request search. `hw05` was right in that run, so it was noise.
- **All three:** fixed everything the three fixed separately, and broke nothing.

**Cost and tokens.** All three together used the fewest input tokens per decision: 7,322 against 8,666 for `main` and 9,853 for the original. Fewer duplicate and no-route turns mean fewer follow-up calls.

**Caveats.** These are one run per row (3 repeats per case). Most of the differences between the four single-approach rows are 1 or 2 cases, which is within the noise. The clear results are:
- past requests on the 6 mid-history cases;
- all three together against `main` (p = 0.016 on the generated set);
- the original against everything.

## A pitfall this comparison found in the evaluator

The simulated router stands in for the model, and it used the app's own tokenizer. Once plural folding became a setting, flipping it changed the judge as well as the search, and a row could gain or lose 5 cases just from that. The router now always reads words as written (`evals/harness.py`), and a test checks it (`tests/test_compare.py`).

The simulated numbers on `fix-plurals-typos` were measured before this fix, with a router that folded plurals. Its retrieval numbers (recall) aren't affected. Its paraphrase accuracy (14/24 there) happens to match the 14/24 here.

## What I'd ship

`main` ships plurals, typo hints and past-request search on, and meaning search off.
- **Past requests:** the only approach that fixes follow-ups about a mid-history request (6 of 6, from 0), and duplicates drop to 0. Its cost is one case where the model asks instead of creating a new agent.
- **Plurals and typo hints:** cheap. They fix the "groceries" and "Sarha" cases from the live demo, and raised paraphrase pass^3 from 87.5% to 95.8%.
- **Meaning search:** with it on, all three together are right on every case and every run, the Ibrahim case included. It needs about 110 MB of downloads and 50 to 80 ms more per turn, so it stays opt-in (`OPENPOKE_AGENT_RETRIEVER=hybrid`) for installs where that's fine.

## Limitations

- Synthetic data. The 12 past-request cases were written to need that feature: they show it works, not how often real users ask that way.
- One seed. 300 agents, plus a simulated run at 1000 ([comparison_1000agents.md](../evals/results/comparison_1000agents.md)): the shipped default gets 181/197, against 169 for the core fix and 103 for the original. The per-branch write-ups cover other seeds and roster sizes.
- The real-model rows are one run each (3 repeats per case). A second full run (about $2.40) would show which 1-case differences repeat.
