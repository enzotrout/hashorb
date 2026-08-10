# Lite, Auto, Max, and Custom Performance Profiles

## What, Why, and Plain Talk

**What:** An optional compute profile resolves a user intent into the existing
backend, CPU-worker, explicit CUDA-device, validated launch-size, parent-chunk,
and inter-range pacing controls. The immutable resolution is fixed for one
command invocation and is completed before backend construction.

**Why:** Backend correctness controls and operating intent are different
concerns. Most operators should not need to understand every technical knob,
while advanced operators still need a fully explicit and reproducible mode.

**Plain talk:** Lite is gentle, Auto is balanced for everyday operation, Max
uses the highest capacity allowed by the safe device policy, and Custom exposes
the approved controls. Profiles choose how intensely HashOrb attempts hashes;
they do not change which hashes are valid or the probability of success for a
fixed number of unique hashes.

## Selection, Precedence, and Legacy Compatibility

The stable environment setting is:

```bash
HASHORB_COMPUTE_PROFILE=lite
```

The CLI form is:

```bash
uv run python -m hashorb profile-info --profile auto
```

An explicit CLI profile wins over the environment profile. An environment
profile is used when a profile-aware command does not provide one. When neither
is present, existing commands retain their legacy behavior. In particular, an
explicit legacy `compute-benchmark --backend ...` and continuous mining with an
explicit `--chunk-size` remain legacy requests unless `--profile` is also
provided. Names are case-normalized; empty, padded, malformed, and unknown names
fail.

Older `.env` files may contain every low-level compute setting because the old
example enabled those defaults. Remove or comment those lines before selecting
Lite, Auto, or Max. This is deliberate: a preset never pretends to be Lite while
hidden manual values make it Max-like.

Profile-controlled values are limited to backend, worker count, explicit CUDA
ordinal or list, CUDA threads per block, parent chunk size, and delay between
complete parent ranges. Profiles never change credentials, endpoints, payout
address, log path, search strategy, runtime or liveness limits, reconnect
policy, Bitcoin work construction, candidate verification, or submission.

## Exact Preset Contracts

| Profile | Selection policy | Parent chunk | Pacing |
| --- | --- | ---: | ---: |
| Lite | Device 0 `cuda` if usable; otherwise sequential `native`; otherwise `python` | GPU 100,000,000; CPU 250,000 | 0.05 s |
| Auto | Exact explicit CUDA ordinal/list; otherwise device 0 `cuda`; otherwise bounded `native-parallel`; then `native`; then `python` | GPU 500,000,000; native-parallel 5,000,000; native 1,000,000; Python 100,000 | GPU 0.08 s; CPU 0 |
| Max | Exact explicit CUDA ordinal/list; otherwise device 0 `cuda`; otherwise bounded `native-parallel`; then `native`; then `python` | GPU 500,000,000; native-parallel 10,000,000; native 2,000,000; Python 250,000 | 0 |
| Custom | Exact explicit approved controls; no guessing or fallback | required | optional, default 0 |

All CUDA presets use the established 256 threads per block. Custom accepts only
the evaluated set `64`, `128`, `256`, or `512`.

Lite rejects every manual compute override. Auto and Max accept only an
explicit CUDA device or exact device list. Several devices always require an
explicit list; neither profile inventories and consumes all visible GPUs.
Auto uses at most `min(logical_cpus // 2, 8)` native workers. Max uses at most
`min(logical_cpus, 32)`. Auto's CPU fallback remains unpaced because the bounded
worker count is already its intensity control. Auto CUDA instead keeps the same
efficient 500-million-hash range as Max and inserts an 80 ms rest after each
complete range. Max remains unpaced. A selected backend execution failure is
terminal; fallback occurs only while resolving availability before mining
starts.

Custom requires an explicit backend and chunk size. `native-parallel` also
requires workers. `cuda` requires exactly one ordinal and a validated launch
size. `cuda-multi` requires an exact ordinal list and a validated launch size.
CPU/CUDA mixtures, a single ordinal plus a list, CUDA workers, sequential CPU
workers, missing critical settings, and unsupported launch sizes fail before
backend construction. For an offline Custom benchmark, explicit `--hash-count`
also supplies the required one-shot chunk when `--chunk-size` is omitted.

## Capability Boundary

`ComputeProfileCapabilityProvider` exposes only logical CPU count, native
availability, one explicit CUDA-ordinal check, and one explicit CUDA-list check.
Tests inject deterministic fakes. The local provider performs narrow probes
only while a profile command executes, closes every probe backend, performs no
network access, and records no device name, UUID, serial number, or PCI address.
Ordinary imports, help, docs generation, CPU-only builds, and legacy commands do
not probe CUDA.

## Profile Pacing and Accounting

Pacing is zero or a finite value from 0 through 60 seconds. A positive delay is
applied only after one complete parent range and before the next. It blocks on
the existing bounded notification receiver rather than busy-waiting, with a
maximum 100 ms check quantum. Stop/runtime expiry, a replacement job, stale
session, or connection recovery interrupts the delay. There is no delay after a
candidate, terminal chunk limit, or observed stop, and no range mapping or hash
accounting changes.

Lite uses pacing on both its CUDA and sequential CPU choices. Auto uses pacing
only when it resolves to CUDA or CUDA-multi; its CPU choices instead rely on the
existing worker and chunk-size caps. Max is unpaced. Preset pacing is owned by
the profile and cannot be manually overridden for Lite, Auto, or Max.

`elapsed_ns` and weighted hashes per second remain compute-call measurements for
backward compatibility. Profiled continuous mining additionally reports
profile wall-clock elapsed time and effective hashes per wall-clock second,
including pacing and lifecycle overhead. Runtime deadlines continue to count
monotonic wall time, including pacing.

## Inspection and Offline Benchmarking

Inspect a decision without loading Stratum settings, opening a socket, or
mining:

```bash
uv run python -m hashorb profile-info --profile lite
uv run python -m hashorb profile-info --profile auto --device 0
uv run python -m hashorb profile-info --profile max --devices 0,1
uv run python -m hashorb profile-info \
  --profile custom --backend cuda --device 0 \
  --threads-per-block 256 --chunk-size 500000000
```

Benchmark the resolved backend on public synthetic work:

```bash
uv run python -m hashorb compute-benchmark \
  --profile auto --hash-count 500000000 --warmup-runs 1 --repetitions 5
```

Benchmark output labels the profile but remains a raw backend compute rate;
profile pacing is not applied. This prevents a paced Lite or Auto effective rate
from being mislabeled as kernel throughput. Continuous mining is the source of
the effective wall-clock rate.

## Spark Evidence and Chosen Constants

All original tuning measurements were offline; no pool command was executed.
On the one-GPU Spark, paired runs used one warmup and five measured repetitions:

| Parent range | `cuda` median | one-device `cuda-multi` median | Difference |
| ---: | ---: | ---: | ---: |
| 100,000,000 | 2.7542 GH/s | 2.6821 GH/s | `cuda-multi` 2.62% lower |
| 500,000,000 | 2.7575 GH/s | 2.7522 GH/s | `cuda-multi` 0.19% lower |

At 500 million hashes, all evaluated launch sizes were close: 64, 128, 256,
and 512 threads per block measured medians of approximately 2.7452, 2.7454,
2.7578, and 2.7628 GH/s. The presets retain 256 because it is the established
validated default and the small difference does not justify overfitting to one
thermal state.

An approximately 11-second unpaced sample sustained about 2.7564 GH/s effective
with active telemetry near 96% utilization and roughly 71–72 W. A matched Lite
sample using 100-million-hash ranges plus 50 ms pacing measured about 1.1565
GH/s effective, with sampled utilization mostly 35–47% and power mostly 35–37 W
after initialization. These are local, approximate telemetry observations, not
power guarantees. They demonstrate that Lite reduces average intensity rather
than merely changing range size.

Lite, Auto, and Max resolved to single-device `cuda` ordinal 0 on this Spark;
Custom accepted the explicit device-0, 256-thread, 500-million-chunk policy.
The CUDA extension rebuilt for `sm_121`; device parity passed for all four
launch sizes, and one-device `cuda-multi` parity passed. Actual two-device
profile execution and scaling remain unvalidated. The established earlier live
baseline remains approximately 2.46 GH/s; the offline paired results do not
replace it.

Before the Auto rebalance, the four-profile live human gate passed with the same
one-device Spark policy:

| Profile | Raw compute rate | Effective wall-clock rate | Outcome |
| --- | ---: | ---: | --- |
| Lite | 2.750 GH/s | 1.145 GH/s | `runtime_limit_reached` |
| Auto | 2.761 GH/s | 2.756 GH/s | `runtime_limit_reached` |
| Max | 2.760 GH/s | 2.755 GH/s | `runtime_limit_reached` |
| Custom | 2.760 GH/s | 2.754 GH/s | `runtime_limit_reached` |

Every run completed once with no incomplete run, duplicate work, connection
loss, reconnect, stale session, or command failure. Lite spent roughly 42% of
wall time hashing and reduced its effective rate about 58.5% versus Auto.
Auto, Max, and Custom were expectedly close because all three resolved to the
same device, launch size, chunk size, and zero pacing. That result motivated the
Auto rebalance.

A later one-hour Max live run on 2026-08-10 sustained 2.759853619 GH/s weighted
compute rate, checked 9,931,190,860,032 hashes over 20,792 completed ranges, and
ended with `runtime_limit_reached`. It recorded zero command failures,
connection losses, reconnects, stale sessions, liveness warnings, or duplicate
work.

At that measured Max rate, a 500-million-hash range takes about 0.181 seconds.
The 80 ms Auto CUDA delay therefore targeted an effective rate near 1.9 GH/s
without changing the raw CUDA kernel rate.

The post-change human gate then ran Lite, Auto, and Max consecutively for five
minutes each against the same one-device Spark and live Stratum endpoint:

| Profile | Raw compute rate | Effective wall-clock rate | Effective vs Max | Outcome |
| --- | ---: | ---: | ---: | --- |
| Lite | 2.7518 GH/s | 1.1486 GH/s | 41.7% | `runtime_limit_reached` |
| Auto | 2.7595 GH/s | 1.8854 GH/s | 68.4% | `runtime_limit_reached` |
| Max | 2.7601 GH/s | 2.7550 GH/s | 100% | `runtime_limit_reached` |

Auto therefore landed materially between Lite and Max as intended: about 1.64
times Lite's effective rate and about 31.6% below Max. All three runs completed
their runtime limit with zero duplicate work, reconnect attempts, successful or
failed reconnects, connection losses, or candidate submissions. The raw CUDA
compute rates remained within about 0.3% of one another, supporting the design
choice to preserve the efficient kernel configuration and control average
intensity through inter-range pacing instead.

These are local measurements from one DGX Spark under one thermal and software
state. They validate the profile separation on that host but are not portable
performance, power, utilization, or temperature guarantees.

## Deferred Work

Profiles are fixed for one invocation. Runtime profile switching, thermal
feedback, fan or clock control, dashboard controls, saved profiles,
distributed-worker profiles, CPU/GPU hybrid allocation, automatic multi-GPU
selection, actual two-device validation, Windows CUDA builds, and portable CUDA
wheels remain deferred.
