# LLM routing evaluation (google/gemma-4-26b-a4b-it)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 23.6% | 76.4% |
| Cases right on all 3 repeats (pass^3) | 8.3% | 62.5% |
| Duplicate created | 48.6% | 9.7% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 20.8% | 13.9% |
| Used search_agents | 0.0% | 29.2% |
| Prompt tokens, first call (from API usage) | 6,606 | 4,496 |
| Seconds per routing decision, p50 / p95 | 3.0 / 12.4 | 3.3 / 6.7 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 5/24 (20.8%) | 9.2% to 40.5% |
| This change | 19/24 (79.2%) | 59.5% to 90.8% |

Same cases, both versions: this change fixed 14 and broke 0 (McNemar's exact test, p = 0.0001, very unlikely to be chance).

## Cost on google/gemma-4-26b-a4b-it

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,792 | 7,890 |
| Output tokens per routing decision | 81 | 92 |
| of which thinking tokens | 0 | 0 |
| Model calls per routing decision | 1.47 | 1.69 |
| Cost per 1,000 routing decisions | $0.77 | $0.63 |
| Cost of this run | $0.06 | $0.05 |

## Same traffic on other models

The token counts measured on google/gemma-4-26b-a4b-it, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.02 | $0.83 | 18% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $20.39 | $16.70 | 18% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $40.79 | $33.40 | 18% |
