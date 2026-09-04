# Support

SharkRail is an early-stage, community-maintained project. Support is provided
on a best-effort basis.

## Choose the right channel

- **Usage question:** open a GitHub Discussion if discussions are enabled;
  otherwise open an issue with the `question` label.
- **Reproducible defect:** use the bug report form.
- **New behavior:** use the feature request form and describe the agent use case.
- **Security vulnerability:** report it privately through [SECURITY.md](SECURITY.md).
- **Conduct incident:** follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Search existing issues and read [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
before filing a new report.

## Information to include

Run:

```bash
sharkrail doctor --bundle sharkrail-diagnostics.json
```

Review the file before attaching it, then include:

- SharkRail version or commit;
- OS and Python version;
- execution mode (`pipe` or `pty`) and target (`native` or `wsl`);
- smallest command or JSON-RPC exchange that reproduces the issue;
- expected behavior and complete actual result;
- whether the failure is consistent; and
- relevant structured errors or redacted logs.

Never publish credentials, environment dumps, proprietary source, private
paths, or unreviewed output/audit files.

## Support scope

Maintainers can help with documented SharkRail behavior on the supported matrix.
They cannot debug private applications without a reproduction, guarantee a
response time, provide emergency operational support, or make executed programs
safe. Platform-specific behavior outside SharkRail's ownership boundary may
need to be reproduced independently.

For production deployments, start with the [operations guide](docs/OPERATIONS.md)
and include the applicable item from the [test and evidence map](docs/TESTING.md)
when reporting a reliability regression.
