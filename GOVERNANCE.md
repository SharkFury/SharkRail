# Governance

SharkRail uses a maintainer-led, contribution-driven governance model. The goal
is to make decisions transparent while keeping a small infrastructure project
able to move decisively.

## Public-interest stewardship

SharkRail is maintained as open public infrastructure, not as a funnel for a
hosted or paid product. The official roadmap optimizes for shared reliability,
interoperability, user control, and maintainability rather than revenue or
paid-customer privilege.

The official runtime, protocol, schemas, conformance assets, and documentation
remain public. The project has no proprietary official edition, paywalled core
capability, mandatory account, required hosted control plane, or required
remote telemetry. Funding or sponsorship, if accepted, must be disclosed and
does not buy protocol outcomes, release decisions, maintainer roles, or a
private roadmap.

The MIT License permits commercial use by anyone. These commitments govern the
stewardship of the official project; they do not add a license restriction.
See [the public value design](docs/VALUE.md) for the complete value hierarchy
and evidence model.

## Roles

- **Users** run SharkRail and provide operational feedback.
- **Contributors** submit issues, code, tests, documentation, or reviews.
- **Reviewers** are trusted contributors who regularly provide accurate review.
- **Maintainers** merge changes, manage releases and security reports, enforce
  community standards, and protect the project's technical contracts.

Roles are earned through sustained, constructive contributions and sound
judgment. Maintainers may invite a contributor into a broader role after public
discussion of their track record and the project's needs.
The current roster and responsibility areas are recorded in
[MAINTAINERS.md](MAINTAINERS.md); CODEOWNERS indicates required review, not a
separate governance role.

## Decisions

Routine changes are decided through issue and pull request review. Significant
changes—public API, protocol, lifecycle, security boundary, compatibility, or
governance—should begin as a design issue describing the problem, options,
trade-offs, and migration impact.

Maintainers seek rough consensus, with extra weight given to evidence from
implementations, tests, and production use. When consensus cannot be reached,
the maintainers make the final decision and document the reasoning. Silence is
not treated as consensus for a breaking change.

Employment, sponsorship, company size, or popularity does not grant additional
decision authority. Vendor-specific requirements should remain in adapters or
extensions unless public evidence demonstrates a shared execution contract.

## Releases and compatibility

Maintainers own release authorization and follow [docs/RELEASING.md](docs/RELEASING.md).
Compatibility decisions follow [docs/VERSIONING.md](docs/VERSIONING.md). Security
fixes may be handled privately until coordinated disclosure.

## Project assets

Maintainers are responsible for least-privilege access to the source repository,
package index, release workflows, and security reports. No single contribution
grants access to project credentials or publishing authority.

## Amendments

Governance changes use the same public design process as other significant
changes. The current policy in the default branch is authoritative.
