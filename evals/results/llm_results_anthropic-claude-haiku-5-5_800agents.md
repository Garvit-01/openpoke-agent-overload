# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 800 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 53.3% | 93.3% |
| Cases right on all 3 repeats (pass^3) | 45.0% | 90.0% |
| Duplicate created | 47.9% | 7.3% |
| False reuse | 4.2% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 14.2% |
| Prompt tokens, first call (from API usage) | 19,471 | 6,914 |
| Seconds per routing decision, p50 / p95 | 3.6 / 5.5 | 3.1 / 6.0 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 21/40 (52.5%) | 37.5% to 67.1% |
| This change | 38/40 (95.0%) | 83.5% to 98.6% |

Same cases, both versions: this change fixed 17 and broke 0 (McNemar's exact test, p = 2e-05, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 19,471 | 8,369 |
| Output tokens per routing decision | 488 | 422 |
| of which thinking tokens | 196 | 147 |
| Model calls per routing decision | 1.00 | 1.18 |
| Cost per 1,000 routing decisions | $2.19 | $1.05 |
| Cost of this run | $0.26 | $0.13 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $2.19 | $1.05 | 52% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $43.82 | $20.96 | 52% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $87.65 | $41.92 | 52% |
