import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from sharkrail.errors import ErrorCode, SharkRailError
from sharkrail.executor import CommandRunner
from sharkrail.models import CommandSpec, ResourceLimits
from sharkrail.policy import ExecutionPolicy, PolicyViolation
from sharkrail.sessions import SessionManager


def test_policy_allows_named_executable_and_bounded_request(tmp_path: Path):
    policy = ExecutionPolicy(
        allowed_executables=frozenset({Path(sys.executable).name}),
        allowed_cwd_roots=(tmp_path,),
        allowed_env_keys=frozenset({"SAFE"}),
        max_timeout_ms=1000,
        max_output_bytes=1024,
        max_memory_bytes=4096,
        max_cpu_time_seconds=2,
        max_process_count=3,
    )
    policy.enforce(
        CommandSpec(
            sys.executable,
            ("-V",),
            cwd=str(tmp_path),
            env={"SAFE": "1"},
            resources=ResourceLimits(
                memory_bytes=4096,
                cpu_time_seconds=2,
                process_count=3,
            ),
        ),
        timeout_ms=1000,
        max_output_bytes=1024,
    )


@pytest.mark.parametrize(
    ("policy", "spec", "timeout_ms", "output_bytes", "rule"),
    [
        (ExecutionPolicy(denied_executables=frozenset({"python"})), CommandSpec("python", ()), 1, 1, "denied_executables"),
        (ExecutionPolicy(allowed_executables=frozenset({"git"})), CommandSpec("python", ()), 1, 1, "allowed_executables"),
        (ExecutionPolicy(require_absolute_executable=True), CommandSpec("python", ()), 1, 1, "require_absolute_executable"),
        (ExecutionPolicy(require_timeout=True), CommandSpec("python", ()), None, 1, "require_timeout"),
        (ExecutionPolicy(max_timeout_ms=10), CommandSpec("python", ()), 11, 1, "max_timeout_ms"),
        (ExecutionPolicy(max_output_bytes=10), CommandSpec("python", ()), 1, 11, "max_output_bytes"),
        (ExecutionPolicy(allow_parent_environment=False), CommandSpec("python", ()), 1, 1, "allow_parent_environment"),
        (ExecutionPolicy(allowed_env_keys=frozenset({"SAFE"})), CommandSpec("python", (), env={"SECRET": "x"}), 1, 1, "allowed_env_keys"),
    ],
)
def test_policy_denials_are_named(policy, spec, timeout_ms, output_bytes, rule):
    with pytest.raises(PolicyViolation) as raised:
        policy.enforce(spec, timeout_ms=timeout_ms, max_output_bytes=output_bytes)
    assert raised.value.rule == rule


def test_policy_rejects_working_directory_escape(tmp_path: Path):
    policy = ExecutionPolicy(allowed_cwd_roots=(tmp_path / "allowed",))
    with pytest.raises(PolicyViolation, match="allowed_cwd_roots"):
        policy.enforce(
            CommandSpec("python", (), cwd=str(tmp_path / "outside")),
            timeout_ms=1,
            max_output_bytes=1,
        )


def test_policy_loads_strict_json(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "allowed_executables": ["python"],
                "allow_parent_environment": False,
                "max_timeout_ms": 1000,
            }
        ),
        encoding="utf-8",
    )
    policy = ExecutionPolicy.from_json(path)
    assert policy.allowed_executables == frozenset({"python"})
    assert policy.allow_parent_environment is False

    with pytest.raises(ValueError, match="unknown execution policy fields"):
        ExecutionPolicy.from_dict({"surprise": True})


def test_session_manager_returns_structured_policy_error():
    async def _run() -> None:
        manager = SessionManager(
            policy=ExecutionPolicy(
                denied_executables=frozenset({Path(sys.executable).name})
            )
        )
        with pytest.raises(SharkRailError) as raised:
            await manager.start(CommandSpec(sys.executable, ("-V",)))
        assert raised.value.error.code == ErrorCode.POLICY_DENIED
        assert raised.value.error.native["rule"] == "denied_executables"
        assert manager.session_count == 0

    asyncio.run(_run())


def test_clean_environment_does_not_inherit_parent_values():
    async def _run() -> None:
        variable = "SHARKRAIL_POLICY_PARENT_PROBE"
        os.environ[variable] = "sensitive"
        manager = SessionManager()
        try:
            session = await manager.start(
                CommandSpec(
                    sys.executable,
                    ("-c", f"import os; print(os.getenv('{variable}', 'missing'))"),
                    inherit_env=False,
                )
            )
            result = await manager.wait(session.id)
            assert result is not None and result.stdout.strip() == "missing"
        finally:
            os.environ.pop(variable, None)
            await manager.shutdown()

    asyncio.run(_run())


def test_dry_run_still_enforces_policy():
    async def _run() -> None:
        runner = CommandRunner(
            dry_run=True,
            policy=ExecutionPolicy(denied_executables=frozenset({"blocked"})),
        )
        result = await runner.run(CommandSpec("blocked", ()))
        assert result.error is not None
        assert result.error.code == ErrorCode.POLICY_DENIED

    asyncio.run(_run())


def test_repository_policy_example_is_valid():
    example = Path(__file__).resolve().parents[1] / "examples" / "policy.json"
    policy = ExecutionPolicy.from_json(example)
    assert policy.require_timeout is True
    assert policy.allow_parent_environment is False
