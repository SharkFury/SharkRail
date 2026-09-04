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

The project does not promise that commands succeed. Within capabilities backed
by conformance evidence, it aims to guarantee that callers can determine what
happened, what was retained or lost, what cleanup was attempted, and which
boundaries the current runtime cannot cross.

## The public value thesis

In the simplest terms:

> SharkRail gives every process session started by an agent workflow bounded,
> observable, and verifiable supervision.

This is process supervision within one runtime lifecycle, not workflow or LLM
context management. SharkRail retains bounded session events and incremental
output while that runtime is alive. The agent remains responsible for workflow
orchestration, retries, cross-session memory, checkpoints, and restart recovery.

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

## Native process APIs vs. SharkRail

SharkRail is built on native process, pipe, signal, PTY/ConPTY, process-group,
and Job Object APIs. The difference is not whether a command can start; it is
who owns the supervision work after it starts.

| Concern | Direct use of native/standard APIs | SharkRail value |
| --- | --- | --- |
| Intended use | One short command controlled by one caller | Long-running command steps, bounded concurrent sessions, interactive tools, or cross-platform work |
| Lifecycle | Start a process and obtain a handle/exit status | Track acceptance, start, exit, drain, completion, failure, and disposal explicitly |
| Long tasks | Caller implements polling, deadlines, and state retention | In-runtime session, non-destructive wait, wall/idle deadlines, and cursor-based incremental reads |
| Output | Caller drains pipes and invents buffering/backpressure policy | Separate pipe streams or merged terminal stream with bounds, offsets, raw bytes, and loss accounting |
| Cancellation | Signal/terminate a process; descendant behavior is caller-specific | Serialize cancellation and report escalation through interrupt, terminate, and owned-tree kill |
| Concurrent sessions | Caller coordinates starts, input races, quotas, and shutdown | Bound active sessions and input/output while serializing mutations per session |
| Interactive tools | Caller selects and integrates a platform PTY | Expose pipe and PTY/ConPTY through the same session operations, including input and resize |
| Cross-platform behavior | Caller translates quoting, shells, signals, and ownership mechanisms | Preserve structured intent and expose verified, degraded, or best-effort capability evidence |
| Failure result | Caller defines and normalizes result semantics | Stable error code/stage, completion reason, ordered events, and explicit loss/degradation |

SharkRail therefore does not make a one-line command inherently better. It
removes repeated supervision work when execution outlives one call or must be
managed as part of an autonomous system.

### Concrete examples

- **Long test suite:** stream output from a long-running test, distinguish root
  exit from final pipe drain, and return a classified timeout instead of hanging.
- **Parallel worktrees:** run builds and linters across multiple worktrees within
  configured session, output, input, and shutdown limits.
- **Development server:** keep a server session open while browser tests run,
  read only new output with a cursor, then cancel and clean up owned descendants.
- **Interactive debugger or REPL:** use PTY/ConPTY input and resize without
  changing the caller-facing lifecycle model.
- **Windows plus WSL:** preserve one execution intent while reporting the real
  cleanup boundary of the Windows launcher and Linux descendants separately.

SharkRail turns repeated private glue into four shared public assets:

| Public asset | Purpose | Current state |
| --- | --- | --- |
| Open execution contract | Stable lifecycle, event, error, limit, and capability semantics | Protocol, schema, and reliability contract exist |
| Native reference runtime | Demonstrate the contract with real platform mechanisms | Python alpha has Windows and POSIX backends plus WSL routing; guarantee coverage must be shown in the public status matrix |
| Cross-implementation conformance kit | Let clients and alternative runtimes prove compatible behavior | Tests exist, but a portable kit is still a roadmap priority |
| Shared failure corpus | Turn hangs, leaked descendants, output loss, encoding faults, and platform surprises into reusable regressions | Cases exist in tests, but need a named, documented corpus |

The project succeeds even when another implementation passes the public
conformance suite and a client can replace the reference runtime. Adoption of a
contract is public value; lock-in to one implementation is not.

## Differentiation must be proven

Process APIs, friendly subprocess wrappers, PTY/ConPTY libraries, process-tree
helpers, and agent terminal protocols already solve substantial parts of this
problem. SharkRail does not claim process creation, JSON-RPC, PTY allocation, or
Job Objects as inventions.

Relevant work includes [Execa](https://github.com/sindresorhus/execa),
[ProcessKit](https://github.com/ZelAnton/ProcessKit-rs),
[node-pty](https://github.com/microsoft/node-pty),
[pywinpty](https://github.com/andfoy/pywinpty),
[portable-pty](https://github.com/wezterm/wezterm/tree/main/pty), and the
[ACP terminal contract](https://agentclientprotocol.com/protocol/v1/terminals).
SharkRail should reuse, interoperate with, or contribute upstream to these
projects where possible and state overlap plainly. Comparative claims require
reproducible fixtures or benchmarks.

Its defensible public contribution must be the combination of domain-specific
semantics and portable black-box proof: exit versus drained completion,
byte-accurate loss accounting, observable cancellation escalation, verified
containment, and explicit Windows/WSL boundaries. General agent transport and
task semantics should map to standards such as ACP and MCP rather than compete
with them. Native libraries should remain replaceable backend building blocks.

Until an implementation-independent conformance kit and independent adopters
exist, the SharkRail wire protocol is a project contract—not an industry
standard. Documentation must preserve that distinction.

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

### Current alpha evidence boundary

The current repository has meaningful tests for session lifecycle, pipe and
POSIX PTY execution, bounded output, cursor behavior, timeouts, protocol paths,
and nominal MCP/ACP integration. It also contains Windows backend and Job Object
code rather than documentation-only placeholders.

That is not yet portable conformance evidence. Native Windows descendant
cleanup and ConPTY stress paths need stronger non-mock tests; WSL currently has
stronger routing evidence than cleanup evidence; resource enforcement needs
real workload verification; and long-running race, leak, and performance
baselines are not yet published. These gaps are release work, not footnotes to
be hidden behind a CI badge.

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
