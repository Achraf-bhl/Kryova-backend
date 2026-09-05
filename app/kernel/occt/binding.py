"""The one place OCCT is imported, and the one place its absence is handled.

Everything else under `app/kernel/occt/` imports from here. That keeps three promises:

**The package imports on a machine with no OCCT.** `app/catia/` already holds this
contract for `pywin32` — the bridge imports everywhere and refuses at call time off
Windows — and the geometry kernel needs it for the same reason plus one more: the
existing suite must keep running for anyone who has not installed a ~800 MB dependency,
and CI must be able to run the offline half without it.

**The binding is named once.** Master plan Phase 1.0 chose **OCP** (`cadquery-ocp`,
pybind11 bindings for OCCT 7.9.3) over `pythonocc-core`, which is not published on PyPI
at all and would have forced conda into the deployment. If that is ever revisited, this
module is the whole of the change.

**The untyped surface is contained.** OCP ships no `py.typed`, so mypy cannot see into
it. Importing it here and nowhere else means one `disable_error_code` override in
`pyproject.toml` covers the entire kernel, instead of the suppression spreading file by
file. `symbol()` hands the rest of the package plain objects.

Symbols are looked up by name through `symbol()` rather than imported directly by
callers so that a missing OCCT install produces `KernelUnavailable` with an install
hint, at the call site that needed it, instead of an `ImportError` traceback at process
start.
"""

from __future__ import annotations

from typing import Any, Final

from app.kernel.errors import KernelUnavailable

#: The distribution that provides the binding, named in install hints.
DISTRIBUTION: Final = "cadquery-ocp"

#: Resolved once at import. A probe that re-attempts a failing import on every call
#: turns a missing dependency into a per-request cost, and this is called per operation.
_SYMBOLS: dict[str, Any] = {}
_IMPORT_ERROR: Exception | None = None

try:  # pragma: no cover - which branch runs depends on the machine, not the test
    from OCP.Bnd import Bnd_Box, Bnd_OBB
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepAlgoAPI import (
        BRepAlgoAPI_Common,
        BRepAlgoAPI_Cut,
        BRepAlgoAPI_Defeaturing,
        BRepAlgoAPI_Fuse,
        BRepAlgoAPI_Section,
        BRepAlgoAPI_Splitter,
    )
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_GTransform,
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeSolid,
        BRepBuilderAPI_MakeVertex,
        BRepBuilderAPI_MakeWire,
        BRepBuilderAPI_Sewing,
        BRepBuilderAPI_Transform,
    )
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.BRepLib import BRepLib
    from OCP.BRepLProp import BRepLProp_SLProps
    from OCP.BRepOffsetAPI import (
        BRepOffsetAPI_DraftAngle,
        BRepOffsetAPI_MakeFilling,
        BRepOffsetAPI_MakeOffsetShape,
        BRepOffsetAPI_MakePipe,
        BRepOffsetAPI_MakePipeShell,
        BRepOffsetAPI_MakeThickSolid,
        BRepOffsetAPI_ThruSections,
    )
    from OCP.BRepPrimAPI import (
        BRepPrimAPI_MakeBox,
        BRepPrimAPI_MakeCone,
        BRepPrimAPI_MakeCylinder,
        BRepPrimAPI_MakePrism,
        BRepPrimAPI_MakeRevol,
        BRepPrimAPI_MakeSphere,
    )
    from OCP.BRepTools import BRepTools
    from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
    from OCP.GC import GC_MakeArcOfCircle, GC_MakeCircle
    from OCP.Geom import Geom_ConicalSurface, Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line
    from OCP.GeomAbs import GeomAbs_Shape
    from OCP.GeomAPI import GeomAPI_Interpolate, GeomAPI_PointsToBSpline
    from OCP.gp import (
        gp_Ax1,
        gp_Ax2,
        gp_Ax3,
        gp_Circ,
        gp_Dir,
        gp_Dir2d,
        gp_Elips,
        gp_GTrsf,
        gp_Lin,
        gp_Mat,
        gp_Pln,
        gp_Pnt,
        gp_Pnt2d,
        gp_Trsf,
        gp_Vec,
        gp_XYZ,
    )
    from OCP.GProp import GProp_GProps
    from OCP.Precision import Precision
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.TColgp import (
        TColgp_Array1OfPnt,
        TColgp_Array1OfPnt2d,
        TColgp_HArray1OfPnt,
    )
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelMap, TDF_TagSource
    from OCP.TDocStd import TDocStd_Application, TDocStd_Document
    from OCP.TNaming import TNaming_Builder, TNaming_Selector, TNaming_Tool
    from OCP.TopAbs import TopAbs_ShapeEnum, TopAbs_State
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Iterator
    from OCP.TopTools import (
        TopTools_IndexedDataMapOfShapeListOfShape,
        TopTools_IndexedMapOfShape,
        TopTools_ListOfShape,
    )

    _SYMBOLS = {
        "BRep_Builder": BRep_Builder,
        "BRep_Tool": BRep_Tool,
        "BRepAdaptor_Curve": BRepAdaptor_Curve,
        "BRepAdaptor_Surface": BRepAdaptor_Surface,
        "BRepAlgoAPI_Common": BRepAlgoAPI_Common,
        "BRepAlgoAPI_Cut": BRepAlgoAPI_Cut,
        "BRepAlgoAPI_Defeaturing": BRepAlgoAPI_Defeaturing,
        "BRepAlgoAPI_Fuse": BRepAlgoAPI_Fuse,
        "BRepAlgoAPI_Section": BRepAlgoAPI_Section,
        "BRepAlgoAPI_Splitter": BRepAlgoAPI_Splitter,
        "BRepBndLib": BRepBndLib,
        "BRepBuilderAPI_GTransform": BRepBuilderAPI_GTransform,
        "BRepBuilderAPI_MakeEdge": BRepBuilderAPI_MakeEdge,
        "BRepBuilderAPI_MakeFace": BRepBuilderAPI_MakeFace,
        "BRepBuilderAPI_MakeSolid": BRepBuilderAPI_MakeSolid,
        "BRepBuilderAPI_MakeVertex": BRepBuilderAPI_MakeVertex,
        "BRepBuilderAPI_MakeWire": BRepBuilderAPI_MakeWire,
        "BRepBuilderAPI_Sewing": BRepBuilderAPI_Sewing,
        "BRepBuilderAPI_Transform": BRepBuilderAPI_Transform,
        "BRepCheck_Analyzer": BRepCheck_Analyzer,
        "BRepExtrema_DistShapeShape": BRepExtrema_DistShapeShape,
        "BRepFilletAPI_MakeChamfer": BRepFilletAPI_MakeChamfer,
        "BRepFilletAPI_MakeFillet": BRepFilletAPI_MakeFillet,
        "BRepGProp": BRepGProp,
        "BRepIntCurveSurface_Inter": BRepIntCurveSurface_Inter,
        "BRepLProp_SLProps": BRepLProp_SLProps,
        "BRepLib": BRepLib,
        "BRepOffsetAPI_DraftAngle": BRepOffsetAPI_DraftAngle,
        "BRepOffsetAPI_MakeFilling": BRepOffsetAPI_MakeFilling,
        "BRepOffsetAPI_MakeOffsetShape": BRepOffsetAPI_MakeOffsetShape,
        "BRepOffsetAPI_MakePipe": BRepOffsetAPI_MakePipe,
        "BRepOffsetAPI_MakePipeShell": BRepOffsetAPI_MakePipeShell,
        "BRepOffsetAPI_MakeThickSolid": BRepOffsetAPI_MakeThickSolid,
        "BRepOffsetAPI_ThruSections": BRepOffsetAPI_ThruSections,
        "BRepPrimAPI_MakeBox": BRepPrimAPI_MakeBox,
        "BRepPrimAPI_MakeCone": BRepPrimAPI_MakeCone,
        "BRepPrimAPI_MakeCylinder": BRepPrimAPI_MakeCylinder,
        "BRepPrimAPI_MakePrism": BRepPrimAPI_MakePrism,
        "BRepPrimAPI_MakeRevol": BRepPrimAPI_MakeRevol,
        "BRepPrimAPI_MakeSphere": BRepPrimAPI_MakeSphere,
        "BRepTools": BRepTools,
        "BRepTopAdaptor_FClass2d": BRepTopAdaptor_FClass2d,
        "Bnd_Box": Bnd_Box,
        "Bnd_OBB": Bnd_OBB,
        "GC_MakeArcOfCircle": GC_MakeArcOfCircle,
        "GC_MakeCircle": GC_MakeCircle,
        "GProp_GProps": GProp_GProps,
        "Geom2d_Line": Geom2d_Line,
        "GeomAPI_Interpolate": GeomAPI_Interpolate,
        "GeomAPI_PointsToBSpline": GeomAPI_PointsToBSpline,
        "GeomAbs_Shape": GeomAbs_Shape,
        "Geom_ConicalSurface": Geom_ConicalSurface,
        "Geom_CylindricalSurface": Geom_CylindricalSurface,
        "Precision": Precision,
        "ShapeAnalysis_FreeBounds": ShapeAnalysis_FreeBounds,
        "ShapeFix_Shape": ShapeFix_Shape,
        "TCollection_ExtendedString": TCollection_ExtendedString,
        "TColgp_Array1OfPnt": TColgp_Array1OfPnt,
        "TColgp_Array1OfPnt2d": TColgp_Array1OfPnt2d,
        "TColgp_HArray1OfPnt": TColgp_HArray1OfPnt,
        "TDF_LabelMap": TDF_LabelMap,
        "TDF_TagSource": TDF_TagSource,
        "TDocStd_Application": TDocStd_Application,
        "TDocStd_Document": TDocStd_Document,
        "TNaming_Builder": TNaming_Builder,
        "TNaming_Selector": TNaming_Selector,
        "TNaming_Tool": TNaming_Tool,
        "TopAbs_ShapeEnum": TopAbs_ShapeEnum,
        "TopAbs_State": TopAbs_State,
        "TopExp": TopExp,
        "TopExp_Explorer": TopExp_Explorer,
        "TopTools_IndexedDataMapOfShapeListOfShape": TopTools_IndexedDataMapOfShapeListOfShape,
        "TopTools_IndexedMapOfShape": TopTools_IndexedMapOfShape,
        "TopTools_ListOfShape": TopTools_ListOfShape,
        "TopoDS": TopoDS,
        "TopoDS_Compound": TopoDS_Compound,
        "TopoDS_Iterator": TopoDS_Iterator,
        "gp_Ax1": gp_Ax1,
        "gp_Ax2": gp_Ax2,
        "gp_Ax3": gp_Ax3,
        "gp_Circ": gp_Circ,
        "gp_Dir": gp_Dir,
        "gp_Dir2d": gp_Dir2d,
        "gp_Elips": gp_Elips,
        "gp_GTrsf": gp_GTrsf,
        "gp_Lin": gp_Lin,
        "gp_Mat": gp_Mat,
        "gp_Pln": gp_Pln,
        "gp_Pnt": gp_Pnt,
        "gp_Pnt2d": gp_Pnt2d,
        "gp_Trsf": gp_Trsf,
        "gp_Vec": gp_Vec,
        "gp_XYZ": gp_XYZ,
    }
except Exception as exc:  # noqa: BLE001 - every import failure means the same thing
    _IMPORT_ERROR = exc


def available() -> bool:
    """Is the geometry kernel usable in this process?"""
    return bool(_SYMBOLS)


def require() -> None:
    """Refuse clearly if the kernel is missing. Call before touching geometry."""
    if _SYMBOLS:
        return
    raise KernelUnavailable(
        "The OCCT geometry kernel is not installed, so nothing can be built locally. "
        f"Install it with `pip install {DISTRIBUTION}`, or run this design on the CATIA "
        f"backend instead. (import failed: {_IMPORT_ERROR})"
    )


def symbol(name: str) -> Any:
    """One bound OCCT symbol.

    Hot path: called once per geometric operation, so it is a dict lookup after a
    truthiness check and nothing more. Do not add work here.
    """
    if not _SYMBOLS:
        require()
    try:
        return _SYMBOLS[name]
    except KeyError:  # pragma: no cover - a typo in this package, not a user path
        raise KernelUnavailable(
            f"{name!r} is not among the OCCT symbols this build imports. Add it to "
            "app/kernel/occt/binding.py rather than importing OCP elsewhere."
        ) from None


def import_error() -> Exception | None:
    """Why the kernel is unavailable — for diagnostics and the health surface."""
    return _IMPORT_ERROR


def occt_version() -> str:
    """The kernel version, for provenance records (master plan 7.3).

    A simulation result is bound to the kernel that produced its geometry, and "same
    spec plus same version ⇒ same geometry" (I5) is uncheckable without a version to
    compare. So this must return something real: OCCT's own C++ `Standard_Version` is
    not exposed by this binding, but `OCP.__version__` tracks it directly — `7.9.3.1`
    is OCCT 7.9.3 plus a binding revision — and the distribution metadata is the
    fallback if that ever disappears.
    """
    if not _SYMBOLS:
        return "unavailable"

    import OCP

    version = getattr(OCP, "__version__", None)
    if version:
        return f"OCCT {version}"

    try:  # pragma: no cover - only reached if the binding stops exporting a version
        from importlib.metadata import version as distribution_version

        return f"OCCT {distribution_version(DISTRIBUTION)}"
    except Exception:  # noqa: BLE001 - a version is provenance, never worth a crash
        return "unknown"


__all__ = [
    "DISTRIBUTION",
    "available",
    "import_error",
    "occt_version",
    "require",
    "symbol",
]
