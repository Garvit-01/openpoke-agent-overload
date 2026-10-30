# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): on.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 55.6% | 91.7% |
| Cases right on all 3 repeats (pass^3) | 50.0% | 87.5% |
| Duplicate created | 44.4% | 5.6% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 2.8% |
| Used search_agents | 0.0% | 13.9% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,727 |
| Seconds per routing decision, p50 / p95 | 3.7 / 5.0 | 3.6 / 6.6 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 13/24 (54.2%) | 35.1% to 72.1% |
| This change | 22/24 (91.7%) | 74.2% to 97.7% |

Same cases, both versions: this change fixed 10 and broke 1 (McNemar's exact test, p = 0.012, unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| handwritten_names_person | 16 | 41.7% | 95.8% |
| handwritten_topic_only | 8 | 83.3% | 83.3% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 10,333 | 8,042 |
| Output tokens per routing decision | 455 | 435 |
| of which thinking tokens | 162 | 149 |
| Model calls per routing decision | 1.00 | 1.18 |
| Cost per 1,000 routing decisions | $1.26 | $1.02 |
| Cost of this run | $0.09 | $0.07 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.26 | $1.02 | 19% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $25.22 | $20.44 | 19% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $50.43 | $40.87 | 19% |
