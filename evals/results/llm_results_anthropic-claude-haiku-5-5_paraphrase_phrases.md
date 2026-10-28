# LLM routing evaluation (anthropic/claude-haiku-5.5)

24 cases (spread across categories) x 3 repeats, 300 agents, seed 7.

| Metric | Original OpenPoke | This change |
|---|---|---|
| Accuracy (all runs, pass^1) | 51.4% | 97.2% |
| Cases right on all 3 repeats (pass^3) | 37.5% | 95.8% |
| Duplicate created | 47.2% | 0.0% |
| False reuse | 0.0% | 0.0% |
| Model did not route at all | 0.0% | 2.8% |
| Used search_agents | 0.0% | 5.6% |
| Prompt tokens, first call (from API usage) | 10,333 | 6,860 |
| Seconds per routing decision, p50 / p95 | 3.3 / 4.7 | 3.2 / 4.9 |

Seconds cover every model call for the decision and depend on the provider's load that day, so compare the two columns with each other, not with other runs.

## Is the difference real?

A case counts as right when more than half of its 3 runs were right.

| | Right | 95% interval |
|---|---|---|
| Original OpenPoke | 13/24 (54.2%) | 35.1% to 72.1% |
| This change | 23/24 (95.8%) | 79.8% to 99.3% |

Same cases, both versions: this change fixed 10 and broke 0 (McNemar's exact test, p = 0.002, unlikely to be chance).
