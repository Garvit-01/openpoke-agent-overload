"""One run at a time per agent, across every batch manager (chat turns and triggers)."""

import asyncio

import pytest

from server.agents.execution_agent import batch_manager as batch_module
from server.agents.execution_agent.batch_manager import ExecutionBatchManager, get_agent_run_locks
from server.agents.execution_agent.runtime import ExecutionResult


@pytest.fixture
def fake_runtime(monkeypatch):
    """A runtime that records overlap and what history each run saw when it started."""

    state = {"running": {}, "peak": {}, "peak_total": 0, "history": {}, "seen": [], "delay": 0.05}

    class FakeRuntime:
        def __init__(self, agent_name):
            self.agent_name = agent_name

        async def execute(self, instructions):
            name = self.agent_name
            # Like the real runtime, the history is read when the run starts
            state["seen"].append((instructions, list(state["history"].get(name, []))))
            state["running"][name] = state["running"].get(name, 0) + 1
            state["peak"][name] = max(state["peak"].get(name, 0), state["running"][name])
            state["peak_total"] = max(state["peak_total"], sum(state["running"].values()))
            await asyncio.sleep(state["delay"])
            state["running"][name] -= 1
            state["history"].setdefault(name, []).append(f"done: {instructions}")
            return ExecutionResult(agent_name=name, success=True, response=f"done: {instructions}")

    async def no_dispatch(self, payload):
        return None

    monkeypatch.setattr(batch_module, "ExecutionAgentRuntime", FakeRuntime)
    monkeypatch.setattr(batch_module, "refresh_agent_profile", lambda name: None)
    monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", no_dispatch)
    return state


def test_two_requests_to_one_agent_run_one_after_the_other(fake_runtime):
    async def main():
        manager = ExecutionBatchManager()
        return await asyncio.gather(
            manager.execute_agent("Email to Sarah", "draft a reply"),
            manager.execute_agent("Email to Sarah", "send it"),
        )

    results = asyncio.run(main())

    assert all(result.success for result in results)
    assert fake_runtime["peak"]["Email to Sarah"] == 1
    # The second run started after the first finished, so it saw the first run's result
    (first, first_saw), (second, second_saw) = fake_runtime["seen"]
    assert first_saw == []
    assert second_saw == [f"done: {first}"]


def test_different_agents_still_run_in_parallel(fake_runtime):
    async def main():
        manager = ExecutionBatchManager()
        await asyncio.gather(
            manager.execute_agent("Email to Sarah", "a"),
            manager.execute_agent("Vercel Job Offer", "b"),
        )

    asyncio.run(main())
    assert fake_runtime["peak_total"] == 2


def test_lock_is_shared_between_chat_and_trigger_managers(fake_runtime):
    # The trigger scheduler creates its own ExecutionBatchManager for every trigger
    async def main():
        await asyncio.gather(
            ExecutionBatchManager().execute_agent("Reminder: pay rent", "reminder fired"),
            ExecutionBatchManager().execute_agent("Reminder: pay rent", "move it to Monday"),
        )

    asyncio.run(main())
    assert fake_runtime["peak"]["Reminder: pay rent"] == 1


def test_waiting_does_not_count_against_the_timeout(fake_runtime):
    fake_runtime["delay"] = 0.15

    async def main():
        manager = ExecutionBatchManager(timeout_seconds=0.25)
        return await asyncio.gather(
            manager.execute_agent("Email to Sarah", "one"),
            manager.execute_agent("Email to Sarah", "two"),
        )

    # Together they take 0.3s, more than the timeout, but each run alone fits
    assert all(result.success for result in asyncio.run(main()))


def test_locks_are_released_and_dropped(fake_runtime):
    async def main():
        manager = ExecutionBatchManager()
        task = asyncio.create_task(manager.execute_agent("Email to Sarah", "a"))
        await asyncio.sleep(0.01)
        running = get_agent_run_locks().is_running("Email to Sarah")
        await task
        return running

    assert asyncio.run(main()) is True
    assert not get_agent_run_locks().is_running("Email to Sarah")
    assert get_agent_run_locks()._locks == {}
