# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 55.6% | 88.9% |
| Cases right on all 3 repeats (pass^3) | 45.8% | 83.3% |
| Duplicate created | 44.4% | 11.1% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 34.7% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,393 |
| Seconds per routing decision, p50 / p95 | 3.4 / 4.4 | 3.7 / 6.2 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 13/24 (54.2%) | 35.1% to 72.1% |
| This change | 21/24 (87.5%) | 69.0% to 95.7% |

Same cases, both versions: this change fixed 10 and broke 2 (McNemar's exact test, p = 0.04, unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 10,333 | 9,297 |
| Output tokens per routing decision | 445 | 451 |
| of which thinking tokens | 159 | 145 |
| Model calls per routing decision | 1.00 | 1.44 |
| Cost per 1,000 routing decisions | $1.26 | $1.16 |
| Cost of this run | $0.09 | $0.08 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.26 | $1.16 | 8% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $25.12 | $23.11 | 8% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $50.24 | $46.22 | 8% |
