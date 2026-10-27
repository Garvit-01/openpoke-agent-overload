"""Coordinate execution agents and batch their results for the interaction agent."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional

from .runtime import ExecutionAgentRuntime, ExecutionResult
from ...logging_config import logger
from ...services.execution.ids import agent_id_for
from ...services.execution.phrases import maybe_add_phrases
from ...services.execution.routing import refresh_agent_profile

# Keep references so background tasks aren't garbage-collected before they finish
_BACKGROUND_TASKS: set = set()


def _start_background(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


@dataclass
class PendingExecution:
    """Track a pending execution request."""

    request_id: str
    agent_name: str
    instructions: str
    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class _BatchState:
    """Collect results for a single interaction-agent turn."""

    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)
    pending: int = 0
    results: List[ExecutionResult] = field(default_factory=list)


class AgentRunLocks:
    """One run at a time per agent.

    Without this, two quick follow-ups to the same agent (or a trigger firing while the
    user is talking to that agent) start two runs that read the same history and can
    both act, e.g. two drafts for one reply. The second run now waits and then sees the
    first run's result in its history. Different agents still run in parallel.
    Locks are dropped once nobody holds or waits on them, so the map stays small.
    """

    def __init__(self) -> None:
        self._locks: Dict[str, asyncio.Lock] = {}
        self._users: Dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, agent_name: str) -> AsyncIterator[bool]:
        """Hold the agent's lock; yields whether this run had to wait for another one."""

        key = agent_id_for(agent_name)
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._users[key] = self._users.get(key, 0) + 1
        waited = lock.locked()
        try:
            async with lock:
                yield waited
        finally:
            self._users[key] -= 1
            if not self._users[key]:
                del self._users[key]
                del self._locks[key]

    def is_running(self, agent_name: str) -> bool:
        lock = self._locks.get(agent_id_for(agent_name))
        return bool(lock and lock.locked())


# Shared by every batch manager: chat turns and the trigger scheduler each create their own
_AGENT_RUN_LOCKS = AgentRunLocks()


def get_agent_run_locks() -> AgentRunLocks:
    return _AGENT_RUN_LOCKS


class ExecutionBatchManager:
    """Run execution agents and deliver their combined outcome."""

    # Initialize batch manager with timeout and coordination state for execution agents
    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: Dict[str, PendingExecution] = {}
        self._batch_lock = asyncio.Lock()
        self._batch_state: Optional[_BatchState] = None

    # Run execution agent with timeout handling and batch coordination for interaction agent
    async def execute_agent(
        self,
        agent_name: str,
        instructions: str,
        request_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute an agent asynchronously and buffer the result for batch dispatch."""

        if not request_id:
            request_id = str(uuid.uuid4())

        batch_id = await self._register_pending_execution(agent_name, instructions, request_id)

        async with get_agent_run_locks().hold(agent_name) as waited:
            if waited:
                logger.info(f"[{agent_name}] Waiting for the agent's previous run to finish")
            result = await self._run_agent(agent_name, instructions, request_id)
            # Keep the agent's summary and entities current so it can be found next time
            refresh_agent_profile(agent_name)

        # Once per agent, in the background: other words a user might use for its task
        _start_background(maybe_add_phrases(agent_name))
        await self._complete_execution(batch_id, result, agent_name)
        return result

    # Run one agent with the timeout (the timeout starts after any wait for the agent's lock)
    async def _run_agent(self, agent_name: str, instructions: str, request_id: str) -> ExecutionResult:
        try:
            logger.info(f"[{agent_name}] Execution started")
            runtime = ExecutionAgentRuntime(agent_name=agent_name)
            result = await asyncio.wait_for(
                runtime.execute(instructions),
                timeout=self.timeout_seconds,
            )
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"[{agent_name}] Execution finished: {status}")
        except asyncio.TimeoutError:
            logger.error(f"[{agent_name}] Execution timed out after {self.timeout_seconds}s")
            result = ExecutionResult(
                agent_name=agent_name,
                success=False,
                response=f"Execution timed out after {self.timeout_seconds} seconds",
                error="Timeout",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(f"[{agent_name}] Execution failed unexpectedly")
            result = ExecutionResult(
                agent_name=agent_name,
                success=False,
                response=f"Execution failed: {exc}",
                error=str(exc),
            )
        finally:
            self._pending.pop(request_id, None)
        return result

    # Add execution request to current batch or create new batch if none exists
    async def _register_pending_execution(
        self,
        agent_name: str,
        instructions: str,
        request_id: str,
    ) -> str:
        """Attach a new execution to the active batch, opening one when required."""

        async with self._batch_lock:
            if self._batch_state is None:
                batch_id = str(uuid.uuid4())
                self._batch_state = _BatchState(batch_id=batch_id)
            else:
                batch_id = self._batch_state.batch_id

            self._batch_state.pending += 1
            self._pending[request_id] = PendingExecution(
                request_id=request_id,
                agent_name=agent_name,
                instructions=instructions,
                batch_id=batch_id,
            )

            return batch_id

    # Store execution result and send combined batch to interaction agent when complete
    async def _complete_execution(
        self,
        batch_id: str,
        result: ExecutionResult,
        agent_name: str,
    ) -> None:
        """Record the execution result and dispatch when the batch drains."""

        dispatch_payload: Optional[str] = None

        async with self._batch_lock:
            state = self._batch_state
            if state is None or state.batch_id != batch_id:
                logger.warning(f"[{agent_name}] Dropping result for unknown batch")
                return

            state.results.append(result)
            state.pending -= 1

            if state.pending == 0:
                dispatch_payload = self._format_batch_payload(state.results)
                agent_names = [entry.agent_name for entry in state.results]
                logger.info(f"Execution batch completed: {', '.join(agent_names)}")
                self._batch_state = None

        if dispatch_payload:
            await self._dispatch_to_interaction_agent(dispatch_payload)

    # Return list of currently pending execution requests for monitoring purposes
    def get_pending_executions(self) -> List[Dict[str, str]]:
        """Expose pending executions for observability."""

        return [
            {
                "request_id": pending.request_id,
                "agent_name": pending.agent_name,
                "batch_id": pending.batch_id,
                "created_at": pending.created_at.isoformat(),
                "elapsed_seconds": (datetime.now() - pending.created_at).total_seconds(),
            }
            for pending in self._pending.values()
        ]

    # Clean up all pending executions and batch state on shutdown
    async def shutdown(self) -> None:
        """Clear pending bookkeeping (no background work remains)."""

        self._pending.clear()
        async with self._batch_lock:
            self._batch_state = None

    # Format multiple execution results into single message for interaction agent
    def _format_batch_payload(self, results: List[ExecutionResult]) -> str:
        """Render execution results into the interaction-agent format."""

        entries: List[str] = []
        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            response_text = (result.response or "(no response provided)").strip()
            entries.append(f"[{status}] {result.agent_name}: {response_text}")
        return "\n".join(entries)

    # Forward combined execution results to interaction agent for user response generation
    async def _dispatch_to_interaction_agent(self, payload: str) -> None:
        """Send the aggregated execution summary to the interaction agent."""

        from ..interaction_agent.runtime import InteractionAgentRuntime

        runtime = InteractionAgentRuntime()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(runtime.handle_agent_message(payload))
            return

        loop.create_task(runtime.handle_agent_message(payload))
