# Bitcoin Core true-solo mining

## What

HashOrb has three structurally separate Bitcoin Core commands:

- `bitcoin-core-check` performs an explicitly enabled, read-only readiness
  check.
- `solo-hash` obtains real templates and runs production construction and
  compute, but receives no proposal or submission capability.
- `solo-mine` obtains a template, builds complete Bitcoin work, searches it
  through one existing compute backend and strategy, requires Core proposal
  validation, and submits a complete verified block at most once.

None uses CKPool, a Stratum setting, a Stratum socket, pool
difficulty, or a share target.

## Why

A Stratum server supplies coinbase fragments, a transaction merkle path, and a
share-submission protocol. Direct solo mining must own the complete consensus
serialization lifecycle and return a whole block to a user-controlled Bitcoin
Core node. Keeping these paths separate prevents pool session state from
becoming an accidental authority over direct block construction.

## Plain talk

The node describes the next valid block. HashOrb can inspect it, hash it with
the send button absent, or—through a separately armed command—ask the node to
validate and accept a winner.

## RPC trust and authentication

The endpoint defaults only to `127.0.0.1:8332`. The client resolves a configured
hostname once, requires every IPv4 or IPv6 result to be loopback, and connects
to that selected numeric address. It implements ordinary bounded HTTP and HTTP
Basic authentication, not TLS, so direct remote RPC is rejected. A node on
another host requires an operator-controlled local tunnel that presents a
loopback endpoint; do not weaken Core's authentication or RPC allowlist.

Configure exactly one authentication method:

```dotenv
HASHORB_BITCOIN_RPC_HOST=127.0.0.1
HASHORB_BITCOIN_RPC_PORT=8332
HASHORB_BITCOIN_RPC_TIMEOUT_SECONDS=10
HASHORB_BITCOIN_RPC_USER=YOUR_RPC_USER
HASHORB_BITCOIN_RPC_PASSWORD=YOUR_RPC_PASSWORD
```

or:

```dotenv
HASHORB_BITCOIN_RPC_COOKIE_FILE=EXPLICIT_COOKIE_PATH
```

Cookie paths are explicit on Linux, macOS, and Windows. HashOrb never
searches default data directories or reads another file. Cookie data must be a
small UTF-8 `username:password` record with only one optional final newline.
The file must be regular and not a symlink; POSIX files must be owned by the
effective user with no group or other permissions. Reads stop at the record
limit before parsing. Credentials never enter a URL, console result, event, or
raw exception.

## Chain and payout boundary

Startup obtains Core's exact `main`, `test`, `testnet4`, `signet`, or `regtest`
identity; safety is never inferred from the port. The template and payout
script come from the same authenticated RPC client. The required
`HASHORB_SOLO_PAYOUT_ADDRESS` has no default. Core's general non-wallet
`validateaddress` RPC validates it for the connected chain and returns the
exact `scriptPubKey`.

HashOrb does not create, load, modify, or import into a wallet. The address
need not belong to the node. Invalid cross-network destinations stop before
mining. The address and resulting script remain private.

## Template and transaction correctness

The immutable template model validates exact lower-case hash, target, bits,
time, height, money, limit, rule, mutation, auxiliary-flag, transaction, and
witness-commitment fields. Raw transactions remain in Core's order. An
independent bounded parser removes witness serialization to derive txid,
retains it to derive wtxid, computes BIP141 weight, rejects trailing or
noncanonical data, and verifies every identity Core supplied.

Core constructs each `depends` array per transaction input. Multiple inputs
that spend the same earlier template transaction therefore repeat its 1-based
index; repetition is valid metadata and remains preserved. `fee` and `sigops`
are unknown when absent under the GBT contract, so the model stores `None` in
that case and validates their exact integer bounds whenever Core provides them.
Transaction data, identities, weight, ordering, and every dependency range
remain strict.

The compact target is decoded with the existing Core-compatible sign and
overflow rules. When Core also supplies a numeric target, the two must agree.
Only `csv`, `segwit` (including Core's required `!segwit` marker), and `taproot`
templates are supported in this milestone; an unknown active rule stops
mining. Signet is identified but its required challenge construction is
explicitly unsupported and stops safely. SegWit is mandatory. The default
commitment must have the exact generated shape and must equal the independent
witness merkle root combined with the zero 32-byte coinbase reserved value.

The sanitized 16-character template identity includes every effective
construction field. Raw previous hashes, transaction identities, template
data, bits, and targets are never observable fields.

## Coinbase, merkle tree, and header

The coinbase has version 2, locktime zero, exactly one null outpoint input,
index and sequence `0xffffffff`, and a consensus-bounded script. The script
starts with Core's exact `CScript() << height` BIP34 serialization: heights 1
through 16 use `OP_1` through `OP_16`, while later heights use the minimally
encoded script number as a data push. It then contains Core's auxiliary flags,
the fixed neutral `/HashOrb/` marker, and a private 64-bit coinbase extra
nonce. The first output pays exactly `coinbasevalue` satoshis to Core's
validated script. The second is the zero-valued witness commitment. There is
no other spendable output and no fee selection.

The ordinary block merkle tree starts with the newly derived coinbase txid,
then exact template txids. It hashes internal-order pairs with double SHA-256
and duplicates an odd final node at every level. The header is exactly 80
bytes: little-endian version, reversed Core display previous hash, internal
merkle root, little-endian time, bits, and nonce. Python serialization and
double SHA-256 remain the oracle for native and CUDA results.

The block is the verified header, canonical compact-size transaction count,
complete witness coinbase, and unchanged template transactions. Assembly
reparses the actual bytes, recomputes the header merkle field, and enforces
Core's size and weight limits.

## Progression, replacement, and bounded runtime

Sequential and orbiting-bit create the same parent ranges used by Stratum.
Python, native, native-parallel, CUDA, and cuda-multi receive the existing
76-byte prepared header boundary. Solo sets both legacy backend target slots to
the network target, but exposes no pool-share concept.

After the 32-bit nonce domain is exhausted, the private 64-bit coinbase extra
nonce advances, the coinbase/merkle/header rebuild, and a fresh strategy cursor
begins. Exact extra-nonce wrap may increment time only when `time` is mutable,
never beyond local wall time, and never more than `--max-time-roll-seconds`
(7,200 by default) beyond the template time. Otherwise a fresh template is
required. Each effective work identity is checked against prior session
variants so a duplicate is never searched.

Template polling defaults to 30 seconds and occurs only at range boundaries;
ordinary lack of change is not stale. Candidate discovery forces a fresh
template before construction. Proposal acceptance forces another refresh
before submission. A changed template, previous block, stop request, runtime
expiry, or invalid RPC session prevents stale submission. Long polling is
deferred to keep the first control path synchronous and stop-safe.

`solo-mine` must have `--max-chunks`, `--max-runtime-seconds`, or both. A
non-cancellable backend may finish its current finite parent range before a
signal or runtime deadline is observed. SIGINT and SIGTERM share the existing
cooperative stop model. Backend, RPC, signal, and event resources each close
once at the command boundary. There is no compute fallback or submission
retry.

## Proposal and submission semantics

`solo-hash` injects a stateless candidate policy with no RPC callables. The
command constructs an independent template-only client exposing chain state,
address validation, and template retrieval; it never constructs or retains the
submission-capable client. Proposal, `submitblock`, and generic arbitrary RPC
are rejected at that client's dispatch boundary. A candidate is independently reconstructed and
double-hashed, checked against the network target, refreshed against current
work, recorded as `candidate_found_submission_disabled`, and stopped. It is not
assembled into a complete block, written to disk, proposed, submitted, or
described as accepted. This mode cannot receive a mining reward because it
cannot submit.

Local verification is mandatory but not sufficient; Bitcoin Core remains the
final block-validity authority. The complete block is sent once to
`getblocktemplate` proposal mode. A null result is acceptance; an allowlisted
Core rejection token becomes a stable category such as `bad_coinbase_height`
or `bad_witness_commitment`. Control characters, whitespace abuse, oversized
values, structured content, and arbitrary text are never copied into output;
an unknown safe token becomes `other_proposal_rejection`. Every rejection
prevents submission. Proposal unavailability is fail-closed. After the second
freshness check, `submitblock` is called once. Only a null result means
accepted. Duplicate, invalid, inconclusive, other rejection, and transport
failure remain distinct sanitized terminal outcomes.

No mainnet block submission was performed during implementation or validation.
The mainnet path is the same tested builder but remains entirely operator
controlled through endpoint, chain, destination, two opt-ins, and finite run
limits.

## Events and log summaries

Solo events contain chain category, sanitized work/template identities,
backend/profile/strategy names, counts, elapsed time, and outcome categories.
They exclude credentials, paths, addresses, scripts, prior hashes, merkle
roots, headers, bits, targets, transaction material, coinbase material,
extra-nonce values, nonce values, candidate hashes, and raw blocks.

`logs-summary` classifies read-only checks, hash-only runs, submission-capable
runs, and Stratum runs separately. The Bitcoin Core aggregate always prints
proposal and submission call counts, including explicit zeroes. It never
labels candidates as shares, and old Stratum/profile logs retain their schema-1
interpretation. Unknown future events are preserved only as validated records
and do not invent aggregates.

Readiness records authenticated, chain-verified, synchronization-verified, and
template-RPC-reachable stages before parsing. A parser failure then carries
only an allowlisted category, field path, expected kind, and observed condition;
template values and arbitrary keys are never included. Intermediate stages do
not count as a successful check: only terminal outcome `ready` does.

## Package, platform, and validation status

The RPC client uses only the Python standard library and adds no package or
Bitcoin Core dependency. Normal import, help, doctor, CPU wheel/sdist, Docker
CPU, macOS, Windows, and Linux behavior remains offline. `bitcoind` and wallets
are not bundled. Docker can reach an explicitly operated host node only when
the operator provides networking that presents it on container loopback (for
example, an appropriate local tunnel or reviewed host-network arrangement); no
node orchestration is included.

Deterministic fakes cover authentication, transport, strict templates,
transaction parsing, SegWit, coinbase, merkle/header byte order, block
assembly, progression, replacement, stale candidate suppression, proposal,
submission, profiles, strategies, runtime, signals, events, summary, and
privacy. The opt-in integration test creates a new temporary regtest data
directory, binds only loopback, uses synthetic credentials and a synthetic
wallet-free address, submits one HashOrb-built block, checks height, stops
the process, and removes temporary state. It passed against Bitcoin Core
v31.1: proposal validation accepted, `submitblock` ran exactly once, and the
isolated chain advanced from height 0 to 1. No wallet or non-regtest node was
used.

The loopback cookie-authenticated read-only gate subsequently passed against a
fully synchronized Bitcoin Core v31.1 mainnet node. It parsed the live template,
validated the configured destination, resolved the Lite profile, and recorded
one completed readiness run with no hashes, candidates, proposals, submissions,
wallet actions, or Stratum commands.

The subsequent bounded mainnet `solo-hash` gate resolved Lite to device-0
CUDA, used sequential scheduling, and reached its 60-second runtime limit after
698 ranges and 69,719,476,736 hashes at 2.834 GH/s aggregate. It received two
templates, replaced work once, used 18 variants, advanced the coinbase extra
nonce 16 times, and rolled no timestamps. It completed once with zero
candidates, suppressions, proposals, submissions, Stratum activity, or
failures. The temporary sanitized log was summarized and removed.

Physical two-GPU validation, Windows CUDA, portable CUDA wheels, the dashboard,
distributed workers, long polling, and adaptive tuning remain deferred.

## Operator and validation commands

Read-only readiness (does not mine, propose, or submit):

```bash
HASHORB_ENABLE_BITCOIN_RPC_CHECK=1 \
uv run hashorb bitcoin-core-check
```

Bounded hash-only compute (cannot propose or submit):

```bash
HASHORB_ENABLE_TRUE_SOLO_HASHING=1 \
uv run hashorb solo-hash \
  --profile lite \
  --max-runtime-seconds 60 \
  --event-log logs/solo-hash.jsonl
```

Isolated regtest end-to-end gate (starts only an existing compatible binary):

```bash
HASHORB_ENABLE_REGTEST_TESTS=1 \
uv run pytest -q tests/test_bitcoin_regtest.py -rs
```

Short bounded mainnet-capable command (review endpoint and chain before use):

```bash
HASHORB_ENABLE_TRUE_SOLO=1 \
HASHORB_ENABLE_BLOCK_SUBMISSION=1 \
uv run hashorb solo-mine \
  --profile auto \
  --max-chunks 1 \
  --max-runtime-seconds 30
```

Sanitized log summary:

```bash
uv run hashorb logs-summary --log-file logs/solo.jsonl
```

Offline proof that malformed Stratum configuration is ignored by solo mining:

```bash
uv run pytest -q tests/test_bitcoin_cli.py \
  -k solo_uses_no_stratum_configuration
```
