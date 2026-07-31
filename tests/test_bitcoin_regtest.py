"""Opt-in isolated Bitcoin Core regtest end-to-end gate."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time

import pytest

from hashphere.bitcoin import BitcoinCoreRpcClient, BitcoinRpcError, parse_block_template
from hashphere.bitcoin.solo import SoloMiningOutcome, SoloMiningPlan, run_solo_mining
from hashphere.compute.python import PythonSequentialBackend
from hashphere.config.bitcoin_rpc import BitcoinRpcSettings
from hashphere.mining import StopController, select_search_strategy

_BITCOIND = shutil.which("bitcoind")
_REGTEST_OPT_IN = "HASHPHERE_ENABLE_REGTEST_TESTS"


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _regtest_witness_address(program: bytes = bytes.fromhex("42" * 20)) -> str:
    """Encode one synthetic v0 witness program for test-only address validation."""

    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

    def polymod(values: list[int]) -> int:
        result = 1
        generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
        for value in values:
            high = result >> 25
            result = ((result & 0x1FFFFFF) << 5) ^ value
            for index, generator in enumerate(generators):
                if (high >> index) & 1:
                    result ^= generator
        return result

    def expand(prefix: str) -> list[int]:
        return (
            [ord(character) >> 5 for character in prefix]
            + [0]
            + [ord(character) & 31 for character in prefix]
        )

    accumulator = 0
    bit_count = 0
    words = [0]
    for byte in program:
        accumulator = (accumulator << 8) | byte
        bit_count += 8
        while bit_count >= 5:
            bit_count -= 5
            words.append((accumulator >> bit_count) & 31)
    if bit_count:
        words.append((accumulator << (5 - bit_count)) & 31)
    prefix = "bcrt"
    checksum_values = expand(prefix) + words + [0] * 6
    checksum = polymod(checksum_values) ^ 1
    words.extend((checksum >> (5 * (5 - index))) & 31 for index in range(6))
    return prefix + "1" + "".join(alphabet[word] for word in words)


def _wait_for_rpc(client: BitcoinCoreRpcClient, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated bitcoind stopped before RPC readiness")
        try:
            client.get_blockchain_info()
            return
        except BitcoinRpcError:
            time.sleep(0.1)
    raise RuntimeError("isolated bitcoind did not reach RPC readiness")


@pytest.mark.skipif(
    _BITCOIND is None or os.getenv(_REGTEST_OPT_IN) != "1",
    reason=(
        "compatible bitcoind is unavailable"
        if _BITCOIND is None
        else f"set {_REGTEST_OPT_IN}=1 to run the isolated regtest gate"
    ),
)
def test_isolated_regtest_constructs_proposes_and_submits_complete_block() -> None:
    """Prove one wallet-free Hashsphere-built block advances an isolated chain."""

    assert _BITCOIND is not None
    version = subprocess.run(
        [_BITCOIND, "--version"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert b"Bitcoin Core" in version.stdout
    rpc_port = _unused_loopback_port()
    peer_port = _unused_loopback_port()
    username = "hashsphere-regtest"
    password = "synthetic-isolated-regtest-secret"

    with tempfile.TemporaryDirectory(prefix="hashsphere-regtest-") as data_directory:
        process = subprocess.Popen(
            [
                _BITCOIND,
                f"-datadir={data_directory}",
                "-regtest=1",
                "-server=1",
                "-listen=0",
                "-discover=0",
                "-dnsseed=0",
                "-printtoconsole=0",
                "-rpcbind=127.0.0.1",
                "-rpcallowip=127.0.0.1",
                f"-rpcport={rpc_port}",
                f"-port={peer_port}",
                f"-rpcuser={username}",
                f"-rpcpassword={password}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client = BitcoinCoreRpcClient(
            BitcoinRpcSettings(
                host="127.0.0.1",
                port=rpc_port,
                timeout_seconds=5,
                username=username,
                password=password,
            )
        )
        try:
            _wait_for_rpc(client, process)
            initial_info = client.get_blockchain_info()
            assert initial_info.chain == "regtest"
            destination = client.validate_address(_regtest_witness_address())
            template = parse_block_template(client.get_block_template())

            result = run_solo_mining(
                SoloMiningPlan(0, 1_000, max_chunks=100, template_poll_seconds=30),
                chain="regtest",
                payout_script=destination.script_pub_key,
                initial_template=template,
                backend=PythonSequentialBackend(),
                strategy=select_search_strategy("sequential"),
                stop_token=StopController(),
                fetch_template=lambda: parse_block_template(client.get_block_template()),
                propose_block=client.propose_block,
                submit_block=client.submit_block,
            )

            assert result.outcome is SoloMiningOutcome.BLOCK_ACCEPTED, (
                f"sanitized proposal category: {result.proposal_category}"
            )
            assert client.get_blockchain_info().blocks == initial_info.blocks + 1
        finally:
            client.close()
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert process.poll() is not None
