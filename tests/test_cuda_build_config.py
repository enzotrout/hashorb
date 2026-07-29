from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import setuptools


def load_setup_module():
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("hashsphere_setup", setup_path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(setuptools, "setup"):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


def test_cuda_arch_flags_use_sm_121_by_default() -> None:
    module = load_setup_module()

    assert module._cuda_arch_flags() == ["-gencode", "arch=compute_121,code=sm_121"]
    assert module._cuda_arch_flags("121") == ["-gencode", "arch=compute_121,code=sm_121"]
    assert module._cuda_arch_flags("120") == ["-gencode", "arch=compute_120,code=sm_120"]


def test_cuda_library_dirs_discover_arm64_toolkit_layout() -> None:
    module = load_setup_module()
    cuda_root = Path("/usr/local/cuda")
    discovered = module._discover_cuda_library_dirs(cuda_root)

    assert any(path.endswith("targets/sbsa-linux/lib") for path in discovered)
