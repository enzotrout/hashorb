"""CLI selection boundary tests for the Fibonacci-bounce strategy."""

from __future__ import annotations

import hashorb.__main__ as cli_module
from hashorb.config import Settings
from hashorb.mining import FibonacciBounceSearchStrategy


def test_cli_configured_strategy_selector_resolves_fibonacci_bounce() -> None:
    settings = Settings(
        stratum_host="pool.example.com",
        stratum_port=3333,
        bitcoin_address="bc1qsynthetic",
        worker_name="test-worker",
        stratum_password="x",
        compute_backend="python",
        search_strategy="fibonacci-bounce",
    )

    selected = cli_module._select_configured_search_strategy(settings)

    assert isinstance(selected, FibonacciBounceSearchStrategy)
    assert selected.capabilities.strategy_name == "fibonacci-bounce"
    assert selected.capabilities.experimental is True
