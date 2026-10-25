"""foamkb: OpenFOAM source knowledge base (lexical core)."""

from .errors import FoamKBError, IndexUnavailable, InternalError, InvalidArgument, NotFound

__version__ = "0.1.0"

__all__ = ["FoamKBError", "InvalidArgument", "NotFound", "IndexUnavailable", "InternalError"]
