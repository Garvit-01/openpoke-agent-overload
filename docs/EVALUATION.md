# Evaluation details

Everything behind the numbers in the [README](../README.md): the tests, how the evaluator works, every run, and what each one showed. Result files are in [evals/results/](../evals/results/).

## How I tested it

### Unit and integration tests: `python -m pytest`

143 tests, about a minute, no network, no API keys. They cover:

- registry create, reuse, revive, entity lookup, persistence, and migration from `roster.json`
- agent ids: the four names that used to share `email-to-sarah.log` now keep separate histories, and old slug logs are carried over without overwriting anything
- the run lock: two requests to one agent run one after the other and the second sees the first's result, different agents still overlap, chat and trigger managers share the lock, waiting doesn't trip the timeout
- retrieval: email beats keywords, two Sarahs told apart by topic, archived agents still found, the shortlist size is fixed
- the named-people list: the Vera case from the real-model eval, where keywords leave the right agent out and the list brings it back. It only lists people the message names, never lists an agent twice, and is capped.
- the resolver: each reuse rule, plus the cases where it must *not* merge (a new contact, an ambiguous name, a common address like the user's own, a new task flagged with `new_task`)
- lifecycle: thresholds, never demoting trigger owners, revive on use
- prompt budget: the roster section stays the same size from 20 to 1,000 agents, while the original grows linearly
- the outbound email guard (see "Other gaps I found" in the README)
- an end-to-end turn through the real interaction and execution runtimes with only the model calls faked: create an agent, refresh its profile after it runs, route a reworded follow-up back to it, check the next prompt carries its summary
- the evaluator itself: labels are unambiguous, the simulated router follows its stated rules, a wrong first turn carries over to later turns, the LLM loop works with a fake model

Two autouse fixtures in `tests/conftest.py` keep tests from touching anything real. Every app data file (conversation log, working memory, agent logs, routing log, registry) points at a temp folder, and no test can call a model, even with a key in `.env`. I added the first after finding that the end-to-end test had been appending fake turns to the real working-memory log.

I also ran the real server once with Haiku for every role, using reminders since Gmail isn't connected. "Remind me to water the plants every Sunday at 9am" created an agent, wrote its registry entry and routing log line, and added 10 search phrases in the background. "Can you move the greenery thing to Saturdays instead?" then went back to the same agent, with no duplicate.

### The evaluator: `python -m evals.run_eval`

**Scenario** (`evals/scenarios.py`). This is a generated history, not real data. There are 300 agents built up over about 10 weeks: email threads, reminders and searches. It's built to contain what causes overload:

- several contacts sharing a first name
- loose agent names like "Email to Sarah"
- one contact on several threads
- threads that come back after weeks
- new requests that look like old ones

The 185 follow-up messages are labelled with the right agent, or "a new agent is right". They fall into 9 categories. One is deliberately hard for my approach: the topic is paraphrased ("the event space agreement" for "venue contract"). A separate set of 24 hand-written paraphrases (`evals/paraphrase_set.py`) avoids the generator's vocabulary altogether. See "Hand-written paraphrases" below.

There are also 26 short conversations (92 turns), because real follow-ups lean on what was just said. There are four kinds:

- **switch and return:** talk about A, switch to B, then "go back to the first one and send that draft"
- **correction:** "Any update from Michael?" then "Not that one, I meant Michael Mendes at Aperture", then "tell them Thursday works"
- **new then follow-up:** email someone new, then "ok, send it" and "did they reply yet?"
- **same person, new topic:** a known contact, then "start a new email to them about something else", then "send it", then back to the old topic

State carries over between turns, so a wrong pick in turn 1 makes "send it" go to the wrong agent as well. The solution arm runs each conversation through the real `route_to_agent` on a fresh copy of the registry.

**Two arms, one router** (`evals/harness.py`). Both arms use the same deterministic router, which stands in for the model choosing an agent. The only thing that differs is what each arm gives it:

- **Original:** every agent name and nothing else. Reuse only on an exact name match. This is copied from upstream commit `5b5f635`, in `evals/baseline.py`.
- **This change:** the real shortlist, `search_agents` and the resolver, imported from the server code rather than re-implemented.

The router's rules are written out in `SimulatedRouter`'s docstring. In short:

1. A visible email address decides.
2. Otherwise a named person decides. A different surname rules an agent out, and the topic picks between several agents with the same name.
3. Otherwise keyword coverage has to clear a threshold.
4. In a conversation, a turn that names nobody and nothing ("ok, send it") goes to the agent the conversation is about. Other turns check the agents already used in the conversation before the roster.

My first version of the router was unfair to the original code. It would create a new agent for "Did Michael Turner reply?" even with "Email to Michael Turner" in the list. I fixed that before taking any numbers, because a strawman baseline would prove nothing.

**Metrics.**
- accuracy
- three ways to be wrong: duplicate, wrong existing agent, false reuse
- recall of the shortlist and of search
- roster tokens and cost per turn
- routing overhead
- results across 3 seeds, a 50 to 800 agent scaling run, and a router threshold sweep (0.5 / 0.6 / 0.75)
- 95% Wilson intervals and a paired test (McNemar's exact test on the cases one version got right and the other didn't), in `evals/stats.py`

Every failure is listed, so it can be checked by hand.

**With a real model: `python -m evals.run_eval --router llm`.** This sends each arm's real prompt and tools through OpenRouter: the original system prompt and tools for the baseline, and the current ones for this branch. It applies each arm's reuse rule to the model's choice. Each case runs several times, because model output is not deterministic. It reports mean accuracy and the share of cases that were right on every repeat, with prompt tokens taken from the API's usage field. It also records input, output and thinking tokens over every call, the cost at OpenRouter's live list prices, and the seconds per decision. It costs money, so by default it runs 40 cases × 3 repeats. Results are written per model, for example [evals/results/llm_results_anthropic-claude-haiku-5-5.md](../evals/results/llm_results_anthropic-claude-haiku-5-5.md). See "With a real model" below.

## Results

From [evals/results/results.md](../evals/results/results.md). The simulated runs cover changes 1 to 13. Search phrases (change 14) need a model to write them, so they appear only in the real-model runs.

| Category | Cases | Original | This change | Right agent in shortlist |
|---|---|---|---|---|
| Full name + topic | 30 | 73.3% | 100% | 100% |
| Shared first name + topic | 30 | 50.0% | 100% | 100% |
| Company + topic | 20 | 25.0% | 100% | 100% |
| Email address | 20 | 20.0% | 100% | 100% |
| Reminder | 15 | 100% | 100% | 100% |
| Stale thread (archived) | 15 | 60.0% | 100% | 100% |
| **Paraphrased topic** | 15 | 6.7% | **20.0%** | **100%** |
| New person, same first name | 20 | 85.0% | 100% | n/a |
| Genuinely new task | 20 | 95.0% | 95.0% | n/a |

As the roster grows:

| Agents | Original accuracy | New accuracy | Original roster tokens | New roster tokens |
|---|---|---|---|---|
| 50 | 61.0% | 90.8% | 518 | 813 |
| 100 | 58.0% | 92.5% | 1,044 | 892 |
| 200 | 59.5% | 92.4% | 2,066 | 924 |
| 400 | 51.9% | 91.9% | 4,183 | 962 |
| 800 | 57.3% | 91.9% | 8,508 | 994 |

Multi-turn conversations (300 agents, seed 7):

| Kind | Conversations | Original: every turn right | This change | This change if the model never sets `new_task` |
|---|---|---|---|---|
| Switch and return | 8 | 25.0% | 100% | 100% |
| Correction | 6 | 16.7% | 100% | 100% |
| New then follow-up | 6 | 66.7% | 100% | 100% |
| Same person, new topic | 6 | 33.3% | 100% | 50.0% |
| **All** | 26 | 34.6% | 100% | 88.5% |

Over seeds 7, 11 and 23 the original gets 37.2% of conversations fully right, and this change gets 100%. At 800 agents with seed 23 it drops to 92.3%. Both failures happened the same way: the agent from earlier in the conversation had the same topic with a different company, and the question stayed with it. For example, Julia's conference talk at Soylent caught "what's the latest on the Oscorp conference talk?".

What I take from this:

- **The conversation eval found a bug in my resolver.** Before `new_task`, "start a new email to Emma Murphy (emma.murphy@vandelay.com) about the audit findings" went to Emma's existing lease renewal agent, because the email rule matched. The new draft would have gone out on the wrong thread. The right-hand column shows what happens if a real model never uses the flag: half of those conversations still fail. Only the real-model run can say how often a model sets it.
- **Early mistakes spread.** In the original, when turn 1 picks the wrong Michael, "tell them Thursday works" goes to the wrong Michael too. Accuracy on turns 2+ is 59.1% for the original and 100% here.
- **Most of the original's mistakes come from missing information, not a weak router.** "Email to Mila" and "Email to Mila Dimitrov" are the same person on two threads. Nothing in the names says which one is the travel itinerary.
- **Duplicates went to zero on this set,** mostly because of the resolver.
- **Paraphrases are the weak spot.** 11 of the 13 failures are paraphrases. Keyword retrieval can't connect "immigration forms" to "visa paperwork", and neither can the simulated router. Since change 12 the right agent is in the prompt for all 15 (60% before), at least by name and topic, so a real model can make the connection the simulated router can't. The real-model run below shows that it does in some cases.
- **For small rosters my version uses more tokens** (813 vs 518 at 50 agents), because summaries are longer than bare names. It is already smaller at 100 agents (892 vs 1,044), so the crossover is somewhere between 50 and 100. After that it grows slowly, from 892 to 994 between 100 and 800 agents, because a named person has more agents to list. The 12-entry cap stops it there.
- **The original barely gets worse with size in this simulation.** That's because the simulated router reads a list of 800 names as carefully as a list of 50. With a real model the overload does show, mostly as duplicates: see "As the roster grows, with a real model" below.

### With a real model

`python -m evals.run_eval --router llm --limit 40 --repeats 3` on Claude Haiku 5.5 through OpenRouter. The 40 cases are spread across the 9 categories, and each case ran 3 times per version. Full output: [llm_results_anthropic-claude-haiku-5-5.md](../evals/results/llm_results_anthropic-claude-haiku-5-5.md).

I ran it four times on the same 40 cases, changing my version between runs. The original's code never changed, so its spread (54% to 58%) is the model's own run-to-run variation.

| Run | This change, as of | Original | This change | Same cases: fixed / broke (McNemar) |
|---|---|---|---|---|
| 1 | changes 1 to 11 | 57.5% | 95.0% | 15 / 1 (p = 5e-4) |
| 2 | + named-people list (change 12) | 58.3% | 97.5% | 15 / 0 (p = 6e-5) |
| 3 | + search wording in the prompt (change 13) | 54.2% | 97.5% | 17 / 0 (p = 2e-5) |
| 4 | + search phrases (change 14), the current version | 58.3% | **99.2%** (40/40 cases) | 16 / 0 (p = 3e-5) |

Run 3 is in `llm_results_anthropic-claude-haiku-5-5.md` and run 4 in `..._phrases.md`. In run 3 this change used `search_agents` more (10.8% of runs, up from 5.8%), so its input tokens per decision rose to 7,809. In run 4 the phrases made search mostly unnecessary (1.7%), and input was 6,915 tokens per first call (9,854 for the original). The details of runs 1 and 2:

| | Original, run 1 | Original, run 2 | This change, run 1 | This change + named-people list, run 2 |
|---|---|---|---|---|
| Routed right (all runs) | 57.5% | 58.3% | 95.0% | **97.5%** |
| Cases right on all 3 runs (pass^3) | 52.5% | 52.5% | 95.0% | 97.5% |
| Duplicate created | 17.7% | 15.6% | 1.0% | 3.1% |
| False reuse | 12.5% | 8.3% | 0.0% | 0.0% |
| Used `search_agents` | n/a | n/a | 5.8% | 5.8% |
| Input tokens per decision | 9,854 | 9,854 | 6,943 | 7,134 |
| Output tokens per decision (thinking included) | 461 (178) | 476 (184) | 422 (143) | 399 (130) |
| Seconds per decision, p50 / p95 | 3.2 / 4.2 | 3.4 / 4.5 | 3.0 / 4.4 | 2.9 / 4.7 |
| Cost per 1,000 decisions on Haiku 5.5 | $1.22 | $1.22 | $0.91 | $0.91 |
| Same tokens priced on Sonnet 5.5 / Opus 5.5 (estimate, not run) | $24.32 / $48.64 | $24.47 / $48.94 | $18.10 / $36.21 | $18.26 / $36.52 |

**Is it real, with only 40 cases?** I treat the case as the unit, not the run, because three runs of the same message aren't independent. A case counts as right when at least 2 of its 3 runs were. Run 2 then gives the original 24/40 (95% Wilson interval 44.6% to 73.7%) and this change 39/40 (87.1% to 99.6%). On the same cases, this change fixed 15 and broke 0, and McNemar's exact test gives p = 6e-5. "pass^3" is the tau-bench name for "right on all 3 runs". The eval prints all of this, and `python -m evals.run_eval --router llm --rescore` recomputes it from the saved run without calling the model.

By category (runs right, 4 or 5 cases × 3 runs each):

| Category | Original, run 2 | This change, run 1 | This change + named-people list, run 2 |
|---|---|---|---|
| Full name + topic | 60% | 100% | 100% |
| Shared first name + topic | 40% | 100% | 100% |
| Company + topic | 20% | 100% | 100% |
| Email address | 20% | 100% | 100% |
| Reminder | 100% | 100% | 100% |
| Stale thread (archived) | 50% | 100% | 100% |
| **Paraphrased topic** | **75%** | **50%** | **75%** |
| New person, same first name | 83% | 100% | 100% |
| Genuinely new task | 100% | 100% | 100% |

What I take from this:

- **The overall result holds with a real model.** 58.3% vs 97.5% in run 2 (99.2% in run 4), close to the simulated 57.8% vs 93.0%. The original's mistakes are the same kinds: 12 of its 15 email-address runs created a duplicate agent, because nothing in a bare name says which address it owns.
- **In run 1 the original did better than mine on paraphrases (75% vs 50%).** This is the opposite of the simulation, which scored the original at 6.7% there. The gap was one case out of four: "Any news from Vera about the first week schedule for the new hire?" should go to "Onboarding Plan with Vera". The original shows every name, and the model links "first week schedule for the new hire" to "Onboarding Plan". My shortlist left that agent out. Three reminders took slots because they share the words "new" and "schedule", and four other Vera agents filled the rest. The model then reused "Email to Vera Garcia", wrong on all 3 runs. So the simulated router was too harsh on the original for paraphrases.
- **The named-people list fixed it.** When the message names a person, the prompt now also lists that person's other agents as "name | topic | status". The model picked "Onboarding Plan with Vera" on all 3 runs, and paraphrases tied the original at 75%. It costs about 70 tokens per turn (909 to 978 roster tokens, simulated) and is capped at 12 entries.
- **One paraphrase still fails in both versions.** "...what we're doing at the team retreat?" belongs to "Email to Michael Brown", whose thread is about the offsite agenda. With the fix, "Email to Michael Brown | offsite agenda | dormant" is in the prompt, but Haiku doesn't link "team retreat" to "offsite". That was the model's judgement, not missing information. In run 4, search phrases (change 14) fixed it: "Email to Michael Brown" got phrases like "company retreat agenda", so keyword search finds it.
- **Duplicates went up slightly, from 1.0% to 3.1%.** That's 3 of 96 runs, all the Michael case above, where the model created "Team Retreat Michael Update" instead of reusing an agent. In run 1 it mostly reused the wrong Michael instead. Same case, a different way of being wrong.
- **Accuracy is the real win. Cost is a modest bonus, and caching could erase it.** The roster section shrinks by about 70% (3,135 to 978 tokens, simulated count). But both versions also send the system prompt and tool definitions on every call, and that part doesn't shrink. So the whole input drops 28% (9,854 to 7,134 tokens) and the bill 25%, at list prices with no caching. With prompt caching, cached input costs a tenth of the normal price on Anthropic models. The original's roster changes only when an agent is created, so it caches well, and with a warm cache it could cost less per call than my shortlist. Two things favor the shortlist anyway. Personal-assistant messages often arrive more than 5 minutes apart, so the cache is often cold. And the original's list keeps growing. I haven't measured cache hit rates. My layout already caches well: tools and the system prompt never change, and the per-turn shortlist goes in the user message, not the system prompt.
- **Speed is the same.** About 3 seconds per decision for both. The extra `search_agents` call costs time in the few cases that use it, and a shorter prompt saves a little. A real turn makes several model calls, so routing isn't where OpenPoke's slowness comes from. OpenRouter also spreads requests across several providers (Google Vertex, AWS, Azure, Anthropic), whose speeds differ, so timings are noisy.
- **Haiku 5.5 thinks by default** on OpenRouter, about 130 to 180 tokens per decision. These are billed as output and are included in the costs above.
- **It's a small sample.** 40 cases, 4 or 5 per category. One case flipping moves a category by 20 to 25 points, so read the per-category numbers as direction, not precision. Each run cost about $0.26.

### As the roster grows, with a real model

The same 40-case comparison on Haiku 5.5 at 50, 300 and 800 agents (`--agents 50` / `--agents 800`, with change 13 and without phrases):

| Agents | Original: routed right | Original: duplicate created | Original: input tokens (first call) | This change: routed right | This change: input tokens |
|---|---|---|---|---|---|
| 50 | 57.5% | 25.0% | 5,253 | 95.0% | 6,604 |
| 300 | 54.2% | 17.7% | 9,854 | 97.5% | about 7,100 |
| 800 | 53.3% | **47.9%** | **19,471** | 93.3% | 6,914 |
| 800, current version (+ search phrases) | 54.2% | 44.8% | 19,471 | **100%** (40/40 cases) | 6,950 |

- **This is the overload, measured with a real model.** The original's accuracy only slips, but at 800 agents it creates a duplicate in almost half the cases where the right agent existed. With 800 look-alike names in front of it, the model gives up looking and starts a new agent. Its prompt is almost 4 times the size it was at 50 agents.
- **This change stays flat.** About 6,600 to 7,100 input tokens whatever the roster size, and 93% to 97.5% right. At 800 agents it dipped a little (38/40 cases, fixed 17 and broke 0 against the original).
- **With search phrases (change 14), 800 agents is no harder than 300.** The last row reran the current version (`--agents 800 --phrases`): every run right, no duplicates, and the model used `search_agents` in 4.2% of runs (14.2% without phrases). Against the original it fixed 19 cases and broke 0 (p = 4e-6). Input per decision, counting every call, was 7,287 tokens vs 19,471, and the cost per 1,000 decisions on Haiku 5.5 was $0.94 vs $2.19. Full output: [llm_results_anthropic-claude-haiku-5-5_800agents_phrases.md](../evals/results/llm_results_anthropic-claude-haiku-5-5_800agents_phrases.md).
- **At 50 agents the original's prompt is smaller.** The full list is still short then, and summaries cost more than bare names. The crossover is somewhere below 300 agents.

### A second model: Gemma 4

To check the result isn't specific to Claude, I ran the current version (with search phrases) on Google's Gemma 4 26B, an open mixture-of-experts model: `--model google/gemma-4-26b-a4b-it --phrases`. Cases right (2 of 3 runs):

| Gemma 4 26B | Original | This change | Same cases: fixed / broke (McNemar) |
|---|---|---|---|
| Main 40 cases | 19/40 (47.5%) | **29/40 (72.5%)** | 14 / 4 (p = 0.03) |
| 24 hand-written paraphrases | 5/24 (20.8%) | **19/24 (79.2%)** | 14 / 0 (p = 0.0001) |

- **The improvement holds on a second model family**, and it's significant on both sets.
- **Gemma is weaker at the task overall.** In about a quarter of runs it didn't route at all (no `send_message_to_agent` call), in both versions equally (24% vs 25% on the main set). That pulls both columns down. It's a tool-calling weakness of this model, not something either version causes.
- **Gemma is where the original's bare names hurt most.** On the hand-written paraphrases it created a duplicate in 48.6% of runs with the original, against 9.7% with this change.
- The search phrases here were still written by Haiku, so this checks the routing model, not the phrase writer.

### Hand-written paraphrases

The generated cases have a weakness: the same code wrote the agent summaries and the messages, so they share words, and keyword retrieval looks better than it would with real users. So I added 24 hand-written paraphrase messages, drafted with Claude's help (`evals/paraphrase_set.py`). Each targets an agent on a topic the generator never uses, added to the same 300-agent history. A test checks that no message shares a content word with its agent's name or summary, except the person's first name. 16 messages name the person ("Did Sarah get back to us on how much redoing the cooking area will cost?"). 8 name nobody ("When is the policy on the house up again?" for "Ashgrove Home Insurance Renewal"). The first names are common ones from the generator, so there are other Sarahs.

`python -m evals.run_eval --router llm --set paraphrase`, Haiku 5.5, 3 runs per case. Cases right (2 of 3 runs):

| | Original (3 runs) | Before change 13 | After change 13 | + search phrases (change 14) |
|---|---|---|---|---|
| Names the person (16) | 7 to 8/16 | **16/16** | **16/16** | **16/16** |
| Names nobody (8) | 6 to 7/8 | 1/8 | 5/8 | **8/8** |
| All 24 | 13 to 15/24 | 17/24 | 21/24 | **24/24** |
| Same cases vs the original: fixed / broke | | 9 / 6 (p = 0.61, could be chance) | 10 / 2 (p = 0.04) | 9 / 0 (p = 0.004) |
| Duplicate agent created (runs) | 37.5% to 44% | 27.8% | 11.1% | 4.2% |
| Cases right on all 3 runs (pass^3) | 54.2% (last run) | | | 83.3% |
| Right agent in the shortlist (simulated, recall@12) | 100% (shows all) | | | 87.5% (21/24) |

The majority score (24/24) is the headline, but the stricter numbers matter too. Four cases were right on 2 of 3 runs, not all 3. And for 3 of the 24 the right agent isn't in the shortlist at all, so the model only finds it by searching. Those three are synonym gaps that the phrases didn't cover: "kids get a place at the academy" for a school enrollment agent, a Japanese manual translation, and a laptop refresh. They're the cases the comparison branch's other retrievers are meant to test.

What this showed:

- **When a person is named, this change wins clearly.** The original keeps creating a new agent, because "Email to Sarah" doesn't say which Sarah or what about. The named-people list puts "Email to Sarah Whitfield | kitchen renovation quote" in front of the model, and it gets all 16.
- **When nobody is named, the original won, 7/8 vs 1/8.** It shows every name, so the model links "the policy on the house" to "Home Insurance Renewal" by meaning. My shortlist doesn't contain that agent, and keywords can't find it. The model searched in only 10 of 24 runs, it searched with the user's words, and then it created a duplicate. The generated set never showed this, because its paraphrase cases all named a person.
- **Change 13 closed most of the gap.** The prompt now says search matches words, not meaning, so search for the name the task was probably filed under, and try another wording before creating an agent. The model then searched in 23 of 24 runs, and topic-only cases went from 1/8 to 5/8. The three it still missed need matching by meaning ("everyone's old computers" for "Laptop Refresh").
- **Search phrases (change 14) closed the rest.** After an agent's first run, a cheap model writes about 10 other ways a user might refer to its task, and keyword search indexes them. "Ashgrove Home Insurance Renewal" gets phrases like "house policy renewal". The simulated run shows the effect on retrieval alone: for the "names nobody" messages, the right agent is now in the prompt 62.5% of the time, up from 0%. With the real model, all 8 are right on at least 2 of 3 runs, and all 24 overall. The main 40-case set didn't suffer either (run 4 above: 40/40). It's one cheap call per agent, cached. For all 324 agents with Haiku that was a few cents (the cache is `evals/results/agent_phrases.json`). It also fixed the "team retreat" case that had failed in both versions until now: "Email to Michael Brown" got phrases like "company retreat agenda".
- **A caveat on the phrases result.** The phrases are written by Haiku, the routing model is Haiku, and the 24 test messages were also written with a Claude model's help. Models from one family may share wording habits, which could flatter this result. The phrase writer never sees a test message, only each agent's own name and summary, so it can't copy the test. But a different phrase model or real user messages could score lower.
- **A mistake I caught.** My first version of that prompt used examples taken from this test set ("home insurance" for "the policy on the house"). It scored 93.1% of runs, which was teaching to the test. I replaced them with examples that appear nowhere in the data ("bathroom remodel" for "fixing up the shower"). It scored 88.9%, and that's the number above.
- **Still small, and not real users.** 24 messages from one author aren't real traffic, and some of them will be easier for my approach than real users would be.
