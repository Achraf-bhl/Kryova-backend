"""What can go wrong on the way out of the system, said in words somebody can act on.

Every message here names the thing that failed *and* the next move, which is the
register `app/design/errors.py` and `app/kernel/errors.py` already keep. A
manufacturing package is the last artefact before somebody spends money on metal,
so "export failed" is not an acceptable sentence.
"""

from __future__ import annotations


class ManufactureError(Exception):
    """Base for everything this package refuses to do."""


class DrawingError(ManufactureError):
    """A drawing could not be laid out, scaled or written."""


class ExportError(ManufactureError):
    """A neutral-format export could not be produced."""


class PackageError(ManufactureError):
    """A manufacturing package could not be assembled."""


__all__ = ["DrawingError", "ExportError", "ManufactureError", "PackageError"]
