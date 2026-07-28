"""Build Hashphere's optional native CPU and explicitly enabled CUDA extensions."""

from __future__ import annotations

import os
import shutil
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

_CUDA_BUILD_FLAG = "HASHPHERE_BUILD_CUDA"


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
    cuda_root = Path(nvcc).resolve().parent.parent
    return Extension(
        "hashphere.compute._cuda",
        sources=["src/hashphere/compute/_cuda.cu"],
        include_dirs=[
            sysconfig.get_paths()["include"],
            str(cuda_root / "include"),
        ],
        library_dirs=[str(cuda_root / "lib64")],
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
