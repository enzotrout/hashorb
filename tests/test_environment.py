import platform
import sys


def test_python_version() -> None:
    assert sys.version_info >= (3, 13)
    assert sys.version_info < (3, 14)


def test_python_implementation() -> None:
    assert platform.python_implementation() == "CPython"


def test_basic_arithmetic() -> None:
    assert 2 + 2 == 4
