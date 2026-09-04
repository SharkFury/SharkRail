# Contributing to SharkRail

Thank you for helping make local execution more predictable for AI agents.
Contributions of code, tests, documentation, failure reports, and design
feedback are welcome.

Read the [public value design](docs/VALUE.md) before proposing a feature. A
minimal reproduction, failure fixture, integration report, or careful review is
as valuable as implementation code.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- Use a bug report for reproducible defects and a feature request for a concrete
  agent or automation use case.
- Open a design issue before changing the public Python API, CLI, protocol,
  lifecycle, security boundary, or platform semantics.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

Small fixes can go directly to a pull request. Maintainer agreement on an issue
does not guarantee a specific design or merge date.

## Development setup

Follow [BUILD.md](BUILD.md) to create a virtual environment and install the test
dependencies. Confirm the baseline passes before editing:

```bash
python -m ruff check src tests .github/scripts
python -m pytest
```

## Make a focused change

1. Create a branch from current `main`.
2. Keep one pull request focused on one behavior or coherent fix set.
3. Add tests for every new behavior and failure path.
4. Update the relevant public documentation and examples.
5. Run the local quality gates from [BUILD.md](BUILD.md).
6. Write a clear commit and pull request description.

English is the primary language for code, API names, commit messages, and
normative documentation. Translations are welcome and should link to the English
source of truth.

## Design rules

- Direct commands must never acquire implicit shell parsing.
- Process exit and output-drained session completion remain distinct.
- Unsupported platform behavior must be discoverable, never silently emulated.
- Output loss, degraded cleanup, and resource-limit failures must be observable.
- Public errors use stable codes and identify their execution stage.
- Logs, diagnostics, and default audit output must not expose command arguments,
  environment values, credentials, or command output.
- New dependencies require a clear need, compatible license, and maintenance
  assessment.
- A new feature must identify its beneficiary, reproducible failure, observable
  outcome, proof strategy, and the public-value measure it improves.
- Prefer a conformance fixture or contract clarification over adding another
  host-specific behavior to core.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/RELIABILITY.md](docs/RELIABILITY.md), and
[docs/VERSIONING.md](docs/VERSIONING.md) before changing core contracts.

## Testing expectations

- Unit-test validation, state transitions, serialization, and errors.
- Add integration coverage when behavior crosses process or transport boundaries.
- Add platform-specific tests for native APIs and capability degradation.
- Avoid timing-only assertions; use observable state and bounded deadlines.
- A bug fix should include a regression test that fails without the fix.
- Keep the repository-wide coverage gate at or above 70%.

Changes are expected to pass the complete CI matrix. A contributor need not own
every supported OS locally; document what was tested so CI and reviewers can
close the remaining platform gap.

## Commits and pull requests

Use concise, imperative commit subjects. Conventional prefixes such as `feat:`,
`fix:`, `docs:`, `test:`, and `chore:` are encouraged. Keep completed features
in separate commits when that improves review and release history.

A pull request should explain:

- the problem and user impact;
- the chosen behavior and alternatives considered;
- tests run and platforms exercised;
- compatibility, security, and observability impact; and
- documentation changed.

Do not include unrelated generated files or reformat unrelated code.

## Review and release

Maintainers review correctness, contract compatibility, test quality, platform
behavior, security boundaries, and maintainability. Review may request changes
even when tests pass. Merged changes ship according to the process in
[docs/RELEASING.md](docs/RELEASING.md).

Project decisions and maintainer responsibilities are described in
[GOVERNANCE.md](GOVERNANCE.md).
