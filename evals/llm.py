"""Opt-in evaluation with a real model through OpenRouter.

Each arm gets the prompt it really sends:
- baseline: the original system prompt, original tools, every agent name
- solution: the current system prompt and tools (incl. search_agents), the shortlist

The model's send_message_to_agent call is then run through that arm's reuse rule
(exact match vs the resolver). Each case runs --repeats times because model output
is not deterministic; we report the mean accuracy and the share of cases that were
right on every repeat. Prompt token counts come from the API's usage field.

    python -m evals.run_eval --router llm --limit 40 --repeats 3

Costs money: roughly limit x repeats x 2 arms model calls (more when search_agents is used).
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from server.agents.interaction_agent.agent import SYSTEM_PROMPT
from server.agents.interaction_agent.tools import get_tool_schemas

from .baseline import ORIGINAL_SYSTEM_PROMPT, original_tool_schemas
from .harness import BaselineArm, SolutionArm
from .scenarios import NEW, Case, Scenario, build_scenario

NO_ROUTE = "__NO_ROUTE__"
NEW_TASK_MARK = " [new_task]"
MAX_STEPS = 4
Complete = Callable[..., Awaitable[Dict[str, Any]]]


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
) -> Tuple[Optional[str], str, Optional[int], bool]:
    """Returns (agent_name, instructions, first-call prompt tokens, used_search).

    The instructions come back with " [new_task]" appended when the model set new_task,
    so the solution arm can pass the flag on without changing this signature.
    """

    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_content}]
    prompt_tokens: Optional[int] = None
    used_search = False
    for _ in range(MAX_STEPS):
        response = await complete(model=model, messages=messages, system=system, api_key=api_key, tools=tools)
        if prompt_tokens is None:
            prompt_tokens = (response.get("usage") or {}).get("prompt_tokens")
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
            if arm_label == "baseline":
                name, _instructions, tokens, used_search = await ask_model(
                    complete=complete, model=model, api_key=api_key, system=ORIGINAL_SYSTEM_PROMPT,
                    tools=baseline_tools, user_content=_user_content(baseline.roster, case.message),
                )
                chosen = baseline.apply(case, name)[0] if name else NO_ROUTE
            else:
                name, instructions, tokens, used_search = await ask_model(
                    complete=complete, model=model, api_key=api_key, system=SYSTEM_PROMPT,
                    tools=solution_tools, user_content=_user_content(solution.roster_section(case.message), case.message),
                    search=lambda query: _search_payload(solution, query),
                )
                new_task = instructions.endswith(NEW_TASK_MARK)
                instructions = instructions[: -len(NEW_TASK_MARK)] if new_task else instructions
                chosen = solution.apply(case, name, instructions, new_task=new_task)[0] if name else NO_ROUTE
        return LLMOutcome(case.id, case.category, arm_label, repeat, case.gold, chosen, name, tokens, used_search)

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
        summary[arm] = {
            "runs": len(rows),
            "accuracy": statistics.fmean(o.correct for o in rows),
            "all_repeats_correct": statistics.fmean(all(v) for v in per_case.values()),
            "duplicate_rate": statistics.fmean(o.chosen == NEW for o in existing) if existing else 0.0,
            "false_reuse_rate": statistics.fmean(o.chosen not in (NEW, NO_ROUTE) for o in new) if new else 0.0,
            "no_route_rate": statistics.fmean(o.chosen == NO_ROUTE for o in rows),
            "search_rate": statistics.fmean(o.used_search for o in rows),
            "prompt_tokens_mean": statistics.fmean(tokens) if tokens else None,
        }
    return summary


def run_llm(args) -> None:
    from server.config import get_settings
    from server.openrouter_client import request_chat_completion

    from .run_eval import RESULTS_DIR

    api_key = get_settings().openrouter_api_key
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set (put it in .env). The offline eval needs no key: python -m evals.run_eval")

    scenario = build_scenario(args.agents, seed=args.seed)
    cases = sample_cases(scenario, args.limit, args.seed)
    print(f"Running {len(cases)} cases x {args.repeats} repeats x 2 arms on {args.model} ...")
    with tempfile.TemporaryDirectory() as workdir:
        outcomes = asyncio.run(
            evaluate_llm(
                scenario, cases, complete=request_chat_completion, model=args.model, api_key=api_key,
                repeats=args.repeats, concurrency=args.concurrency, workdir=Path(workdir),
            )
        )
    summary = summarise(outcomes)

    def pct(value):
        return "n/a" if value is None else f"{value * 100:.1f}%"

    lines = [
        f"# LLM routing evaluation ({args.model})",
        "",
        f"{len(cases)} cases (spread across categories) x {args.repeats} repeats, {args.agents} agents, seed {args.seed}.",
        "",
        "| Metric | Original OpenPoke | This change |",
        "|---|---|---|",
    ]
    for label, key in [("Accuracy (all runs)", "accuracy"), ("Cases right on every repeat", "all_repeats_correct"),
                       ("Duplicate created", "duplicate_rate"), ("False reuse", "false_reuse_rate"),
                       ("Model did not route at all", "no_route_rate"), ("Used search_agents", "search_rate")]:
        lines.append(f"| {label} | {pct(summary['baseline'][key])} | {pct(summary['solution'][key])} |")
    tokens = [summary[arm]["prompt_tokens_mean"] for arm in ("baseline", "solution")]
    lines.append(
        "| Prompt tokens, first call (from API usage) | "
        + " | ".join("n/a" if value is None else f"{value:,.0f}" for value in tokens)
        + " |"
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "llm_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS_DIR / "llm_results.json").write_text(
        json.dumps({"summary": summary, "outcomes": [o.__dict__ for o in outcomes]}, indent=2), encoding="utf-8"
    )
    print("\n".join(lines))


__all__ = ["LLMOutcome", "ask_model", "evaluate_llm", "run_llm", "sample_cases", "summarise"]
