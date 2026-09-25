"""`catia_fillet_variable`'s COM calls, as the seat's type library states them.

Read off V5-R33's PARTITF type library on 2026-09-25 (through ITypeInfo, no
gencache): `AddNewSolidEdgeFilletWithVaryingRadius(iEdgeToFillet, iPropagMode,
iVariationMode, iDefaultRadius)` takes four arguments, and `VarRadEdgeFillet`
has `FilletVariation` and `AddImposedVertex(iVertex, iRadius)` -- no
`VariationType`, no `AddVariationPoint`. The bridge called the factory with
three arguments ("Nombre de parametres non valide" every time) and then two
members that do not exist, so the tool had never built a fillet.

Structural, because the bridge is Windows-only COM: these pin the call shapes
the type library states so a recalled signature cannot come back.
"""

from __future__ import annotations

import ast
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"


def _fillet_variable() -> ast.FunctionDef:
    tree = ast.parse((BRIDGE / "com" / "part_design.py").read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "fillet_variable"
    )


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        c
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == name
    ]


class TestTheCallsMatchTheTypeLibrary:
    def test_the_factory_gets_four_arguments(self) -> None:
        (call,) = _calls(_fillet_variable(), "AddNewSolidEdgeFilletWithVaryingRadius")
        assert len(call.args) == 4

    def test_points_are_imposed_through_the_member_that_exists(self) -> None:
        body = _fillet_variable()
        assert _calls(body, "AddImposedVertex")
        assert not _calls(body, "AddVariationPoint")

    def test_no_variation_type_property_is_set(self) -> None:
        body = _fillet_variable()
        assigned = [
            target.attr
            for node in ast.walk(body)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
        ]
        assert "VariationType" not in assigned
