from sharkrail.models import CommandMode, ResourceLimits
from sharkrail.routing import Shell, Target, WslOptions, direct_command, shell_command


def test_direct_command_preserves_argv_without_shell_parsing():
    spec = direct_command("tool", ("a b", "; rm -rf nope"))
    assert spec.argv_list == ["tool", "a b", "; rm -rf nope"]


def test_powershell_command_uses_noninteractive_profile_free_invocation():
    spec = shell_command(Shell.PWSH, "Write-Output hello")
    assert spec.executable == "pwsh"
    assert spec.argv[:4] == ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")


def test_wsl_direct_command_is_structured():
    spec = direct_command(
        "python3",
        ("-c", "print('hello world')"),
        target=Target.WSL,
        wsl=WslOptions(distribution="Ubuntu", user="agent", cwd="/work"),
    )
    assert spec.executable == "wsl.exe"
    assert spec.argv == (
        "--distribution",
        "Ubuntu",
        "--user",
        "agent",
        "--cd",
        "/work",
        "--exec",
        "python3",
        "-c",
        "print('hello world')",
    )


def test_wsl_shell_rejects_windows_shell():
    try:
        shell_command(Shell.CMD, "echo hi", target=Target.WSL)
        assert False
    except ValueError as err:
        assert "bash and zsh" in str(err)


def test_shell_command_keeps_requested_mode():
    spec = shell_command(Shell.BASH, "printf hello", mode=CommandMode.PTY)
    assert spec.mode == CommandMode.PTY


def test_direct_command_preserves_resource_policy():
    limits = ResourceLimits(memory_bytes=1024 * 1024, process_count=2)
    spec = direct_command("python", ("-V",), resources=limits)

    assert spec.resources == limits
