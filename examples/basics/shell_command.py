"""Run shell syntax only after selecting an available shell explicitly."""

from __future__ import annotations

import asyncio
import json

from sharkrail import CommandRunner, Shell, collect, shell_command


async def main() -> None:
    capabilities = collect()
    preferred = next(
        (
            shell
            for shell in (
                Shell.BASH,
                Shell.ZSH,
                Shell.PWSH,
                Shell.POWERSHELL,
                Shell.CMD,
            )
            if shell.value in capabilities.shells
        ),
        None,
    )
    if preferred is None:
        print(json.dumps({"skipped": "no supported shell is available"}))
        return
    if preferred in {Shell.PWSH, Shell.POWERSHELL}:
        script = "Write-Output shell-ok"
    elif preferred == Shell.CMD:
        script = "echo shell-ok"
    else:
        script = "printf 'shell-ok\\n'"
    result = await CommandRunner().run(
        shell_command(preferred, script), timeout_ms=5_000
    )
    print(json.dumps({"shell": preferred.value, "output": result.stdout.strip()}))


if __name__ == "__main__":
    asyncio.run(main())
