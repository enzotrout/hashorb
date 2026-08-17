# Search Strategies

## What

A HashOrb search strategy decides which ordinary parent nonce range should be searched next. It does not perform SHA-256 itself and it does not own Bitcoin, Stratum, recovery, or submission behavior.

Current built-in strategies are:

- `sequential`
- `orbiting-bit`
- `fibonacci-bounce`
- `auto`, which currently aliases the sequential reference order

## Why

Separating **where to search next** from **how to hash that range** lets the same strategy work with Python, native CPU, parallel CPU, CUDA, or explicitly configured multi-CUDA execution.

It also makes experimental search orders testable without giving them authority over networking or Bitcoin correctness.

## Plain Talk

Imagine the nonce space as a map divided into equal regions. A strategy chooses which region to visit next. The backend is the vehicle used to search that region.

Sequential drives through the regions in order. Orbiting Bit jumps across the map in a deterministic bit-reversal pattern. Fibonacci Bounce follows a deterministic Fibonacci-derived permutation. All of them still ask the backend to search normal contiguous ranges.

None claims to make a particular Bitcoin hash more likely to exist.

## Strategy and Backend Are Independent

A strategy assignment contains an exact inclusive start and exclusive stop for one parent range. The selected compute backend searches only that supplied range.

Conceptually:

```text
prepared work
     ↓
search strategy
     ↓
[start_nonce, stop_nonce)
     ↓
compute backend
     ↓
verified search result
```

The backend does not select the next global range. The strategy does not hash individual nonces or submit a share.

## Sequential

`sequential` is the reference order. It walks the configured parent ranges from low nonce to high nonce.

Example with eight equal ranges:

```text
0 → 1 → 2 → 3 → 4 → 5 → 6 → 7
```

Sequential is useful as the simplest comparison point for correctness, logging, performance, and experimental strategies.

## Orbiting Bit

`orbiting-bit` visits the same parent ranges using a fixed-width bit-reversal permutation.

For eight ranges:

```text
0 → 4 → 2 → 6 → 1 → 5 → 3 → 7
```

It is deterministic, exhaustive for the configured range set, and does not change the hash function or validity rules.

See [Orbiting Bit](09-orbiting-bit.md).

## Fibonacci Bounce

`fibonacci-bounce` uses a Fibonacci-derived stride that is coprime with the number of parent ranges, combined with a deterministic bounce order. The resulting ordering is designed to be exhaustive and duplicate-free.

It is the modern range-order version of an earlier Fibonacci nonce experiment; it does not directly generate individual nonce values and does not use a random or clock seed.

See [Fibonacci Bounce](17-fibonacci-bounce.md).

## Selection

Use the environment setting:

```dotenv
HASHORB_SEARCH_STRATEGY=sequential
```

Alternative examples:

```dotenv
HASHORB_SEARCH_STRATEGY=orbiting-bit
```

```dotenv
HASHORB_SEARCH_STRATEGY=fibonacci-bounce
```

Search strategy is intentionally independent of performance profile. Lite, Auto, Max, and Custom control compute intensity and backend-related settings; they do not redefine the strategy algorithm.

## Correctness Properties

A production-usable strategy should preserve these boundaries:

- deterministic behavior for the same configured work and cursor state
- exact bounded parent-range assignments
- no out-of-domain nonce ranges
- no duplicate parent range before the strategy's intended cycle completes
- no gaps when the strategy claims exhaustive coverage
- a fresh effective cursor when work identity changes as required by mining progression
- no ownership of hashing, sockets, credentials, target math, or submission

The compute backend still independently validates its supplied range and any candidate it reports.

## Probability

Changing the order of unique nonce searches does not change the probability attached to each individual SHA-256 attempt. If two strategies examine the same number of unique valid hashes, HashOrb does not claim that Orbiting Bit or Fibonacci Bounce has a mathematical advantage over sequential search.

The strategies exist to explore deterministic search-space traversal, architecture, visualization, scheduling, and future distributed-work ideas.

## Related Documentation

- [Compute Backends](05-compute-backends.md)
- [Orbiting Bit](09-orbiting-bit.md)
- [Fibonacci Bounce](17-fibonacci-bounce.md)
- [Performance Profiles](12-performance-profiles.md)
- [Stratum and Compute Design](03-stratum-and-compute-design.md)