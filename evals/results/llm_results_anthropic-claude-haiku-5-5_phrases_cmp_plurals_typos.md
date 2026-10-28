# LLM routing evaluation (anthropic/claude-haiku-5.5)

46 cases (spread across categories) x 3 repeats, 300 agents, seed 7.
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): off. 6 cases of the added categories (followup_past_request) are appended to the usual sample.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 50.7% | 84.8% |
| Cases right on all 3 repeats (pass^3) | 45.7% | 80.4% |
| Duplicate created | 30.7% | 13.2% |
| False reuse | 16.7% | 0.0% |
| Model did not route at all | 0.0% | 2.9% |
| Used search_agents | 0.0% | 18.1% |
| Prompt tokens, first call (from API usage) | 9,853 | 6,974 |
| Seconds per routing decision, p50 / p95 | 3.3 / 4.3 | 3.3 / 5.8 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/46 (52.2%) | 38.1% to 65.9% |
| This change | 39/46 (84.8%) | 71.8% to 92.4% |

Same cases, both versions: this change fixed 16 and broke 1 (McNemar's exact test, p = 0.0003, very unlikely to be chance).

### The usual 40-case sample only

Accuracy (all runs): 58.3% original, 96.7% this change. Right on all repeats: 52.5% original, 92.5% this change.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/40 (60.0%) | 44.6% to 73.7% |
| This change | 39/40 (97.5%) | 87.1% to 99.6% |

Same cases, both versions: this change fixed 16 and broke 1 (McNemar's exact test, p = 0.0003, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| followup_company_topic | 5 | 20.0% | 93.3% |
| followup_email | 5 | 33.3% | 100.0% |
| followup_first_name_topic | 5 | 40.0% | 100.0% |
| followup_full_name | 5 | 60.0% | 100.0% |
| followup_paraphrase | 4 | 75.0% | 91.7% |
| followup_past_request | 6 | 0.0% | 5.6% |
| followup_reminder | 4 | 100.0% | 100.0% |
| followup_stale | 4 | 50.0% | 100.0% |
| new_same_first_name | 4 | 66.7% | 83.3% |
| new_task | 4 | 100.0% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,853 | 8,356 |
| Output tokens per routing decision | 452 | 423 |
| of which thinking tokens | 169 | 153 |
| Model calls per routing decision | 1.00 | 1.20 |
| Cost per 1,000 routing decisions | $1.21 | $1.05 |
| Cost of this run | $0.17 | $0.14 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.21 | $1.05 | 14% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.23 | $20.94 | 14% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.46 | $41.89 | 14% |
