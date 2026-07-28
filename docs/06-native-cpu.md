# Portable Native CPU Backend

## Decision

Hashphere uses a small CPython C extension for its first optimized backend.
Rust with PyO3 and maturin was considered and remains a sound future option,
but it would add a second language package manager, extension framework, and
build backend to a repository that previously had only Python packaging. The C
extension is materially smaller for the current fixed interface and builds
with the platform compiler already used for CPython extensions.

The implementation is portable C11 with no architecture-specific assembly,
SIMD intrinsic, operating-system library, external cryptographic dependency,
thread, or process. It contains a self-contained SHA-256 implementation solely
for the supplied 80-byte header search. Python remains the authoritative
correctness implementation.

## Source Layout

```text
setup.py
src/hashphere/compute/
├── _native.c
├── backend.py
├── benchmark.py
├── native.py
├── python.py
└── registry.py
```

- `setup.py` declares the extension as optional.
- `_native.c` owns the GIL-released sequential hash loop and narrow tuple
  result.
- `native.py` owns loading, availability, timing, result validation, Python
  candidate verification, and public result construction.
- `benchmark.py` owns the public deterministic synthetic fixture.
- `registry.py` lists native availability and preserves `auto → python`.

## Extension Contract

The private extension callable receives exactly:

```text
header_prefix: 76 immutable bytes
share_target: 32 immutable little-endian bytes
network_target: 32 immutable little-endian bytes
start_nonce: unsigned integer from 0 through 0xffffffff
stop_nonce: unsigned integer from 1 through 2**32
```

The start is inclusive and stop is exclusive. Both targets must encode positive
integers. Boolean bounds, mutable buffers, malformed lengths, empty or reversed
ranges, and overflow are rejected without coercion.

For each ascending nonce, C appends four explicit little-endian bytes, applies
double-SHA256, interprets the raw digest as an unsigned little-endian value,
and compares independently and inclusively with both targets. It stops at the
first qualifying candidate. The result contains optional nonce and raw digest,
both Boolean flags, and exact hashes checked.

The loop releases the GIL with `Py_BEGIN_ALLOW_THREADS` and reacquires it before
constructing Python objects. It does not create worker threads.

## Candidate Verification

The wrapper never trusts a native candidate directly. It requires the nonce to
equal the count-implied first result inside the assigned range, reconstructs
the 80-byte header, recomputes the digest through Python's existing
`hash_block_header`, and recomputes both flags through `hash_meets_target`.
Mismatch raises a controlled `ComputeBackendExecutionError`; submission cannot
receive an unverified candidate.

This verification occurs only for a reported match. Exhausted-range throughput
does not pay for a second Python hash per candidate.

## Build Process

The project now uses `setuptools.build_meta` while retaining the existing `uv`
workflow. From a clean checkout:

```bash
uv sync --locked
uv run python -c "from hashphere.compute import list_compute_backends; print(list_compute_backends())"
uv build
```

For an editable rebuild after C changes:

```bash
uv sync --reinstall-package hashphere
```

Generated shared libraries, build directories, distributions, and egg metadata
are ignored by Git. Source distributions contain `_native.c`; compiled wheels
contain the platform extension.

Because the extension is optional, a compiler or extension-build failure does
not remove the Python package. The registry then reports `native` unavailable
with a controlled category, while `python`, `auto`, and legacy `cpu` continue to
operate. Explicit native selection fails before live networking. There is no
runtime fallback after native execution begins.

## Development Prerequisites

Common prerequisites are Python 3.13 development headers, `uv`, and a C11
compiler compatible with that Python installation.

### macOS

Install the Xcode Command Line Tools and use the repository's Python/uv setup:

```bash
xcode-select --install
uv sync --locked
```

The current source build and strict warning check have been validated on Apple
Silicon with Apple Clang. Intel macOS follows the same CPython extension model
but requires its own CI build and wheel validation.

### Linux and Docker Direction

Linux source builds use GCC or Clang plus the matching Python development
headers. Docker images should build in a compiler stage and copy the installed
wheel or environment into the runtime stage. Linux x86-64 and ARM64 wheels are
planned; manylinux policy and CI runners remain deferred.

### Windows Direction

Windows builds should use the Microsoft C toolchain compatible with the target
CPython release. The code uses fixed-width standard integer types and no POSIX
API. Windows x86-64 is the first planned wheel target; Windows ARM64 remains a
later packaging target.

## Formatting and Static Validation

No native formatter is assumed to be installed. The portable strict compiler
check used for this milestone is:

```bash
include_dir=$(uv run python -c \
  'import sysconfig; print(sysconfig.get_config_var("INCLUDEPY"))')
cc -std=c11 -Wall -Wextra -Werror \
  -I"$include_dir" \
  -fsyntax-only src/hashphere/compute/_native.c
```

When `clang-format` becomes part of the locked development toolchain, its style
and check command should be committed before enforcing it in CI.

## Deterministic Benchmark Fixture

`deterministic_benchmark_work()` returns immutable synthetic work with public
fixed bytes and minimum positive targets. It contains no pool job, credential,
address, session nonce, or protocol payload and is unsuitable for submission.
Both benchmark commands search the same fixture and range:

```bash
uv run python -m hashphere compute-benchmark \
  --backend python \
  --hash-count 100000

uv run python -m hashphere compute-benchmark \
  --backend native \
  --hash-count 100000
```

The command reports actual local elapsed nanoseconds and calculates rate from
unrounded totals. Automated tests assert correctness and deterministic output
shape, not a fragile performance threshold. One machine's result is not a
general speedup claim or evidence of live pool behavior.

## Correctness Policy

Python/native parity covers exhausted ranges, independent and combined target
matches, start and final included nonces, one-nonce ranges, ranges ending at
`2**32`, exact counts and flags, and fixed-seed randomized small ranges. Direct
extension tests cover boundary representation and raw digest parity. Wrapper
tests inject malformed results, exceptions, digest mismatches, nonce/count
disagreements, and flag mismatches.

Every future native optimization must keep the Python backend as oracle and
pass the same tests. No performance change may weaken candidate verification,
range accounting, or failure isolation.

## Wheel Strategy

Publishing wheels is deferred. The future CI matrix should build and test each
wheel on macOS ARM64 and x86-64, Windows x86-64, and Linux x86-64 and ARM64,
then run parity tests against the installed artifact. Python-only artifacts or
source installation must also be tested with the extension unavailable.

Parallel CPU assignments, SIMD dispatch, cooperative cancellation, GPU/CUDA,
device selection, Lite/Auto/Max/Custom profiles, and automatic native selection
remain separate milestones.
