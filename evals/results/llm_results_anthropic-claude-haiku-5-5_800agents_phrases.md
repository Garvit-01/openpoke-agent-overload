# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 800 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 54.2% | 100.0% |
| Cases right on all 3 repeats (pass^3) | 52.5% | 100.0% |
| Duplicate created | 44.8% | 0.0% |
| False reuse | 8.3% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 4.2% |
| Prompt tokens, first call (from API usage) | 19,471 | 6,950 |
| Seconds per routing decision, p50 / p95 | 3.7 / 4.9 | 3.4 / 5.4 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 21/40 (52.5%) | 37.5% to 67.1% |
| This change | 40/40 (100.0%) | 91.2% to 100.0% |

Same cases, both versions: this change fixed 19 and broke 0 (McNemar's exact test, p = 4e-06, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 19,471 | 7,287 |
| Output tokens per routing decision | 487 | 415 |
| of which thinking tokens | 200 | 151 |
| Model calls per routing decision | 1.00 | 1.04 |
| Cost per 1,000 routing decisions | $2.19 | $0.94 |
| Cost of this run | $0.26 | $0.11 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $2.19 | $0.94 | 57% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $43.82 | $18.72 | 57% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $87.63 | $37.44 | 57% |
