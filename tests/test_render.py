"""Phase 4.1 and 4.3 — deterministic rendering, section cuts, and render diffs.

Offline: OCCT only, no database, no network, no model. These tests are the
evidence behind the E4 board row, and they are written to be run on the Windows
seat alongside everything else.

The one that matters most is `TestTheRenderIsTheRightWayUp`. The renderer
shipped vertically mirrored on 2026-09-05 and **not one existing check could
see it** — a consistently upside-down image is still byte-identical to itself,
so determinism held; a diff of two mirrored renders is still correct, so 4.3
held; and a wireframe of a plate looks entirely plausible upside down. It is
exactly the "wrong orientation" error that 4.1 says a render hash exists to
catch, which is why the fix went into the projection rather than into the
raster.
"""

from __future__ import annotations

import math

import pytest

from app.kernel.occt.binding import require, symbol
from app.render import Render, render, render_pair, render_views
from app.render.diff import FramesDiffer, diff
from app.render.project import project
from app.render.raster import BACKGROUND, HATCH, to_png
from app.render.section import (
    CANONICAL_SECTIONS,
    Section,
    SectionError,
    cut,
    face_outlines,
    mid_section,
    offset_section,
    render_section,
    section_faces,
    section_named,
)
from app.render.views import ALL_VIEWS, ORTHOGRAPHIC, frame_for, view_named


def _box(dx: float = 60.0, dy: float = 40.0, dz: float = 20.0, at=(0.0, 0.0, 0.0)):
    require()
    return symbol("BRepPrimAPI_MakeBox")(symbol("gp_Pnt")(*at), dx, dy, dz).Shape()


def _plate_with_a_hole(diameter: float = 14.0):
    """A 60x40x20 plate with a bore straight through it in Z."""
    plate = _box()
    axis = symbol("gp_Ax2")(symbol("gp_Pnt")(30.0, 20.0, -5.0), symbol("gp_Dir")(0.0, 0.0, 1.0))
    bore = symbol("BRepPrimAPI_MakeCylinder")(axis, diameter / 2.0, 30.0).Shape()
    operation = symbol("BRepAlgoAPI_Cut")(plate, bore)
    operation.Build()
    return operation.Shape()


def _ink_rows(shot: Render) -> list[int]:
    """Which canvas rows carry any ink, decoded from the published PNG."""
    from app.render.diff import _decode

    pixels = _decode(shot.png)
    return [row for row in range(pixels.shape[0]) if (pixels[row] < 200).any()]


# ---------------------------------------------------------------------------


class TestTheRenderIsTheRightWayUp:
    """The defect no determinism check could see."""

    def test_the_top_of_a_part_is_drawn_at_the_top_of_the_image(self) -> None:
        """OCCT's Ax2 Y axis points down relative to the up vector views.py declares."""
        # An L on its side: a 60x40x20 slab with a tall 60x40x60 tower on top of
        # nothing, so the drawing is unmistakably heavier at one end.
        base = _box(60.0, 40.0, 10.0)
        tower = _box(10.0, 40.0, 50.0, at=(0.0, 0.0, 10.0))
        fused = symbol("BRepAlgoAPI_Fuse")(base, tower)
        fused.Build()

        shot = render(fused.Shape(), "front")
        rows = _ink_rows(shot)
        middle = (min(rows) + max(rows)) / 2.0
        top_half = sum(1 for row in rows if row < middle)
        bottom_half = sum(1 for row in rows if row > middle)
        # The wide base is at the bottom of the part, so the *lower* rows of the
        # image must carry the wide span. Mirrored, this assertion flips.
        assert bottom_half > 0 and top_half > 0
        from app.render.diff import _decode

        pixels = _decode(shot.png)
        widest_row = max(rows, key=lambda row: int((pixels[row] < 200).sum()))
        assert widest_row > middle, "the wide base rendered above the narrow tower"

    def test_hlr_and_the_hand_projection_agree(self) -> None:
        """The guard against `View.to_view_mm` drifting from `project`."""
        view = view_named("front")
        edge_start = (7.0, 0.0, 3.0)
        by_hand = view.to_view_mm(*edge_start)

        # The same point, through HLR: a degenerate box's corner at that spot.
        projected = project(_box(1e-6, 1e-6, 1e-6, at=edge_start), view)
        points = [point for line in projected.visible + projected.hidden for point in line]
        assert points
        nearest = min(points, key=lambda p: (p[0] - by_hand[0]) ** 2 + (p[1] - by_hand[1]) ** 2)
        assert nearest == pytest.approx(by_hand, abs=1e-3)

    def test_the_declared_up_vector_is_the_opposite_of_occts(self) -> None:
        """Stated because it is the whole reason `_flatten` negates y."""
        view = view_named("front")
        ax2 = symbol("gp_Ax2")(
            symbol("gp_Pnt")(0.0, 0.0, 0.0),
            symbol("gp_Dir")(*view.direction),
            symbol("gp_Dir")(*view.right()),
        )
        occt_up = ax2.YDirection()
        declared = view.frame_up()
        assert (occt_up.X(), occt_up.Y(), occt_up.Z()) == pytest.approx(
            tuple(-one for one in declared)
        )


class TestDeterminism:
    def test_the_same_shape_renders_byte_identically(self) -> None:
        assert render(_box(), "iso").png == render(_box(), "iso").png

    def test_a_part_rebuilt_from_nothing_matches(self) -> None:
        assert render(_box(), "front").digest == render(_box(), "front").digest

    def test_a_pocket_changes_the_bytes(self) -> None:
        assert render(_box(), "top").digest != render(_plate_with_a_hole(), "top").digest

    def test_the_digest_is_of_the_published_png(self) -> None:
        import hashlib

        shot = render(_box(), "iso")
        assert shot.digest == hashlib.sha256(shot.png).hexdigest()


class TestFraming:
    def test_several_views_share_one_frame(self) -> None:
        shots = render_views(_box(), ORTHOGRAPHIC)
        frames = {shot.frame for shot in shots.values()}
        assert len(frames) == 1

    def test_a_pair_is_framed_together_so_a_diff_means_something(self) -> None:
        before, after = render_pair(_box(), _plate_with_a_hole(), "top")
        assert before.frame == after.frame

    def test_every_canonical_view_renders(self) -> None:
        shots = render_views(_box(), ALL_VIEWS)
        assert set(shots) == set(ALL_VIEWS)
        assert not any(shot.is_blank for shot in shots.values())

    def test_an_unknown_view_is_refused_with_the_list(self) -> None:
        with pytest.raises(ValueError, match="canonical view"):
            view_named("sideways")

    def test_a_part_with_no_extent_frames_rather_than_dividing_by_zero(self) -> None:
        frame = frame_for((0.0, 0.0, 0.0, 0.0), 100, 100)
        assert frame.scale == 1.0

    def test_pixels_round_half_up_not_to_even(self) -> None:
        """Banker's rounding snaps one end of a line one way and the other the other."""
        frame = frame_for((0.0, 0.0, 10.0, 10.0), 100, 100)
        assert frame.to_pixels(*(0.0, 0.0))[0] == math.floor(
            (0.0 - frame.origin_mm[0]) * frame.scale + 0.5
        )


class TestTheDiffComparesInkNotShade:
    def test_a_new_pocket_arrives_and_nothing_goes(self) -> None:
        before, after = render_pair(_box(), _plate_with_a_hole(), "top")
        change = diff(before, after)
        assert change.arrived > 0
        assert change.gone == 0
        assert 0.0 < change.fraction < 0.5

    def test_an_unchanged_part_diffs_to_nothing(self) -> None:
        before, after = render_pair(_box(), _box(), "iso")
        assert diff(before, after).identical

    def test_two_differently_framed_renders_are_refused(self) -> None:
        """Framed apart, a part 2 mm bigger changes every pixel and says nothing."""
        with pytest.raises(FramesDiffer, match="framed differently"):
            diff(render(_box(60.0), "front"), render(_box(90.0), "front"))

    def test_a_diff_across_two_views_is_refused(self) -> None:
        first, second = render_pair(_box(), _box(), "front")
        object.__setattr__(second, "view", "top")
        with pytest.raises(FramesDiffer, match="different views"):
            diff(first, second)

    def test_the_decoder_refuses_a_png_it_did_not_write(self) -> None:
        import numpy as np

        rgb = to_png(np.zeros((4, 4, 3), dtype=np.uint8))
        from app.render.diff import _decode

        with pytest.raises(ValueError, match="greyscale"):
            _decode(rgb)


class TestWhereToCut:
    def test_a_mid_section_lands_half_way_along_the_axis(self) -> None:
        section = mid_section(_box(60.0, 40.0, 20.0), "x")
        assert section.origin[0] == pytest.approx(30.0)
        assert section.normal == (1.0, 0.0, 0.0)
        assert section.name == "mid-x"

    def test_a_section_that_misses_the_part_is_refused_not_returned_empty(self) -> None:
        """An empty cut looks exactly like a successful cut of a solid part."""
        with pytest.raises(SectionError, match="misses the part"):
            offset_section(_box(), "x", 900.0)

    def test_the_refusal_names_the_range_that_would_work(self) -> None:
        with pytest.raises(SectionError, match="0 to 60"):
            offset_section(_box(), "x", -5.0)

    def test_an_unknown_canonical_section_is_refused_with_the_list(self) -> None:
        with pytest.raises(SectionError, match="mid-x, mid-y, mid-z"):
            section_named(_box(), "mid-diagonal")

    def test_the_canonical_sections_resolve(self) -> None:
        for name in CANONICAL_SECTIONS:
            assert section_named(_box(), name).name == name

    def test_a_natural_view_looks_from_where_the_material_was_taken(self) -> None:
        """A section seen from behind is an ordinary view of a part with a bite out."""
        section = mid_section(_box(), "x")
        view = section.natural_view()
        assert view.direction == pytest.approx((-1.0, 0.0, 0.0))

    def test_an_oblique_plane_falls_back_to_iso_rather_than_guessing(self) -> None:
        oblique = Section("oblique", (0.0, 0.0, 0.0), (0.6, 0.8, 0.0))
        assert oblique.natural_view().name == "iso"


class TestTheCutItself:
    def test_a_mid_cut_removes_half_the_volume(self) -> None:
        from app.kernel.occt.metrology import volume_mm3

        whole = _box(60.0, 40.0, 20.0)
        half = cut(whole, mid_section(whole, "x"))
        assert volume_mm3(half) == pytest.approx(volume_mm3(whole) / 2.0, rel=1e-9)

    def test_the_normal_points_at_what_goes_away(self) -> None:
        """The same convention `catia_split` states; two conventions is how a part mirrors."""
        from app.kernel.occt.metrology import bounding_box_mm

        whole = _box(60.0, 40.0, 20.0)
        remaining = cut(whole, mid_section(whole, "x"))
        # Normal is +x, so the +x half is gone and the survivor spans 0..30.
        assert bounding_box_mm(remaining)["max"][0] == pytest.approx(30.0)

    def test_the_cut_face_is_found_by_geometry_not_by_boolean_history(self) -> None:
        whole = _box(60.0, 40.0, 20.0)
        section = mid_section(whole, "x")
        faces = section_faces(cut(whole, section), section)
        assert len(faces) == 1

    def test_a_bore_through_the_cut_face_leaves_one_face_with_two_wires(self) -> None:
        plate = _plate_with_a_hole()
        section = mid_section(plate, "z")
        faces = section_faces(cut(plate, section), section)
        assert len(faces) == 1
        assert len(face_outlines(faces[0], view_named("bottom"))) == 2


class TestTheSectionIsDrawnHatched:
    def test_a_section_view_carries_hatching(self) -> None:
        """Unhatched, a cut block and a solid block are the same outline."""
        from app.render.diff import _decode

        whole = _box(60.0, 40.0, 20.0)
        shot = render_section(whole, mid_section(whole, "x"))
        pixels = _decode(shot.png)
        assert int((pixels == HATCH).sum()) > 0

    def test_the_hatch_can_be_switched_off(self) -> None:
        from app.render.diff import _decode

        whole = _box()
        plain = render_section(whole, mid_section(whole, "x"), hatched=False)
        assert int((_decode(plain.png) == HATCH).sum()) == 0

    def test_a_bore_is_left_unhatched_by_the_even_odd_rule(self) -> None:
        """Nothing identifies the hole as a hole; the parity does it."""
        from app.render.diff import _decode

        plate = _plate_with_a_hole(20.0)
        section = mid_section(plate, "z")
        pixels = _decode(render_section(plate, section).png)
        centre = pixels[pixels.shape[0] // 2, pixels.shape[1] // 2]
        assert centre == BACKGROUND

    def test_the_outline_is_drawn_over_the_hatch_not_under_it(self) -> None:
        """A hatch line breaking an edge costs the shape the eye reads it from."""
        from app.render.diff import _decode

        whole = _box()
        pixels = _decode(render_section(whole, mid_section(whole, "x")).png)
        # The darkest thing on the canvas is a visible edge, not a hatch line.
        assert int(pixels.min()) == 0

    def test_a_section_render_is_deterministic_like_every_other(self) -> None:
        first = render_section(_box(), mid_section(_box(), "y"))
        second = render_section(_box(), mid_section(_box(), "y"))
        assert first.png == second.png
