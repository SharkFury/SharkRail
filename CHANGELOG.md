# Changelog

## 0.1.0 - 2026-09-03

- Add concurrent pipe and persistent terminal sessions
- Add native POSIX PTY and Windows ConPTY support
- Add POSIX process-group and Windows Job Object tree cleanup
- Add stdin, EOF, resize, interrupt, cancellation escalation, timeout, wait, and dispose
- Add ordered output/lifecycle events and cursor-based reads
- Add byte-accurate output truncation and runtime resource limits
- Add structured error codes and execution stages
- Add explicit shell and WSL target routing
- Add stdio JSON-RPC 2.0 runtime service
- Add capability negotiation and doctor diagnostics
- Add Windows, Linux, and macOS CI for Python 3.9 and 3.11
- Add a tested PyPI trusted-publishing and GitHub Release pipeline

## 0.0.1

- Initialize open-source repository layout for SharkRail
- Add initial CLI entrypoint and command execution core
- Add unit tests for command parsing and execution result handling
