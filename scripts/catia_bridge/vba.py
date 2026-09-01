"""A closed library of VBScript snippets, run through `SystemService.Evaluate`.

**Read this before adding anything here.**

`SystemService.Evaluate` is CATIA's arbitrary-code hatch: hand it a string and
CATIA runs it with the desktop user's full authority. `docs/CATIA_BRIDGE_SETUP.md`
says it "must never be added" because it "would turn one prompt injection into
remote code execution on the workstation". That reasoning is correct and it still
stands -- for *model-supplied* script text.

What this module does is narrower, and the distinction is the whole design:

* Every script is a **module-level constant in this file**, written by hand and
  reviewed like any other code. There is no code path that builds one from a
  string at run time.
* Arguments never reach the script through text. They are passed as COM values
  in `Evaluate`'s fourth parameter, the way a prepared statement passes
  parameters, so there is nothing to escape and nothing to inject into.
* `run` refuses any script that is not one of the frozen constants, so a future
  caller cannot pass its own.

The reason this is needed at all: a whole class of CATIA methods return their
result through a SAFEARRAY out-parameter -- `Measurable.GetCOG`,
`Measurable.GetPoint`, `Analyze.GetGravityCenter`. pywin32's late binding cannot
marshal one. It accepts the call, runs it, and leaves the list you handed it
untouched, so the caller reads back the zeros it initialised and believes it
measured something. This is a known limitation, and running a small VBScript
that does the out-parameter dance and returns a plain string is the established
workaround (it is how `pycatia` solves the same problem).

Early binding via `makepy` would also fix it, and is refused for a separate good
reason: it writes into pywin32's shared `gen_py` cache and changes how every
*other* early-binding application on the workstation resolves CATIA.
"""

from __future__ import annotations

from typing import Any

#: `CATScriptLanguage`'s first member. The IDL declares
#: `enum CATScriptLanguage { CATVBScriptLanguage, CATVBALanguage, ... }`, so
#: VBScript is 0.
VBSCRIPT = 0

#: Centre of gravity of anything measurable, in millimetres.
#:
#: One snippet serves both callers because of a convenient property: the centre
#: of gravity of a *point* is the point. `Measurable.GetPoint` looks like the
#: right call for reading an extremum's position and it is not -- a live
#: V5-6R2023 answers `La methode GetPoint a echoue` for a
#: `HybridShapeExtremum`, while `GetCOG` on the same reference returns its
#: coordinates. So bodies and points both go through here.
#:
#: **The result is already in millimetres.** That is worth stating because the
#: neighbouring interface is not: `Measurable.Volume` and `Measurable.Area`
#: report SI (a 100 mm cube gives `0.001` and `0.06`), while `GetCOG` on the
#: same object gives millimetres. Measured on live CATIA, both ways. No
#: conversion belongs here.
CENTRE_OF_GRAVITY = """\
Function KryovaCentreOfGravity(part, element)
    Dim spa, measurable, cog(2)
    Set spa = part.Parent.GetWorkbench("SPAWorkbench")
    Set measurable = spa.GetMeasurable(part.CreateReferenceFromObject(element))
    measurable.GetCOG cog
    KryovaCentreOfGravity = CStr(cog(0)) & ";" & CStr(cog(1)) & ";" & CStr(cog(2))
End Function
"""

#: Start, middle and end of an edge, nine numbers in millimetres.
#:
#: This is what makes edges *selectable by meaning*: an edge reference from
#: `Selection.Search` is anonymous, and the only way to say "the top edges" is
#: to measure where each one actually is. `GetPointsOnCurve` fills a 9-slot
#: SAFEARRAY -- exactly the out-parameter shape pywin32 cannot marshal -- so it
#: goes through here like `GetCOG` does. Millimetres, measured live.
POINTS_ON_CURVE = """\
Function KryovaPointsOnCurve(part, ref)
    Dim spa, measurable, pts(8), out, i
    Set spa = part.Parent.GetWorkbench("SPAWorkbench")
    Set measurable = spa.GetMeasurable(ref)
    measurable.GetPointsOnCurve pts
    out = ""
    For i = 0 To 8
        If i > 0 Then out = out & ";"
        out = out & CStr(pts(i))
    Next
    KryovaPointsOnCurve = out
End Function
"""

#: Every solid edge of the part in ONE round trip: search, measure, report.
#:
#: The per-edge variant above is correct but O(edges) Evaluate calls, and a
#: padded gear has a thousand solid edges -- measured live, classifying its
#: edges one call at a time blew straight through the daemon's 30 s watchdog.
#: This does the whole sweep inside CATIA and returns one line per solid edge:
#: `<selection index>;<9 coordinates>`. The search QUERY is a parameter, not
#: script text -- the caller picks the localized grammar. Edges that refuse to
#: be measured are skipped rather than failing the batch.
EDGE_MAP = """\
Function KryovaEdgeMap(part, query, scopeShape)
    Dim doc, sel, spa, i, j, out, ref, m, pts(8), line
    Set doc = part.Parent
    Set sel = doc.Selection
    sel.Clear
    sel.Add scopeShape
    sel.Search query
    Set spa = doc.GetWorkbench("SPAWorkbench")
    out = ""
    For i = 1 To sel.Count2
        If InStr(sel.Item2(i).Type, "TriDim") > 0 Then
            On Error Resume Next
            Err.Clear
            line = ""
            Set ref = sel.Item2(i).Reference
            Set m = spa.GetMeasurable(ref)
            m.GetPointsOnCurve pts
            If Err.Number = 0 Then
                line = CStr(i)
                For j = 0 To 8
                    line = line & ";" & CStr(pts(j))
                Next
                out = out & line & vbLf
            End If
            On Error GoTo 0
        End If
    Next
    KryovaEdgeMap = out
End Function
"""

#: Every script this module will run, and the function each one exposes.
#: `run` checks membership, so nothing outside this mapping can be evaluated.
_ALLOWED: dict[str, str] = {
    CENTRE_OF_GRAVITY: "KryovaCentreOfGravity",
    POINTS_ON_CURVE: "KryovaPointsOnCurve",
    EDGE_MAP: "KryovaEdgeMap",
}


class VbaUnavailable(RuntimeError):
    """`Evaluate` is not usable on this CATIA -- macros may be disabled."""


def run(app: Any, script: str, parameters: list[Any]) -> Any:
    """Evaluate one of the frozen scripts and return its result.

    `script` must be one of this module's constants, by identity of content.
    That check is what keeps this from being a general code-execution hatch: a
    caller cannot supply text, only choose from the library above.
    """
    function = _ALLOWED.get(script)
    if function is None:
        raise VbaUnavailable(
            "Refusing to evaluate a script that is not one of the frozen "
            "snippets in catia_bridge/vba.py."
        )
    try:
        return app.SystemService.Evaluate(script, VBSCRIPT, function, parameters)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise VbaUnavailable(
            f"CATIA refused to evaluate the {function} helper ({exc}). Macros may be "
            "disabled on this workstation, or the security level set to refuse them."
        ) from exc


def _triple(raw: Any) -> tuple[float, float, float]:
    """Parse the `x;y;z` a helper returns, or raise if it is not that."""
    parts = str(raw).split(";")
    if len(parts) != 3:
        raise VbaUnavailable(f"Expected three coordinates from CATIA, got {raw!r}.")
    try:
        # VBScript formats decimals with the workstation's locale separator, so
        # a French CATIA answers "12,5" where an English one answers "12.5".
        # Both mean the same number and neither is an error.
        return tuple(float(part.strip().replace(",", ".")) for part in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise VbaUnavailable(f"CATIA returned unparseable coordinates {raw!r}.") from exc


def centre_of_gravity(app: Any, part: Any, element: Any) -> tuple[float, float, float]:
    """(x, y, z) of a body's centre of gravity, in millimetres.

    Also the way to read a point's position -- see `CENTRE_OF_GRAVITY`.
    """
    return _triple(run(app, CENTRE_OF_GRAVITY, [part, element]))


Point = tuple[float, float, float]


def edge_map(
    app: Any, part: Any, query: str, scope_shape: Any
) -> dict[int, tuple[Point, Point, Point]]:
    """Selection index -> (start, middle, end) for every measurable solid edge.

    Runs `EDGE_MAP` -- search plus measurement in one Evaluate -- and leaves
    the document's Selection holding the search result, so the caller can pull
    `Selection.Item2(index).Reference` for the indices it keeps.

    `scope_shape` is always required and the search query must use the ",sel"
    scope: the script clears the selection before adding it, so a scope set up
    by the caller beforehand would be wiped -- which is exactly the bug that
    made every feature-scoped fillet report "no solid edges".
    """
    raw = str(run(app, EDGE_MAP, [part, query, scope_shape]))
    edges: dict[int, tuple[Point, Point, Point]] = {}
    for line in raw.splitlines():
        parts = line.split(";")
        if len(parts) != 10:
            continue
        try:
            index = int(parts[0])
            values = [float(v.strip().replace(",", ".")) for v in parts[1:]]
        except ValueError:
            continue
        edges[index] = (tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9]))  # type: ignore[assignment]
    return edges


def points_on_curve(app: Any, part: Any, reference: Any) -> tuple[Point, Point, Point]:
    """(start, middle, end) of an edge, each (x, y, z) in millimetres.

    Unlike `centre_of_gravity` this takes a *Reference*, not an element: edge
    references arrive from `Selection.Search` already wrapped, and re-wrapping
    one with `CreateReferenceFromObject` is refused by CATIA.
    """
    raw = str(run(app, POINTS_ON_CURVE, [part, reference]))
    parts = raw.split(";")
    if len(parts) != 9:
        raise VbaUnavailable(f"Expected nine coordinates from CATIA, got {raw!r}.")
    try:
        values = [float(part.strip().replace(",", ".")) for part in parts]
    except ValueError as exc:
        raise VbaUnavailable(f"CATIA returned unparseable coordinates {raw!r}.") from exc
    return tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9])  # type: ignore[return-value]
