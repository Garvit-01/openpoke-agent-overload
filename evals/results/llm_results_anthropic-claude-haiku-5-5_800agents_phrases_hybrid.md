# LLM routing evaluation (anthropic/claude-haiku-5.5)

46 cases (spread across categories) x 3 repeats, 800 agents, seed 7, agent search: hybrid (keywords + local embeddings).
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): on. 6 cases of the added categories (followup_past_request) are appended to the usual sample.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 46.4% | 97.8% |
| Cases right on all 3 repeats (pass^3) | 41.3% | 95.7% |
| Duplicate created | 57.0% | 0.9% |
| False reuse | 8.3% | 0.0% |
| Model did not route at all | 0.0% | 1.4% |
| Used search_agents | 0.0% | 7.2% |
| Prompt tokens, first call (from API usage) | 19,471 | 7,080 |
| Seconds per routing decision, p50 / p95 | 3.7 / 5.0 | 3.4 / 5.9 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 22/46 (47.8%) | 34.1% to 61.9% |
| This change | 45/46 (97.8%) | 88.7% to 99.6% |

Same cases, both versions: this change fixed 23 and broke 0 (McNemar's exact test, p = 2e-07, very unlikely to be chance).

### The usual 40-case sample only

Accuracy (all runs): 53.3% original, 97.5% this change. Right on all repeats: 47.5% original, 95.0% this change.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 22/40 (55.0%) | 39.8% to 69.3% |
| This change | 39/40 (97.5%) | 87.1% to 99.6% |

Same cases, both versions: this change fixed 17 and broke 0 (McNemar's exact test, p = 2e-05, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| followup_company_topic | 5 | 20.0% | 93.3% |
| followup_email | 5 | 20.0% | 100.0% |
| followup_first_name_topic | 5 | 40.0% | 100.0% |
| followup_full_name | 5 | 53.3% | 100.0% |
| followup_paraphrase | 4 | 25.0% | 100.0% |
| followup_past_request | 6 | 0.0% | 100.0% |
| followup_reminder | 4 | 100.0% | 100.0% |
| followup_stale | 4 | 58.3% | 100.0% |
| new_same_first_name | 4 | 83.3% | 83.3% |
| new_task | 4 | 100.0% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 19,471 | 7,723 |
| Output tokens per routing decision | 473 | 415 |
| of which thinking tokens | 187 | 157 |
| Model calls per routing decision | 1.00 | 1.08 |
| Cost per 1,000 routing decisions | $2.18 | $0.98 |
| Cost of this run | $0.30 | $0.14 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $2.18 | $0.98 | 55% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $43.68 | $19.60 | 55% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $87.35 | $39.19 | 55% |
