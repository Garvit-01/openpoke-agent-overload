# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): on.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 52.8% | 100.0% |
| Cases right on all 3 repeats (pass^3) | 45.8% | 100.0% |
| Duplicate created | 43.1% | 0.0% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 6.9% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,866 |
| Seconds per routing decision, p50 / p95 | 3.8 / 5.2 | 3.6 / 5.1 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 13/24 (54.2%) | 35.1% to 72.1% |
| This change | 24/24 (100.0%) | 86.2% to 100.0% |

Same cases, both versions: this change fixed 11 and broke 0 (McNemar's exact test, p = 0.001, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| handwritten_names_person | 16 | 43.8% | 100.0% |
| handwritten_topic_only | 8 | 70.8% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 10,333 | 7,413 |
| Output tokens per routing decision | 454 | 413 |
| of which thinking tokens | 160 | 137 |
| Model calls per routing decision | 1.00 | 1.07 |
| Cost per 1,000 routing decisions | $1.26 | $0.95 |
| Cost of this run | $0.09 | $0.07 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.26 | $0.95 | 25% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $25.20 | $18.96 | 25% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $50.40 | $37.92 | 25% |
