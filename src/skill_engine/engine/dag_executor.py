from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections.abc import Callable

from skill_engine.models.skill import SkillDefinition, StepDefinition, NodeStatus
from skill_engine.models.registry import ToolRegistry
from skill_engine.engine.resolver import resolve_input
from skill_engine.engine.criteria import evaluate_success, evaluate_failure
from skill_engine.engine.validator import validate_input


class DAGExecutor:
    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def execute(
        self,
        skill: SkillDefinition,
        input_data: dict,
        sync: bool = True,
        *,
        tracer=None,
    ) -> dict:
        validation_errors = validate_input(skill.input_schema, input_data)
        if validation_errors:
            return {
                "run_id": "",
                "status": "failed",
                "output": None,
                "error": f"Input validation failed: {'; '.join(validation_errors)}",
            }

        try:
            execution_order = skill.topological_order()
        except ValueError as e:
            return {
                "run_id": "",
                "status": "failed",
                "output": None,
                "error": f"DAG validation failed: {e}",
            }

        if not sync:
            task = asyncio.create_task(
                self._execute_dag(skill, input_data, execution_order, tracer)
            )
            run_id = _new_run_id()
            self._running_tasks[run_id] = task
            return {"run_id": run_id, "status": "running", "output": None, "error": None}

        return await self._execute_dag(skill, input_data, execution_order, tracer)

    async def _execute_dag(
        self,
        skill: SkillDefinition,
        input_data: dict,
        execution_order: list[StepDefinition],
        tracer=None,
    ) -> dict:
        trace = None
        if tracer:
            trace = await tracer.start_trace(skill, input_data)

        step_outputs: dict[str, object] = {}
        step_statuses: dict[str, NodeStatus] = {s.id: NodeStatus.PENDING for s in skill.steps}
        step_map: dict[str, StepDefinition] = {s.id: s for s in skill.steps}

        levels = _group_by_level(skill.steps, execution_order)
        semaphore = asyncio.Semaphore(skill.max_concurrency)
        overall_error: str | None = None

        for level in levels:
            tasks = []
            for step in level:
                if step_statuses[step.id] == NodeStatus.SKIPPED:
                    continue
                tasks.append(
                    self._execute_step(
                        step, input_data, step_outputs, step_statuses,
                        semaphore, trace, tracer
                    )
                )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            idx = 0
            for step in level:
                if step_statuses.get(step.id) == NodeStatus.SKIPPED:
                    continue
                result = results[idx]
                idx += 1

                if isinstance(result, Exception):
                    step_statuses[step.id] = NodeStatus.FAILED
                    overall_error = f"Step '{step.id}' unexpected error: {result}"
                    _skip_downstream(step.id, skill.steps, step_statuses)
                elif isinstance(result, dict) and result.get("failed"):
                    step_statuses[step.id] = NodeStatus.FAILED
                    overall_error = result.get("error")
                    _skip_downstream(step.id, skill.steps, step_statuses)

            if overall_error:
                break

        all_succeeded = all(s == NodeStatus.SUCCEEDED for s in step_statuses.values())
        any_failed = any(s == NodeStatus.FAILED for s in step_statuses.values())

        terminal_ids = skill.terminal_steps()
        output = {sid: step_outputs.get(sid) for sid in terminal_ids}

        if all_succeeded:
            status = "succeeded"
            error = None
        elif any_failed:
            status = "failed"
            error = overall_error
        else:
            status = "partial"
            error = overall_error

        if trace:
            trace.status = status
            trace.output = output
            trace.error = error
            await tracer.finish_trace(trace)

        return {"run_id": trace.run_id if trace else _new_run_id(), "status": status, "output": output, "error": error}

    async def _execute_step(
        self,
        step: StepDefinition,
        skill_input: dict,
        step_outputs: dict[str, object],
        step_statuses: dict[str, NodeStatus],
        semaphore: asyncio.Semaphore,
        trace=None,
        tracer=None,
    ) -> None | dict:
        async with semaphore:
            step_statuses[step.id] = NodeStatus.RUNNING

            step_trace = None
            if tracer and trace:
                step_trace = tracer.start_step_trace(trace, step)

            resolved_input = resolve_input(step.input_mapping, skill_input, step_outputs)

            if step_trace:
                step_trace.input = resolved_input

            tool_fn = self.tool_registry.get(step.tool)

            last_error = None
            last_output = None
            for attempt in range(step.retry.max_attempts):
                is_exception = False
                try:
                    if tool_fn is not None:
                        last_output = await asyncio.wait_for(
                            _call_tool(tool_fn, resolved_input),
                            timeout=step.timeout_seconds,
                        )
                    else:
                        last_output = await asyncio.wait_for(
                            _run_command(step.tool, resolved_input),
                            timeout=step.timeout_seconds,
                        )

                    if evaluate_success(step.success_criteria, last_output):
                        step_outputs[step.id] = last_output
                        step_statuses[step.id] = NodeStatus.SUCCEEDED
                        if step_trace:
                            step_trace.status = "succeeded"
                            step_trace.output = last_output
                            if tracer:
                                await tracer.finish_step_trace(step_trace)
                        return None

                    last_error = f"Success criteria not met for step '{step.id}'"

                except asyncio.TimeoutError:
                    last_error = f"Step '{step.id}' timed out after {step.timeout_seconds}s"
                    is_exception = True
                except Exception as e:
                    last_error = str(e)
                    is_exception = True

                if step_trace:
                    step_trace.retry_count = attempt + 1

                if is_exception and step.failure_criteria and evaluate_failure(step.failure_criteria, last_error):
                    break

                if attempt < step.retry.max_attempts - 1:
                    delay = _backoff_delay(step.retry, attempt)
                    await asyncio.sleep(delay)

            step_statuses[step.id] = NodeStatus.FAILED
            if step_trace:
                step_trace.status = "failed"
                step_trace.error = last_error
                step_trace.output = last_output
                if tracer:
                    await tracer.finish_step_trace(step_trace)
            return {"failed": True, "error": last_error}


async def _call_tool(fn: Callable, input_data: dict) -> object:
    if asyncio.iscoroutinefunction(fn):
        return await fn(**input_data)
    return fn(**input_data)


async def _run_command(command: str, input_vars: dict) -> dict:
    """Execute a shell command, substituting {varname} placeholders with input values."""
    # Resolve {varname} placeholders
    cmd = command
    for key, val in input_vars.items():
        cmd = cmd.replace(f"{{{key}}}", shlex.quote(str(val)))

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ},
    )
    stdout, stderr = await proc.communicate()

    result = {
        "stdout": stdout.decode("utf-8", errors="replace").strip(),
        "stderr": stderr.decode("utf-8", errors="replace").strip(),
        "returncode": proc.returncode,
        "command": cmd,
    }

    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {proc.returncode}): {result['stderr'][:500]}"
        )

    return result


def _new_run_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _group_by_level(
    steps: list[StepDefinition], order: list[StepDefinition]
) -> list[list[StepDefinition]]:
    levels: list[list[StepDefinition]] = []
    remaining = set(s.id for s in steps)
    step_map = {s.id: s for s in steps}

    while remaining:
        level = []
        for sid in list(remaining):
            step = step_map[sid]
            if all(dep not in remaining for dep in step.depends_on):
                level.append(step)
        for s in level:
            remaining.discard(s.id)
        levels.append(level)

    return levels


def _skip_downstream(
    failed_step_id: str,
    steps: list[StepDefinition],
    statuses: dict[str, NodeStatus],
) -> None:
    for step in steps:
        if failed_step_id in step.depends_on:
            if statuses[step.id] not in (NodeStatus.FAILED, NodeStatus.SUCCEEDED):
                statuses[step.id] = NodeStatus.SKIPPED
                _skip_downstream(step.id, steps, statuses)


def _backoff_delay(retry, attempt: int) -> float:
    if retry.backoff == "exponential":
        return retry.backoff_base_seconds * (2 ** attempt)
    elif retry.backoff == "fixed":
        return retry.backoff_base_seconds
    return 0.0
