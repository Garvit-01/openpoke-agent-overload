# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 54.2% | 97.5% |
| Cases right on all 3 repeats (pass^3) | 47.5% | 95.0% |
| Duplicate created | 17.7% | 2.1% |
| False reuse | 29.2% | 0.0% |
| Model did not route at all | 0.0% | 0.8% |
| Used search_agents | 0.0% | 10.8% |
| Prompt tokens, first call (from API usage) | 9,854 | 6,885 |
| Seconds per routing decision, p50 / p95 | 3.3 / 4.4 | 3.1 / 5.1 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 22/40 (55.0%) | 39.8% to 69.3% |
| This change | 39/40 (97.5%) | 87.1% to 99.6% |

Same cases, both versions: this change fixed 17 and broke 0 (McNemar's exact test, p = 2e-05, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,854 | 7,809 |
| Output tokens per routing decision | 462 | 426 |
| of which thinking tokens | 181 | 156 |
| Model calls per routing decision | 1.00 | 1.12 |
| Cost per 1,000 routing decisions | $1.22 | $0.99 |
| Cost of this run | $0.15 | $0.12 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.22 | $0.99 | 18% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.33 | $19.88 | 18% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.66 | $39.75 | 18% |
