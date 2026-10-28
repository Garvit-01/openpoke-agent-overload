# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): off.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 59.7% | 94.4% |
| Cases right on all 3 repeats (pass^3) | 54.2% | 87.5% |
| Duplicate created | 36.1% | 4.2% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 1.4% |
| Used search_agents | 0.0% | 16.7% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,842 |
| Seconds per routing decision, p50 / p95 | 3.3 / 4.5 | 3.0 / 5.4 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 14/24 (58.3%) | 38.8% to 75.5% |
| This change | 23/24 (95.8%) | 79.8% to 99.3% |

Same cases, both versions: this change fixed 10 and broke 1 (McNemar's exact test, p = 0.012, unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| handwritten_names_person | 16 | 47.9% | 97.9% |
| handwritten_topic_only | 8 | 83.3% | 87.5% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 10,333 | 8,312 |
| Output tokens per routing decision | 475 | 422 |
| of which thinking tokens | 177 | 140 |
| Model calls per routing decision | 1.00 | 1.19 |
| Cost per 1,000 routing decisions | $1.27 | $1.04 |
| Cost of this run | $0.09 | $0.08 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.27 | $1.04 | 18% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $25.42 | $20.85 | 18% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $50.83 | $41.69 | 18% |
