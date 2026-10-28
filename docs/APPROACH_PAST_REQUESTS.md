# Approach: search each agent's past requests

Branch `approach-past-requests`, cut from `agent-overload` at `104bc97`. Not merged.

## The problem

Each agent's summary holds its first request, its latest request and its latest result. Anything asked in between is in the agent's log, but search can't see it.

The live demo showed this. A "Grocery list" agent was asked to "add 3 apples" in a middle request. Later follow-ups about the apples couldn't find the agent, because no searchable text said "apples".

## What it does

1. **Stores every request routed to an agent.** A new SQLite table, `agent_requests`, keeps the last 20 per agent, each clipped to 300 characters. The oldest is dropped first. If the same text comes again (a daily trigger, say), the old copy is removed and the new one goes to the end, so a repeating trigger can't push out the rest of the history.
2. **Writes it on both paths.** Chat requests are stored in `route_to_agent`, right after the agent is resolved, so they are searchable before the run ends. Trigger runs store the trigger payload only, not the "Trigger fired at" and metadata lines.
3. **Backfills once.** On startup, `get_agent_registry` reads each agent's log and stores its newest 20 distinct requests. A flag in a new `registry_meta` table makes this run only once. Agents that already have stored requests are skipped.
4. **Searches it as a second, weaker field.** For each agent, the request text is reduced to distinct words that its name, summary and entities don't already have. Each word counts once. These words get their own BM25 index, and that score is added at weight 0.5. Repeated words can't inflate the score, and a long history doesn't change the length normalisation of the main index.
5. **Shows why.** When an agent is listed because of a past request, the prompt's `matched` attribute says `past request: ...` with about 14 words around the match. Without this, a model would see an agent whose name and summary don't mention the item.
6. **Never merges.** The resolver passes `include_requests=False` on both of its searches (picking among agents that share an email, and listing possible duplicates). Only email addresses and thread ids are hard matches, as before.
7. **Has a switch.** `OPENPOKE_AGENT_INDEX_REQUESTS` (default `1` on this branch). With `0`, search is exactly what it was. Requests are still stored, so turning it on later has the history.

Files: `server/services/execution/registry.py`, `retrieval.py`, `resolver.py`, `routing.py`, `roster.py`, `server/services/trigger_scheduler.py`, `server/config.py`.

Tests: 18 new, 146 in total (128 before). They cover: a middle request makes the agent findable, the setting off gives the same ranking and scores, words already in the profile don't add score, a 20-request history doesn't outrank a name match, the cap and repeat handling, clipping, import and clear, backfill from a log (once only, newest 20), backfill at startup, the chat and trigger write paths, and two "never merges" cases.

## How it was measured

### Eval changes

The synthetic agents had one request each, so the feature had nothing to find. The scenario now adds request histories with a separate random generator. Existing cases, conversations and summaries are unchanged. A test checks that the new words appear in no other case or conversation message.

- Every agent stores its first request.
- 20% of thread agents get one to three everyday follow-ups, for example "Move the onboarding plan call with Vera to next week" or "Cc Paige Patel on the next reply". These are noise, and some name another contact on purpose.
- 12 of them also get a request for a unique item in the middle of their history, such as "Ask Priya to also send the dietary restrictions for the job offer". A new category, `followup_past_request`, asks about that item later. Half the cases give only the item ("Did the seating chart ever come through?"). Half also give the first name ("Has Priya sent the dietary restrictions yet?").

Assumption: summaries stay as generated, so every other category is measured on the same profiles as before. In the app, the summary would also show the latest ask. That is why the item request is never the latest one. The summary would not show it in either case.

The new category is built to need this feature, so its result shows that the mechanism works. It doesn't show how often real users ask about a middle request.

The baseline arm (the original OpenPoke copy) only sees agent names. It is unaffected, and its numbers on the 185 existing cases match the committed run exactly (107/185).

The real-model sample keeps the same 40 cases as the committed runs, checked by case id, and appends 6 cases of the new category.

### A bug fixed along the way

`python -m evals.run_eval --set paraphrase --phrases` failed at `104bc97` with "No cached phrases for 74 agents". The phrase cache is keyed by agent name. The 800-agent run had overwritten 74 entries that share a name with a 300-agent scenario agent. The 300-agent cache is restored from `32f6566`, which holds the phrases the committed runs used. The 800-agent cache moves to `agent_phrases_800agents.json`. No phrases were regenerated.

## Results: simulated router (free)

`python -m evals.run_eval --tag requests_off|requests_on` and `--set paraphrase --phrases --tag ...`. 300 agents, seed 7. Files: `evals/results/results_requests_{off,on}.md`, `results_paraphrase_phrases_requests_{off,on}.md`.

With the setting off, the results match the committed `results.md` and `results_paraphrase_phrases.md` on every case. Only routing time differs, because the machine was busy.

### Generated set, this change's arm

| | Committed | Off | On |
|---|---|---|---|
| Existing 185 cases right | 172 (93.0%) | 172 (93.0%) | 173 (93.5%) |
| Existing cases, recall@12 | 145/145 | 145/145 | 145/145 |
| Existing cases, in search_agents top 8 | 139/145 | 139/145 | 140/145 |
| New `followup_past_request`, right | n/a | 0/12 | 12/12 |
| New category, recall@12 | n/a | 6/12 | 12/12 |
| Roster tokens per turn, existing cases | 978 | 978 | 981 |
| Roster tokens per turn, new cases | n/a | 632 | 702 |
| Multi-turn conversations, turns right | 100% | 100% | 100% |

The original OpenPoke gets 1/12 on the new category.

Off to on, same cases:
- existing 185 cases: fixed 1, broke 0 (McNemar's exact test, p = 1, could be chance);
- all 197 cases: fixed 13, broke 0 (p = 0.0002, very unlikely to be chance).

The one existing fix is a coincidence. "Any news from Vera about the first week schedule for the new hire?" now finds "Onboarding Plan with Vera" because a noise request for that agent says "next week". It is not a real paraphrase win.

Without past requests, the 6 named cases reach the prompt through the person's name, but the router then picks another agent of that person. The 6 item-only cases don't reach the prompt at all.

Accuracy over all 197 cases (new category included) across seeds, roster sizes and router thresholds:

| | Off | On |
|---|---|---|
| Seeds 7 / 11 / 23 | 87.3% / 86.8% / 86.8% | 93.9% / 92.9% / 92.4% |
| 50 / 100 / 200 / 400 / 800 agents | 85.0 / 87.6 / 87.8 / 86.8 / 86.8% | 91.5 / 93.0 / 92.9 / 92.4 / 92.4% |
| Roster tokens, 50 and 800 agents | 781 / 972 | 803 / 982 |
| Threshold 0.5 / 0.6 / 0.75 | 83.8 / 87.3 / 87.8% | 90.4 / 93.9 / 94.4% |

The gap is the 12 new cases at every size and setting. Nothing else moved.

### Paraphrase set, with search phrases

| | Committed | Off | On |
|---|---|---|---|
| Right (simulated router) | 13/24 | 13/24 | 13/24 |
| Recall@12 | 21/24 | 21/24 | 21/24 |
| Roster tokens per turn | 860 | 860 | 866 |

Fixed 0, broke 0. The same three agents are still missing from the prompt: School Enrollment ("kids get a place at the academy"), Orchard Manual Translation and Ironbridge Laptop Refresh. Their messages share no words with any request, so this approach can't help them.

### Routing time

The timing in the eval reports was taken while other agents' processes were running, so I measured it again. Off and on alternated case by case on the same registry, 3 passes, so machine load hit both the same way:

| | Off, p50 / p95 | On, p50 / p95 |
|---|---|---|
| 300 agents | 46.6 / 96.6 ms | 53.8 / 115.3 ms |
| 800 agents | 113.8 / 177.7 ms | 134.2 / 188.8 ms |

That is about 15 to 20% more routing time, or 7 to 20 ms per turn. Most of it comes from loading all stored requests and building a second index on every search. The absolute numbers are higher than the committed 26 / 70 ms because the machine was busy. Compare the two columns with each other.

## Results: Claude Haiku 5.5 (paid)

Run only after the simulated results came out neutral to positive. Files: `evals/results/llm_results_anthropic-claude-haiku-5-5_phrases_requests_on.md` and `..._paraphrase_phrases_requests_on.md`. Both arms were rerun. The original arm is unaffected by design, so its movement shows the run-to-run noise.

### Generated set: the same 40 cases plus 6 new ones, 3 repeats

| | Committed (on 40) | Now, same 40 | Now, 6 new | Now, all 46 |
|---|---|---|---|---|
| Original, pass^1 | 58.3% | 57.5% | 0.0% | 50.0% |
| This change, pass^1 | 99.2% | 100.0% | 100.0% | 100.0% |
| Original, pass^3 | 55.0% | 52.5% | | 45.7% |
| This change, pass^3 | 97.5% | 100.0% | | 100.0% |
| Majority right, original vs this change | 24/40 vs 40/40 | 22/40 vs 40/40 | | 22/46 vs 46/46 |
| Fixed / broke vs original | 16 / 0, p = 3e-05 | 18 / 0, p = 8e-06 | | 24 / 0, p = 1e-07 |

On the same 40 cases, this change's arm has the same majority result as the committed run: fixed 0, broke 0. The committed run's one miss was "did not route" on a stale follow-up (2/3). It is now 3/3. Its prompt didn't change with past requests on, so that is noise, not this feature.

On the 6 new cases, Haiku picked the right agent in all 18 runs, using the `past request:` line in the prompt. I did not run the off setting on the real model, to save credit. On the simulated router the off setting got 0/12, and 6 of the 12 never reached the prompt.

Prompt tokens on the first call: 6,866, against 6,915 committed. Cost per 1,000 routing decisions: $0.91.

### Paraphrase set: 24 hand-written cases, 3 repeats

| | Committed | Now (on) |
|---|---|---|
| Original, pass^1 / pass^3 | 62.5% / 54.2% | 55.6% / 50.0% |
| This change, pass^1 / pass^3 | 94.4% / 83.3% | 91.7% / 87.5% |
| Majority right, this change | 24/24 | 22/24 |
| Fixed / broke vs original | 9 / 0, p = 0.0039 | 10 / 1, p = 0.012 |

This change's arm, per case against the committed run:
- worse: Email to Alex Moreau (2/3 to 1/3, the model did not route) and School Enrollment with Jasmine (2/3 to 0/3, a new agent "Academy admission status");
- better: Orchard Manual Translation (2/3 to 3/3).

I checked whether past requests caused these. For all three, the prompt was byte for byte the same with the setting off and on. Only 2 of the 24 paraphrase prompts changed at all (hw07 and hw12), and their outcomes didn't move. The Alex Moreau runs used no search. The other two used search_agents. The model's search queries are not saved, but 15 likely queries ("kids academy place", "school enrollment", "Japanese instructions booklet" and so on) return the same top 8 with the setting off and on. So these changes are most likely model noise. The original arm moved by a similar amount (62.5% to 55.6%) on identical prompts. I can't rule out that the model sent a query I didn't try.

### Cost

| Run | Original arm | This change | Total |
|---|---|---|---|
| Generated set, 46 cases x 3 | $0.17 | $0.13 | $0.30 |
| Paraphrase set, 24 cases x 3 | $0.09 | $0.07 | $0.16 |

That is about $0.46, at the list prices the reports fetch. One earlier attempt at the generated run stopped on a network error ("All connection attempts failed") before writing any results. A few calls may have been billed before it stopped. No phrases were regenerated.

## What it fixes and what it doesn't

It fixes:
- follow-ups about something asked in the middle of an agent's history, the Grocery list case: 0/12 to 12/12 simulated, 18/18 runs on Haiku;
- named follow-ups where several agents share the person and only a middle request tells them apart.

It doesn't fix:
- paraphrases. A request is still keywords. "kids get a place at the academy" shares no word with any request;
- plurals and typos. It uses the same `tokenize`, so "apple" doesn't match "apples". Another branch works on that;
- anything older than the last 20 distinct requests;
- the summary itself, which still shows only the first and latest request.

Costs:
- about 15 to 20% more routing time per turn;
- about 3 more roster tokens per turn on existing cases, and about 70 on cases where a past request matches;
- one more SQLite table, and a one-time log read at startup.

Risk: request words are noisy. In the eval, a noise request containing "week" pulled an agent into a paraphrase case. That happened to be the right agent, but the same thing could list a wrong one. The 0.5 weight and the one-count-per-word rule kept this from breaking any existing case here. Real request text is longer and more varied than the synthetic noise, so the risk in real use is likely higher than measured.

## Verdict

Merge it, as a setting that defaults to on, with two caveats.

- **For:** it fixes a failure seen in the live demo, at low cost. It broke 0 existing cases across the simulated runs (185 generated, 24 paraphrase, 92 conversation turns) and 0 on the same 40 real-model cases. It can't merge agents. One setting turns it off.
- **Caveat 1:** the evidence for the gain comes from a category built to need it. It shows that the mechanism works, not how often it matters. The routing log should show how often a chosen agent was found only through a past request.
- **Caveat 2:** the real-model paraphrase run ended 22/24 against 24/24 committed. My checks point to model noise, not this change, but the run can't prove it. A rerun with the setting off on the same day would settle it, for about $0.16.

If time is short, this is the cheapest of the alternatives to keep. If routing time matters at thousands of agents, the request index could be cached between turns instead of rebuilt on every search.
