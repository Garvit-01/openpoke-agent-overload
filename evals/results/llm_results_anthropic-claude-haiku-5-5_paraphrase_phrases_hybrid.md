# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7, agent search: hybrid (keywords + local embeddings).

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 63.9% | 100.0% |
| Cases right on all 3 repeats (pass^3) | 58.3% | 100.0% |
| Duplicate created | 34.7% | 0.0% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 4.2% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,830 |
| Seconds per routing decision, p50 / p95 | 4.0 / 5.9 | 3.6 / 5.9 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 15/24 (62.5%) | 42.7% to 78.8% |
| This change | 24/24 (100.0%) | 86.2% to 100.0% |

Same cases, both versions: this change fixed 9 and broke 0 (McNemar's exact test, p = 0.0039, unlikely to be chance).

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 10,333 | 7,148 |
| Output tokens per routing decision | 457 | 414 |
| of which thinking tokens | 169 | 135 |
| Model calls per routing decision | 1.00 | 1.04 |
| Cost per 1,000 routing decisions | $1.26 | $0.92 |
| Cost of this run | $0.09 | $0.07 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.26 | $0.92 | 27% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $25.24 | $18.43 | 27% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $50.47 | $36.86 | 27% |
