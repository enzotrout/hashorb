# Stratum and Compute Design

## What

HashOrb has two deliberately separate live Bitcoin paths:

1. **Stratum mining** connects to a mining server such as CKPool, receives jobs, prepares work, searches nonce ranges, and can submit qualifying shares.
2. **Bitcoin Core true solo** talks to an operator-controlled local Bitcoin Core node, constructs complete block work, and keeps readiness, hash-only, and submission-capable commands structurally separate.

This document is the architecture overview for the Stratum path and the shared compute/search boundaries. Direct Bitcoin Core operation is documented separately in [Bitcoin Core True Solo](14-bitcoin-core-true-solo.md).

## Why

Network protocol, Bitcoin work construction, search order, and hash execution are different responsibilities. Keeping them separate lets HashOrb change a compute backend or search strategy without silently changing protocol behavior, share submission, target math, or block construction.

## Plain Talk

The pool tells HashOrb what to work on. HashOrb turns that job into Bitcoin header work. A search strategy chooses the next ordinary region of the nonce map. A compute backend hashes that region. If a result meets the required target, HashOrb verifies it before the appropriate network layer is allowed to submit it.

Changing the route around the nonce map does not change SHA-256. Changing CPU to CUDA does not change the pool protocol.

## Stratum Configuration

The example configuration uses Solo CKPool:

```dotenv
HASHORB_STRATUM_HOST=stratum.ckpool.org
HASHORB_STRATUM_PORT=3333
HASHORB_BITCOIN_ADDRESS=YOUR_BITCOIN_ADDRESS
HASHORB_WORKER_NAME=auto
HASHORB_STRATUM_PASSWORD=x
```

The payout address is a **public Bitcoin receive address**. HashOrb does not need a wallet seed phrase, private key, or wallet password for Stratum mining.

When the worker name is `auto`, HashOrb derives a sanitized worker identity from the machine environment. An explicit worker name is useful when container hostnames are not stable.

## Live-Operation Boundary

HashOrb does not begin live Stratum mining merely because configuration exists. Live networking and live mining are explicit opt-ins.

A short bounded example is:

```bash
export HASHORB_ENABLE_LIVE_STRATUM=1
export HASHORB_ENABLE_LIVE_MINING=1
hashorb stratum-mine \
  --profile auto \
  --max-runtime-seconds 300 \
  --log-file logs/events.jsonl
```

See the [Quick Start Guide](QUICKSTART.md) for Linux, macOS, Windows, and Docker forms.

## Shared Mining Pipeline

Conceptually, the Stratum path is:

```text
Stratum server
      ↓
job / difficulty notifications
      ↓
validated mining job
      ↓
prepared Bitcoin work
      ↓
search strategy chooses parent range
      ↓
compute backend searches that range
      ↓
Python-side result verification
      ↓
share candidate
      ↓
Stratum submission and response
```

The layers are intentionally narrow:

- **Stratum transport/client** owns connection framing, requests, notifications, and responses.
- **Mining job/work code** owns protocol-to-Bitcoin work preparation.
- **Search strategy** owns only the order of parent nonce ranges.
- **Compute backend** owns only searching the supplied range.
- **Mining orchestration** owns progression, liveness, reconnects, stop boundaries, and submission decisions.
- **Observability** receives sanitized events after the mining logic has made its decisions.

## Search Strategy vs. Compute Backend

These are independent selections.

Implemented search strategies include:

- `sequential`
- `orbiting-bit`
- `fibonacci-bounce`

See [Search Strategies](08-search-strategies.md).

Implemented compute paths include:

- `python`
- `native`
- `native-parallel`
- `cuda`
- `cuda-multi` where explicitly configured and available

See [Compute Backends](05-compute-backends.md).

A strategy receives no authority to open sockets, submit shares, construct blocks, or change targets. A backend receives no authority to choose the global range order or submit a result.

## Targets

Pool mining distinguishes the target used for share qualification from the Bitcoin network target. A pool share can be useful to the server without being a valid Bitcoin block.

HashOrb keeps target interpretation in Bitcoin/mining logic rather than hiding it inside a search strategy or hardware backend. Candidate results are independently checked before they can cross a submission boundary.

## Work Progression

A 32-bit nonce range is not the entire lifetime of a mining job. HashOrb can advance work variants as required by the active mining path while preserving explicit job identity and stale-work boundaries.

New Stratum jobs, clean-job signals, liveness events, reconnects, and work-variant changes are handled above the compute layer. A backend only sees the prepared work and exact bounded range it was asked to search.

## Recovery and Rejection Behavior

A share response is not treated as permission to stop all mining. Accepted and structured rejected shares can be recorded while continuous mining proceeds when the surrounding work remains valid.

Connection loss, stale work, ambiguous protocol failures, and explicit stop conditions are handled by the mining/recovery layer rather than by compute code.

## Observability

Live commands can write sanitized JSONL events:

```bash
--log-file logs/events.jsonl
```

The event stream is local and intentionally excludes secret configuration values. The terminal dashboard reads this stream rather than reaching back into mining control.

See [Observability](04-observability.md) and [Terminal Dashboard](14-dashboard-tui.md).

## Direct Bitcoin Core Is Separate

Stratum configuration must not become an accidental authority for Bitcoin Core submission. Direct solo mining has its own RPC configuration and separate command boundaries:

- `hashorb bitcoin-core-check`
- `hashorb solo-hash`
- `hashorb solo-mine`

The hash-only path cannot submit a reward-winning block because submission capability is absent. The submission-capable path requires the explicit true-solo controls described in [Bitcoin Core True Solo](14-bitcoin-core-true-solo.md).

## Design Rule

A useful way to read the HashOrb architecture is:

> Protocol decides what work is available. Mining orchestration decides whether that work is still valid. Strategy decides where to search next. Backend decides how to execute that search. Verification decides whether a result is real. Only the explicitly authorized network boundary may submit it.

For a deeper repository-wide view, see [`ARCHITECTURE.md`](../ARCHITECTURE.md).