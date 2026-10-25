"""Error-family contract: base class, exactly four codes, .code and message (SC-005)."""

import pytest

from foamkb.errors import (
    FoamKBError,
    IndexUnavailable,
    InternalError,
    InvalidArgument,
    NotFound,
)

FOUR = (InvalidArgument, NotFound, IndexUnavailable, InternalError)
CODES = {
    InvalidArgument: "INVALID_ARGUMENT",
    NotFound: "NOT_FOUND",
    IndexUnavailable: "INDEX_UNAVAILABLE",
    InternalError: "INTERNAL_ERROR",
}


def test_base_class_is_an_exception():
    assert issubclass(FoamKBError, Exception), "FoamKBError must derive from Exception"


def test_each_subclass_inherits_from_base():
    for cls in FOUR:
        assert issubclass(cls, FoamKBError), f"{cls.__name__} must subclass FoamKBError"


def test_base_has_exactly_four_direct_subclasses():
    direct = set(FoamKBError.__subclasses__())
    assert direct == set(FOUR), f"error family must be closed to four codes, got {sorted(c.__name__ for c in direct)}"


@pytest.mark.parametrize("cls,code", list(CODES.items()))
def test_instance_carries_its_code(cls, code):
    exc = cls("boom")
    assert exc.code == code, f"{cls.__name__}.code must be {code}, got {exc.code}"


@pytest.mark.parametrize("cls", FOUR)
def test_instance_is_a_foamkb_error_and_keeps_message(cls):
    exc = cls("boom happened")
    assert isinstance(exc, FoamKBError), f"{cls.__name__} instance must be a FoamKBError"
    assert str(exc) == "boom happened", f"str({cls.__name__}) must keep the description, got {str(exc)!r}"
