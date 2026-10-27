"""Opt-in evaluation with a real model through OpenRouter.

Each arm gets the prompt it really sends:
- baseline: the original system prompt, original tools, every agent name
- solution: the current system prompt and tools (incl. search_agents), the shortlist

The model's send_message_to_agent call is then run through that arm's reuse rule
(exact match vs the resolver). Each case runs --repeats times because model output
is not deterministic; we report the mean accuracy and the share of cases that were
right on every repeat. Token counts come from the API's usage field, summed over every
model call a decision needed. Cost uses OpenRouter's live list prices (no prompt caching),
and the time per decision is the wall time of those calls.

    python -m evals.run_eval --router llm --limit 40 --repeats 3 --model anthropic/claude-haiku-5.5
    python -m evals.run_eval --router llm --limit 1 --repeats 1   # check the key and model first

Costs money: roughly limit x repeats x 2 arms model calls (more when search_agents is used).
"""

from __future__ import annotations

import asyncio
import functools
import json
import random
import re
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from server.agents.interaction_agent.agent import SYSTEM_PROMPT
from server.agents.interaction_agent.tools import get_tool_schemas

from .baseline import ORIGINAL_SYSTEM_PROMPT, original_tool_schemas
from .harness import BaselineArm, SolutionArm
from .scenarios import NEW, Case, Scenario, build_scenario
from .stats import majority_right, paired_lines

NO_ROUTE = "__NO_ROUTE__"
NEW_TASK_MARK = " [new_task]"
MAX_STEPS = 4
MAX_OUTPUT_TOKENS = 4096
Complete = Callable[..., Awaitable[Dict[str, Any]]]
PRICES_URL = "https://openrouter.ai/api/v1/models"
# Models priced in the cost comparison, using the token counts measured in this run
COMPARE_MODELS = ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5"]
Prices = Dict[str, Tuple[float, float]]  # model id -> (USD per input token, USD per output token)


@dataclass
class LLMOutcome:
    case_id: str
    category: str
    arm: str
    repeat: int
    gold: str
    chosen: str
    requested_name: Optional[str]
    prompt_tokens: Optional[int]
    used_search: bool
    # Summed over every model call this decision took (a search adds a call)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0  # part of output_tokens
    calls: int = 0
    seconds: float = 0.0

    @property
    def correct(self) -> bool:
        return self.chosen == self.gold


def _user_content(roster_section: str, message: str) -> str:
    return (
        "<conversation_history>\nNone\n</conversation_history>\n\n"
        f"{roster_section}\n\n<new_user_message>\n{message}\n</new_user_message>"
    )


def _search_payload(arm: SolutionArm, query: str) -> str:
    matches = arm.search(query)
    return json.dumps(
        {
            "tool": "search_agents",
            "status": "success",
            "result": {
                "total_agents": arm.total,
                "matches": [
                    {
                        "agent_name": item.name,
                        "status": item.record.status,
                        "last_active": item.record.last_active_at[:10],
                        "matched": "; ".join(item.reasons),
                        "summary": item.record.summary[:300],
                    }
                    for item in matches
                ],
            },
        }
    )


# Drive the model until it calls send_message_to_agent (answering search_agents along the way)
async def ask_model(
    *,
    complete: Complete,
    model: str,
    api_key: Optional[str],
    system: str,
    tools: List[Dict[str, Any]],
    user_content: str,
    search: Optional[Callable[[str], str]] = None,
    usage: Optional[Dict[str, int]] = None,
) -> Tuple[Optional[str], str, Optional[int], bool]:
    """Returns (agent_name, instructions, first-call prompt tokens, used_search).

    The instructions come back with " [new_task]" appended when the model set new_task,
    so the solution arm can pass the flag on without changing this signature.
    Pass a dict as `usage` to get input/output tokens and the call count summed over all calls.
    """

    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_content}]
    prompt_tokens: Optional[int] = None
    used_search = False
    totals = usage if usage is not None else {}
    for key in ("input", "output", "reasoning", "calls"):
        totals.setdefault(key, 0)
    for _ in range(MAX_STEPS):
        response = await complete(model=model, messages=messages, system=system, api_key=api_key, tools=tools)
        reported = response.get("usage") or {}
        if prompt_tokens is None:
            prompt_tokens = reported.get("prompt_tokens")
        totals["input"] += reported.get("prompt_tokens") or 0
        totals["output"] += reported.get("completion_tokens") or 0
        # Thinking tokens, if the model used any, are already inside completion_tokens (and billed as output)
        totals["reasoning"] += (reported.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        totals["calls"] += 1
        message = ((response.get("choices") or [{}])[0]).get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            return None, "", prompt_tokens, used_search
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if name == "send_message_to_agent":
                instructions = arguments.get("instructions", "")
                if arguments.get("new_task"):
                    instructions += NEW_TASK_MARK
                return arguments.get("agent_name"), instructions, prompt_tokens, used_search
            if name == "search_agents" and search is not None:
                used_search = True
                content = search(str(arguments.get("query", "")))
            else:
                content = json.dumps({"tool": name, "status": "success", "result": {"status": "ok"}})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": content})
    return None, "", prompt_tokens, used_search


def sample_cases(scenario: Scenario, limit: int, seed: int) -> List[Case]:
    """Spread the limited budget evenly across categories."""
    rng = random.Random(seed)
    by_category: Dict[str, List[Case]] = {}
    for case in scenario.cases:
        by_category.setdefault(case.category, []).append(case)
    for cases in by_category.values():
        rng.shuffle(cases)
    picked: List[Case] = []
    while len(picked) < limit and any(by_category.values()):
        for name in sorted(by_category):
            if by_category[name] and len(picked) < limit:
                picked.append(by_category[name].pop())
    return picked


async def evaluate_llm(
    scenario: Scenario,
    cases: Sequence[Case],
    *,
    complete: Complete,
    model: str,
    api_key: Optional[str],
    repeats: int = 3,
    concurrency: int = 4,
    workdir: Path,
) -> List[LLMOutcome]:
    baseline = BaselineArm(scenario)
    solution = SolutionArm(scenario, workdir)
    solution_tools = get_tool_schemas()
    baseline_tools = original_tool_schemas(solution_tools)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: Case, arm_label: str, repeat: int) -> LLMOutcome:
        async with semaphore:
            usage: Dict[str, int] = {}
            started = time.perf_counter()
            if arm_label == "baseline":
                name, _instructions, tokens, used_search = await ask_model(
                    complete=complete, model=model, api_key=api_key, system=ORIGINAL_SYSTEM_PROMPT,
                    tools=baseline_tools, user_content=_user_content(baseline.roster, case.message), usage=usage,
                )
                seconds = time.perf_counter() - started
                chosen = baseline.apply(case, name)[0] if name else NO_ROUTE
            else:
                name, instructions, tokens, used_search = await ask_model(
                    complete=complete, model=model, api_key=api_key, system=SYSTEM_PROMPT,
                    tools=solution_tools, user_content=_user_content(solution.roster_section(case.message), case.message),
                    search=lambda query: _search_payload(solution, query), usage=usage,
                )
                seconds = time.perf_counter() - started
                new_task = instructions.endswith(NEW_TASK_MARK)
                instructions = instructions[: -len(NEW_TASK_MARK)] if new_task else instructions
                chosen = solution.apply(case, name, instructions, new_task=new_task)[0] if name else NO_ROUTE
        return LLMOutcome(
            case.id, case.category, arm_label, repeat, case.gold, chosen, name, tokens, used_search,
            input_tokens=usage["input"], output_tokens=usage["output"], reasoning_tokens=usage["reasoning"],
            calls=usage["calls"], seconds=seconds,
        )

    jobs = [one(case, arm, repeat) for case in cases for arm in ("baseline", "solution") for repeat in range(repeats)]
    return list(await asyncio.gather(*jobs))


def summarise(outcomes: Sequence[LLMOutcome]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for arm in ("baseline", "solution"):
        rows = [o for o in outcomes if o.arm == arm]
        if not rows:
            continue
        per_case: Dict[str, List[bool]] = {}
        for row in rows:
            per_case.setdefault(row.case_id, []).append(row.correct)
        existing = [o for o in rows if o.gold != NEW]
        new = [o for o in rows if o.gold == NEW]
        tokens = [o.prompt_tokens for o in rows if o.prompt_tokens]
        seconds = sorted(o.seconds for o in rows)
        summary[arm] = {
            "runs": len(rows),
            "accuracy": statistics.fmean(o.correct for o in rows),
            "all_repeats_correct": statistics.fmean(all(v) for v in per_case.values()),
            "duplicate_rate": statistics.fmean(o.chosen == NEW for o in existing) if existing else 0.0,
            "false_reuse_rate": statistics.fmean(o.chosen not in (NEW, NO_ROUTE) for o in new) if new else 0.0,
            "no_route_rate": statistics.fmean(o.chosen == NO_ROUTE for o in rows),
            "search_rate": statistics.fmean(o.used_search for o in rows),
            "prompt_tokens_mean": statistics.fmean(tokens) if tokens else None,
            "input_tokens_per_decision": statistics.fmean(o.input_tokens for o in rows),
            "output_tokens_per_decision": statistics.fmean(o.output_tokens for o in rows),
            "reasoning_tokens_per_decision": statistics.fmean(o.reasoning_tokens for o in rows),
            "calls_per_decision": statistics.fmean(o.calls for o in rows),
            "seconds_p50": seconds[len(seconds) // 2],
            "seconds_p95": seconds[max(0, int(len(seconds) * 0.95) - 1)],
        }
    return summary


def fetch_prices() -> Prices:
    """List prices from OpenRouter's public model list. Empty on failure, so the run still writes its results."""
    import httpx

    try:
        response = httpx.get(PRICES_URL, timeout=20.0)
        response.raise_for_status()
        prices: Prices = {}
        for item in response.json().get("data", []):
            pricing = item.get("pricing") or {}
            prices[item["id"]] = (float(pricing.get("prompt") or 0), float(pricing.get("completion") or 0))
        return prices
    except Exception as exc:  # cost is a nice-to-have; accuracy is the point of the run
        print(f"Could not fetch prices ({exc}); skipping the cost tables.")
        return {}


def cost_per_1k(arm_summary: Dict[str, Any], price: Tuple[float, float]) -> float:
    """USD per 1,000 routing decisions at a model's list price, from the measured tokens per decision."""
    per_input, per_output = price
    per_decision = arm_summary["input_tokens_per_decision"] * per_input + arm_summary["output_tokens_per_decision"] * per_output
    return per_decision * 1000


def cost_lines(summary: Dict[str, Dict[str, Any]], prices: Prices, model: str) -> List[str]:
    """The run's own cost, then the same token counts priced on other models."""
    lines: List[str] = []
    arms = ("baseline", "solution")
    if model in prices:
        lines += [
            "", f"## Cost on {model}", "",
            "| | Original OpenPoke | This change |", "|---|---|---|",
            "| Input tokens per routing decision | "
            + " | ".join(f"{summary[a]['input_tokens_per_decision']:,.0f}" for a in arms) + " |",
            "| Output tokens per routing decision | "
            + " | ".join(f"{summary[a]['output_tokens_per_decision']:,.0f}" for a in arms) + " |",
            "| of which thinking tokens | "
            + " | ".join(f"{summary[a].get('reasoning_tokens_per_decision', 0):,.0f}" for a in arms) + " |",
            "| Model calls per routing decision | "
            + " | ".join(f"{summary[a]['calls_per_decision']:.2f}" for a in arms) + " |",
            "| Cost per 1,000 routing decisions | "
            + " | ".join(f"${cost_per_1k(summary[a], prices[model]):.2f}" for a in arms) + " |",
            "| Cost of this run | "
            + " | ".join(f"${cost_per_1k(summary[a], prices[model]) * summary[a]['runs'] / 1000:.2f}" for a in arms) + " |",
        ]
    compare = [m for m in COMPARE_MODELS if m in prices]
    if compare:
        lines += [
            "", "## Same traffic on other models", "",
            f"The token counts measured on {model}, priced at each model's list price. Another model could make a "
            "different number of calls (for example use search_agents more), so this is an estimate.", "",
            "| Model | Input / output $ per million | Original, per 1,000 decisions | This change, per 1,000 decisions | Saving |",
            "|---|---|---|---|---|",
        ]
        for name in compare:
            base, new = (cost_per_1k(summary[a], prices[name]) for a in arms)
            saving = f"{(1 - new / base) * 100:.0f}%" if base else "n/a"
            per_input, per_output = prices[name]
            lines.append(
                f"| {name} | ${per_input * 1e6:.2f} / ${per_output * 1e6:.2f} | ${base:.2f} | ${new:.2f} | {saving} |"
            )
    return lines


def run_llm(args) -> None:
    from server.config import get_settings
    from server.openrouter_client import request_chat_completion

    from .run_eval import RESULTS_DIR

    # One file per model, so runs on different models don't overwrite each other
    stem = "llm_results_" + re.sub(r"[^a-z0-9]+", "-", args.model.lower()).strip("-")
    if getattr(args, "set", "generated") == "paraphrase":
        stem += "_paraphrase"
    saved = RESULTS_DIR / f"{stem}.json"
    if args.rescore:
        # Re-report a saved run (new metrics, fixed formatting) without paying for the model calls again
        if not saved.exists():
            sys.exit(f"No saved run at {saved}")
        outcomes = [LLMOutcome(**row) for row in json.loads(saved.read_text(encoding="utf-8"))["outcomes"]]
        case_count = len({o.case_id for o in outcomes})
        args.repeats = max(o.repeat for o in outcomes) + 1
        print(f"Rescoring {saved} ({case_count} cases x {args.repeats} repeats), no model calls")
    else:
        api_key = get_settings().openrouter_api_key
        if not api_key:
            sys.exit("OPENROUTER_API_KEY is not set (put it in .env). The offline eval needs no key: python -m evals.run_eval")
        if getattr(args, "set", "generated") == "paraphrase":
            from .paraphrase_set import build_paraphrase_scenario

            scenario = build_paraphrase_scenario(args.agents, seed=args.seed)
        else:
            scenario = build_scenario(args.agents, seed=args.seed)
        cases = sample_cases(scenario, args.limit, args.seed)
        case_count = len(cases)
        print(f"Running {case_count} cases x {args.repeats} repeats x 2 arms on {args.model} ...")
        with tempfile.TemporaryDirectory() as workdir:
            # A routing decision is a short tool call; the cap stops OpenRouter reserving 65k output tokens per request
            complete = functools.partial(request_chat_completion, max_tokens=MAX_OUTPUT_TOKENS)
            outcomes = asyncio.run(
                evaluate_llm(
                    scenario, cases, complete=complete, model=args.model, api_key=api_key,
                    repeats=args.repeats, concurrency=args.concurrency, workdir=Path(workdir),
                )
            )
    summary = summarise(outcomes)

    def pct(value):
        return "n/a" if value is None else f"{value * 100:.1f}%"

    lines = [
        f"# LLM routing evaluation ({args.model})",
        "",
        f"{case_count} cases (spread across categories) x {args.repeats} repeats, {args.agents} agents, seed {args.seed}.",
        "",
        "| Metric | Original OpenPoke | This change |",
        "|---|---|---|",
    ]
    for label, key in [("Accuracy (all runs, pass^1)", "accuracy"),
                       (f"Cases right on all {args.repeats} repeats (pass^{args.repeats})", "all_repeats_correct"),
                       ("Duplicate created", "duplicate_rate"), ("False reuse", "false_reuse_rate"),
                       ("Model did not route at all", "no_route_rate"), ("Used search_agents", "search_rate")]:
        lines.append(f"| {label} | {pct(summary['baseline'][key])} | {pct(summary['solution'][key])} |")
    tokens = [summary[arm]["prompt_tokens_mean"] for arm in ("baseline", "solution")]
    lines.append(
        "| Prompt tokens, first call (from API usage) | "
        + " | ".join("n/a" if value is None else f"{value:,.0f}" for value in tokens)
        + " |"
    )
    lines.append(
        "| Seconds per routing decision, p50 / p95 | "
        + " | ".join(f"{summary[arm]['seconds_p50']:.1f} / {summary[arm]['seconds_p95']:.1f}" for arm in ("baseline", "solution"))
        + " |"
    )
    lines += ["", "Seconds cover every model call for the decision and depend on the provider's load that day, "
              "so compare the two columns with each other, not with other runs."]
    # The case is the unit, not the run: repeats of one message aren't independent
    by_arm = {
        arm: majority_right((o.case_id, o.correct) for o in outcomes if o.arm == arm) for arm in ("baseline", "solution")
    }
    lines += [
        "", "## Is the difference real?", "",
        f"A case counts as right when more than half of its {args.repeats} runs were right.", "",
        *paired_lines(by_arm["baseline"], by_arm["solution"]),
    ]
    lines += cost_lines(summary, fetch_prices(), args.model)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    saved.write_text(
        json.dumps({"model": args.model, "summary": summary, "outcomes": [o.__dict__ for o in outcomes]}, indent=2),
        encoding="utf-8",
    )
    print("\n".join(lines))
    print(f"\nWrote {RESULTS_DIR / stem}.md")


__all__ = [
    "LLMOutcome", "ask_model", "cost_lines", "cost_per_1k", "evaluate_llm", "fetch_prices", "run_llm",
    "sample_cases", "summarise",
]
