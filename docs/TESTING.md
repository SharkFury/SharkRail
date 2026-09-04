# Test and evidence map

SharkRail treats tests as evidence for a bounded claim, not proof that every
program behaves identically on every machine.

| Failure class | Evidence | Platforms |
| --- | --- | --- |
| Exit before inherited output handles close | drain-timeout and descendant fixtures in `test_sessions.py` | Linux, macOS, Windows |
| Cancellation leaves a child alive | escalation and process-tree tests in `test_backends.py` and `test_reliability.py` | Linux, macOS, Windows |
| Output grows without bound or is silently lost | budget, event limit, tail retention, and accounting properties | All; PTY integration per OS |
| Protocol crashes on malformed input | bounded-frame tests and Hypothesis JSON boundary properties | Platform-independent |
| PTY semantics diverge | isatty, resize, interactive input, ConPTY probe and ACP tail-output tests | Linux, macOS, Windows |
| Capability claim is stale | active doctor and capability degradation tests | Linux, macOS, Windows |
| A source checkout hides a packaging defect | sdist/wheel metadata and clean installed-wheel smoke jobs | Linux, macOS, Windows |
| Supply-chain workflow drifts | pinned-action, least-privilege, release, SBOM and provenance tests | GitHub Actions |

The CI matrix runs the complete suite on Python 3.9, 3.11, and 3.14 across
Ubuntu, macOS, and Windows. Coverage is a regression signal with a 70% floor;
it is not a substitute for platform and failure-path assertions. Property tests
use reproducible Hypothesis examples and save minimized local failures outside
version control.

Before citing a reliability result, record the exact commit, OS image, Python
version, backend, iteration count, configured limits, workload, failure
definition, and known exclusions. WSL cleanup remains best effort and is not
represented as equivalent to an in-distribution supervisor.

The current suite tests the Python reference implementation. A portable,
black-box conformance kit for independent implementations remains roadmap work;
until then, do not describe these tests as implementation-neutral certification.
