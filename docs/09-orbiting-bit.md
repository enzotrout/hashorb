# Orbiting-Bit Search Order

## What

Orbiting-bit is an experimental deterministic search strategy. It divides the
available nonce domain into the same ordinary parent ranges as sequential
search, then visits those range indexes in fixed-width bit-reversal order.

## Why

The strategy proves HashOrb can change global search order without changing
Bitcoin correctness, hashing, compute backends, native worker partitioning,
Stratum, progression, recovery, or submission. It is a clean architecture and
testing milestone, not a probability optimization.

## Plain Talk

Sequential search walks a map from left to right. Orbiting-bit visits the first
region, then a far-away region, then one between them, continuing until every
region has been visited once.

For eight equal regions:

```text
Physical regions

0---1---2---3---4---5---6---7

Sequential visits

0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7

Orbiting-bit visits

0 -> 4 -> 2 -> 6 -> 1 -> 5 -> 3 -> 7
```

The strategy does not search individual nonces in a new way. It chooses the
next contiguous parent range; the selected compute backend searches that range
normally.

## Exact Algorithm

For configured `start_nonce`, positive `chunk_size`, and exclusive nonce limit
`N` (normally `2**32`):

```text
range_count = ceil((N - start_nonce) / chunk_size)
permutation_size = smallest power of two >= range_count
bit_width = log2(permutation_size)
permutation_counter = 0
emission_index = 0
```

For every counter in the finite permutation domain:

1. Reverse exactly the lowest `bit_width` bits of `permutation_counter`.
2. Treat the result as `physical_range_index`.
3. Advance `permutation_counter` once.
4. If `physical_range_index >= range_count`, skip it internally.
5. Otherwise calculate:

   ```text
   start = start_nonce + physical_range_index * chunk_size
   stop = min(start + chunk_size, N)
   ```

6. Emit `SearchAssignment(emission_index, start, stop)`.
7. Advance `emission_index` once.

Exhaustion is final after the complete enclosing permutation domain is
processed and all `range_count` valid parent ranges are emitted.

No floating-point arithmetic is used. Power-of-two size and bit width come from
integer bit-length operations, and reversal uses integer shifts and masks.

## Index Terminology

The three relevant indexes are deliberately distinct:

- `permutation_counter`: the next bounded input to bit reversal;
- `physical_range_index`: the reversed value that chooses range bounds;
- `SearchAssignment.assignment_index`: the zero-based order of actual emitted
  backend calls.

Skipped physical indexes never receive an assignment index. They do not count
as mining chunks.

## Non-Power-of-Two Example

Five physical ranges require an enclosing permutation size of eight and a
three-bit reversal:

| Counter | Reversed index | Result |
|---:|---:|---|
| 0 | 0 | emit range 0 |
| 1 | 4 | emit range 4 |
| 2 | 2 | emit range 2 |
| 3 | 6 | skip |
| 4 | 1 | emit range 1 |
| 5 | 5 | skip |
| 6 | 3 | emit range 3 |
| 7 | 7 | skip |

The actual emission order is therefore `0, 4, 2, 1, 3`. Exactly five backend
calls and five range-start/range-completion event pairs occur. The three skips
consume no chunk limit, hash count, elapsed mining time, or event record.

## Complete-Coverage and No-Repeat Proof

Reversing a fixed number of bits is its own inverse: reversing the result again
returns the original counter. It is therefore a bijection over every integer in
`[0, permutation_size)`. No reversed physical index can appear twice, and none
is absent from that enclosing domain.

Filtering indexes greater than or equal to `range_count` removes only indexes
that have no physical parent range. Every valid index from zero through
`range_count - 1` remains exactly once. Mapping those indexes through fixed
`chunk_size` boundaries partitions `[start_nonce, N)` without gaps or overlap;
the final physical range is shortened when necessary. The cursor never wraps.

## Complexity and Bounded Memory

The permutation size is the smallest enclosing power of two. For non-power-of-
two positive range counts it is less than twice `range_count`, so internal
skipping terminates. Each counter uses at most 32 integer bit steps. Cursor
memory is constant: it stores only numeric configuration and progress counters,
not visited indexes, searched nonces, assignments, or prepared work.

## Lifecycle Resets and Pool Priority

One `OrbitingBitSearchStrategy` definition is selected per mining invocation
and survives work progression and reconnect. A fresh cursor with permutation
counter zero is created for each legitimate new:

- pool job;
- `extra_nonce_2` value;
- rolled network time; or
- recovered Stratum session.

Difficulty-only notifications and duplicate pool work do not reset the current
cursor. After a completed range, candidate handling and cooperative stop happen
first, then queued pool notifications. New pool work replaces the cursor before
another orbiting assignment or local work progression. A found candidate is
submitted through the existing terminal path without another poll or range.

## Native-Parallel Interaction

```text
orbiting-bit cursor
    -> one contiguous parent range
    -> native-parallel backend
    -> private balanced worker assignments
    -> one verified NonceSearchResult
```

The strategy does not know backend type, worker count, CUDA device, executor
state, internal assignments, kernel mapping, hashing implementation, candidate
verification, or timing. Worker count and CUDA launch geometry do not alter the
bit-reversal parent order. Python, native, native-parallel, and cuda are all
compatible with orbiting-bit.

## Probability Limitations

Orbiting-bit changes only the order of unique nonce hashes. It does not:

- change double-SHA256 or target comparison;
- make an individual nonce more likely to qualify;
- increase success probability for a fixed number of unique hashes;
- add nonce space;
- predict where a qualifying hash occurs; or
- establish a statistical advantage over sequential order.

The strategy is experimental because it is a new scheduling policy, not because
it provides better Bitcoin odds.

## Testing Policy

Deterministic tests compare every complete domain from one through 64 physical
ranges against an independently calculated reference. Additional tests cover
zero- through 32-bit reversal, exact power-of-two and non-power-of-two domains,
the one-range case, the full 32-bit-scale domain without history allocation,
shortened final ranges, lifecycle resets, pool priority, recovery, candidates,
native-parallel ownership, configuration, events, and log summaries.

Tests use synthetic work, fakes, bounded tiny ranges, and temporary files. They
open no live socket, perform no live Stratum command, and record no real work or
credential material.

## Observability and Privacy

One `search_strategy_selected` event reports stable capabilities:

```text
strategy_name=orbiting-bit
implementation=bit-reversal
deterministic=true
contiguous_parent_ranges=false
exhaustive=true
experimental=true
```

The contiguity flag describes global range order; each emitted parent range is
still contiguous. Existing nonce-range events describe actual backend calls.
There is no per-skip event and no logging of permutation counters, physical
indexes, cursor history, jobs, headers, extra nonces, credentials, protocol
messages, candidates, or worker assignments.

## CUDA and Future Distributed Execution

The optional CUDA correctness backend hashes any ordinary parent range supplied
by either strategy without understanding bit reversal. Its device scheduling,
deterministic smallest-candidate reduction, Python verification, and cleanup
remain backend responsibilities. Orbiting order is unchanged. `cuda-multi`
partitions only the supplied parent range without moving device, worker, or
submission state into the orbiting-bit cursor. Future distributed coordination
must preserve the same boundary.
