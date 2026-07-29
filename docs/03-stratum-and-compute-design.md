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

This primitive remains a bounded synchronous search and performs no submission.
Higher-level bounded and continuous orchestration may call and submit its typed
result. `extra_nonce_2` rollover, network-time rolling, mid-chunk `clean_jobs`
cancellation, worker partitioning, threads, multiprocessing, GPU execution,
and alternative search order remain outside this Python reference primitive.
Higher layers now provide deterministic work progression and configurable
parent-range scheduling; native-parallel and CUDA own their private execution
mappings.
Mid-chunk cancellation, multi-GPU execution, and additional search orders
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

The message helper itself performs no network activity. Authenticated
transmission is owned separately by `StratumClient`.

## Authenticated Share-Transmission Boundary

`StratumClient.submit_share` is available only in the `AUTHORIZED` state. It
allocates the next internal request ID, uses the authenticated
`Settings.stratum_username`, builds the request through `build_submit_request`,
and sends it through the existing synchronous transport abstraction. The
caller supplies the job ID, `extra_nonce_2`, network time, and nonce produced by
the prepared-work and bounded-search boundary.

Submission responses use the existing request-routing path. Supported
notifications received before the matching response are parsed and queued in
arrival order for later `receive_notification` calls. Malformed or mismatched
response IDs and non-null Stratum errors retain their existing client error
behavior.

A Boolean `true` response means accepted; `false` means rejected and remains a
normal result rather than a protocol exception. Either result leaves the
client authorized. Send, receive, validation, and protocol failures propagate
without automatically closing, reconnecting, retrying, or resubmitting; the
caller remains responsible for `close`.

The explicitly opt-in runners described below own bounded and continuous live
integration. Stale- or duplicate-share classification and automatic retry
remain deferred.

## Bounded Notification Polling

An authorized `StratumClient` can call `poll_notification(timeout_seconds=0.0)`
to check the shared Stratum stream for one supported notification without
blocking indefinitely. The timeout accepts a finite, nonnegative integer or
float. Zero requests a nonblocking check; a normal receive timeout returns
`None` and leaves the client authorized. Supported queued notifications are
always returned first and retain their original arrival order.

When the queue is empty, the transport temporarily applies the requested
timeout while preserving newline-delimited framing. It restores the socket's
previous timeout after a message, a timeout, or a receive failure. Partial
message bytes remain buffered for the next receive. Only the dedicated normal
receive-timeout condition becomes `None`; connection closure, transport
failure, malformed messages, unexpected responses, and unsupported
notifications continue to raise their existing errors. Polling does not close,
reconnect, retry, change request IDs, or discard messages.

Bounded chunked mining uses this check between exhausted nonfinal searches.
Continuous mining uses nonblocking drains between chunks and 0.25-second polls
while waiting for initial work or after deterministic local progression is
fully exhausted. Mid-chunk cancellation remains deferred; local time rolling
belongs to the separate deterministic progression boundary below.

## One-Shot Live Mining Orchestration

`stratum-mine-once` is guarded by both
`HASHPHERE_ENABLE_LIVE_STRATUM=1` and
`HASHPHERE_ENABLE_LIVE_MINING=1`. It loads `Settings`, handshakes through
`StratumClient`, creates a `MiningJobAssembler` from the subscription, generates
one `extra_nonce_2`, prepares fixed work once, searches exactly one caller-
bounded half-open nonce range, and conditionally submits the exact returned
match. The client is closed on success or failure.

Difficulty applies only to subsequent jobs. Each difficulty notification
replaces the assembler's current difficulty. Jobs arriving before the first
difficulty are discarded for assembly rather than retained and combined with a
later update. The first valid job arriving after a known difficulty snapshots
that difficulty and becomes the only job searched by the invocation. This also
applies to notifications that were queued during the handshake.

The runner generates `extra_nonce_2` exactly once with
`secrets.token_hex(extra_nonce_2_size)`. The same lowercase hexadecimal value
is passed to work preparation and any conditional submission. It is not rolled
or regenerated during search. Fixed work is prepared once and
`search_nonce_range` is called once with the requested start and exclusive stop
unchanged.

An exhausted range is a successful bounded run and performs no submission. A
share-target or network-target match is submitted once. Pool acceptance and
rejection are both completed exchanges with exit status zero; failures return
nonzero. There is no retry, resubmission, reconnect, or automatic continuation
into another range.

## Bounded Multi-Chunk Mining Orchestration

`stratum-mine-chunks` uses both live opt-ins and a caller-supplied
`ChunkedMiningPlan`. The plan contains a configured start nonce, positive chunk
size, and positive invocation-wide maximum hash count. All values use the
unsigned 32-bit nonce space, and the global budget may not extend beyond the
space remaining after the configured start.

The reusable mining orchestrator prepares initial fixed work once, then
searches adjacent half-open ranges for that job. Each stop is the smaller of
the next chunk boundary and remaining global budget, so there are no gaps,
overlaps, or silent budget increases. The last chunk is shortened as needed.
Hash and elapsed-time totals span every chunk and job; aggregate rate is
derived from those integer totals rather than averaged chunk rates.

After an exhausted nonfinal chunk, the orchestrator repeatedly requests a
nonblocking poll until no immediately available notification remains. Arrival
order is semantic. Difficulty replaces the assembler's current value for
subsequent jobs only. Each `mining.notify` immediately snapshots the
difficulty current at that position. Thus job-then-difficulty preserves the
old difficulty for that job, while difficulty-then-job uses the new value;
repeated updates replace the value used by the next job.

Every announced job is validated and assembled in arrival order using the
difficulty current at that position. If several jobs are drained, only the
final newest selected job is prepared using the invocation's same
`extra_nonce_2`; superseded intermediate jobs are neither prepared nor
searched. One drain therefore records at most one replacement transition from
the previously searched job to the final job selected for the next chunk. Both
`clean_jobs=true` and `clean_jobs=false` switch to the newer job. The former
invalidates old work by protocol instruction, while the latter switch is
Hashphere's deliberate freshness policy even though the pool may still accept
the old job.

A replacement restarts its own nonce position at the configured start, but
does not reset hashes already consumed from the global budget. Exactly one
`extra_nonce_2` is generated per invocation and is never printed, regenerated,
or advanced. A running chunk is never interrupted; notifications are
processed only after it exhausts. A candidate stops the loop and is submitted
immediately with its exact work, without polling or job switching first.
Network-target-only candidates are also submitted. Acceptance and rejection
are terminal successful outcomes, with no retry or continuation.

Mid-chunk `clean_jobs` cancellation, `extra_nonce_2` progression, network-time
rolling, reconnects, telemetry aggregation, multiprocessing, GPU scheduling,
and additional search orders remain deferred. The selected sequential or
orbiting-bit strategy may schedule the command's bounded parent-range domain;
its internal skipped permutation indexes are not searched chunks.

## Continuous Mining Lifecycle

`ContinuousMiningPlan` defines a configured start nonce, positive chunk size,
optional positive maximum searched-chunk count, and optional positive finite
runtime limit of at most 31,536,000 seconds. Omitting both limits creates no
hidden boundary: the synchronous session continues until cooperative stop, a
submission result, or failure. Runtime timing starts from a monotonic clock only
after configuration and backend/strategy validation enter the active lifecycle.
Idle polls and notifications consume runtime but do not consume chunk count;
replacement work resets neither limit.

The CLI creates one `StopController` and a `StratumSessionRecovery` owner. The
owner handshakes, creates a fresh `MiningJobAssembler`, and generates one
session-scoped `extra_nonce_2` seed after successful authorization. Initial
work is acquired with repeated 0.25-second `StratumClient.poll_notification`
calls rather than an indefinite blocking read. A normal timeout returns
control for another stop check. A job received before the first difficulty is
observed and discarded; it is never retained for later reuse. The first job
arriving after known difficulty starts that session's mining lifecycle.

For one prepared variant, `run_continuous_mining` searches adjacent half-
open ranges. The first begins at the configured start, every later range begins
at the previous exclusive stop, and a range approaching `2**32` is shortened.
Each actual chunk invokes `search_nonce_range` exactly once. Preparation occurs
exactly once per effective variant. Integer hash and elapsed-nanosecond totals
span all jobs, times, extra nonces, and chunks, and weighted rate is derived
from those totals rather than averaged per-chunk rates.

After an exhausted chunk, a stop request and optional chunk limit are checked
before any further poll or search. When continuing, nonblocking polls drain all
immediately available notifications in order. Difficulty changes affect only
jobs announced later. Each job snapshots the difficulty at its arrival
position. Only the final newest job in one drain is prepared and searched;
superseded intermediate jobs remain observed but do not count as used work or
replacement transitions. Both `clean_jobs` values switch work under the
documented freshness policy. A replacement abandons the old local progression
cursor, starts from the same current-session seed and the new pool job's network
time, restarts at the configured nonce, and preserves all session counters.

`StopToken` is a read-only cooperative boundary. The CLI maps the first SIGINT
or SIGTERM to an idempotent user stop and restores every previous handler during
cleanup; repeated signals have no additional effect. The same controller
observes the optional monotonic deadline without a timer thread. A user signal
finishes as `stopped_by_user`; deadline expiry finishes as
`runtime_limit_reached`. Both return success and emit one `command_completed`.
A stop prevents another range, progression search, reconnect attempt, or
replacement wait. A running compute call is not interrupted mid-range. If that
call returns a candidate, the exact candidate is still submitted once before
termination. CUDA and native-parallel responsiveness is therefore bounded by
the current parent-range duration; unsafe kernel or worker cancellation is not
introduced.

## Opt-In Session and Work Liveness

Continuous mining keeps three separate monotonic observations:

- server activity, refreshed by every supported complete difficulty or job
  notification;
- active-job age, refreshed only by `mining.notify`; and
- work activity, refreshed when one backend range completes.

`--max-server-silence-seconds` and `--max-job-age-seconds` are optional positive
finite decimal limits through 31,536,000 seconds and are disabled by default.
Range completion never refreshes server activity, and difficulty traffic never
refreshes job age. Job age alone is not universal proof of obsolete work: some
compliant servers may legitimately keep one job active for a long interval, so
only an explicit operator policy may enforce it.

At a configured threshold, the just-completed range remains in aggregate hash
accounting but no candidate from that stale session is submitted. The current
client closes and `StratumSessionRecovery` performs its existing bounded delay,
subscribe, authorize, difficulty, and usable-job sequence. The fresh session
owns fresh subscription and extra-nonce state; old session state is never
reused. Runtime expiry or a signal during delay prevents another client or
range. Recovery success is not a command failure; exhaustion retains the
existing terminal recovery failure semantics.

The server-silence policy also applies after authorization while recovery waits
for the first usable difficulty/job pair. A silent initial client is closed and
retried through the same `session_work` recovery stage; no hashing has begun and
job-age policy starts only after usable work exists.

The transport enables `SO_KEEPALIVE` with OS defaults when supported. It does
not tune Linux, macOS, or Windows intervals and performs no privileged or global
configuration. Keepalive may identify some dead peers but cannot detect an
application that accepts TCP while withholding fresh work. Automatic suspend
detection is deferred: Python monotonic clocks do not provide one portable
suspend contract across supported systems, and comparing wall time can mistake
ordinary scheduling delay or clock adjustment for sleep.

## Deterministic Work-Space Expansion

The continuous hierarchy is:

```text
pool job -> effective network time -> extra_nonce_2 -> nonce range
```

The recovery owner generates exactly one random lowercase `extra_nonce_2` of
the negotiated width after each successful session authorization.
`MiningWorkCursor` treats it as a numeric starting offset and advances by one
modulo `2**(8 * extra_nonce_2_size)`. It records how many variants have been
searched at the current time, so every value—including zero after wrap—is
visited exactly once without allocating a history set. The starting value is
not repeated until the cycle is declared complete, and no additional random
value is generated within that session.

Each successor keeps fixed-width lowercase hexadecimal representation,
prepares a new coinbase, Merkle root, targets, and 76-byte header prefix once,
and restarts the nonce at the configured start. No random value is generated
after session initialization. Once the complete negotiated extra-nonce
cycle has been searched at one time, the cursor increments network time by
exactly one second and resets the extra nonce to the current-session seed.
Locally rolled time is exactly eight lowercase hexadecimal characters. It
never wraps beyond `ffffffff`.

Ordering at an exhausted nonce boundary is deliberate: finish the chunk,
honor stop and chunk-limit boundaries, emit exhaustion, drain every immediately
available notification, select the final newest valid pool job when present,
and only otherwise advance local work. Pool work therefore wins over local
progression. A new pool job resets local time to the pool-provided value and
uses the same current-session seed; local rolled time from an older job is
never carried forward.

Duplicate prevention uses compact validated identities rather than an
unbounded nonce ledger. Pool context identity covers all job construction data
and snapshotted difficulty but ignores `clean_jobs` alone. Effective work
identity covers job ID, the prepared 76-byte prefix, network target, and share
target. An identical reannouncement is observed but does not restart the
configured nonce range. A header-identical announcement with a genuinely
changed share target is a new acceptance context and is prepared and searched.
Arithmetic cursor state prevents duplicate local extra-nonce/time variants and
nonce wrap.

Only after the final extra-nonce cycle at network time `ffffffff` does local
progression become unavailable. The lifecycle then enters bounded 0.25-second
waits, continues applying difficulty updates, ignores repeated work, and
resumes only with genuinely new pool work. Stop requests remain responsive,
and optional chunk limits count actual searches across variants rather than
waits or preparations.

The first share-target or network-target match ends searching. No notification
is polled between discovery and submission. Exact prepared-work job ID, extra
nonce, network time, and matched nonce are submitted at most once. Pool
acceptance and rejection are both terminal completed outcomes; submission
failure is not retried.

Controlled outcomes are `stopped_by_user`, `chunk_limit_reached`,
`share_accepted`, and `share_rejected`. The CLI returns zero for each, two for
syntax or opt-in failure, and one for runtime, recovery exhaustion, or cleanup
failure. Python, native, native-parallel, and explicitly built CUDA backends
are available; sequential and experimental orbiting-bit strategies remain
independent of those backends. CUDA hardware parity passed on a CUDA 13.0
NVIDIA GB10 `sm_121` build. Offline CUDA tuning retains this exact range and
Python-verification boundary while using prepared midstates and backend-owned
device resources. Multi-GPU execution, Windows CUDA packaging, portable CUDA
wheels, and pool failover remain deferred. Controlled CKPool CUDA, endurance,
and liveness runs completed without submission or command failure before
tuning; their local rates are not post-tuning or general performance claims.

The live command orchestration may emit explicitly sanitized structured events
through the observability boundary. Networking and mining-domain modules do not
write log files directly. The event schema, safe-field policy, persistence
lifecycle, and read-only analysis behavior are documented in
[`04-observability.md`](04-observability.md).

## Single-Endpoint Session Recovery

`ReconnectPolicy` is an immutable, validated policy. Its production default is
five attempts after the failed active connection or failed initial attempt.
One-based attempts use deterministic exponential delays of 1, 2, 4, 8, and 16
seconds; larger configured sequences are capped at 30 seconds. The CLI accepts
`--max-reconnect-attempts` from 0 through 100, where zero disables retry.
There is no jitter and every fresh client still targets only the configured
host and port.

The recoverable class is deliberately narrow: only `StratumConnectionError`
from connect, handshake transport, initial work waiting, nonblocking
between-chunk notification polling, or terminal replacement waiting enters
recovery. A normal receive timeout is not a failure. Invalid configuration,
malformed or unsupported messages, response-ID errors, authorization
rejection, mining and progression invariants, event-log failures, and arbitrary
programming errors are terminal.

Connection loss follows this state flow:

```text
connection unavailable
    -> failed client closed best-effort
    -> interruptible deterministic backoff
    -> fresh client and request-ID lifecycle
    -> subscribe and authorize
    -> fresh assembler and one new negotiated-width seed
    -> wait for fresh difficulty
    -> wait for a later usable mining.notify
    -> install the new session and resume
```

No queued notification, old difficulty, assembler, prepared work, progression
cursor, rolled time, or request ID survives the disconnected session. Jobs
arriving before the new session's first difficulty remain unusable. After a
difficulty is known, the normal arrival-order rules apply and the newest job
from the immediate drain wins. Both `clean_jobs` values continue to switch
work under the freshness policy.

One new random seed is generated for every successfully authorized session,
using that session's `extra_nonce_2_size`; failed connection and authorization
attempts do not generate one. A fresh session establishes a new Stratum
acceptance context. Consequently, its first usable work may be searched even
if header and target values happen to be identical to the disconnected
session. This is explicit because submission belongs to the newly authorized
session, not the stale client context.

Continuous orchestration installs a new cursor at the configured start nonce
and discards all session-local progression. It preserves invocation-wide
chunks consumed, hashes checked, mining elapsed nanoseconds, candidates,
submissions, reconnect attempts, successful reconnects, failed reconnect
attempts, and the optional `max_chunks` budget. New-session work counts as a
replacement only when prior work was actually searched; unseen work is not
reported as used or replaced.

Backoff checks the shared stop token in bounded sleep quanta. A stop before an
attempt, during its delay, after a failed attempt, or after authorization but
before usable work prevents another client or search and produces the normal
`stopped_by_user` outcome. No receiver thread, asyncio task, or busy loop is
introduced. Signal installation and restoration remain CLI responsibilities.

Candidate discovery proceeds directly to at most one submission through the
current session. Recovery never runs between discovery and submission. A
`False` pool response remains a terminal normal rejection, and any transport
failure during `mining.submit` is terminal because the pool may already have
received the request. The uncertain request is never resent.

After every permitted connection attempt fails,
`SessionRecoveryExhaustedError` records only the safe attempt count, recovery
stage, and controlled error category. The CLI returns status 1 without raw
exception text. Pool failover, random jitter, and durable recovery state remain
deferred.

## Search Strategy and Parent Assignments

Search strategy and compute backend are independent selections. The strategy
chooses the next parent half-open range for one effective prepared-work variant;
the backend hashes exactly that range. `native-parallel` may subdivide it among
workers internally, while `cuda` may map it across GPU lanes. Worker count,
device ordinal, private worker assignments, and kernel geometry are not
strategy inputs.

The built-in `sequential` strategy reproduces the established ascending,
contiguous range order. Experimental `orbiting-bit` reverses fixed-width
permutation-counter bits to select physical parent-range indexes, skipping
indexes outside non-power-of-two domains. Those skips create no backend call,
chunk, range event, or hash accounting. Each emitted assignment is still one
ordinary contiguous half-open range, and complete coverage has no repetition.

One immutable strategy definition is selected before networking and reused
across the invocation, including reconnects. Each pool job, extra-nonce value,
rolled network time, or recovered session creates a fresh compact cursor
beginning at permutation counter zero. Difficulty-only updates and duplicate
pool work do not reset it. Pool notifications retain priority before the next
strategy assignment is requested.

One-shot mining preserves its explicitly requested range. Bounded chunked and
continuous mining use strategy assignments, and any strategy invariant failure
is terminal without reconnect or compute fallback. The strategy layer owns no
hashing, work construction, progression, networking, submission, worker pool,
or cleanup resource. The complete strategy contract is in
[`08-search-strategies.md`](08-search-strategies.md), with the exact
bit-reversal algorithm and coverage proof in
[`09-orbiting-bit.md`](09-orbiting-bit.md).

## Compute Backend and Compute Profile

Compute backend and compute profile remain separate settings. The backend
selects how one already-prepared half-open nonce range is executed. The profile
will eventually control resource policy such as worker count, duty cycle, and
scheduling priority; profile behavior is still deferred and is not silently
interpreted as a backend selector.

`HASHPHERE_COMPUTE_WORKERS` currently provides one explicit strict worker count
only to `native-parallel`; it does not implement a profile or alter either
sequential backend.

`HASHPHERE_CUDA_DEVICE` supplies one strict device ordinal only when `cuda` is
explicitly selected. It defaults to zero, does not affect CPU backends, and
does not implement automatic or multi-GPU selection.

The built-in `python` backend delegates to validated `search_nonce_range` and
remains the correctness oracle. The optional built-in `native` backend performs
the same sequential search in portable C and verifies candidates again through
Python. The default `auto` selector deliberately remains `python`; the earlier
`cpu` setting remains its compatibility alias. Explicit `native` selection is
available only when the extension imports successfully.

The optional `native-parallel` backend partitions the same parent range into
exact nonoverlapping worker assignments and invokes the verified native wrapper
for each. It reports actual aggregate hashes and parent-call wall-clock time,
selects the lowest qualifying nonce independent of completion order, and owns
only its reusable executor lifecycle. It is explicit, requires the native
extension, and never changes Stratum recovery or submission semantics.

The explicitly built `cuda` backend receives the same parent bounds, evaluates
the complete range with a deterministic grid-stride mapping, reduces all
qualifying results to the smallest nonce, and reports the full range size as
its hash count. Its wrapper reconstructs and rehashes every reported candidate
with the Python correctness primitives and independently checks both target
flags before returning the shared result. CUDA selection initializes exactly
one configured device before networking; execution and verification failures
are terminal without CPU fallback or Stratum reconnect.

CLI mining orchestration selects one backend before opening the live Stratum
connection and reuses that same instance for every range, job replacement,
extra-nonce or network-time variant, and recovered Stratum session. The backend
receives immutable `PreparedMiningWork` and exact inclusive-start,
exclusive-stop bounds, then returns the existing immutable
`NonceSearchResult`. It does not own work construction, progression, recovery,
submission, signals, console output, or event files. Execution failures are
terminal and do not trigger another search, fallback, or Stratum reconnect.

The capability declaration is immutable and low-cardinality. Python and native
report sequential execution; native-parallel reports parallel execution and a
safe worker count; CUDA reports GPU kind, parallel execution, explicit device
selection, and a safe ordinal. All report deterministic result order and no
cooperative mid-range cancellation or preferred batch size. SIMD, additional
strategies, automatic device probing, multi-GPU execution, tuning, and
Lite/Auto/Max/Custom resource profiles remain deferred. Detailed compute
contracts are documented in
[`05-compute-backends.md`](05-compute-backends.md) and
[`06-native-cpu.md`](06-native-cpu.md), with parallel design in
[`07-parallel-cpu.md`](07-parallel-cpu.md) and CUDA design in
[`10-cuda-backend.md`](10-cuda-backend.md).

## Mining and Compute Components

```text
src/hashphere/
├── compute/
│   ├── backend.py
│   ├── benchmark.py
│   ├── cuda.py
│   ├── native.py
│   ├── parallel.py
│   ├── python.py
│   └── registry.py
└── mining/
    ├── chunks.py
    ├── coinbase.py
    ├── continuous.py
    ├── header.py
    ├── job.py
    ├── merkle.py
    ├── progression.py
    ├── recovery.py
    ├── search.py
    ├── strategy.py
    └── target.py
```

The mining package owns Bitcoin work construction, targets, progression,
parent-range strategy, session recovery, and lifecycle orchestration. The
compute package owns only the execution boundary for a supplied prepared range,
stable capabilities, and deterministic backend selection. This separation
allows alternative search orders and CUDA execution without changing Stratum
or Bitcoin-domain semantics.

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
