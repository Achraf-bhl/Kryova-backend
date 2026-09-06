"""A discarded feature takes its sketch with it, and the refusal says so.

Measured on the seat, 2026-09-06, ladder prompt PRO1 run 3. A pad from an
invalid profile was refused, and the refusal ended with the advice this file
exists because of:

    Read the sketch with catia_list_features, and draw one profile per sketch
    if in doubt.

The very next call came back:

    No sketch named 'CBody Profile' in this part. It has: (none).

CATIA makes a profile a child of the feature built from it -- a padded sketch
moves under the pad in the tree -- so `_discard_failed_feature`, which exists
because a feature left in an error state kills every later `Update`, takes the
drawing with it. The cleanup is right and stays; what was wrong is that nothing
said the sketch had gone, so the advice pointed at something that no longer
existed and the agent spent a round discovering that.

The refusal now checks whether the profile survived and, when it has not, says
so and says to draw it again with `catia_sketch_polyline`, which takes the
whole outline in one call.

Offline: fake parts, no CATIA.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402


class _Part:
    def __init__(self, *, updates: bool, sketches: set[str]) -> None:
        self._updates = updates
        self.sketches = set(sketches)

    def Update(self) -> None:  # noqa: N802 - COM spelling
        if not self._updates:
            raise RuntimeError("La methode Update a echoue")


class _Com(CatiaCom):
    """Only the four hooks `_update_or_discard` reaches for."""

    def __init__(self, *, updates: bool, sketches: set[str], takes_sketch: bool) -> None:
        self.part = _Part(updates=updates, sketches=sketches)
        self.takes_sketch = takes_sketch
        self.discarded: list[object] = []

    def _part(self):  # type: ignore[override]
        return self.part

    def _discard_failed_feature(self, shape):  # type: ignore[override]
        self.discarded.append(shape)
        if self.takes_sketch:
            # What CATIA really does: the profile is a child of the feature.
            self.part.sketches.clear()

    def _profile_survived(self, sketch: str) -> bool:  # type: ignore[override]
        return not sketch or sketch in self.part.sketches


ADVICE = "CATIA could not extrude CBody Profile into a solid."


class TestWhenTheProfileIsTakenWithIt:
    def test_the_refusal_says_the_sketch_went_too(self) -> None:
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=True)
        with pytest.raises(CatiaOperationError) as raised:
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        message = str(raised.value)
        assert "'CBody Profile' went with it" in message
        assert "child of the feature" in message

    def test_it_says_how_to_draw_it_again(self) -> None:
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=True)
        with pytest.raises(CatiaOperationError) as raised:
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        assert "catia_sketch_polyline" in str(raised.value)

    def test_the_caller_s_own_advice_is_still_there(self) -> None:
        """The sentence about *why* the pad failed is the useful half."""
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=True)
        with pytest.raises(CatiaOperationError) as raised:
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        assert ADVICE in str(raised.value)

    def test_the_feature_is_still_discarded(self) -> None:
        """The cleanup is why this path exists at all -- a feature left in an
        error state fails every later Update."""
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=True)
        with pytest.raises(CatiaOperationError):
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        assert len(com.discarded) == 1


class TestWhenTheProfileSurvives:
    def test_nothing_extra_is_said(self) -> None:
        """A refusal that claims the sketch is gone when it is still there
        sends the agent to redraw work it already has."""
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=False)
        with pytest.raises(CatiaOperationError) as raised:
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        assert "went with it" not in str(raised.value)

    def test_the_part_is_still_reported_as_buildable(self) -> None:
        com = _Com(updates=False, sketches={"CBody Profile"}, takes_sketch=False)
        with pytest.raises(CatiaOperationError, match="still buildable"):
            com._update_or_discard(object(), ADVICE, profile="CBody Profile")

    def test_an_operation_with_no_profile_says_nothing_about_one(self) -> None:
        """A fillet or a chamfer consumes no sketch."""
        com = _Com(updates=False, sketches=set(), takes_sketch=True)
        with pytest.raises(CatiaOperationError) as raised:
            com._update_or_discard(object(), "A fillet failed.")
        assert "went with it" not in str(raised.value)


class TestASuccessfulUpdate:
    def test_discards_nothing_and_raises_nothing(self) -> None:
        com = _Com(updates=True, sketches={"CBody Profile"}, takes_sketch=True)
        com._update_or_discard(object(), ADVICE, profile="CBody Profile")
        assert com.discarded == []
        assert com.part.sketches == {"CBody Profile"}


class TestTheOperationsThatConsumeAProfilePassItIn:
    @pytest.mark.parametrize("operation", ["pad", "pocket"])
    def test_the_profile_reaches_the_refusal(self, operation: str) -> None:
        """A report the call site does not pass is a report nobody sees."""
        import inspect

        source = inspect.getsource(getattr(CatiaCom, operation))
        assert "profile=sketch" in source
