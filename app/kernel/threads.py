"""Thread designations, read rather than guessed.

A thread on a CAD model is an **annotation**, not helical material: it drives the drawing
callout, the tapping operation and the fastener that goes in, and modelling the helix
costs a great deal of geometry to change nothing anybody measures. CATIA works this way
and so does this kernel — see `app.kernel.occt.operations.annotations`.

What *is* needed is the numbers behind the designation, because they are what a clearance
check, a wall-thickness check or a bill of materials asks for. `M10x1.5` carries a
nominal diameter of 10 mm and a pitch of 1.5; `M10` carries the same pitch by ISO 261's
coarse series, which is a table and not an inference.

**A designation this module does not recognise reports its pitch as unknown**, and the
operation says so in words. The alternative — assuming a coarse metric pitch for
`1/4-20 UNC` — produces a tapped hole that is confidently the wrong size, which is worse
than a part that says it does not know.

Backend-neutral on purpose: a CATIA seat parses the same strings and must report the same
numbers, so this sits beside `measurement.py` and `contract.py` rather than under
`occt/`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: ISO 261 coarse-series pitch, in mm, for each nominal metric diameter.
#:
#: The coarse series is what `M10` means with no pitch given — the one every fastener
#: catalogue stocks by default. Fine pitches exist and are always written out
#: (`M10x1.25`), which is why only this series needs a table.
ISO_COARSE_PITCH_MM: Final[dict[float, float]] = {
    1.0: 0.25,
    1.2: 0.25,
    1.4: 0.3,
    1.6: 0.35,
    2.0: 0.4,
    2.5: 0.45,
    3.0: 0.5,
    4.0: 0.7,
    5.0: 0.8,
    6.0: 1.0,
    8.0: 1.25,
    10.0: 1.5,
    12.0: 1.75,
    14.0: 2.0,
    16.0: 2.0,
    18.0: 2.5,
    20.0: 2.5,
    22.0: 2.5,
    24.0: 3.0,
    27.0: 3.0,
    30.0: 3.5,
    33.0: 3.5,
    36.0: 4.0,
    39.0: 4.0,
    42.0: 4.5,
    45.0: 4.5,
    48.0: 5.0,
    52.0: 5.0,
    56.0: 5.5,
    60.0: 5.5,
    64.0: 6.0,
}

#: `M10`, `M10x1.5`, `M10×1.25-6H`. The separator may be an ASCII `x`, a capital `X` or
#: the multiplication sign a European drawing office actually types.
_METRIC_RE: Final = re.compile(
    r"^M\s*(?P<diameter>\d+(?:\.\d+)?)\s*(?:[x×X]\s*(?P<pitch>\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)

#: The height of an ISO 68-1 fundamental triangle, as a multiple of the pitch. The minor
#: diameter of an internal thread is `D - 2 × (5/8) × H`, which reduces to the constant
#: below — the number a tapping drill chart is built from.
_MINOR_FACTOR: Final = 1.0825


@dataclass(frozen=True)
class ThreadSpec:
    """What a designation says, and what it does not.

    `pitch_mm` is `None` when the designation is not one this module reads and the caller
    supplied none. Every consumer must carry that through as "unknown" rather than
    substituting a default — the whole point of the type.
    """

    designation: str
    nominal_diameter_mm: float | None = None
    pitch_mm: float | None = None

    #: Why the numbers are missing, when they are. Empty when they are known.
    unrecognised: str = ""

    @property
    def is_understood(self) -> bool:
        return self.nominal_diameter_mm is not None and self.pitch_mm is not None

    def minor_diameter_mm(self) -> float | None:
        """The tapping-drill diameter — what an internal thread is cut into.

        `None` when the designation was not understood, because a minor diameter derived
        from a guessed pitch would be a specific, checkable, wrong number.
        """
        if self.nominal_diameter_mm is None or self.pitch_mm is None:
            return None
        return self.nominal_diameter_mm - _MINOR_FACTOR * self.pitch_mm

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"designation": self.designation}
        if self.nominal_diameter_mm is not None:
            payload["nominal_diameter_mm"] = round(self.nominal_diameter_mm, 6)
        if self.pitch_mm is not None:
            payload["pitch_mm"] = round(self.pitch_mm, 6)
            minor = self.minor_diameter_mm()
            if minor is not None:
                payload["minor_diameter_mm"] = round(minor, 6)
        if self.unrecognised:
            payload["unrecognised"] = self.unrecognised
        return payload


def parse_designation(designation: str, *, pitch_mm: float | None = None) -> ThreadSpec:
    """Read a thread designation, honouring an explicitly supplied pitch.

    An explicit `pitch_mm` always wins, including over a pitch written into the
    designation itself: the caller who passes both has said something more specific than
    the string, and silently preferring the string would ignore an argument the operation
    documents as meaningful.
    """
    text = str(designation).strip()
    if not text:
        return ThreadSpec(
            designation=text,
            pitch_mm=pitch_mm,
            unrecognised="no designation was given",
        )

    match = _METRIC_RE.match(text)
    if match is None:
        return ThreadSpec(
            designation=text,
            pitch_mm=pitch_mm,
            unrecognised=(
                "only ISO metric designations (M6, M10x1.5) are read here, so the "
                "diameter and pitch behind this one are not known"
            ),
        )

    diameter = float(match.group("diameter"))
    written = match.group("pitch")
    resolved = (
        float(pitch_mm)
        if pitch_mm is not None
        else float(written)
        if written is not None
        else ISO_COARSE_PITCH_MM.get(diameter)
    )

    if resolved is None:
        return ThreadSpec(
            designation=text,
            nominal_diameter_mm=diameter,
            unrecognised=(
                f"M{diameter:g} is not in the ISO 261 coarse series, so its pitch is not "
                "implied. Write it out, as in M10x1.5, or pass pitch_mm"
            ),
        )
    return ThreadSpec(
        designation=text, nominal_diameter_mm=diameter, pitch_mm=resolved
    )


__all__ = ["ISO_COARSE_PITCH_MM", "ThreadSpec", "parse_designation"]
