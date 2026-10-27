# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 58.3% | 97.5% |
| Cases right on all 3 repeats (pass^3) | 52.5% | 97.5% |
| Duplicate created | 15.6% | 3.1% |
| False reuse | 8.3% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 5.8% |
| Prompt tokens, first call (from API usage) | 9,854 | 6,690 |
| Seconds per routing decision, p50 / p95 | 3.4 / 4.5 | 2.9 / 4.7 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/40 (60.0%) | 44.6% to 73.7% |
| This change | 39/40 (97.5%) | 87.1% to 99.6% |

Same cases, both versions: this change fixed 15 and broke 0 (McNemar's exact test, p = 6e-05, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,854 | 7,134 |
| Output tokens per routing decision | 476 | 399 |
| of which thinking tokens | 184 | 130 |
| Model calls per routing decision | 1.00 | 1.06 |
| Cost per 1,000 routing decisions | $1.22 | $0.91 |
| Cost of this run | $0.15 | $0.11 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.22 | $0.91 | 25% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.47 | $18.26 | 25% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.94 | $36.52 | 25% |
