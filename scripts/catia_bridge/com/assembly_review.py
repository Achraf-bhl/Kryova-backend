"""Reading an assembly back: bill of materials, clash, constraint health, mass.

Split from `assembly.py` because it is a different job. That module builds the
structure; this one answers questions about it, and the two share only the
`_product()` / `_component()` accessors defined there. Keeping them apart is
what stops either becoming the 1 500-line file nobody opens.

Assembly *features* live here too — a hole drilled through three stacked plates
at once belongs with the tools that reason across components rather than with
the ones that place them.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext

logger = logging.getLogger("kryova.catia.com.assembly_review")

#: `Clash.ComputationType` is the **scope** of the run, not what it looks for.
#: Measured on a real V5-R33, 2026-09-06, against two 40x40x30 blocks
#: overlapping by 20 mm: 1 reports the interference, 2 and 3 report nothing --
#: 2 is "inside one component" and 3 needs `FirstGroup`/`SecondGroup` set.
#: The table this replaces read {"contact": 1, "clash": 2, "clearance": 3}, so
#: the one word an engineer is most likely to use, "clash", selected the value
#: that finds nothing, and the check reported a clean assembly.
_SCOPE_BETWEEN_ALL = 1

#: `Clash.InterferenceType` is what counts as a problem. 0 finds solids that
#: overlap or touch. 1 additionally finds pairs closer than `Clearance` -- at a
#: 3 mm gap with Clearance = 5, 0 reported nothing and 1 reported 3.0000.
_INTERFERENCE_CONTACT = 0
_INTERFERENCE_CLEARANCE = 1

#: How far from zero a reported value still means "touching". DMU computes the
#: value on a tessellation, not on the exact solids: the same 20 mm overlap came
#: back as -20.0358 on one run and -19.6484 on another. So the sign is reliable
#: and the magnitude is an approximation, which is what the payload says.
_TOUCHING_MM = 1e-6


def _classify(value: float | None) -> str:
    """What a conflict is, from the sign of its value.

    `Conflict.Status` was 0 for every conflict measured -- overlapping, touching
    and clearance alike -- so it does not discriminate. The previous code read
    it through {0: "no_interference", ...} and skipped on that word, which would
    have dropped every real conflict even once the collection was found.
    """
    if value is None:
        return "unknown"
    if value < -_TOUCHING_MM:
        return "clash"
    if value <= _TOUCHING_MM:
        return "contact"
    return "clearance"


class AssemblyReviewMixin:
    """Bill of materials, interference, constraint health, and assembly features."""

    # -- bill of materials ---------------------------------------------------

    def bill_of_materials(  # pragma: no cover - Windows only
        self: ComContext, *, recursive: bool = True, format: str = "summary"  # noqa: A002
    ) -> dict[str, Any]:
        """What the assembly is made of, counted by part number.

        Walked here rather than through CATIA's own `ExtractBOM`, which writes a
        file and needs a format chosen in a dialog. Walking the tree gives the
        same answer, returns it as data, and cannot put a modal window in front
        of an unattended workstation.
        """
        root = self._product()
        lines: dict[str, dict[str, Any]] = {}
        instances: list[dict[str, Any]] = []

        def visit(product: Any, depth: int, path: str) -> None:
            children = product.Products
            for index in range(1, int(children.Count) + 1):
                child = children.Item(index)
                number = str(child.PartNumber)
                where = f"{path}/{child.Name}" if path else str(child.Name)

                line = lines.setdefault(
                    number,
                    {
                        "part_number": number,
                        "quantity": 0,
                        "nomenclature": _text(child, "Nomenclature"),
                        "revision": _text(child, "Revision"),
                        "source": _text(child, "Source"),
                    },
                )
                line["quantity"] += 1

                if format == "detailed":
                    instances.append(
                        {"instance": str(child.Name), "part_number": number,
                         "path": where, "depth": depth}
                    )
                if recursive:
                    visit(child, depth + 1, where)

        visit(root, 1, "")

        result: dict[str, Any] = {
            "assembly": str(root.PartNumber),
            "lines": sorted(lines.values(), key=lambda line: line["part_number"]),
            "distinct_parts": len(lines),
            "total_instances": sum(line["quantity"] for line in lines.values()),
            "recursive": bool(recursive),
        }
        if format == "detailed":
            result["instances"] = instances
        return result

    # -- interference --------------------------------------------------------

    def assembly_clash(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        components: list[str] | None = None,
        clearance_mm: float | None = None,
        kind: str = "clash",
    ) -> dict[str, Any]:
        """Find where components overlap, touch, or come closer than a clearance.

        This is the one assembly operation that is genuinely slow — DMU tests
        every face pair — which is why it is declared long-running and why the
        result reports how many components were in scope. A clash run that
        reports zero interferences over zero components has not proved anything.

        The three properties this drives were all measured on a real V5-R33 on
        2026-09-06 rather than taken from the enum names; `_SCOPE_BETWEEN_ALL`
        and `_classify` record what was wrong before and what the seat answered.
        """
        if kind == "clearance" and not clearance_mm:
            raise CatiaOperationError(
                "A clearance check needs a distance: pass clearance_mm. Without one "
                "there is nothing to be closer than, and the run would only find "
                "solids that already touch -- which is kind='clash'."
            )

        document = self._document()
        try:
            workbench = document.GetWorkbench("SPAWorkbench")
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "Interference checking needs the DMU Space Analysis workbench, which "
                "this CATIA installation does not have licensed."
            ) from error

        clash = workbench.Clashes.Add()
        clash.ComputationType = _SCOPE_BETWEEN_ALL
        if kind == "clearance":
            clash.InterferenceType = _INTERFERENCE_CLEARANCE
            clash.Clearance = float(clearance_mm)
        else:
            clash.InterferenceType = _INTERFERENCE_CONTACT

        try:
            clash.Compute()
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not complete the interference check. ({error})"
            ) from error

        # `Conflicts` is the collection of problems found, not of pairs looked
        # at -- so its count can never say how much was checked. The scope is
        # every pair in the assembly, which is a number we hold ourselves.
        found = clash.Conflicts
        try:
            total = int(found.Count)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA computed the interference check but would not report it. ({error})"
            ) from error

        conflicts: list[dict[str, Any]] = []
        for index in range(1, total + 1):
            item = found.Item(index)
            value = _number(item, "Value")
            conflicts.append(
                {
                    "status": _classify(value),
                    "components": [
                        _text(item, "FirstProduct"),
                        _text(item, "SecondProduct"),
                    ],
                    "value_mm": value,
                    "value_is_approximate": True,
                }
            )

        # Asked about named components, DMU still checks the whole assembly at
        # this scope, so the narrowing is done here and said out loud. Reporting
        # a whole-assembly result as if it were the scoped one would name
        # components the caller never asked about.
        scope = "whole assembly"
        if components:
            wanted = {str(name) for name in components}
            conflicts = [
                entry for entry in conflicts if wanted.intersection(entry["components"])
            ]
            scope = "filtered to " + ", ".join(sorted(wanted))

        return {
            "kind": kind,
            "scope": scope,
            "components_in_assembly": _component_count(self._product()),
            "interferences": conflicts,
            "clear": not conflicts,
            "note": (
                "Interference depths and clearances are computed on CATIA's tessellation, "
                "not on the exact solids: the sign is reliable, the magnitude is "
                "approximate to about a percent."
            ),
        }

    # -- constraint and structure health -------------------------------------

    def assembly_analysis(  # pragma: no cover - Windows only
        self: ComContext, *, kind: str, component: str = ""
    ) -> dict[str, Any]:
        """Report on constraints, freedom, broken links, dependencies or mass."""
        root = self._component(component) if component else self._product()
        if kind == "constraints":
            return self._constraint_report(root)
        if kind == "degrees_of_freedom":
            return self._freedom_report(root)
        if kind == "broken_links":
            return self._broken_links(root)
        if kind == "dependencies":
            return self._dependencies(root)
        return self._mass_report(root)

    def _constraint_report(self: ComContext, root: Any) -> dict[str, Any]:  # pragma: no cover
        """Every constraint, and which of them CATIA cannot currently satisfy."""
        try:
            constraints = self._product().Connections("CATIAConstraints")
        except Exception:  # noqa: BLE001 - an assembly with none has no set
            return {"kind": "constraints", "constraints": [], "total": 0, "broken": 0}

        listed: list[dict[str, Any]] = []
        for index in range(1, int(constraints.Count) + 1):
            constraint = constraints.Item(index)
            status = _text(constraint, "Status")
            listed.append(
                {
                    "name": str(constraint.Name),
                    "active": bool(getattr(constraint, "Active", True)),
                    "status": status,
                    "value": _number(constraint, "Dimension"),
                }
            )
        broken = [entry for entry in listed if entry["status"] not in {"", "0", "Verified"}]
        del root
        return {
            "kind": "constraints",
            "constraints": listed,
            "total": len(listed),
            "broken": len(broken),
        }

    def _freedom_report(self: ComContext, root: Any) -> dict[str, Any]:  # pragma: no cover
        """Which components can still move, and are therefore under-constrained.

        Reported per component rather than as one number, because the useful
        question is never "how free is this assembly" but "which part is going
        to slide when I update".
        """
        loose: list[str] = []
        products = root.Products
        for index in range(1, int(products.Count) + 1):
            child = products.Item(index)
            if not _is_fixed(self._product(), str(child.Name)):
                loose.append(str(child.Name))
        return {
            "kind": "degrees_of_freedom",
            "unconstrained": loose,
            "components": int(products.Count),
            "fully_positioned": not loose,
        }

    def _broken_links(self: ComContext, root: Any) -> dict[str, Any]:  # pragma: no cover
        """Components whose backing document CATIA can no longer load."""
        broken: list[dict[str, str]] = []
        for child in _walk(root):
            try:
                document = child.ReferenceProduct.Parent
                path = str(getattr(document, "FullName", ""))
            except Exception:  # noqa: BLE001 - that is the failure being looked for
                broken.append({"component": str(child.Name), "reason": "not loaded"})
                continue
            if not path:
                broken.append({"component": str(child.Name), "reason": "never saved"})
        return {"kind": "broken_links", "broken": broken, "healthy": not broken}

    def _dependencies(self: ComContext, root: Any) -> dict[str, Any]:  # pragma: no cover
        """Which document each component comes from, and which are shared."""
        by_document: dict[str, list[str]] = {}
        for child in _walk(root):
            try:
                path = str(getattr(child.ReferenceProduct.Parent, "FullName", "") or "(unsaved)")
            except Exception:  # noqa: BLE001
                path = "(not loaded)"
            by_document.setdefault(path, []).append(str(child.Name))
        return {
            "kind": "dependencies",
            "documents": [
                {"document": path, "components": names, "instances": len(names)}
                for path, names in sorted(by_document.items())
            ],
            "distinct_documents": len(by_document),
        }

    def _mass_report(self: ComContext, root: Any) -> dict[str, Any]:  # pragma: no cover
        """Total mass and centre of gravity, per component and for the whole.

        A component with no material contributes nothing and is listed as such
        rather than as zero: an assembly that quietly weighs less than it should
        because one part was never given a material is a mistake worth seeing.
        """
        components: list[dict[str, Any]] = []
        total = 0.0
        unmeasured: list[str] = []

        for child in _walk(root):
            try:
                inertia = child.ReferenceProduct.Parent.Product.Analyze
                mass = float(inertia.Mass)
            except Exception:  # noqa: BLE001 - no material, or not a part
                unmeasured.append(str(child.Name))
                continue
            if mass <= 0.0:
                unmeasured.append(str(child.Name))
                continue
            total += mass
            components.append({"component": str(child.Name), "mass_kg": round(mass, 6)})

        return {
            "kind": "mass",
            "total_mass_kg": round(total, 6),
            "components": components,
            # Named, not counted: the caller needs to know which part to fix.
            "without_material": unmeasured,
        }

    # -- assembly features ---------------------------------------------------

    def assembly_feature(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        affected: list[str],
        sketch: str = "",
        at: list[float] | None = None,
        diameter_mm: float | None = None,
        depth_mm: float | None = None,
        cutting: str = "",
    ) -> dict[str, Any]:
        """A feature cut through several components at once.

        The case this exists for is a hole drilled through a stack after
        assembly: one feature, one diameter, and the components stay aligned
        when it moves. Modelling the same hole separately in each part gives
        three holes that agree today and drift the moment one is edited.
        """
        product = self._product()
        try:
            features = product.GetItem("CATAsmAssemblyFeatures")
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "Assembly features are not available through automation on this "
                "CATIA. Model the feature in each part instead, or add it to the "
                "part that owns the geometry and let the others reference it."
            ) from error

        targets = [self._component(name) for name in affected]
        if len(targets) < 1:
            raise CatiaOperationError(
                "An assembly feature needs at least one component to affect."
            )

        builder = {
            "hole": "AddAssemblyHole",
            "pocket": "AddAssemblyPocket",
            "add": "AddAssemblyAdd",
            "remove": "AddAssemblyRemove",
            "split": "AddAssemblySplit",
            "remove_lump": "AddAssemblyRemoveLump",
        }[kind]

        try:
            feature = getattr(features, builder)()
        except AttributeError as error:
            raise CatiaOperationError(
                f"This CATIA does not offer an assembly {kind} through automation."
            ) from error
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused to create an assembly {kind}. ({error})"
            ) from error

        _configure_feature(feature, kind, at, diameter_mm, depth_mm, sketch, cutting)
        for target in targets:
            try:
                feature.AddAffectedProduct(target)
            except Exception:  # noqa: BLE001 - some kinds take the whole assembly
                break

        product.Update()
        return {
            "feature": str(feature.Name),
            "kind": kind,
            "affected": [str(target.Name) for target in targets],
        }


# -- helpers -------------------------------------------------------------------


def _walk(root: Any) -> list[Any]:  # pragma: no cover - Windows only
    """Every component under `root`, depth first."""
    found: list[Any] = []
    products = root.Products
    for index in range(1, int(products.Count) + 1):
        child = products.Item(index)
        found.append(child)
        found.extend(_walk(child))
    return found



def _component_count(product: object) -> int:  # pragma: no cover - Windows only
    """How many components the scope covered, as the honest denominator.

    Zero conflicts over zero components proves nothing; zero over two is a
    result. The old payload called `Conflicts.Count` the pairs checked, which made
    a clean assembly report that nothing had been examined.
    """
    try:
        return int(product.Products.Count)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - not a product, or an empty one
        return 0

def _text(owner: Any, attribute: str) -> str:  # pragma: no cover - Windows only
    """An optional string property, as "" when the release does not expose it."""
    try:
        return str(getattr(owner, attribute))
    except Exception:  # noqa: BLE001 - absent is a legitimate answer
        return ""


def _number(owner: Any, attribute: str) -> float | None:  # pragma: no cover - Windows only
    """An optional numeric property, as None when absent or not a number."""
    try:
        value = getattr(owner, attribute)
        return float(getattr(value, "Value", value))
    except Exception:  # noqa: BLE001
        return None


def _is_fixed(product: Any, name: str) -> bool:  # pragma: no cover - Windows only
    """Whether any constraint references this component."""
    try:
        constraints = product.Connections("CATIAConstraints")
    except Exception:  # noqa: BLE001 - no constraints at all
        return False
    for index in range(1, int(constraints.Count) + 1):
        constraint = constraints.Item(index)
        for slot in (1, 2):
            try:
                reference = constraint.GetConstraintElement(slot)
            except Exception:  # noqa: BLE001 - a mono-element constraint
                break
            if name in str(reference.DisplayName):
                return True
    return False


def _configure_feature(  # pragma: no cover - Windows only
    feature: Any,
    kind: str,
    at: list[float] | None,
    diameter_mm: float | None,
    depth_mm: float | None,
    sketch: str,
    cutting: str,
) -> None:
    """Apply whichever of the optional parameters this feature kind understands.

    Each is attempted independently and a refusal is logged rather than raised:
    the feature itself has already been created at this point, and failing the
    whole call over a diameter that a split does not have would leave a
    half-configured feature in the tree with an error saying it was not created.
    """
    settings = (
        ("Diameter", diameter_mm),
        ("Depth", depth_mm),
        ("Sketch", sketch or None),
        ("CuttingElement", cutting or None),
    )
    for attribute, value in settings:
        if value is None:
            continue
        try:
            target = getattr(feature, attribute)
            if hasattr(target, "Value"):
                target.Value = float(value)
            else:
                setattr(feature, attribute, value)
        except Exception:  # noqa: BLE001
            logger.debug("Assembly %s does not take %s", kind, attribute)

    if at:
        try:
            feature.SetOrigin(float(at[0]), float(at[1]), float(at[2]))
        except Exception:  # noqa: BLE001
            logger.debug("Assembly %s does not take an origin", kind)
