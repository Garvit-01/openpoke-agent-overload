# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 50 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 57.5% | 95.0% |
| Cases right on all 3 repeats (pass^3) | 50.0% | 90.0% |
| Duplicate created | 25.0% | 3.1% |
| False reuse | 16.7% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 4.2% |
| Prompt tokens, first call (from API usage) | 5,253 | 6,604 |
| Seconds per routing decision, p50 / p95 | 3.2 / 4.3 | 2.9 / 4.6 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 23/40 (57.5%) | 42.2% to 71.5% |
| This change | 39/40 (97.5%) | 87.1% to 99.6% |

Same cases, both versions: this change fixed 16 and broke 0 (McNemar's exact test, p = 3e-05, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 5,253 | 6,939 |
| Output tokens per routing decision | 437 | 393 |
| of which thinking tokens | 152 | 130 |
| Model calls per routing decision | 1.00 | 1.04 |
| Cost per 1,000 routing decisions | $0.74 | $0.89 |
| Cost of this run | $0.09 | $0.11 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $0.74 | $0.89 | -20% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $14.88 | $17.80 | -20% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $29.75 | $35.61 | -20% |
