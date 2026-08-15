# Fibonacci Bounce Search Strategy

## Purpose

`fibonacci-bounce` is an experimental HashOrb parent-range ordering strategy.
It changes only which ordinary nonce range is searched next. It does not alter
SHA-256, Bitcoin work construction, Stratum, target math, compute backends,
recovery, work progression, or share submission.

Select it with:

```bash
HASHORB_SEARCH_STRATEGY=fibonacci-bounce
```

The strategy is deterministic, exhaustive, duplicate-free, backend-independent,
and resets to a fresh cursor for each effective work variant just like the other
HashOrb strategies.

## Why this version differs from the old experiment

Earlier HashOrb prototypes generated nonce values directly from a Fibonacci
recurrence and periodically reseeded that recurrence with an LCG-style jump.
That path was deterministic and visually interesting, but it could revisit
nonce values and did not provide a clean proof of exhaustive coverage.

The current mining architecture schedules contiguous parent ranges. The modern
Fibonacci Bounce strategy therefore keeps the Fibonacci/jumping idea while
upgrading it to a true finite permutation with no repeated parent range.

There is deliberately no clock seed, random seed, LCG reseed, or work-derived
secret state in this version.

## Algorithm

For one prepared work variant:

1. Divide the configured nonce domain into the same ordinary parent ranges used
   by the other strategies. Let the number of ranges be `N`.
2. Generate Fibonacci numbers below `N` and select the largest one whose greatest
   common divisor with `N` is `1`. Call that value the Fibonacci stride `S`.
3. Emit signed bounce offsets in this order:

   ```text
   0, +1, -1, +2, -2, +3, -3, ...
   ```

4. Convert each bounce offset `B` into a physical parent-range index:

   ```text
   physical_index = (B * S) mod N
   ```

5. Search that ordinary contiguous parent range with the already-selected
   compute backend.

The cursor stops after exactly `N` assignments.

## Example with eight parent ranges

For `N = 8`, the largest Fibonacci number below eight that is coprime with eight
is five, so `S = 5`.

```text
bounce offsets:  0, +1, -1, +2, -2, +3, -3, +4
range indexes:   0,  5,  3,  2,  6,  7,  1,  4
```

The result jumps between separated regions while still visiting every range
exactly once.

For `N = 9`, `S = 8`:

```text
range indexes:   0, 8, 1, 7, 2, 6, 3, 5, 4
```

That produces the most literal edge-to-edge bounce shape.

## Coverage proof

The first `N` signed bounce offsets are a complete set of residues modulo `N`:

- odd emission indexes contribute `+1, +2, +3, ...`
- even nonzero emission indexes contribute `-1, -2, -3, ...`
- zero contributes the origin

For odd `N`, positive and negative magnitudes meet after covering every residue.
For even `N`, the final magnitude is `N/2`, which is its own negative modulo
`N`. No earlier offset is congruent to it.

The selected Fibonacci stride satisfies `gcd(S, N) = 1`. Multiplication by a
number coprime with `N` is a bijection modulo `N`. Therefore multiplying the
complete bounce-offset residue set by `S` cannot create a duplicate or omit a
physical range.

Consequently the strategy:

- emits exactly `N` parent ranges,
- emits every physical parent range exactly once,
- preserves the exact union of nonce values,
- never changes the size or contents of a parent range,
- remains exhaustive even when the final physical parent range is shorter than
  the configured chunk size.

## Backend independence

`fibonacci-bounce` schedules parent ranges only. Python, native, native-parallel,
CUDA, and CUDA-multi backends receive the same ordinary contiguous range
contract they receive from other strategies. Internal worker or GPU partitioning
remains private to the backend.

This means changing the strategy does not require rebuilding CUDA or the native
extension unless those compiled extensions are otherwise stale relative to the
checked-out branch.

## Dashboard behavior

The dashboard reads the actual emitted nonce-range events. It therefore shows
Fibonacci Bounce movement automatically in the Search Activity trail even
without inspecting strategy-private state. The header reports:

```text
STRATEGY fibonacci-bounce
```

and Recent Range Path reflects the real completed parent ranges.

## Probability statement

Fibonacci Bounce does not make a valid Bitcoin hash more likely for a fixed
number of unique hashes. It is an ordering experiment, not a cryptographic
shortcut, predictor, or proven mining-odds improvement.

Its value is that it gives HashOrb a third deterministic, visibly different,
coverage-preserving search path that can be compared experimentally against
`sequential` and `orbiting-bit` while keeping all other mining components fixed.

## Recommended live test

On a DGX Spark with a current CUDA build:

```bash
HASHORB_SEARCH_STRATEGY=fibonacci-bounce \
HASHORB_ENABLE_LIVE_STRATUM=1 \
HASHORB_ENABLE_LIVE_MINING=1 \
uv run python -m hashorb stratum-mine \
  --profile auto \
  --device 0 \
  --start-nonce 0 \
  --max-runtime-seconds 3600 \
  --max-reconnect-attempts 5 \
  --log-file logs/hashorb-fibonacci-bounce-1h.jsonl
```

On macOS, omit `--device 0`; Auto can resolve to the appropriate local CPU
profile/backend.

Run the dashboard separately:

```bash
uv run python -m hashorb dashboard \
  --log-file logs/hashorb-fibonacci-bounce-1h.jsonl
```
