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

## Mining Job Domain Boundary

`MiningJobAssembler` bridges parsed Stratum notifications into the mining
domain. It retains the subscription's extra-nonce parameters, tracks the most
recent difficulty notification, and snapshots that difficulty into each new
immutable `MiningJob`. A job cannot be assembled until a difficulty has been
received. Later difficulty changes affect only later jobs, and `clean_jobs` is
retained without managing an active-job registry or invalidation policy.

The domain validates identifiers, hexadecimal byte structure, fixed protocol
field sizes, difficulty, and extra-nonce sizes independently of the networking
parser. Protocol hexadecimal strings remain unchanged; byte-order conversion
is deferred to serialization.

The mining-job boundary itself does not calculate share or network targets,
build coinbase transactions, calculate Merkle roots, serialize headers, generate
`extra_nonce_2`, hash, mine, or submit shares. Those responsibilities remain
deferred to later slices.

## Coinbase Assembly Boundary

`build_coinbase_transaction` deterministically assembles raw transaction bytes
from a validated `MiningJob` and a caller-supplied `extra_nonce_2`. The protocol
composition is exactly:

    coinbase_part_1 || extra_nonce_1 || extra_nonce_2 || coinbase_part_2

All four hexadecimal components are decoded in that order without padding,
normalization, byte reversal, or endian conversion. The caller is responsible
for choosing or generating `extra_nonce_2`; the assembler only validates its
type, hexadecimal representation, and exact session-defined size.

This boundary does not parse or otherwise validate the assembled transaction,
calculate a transaction ID or coinbase hash, apply double-SHA256, calculate a
Merkle root or target, construct a block header, iterate nonces, hash, perform
networking, or submit shares. Those stages remain deferred.

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
    ├── coinbase.py
    ├── job.py
    ├── engine.py
    ├── profiles.py
    ├── scheduler.py
    └── backends/
        ├── __init__.py
        ├── cpu.py
        └── gpu.py

Responsibilities:

- `coinbase.py`: assemble raw coinbase transaction bytes from protocol components
- `job.py`: validate and assemble immutable mining-job snapshots
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
