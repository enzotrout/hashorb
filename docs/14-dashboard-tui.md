# Terminal Dashboard TUI

## Purpose

HashOrb's first dashboard is a read-only terminal user interface built on the existing sanitized JSONL event stream. It is intentionally downstream of mining and observability:

```text
Mining / Stratum / Compute
          ↓
Sanitized JSONL events
          ↓
Dashboard state projection
          ↓
Terminal renderer
```

The dashboard does not call mining functions, change profiles, select backends, submit shares, reconnect sessions, or alter nonce-search strategy state. It only reads an existing structured log.

## Run It

Live mode follows a log and redraws the terminal until Ctrl-C:

```bash
hashorb dashboard --log-file logs/hashorb.jsonl
```

Choose a different refresh cadence when needed:

```bash
hashorb dashboard \
  --log-file logs/hashorb.jsonl \
  --refresh-seconds 0.5
```

Render one deterministic snapshot and exit:

```bash
hashorb dashboard \
  --log-file logs/hashorb.jsonl \
  --once
```

`--once` is also the preferred mode for CI, archived logs, redirected output, and terminals where continuous redraw is not appropriate.

## What It Shows

The initial dashboard includes:

- active mining command and run state
- requested/effective profile when present
- backend and selected CUDA ordinal or CPU worker count
- search strategy
- sanitized Stratum endpoint
- difficulty and current safe job identifier
- uptime and current job age
- hashes checked and completed nonce ranges
- weighted raw compute rate
- recent wall-clock effective rate, including profile pacing
- rate-history sparkline from completed ranges
- jobs received and replacements
- work-variant progression
- reconnects and connection losses
- duplicate work and liveness/stale-session counters
- share candidates and accepted/rejected submission counts
- recent notable events
- optional safe NVIDIA temperature, power, utilization, and memory readings while a run is active

No payout address, username, password, raw work, complete extra nonce, wallet material, arbitrary exception text, GPU UUID, serial number, PCI address, or device path is displayed.

## Nonce-Space Visualization

The 32-bit nonce space is rendered as terminal buckets between `0x00000000` and `0xffffffff`.

The dashboard does not ask a search strategy where it plans to go. It observes only the already-sanitized half-open ranges recorded by `nonce_range_started` and `nonce_range_completed`.

For the sequential strategy, completed buckets naturally fill from low nonce values toward high values. For orbiting-bit, completed buckets naturally appear distributed because the parent ranges arrive in the strategy's bit-reversal order. The dashboard also shows a short observed bucket path so the difference is visually obvious.

The visualization resets when HashOrb selects a new work variant through `mining_job_replaced` or `mining_work_advanced`. Historical hash totals remain run-wide; only the per-work nonce picture resets.

Legend:

```text
█  observed completed nonce space
▓  range currently being searched
·  not yet observed for the current work variant
```

This display is diagnostic and illustrative. It does not alter range allocation, uniqueness, strategy ordering, or candidate probability.

## Raw Rate Versus Effective Rate

The dashboard deliberately shows two rates:

- **Raw** is the weighted compute-only rate from completed nonce-range timings.
- **Effective** is a recent wall-clock rate derived from cumulative completed hashes and event timestamps.

This distinction makes profile pacing visible. On a paced CUDA profile such as Auto, the CUDA kernel can remain near its normal raw rate while effective throughput is lower because deliberate inter-range rest time is included in wall time.

For a completed profiled run, the dashboard uses the terminal `effective_hashes_per_second` recorded by HashOrb when that field is present. Completed-run uptime and job age also freeze at the terminal event timestamp. An archived `--once` snapshot therefore remains stable instead of continuing to age against the current clock.

## NVIDIA Telemetry

When an active run exposes a CUDA ordinal and `nvidia-smi` is installed, the dashboard may perform a read-only local query for exactly:

- GPU temperature
- power draw
- GPU utilization
- total memory
- used memory

Telemetry failure is informational only. HashOrb does not display raw `nvidia-smi` errors and does not query hardware identity fields such as UUID, serial number, PCI address, or device path.

GPU telemetry is intentionally live-only. Once a run reaches a terminal completion or failure, the dashboard stops probing `nvidia-smi` and reports that GPU telemetry was omitted because current host readings are not historical measurements of the completed run.

## Log-Following Behavior

The follower opens the source as a regular, non-symlink file in read-only mode. It tracks the byte offset and preserves an incomplete final JSON line until the writer finishes it. A complete malformed record fails visibly instead of being skipped.

If the file is replaced or truncated, the follower resets its projection and rereads from the beginning of the new source. It never creates, repairs, truncates, appends, rotates, or deletes the log.

If multiple command runs exist in one append-only file, the dashboard selects the newest supported mining run and ignores later interleaved events from older runs.

## First-Slice Boundary

The visual mockup includes keyboard actions such as start/stop mining, profile changes, backend changes, and reconnect commands. Those are deliberately **not** part of this foundation.

The first slice proves the state model, live rates, nonce-space visuals, terminal rendering, safe local telemetry, and read-only log following. Interactive mining controls can be designed later as a separate authorization and lifecycle slice rather than mixing control behavior into the presentation foundation.
