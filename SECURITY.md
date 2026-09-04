# Security policy

SharkRail executes local programs with the permissions of its caller. It is not
a sandbox and does not make untrusted code safe. Use an appropriate container,
virtual machine, AppContainer, Windows Sandbox, or equivalent isolation boundary
for untrusted workloads.

## Supported versions

| Version | Security fixes |
| --- | --- |
| Latest release on the default branch | Supported |
| Older releases | Best effort |

Until SharkRail reaches 1.0, security fixes may require compatibility changes.
Such changes will be called out in release notes.

## Report a vulnerability

Do not open a public issue or pull request for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/SharkFury/SharkRail/security/advisories/new).

Include, when available:

- affected version, commit, OS, and execution mode;
- minimal reproduction and required privileges;
- expected and actual security boundary;
- impact on confidentiality, integrity, or availability;
- whether the issue is already public or actively exploited; and
- a suggested mitigation.

Do not include real credentials, private command output, or third-party data.
The maintainers will acknowledge the report, assess scope and severity, and
coordinate disclosure and a fix. Response timing depends on impact and
maintainer availability; reporters will receive status updates through the
private advisory.

## Security boundary

SharkRail:

- does not elevate privileges;
- does not isolate executed programs from the caller or filesystem;
- does not validate that a requested executable is trustworthy;
- does not open a network listener in the v0.1 stdio service;
- treats WSL descendant cleanup as best effort; and
- cannot prevent privileged children from escaping OS process-group controls.

Direct argv execution avoids unintended shell parsing. Explicit shell execution
still interprets caller-provided script text and must be treated accordingly.

## Sensitive observability data

Structured runtime logs exclude command arguments and environment values.
Diagnostic bundles exclude environment values, command arguments, and session
output. JSONL event audits redact output by default.

`--event-log-include-output` records command output verbatim. Protect that file
with appropriate filesystem permissions, retention, and access controls, and
review it before sharing. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
