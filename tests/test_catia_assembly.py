"""The assembly tools: save a part, place it, constrain it.

Two halves, for the reason `test_catia_com_contract.py` sets out. The mock half
pins the *sequencing* an agent gets wrong -- assembling before saving,
constraining a component that was never added -- because that is what the tool
descriptions have to steer it away from. The stub-COM half pins the calls that
were established against a live V5-R33 and would otherwise drift: the SAFEARRAY
in-parameter, the constraint integers, and the fact that a reference resolves
only under the seat's own name for an origin plane.

Neither half proves CATIA accepts anything. Only a live session does that, and
`Desktop/test-kyrova` holds the run that did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import TOOL_METHODS, CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402
from catia_bridge.mock_catia import MockCatia  # noqa: E402
from catia_bridge.tool_table import TOOLS, check_call  # noqa: E402

ASSEMBLY_TOOLS = (
    "catia_save_part",
    "catia_new_product",
    "catia_add_component",
    "catia_constrain",
    "catia_list_constraints",
)


# --------------------------------------------------------------- wiring ----


def test_every_assembly_tool_is_wired_end_to_end() -> None:
    """A tool the server knows and the daemon does not is refused at runtime."""
    from app.catia.tool_specs import TOOL_SPECS_BY_NAME

    for tool in ASSEMBLY_TOOLS:
        assert tool in TOOL_SPECS_BY_NAME, f"{tool} missing from the server's specs"
        assert tool in TOOLS, f"{tool} missing from the daemon's table"
        assert tool in TOOL_METHODS, f"{tool} has no backend method"
        method = TOOL_METHODS[tool]
        assert hasattr(CatiaCom, method), f"CatiaCom has no {method}"
        assert hasattr(MockCatia, method), f"MockCatia has no {method}"


def test_constraint_kinds_agree_across_the_wire() -> None:
    """The two tables are separate copies; a drift between them fails closed."""
    from app.catia.tool_specs import CONSTRAINT_KINDS as SERVER_KINDS

    from catia_bridge.tool_table import CONSTRAINT_KINDS as DAEMON_KINDS

    assert set(SERVER_KINDS) == set(DAEMON_KINDS)


def test_no_assembly_tool_accepts_a_filesystem_path() -> None:
    """`part` is a name the daemon resolves in its own directory, never a path."""
    schema = TOOLS["catia_add_component"][1]
    assert set(schema["properties"]) == {"part", "count", "name"}
    assert schema["additionalProperties"] is False
    with pytest.raises(Exception):
        check_call(
            "catia_add_component",
            {"part": "Sun", "path": r"C:\Windows\System32"},
            approval_token=None,
        )


# ------------------------------------------------- sequencing, via mock ----


@pytest.fixture()
def mock(tmp_path: Path) -> MockCatia:
    return MockCatia(tmp_path)


def _a_part(mock: MockCatia, name: str) -> None:
    mock.new_part(name=name)
    mock.sketch_circle(plane="XY", diameter_mm=40.0)
    mock.pad(sketch="Sketch.1", length_mm=10.0)


def test_a_part_must_be_saved_before_it_can_be_a_component(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.new_product(name="Stage")
    with pytest.raises(CatiaOperationError) as exc:
        mock.add_component(part="Sun")
    assert "catia_save_part" in str(exc.value)
    assert "Nothing saved yet" in str(exc.value)


def test_components_need_a_product_open(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.save_part(name="Sun")
    with pytest.raises(CatiaOperationError) as exc:
        mock.add_component(part="Sun")
    assert "catia_new_product" in str(exc.value)


def test_saving_then_placing_three_instances(mock: MockCatia) -> None:
    _a_part(mock, "Planet")
    saved = mock.save_part(name="Planet")
    assert saved["part"] == "Planet"

    mock.new_product(name="Stage")
    placed = mock.add_component(part="Planet", count=3)
    assert placed["count"] == 3
    assert placed["added"] == ["Planet.1", "Planet.2", "Planet.3"]


def test_constraining_an_unknown_component_says_what_is_there(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.save_part(name="Sun")
    mock.new_product(name="Stage")
    mock.add_component(part="Sun")
    with pytest.raises(CatiaOperationError) as exc:
        mock.constrain(kind="coincidence", component="Sun.9", to_component="Sun.1")
    assert "Sun.1" in str(exc.value)


def test_only_fix_takes_a_single_component(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.save_part(name="Sun")
    mock.new_product(name="Stage")
    mock.add_component(part="Sun", count=2)

    fixed = mock.constrain(kind="fix", component="Sun.1")
    assert fixed["kind"] == "fix"

    with pytest.raises(CatiaOperationError) as exc:
        mock.constrain(kind="coincidence", component="Sun.2")
    assert "to_component" in str(exc.value)


def test_offset_keeps_its_value_and_coincidence_has_none(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.save_part(name="Sun")
    mock.new_product(name="Stage")
    mock.add_component(part="Sun", count=2)

    mock.constrain(
        kind="offset", component="Sun.2", to_component="Sun.1", plane="YZ", value=42.0
    )
    mock.constrain(kind="coincidence", component="Sun.2", to_component="Sun.1", plane="XY")

    listed = mock.list_constraints()
    assert listed["count"] == 2
    offset = next(c for c in listed["constraints"] if c["kind"] == "offset")
    assert offset["value_mm"] == 42.0
    assert offset["plane"] == "YZ"
    coincidence = next(c for c in listed["constraints"] if c["kind"] == "coincidence")
    assert "value_mm" not in coincidence


def test_a_new_part_leaves_the_product_but_keeps_saved_components(mock: MockCatia) -> None:
    _a_part(mock, "Sun")
    mock.save_part(name="Sun")
    mock.new_product(name="Stage")
    mock.add_component(part="Sun")

    _a_part(mock, "Planet")  # back to modelling
    with pytest.raises(CatiaOperationError):
        mock.list_constraints()
    mock.save_part(name="Planet")
    assert set(mock.saved_parts) == {"Sun", "Planet"}


# ------------------------------------------------ COM calls, via a stub ----


class _Constraint:
    def __init__(self, kind: int, index: int) -> None:
        self.Type = kind
        self.Name = f"Constraint.{index}"
        self.Dimension = _Dimension()


class _Dimension:
    Value = 0.0


class _Constraints:
    def __init__(self) -> None:
        self.items: list[_Constraint] = []
        self.bi_calls: list[tuple[int, str, str]] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Constraint:
        return self.items[index - 1]

    def AddBiEltCst(self, kind: int, first: str, second: str) -> _Constraint:
        self.bi_calls.append((kind, first, second))
        made = _Constraint(kind, len(self.items) + 1)
        self.items.append(made)
        return made

    def AddMonoEltCst(self, kind: int, reference: str) -> _Constraint:
        made = _Constraint(kind, len(self.items) + 1)
        self.items.append(made)
        return made


class _Component:
    def __init__(self, name: str) -> None:
        self.Name = name


class _Products:
    def __init__(self) -> None:
        self.items: list[_Component] = []
        self.added: list[tuple] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> _Component:
        return self.items[index - 1]

    def AddComponentsFromFiles(self, paths, mode: str) -> None:
        # The in-parameter must arrive as a sequence; a bare string is the bug
        # this records, because pywin32 will not marshal one as a SAFEARRAY.
        assert isinstance(paths, (tuple, list)), "paths must be a sequence"
        assert mode == "All"
        self.added.append(tuple(paths))
        self.items.append(_Component(f"Part{len(self.items) + 1}.1"))


class _Root:
    PartNumber = "Stage"

    def __init__(self) -> None:
        self.Products = _Products()
        self._constraints = _Constraints()
        self.asked: list[str] = []

    def Connections(self, name: str) -> _Constraints:
        assert name == "CATIAConstraints"
        return self._constraints

    def CreateReferenceFromName(self, name: str):
        self.asked.append(name)
        # Only the French label resolves on this stub, mirroring the seat the
        # behaviour was measured on.
        if "!Plan " not in name:
            raise RuntimeError("La méthode CreateReferenceFromName a échoué")
        return f"ref:{name}"

    def Update(self) -> None:
        return None


class _ProductDocument:
    Name = "Stage.CATProduct"

    def __init__(self) -> None:
        self.Product = _Root()

    def __getattr__(self, item: str):
        if item == "Part":
            raise AttributeError("a product has no Part")
        raise AttributeError(item)


def _com(tmp_path: Path, document) -> CatiaCom:
    com = CatiaCom.__new__(CatiaCom)
    com.workdir = tmp_path
    com.ui_language = "fr"
    com._document = lambda: document  # type: ignore[method-assign]
    return com


def test_add_component_passes_a_sequence_not_a_string(tmp_path: Path) -> None:
    document = _ProductDocument()
    com = _com(tmp_path, document)
    (com._components_dir() / "Sun.CATPart").write_bytes(b"x")

    com.add_component(part="Sun", count=2)

    assert len(document.Product.Products.added) == 2
    for call in document.Product.Products.added:
        assert isinstance(call, tuple) and call[0].endswith("Sun.CATPart")


class _OpenDocument:
    """A component CATIA still has open, as it is on the second save."""

    def __init__(self, path: str) -> None:
        self.FullName = path
        self.closed = False

    def Close(self) -> None:
        self.closed = True


class _Documents:
    def __init__(self, items: list[_OpenDocument]) -> None:
        self.items = items

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int):
        return self.items[index - 1]


class _PartDocument:
    Name = "Sun.CATPart"

    def __init__(self) -> None:
        self.Part = object()
        self.saved_to: list[str] = []
        self.others: list[_OpenDocument] = []

    def SaveAs(self, path: str) -> None:
        # Reproduces what a live V5-R33 does. CATIA checks its *session*, not
        # the disk: a path it still has loaded is refused with a modal dialog
        # ("le fichier existe deja dans la session"), which blocks COM until the
        # watchdog fires. An existing file on disk is refused the same way.
        for other in self.others:
            if other.FullName.lower() == path.lower():
                raise AssertionError("SaveAs onto a path held in the session wedges CATIA")
        if Path(path).exists():
            raise AssertionError("SaveAs onto an existing file wedges CATIA")
        Path(path).write_bytes(b"catpart")
        self.saved_to.append(path)


def test_saving_the_same_component_twice_does_not_wedge_catia(tmp_path: Path) -> None:
    """The second save is the normal case: an agent revises a part and re-saves.

    The point of the index is that `part` stays 'Sun' while the file behind it
    changes, so the caller's vocabulary never has to know about revisions.
    """
    document = _PartDocument()
    com = _com(tmp_path, document)
    com._app = type("_App", (), {"Documents": _Documents([])})()

    first = com.save_part(name="Sun")
    second = com.save_part(name="Sun")

    assert first["part"] == second["part"] == "Sun"
    assert len(document.saved_to) == 2
    assert document.saved_to[0] != document.saved_to[1], "must not reuse the path"
    # and the component still resolves, to the newest file
    assert com._component_path("Sun").name == Path(document.saved_to[1]).name


def test_a_path_the_session_still_holds_is_never_reused(tmp_path: Path) -> None:
    """CATIA refuses a path it has loaded even when the file is gone from disk."""
    document = _PartDocument()
    com = _com(tmp_path, document)

    held = _OpenDocument(str(com._components_dir() / "Sun.CATPart"))
    document.others = [held]
    com._app = type("_App", (), {"Documents": _Documents([held])})()

    saved = com.save_part(name="Sun")

    assert saved["part"] == "Sun"
    assert Path(document.saved_to[0]).name != "Sun.CATPart"
    assert com._component_path("Sun").is_file()


def test_the_index_survives_being_corrupt(tmp_path: Path) -> None:
    """A damaged index must not take the whole assembly path down with it."""
    document = _PartDocument()
    com = _com(tmp_path, document)
    com._app = type("_App", (), {"Documents": _Documents([])})()
    com._components_dir().mkdir(parents=True, exist_ok=True)
    com._component_index_path().write_text("{not json", encoding="utf-8")

    saved = com.save_part(name="Sun")

    assert saved["part"] == "Sun"
    assert com._component_index() == {"Sun": Path(document.saved_to[0]).name}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Ring gear", "Ring-gear"),
        ("///", "part"),
        (r"..\..\Windows\System32\config\SAM", "Windows-System32-config-SAM"),
    ],
)
def test_save_part_reports_the_name_it_actually_used(
    tmp_path: Path, given: str, expected: str
) -> None:
    """The slug is returned so a rewritten name is visible, not a surprise later
    when catia_add_component is called with the name the model remembers."""
    com = _com(tmp_path, _PartDocument())
    saved = com.save_part(name=given)
    assert saved["part"] == expected
    assert (com._components_dir() / f"{expected}.CATPart").is_file()


def test_add_component_refuses_a_part_that_was_never_saved(tmp_path: Path) -> None:
    com = _com(tmp_path, _ProductDocument())
    with pytest.raises(CatiaOperationError) as exc:
        com.add_component(part="Ghost")
    assert "catia_save_part" in str(exc.value)


def test_a_component_name_cannot_escape_the_component_directory(tmp_path: Path) -> None:
    """`part` is sanitised, so it cannot address a file outside the workdir."""
    com = _com(tmp_path, _ProductDocument())
    with pytest.raises(CatiaOperationError):
        com.add_component(part=r"..\..\Windows\System32\config\SAM")


def test_constraint_integers_are_the_ones_catia_took(tmp_path: Path) -> None:
    document = _ProductDocument()
    com = _com(tmp_path, document)
    (com._components_dir() / "Sun.CATPart").write_bytes(b"x")
    com.add_component(part="Sun", count=2)

    com.constrain(kind="coincidence", component="Part1.1", to_component="Part2.1")
    com.constrain(
        kind="offset", component="Part2.1", to_component="Part1.1", plane="YZ", value=42.0
    )

    kinds = [call[0] for call in document.Product._constraints.bi_calls]
    assert kinds == [2, 1], "coincidence is 2 and offset is 1 on a live V5-R33"
    assert document.Product._constraints.items[-1].Dimension.Value == 42.0


def test_an_unknown_component_is_named_before_catia_is_asked(tmp_path: Path) -> None:
    """CATIA returns a reference for a name that does not exist and only fails on
    use, so the check has to happen here or the error blames the constraint."""
    document = _ProductDocument()
    com = _com(tmp_path, document)
    (com._components_dir() / "Sun.CATPart").write_bytes(b"x")
    com.add_component(part="Sun")

    with pytest.raises(CatiaOperationError) as exc:
        com.constrain(kind="coincidence", component="Ghost.1", to_component="Part1.1")
    assert "Ghost.1" in str(exc.value)
    assert "Part1.1" in str(exc.value)
    assert document.Product._constraints.Count == 0, "nothing should have been created"


def test_references_use_the_seats_own_plane_label(tmp_path: Path) -> None:
    document = _ProductDocument()
    com = _com(tmp_path, document)
    (com._components_dir() / "Sun.CATPart").write_bytes(b"x")
    com.add_component(part="Sun", count=2)

    com.constrain(kind="coincidence", component="Part1.1", to_component="Part2.1", plane="ZX")

    assert document.Product.asked[-1] == "Stage/Part2.1/!Plan zx"


def test_an_unresolvable_plane_says_which_names_it_tried(tmp_path: Path) -> None:
    document = _ProductDocument()
    com = _com(tmp_path, document)
    com.ui_language = "en"
    document.Product.CreateReferenceFromName = lambda name: (_ for _ in ()).throw(
        RuntimeError("nope")
    )
    (com._components_dir() / "Sun.CATPart").write_bytes(b"x")
    com.add_component(part="Sun")

    with pytest.raises(CatiaOperationError) as exc:
        com.constrain(kind="fix", component="Part1.1")
    assert "xy plane" in str(exc.value) and "Plan xy" in str(exc.value)


def test_update_on_a_product_resolves_it_and_reports_mass_in_mm_units(
    tmp_path: Path,
) -> None:
    """`Analyze` reports mm3 and kg; scaling it would repeat the 1e9 bug."""

    class _Analyze:
        Volume = 1_000_000.0
        Mass = 7.86

    document = _ProductDocument()
    document.Product.Analyze = _Analyze()
    com = _com(tmp_path, document)

    result = com.update()

    assert result["updated"] is True
    assert result["volume_mm3"] == 1_000_000.0
    assert result["mass_kg"] == 7.86
    assert result["mass_is_provisional"] is False


def test_an_assembly_with_no_materials_reports_no_mass_rather_than_zero(
    tmp_path: Path,
) -> None:
    class _Analyze:
        Volume = 1_000_000.0
        Mass = 0.0

    document = _ProductDocument()
    document.Product.Analyze = _Analyze()
    com = _com(tmp_path, document)

    result = com.update()

    assert result["mass_kg"] is None
    assert result["mass_is_provisional"] is True
    assert "material" in result["mass_warning"]
