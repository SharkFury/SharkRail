from sharkrail.models import CommandMode, CommandSpec, ResourceLimits


def test_command_spec_validation_ok():
    spec = CommandSpec(executable="echo", argv=("hello",), mode=CommandMode.PIPE)
    spec.validate()
    assert spec.argv_list == ["echo", "hello"]


def test_pty_command_can_start_executable_without_arguments():
    spec = CommandSpec(executable="python", argv=(), mode=CommandMode.PTY)

    spec.validate()
    assert spec.argv_list == ["python"]


def test_command_spec_validation_empty_executable():
    try:
        CommandSpec(executable="", argv=()).validate()
        assert False
    except ValueError as err:
        assert "executable" in str(err)


def test_command_spec_reject_empty_arg():
    try:
        CommandSpec(executable="echo", argv=("",)).validate()
        assert False
    except ValueError:
        pass


def test_command_spec_rejects_invalid_environment_name():
    try:
        CommandSpec(executable="echo", argv=(), env={"BAD=NAME": "value"}).validate()
        assert False
    except ValueError as err:
        assert "environment" in str(err)


def test_resource_limits_must_be_positive():
    spec = CommandSpec(
        executable="echo",
        argv=("hello",),
        resources=ResourceLimits(memory_bytes=0),
    )

    try:
        spec.validate()
        assert False
    except ValueError as err:
        assert "memory_bytes" in str(err)
