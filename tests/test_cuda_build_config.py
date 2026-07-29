from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import setuptools


def load_setup_module():
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("hashsphere_setup", setup_path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(setuptools, "setup"):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


def add_runtime(directory: Path, filename: str = "libcudart.so") -> Path:
    directory.mkdir(parents=True)
    runtime = directory / filename
    runtime.touch()
    return directory


def test_cuda_arch_flags_require_an_explicit_tested_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_setup_module()
    monkeypatch.delenv("HASHPHERE_CUDA_ARCH", raising=False)

    assert module._cuda_arch_flags("121") == ["-gencode", "arch=compute_121,code=sm_121"]
    assert module._cuda_arch_flags("120") == ["-gencode", "arch=compute_120,code=sm_120"]
    with pytest.raises(RuntimeError, match="required.*120 or 121"):
        module._cuda_arch_flags()


@pytest.mark.parametrize("arch", ["", " 121", "121 ", "sm_121", "compute_121", "89", "all"])
def test_cuda_arch_flags_reject_malformed_or_untested_values(arch: str) -> None:
    module = load_setup_module()

    with pytest.raises(RuntimeError, match="HASHPHERE_CUDA_ARCH"):
        module._cuda_arch_flags(arch)


def test_cuda_arch_flags_read_the_explicit_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_setup_module()
    monkeypatch.setenv("HASHPHERE_CUDA_ARCH", "121")

    assert module._cuda_arch_flags() == ["-gencode", "arch=compute_121,code=sm_121"]


def test_cpu_only_setup_ignores_cuda_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HASHPHERE_BUILD_CUDA", raising=False)
    monkeypatch.setenv("HASHPHERE_CUDA_ARCH", "not-a-compiler-flag")

    load_setup_module()


def test_cuda_host_compiler_flags_remap_the_python_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_setup_module()
    python_prefix = tmp_path / "private-python"
    monkeypatch.setattr(module.sys, "base_prefix", str(python_prefix))
    monkeypatch.setattr(module.sys, "platform", "linux")

    assert module._cuda_host_compiler_flags() == [
        "-Xcompiler=-fPIC",
        f"-Xcompiler=-ffile-prefix-map={python_prefix}=/python",
    ]


def test_cuda_library_dirs_discover_supported_layouts(tmp_path: Path) -> None:
    module = load_setup_module()
    cuda_root = tmp_path / "cuda"
    expected = [
        add_runtime(cuda_root / "lib64"),
        add_runtime(cuda_root / "lib"),
        add_runtime(cuda_root / "lib" / "x64", "cudart.lib"),
        add_runtime(cuda_root / "targets" / "aarch64-linux" / "lib"),
        add_runtime(cuda_root / "targets" / "sbsa-linux" / "lib64"),
        add_runtime(cuda_root / "targets" / "x86_64-linux" / "lib"),
    ]

    assert module._discover_cuda_library_dirs(cuda_root) == [str(path) for path in expected]


def test_cuda_library_dirs_require_a_runtime_file(tmp_path: Path) -> None:
    module = load_setup_module()
    cuda_root = tmp_path / "cuda"
    (cuda_root / "lib64").mkdir(parents=True)
    (cuda_root / "targets" / "aarch64-linux" / "lib").mkdir(parents=True)

    assert module._discover_cuda_library_dirs(cuda_root) == []


def test_cuda_library_dirs_have_deterministic_precedence(tmp_path: Path) -> None:
    module = load_setup_module()
    cuda_root = tmp_path / "cuda"
    expected = [
        add_runtime(cuda_root / "lib64", "libcudart.so.13"),
        add_runtime(cuda_root / "lib", "libcudart_static.a"),
        add_runtime(cuda_root / "targets" / "alpha" / "lib"),
        add_runtime(cuda_root / "targets" / "zeta" / "lib"),
    ]

    assert module._discover_cuda_library_dirs(cuda_root) == [str(path) for path in expected]
