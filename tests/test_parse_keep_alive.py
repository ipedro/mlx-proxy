"""
Unit tests for the parse_keep_alive() helper function.

parse_keep_alive converts human-readable duration strings into seconds.
"""

import argparse

import pytest

from mlx_proxy.__main__ import parse_keep_alive


@pytest.mark.parametrize(
    "value, expected",
    [
        ("30s", 30),
        ("5m", 300),
        ("1h", 3600),
        ("2h", 7200),
        ("120", 120),  # bare number defaults to seconds
        ("0", 0),  # disabled
        ("-1", -1),  # always keep alive
        ("forever", -1),  # alias for -1
        ("  5m  ", 300),  # surrounding whitespace is stripped
    ],
)
def test_valid_values(value, expected):
    assert parse_keep_alive(value) == expected


@pytest.mark.parametrize("value", ["5x", "abc", "1.5m", "m5", ""])
def test_invalid_values_raise(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_keep_alive(value)
