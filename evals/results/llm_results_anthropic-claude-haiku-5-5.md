# LLM routing evaluation (anthropic/claude-haiku-5.5)

40 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs) | 57.5% | 95.0% |
| Cases right on every repeat | 52.5% | 95.0% |
| Duplicate created | 17.7% | 1.0% |
| False reuse | 12.5% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 5.8% |
| Prompt tokens, first call (from API usage) | 9,854 | 6,517 |
| Seconds per routing decision, p50 / p95 | 3.2 / 4.2 | 3.0 / 4.4 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,854 | 6,943 |
| Output tokens per routing decision | 461 | 422 |
| of which thinking tokens | 178 | 143 |
| Model calls per routing decision | 1.00 | 1.06 |
| Cost per 1,000 routing decisions | $1.22 | $0.91 |
| Cost of this run | $0.15 | $0.11 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.22 | $0.91 | 26% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.32 | $18.10 | 26% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.64 | $36.21 | 26% |
