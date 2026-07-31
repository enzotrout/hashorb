# Search Strategies

## Purpose

A search strategy decides which parent nonce interval HashOrb should search
next. A compute backend decides how to hash that already selected interval.
Keeping those policies separate lets future search orders reuse Python, native,
and native-parallel execution without moving Bitcoin, Stratum, progression, or
submission behavior into either layer.

The deterministic `sequential` reference strategy preserves the range order
used before the abstraction. Experimental `orbiting-bit` is the first genuine
alternative: it visits the same physical parent ranges through a fixed-width
bit-reversal permutation. Strided, random, probabilistic, and other alternative
orders are not implemented.

## Public Contract

The strategy package exports:

```python
class MiningSearchStrategy(Protocol): ...
class SearchStrategyCursor(Protocol): ...

@dataclass(frozen=True, slots=True)
class SearchStrategyCapabilities: ...

@dataclass(frozen=True, slots=True)
class SearchAssignment: ...

class SequentialSearchStrategy: ...
class OrbitingBitSearchStrategy: ...
class SearchStrategyRegistry: ...

def reverse_bits(value: int, bit_width: int) -> int: ...
def next_power_of_two(value: int) -> int: ...
def calculate_orbiting_range_count(...) -> int: ...
def builtin_search_strategy_registry() -> SearchStrategyRegistry: ...
def select_search_strategy(name: str) -> MiningSearchStrategy: ...
def list_search_strategies() -> tuple[SearchStrategyCapabilities, ...]: ...
def validate_search_strategy_compatibility(
    strategy: MiningSearchStrategy,
    backend_capabilities: SearchBackendCapabilities,
) -> None: ...
```

`SearchAssignment` contains a zero-based assignment index and exact
inclusive-start, exclusive-stop nonce bounds. Capabilities contain only stable,
low-cardinality identity, behavior, availability, and compatibility facts.
They never include cursor positions, prepared work, work identities, headers,
extra nonces, candidate data, credentials, or hardware details.

## Sequential Reference Strategy

For configured `start_nonce` and positive `chunk_size`, one sequential cursor
emits ascending contiguous parent ranges. A normal assignment contains at most
`chunk_size` nonces. The final assignment is shortened at its finite nonce
limit, which can be the Bitcoin boundary `2**32` or a smaller bounded-command
budget. No assignment is empty or outside the permitted range.

For example, a start of zero and chunk size 100000 emits:

```text
[0, 100000)
[100000, 200000)
[200000, 300000)
...
```

A start of `4294967295` with the full nonce limit emits exactly:

```text
[4294967295, 4294967296)
```

and then reports exhaustion. Assignment indexes begin at zero and increase by
one. The cursor never wraps, moves backward, skips, overlaps, or repeats a
nonce or completed assignment. It uses numeric cursor state rather than an
unbounded history set. Its capabilities declare deterministic, contiguous,
exhaustive, nonrepeating operation and compatibility with parallel backends.

## Orbiting-Bit Strategy

For one cursor domain, let `N` be the exclusive nonce limit, normally `2**32`:

```text
range_count = ceil((N - start_nonce) / chunk_size)
permutation_size = next_power_of_two(range_count)
bit_width = log2(permutation_size)
```

All calculations use exact integer arithmetic. For permutation counters from
zero through `permutation_size - 1`, the cursor reverses exactly `bit_width`
low bits. A reversed value is a physical range index. Values greater than or
equal to `range_count` are skipped; each remaining value maps to:

```text
start = start_nonce + physical_range_index * chunk_size
stop = min(start + chunk_size, N)
```

The public `SearchAssignment.assignment_index` is the zero-based emission
sequence, not the physical range index. The physical index is used only to
derive bounds. Every emitted assignment itself remains contiguous; the
sequence of assignments is globally noncontiguous.

For eight ranges, the three-bit physical order is:

```text
counter:        0 1 2 3 4 5 6 7
physical index: 0 4 2 6 1 5 3 7
```

When `range_count` is five, the enclosing size is eight. The raw reversed
sequence is `0, 4, 2, 6, 1, 5, 3, 7`; indexes `6`, `5`, and `7` are invalid and
are skipped, producing `0, 4, 2, 1, 3`. A skip is not an assignment, backend
call, chunk, nonce-range event, or accounting entry. Since the enclosing power
of two is less than twice any non-power-of-two positive range count, internal
iteration remains finite and bounded.

The one-range special case has a zero-bit permutation. Counter zero reverses to
physical index zero, that range is emitted once, and the cursor is exhausted.

Bit reversal is a bijection over the enclosing power-of-two domain, so no
physical index appears twice. Filtering only indexes outside `range_count`
leaves every valid physical index exactly once. The physical ranges partition
`[start_nonce, N)`, including a shortened final range, proving complete
coverage with no gap, overlap, repeated nonce, wrap, or unbounded history.

Cursor state is bounded to numeric configuration, range and permutation sizes,
bit width, permutation counter, emission count, and next assignment index. It
retains no visited set, prepared work, header, extra nonce, job, credential, or
candidate data. Detailed plain-language diagrams and probability limitations
are in [`09-orbiting-bit.md`](09-orbiting-bit.md).

## Cursor and Reset Semantics

One selected strategy definition lives for the complete mining command,
including all chunks, work changes, and recovered Stratum sessions. A cursor is
narrower: it is scoped to exactly one effective prepared-work variant and does
not retain that work.

A fresh cursor begins at the configured start nonce for each legitimate new:

- selected pool job;
- `extra_nonce_2` value;
- locally rolled network time; or
- recovered Stratum session's first effective work.

Difficulty changes affect later job snapshots but do not reset the current
cursor. An identical pool reannouncement also does not reset it. An exhausted
cursor can never emit another assignment; orchestration must first select or
derive a genuinely new effective work variant and create a fresh cursor.

## Work Progression and Pool Priority

After an exhausted backend call, continuous orchestration records the result,
handles any candidate, checks cooperative stop, and drains immediately queued
pool notifications before asking the cursor for another assignment. New pool
work therefore supersedes both the old cursor and local extra-nonce or network-
time progression.

If the current cursor still has space, its next assignment is searched. If it
is exhausted, deterministic work progression advances the extra nonce or
network time, prepares the new variant once, and creates a fresh cursor.
Strategy state never chooses or stores pool jobs, difficulty, extra nonces,
network time, prepared headers, targets, or submission metadata.

One-shot mining deliberately keeps its explicit user-requested range and only
validates and reports the selected strategy. Bounded chunked and continuous
mining obtain their repeated parent ranges from strategy cursors. Their prior
budgets, stop boundaries, notification ordering, cumulative accounting, and
submission rules are unchanged.

## Strategy and Compute Backend

The boundary is:

```text
selected strategy
    -> parent range [start_nonce, stop_nonce)
    -> selected compute backend
    -> one NonceSearchResult
```

All current backends support `sequential` and `orbiting-bit`. Python and native
search each parent range sequentially. `native-parallel` privately divides that
parent range into balanced, contiguous, nonoverlapping worker assignments.
`cuda` applies its private grid-stride mapping to the same exact parent bounds
and returns a Python-verified smallest candidate. The strategy does not know
the worker count, CUDA device, executor, kernel mapping, private assignments,
candidate reduction, or backend hash and timing accounting.

Compatibility is validated after backend and strategy selection but before a
live client is constructed. This explicit boundary allows a future strategy to
reject an unsupported backend without opening a network connection. There is
no automatic strategy or compute fallback.

## Registry and Configuration

`HASHORB_SEARCH_STRATEGY` defaults to `sequential`. Exact built-in names are
`sequential` and `orbiting-bit`; `auto` is a deterministic alias for
`sequential` and does not benchmark, probe hardware, or adapt at runtime.
Unknown selectors fail as configuration errors before networking.

The built-in registry snapshots its definitions into isolated instance state,
rejects duplicate names, lists capabilities in sorted name order, performs
exact-name selection, and rejects unknown or unavailable entries. It performs
no plugin discovery, entry-point loading, dynamic imports, or hardware probing.
`HASHORB_COMPUTE_BACKEND`, `HASHORB_COMPUTE_WORKERS`, and
`HASHORB_COMPUTE_PROFILE` retain their separate meanings.

## Duplicate Prevention

The sequential cursor's monotonic next position and assignment index prove that
one cursor cannot issue a parent assignment twice or move backward. Contiguous
bounds prevent gaps and overlap without retaining every prior assignment.
Orbiting-bit instead relies on the fixed-width reversal bijection, a monotonic
permutation counter, and an emitted count. Invalid enclosing-domain indexes are
discarded permanently when processed, and exhaustion is final after the finite
domain is consumed.
Existing work-context and prepared-work identities independently prevent an
identical pool notification or local variant from restarting at the configured
nonce. Those checks remain orchestration and progression responsibilities.

## Failure and Resource Model

Malformed cursor inputs, assignments, capabilities, and registry definitions
raise focused validation errors. Unknown or unavailable strategy selection is
a configuration failure and returns CLI status 2 before network access.
Compatibility failure has the same boundary.

A strategy invariant or execution failure after mining begins is terminal and
returns runtime status 1. It is not range exhaustion, is not recoverable as a
Stratum connection loss, and triggers no reconnect, compute fallback, strategy
fallback, or duplicate search. Raw implementation exceptions are not printed
or logged.

Strategy definitions and cursors own no sockets, threads, processes, executor,
file handle, background task, or cleanup operation. Existing client, backend,
event-sink, and signal owners keep their established cleanup responsibilities.

## Observability and Privacy

Mining commands print the stable selected strategy name and emit one
`search_strategy_selected` event per invocation. Its safe fields are strategy
name, implementation, deterministic flag, contiguous-parent-ranges flag,
exhaustive flag, and experimental flag. The read-only log summary aggregates
selection counts by stable strategy name.

Existing `nonce_range_started` and `nonce_range_completed` events remain the
authoritative per-parent-assignment records. There is no event for each cursor
decision, skipped permutation index, or internal parallel worker assignment.
Cursor state, assignment
history, job IDs, headers, extra nonce values, credentials, payout addresses,
protocol data, raw exceptions, and candidate digests are excluded.

## Probability and Deferred Strategies

Orbiting-bit changes ordering only. It does not change SHA-256, create search
space, make an individual nonce more likely to succeed, increase the success
probability for a fixed number of unique hashes, or predict a valid nonce.
There is no claim that it is statistically superior to sequential search. Its
capability metadata therefore marks it experimental, not advantageous.

Future random, strided, probabilistic, or other strategies must preserve
explicit finite assignments, truthful capabilities, duplicate prevention, stop
and pool-notification priority, exact accounting, controlled failure, and
compatibility validation.

The optional CUDA backend remains an execution implementation: it hashes one
strategy-supplied parent assignment and verifies a candidate through the shared
result contract. GPU device selection, grid mapping, host-side verification,
and cleanup stay backend concerns. CUDA execution does not change sequential or
orbiting assignment order. `cuda-multi` preserves the same split by privately
partitioning one parent range. The strategy remains unaware of CUDA, devices,
worker topology, Stratum, or share submission.
