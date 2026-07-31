"""Public deterministic synthetic work for offline compute benchmarks."""

from __future__ import annotations

from hashorb.mining.search import PreparedMiningWork

_BENCHMARK_HEADER_PREFIX = bytes(range(76))


def deterministic_benchmark_work() -> PreparedMiningWork:
    """Return immutable public synthetic work that is not valid pool work."""

    return PreparedMiningWork(
        job_id="synthetic-compute-benchmark",
        extra_nonce_2="00000000",
        network_time="00000000",
        header_prefix=_BENCHMARK_HEADER_PREFIX,
        network_target=1,
        share_target=1,
    )
