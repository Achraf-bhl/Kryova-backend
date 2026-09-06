"""An assembly tool addresses the product; a part tool sent to one is refused by name.

Measured on ladder prompt S2 turn 2, 2026-09-06, the first assembly ever built
on the seat. With the product active, `catia_constrain` reached the assembly
and `catia_list_features` was refused as "not a part". The agent reopened the
shaft to read it -- and then `catia_constrain` reported the assembly
"contains: (none)", because the scoped document was now the shaft. Six of
twenty rounds flip-flopping between two documents that were both there, both
owned, and both needed.

Two rules close it, and both are decided on the server where the rows are:

* an Assembly Design tool is scoped to the conversation's product **whatever
  document is active** -- it can mean nothing else;
* a part-geometry tool sent while a product is active is refused **with the
  parts by name**, which is the one thing the daemon's "activate the CATPart"
  could not say.

Everything else keeps addressing the active document.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.catia import dispatch
from app.catia.tool_specs import CATIA_TOOL_SPECS, TOOL_SPECS_BY_NAME
from tests.test_catia_dispatch import run, wired  # noqa: F401 - fixture re-export


def _reply(name: str, suffix: str = ".CATPart") -> dict[str, Any]:
    return {"doc_name": name, "remote_path": f"C:\\work\\{name}{suffix}", "features": []}


def _assembly_of_two(wired) -> None:
    # The scripted device's default reply names every part "Bracket"; say
    # which part this is, as the daemon would.
    wired["connection"].replies["catia_new_part"] = _reply("Shaft")
    run(wired, "catia_new_part", {"name": "Shaft"})
    wired["connection"].replies["catia_new_part"] = _reply("Bushing")
    run(wired, "catia_new_part", {"name": "Bushing"})
    wired["connection"].replies["catia_product_create"] = _reply("Assembly", ".CATProduct") | {
        "part_number": "Assembly",
        "components": 0,
    }
    run(wired, "catia_product_create", {"name": "Assembly"})
    wired["connection"].calls.clear()


def _sent(wired, tool: str) -> dict[str, Any]:
    return [c for c in wired["connection"].calls if c["tool"] == tool][0]


class TestTheSpecCarriesItsWorkbench:
    def test_every_spec_names_one(self) -> None:
        assert all(spec.workbench for spec in CATIA_TOOL_SPECS)

    def test_the_assembly_tools_say_assembly_design(self) -> None:
        for name in ("catia_constrain", "catia_component_add", "catia_product_create"):
            assert TOOL_SPECS_BY_NAME[name].workbench == "Assembly Design"

    def test_the_clash_check_is_dmu_and_counts_as_assembly(self) -> None:
        """The workbench the registry files it under is not Assembly Design,
        and a clash check of one part is not a thing -- so the scoping rule
        has to know both."""
        assert TOOL_SPECS_BY_NAME["catia_assembly_clash"].workbench == "DMU Navigator"
        assert "DMU Navigator" in dispatch._ASSEMBLY_WORKBENCHES

    def test_the_part_tools_say_so_too(self) -> None:
        assert TOOL_SPECS_BY_NAME["catia_pad"].workbench == "Part Design"
        assert TOOL_SPECS_BY_NAME["catia_sketch_polyline"].workbench == "Sketcher"


class TestAssemblyToolsAddressTheProduct:
    def test_while_the_product_is_active(self, wired) -> None:
        _assembly_of_two(wired)
        run(wired, "catia_constrain", {"kind": "fix", "elements": ["Shaft"]})
        assert _sent(wired, "catia_constrain")["document"]["doc_name"] == "Assembly"

    def test_while_a_part_is_active(self, wired) -> None:
        """The flip-flop: reopening the shaft to read it must not send the
        next constraint to the shaft."""
        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Shaft")
        run(wired, "catia_open_document", {"name": "Shaft"})
        wired["connection"].calls.clear()
        run(wired, "catia_constrain", {"kind": "fix", "elements": ["Shaft"]})
        assert _sent(wired, "catia_constrain")["document"]["doc_name"] == "Assembly"

    def test_the_clash_check_too(self, wired) -> None:
        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Shaft")
        run(wired, "catia_open_document", {"name": "Shaft"})
        wired["connection"].calls.clear()
        run(wired, "catia_assembly_clash", {})
        assert _sent(wired, "catia_assembly_clash")["document"]["doc_name"] == "Assembly"

    def test_with_no_product_they_address_the_active_document(self, wired) -> None:
        """Nothing here invents a product; without one the daemon's own
        refusal -- call catia_product_create -- is the right answer."""
        wired["connection"].replies["catia_new_part"] = _reply("Shaft")
        run(wired, "catia_new_part", {"name": "Shaft"})
        wired["connection"].calls.clear()
        run(wired, "catia_constrain", {"kind": "fix", "elements": ["Shaft"]})
        assert _sent(wired, "catia_constrain")["document"]["doc_name"] == "Shaft"


class TestPartToolsAndTheProduct:
    def test_a_part_tool_sent_to_the_product_is_refused_with_the_parts_by_name(
        self, wired
    ) -> None:
        _assembly_of_two(wired)
        with pytest.raises(dispatch.CatiaError) as raised:
            run(wired, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
        message = str(raised.value)
        assert "Assembly" in message
        assert "Shaft" in message and "Bushing" in message
        assert "catia_open_document name=" in message
        # Refused here, before the daemon: nothing was sent.
        assert not [c for c in wired["connection"].calls if c["tool"] == "catia_pad"]

    def test_a_sketch_tool_is_refused_the_same_way(self, wired) -> None:
        _assembly_of_two(wired)
        with pytest.raises(dispatch.CatiaError, match="works on a part"):
            run(wired, "catia_sketch_create", {"support": "XY"})

    def test_a_part_tool_goes_to_the_active_part_once_one_is_active(self, wired) -> None:
        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Bushing")
        run(wired, "catia_open_document", {"name": "Bushing"})
        wired["connection"].calls.clear()
        run(wired, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
        assert _sent(wired, "catia_pad")["document"]["doc_name"] == "Bushing"

    def test_a_read_on_the_product_is_left_to_the_daemon(self, wired) -> None:
        """`catia_measure` is filed under Part Design and on a product it is
        the assembly's mass, rolled up through `Product.Analyze`. Only
        *mutations* of part geometry are refused here."""
        _assembly_of_two(wired)
        run(wired, "catia_measure", {})
        assert _sent(wired, "catia_measure")["document"]["doc_name"] == "Assembly"

    def test_with_no_parts_yet_the_refusal_says_to_make_one(self, wired) -> None:
        wired["connection"].replies["catia_product_create"] = _reply(
            "Assembly", ".CATProduct"
        ) | {"part_number": "Assembly", "components": 0}
        run(wired, "catia_product_create", {"name": "Assembly"})
        with pytest.raises(dispatch.CatiaError, match="catia_new_part"):
            run(wired, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})


class TestTheConstraintVocabularySaysHowToSpellAReference:
    def test_component_slash_geometry(self) -> None:
        text = TOOL_SPECS_BY_NAME["catia_constrain"].parameters["properties"]["elements"][
            "description"
        ]
        assert "Component/Geometry" in text
        assert "Shaft/Plan xy" in text

    def test_the_coaxial_recipe_is_stated(self) -> None:
        """What the agent guessed at for three rounds."""
        text = TOOL_SPECS_BY_NAME["catia_constrain"].parameters["properties"]["elements"][
            "description"
        ]
        assert "coaxial" in text
        # The recipe itself, not the plane names loose: two pairs of origin
        # planes, named as component/plane on both sides.
        assert "Shaft/Plan yz with Bushing/Plan yz" in text
        assert "Shaft/Plan zx with Bushing/Plan zx" in text

    def test_the_guessed_syntax_is_named_as_wrong(self) -> None:
        text = TOOL_SPECS_BY_NAME["catia_constrain"].parameters["properties"]["elements"][
            "description"
        ]
        assert "Shaft@axis" in text


class TestTheCheckpointFollowsTheTarget:
    """The snapshot filed as a mutation's undo is of the document the mutation
    changes -- `_auto_checkpoint` says why this is the worst place to disagree.

    With the shaft active and the assembly owned, `catia_constrain` is sent to
    the assembly. Its auto-checkpoint must be of the assembly too: a snapshot
    of the shaft filed as the constraint's undo would, on restore, overwrite
    the shaft rather than recover the assembly.
    """

    def test_a_mutation_scoped_to_the_product_checkpoints_the_product(
        self, wired, db_session
    ) -> None:
        from sqlalchemy import select

        from app.models.catia import CatiaCheckpoint, CatiaDocument

        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Shaft")
        run(wired, "catia_open_document", {"name": "Shaft"})
        before = set(db_session.scalars(select(CatiaCheckpoint.id)))

        run(wired, "catia_constrain", {"kind": "fix", "elements": ["Shaft"]})

        product = db_session.scalar(
            select(CatiaDocument).where(CatiaDocument.doc_name == "Assembly")
        )
        shaft = db_session.scalar(select(CatiaDocument).where(CatiaDocument.doc_name == "Shaft"))
        assert shaft.is_active is True
        new = [
            c
            for c in db_session.scalars(select(CatiaCheckpoint))
            if c.id not in before and c.label == "before catia_constrain"
        ]
        assert len(new) == 1
        assert new[0].document_id == product.id

    def test_the_checkpoint_frame_names_the_product_too(self, wired) -> None:
        """Not only the row: the daemon is told which document to snapshot."""
        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Shaft")
        run(wired, "catia_open_document", {"name": "Shaft"})
        wired["connection"].calls.clear()
        run(wired, "catia_constrain", {"kind": "fix", "elements": ["Shaft"]})
        snapshot = [c for c in wired["connection"].calls if c["tool"] == "catia_checkpoint"][0]
        assert snapshot["document"]["doc_name"] == "Assembly"

    def test_a_part_mutation_still_checkpoints_the_part(self, wired) -> None:
        """The rule must not drag every checkpoint to the product."""
        _assembly_of_two(wired)
        wired["connection"].replies["catia_open_document"] = _reply("Bushing")
        run(wired, "catia_open_document", {"name": "Bushing"})
        wired["connection"].calls.clear()
        run(wired, "catia_pad", {"sketch": "Sketch.1", "length_mm": 10})
        snapshot = [c for c in wired["connection"].calls if c["tool"] == "catia_checkpoint"][0]
        assert snapshot["document"]["doc_name"] == "Bushing"
