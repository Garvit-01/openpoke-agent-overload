# LLM routing evaluation (anthropic/claude-haiku-5.5)

46 cases (spread across categories) x 3 repeats, 300 agents, seed 7, agent search: hybrid (keywords + local embeddings).
Past requests indexed (OPENPOKE_AGENT_INDEX_REQUESTS): on. 6 cases of the added categories (followup_past_request) are appended to the usual sample.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 50.7% | 100.0% |
| Cases right on all 3 repeats (pass^3) | 45.7% | 100.0% |
| Duplicate created | 33.3% | 0.0% |
| False reuse | 16.7% | 0.0% |
| Model did not route at all | 0.0% | 0.0% |
| Used search_agents | 0.0% | 3.6% |
| Prompt tokens, first call (from API usage) | 9,853 | 7,049 |
| Seconds per routing decision, p50 / p95 | 3.4 / 4.4 | 3.1 / 4.2 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/46 (52.2%) | 38.1% to 65.9% |
| This change | 46/46 (100.0%) | 92.3% to 100.0% |

Same cases, both versions: this change fixed 22 and broke 0 (McNemar's exact test, p = 5e-07, very unlikely to be chance).

### The usual 40-case sample only

Accuracy (all runs): 58.3% original, 100.0% this change. Right on all repeats: 52.5% original, 100.0% this change.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 24/40 (60.0%) | 44.6% to 73.7% |
| This change | 40/40 (100.0%) | 91.2% to 100.0% |

Same cases, both versions: this change fixed 16 and broke 0 (McNemar's exact test, p = 3e-05, very unlikely to be chance).

## By category

| Category | Cases | Original OpenPoke | This change |
|---|---|---|---|
| followup_company_topic | 5 | 20.0% | 100.0% |
| followup_email | 5 | 20.0% | 100.0% |
| followup_first_name_topic | 5 | 40.0% | 100.0% |
| followup_full_name | 5 | 73.3% | 100.0% |
| followup_paraphrase | 4 | 75.0% | 100.0% |
| followup_past_request | 6 | 0.0% | 100.0% |
| followup_reminder | 4 | 100.0% | 100.0% |
| followup_stale | 4 | 50.0% | 100.0% |
| new_same_first_name | 4 | 66.7% | 100.0% |
| new_task | 4 | 100.0% | 100.0% |

## Cost on anthropic/claude-haiku-5.5

| | Original OpenPoke | This change |
|---|---|---|
| Input tokens per routing decision | 9,853 | 7,322 |
| Output tokens per routing decision | 454 | 410 |
| of which thinking tokens | 172 | 153 |
| Model calls per routing decision | 1.00 | 1.04 |
| Cost per 1,000 routing decisions | $1.21 | $0.94 |
| Cost of this run | $0.17 | $0.13 |

## Same traffic on other models

The token counts measured on anthropic/claude-haiku-5.5, priced at each model's list price. Another model could make a different number of calls (for example use search_agents more), so this is an estimate.

| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |
|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | $0.10 / $0.50 | $1.21 | $0.94 | 23% |
| anthropic/claude-sonnet-5.5 | $2.00 / $10.00 | $24.25 | $18.74 | 23% |
| anthropic/claude-opus-5.5 | $4.00 / $20.00 | $48.49 | $37.49 | 23% |
