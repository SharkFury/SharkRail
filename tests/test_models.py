from sharkrail.models import CommandMode, CommandSpec


def test_command_spec_validation_ok():
    spec = CommandSpec(executable="echo", argv=("hello",), mode=CommandMode.PIPE)
    spec.validate()
    assert spec.argv_list == ["echo", "hello"]


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

