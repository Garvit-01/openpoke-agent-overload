# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 58.3% | 99.2% |
| Cases right on all 3 repeats (pass^3) | 55.0% | 97.5% |
| Duplicate created | 16.7% | 0.0% |
| False reuse | 16.7% | 0.0% |
| Model did not route at all | 0.0% | 0.8% |
| Used search_agents | 0.0% | 1.7% |
| Prompt tokens, first call (from API usage) | 9,854 | 6,915 |
| Seconds per routing decision, p50 / p95 | 3.3 / 4.2 | 3.1 / 4.1 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/40 (60.0%) | 44.6% to 73.7% |
| This change | 40/40 (100.0%) | 91.2% to 100.0% |

Same cases, both versions: this change fixed 16 and broke 0 (McNemar's exact test, p = 3e-05, very unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,854 | 7,041 |
| Output tokens per routing decision | 458 | 401 |
| of which thinking tokens | 176 | 141 |
| Model calls per routing decision | 1.00 | 1.02 |
| Cost per 1,000 routing decisions | $1.21 | $0.90 |
| Cost of this run | $0.15 | $0.11 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.21 | $0.90 | 26% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.29 | $18.09 | 26% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.58 | $36.19 | 26% |
