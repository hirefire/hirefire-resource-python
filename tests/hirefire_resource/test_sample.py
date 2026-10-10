import math
from decimal import Decimal
from fractions import Fraction

import pytest

from hirefire_resource.sample import coerce_sample, format_sample_value


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),
        (7, 7),
        (10**400, 10**400),
        (0.0, 0.0),
        (1.5, 1.5),
        (Decimal("2.5"), 2.5),
        (Decimal("0"), 0.0),
        (Fraction(1, 4), 0.25),
    ],
)
def test_a_number_of_zero_or_more_is_a_sample_of_the_type_the_wire_takes(
    value, expected
):
    sample = coerce_sample(value)

    assert sample == expected
    assert type(sample) is type(expected)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        "3",
        b"3",
        [1],
        -1,
        -0.5,
        Decimal("-1"),
        Fraction(-1, 4),
        math.nan,
        math.inf,
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Fraction(10**400),
        1j,
    ],
)
def test_a_value_that_is_not_a_finite_number_of_zero_or_more_is_no_sample(value):
    assert coerce_sample(value) is None


def test_a_rejected_value_is_named_with_its_type():
    assert format_sample_value("many") == "str('many')"
    assert format_sample_value(None) == "NoneType('None')"


def test_a_rejected_value_of_64_bytes_is_printed_in_full_and_a_longer_one_is_cut():
    assert format_sample_value("a" * 64) == f"str('{'a' * 64}')"
    assert format_sample_value("a" * 65) == f"str('{'a' * 64}…')"


def test_a_cut_inside_a_character_of_several_bytes_leaves_a_valid_text():
    assert format_sample_value("a" + "é" * 40) == f"str('a{'é' * 31}\ufffd…')"


def test_a_value_that_cannot_be_printed_is_named_by_its_type_alone():
    class Unprintable:
        def __str__(self):
            raise RuntimeError("no text")

    assert format_sample_value(Unprintable()) == "Unprintable"


def test_a_value_of_exactly_64_bytes_is_shown_whole_and_a_longer_one_is_cut_there():
    assert format_sample_value("x" * 64) == "str('" + "x" * 64 + "')"
    assert format_sample_value("x" * 65) == "str('" + "x" * 64 + "…')"
