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

Coinbase assembly does not parse or otherwise validate the assembled
transaction. Hashing is a separate operation described below.

## Double-SHA256 Boundary

`double_sha256` applies SHA-256 twice to immutable bytes and returns the raw
32-byte digest. `hash_coinbase_transaction` validates that assembled coinbase
transaction bytes are nonempty and delegates to that single cryptographic
primitive. The raw coinbase digest is the first value in the Merkle-root
calculation described below.

The raw internal digest is not byte-reversed or formatted as a displayed
transaction identifier. Human-readable transaction-ID display order is a
separate conversion and remains deferred.

This boundary does not calculate a Merkle root or target, construct a block
header, iterate nonces, mine, perform networking, or submit shares.

## Merkle Root Boundary

`calculate_merkle_root` starts with the raw coinbase transaction hash and
reduces the ordered Stratum Merkle branches iteratively. For each branch, it
decodes the branch hexadecimal directly and applies `double_sha256` to:

    current_hash || branch_bytes

The resulting raw digest becomes `current_hash` for the next branch. Branches
are processed exactly in their supplied order; an empty branch tuple returns
the original raw coinbase hash. Neither operands, intermediate hashes, nor the
final 32-byte root are reversed or converted to display order.

This boundary does not serialize a block header or perform header-specific byte
ordering. That conversion belongs to the block-header boundary below. Target
calculation, nonce generation and search, mining loops, networking, and share
submission remain deferred.

## Block Header Serialization Boundary

`serialize_block_header` produces the consensus 80-byte header layout:

    version (4) || previous block hash (32) || Merkle root (32) ||
    network time (4) || network bits (4) || nonce (4)

Bitcoin's numeric header fields use little-endian serialization. The Stratum
`version`, `network_time`, and `network_bits` strings are parsed as unsigned
32-bit hexadecimal values and serialized explicitly in little-endian order. The
caller-supplied nonce follows the same unsigned 32-bit little-endian rule. This
preserves every 32-bit pattern without relying on native machine endianness.

CKPool supplies the Stratum `previous_block_hash` in 32-bit-word-swapped form.
Conversion to the header's internal hash order reverses the bytes within each
successive four-byte chunk while leaving the eight chunks in place; it is not a
whole-hash reversal. By contrast, the raw Merkle root returned by
`calculate_merkle_root` is already in the internal byte order used by the
serialized header and is copied unchanged.

These rules were verified against the [Bitcoin block-header
reference](https://developer.bitcoin.org/reference/block_chain.html#block-headers),
Bitcoin Core's [genesis block
constants](https://github.com/bitcoin/bitcoin/blob/e75b76b12c5dcaf1c3b9f02d8739b1f551dcf421/src/kernel/chainparams.cpp),
and CKPool's [Stratum/header conversion
code](https://github.com/ckolivas/ckpool/blob/308410ddf321349704f252f36b82d77f2ae007fc/src/stratifier.c#L1594-L1610).
Protocol hexadecimal, raw digest bytes, serialized header order, and reversed
human-readable hash display order are distinct representations. Display-order
conversion is not part of the serializer.

Serialization itself does not hash headers, decode compact targets, compare
targets, generate or iterate nonces, mine, perform networking, or submit shares.

## Block Header Hashing Boundary

`hash_block_header` validates one serialized 80-byte header and delegates to the
generic `double_sha256` primitive to produce one raw 32-byte digest. The header
bytes are neither mutated nor reinterpreted.

The returned digest remains in the raw order produced by double-SHA256. Bitcoin
block hashes are conventionally displayed by reversing those raw bytes for
presentation, but this boundary performs no reversal and provides no display or
hexadecimal formatting API. It also does not interpret the digest as an integer.

This boundary does not convert pool difficulty, compare a hash with a target,
generate or iterate nonces, mine, perform networking, or submit shares. Compact
target decoding belongs to the boundary below.

## Compact Network Target Boundary

Bitcoin's `nBits` field represents the network proof-of-work target in a compact
base-256 form. `decode_compact_target` treats the unchanged eight-character
Stratum `network_bits` value as a 32-bit pattern. The high byte is the exponent,
the low 23 bits are the mantissa, and bit `0x00800000` is the sign flag. For
exponents through three, the mantissa is right-shifted; for larger exponents it
is left-shifted. The resulting full target is returned as a positive Python
integer using integer arithmetic only.

Negative encodings, targets reduced to zero by the small-exponent right shift,
and Bitcoin Core's three compact overflow cases are rejected. Results must fit
within 256 bits. The decoder does not clamp or normalize the representation and
does not enforce Bitcoin mainnet's proof-of-work limit; that is network policy,
not compact-format validation.

These rules mirror Bitcoin Core's [`arith_uint256::SetCompact`](https://github.com/bitcoin/bitcoin/blob/e75b76b12c5dcaf1c3b9f02d8739b1f551dcf421/src/arith_uint256.cpp#L174-L193)
and the structural checks used by [`DeriveTarget`](https://github.com/bitcoin/bitcoin/blob/e75b76b12c5dcaf1c3b9f02d8739b1f551dcf421/src/pow.cpp#L146-L158),
apart from the intentionally deferred network-specific proof-of-work limit.

The network target encoded by `network_bits` is distinct from CKPool's share
target derived from `mining.set_difficulty`. Compact decoding does not perform
share-difficulty conversion or compare a hash with either target.

## Network Proof-of-Work Comparison Boundary

`block_hash_to_int` interprets the unchanged raw 32-byte digest returned by
`hash_block_header` as an unsigned little-endian integer using exactly:

    int.from_bytes(block_hash, byteorder="little", signed=False)

The input is not reversed or converted through displayed hexadecimal. This
matches Bitcoin Core's little-endian `uint256` storage and its
`UintToArith256` conversion. `hash_meets_target` validates both operands and
returns whether the resulting integer is less than or equal to the structurally
decoded network target. Equality is therefore valid proof of work.

These rules mirror Bitcoin Core's [`UintToArith256`](https://github.com/bitcoin/bitcoin/blob/e75b76b12c5dcaf1c3b9f02d8739b1f551dcf421/src/arith_uint256.cpp#L212-L217)
and [`CheckProofOfWorkImpl`](https://github.com/bitcoin/bitcoin/blob/e75b76b12c5dcaf1c3b9f02d8739b1f551dcf421/src/pow.cpp#L145-L154).
The usual displayed block-hash string is a separate presentation convention and
is not used in production comparison.

Meeting the network target identifies a block candidate; it is distinct from
meeting CKPool's easier share target. Share-target calculation belongs to the
boundary below.

## Stratum Share Target Boundary

`difficulty_to_share_target` converts the positive integer or decimal float
from `mining.set_difficulty` into a full share target. Stratum difficulty uses
this exact difficulty-1 target:

    00000000ffff0000000000000000000000000000000000000000000000000000

The conversion is the mathematical floor of:

    difficulty-1 target / difficulty

Integer difficulties become `Fraction(difficulty, 1)`. Finite floats first use
their decimal string representation through `Fraction(str(difficulty))`. The
target is then calculated with integer multiplication and floor division:

    (difficulty-1 target * ratio.denominator) // ratio.numerator

This avoids binary floating-point target division and makes decimal Stratum
values such as `0.01` deterministic. Results must remain within the inclusive
unsigned 256-bit range; the implementation rejects rather than clamps zero or
overflowing targets.

The difficulty-1 convention is documented by the [Stratum V1 protocol
reference](https://reference.cash/mining/stratum-protocol#mining-set-difficulty)
and matches the constant and division used by [CKPool](https://github.com/ckolivas/ckpool/blob/308410ddf321349704f252f36b82d77f2ae007fc/src/libckpool.c#L2089-L2095)
and [cgminer](https://github.com/ckolivas/cgminer/blob/b8491c66e7e22f23a9edf095dd1337ee581e88bd/cgminer.c#L4276-L4282).

The network target still comes independently from `network_bits`; the share
target comes from the current difficulty snapshot. Both use the existing
inclusive `hash_meets_target` comparison without duplicating comparison logic.

## Prepared Work and Bounded Nonce Search

`prepare_mining_work` performs the fixed mining pipeline once for each
immutable `MiningJob` and caller-supplied `extra_nonce_2`: coinbase assembly,
coinbase hashing, Merkle reduction, nonce-zero header serialization, network
target decoding, and share-target calculation. It retains the job ID, unchanged
extra nonce and network time, both targets, and the first 76 bytes of the
validated serialized header. Those bytes contain every header field except the
four-byte nonce.

`search_nonce_range` searches a bounded half-open range: `start_nonce` is
inclusive and `stop_nonce` is exclusive. It visits nonces sequentially in
ascending order. A stop value of `2**32` allows the final unsigned 32-bit nonce,
`0xffffffff`, to be included. Within the loop, the only changing data is the
nonce serialized explicitly as four little-endian bytes and appended to the
prepared prefix. Coinbase data, Merkle roots, header prefixes, and targets are
not recalculated.

Each raw header digest is interpreted once in the existing unsigned
little-endian representation and compared independently with the share and
network targets. Search stops at the first digest meeting either target. A
share match records separately whether it is also a network candidate, and a
network candidate remains valid even if an unusual target configuration means
it does not meet the share target.

The immutable result records the requested range, exact number of hashes,
monotonic elapsed nanoseconds, and an optional first match. Exhausted ranges
contain no match. Local hashes per second are derived from the count and elapsed
time; a zero-duration measurement has no reported rate.

This is deliberately a bounded synchronous search and performs no submission.
Continuous mining, `extra_nonce_2` generation or rollover, network-time rolling,
`clean_jobs` cancellation, worker partitioning, threads, multiprocessing, GPU
execution, orbiting-bit search, live Stratum integration, and share submission
remain deferred.

## Stratum Share-Submission Message Boundary

`build_submit_request` constructs, but does not transmit, a `mining.submit`
request. Its five parameters are ordered exactly as follows:

1. the previously authorized username supplied by the caller
2. `PreparedMiningWork.job_id`
3. `PreparedMiningWork.extra_nonce_2`
4. `PreparedMiningWork.network_time` (Stratum `ntime`)
5. the nonce from `NonceSearchMatch`

The nonce integer is converted with
`nonce.to_bytes(4, byteorder="little", signed=False).hex()`. This produces the
same four bytes that the bounded search appended to its 76-byte header prefix;
it is not direct integer formatting, native-endian serialization, or reversal
of formatted text. Other hexadecimal parameters retain their caller-supplied
case and representation. The ordering and byte-based nonce representation
match [CKPool's upstream submission construction](https://github.com/ckolivas/ckpool/blob/308410ddf321349704f252f36b82d77f2ae007fc/src/generator.c#L2042-L2048)
and [cgminer's Stratum share construction](https://github.com/ckolivas/cgminer/blob/b8491c66e7e22f23a9edf095dd1337ee581e88bd/cgminer.c#L7138-L7163).

`parse_submit_result` accepts only an actual JSON Boolean: `true` means the
pool accepted the share and `false` means it rejected the share. A rejection is
a valid parsed response, while non-null Stratum errors remain the responsibility
of the request-response routing layer.

Request transmission, a `StratumClient` submission operation, live pool
submission, retries, stale- or duplicate-share handling, and result counters
remain deferred. This slice performs no network activity.

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
    ├── header.py
    ├── job.py
    ├── merkle.py
    ├── search.py
    ├── target.py
    ├── engine.py
    ├── profiles.py
    ├── scheduler.py
    └── backends/
        ├── __init__.py
        ├── cpu.py
        └── gpu.py

Responsibilities:

- `coinbase.py`: assemble and hash raw coinbase transaction bytes
- `header.py`: serialize and hash raw 80-byte block headers
- `job.py`: validate and assemble immutable mining-job snapshots
- `merkle.py`: reduce a raw coinbase hash and ordered branches to a raw Merkle root
- `search.py`: prepare fixed mining work and search bounded sequential nonce ranges
- `target.py`: calculate targets and compare raw block-hash integers
- `engine.py`: coordinate mining jobs and search operations
- `profiles.py`: define Lite, Auto, and Max policies
- `scheduler.py`: control worker allocation and duty cycles
- `backends/cpu.py`: implement CPU hashing
- `backends/gpu.py`: implement future GPU hashing

## Cryptographic Components

    src/hashphere/crypto/
    └── hashing.py

Responsibilities:

- `hashing.py`: provide reusable raw double-SHA256 digest calculation

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
