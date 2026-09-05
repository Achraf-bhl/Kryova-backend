"""Faces the mould cannot reach — master plan 3.2's undercut detection.

**An undercut is not the same failure as insufficient draft, and conflating them is the
usual mistake.** Draft asks whether a face is angled enough to slide off the tool.
Undercut asks whether the tool can get to it at all. A face can have textbook draft and
still be undercut, because something else on the part stands in front of it — a boss
overhanging a pocket, a snap hook, a hole through a side wall. Draft is fixed by tilting
a wall; an undercut is fixed by a side-action, a lifter, a split line moved, or a
redesign. They are different conversations and this module answers the second one.

**The test is visibility, done with rays.** From a point just outside each face, one ray
is fired along the pull direction and one against it. If the part's own material blocks
*both*, no half of a straight-pull two-part tool can reach that face, and it is reported
as undercut. If either ray escapes, the face comes out with one half or the other.

Three honest limits, all of them the method's rather than the implementation's:

* **One point per face.** A face is usually wholly reachable or wholly hidden, and one
  sample settles it. A large face occluded over only part of its area is not caught. The
  report says how many faces it tested so this is visible rather than assumed.
* **Straight two-part pull only.** Side-actions, collapsing cores and rotating unscrew
  cores all defeat this test, correctly — they exist precisely to make undercuts
  mouldable. A face reported here is undercut *for a simple tool*, which is the question
  worth asking first because it is the one that is free to fix.
* **The part is checked against itself.** No tool geometry is involved, so a face can be
  reachable in this test and still be blocked by a real mould's own walls.
"""

from __future__ import annotations

from typing import Any

from app.kernel.interrogation import UndercutReport, unit_vector
from app.kernel.occt.binding import require
from app.kernel.occt.interrogate.raycast import escape_point, is_blocked, opposite
from app.kernel.occt.interrogate.sampling import sample_face_centre
from app.kernel.occt.topology import faces


def find_undercuts(
    shape: Any,
    pull_direction: tuple[float, float, float] | list[float],
) -> UndercutReport:
    """Faces reachable from neither half of a straight-pull tool.

    A face whose representative point cannot be found — a sliver, a degenerate patch — is
    counted as untested rather than as clear. Reporting it as clear would be the false
    green this codebase refuses everywhere else: the one face nobody could check is
    exactly the one worth checking.
    """
    require()
    pull = unit_vector(pull_direction)
    against = opposite(pull)

    undercut: list[int] = []
    tested = 0
    untested = 0

    for index, face in enumerate(faces(shape)):
        surface_point = sample_face_centre(face)
        if surface_point is None:
            untested += 1
            continue

        origin = escape_point(surface_point.point, surface_point.normal)
        tested += 1
        if is_blocked(shape, origin, pull) and is_blocked(shape, origin, against):
            undercut.append(index)

    return UndercutReport(
        pull_direction=pull,
        undercut_faces=tuple(undercut),
        tested=tested,
        untested=untested,
    )


__all__ = ["find_undercuts"]
