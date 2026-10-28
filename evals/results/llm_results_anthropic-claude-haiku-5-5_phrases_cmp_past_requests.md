# LLM routing evaluation (anthropic/claude-haiku-5.5)

46 cases (spread across categories) x 3 repeats, 300 agents, seed 7.
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): on. 6 cases of the added categories (followup_past_request) are appended to the usual sample.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 47.8% | 95.7% |
| Cases right on all 3 repeats (pass^3) | 41.3% | 93.5% |
| Duplicate created | 31.6% | 0.0% |
| False reuse | 16.7% | 0.0% |
| Model did not route at all | 0.0% | 4.3% |
| Used search_agents | 0.0% | 8.0% |
| Prompt tokens, first call (from API usage) | 9,853 | 6,990 |
| Seconds per routing decision, p50 / p95 | 3.5 / 4.4 | 3.2 / 4.9 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 22/46 (47.8%) | 34.1% to 61.9% |
| This change | 44/46 (95.7%) | 85.5% to 98.8% |

Same cases, both versions: this change fixed 23 and broke 1 (McNemar's exact test, p = 3e-06, very unlikely to be chance).

### The usual 40-case sample only

Accuracy (all runs): 55.0% original, 95.0% this change. Right on all repeats: 47.5% original, 92.5% this change.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 22/40 (55.0%) | 39.8% to 69.3% |
| This change | 38/40 (95.0%) | 83.5% to 98.6% |

Same cases, both versions: this change fixed 17 and broke 1 (McNemar's exact test, p = 0.0001, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| followup_company_topic | 5 | 20.0% | 93.3% |
| followup_email | 5 | 26.7% | 100.0% |
| followup_first_name_topic | 5 | 40.0% | 100.0% |
| followup_full_name | 5 | 40.0% | 100.0% |
| followup_paraphrase | 4 | 75.0% | 100.0% |
| followup_past_request | 6 | 0.0% | 100.0% |
| followup_reminder | 4 | 100.0% | 100.0% |
| followup_stale | 4 | 50.0% | 100.0% |
| new_same_first_name | 4 | 66.7% | 58.3% |
| new_task | 4 | 100.0% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,853 | 7,655 |
| Output tokens per routing decision | 462 | 410 |
| of which thinking tokens | 172 | 155 |
| Model calls per routing decision | 1.00 | 1.09 |
| Cost per 1,000 routing decisions | $1.22 | $0.97 |
| Cost of this run | $0.17 | $0.13 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.22 | $0.97 | 20% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.33 | $19.41 | 20% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.65 | $38.81 | 20% |
