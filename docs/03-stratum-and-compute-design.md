# Stratum and Compute Design

## Purpose

Hashphere will initially mine through Solo CKPool using the Stratum
protocol. This avoids requiring a local Bitcoin Core node and the full
blockchain download during early development.

Support for direct solo mining through Bitcoin Core will be added later.

## Initial CKPool Configuration

Default endpoint:

    stratum+tcp://stratum.ckpool.org:3333

CKPool authentication uses:

- a Bitcoin payout address as the account identity
- an optional worker name appended to the address
- a simple Stratum password, normally `x`

Hashphere constructs the username as:

    <bitcoin-address>.<worker-name>

The payout address must be supplied through an environment variable. It
must not be embedded in source code.

## Worker Identity

Hashphere separates the payout address from the worker identity:

    HASHPHERE_BITCOIN_ADDRESS=YOUR_BITCOIN_ADDRESS
    HASHPHERE_WORKER_NAME=auto

When `HASHPHERE_WORKER_NAME` is `auto`, Hashphere uses the machine
hostname after sanitizing it.

Examples:

    bc1q...example.macbook-air
    bc1q...example.spark-2b09
    bc1q...example.windows-pc

A worker name may also be explicitly configured. This is useful for
Docker containers whose generated hostnames may not be stable.

## Security Boundary

Hashphere requires only a public Bitcoin receive address.

Hashphere must never request, read, store, or transmit:

- wallet seed phrases
- wallet private keys
- wallet passwords
- Exodus recovery information

A public receive address is sufficient for CKPool authentication and
payout.

## Architectural Principle

Hashphere separates the mining engine from the source of mining work.

                         +-------------------+
                         |   Mining Engine   |
                         +---------+---------+
                                   |
                    +--------------+--------------+
                    |                             |
          Stratum work source             Bitcoin Core source
             CKPool first                       future

The mining engine receives normalized mining jobs. It must not depend
directly on CKPool, Stratum socket handling, or Bitcoin Core RPC.

## Compute Backend and Compute Profile

Compute backend and compute profile are separate settings.

### Backends

- `cpu`: use the CPU hashing backend
- `gpu`: use a supported GPU backend
- `auto`: choose the best available supported backend

The first implementation will support the CPU backend.

GPU support is an architectural requirement, but it will be implemented
after the CPU mining path is correct, tested, and benchmarked.

### Profiles

#### Lite

Designed for normal computer use while Hashphere runs in the background.

Initial policy:

- use approximately 25 percent of logical CPU capacity
- run workers at a reduced duty cycle
- use low process priority where supported
- reserve substantial capacity for the user and operating system

#### Auto

Designed to adapt to current system activity.

Initial policy:

- begin near 50 percent of logical CPU capacity
- monitor system load and responsiveness
- reduce mining activity when the computer becomes busy
- cautiously increase activity when capacity becomes available
- never exceed the limits of the Max profile

#### Max

Designed for the highest practical hashrate without intentionally making
the operating system unusable.

Initial policy:

- use most logical CPUs
- reserve at least one logical CPU for the operating system
- avoid real-time scheduling priority
- allow thermal and load safeguards to reduce activity

## Power Terminology

The compute profiles control:

- worker count
- worker duty cycle
- scheduling priority

They do not promise an exact electrical-power percentage.

Exact power measurement and control are platform-specific and may not be
available consistently on macOS, Windows, Linux, Docker, and DGX Spark.

## Proposed Mining Components

    src/hashphere/mining/
    ├── engine.py
    ├── profiles.py
    ├── scheduler.py
    └── backends/
        ├── __init__.py
        ├── cpu.py
        └── gpu.py

Responsibilities:

- `engine.py`: coordinate mining jobs and search operations
- `profiles.py`: define Lite, Auto, and Max policies
- `scheduler.py`: control worker allocation and duty cycles
- `backends/cpu.py`: implement CPU hashing
- `backends/gpu.py`: implement future GPU hashing

## Proposed Stratum Components

    src/hashphere/network/
    └── stratum/
        ├── client.py
        ├── messages.py
        ├── protocol.py
        └── transport.py

Responsibilities:

- `transport.py`: manage TCP and newline-delimited JSON
- `protocol.py`: handle Stratum requests and responses
- `messages.py`: parse and validate Stratum messages
- `client.py`: manage the connection lifecycle and public interface

## First Vertical Slice

The first executable Stratum probe will:

1. Connect to CKPool over TCP.
2. Send `mining.subscribe`.
3. Parse the subscription response.
4. Send `mining.authorize`.
5. Confirm authorization.
6. Receive `mining.set_difficulty`.
7. Receive `mining.notify`.
8. Display sanitized protocol information.
9. Disconnect cleanly.

The first probe will not hash or submit shares.

## Definition of Done

The first Stratum milestone is complete when:

- configuration loads from environment variables
- no real payout address is committed to Git
- automatic worker names use sanitized hostnames
- the TCP connection opens and closes cleanly
- subscribe and authorize messages are tested
- incoming JSON messages are parsed safely
- malformed messages do not crash the client
- wallet secrets are never requested
- setup and troubleshooting instructions are documented
