# LLM routing evaluation (anthropic/claude-haiku-5.5)

46 cases (spread across categories) x 3 repeats, 300 agents, seed 7, agent search: hybrid (keywords + local embeddings).
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): off. 6 cases of the added categories (followup_past_request) are appended to the usual sample.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 50.0% | 87.0% |
| Cases right on all 3 repeats (pass^3) | 43.5% | 87.0% |
| Duplicate created | 29.8% | 14.0% |
| False reuse | 20.8% | 0.0% |
| Model did not route at all | 0.0% | 0.7% |
| Used search_agents | 0.0% | 17.4% |
| Prompt tokens, first call (from API usage) | 9,853 | 7,036 |
| Seconds per routing decision, p50 / p95 | 3.4 / 4.3 | 3.3 / 6.3 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 23/46 (50.0%) | 36.1% to 63.9% |
| This change | 40/46 (87.0%) | 74.3% to 93.9% |

Same cases, both versions: this change fixed 17 and broke 0 (McNemar's exact test, p = 2e-05, very unlikely to be chance).

### The usual 40-case sample only

Accuracy (all runs): 57.5% original, 100.0% this change. Right on all repeats: 50.0% original, 100.0% this change.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 23/40 (57.5%) | 42.2% to 71.5% |
| This change | 40/40 (100.0%) | 91.2% to 100.0% |

Same cases, both versions: this change fixed 17 and broke 0 (McNemar's exact test, p = 2e-05, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| followup_company_topic | 5 | 20.0% | 100.0% |
| followup_email | 5 | 33.3% | 100.0% |
| followup_first_name_topic | 5 | 40.0% | 100.0% |
| followup_full_name | 5 | 60.0% | 100.0% |
| followup_paraphrase | 4 | 75.0% | 100.0% |
| followup_past_request | 6 | 0.0% | 0.0% |
| followup_reminder | 4 | 100.0% | 100.0% |
| followup_stale | 4 | 50.0% | 100.0% |
| new_same_first_name | 4 | 58.3% | 100.0% |
| new_task | 4 | 100.0% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,853 | 8,775 |
| Output tokens per routing decision | 458 | 446 |
| of which thinking tokens | 174 | 165 |
| Model calls per routing decision | 1.00 | 1.22 |
| Cost per 1,000 routing decisions | $1.21 | $1.10 |
| Cost of this run | $0.17 | $0.15 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.21 | $1.10 | 9% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.29 | $22.00 | 9% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.58 | $44.01 | 9% |
