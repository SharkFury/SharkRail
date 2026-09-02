# Security Policy

If you find a security issue, do not create a public issue.

Please include:

- Affected version
- Reproduction steps
- Impact scope
- Suggested mitigation (if any)

The maintainer team will investigate and publish a fix timeline in the public issue when resolved.

Structured runtime logs exclude command arguments and environment values. JSONL
event audit files redact output by default, but `--event-log-include-output`
records command output verbatim and must only be used with an appropriately
protected destination.
