# Observability

SharkRail exposes observability without requiring a monitoring stack and keeps
protocol stdout free of logs.

## Events and inspection

Every lifecycle event contains an absolute sequence number, UTC timestamp,
monotonic timestamp, trace ID, kind, and payload. Output events include both a
UTF-8 text view and Base64 bytes. Results report execution and drain duration,
retained/dropped byte counts, decoding status, and lossless Base64 output.

JSON-RPC clients can use:

- `runtime.health` for lightweight liveness/readiness
- `runtime.stats` for session, error, I/O, cancellation, RPC, and recorder data
- `session.list` for retained sessions
- `session.inspect` for one session's state, age, cursors, buffers, and context
- `session.subscribe` for bounded, cursor-based event pages

## Structured logs

Logs are JSON objects written to stderr. The default level is `WARNING`; set
`SHARKRAIL_LOG_LEVEL=INFO` or call `configure_logging`. Session logs contain
identifiers, mode, reason, timings, and error codes, but never command arguments
or environment values.

## OpenTelemetry

Install the optional integration and connect providers in the host process:

```bash
python -m pip install -e ".[observability]"
```

```python
from sharkrail import configure_opentelemetry

configure_opentelemetry()
```

SharkRail then records completion counters, execution/drain histograms, byte
counters, and a session span through the host's configured OpenTelemetry meter
and tracer providers. No exporter or network destination is selected by
SharkRail.

## Event audit files

The CLI and server can append a size-bounded JSONL event audit:

```bash
sharkrail run --event-log sharkrail-events.jsonl -- python -V
sharkrail serve --event-log sharkrail-events.jsonl
```

Output text and Base64 data are redacted by default. Use
`--event-log-include-output` only when the destination has appropriate access
controls. `--event-log-max-bytes` prevents unbounded growth; `runtime.stats`
reports truncation or recorder write errors.

For an issue-ready system snapshot, run:

```bash
sharkrail doctor --bundle sharkrail-diagnostics.json
```

The bundle contains platform and active-probe results, not environment values,
command arguments, or session output.
