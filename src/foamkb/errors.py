"""Closed error-code family shared by every foamkb module (Constitution III, D4)."""


class FoamKBError(Exception):
    """Root of the family; it names no code of its own."""


class InvalidArgument(FoamKBError):
    """Caller supplied an unusable argument: bad path, mode or range."""

    code = "INVALID_ARGUMENT"


class NotFound(FoamKBError):
    """A ref, rev or chunk does not exist in the current index."""

    code = "NOT_FOUND"


class IndexUnavailable(FoamKBError):
    """The index is missing, unreadable, not SQLite, or has a wrong schema/meta."""

    code = "INDEX_UNAVAILABLE"


class InternalError(FoamKBError):
    """An unexpected failure, e.g. the atomic replace of the index failed."""

    code = "INTERNAL_ERROR"
