# Three alternative approaches, compared

After the main fix (branch `main`, which is `agent-overload`), I tried three more ideas, each on its own branch:

| Branch | What it adds | Write-up |
|---|---|---|
| `fix-plurals-typos` | Plural folding ("groceries" finds "Grocery list") and "did you mean ...?" hints for misspelt contact names ("Sarha" lists every Sarah) | [EVALUATION.md](EVALUATION.md), changes 15 and 16 in the README |
| `approach-past-requests` | Keyword search also reads each agent's last 20 requests, at half weight | [APPROACH_PAST_REQUESTS.md](APPROACH_PAST_REQUESTS.md) |
| `approach-embeddings` | Optional search by meaning with a small local embedding model (bge-small), next to keyword search | [APPROACH_EMBEDDINGS.md](APPROACH_EMBEDDINGS.md) |

This branch, `all-approaches`, has all three, each behind its own setting, so one command can measure them on the same cases:

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

Meaning search is off by default because its real-model effect isn't measured yet (see below) and it needs an extra 110 MB install.

## Simulated results (free, run on 2026-10-28)

All rows use the same seed and cases. Each row turns one approach on over `main`, and the last row turns on all three. "Fixed / broke" counts cases against `main`, with McNemar's exact p.

### Generated follow-ups

197 cases, 300 agents. That's the 185 cases from the main evaluation, plus 12 cases that ask about something requested mid-history (`followup_past_request`, added on the past-requests branch).

| Version | Right | Duplicates | Wrong agent | Right agent in prompt | In search top 8 | Past-request cases right | Fixed / broke vs main | Roster tokens | Routing p50 |
|---|---|---|---|---|---|---|---|---|---|
| Original OpenPoke | 108/197 | 17.2% | 36.9% | all shown | n/a | 8.3% | | 3,135 | 1 ms |
| `main` | 172/197 | 3.8% | 11.5% | 96.2% | 92.4% | 0.0% | | 957 | 24 ms |
| + plurals and typo hints | 172/197 | 3.8% | 11.5% | 96.2% | 92.4% | 0.0% | 0 / 0 | 959 | 29 ms |
| + past-request search | 185/197 | 0.0% | 7.0% | 100.0% | 96.8% | 100.0% | 13 / 0 (p = 0.00024) | 964 | 27 ms |
| + meaning search | 172/197 | 3.8% | 11.5% | 96.2% | 93.6% | 0.0% | 0 / 0 | 983 | 61 ms |
| all three together | 185/197 | 0.0% | 7.0% | 100.0% | 98.7% | 100.0% | 13 / 0 (p = 0.00024) | 991 | 37 ms |

### Hand-written paraphrases, with search phrases

24 cases, 324 agents. These messages share no content words with their agent. The simulated router matches words, so it can't solve them by design. Read the recall columns: they say whether a real model would even see the right agent.

| Version | Right | Right agent in prompt | In search top 8 | Topic-only messages in prompt | Fixed / broke vs main | Roster tokens | Routing p50 |
|---|---|---|---|---|---|---|---|
| Original OpenPoke | 4/24 | all shown | n/a | n/a | | 3,409 | 1 ms |
| `main` | 13/24 | 87.5% | 83.3% | 62.5% | | 860 | 64 ms |
| + plurals and typo hints | 14/24 | 95.8% | 91.7% | 87.5% | 1 / 0 | 873 | 69 ms |
| + past-request search | 13/24 | 87.5% | 83.3% | 62.5% | 0 / 0 | 866 | 62 ms |
| + meaning search | 13/24 | 100.0% | 100.0% | 100.0% | 0 / 0 | 926 | 120 ms |
| all three together | 14/24 | 100.0% | 100.0% | 100.0% | 1 / 0 | 934 | 81 ms |

### What this shows

- **Nothing breaks anything.** In both sets, no approach, and not all three together, gets a case wrong that `main` got right.
- **Each approach fixes a different gap:**
  - Past requests fix the 12 mid-history follow-ups, which no other approach reaches.
  - Plurals put 2 of the 3 paraphrase misses into the prompt ("kids" now finds "kid's registration", and "computers" finds "computer upgrade").
  - Meaning search puts all 3 there, including "the Japanese version of the instructions booklet" for "Orchard Manual Translation".
- **The costs are small.** All three together add 34 roster tokens per turn (957 to 991) and about 13 to 17 ms of routing time. Meaning search alone adds about 37 to 56 ms, mostly to embed the message.
- **The paraphrase "Right" column barely moves**, because the simulated router can't read meaning. The real-model runs below are the ones that count for accuracy.

## Real-model results (Claude Haiku 5.5, from each branch)

These runs come from each branch, at different times of day, so they don't make a clean side-by-side comparison. The original's column moved between 55.0% and 57.5% on the same 40 cases across these runs, which gives a sense of the run-to-run noise.

| Run (3 repeats per case) | Original | `main` (committed) | Plurals and typos | Past requests |
|---|---|---|---|---|
| Main 40 cases, pass^1 | 55.0% to 58.3% | 99.2% | 95.8% * | 100% |
| Main 40 cases, right by majority | 21 to 24 /40 | 40/40 | 39/40 * | 40/40 |
| 6 cases about a mid-history request | 0/18 runs | not run | not run | 18/18 runs |
| Paraphrases, right by majority | 13 to 15 /24 | 24/24 | 23/24 | 22/24 |
| Paraphrases, right on all 3 runs (pass^3) | 37.5% to 54.2% | 83.3% | 95.8% | 87.5% |

\* This run had to regenerate search phrases for 74 agents, because the phrase cache had been overwritten (fixed since). It isn't a clean comparison. Rendering the prompts again with the old phrases gave byte-identical prompts before and after the change for every case that moved.

The paraphrase drops (24/24 to 23/24 and 22/24) are cases where the prompt was identical with the change on and off, so they are most likely model noise. Each branch's write-up has the case-by-case check.

**Meaning search has not been run on the real model.** The OpenRouter credit ran out first. These two runs would measure it (about $0.41 together):

```bash
python -m evals.run_eval --set paraphrase --router llm --phrases --retriever hybrid
python -m evals.run_eval --router llm --phrases --retriever hybrid
```

## A pitfall this comparison found in the evaluator

The simulated router stands in for the model, and it used the app's own tokenizer. Once plural folding became a setting, flipping it changed the judge as well as the search, and a row could gain or lose 5 cases just from that. The router now always reads words as written (`evals/harness.py`), and a test checks it (`tests/test_compare.py`).

The simulated numbers on `fix-plurals-typos` were measured before this fix, with a router that folded plurals. Its retrieval numbers (recall) aren't affected. Its paraphrase accuracy (14/24 there) happens to match the 14/24 here.

## What I'd ship

- **Past requests: yes.** It's the only approach that fixes a whole class of follow-ups, it breaks nothing, and it never merges agents.
- **Plurals and typo hints: yes.** It's small, cheap and fixes the "groceries" and "Sarha" cases from the live demo. It's also more stable on the real model (pass^3 95.8%).
- **Meaning search: as an option, off by default,** until the two real-model runs above show it improves pass^3 without costing the main 40 cases.

## Limitations

- Synthetic data. The 12 past-request cases were written to need that feature: they show it works, not how often real users ask that way.
- One seed and 300 agents here. The per-branch write-ups cover other seeds and roster sizes.
- The real-model numbers are from different runs, and meaning search has none.
