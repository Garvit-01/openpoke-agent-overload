# LLM routing evaluation (google/gemma-4-26b-a4b-it)

40 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 44.2% | 70.0% |
| Cases right on all 3 repeats (pass^3) | 25.0% | 47.5% |
| Duplicate created | 11.5% | 0.0% |
| False reuse | 0.0% | 4.2% |
| Model did not route at all | 24.2% | 25.0% |
| Used search_agents | 0.0% | 25.8% |
| Prompt tokens, first call (from API usage) | 6,317 | 4,623 |
| Seconds per routing decision, p50 / p95 | 2.1 / 6.2 | 2.4 / 6.3 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 19/40 (47.5%) | 32.9% to 62.5% |
| This change | 29/40 (72.5%) | 57.2% to 83.9% |

Same cases, both versions: this change fixed 14 and broke 4 (McNemar's exact test, p = 0.031, unlikely to be chance).

## Cost on google/gemma-4-26b-a4b-it

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 8,949 | 8,484 |
| Output tokens per routing decision | 73 | 89 |
| of which thinking tokens | 0 | 0 |
| Model calls per routing decision | 1.41 | 1.75 |
| Cost per 1,000 routing decisions | $0.70 | $0.67 |
| Cost of this run | $0.08 | $0.08 |

## Same traffic on other models

The token counts measured on google/gemma-4-26b-a4b-it, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $0.93 | $0.89 | 4% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $18.63 | $17.86 | 4% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $37.26 | $35.72 | 4% |
