# Optional CUDA Correctness Backend

## What

Hashphere has one optional `cuda` compute backend implementing the existing
`MiningComputeBackend` contract. It accepts validated `PreparedMiningWork` and
one ordinary half-open parent nonce range, evaluates that complete range on one
explicitly selected NVIDIA device, and returns the existing immutable
`NonceSearchResult`.

The implementation is correctness-first. CUDA compilation, toolkit/runtime
initialization, and real-device parity are explicitly gated. The Python and
native CPU paths remain independent and operational without CUDA.

## Why

This boundary proves GPU execution can be introduced without moving Bitcoin
work construction, Stratum, search strategies, progression, recovery,
submission, signals, console output, or JSONL ownership into GPU code. It also
creates a defense-in-depth rule: a CUDA candidate cannot leave the backend
until the existing Python cryptographic primitives verify it.

## Plain Talk

Sequential or orbiting-bit chooses one region of the nonce map. CUDA divides
that region among many device lanes. Every lane hashes its assigned nonces, and
the GPU keeps the smallest qualifying nonce. Python then rebuilds and hashes
that exact candidate again. If the GPU and Python disagree, mining stops rather
than submitting it or trying a CPU fallback.

## Source Layout

```text
setup.py                         explicit optional-build integration
MANIFEST.in                     CUDA source retained in source distributions
src/hashphere/compute/_cuda.cu  Python extension and CUDA kernel
src/hashphere/compute/cuda.py   availability, validation, timing, verification
tests/test_cuda_backend.py      host-only fake-runtime and mapping tests
tests/test_cuda_hardware.py     explicitly gated real-device parity tests
```

No numerical-computing framework or runtime package is downloaded. The CUDA
toolkit and driver are external prerequisites owned by the operator.

## Optional Build Process

Normal installation does not invoke `nvcc`:

```bash
uv sync --locked --reinstall-package hashphere
```

CUDA source is included in the sdist but the extension is built only with:

```bash
HASHPHERE_BUILD_CUDA=1 \
uv sync --locked --reinstall-package hashphere
```

Prerequisites are:

- a supported NVIDIA GPU;
- an installed compatible NVIDIA driver;
- an installed CUDA toolkit and runtime;
- `nvcc` available on `PATH`;
- a Python 3.13 development environment supported by the toolkit.

The explicit build adds `_cuda` and links it with the toolkit's CUDA runtime.
If `HASHPHERE_BUILD_CUDA=1` is set and `nvcc` is missing or compilation fails,
the build fails. It does not silently publish a CPU package that claims CUDA
availability. Normal CPU builds and forced native-C compiler-failure builds do
not attempt CUDA compilation. CUDA wheel publication is deferred.

## Runtime Availability and Configuration

Select CUDA and one ordinal through:

```bash
HASHPHERE_COMPUTE_BACKEND=cuda
HASHPHERE_CUDA_DEVICE=0
```

The ordinal is a strict unpadded ASCII decimal integer from `0` through
`2147483647`. CPU backends ignore the CUDA setting. `auto` and the compatibility
alias `cpu` remain static aliases for Python.

Importing the general Hashphere package or selecting a CPU backend does not
initialize CUDA. Initialization is limited to CUDA capability listing or
explicit CUDA selection. Operational availability requires:

1. successful `_cuda` import;
2. the three expected extension callables;
3. successful CUDA runtime initialization;
4. an available requested device.

Absence or initialization failure becomes controlled low-cardinality
unavailability. There is no CUDA-to-CPU fallback.

## Backend Capabilities

CUDA declares:

```text
backend_name: cuda
backend_kind: gpu
implementation: cuda
deterministic_search_order: true
supports_parallel_search: true
supports_cooperative_cancellation: false
supports_device_selection: true
preferred_batch_size: null
```

Deterministic search order means the public result is independent of device
scheduling order: the smallest qualifying nonce is selected. It is not a claim
that GPU lanes execute sequentially.

## Kernel Range Mapping

For one supplied `[start_nonce, stop_nonce)` range:

```text
range_size = stop_nonce - start_nonce
lane = block_index * threads_per_block + thread_index
stride = grid_size * threads_per_block
offset = lane + iteration * stride
nonce = start_nonce + offset
```

A lane continues only while `offset < range_size`. Therefore every logical
offset belongs to exactly one lane modulo `stride`, no two lanes own the same
offset, no offset is omitted, and no out-of-range nonce is evaluated. Bounds
are carried in 64-bit arithmetic so the exclusive stop may equal `2**32`; each
actual nonce remains an unsigned 32-bit value and never wraps.

The host-only `cuda_grid_stride_offsets` utility independently models this
mapping for small deterministic tests. It tests coverage and uniqueness, not
unobservable GPU scheduling order.

## Header Hashing and Target Comparison

For each nonce, the kernel copies the validated 76-byte prefix and appends:

```text
nonce.to_bytes(4, byteorder="little", signed=False)
```

It performs SHA-256 twice and leaves the raw 32 digest bytes unchanged. Share
and network targets arrive as exact 32-byte little-endian values. Comparison
walks from the most-significant little-endian byte down, applies `<=`
inclusively, and evaluates the two targets independently. No displayed-hash
conversion, target approximation, or endian reversal occurs.

## Candidate Reduction and Hash Accounting

Qualifying lanes apply an unsigned atomic minimum to the absolute nonce. This
makes the smallest qualifying nonce the only public candidate regardless of
which lane completes first. After the complete search synchronizes, one
single-lane device check recovers that candidate's two target flags.

This correctness kernel does not stop early. It evaluates the entire supplied
range, so:

```text
hashes_checked = stop_nonce - start_nonce
```

The wrapper's `elapsed_ns` uses `perf_counter_ns` around the complete extension
call. The interval includes allocation, transfers, launch, synchronization,
candidate retrieval, flag retrieval, and required cleanup. It is not a sum of
per-thread timings. A negative measured delta is safely clamped to zero.

## Python Candidate Verification

The extension returns only:

- candidate nonce or `None`;
- share-target Boolean;
- network-target Boolean;
- full hash count.

For a candidate, Python verifies:

1. the nonce is an actual integer inside the requested range;
2. the count equals the complete range size;
3. at least one returned flag is true;
4. the exact 80-byte header is `header_prefix || nonce_little_endian`;
5. `hash_block_header` produces the authoritative raw digest;
6. `hash_meets_target` independently reproduces both flags.

Any malformed result or mismatch raises `ComputeBackendExecutionError`. Only
the Python-reconstructed digest enters `NonceSearchMatch`. An unverified CUDA
candidate cannot reach `mining.submit`.

## Strategy and Lifecycle Integration

Sequential and orbiting-bit both emit ordinary contiguous parent ranges. CUDA
receives those bounds unchanged and has no access to global assignment order,
orbiting permutation state, prior ranges, CPU worker counts, pool history, or
submission metadata. Its private launch geometry does not alter strategy order.

One backend instance and ordinal are selected before live networking and
survive:

- repeated chunks;
- pool-job replacement;
- extra-nonce progression;
- network-time rolling;
- search-strategy cursor replacement; and
- recovered Stratum sessions.

The CLI closes the backend after success, controlled stop, execution failure,
recovery exhaustion, and cleanup paths. Close is idempotent and synchronizes
backend-owned work without calling a global device reset. CUDA execution or
verification failure is terminal and does not cause Stratum reconnect,
backend reselection, range retry, or fallback.

## Offline Benchmark

The benchmark requires no `.env`, credentials, live opt-in, or networking:

```bash
uv run python -m hashphere compute-benchmark \
  --backend cuda \
  --device 0 \
  --hash-count 1000000
```

It uses the same public deterministic synthetic work as CPU backends. Output is
limited to stable backend identity, ordinal, hashes checked, elapsed
nanoseconds, rate or unavailable, and exhausted/candidate status. It excludes
the fixture header, targets, digest, candidate nonce, device UUID, serial, PCI
address, compiler path, and driver path. No performance threshold is asserted.

## Hardware Parity Gate

Default pytest never initializes CUDA. After an explicit CUDA build, run:

```bash
HASHPHERE_ENABLE_CUDA_TESTS=1 \
HASHPHERE_CUDA_DEVICE=0 \
uv run pytest -q tests/test_cuda_hardware.py
```

The gated suite compares CUDA with `PythonSequentialBackend` over exhausted,
share, network, both-target, first/final nonce, `2**32` boundary, and fixed-seed
random small ranges. It checks exact candidate nonce, Python-reconstructed
digest and flags, full CUDA range count, and cleanup. It skips cleanly if the
extension or requested device is absent.

Hardware validation is complete only after the extension compiles and this
suite executes on a real CUDA device. Host fake tests and source review are not
a substitute. Toolkit, driver, and device details belong only in the local
validation report, never privacy-sensitive JSONL.

## Observability and Privacy

`compute_backend_selected` may contain stable name, kind, implementation,
parallel/cancellation/device-selection flags, and device ordinal. Existing
nonce-range events remain the only parent-search records. There are no
per-thread, block, lane, transfer, or kernel events.

JSONL and ordinary console failures exclude UUIDs, serial numbers, PCI
addresses, paths, raw CUDA errors, work bytes, target values, candidate values,
credentials, pool jobs, and session nonce material. The read-only summary
counts `cuda` only by stable backend name.

## Limitations and Deferred Work

This slice does not implement:

- early termination or cooperative mid-range cancellation;
- automatic block/grid tuning;
- pinned-memory or persistent-buffer optimization;
- architecture-specific kernels or DGX Spark/GB10 tuning;
- multiple GPUs or cross-device reduction;
- CUDA wheel publication or automatic toolkit installation;
- fallback, retry, reconnect, or compute profiles;
- mining lifecycle, Stratum, strategy, or submission behavior inside CUDA.

DGX Spark/GB10 correctness and performance tuning is the next GPU milestone
after real-device parity. Multi-GPU execution follows later. No speed advantage
is claimed before controlled hardware evidence exists.
