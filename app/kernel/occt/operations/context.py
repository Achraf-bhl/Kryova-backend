"""What every operation handler is given, and how raw arguments become geometry.

An operation handler is `(BuildContext, arguments) -> result`. The context carries the
document being built and the few services every handler needs; the coercion helpers turn
the registry's argument vocabulary — points as lists or objects, optional directions,
named edge groups — into OCCT types exactly once, here, rather than in each handler.

**Coercion refuses rather than guesses.** A point that is not a point, a length that is
missing on a shape that needs one: each raises with the argument named and the accepted
form spelled out. The alternative is a default that silently builds the wrong part,
which is the failure mode this whole kernel is arranged to make impossible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.measurement import Detail
from app.kernel.occt.binding import symbol
from app.kernel.occt.document import Feature, PartDocument

#: Direction used when a caller omits one. +Z is the extrusion axis for every primitive
#: in the registry's vocabulary, so it is the axis a user who says nothing means.
DEFAULT_AXIS: tuple[float, float, float] = (0.0, 0.0, 1.0)

#: Below this, a direction vector has no meaningful orientation and is treated as absent
#: rather than normalised into a division by zero.
MIN_DIRECTION_LENGTH: float = 1e-12


@dataclass
class BuildContext:
    """The document under construction, plus how much to measure after each step."""

    document: PartDocument | None = None

    #: How much post-state to compute per mutating call. `Detail.FULL` is right for
    #: interactive work — the agent is prompted to react to what it sees and cannot
    #: react to a number it was not given. A bulk replay should lower it; see
    #: `app.kernel.measurement.Detail`.
    detail: Detail = Detail.FULL

    def require_document(self) -> PartDocument:
        if self.document is None:
            raise GeometryError(
                "No document is open. A plan starts with catia_new_part; this runner "
                "was asked to build something before one existed."
            )
        return self.document

    def require_shape(self, tool: str) -> Any:
        document = self.require_document()
        if document.shape is None:
            raise GeometryError(
                f"{tool} needs existing geometry to act on, and nothing has been built "
                f"in {document.name} yet."
            )
        return document.shape

    def result_for(self, feature: Feature) -> dict[str, Any]:
        """Post-state in the shape both backends promise.

        `feature` first because the executor binds late-bound names from it; the
        measurement alongside because the agent is prompted to react to what it sees.
        """
        document = self.require_document()
        payload: dict[str, Any] = {"feature": feature.catia_style_name}
        payload.update(document.measure(detail=self.detail))
        return payload


# -- argument coercion --------------------------------------------------------


def as_point(value: Any, *, argument: str = "point") -> tuple[float, float, float]:
    """`[x, y, z]` or `{x, y, z}` → a tuple. Absent means the origin."""
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, Mapping):
        return (
            float(value.get("x", 0.0)),
            float(value.get("y", 0.0)),
            float(value.get("z", 0.0)),
        )
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    raise GeometryError(
        f"{argument} must be [x, y, z] or {{x, y, z}} in millimetres, got {value!r}."
    )


def as_direction(value: Any, *, argument: str = "axis") -> tuple[float, float, float]:
    """A direction vector, defaulting to +Z. A zero vector is treated as absent."""
    if value is None:
        return DEFAULT_AXIS
    vector = as_point(value, argument=argument)
    if sum(component * component for component in vector) < MIN_DIRECTION_LENGTH:
        return DEFAULT_AXIS
    return vector


def as_positive_length(value: Any, *, argument: str, tool: str) -> float:
    if value is None:
        raise GeometryError(f"{tool} needs {argument} and none was given.")
    length = float(value)
    if length <= 0.0:
        raise GeometryError(
            f"{tool} needs a positive {argument}; got {length}. A zero-sized feature is "
            "not a feature — remove it from the design instead."
        )
    return length


def point(xyz: tuple[float, float, float]) -> Any:
    return symbol("gp_Pnt")(*xyz)


def direction(xyz: tuple[float, float, float]) -> Any:
    return symbol("gp_Dir")(*xyz)


def frame(origin: tuple[float, float, float], axis: tuple[float, float, float]) -> Any:
    """A local coordinate system: where a primitive sits and which way it points."""
    return symbol("gp_Ax2")(point(origin), direction(axis))


def feature_name(arguments: Mapping[str, Any], fallback: str) -> str:
    """The design's name for what is being built, or a readable default."""
    return str(arguments.get("name") or fallback)


def build_or_raise(maker: Any, *, tool: str, detail: str) -> Any:
    """Run an OCCT algorithm and turn either failure mode into a useful error.

    OCCT signals failure two ways and both must be caught: a shape it cannot make
    usually leaves `IsDone()` false, while an input it cannot accept at all raises
    `Standard_Failure` straight out of `Build()`. Letting the raw OCCT exception escape
    would put a C++ type and no advice in front of the agent, which is exactly what this
    codebase's error conventions exist to prevent.
    """
    try:
        maker.Build()
        done = maker.IsDone()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(f"{tool} could not run: {exc}. {detail}") from exc
    if not done:
        raise GeometryError(f"{tool} did not produce a shape. {detail}")
    return maker.Shape()


__all__ = [
    "DEFAULT_AXIS",
    "MIN_DIRECTION_LENGTH",
    "BuildContext",
    "as_direction",
    "as_point",
    "as_positive_length",
    "build_or_raise",
    "direction",
    "feature_name",
    "frame",
    "point",
]
