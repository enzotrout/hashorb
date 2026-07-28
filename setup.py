"""Build the optional portable Hashphere native CPU extension."""

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "hashphere.compute._native",
            sources=["src/hashphere/compute/_native.c"],
            optional=True,
        )
    ]
)
