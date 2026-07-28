"""Tests for Hashphere runtime configuration."""

import pytest

import hashphere.config.settings as settings_module
from hashphere.config.settings import (
    Settings,
    resolve_worker_name,
    sanitize_worker_name,
)

_ENVIRONMENT_VARIABLES = (
    "HASHPHERE_STRATUM_HOST",
    "HASHPHERE_STRATUM_PORT",
    "HASHPHERE_BITCOIN_ADDRESS",
    "HASHPHERE_WORKER_NAME",
    "HASHPHERE_STRATUM_PASSWORD",
    "HASHPHERE_COMPUTE_BACKEND",
    "HASHPHERE_COMPUTE_PROFILE",
    "HASHPHERE_COMPUTE_WORKERS",
    "HASHPHERE_SEARCH_STRATEGY",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the developer's real .env file from affecting tests."""

    monkeypatch.setattr(settings_module, "load_dotenv", lambda: False)

    for variable in _ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("MacBook-Air", "macbook-air"),
        ("Spark 2B09.local", "spark-2b09-local"),
        ("  worker_01  ", "worker_01"),
        ("worker@home!", "worker-home"),
        ("---", "hashphere"),
        ("", "hashphere"),
    ],
)
def test_sanitize_worker_name(raw_name: str, expected: str) -> None:
    assert sanitize_worker_name(raw_name) == expected


def test_resolve_worker_name_uses_hostname_for_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module.socket,
        "gethostname",
        lambda: "Loren MacBook.local",
    )

    assert resolve_worker_name("auto") == "loren-macbook-local"


def test_resolve_worker_name_accepts_explicit_name() -> None:
    assert resolve_worker_name("Mining Rig 01") == "mining-rig-01"


def test_settings_load_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HASHPHERE_BITCOIN_ADDRESS",
        "bc1qexampleaddress",
    )
    monkeypatch.setattr(
        settings_module.socket,
        "gethostname",
        lambda: "hashphere-test",
    )

    settings = Settings.from_env()

    assert settings.stratum_host == "stratum.ckpool.org"
    assert settings.stratum_port == 3333
    assert settings.bitcoin_address == "bc1qexampleaddress"
    assert settings.worker_name == "hashphere-test"
    assert settings.stratum_username == ("bc1qexampleaddress.hashphere-test")
    assert settings.stratum_password == "x"
    assert settings.compute_backend == "auto"
    assert settings.compute_profile == "lite"
    assert settings.compute_workers == 2
    assert settings.search_strategy == "sequential"


def test_settings_load_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HASHPHERE_STRATUM_HOST", "pool.example.com")
    monkeypatch.setenv("HASHPHERE_STRATUM_PORT", "4444")
    monkeypatch.setenv(
        "HASHPHERE_BITCOIN_ADDRESS",
        "bc1qexampleaddress",
    )
    monkeypatch.setenv("HASHPHERE_WORKER_NAME", "worker-02")
    monkeypatch.setenv("HASHPHERE_STRATUM_PASSWORD", "test-password")
    monkeypatch.setenv("HASHPHERE_COMPUTE_BACKEND", "GPU")
    monkeypatch.setenv("HASHPHERE_COMPUTE_PROFILE", "MAX")
    monkeypatch.setenv("HASHPHERE_COMPUTE_WORKERS", "256")
    monkeypatch.setenv("HASHPHERE_SEARCH_STRATEGY", "auto")

    settings = Settings.from_env()

    assert settings.stratum_host == "pool.example.com"
    assert settings.stratum_port == 4444
    assert settings.worker_name == "worker-02"
    assert settings.stratum_password == "test-password"
    assert settings.compute_backend == "gpu"
    assert settings.compute_profile == "max"
    assert settings.compute_workers == 256
    assert settings.search_strategy == "auto"


def test_missing_bitcoin_address_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="HASHPHERE_BITCOIN_ADDRESS is required",
    ):
        Settings.from_env()


def test_non_integer_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HASHPHERE_BITCOIN_ADDRESS",
        "bc1qexampleaddress",
    )
    monkeypatch.setenv("HASHPHERE_STRATUM_PORT", "not-a-port")

    with pytest.raises(
        ValueError,
        match="HASHPHERE_STRATUM_PORT must be an integer",
    ):
        Settings.from_env()


@pytest.mark.parametrize("port", ["0", "65536", "-1"])
def test_out_of_range_port_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setenv(
        "HASHPHERE_BITCOIN_ADDRESS",
        "bc1qexampleaddress",
    )
    monkeypatch.setenv("HASHPHERE_STRATUM_PORT", port)

    with pytest.raises(
        ValueError,
        match="HASHPHERE_STRATUM_PORT must be between 1 and 65535",
    ):
        Settings.from_env()


@pytest.mark.parametrize("workers", ["1", "2", "256"])
def test_compute_workers_accept_strict_supported_values(
    monkeypatch: pytest.MonkeyPatch,
    workers: str,
) -> None:
    monkeypatch.setenv("HASHPHERE_BITCOIN_ADDRESS", "bc1qexampleaddress")
    monkeypatch.setenv("HASHPHERE_COMPUTE_WORKERS", workers)

    assert Settings.from_env().compute_workers == int(workers)


@pytest.mark.parametrize(
    "workers",
    ["", "0", "257", "01", "+2", "-2", "0x2", "2.0", " 2", "2 ", "２"],
)
def test_compute_workers_reject_malformed_or_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
    workers: str,
) -> None:
    monkeypatch.setenv("HASHPHERE_BITCOIN_ADDRESS", "bc1qexampleaddress")
    monkeypatch.setenv("HASHPHERE_COMPUTE_WORKERS", workers)

    with pytest.raises(ValueError, match="HASHPHERE_COMPUTE_WORKERS"):
        Settings.from_env()


@pytest.mark.parametrize(
    "strategy",
    ["sequential", "orbiting-bit", "auto", "future-strategy"],
)
def test_search_strategy_accepts_exact_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    monkeypatch.setenv("HASHPHERE_BITCOIN_ADDRESS", "bc1qexampleaddress")
    monkeypatch.setenv("HASHPHERE_SEARCH_STRATEGY", strategy)

    assert Settings.from_env().search_strategy == strategy


@pytest.mark.parametrize(
    "strategy",
    ["", "Sequential", " sequential", "sequential ", "two words", "+sequential", "é"],
)
def test_search_strategy_rejects_malformed_values(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    monkeypatch.setenv("HASHPHERE_BITCOIN_ADDRESS", "bc1qexampleaddress")
    monkeypatch.setenv("HASHPHERE_SEARCH_STRATEGY", strategy)

    with pytest.raises(ValueError, match="HASHPHERE_SEARCH_STRATEGY"):
        Settings.from_env()
