# Public Value Design

Status: project charter for roadmap and contribution decisions

SharkRail exists to make local process execution predictable for autonomous
software. It is maintained as open, vendor-neutral infrastructure, not as a
funnel for a hosted or paid product.

The protocol and reliability documents define runtime behavior. This document
defines why the project exists, whose problems it prioritizes, how public value
is demonstrated, and which work belongs outside the project.

## Mission

> Give every local agent execution an explicit intent, a verifiable outcome,
> and an honest boundary.

Operating-system APIs already start processes. SharkRail is valuable only when
it makes supervision materially more reliable: output drain, terminal
semantics, cancellation, descendant cleanup, bounded resources, stable errors,
and discoverable platform differences.

The project does not promise that commands succeed. It promises that callers
can determine what happened, what was retained or lost, what cleanup was
attempted, and which guarantees the current runtime actually supports.

## The public value thesis

For a short, non-interactive command on one platform, use the standard library.
Adding SharkRail would create more concepts and another failure layer without
enough benefit. The project earns its place when execution is a durable product
capability and one or more of these conditions apply:

- Windows, WSL, Linux, or macOS must share one caller-facing contract;
- both structured pipe output and real PTY/ConPTY behavior are required;
- jobs are long-running, concurrent, interactive, or produce large output;
- timeout and cancellation must clean up an owned process tree;
- the caller must distinguish process exit from fully drained completion;
- limits, truncation, degraded cleanup, and unsupported capabilities must be
  machine-observable; or
- multiple agent hosts are duplicating the same supervision code.

SharkRail turns repeated private glue into four shared public assets:

| Public asset | Purpose | Current state |
| --- | --- | --- |
| Open execution contract | Stable lifecycle, event, error, limit, and capability semantics | Protocol, schema, and reliability contract exist |
| Native reference runtime | Demonstrate the contract with real platform mechanisms | Python alpha exists for Windows, WSL, Linux, and macOS |
| Cross-implementation conformance kit | Let clients and alternative runtimes prove compatible behavior | Tests exist, but a portable kit is still a roadmap priority |
| Shared failure corpus | Turn hangs, leaked descendants, output loss, encoding faults, and platform surprises into reusable regressions | Cases exist in tests, but need a named, documented corpus |

The project succeeds even when another implementation passes the public
conformance suite and a client can replace the reference runtime. Adoption of a
contract is public value; lock-in to one implementation is not.

## Beneficiaries and jobs

| Priority | Beneficiary | Job to be done | Evidence of value |
| --- | --- | --- | --- |
| Primary adopter | Agent, IDE, and automation-runtime maintainers | Replace duplicated subprocess, PTY, buffering, deadline, and cleanup code with one explicit session contract | Less integration glue; failures become classified; the same tests pass across supported platforms |
| Primary rights holder | The person who lets an agent operate their machine | Bound, observe, cancel, and diagnose execution without hidden loss or lingering owned processes | Limits and cancellation are visible; diagnostics avoid secrets; platform caveats are explicit |
| Ecosystem implementer | Client, adapter, SDK, or platform-backend authors | Reuse or implement the contract without copying supervision logic or depending on one agent vendor | Adapters remain thin; independent components pass conformance fixtures |
| Community contributor | Systems programmers, CI authors, and failure reporters | Convert one real execution failure into a minimal reproduction and permanent regression asset | Reproducible reports become documented fixtures and tests |

The machine-facing consumer is an agent, but the human machine owner remains
the most important rights holder. Convenience for an agent never outranks user
control, privacy, or truthful reporting.

## Value hierarchy

When goals conflict, decisions follow this order:

1. User control, privacy, and honest security boundaries.
2. Lifecycle correctness, bounded behavior, and output integrity.
3. Truthful platform capability and degradation reporting.
4. Protocol interoperability and compatibility.
5. Integration simplicity and diagnostic quality.
6. Feature and adapter breadth.

This ordering deliberately makes a smaller, dependable runtime more valuable
than a broad runtime whose completion and cleanup behavior cannot be proven.

## What SharkRail replaces—and what it does not

| Alternative | Use it when | SharkRail's role |
| --- | --- | --- |
| `subprocess`, `asyncio`, or a platform process API | Commands are short, non-interactive, and controlled by one application on one platform | Do not replace it unless supervision failures justify the extra runtime |
| PTY libraries such as pywinpty or portable-pty | The main requirement is opening and driving a terminal | Build on or complement them with lifecycle, limits, events, and protocol semantics |
| Terminal applications and multiplexers | A human needs to see and operate a terminal | Complement them; SharkRail has no terminal UI |
| Containers, VMs, AppContainer, or Windows Sandbox | Untrusted code requires isolation | Compose with them; SharkRail is not a security sandbox |
| CI and workflow engines | Work requires orchestration, durable scheduling, retries, or distributed workers | Remain below them as a local execution primitive |
| A host-specific exec server | One product and platform need highly custom behavior | Migrate only when shared contracts and cross-platform maintenance provide clear value |

## Permanent scope guards

SharkRail does not become:

- a general agent framework, planner, tool marketplace, or workflow engine;
- a hosted execution cloud, remote shell, or multi-tenant compute service;
- a malware sandbox, credential vault, or privilege-elevation system;
- a terminal emulator UI or full human terminal product;
- a prompt-, color-, or regex-based process completion detector;
- a lowest-common-denominator API that hides real operating-system differences;
- a collection of vendor-specific behavior inside the execution core; or
- a reason to replace a standard-library subprocess call that already works.

Agent protocols belong in adapters. Vendor-specific behavior belongs outside
the core. Features that cannot state a bounded completion condition, observable
failure, platform behavior, and maintenance owner do not enter the runtime.

## Open-source stewardship commitment

The official SharkRail project is stewarded as public infrastructure:

- the runtime, protocol, schemas, conformance assets, documentation, and normal
  development process remain public;
- the roadmap is optimized for shared reliability and interoperability, not
  revenue, cloud conversion, or paid-customer privilege;
- there is no proprietary official edition, paywalled core capability,
  mandatory account, or required hosted control plane;
- default operation remains local-first, with no required remote telemetry;
- important technical and governance decisions are made in public, except for
  the temporary confidentiality required to handle security reports; and
- sponsorship or grants, if ever accepted, are disclosed and cannot purchase a
  protocol outcome, release decision, maintainer role, or private roadmap.

SharkRail uses the MIT License. That license permits individuals and companies
to use, modify, package, and support the software commercially. This stewardship
commitment governs the direction of the official project; it is not an
additional license restriction and does not reduce user freedom.

## Evidence, not claims

The north-star measure is:

> Verified real-world execution failure classes eliminated across supported
> platform and backend combinations.

The project keeps a public value scorecard around four groups of evidence.
Counts such as stars, downloads, lines of code, adapters, and features are
distribution signals, not proof of value.

### 1. Contract correctness

- Known regression fixtures that pass on each applicable platform/backend.
- Unbounded waits caused by the runtime: target zero.
- Silent output loss and unclassified terminal outcomes: target zero.
- Owned descendants left after successful cleanup: target zero within the
  documented platform boundary.
- Capability claims that disagree with probed behavior: target zero.
- Every bounded loss, deadline, and degraded cleanup path has a structured result.

### 2. Interoperability and adoption quality

- Independent agent, IDE, and automation integrations used outside the repo.
- Time and host-specific glue required to complete a reference integration.
- Independent clients or runtimes passing the conformance suite.
- Existing clients that continue working through compatible upgrades.
- Core fixes demonstrably benefiting more than one host or adapter.

### 3. Community sustainability

- External failure reports converted into minimal fixtures and regressions.
- Reviewers and maintainers with independent organizational and platform experience.
- Major decisions with public rationale, compatibility analysis, and migration guidance.
- Documentation, schema, examples, and implementation discrepancies remaining open.
- Critical platforms with an active reviewer who understands their native semantics.

### 4. User sovereignty and trust

- Required network calls and remote telemetry in the default runtime: zero.
- Sensitive-data leaks from default logs and diagnostic bundles: target zero.
- Output loss, resource intervention, and capability degradation reported explicitly.
- Public breaking changes accompanied by migration guidance and compatibility tests.

Published measurements must include the test environment, iteration count,
failure definition, known blind spots, and raw reproduction instructions. A
green badge without a stated boundary is not sufficient evidence.

## Work admission test

Every feature proposal must answer:

1. Who encounters which reproducible execution failure, and how often?
2. Why are standard process APIs, a PTY library, or composition insufficient?
3. Is this a shared core contract, a platform backend, an adapter, or an example?
4. What machine-observable result, error, resource bound, and completion rule
   will callers receive?
5. Which fixture, fault injection, or conformance test proves the value?
6. What are the native semantics and honest degradation on each platform?
7. What changes for privacy, privilege, supply chain, and compatibility?
8. Who will maintain the behavior and its platform tests?
9. Which public-value measure should improve?
10. How can the feature be deprecated or moved out of core if evidence fails?

A proposal ends in one of five places: core contract, platform backend, adapter,
example/recipe, or rejection/postponement. It does not enter core merely because
it is possible to implement.

Immediate rejection criteria include implicit shell parsing, silent
degradation, unbounded resources or waiting, sandbox-like claims, binding the
core to one agent vendor, or adding behavior without a compatibility and test
strategy.

## Community value loop

```text
real failure report
        -> minimal reproduction
        -> named cross-platform fixture
        -> contract clarification
        -> reference implementation fix
        -> conformance regression
        -> documentation and adopter feedback
```

Code is only one contribution in this loop. Failure reports, Windows/WSL
reproductions, test fixtures, documentation, translations, reviews, and
integration reports are first-class contributions.

## Maturity path

Maturity is earned through evidence, not a calendar or feature count.

### Credible

- One canonical, internally consistent set of shipped-state documentation.
- A named failure corpus covering lifecycle, output, cancellation, PTY, and
  platform-specific behavior.
- A published cross-platform scorecard with bounded stress and fault tests.
- Every advertised guarantee linked to a test or explicitly marked as a limit.

### Useful

- Reference integrations demonstrate that hosts can adopt the contract without
  duplicating supervision logic.
- Independent users provide reproducible cases and published integration reports.
- Upgrades have clear compatibility tests and migration paths.

### Infrastructure

- The conformance kit runs independently of the Python reference implementation.
- More than one independently maintained client or runtime passes it.
- Governance, review, and release authority no longer depend on one person or
  one organization.

The roadmap should move in this order: prove the contract, make it easy to
adopt, make it independently implementable, and only then broaden features.
