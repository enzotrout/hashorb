"""Build Hashphere's optional native CPU and explicitly enabled CUDA extensions."""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

_CUDA_BUILD_FLAG = "HASHPHERE_BUILD_CUDA"


def _cuda_arch_flags(arch: str | None = None) -> list[str]:
    """Return nvcc architecture flags for the configured CUDA target."""

    requested = (arch or os.getenv("HASHPHERE_CUDA_ARCH", "121")).strip()
    if not requested:
        requested = "121"
    normalized = requested.lower()
    if normalized.startswith("sm_"):
        normalized = normalized[3:]
    elif normalized.startswith("compute_"):
        normalized = normalized[8:]
    if normalized in {"120", "121"}:
        return ["-gencode", f"arch=compute_{normalized},code=sm_{normalized}"]
    raise RuntimeError("HASHPHERE_CUDA_ARCH must be 120 or 121")


def _discover_cuda_library_dirs(cuda_root: Path) -> list[str]:
    """Discover CUDA runtime library directories for the current toolkit layout."""

    discovered: list[str] = []
    seen: set[str] = set()
    for base in (cuda_root, cuda_root / "targets"):
        if not base.exists():
            continue
        for child in (base / "lib64", base / "lib"):
            if child.exists() and str(child) not in seen:
                discovered.append(str(child))
                seen.add(str(child))
    targets_root = cuda_root / "targets"
    if targets_root.exists():
        for target_dir in sorted(targets_root.iterdir()):
            if not target_dir.is_dir():
                continue
            for child in (target_dir / "lib", target_dir / "lib64"):
                if child.exists() and str(child) not in seen:
                    discovered.append(str(child))
                    seen.add(str(child))
    return discovered


class CudaBuildExt(build_ext):
    """Compile CUDA sources with nvcc only during an explicit CUDA build."""

    def build_extensions(self) -> None:
        cuda_extensions = [
            extension
            for extension in self.extensions
            if any(source.endswith(".cu") for source in extension.sources)
        ]
        if not cuda_extensions:
            super().build_extensions()
            return
        nvcc = shutil.which("nvcc")
        if nvcc is None:
            raise RuntimeError("HASHPHERE_BUILD_CUDA=1 requires nvcc")
        compiler = self.compiler
        if compiler is None:
            raise RuntimeError("CUDA build compiler is unavailable")
        if ".cu" not in compiler.src_extensions:
            compiler.src_extensions.append(".cu")
        original_compile = compiler._compile

        def compile_source(
            object_file: str,
            source: str,
            extension: str,
            compiler_arguments: list[str],
            extra_arguments: list[str],
            preprocessor_options: list[str],
        ) -> None:
            if not source.endswith(".cu"):
                original_compile(
                    object_file,
                    source,
                    extension,
                    compiler_arguments,
                    extra_arguments,
                    preprocessor_options,
                )
                return
            command = [
                nvcc,
                "-c",
                source,
                "-o",
                object_file,
                "-std=c++17",
                *_cuda_arch_flags(),
                "-Xcompiler=-fPIC",
            ]
            command.extend(
                argument
                for argument in compiler_arguments
                if argument.startswith(("-I", "-D", "-U"))
            )
            command.extend(extra_arguments)
            self.spawn(command)

        compiler._compile = compile_source
        try:
            super().build_extensions()
        finally:
            compiler._compile = original_compile


def cuda_extension() -> Extension:
    """Create the explicitly enabled CUDA extension or fail before compilation."""

    nvcc = shutil.which("nvcc")
    if nvcc is None:
        raise RuntimeError("HASHPHERE_BUILD_CUDA=1 requires nvcc")
    cuda_home = os.getenv("CUDA_HOME") or os.getenv("CUDA_PATH")
    cuda_root = Path(cuda_home).resolve() if cuda_home else Path(nvcc).resolve().parent.parent
    library_dirs = _discover_cuda_library_dirs(cuda_root)
    if not library_dirs:
        raise RuntimeError(
            "CUDA runtime library directory could not be discovered; set CUDA_HOME or CUDA_PATH"
        )
    runtime_library_dirs = library_dirs if sys.platform != "win32" else []
    return Extension(
        "hashphere.compute._cuda",
        sources=["src/hashphere/compute/_cuda.cu"],
        include_dirs=[
            sysconfig.get_paths()["include"],
            str(cuda_root / "include"),
        ],
        library_dirs=library_dirs,
        runtime_library_dirs=runtime_library_dirs,
        libraries=["cudart"],
        language="c++",
        optional=False,
    )


extensions = [
    Extension(
        "hashphere.compute._native",
        sources=["src/hashphere/compute/_native.c"],
        optional=True,
    )
]
if os.getenv(_CUDA_BUILD_FLAG) == "1":
    extensions.append(cuda_extension())

setup(
    ext_modules=extensions,
    cmdclass={"build_ext": CudaBuildExt},
)
