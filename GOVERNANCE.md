# Governance

SharkRail uses a maintainer-led, contribution-driven governance model. The goal
is to make decisions transparent while keeping a small infrastructure project
able to move decisively.

## Roles

- **Users** run SharkRail and provide operational feedback.
- **Contributors** submit issues, code, tests, documentation, or reviews.
- **Reviewers** are trusted contributors who regularly provide accurate review.
- **Maintainers** merge changes, manage releases and security reports, enforce
  community standards, and protect the project's technical contracts.

Roles are earned through sustained, constructive contributions and sound
judgment. Maintainers may invite a contributor into a broader role after public
discussion of their track record and the project's needs.

## Decisions

Routine changes are decided through issue and pull request review. Significant
changes—public API, protocol, lifecycle, security boundary, compatibility, or
governance—should begin as a design issue describing the problem, options,
trade-offs, and migration impact.

Maintainers seek rough consensus, with extra weight given to evidence from
implementations, tests, and production use. When consensus cannot be reached,
the maintainers make the final decision and document the reasoning. Silence is
not treated as consensus for a breaking change.

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
