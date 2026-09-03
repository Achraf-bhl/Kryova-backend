"""Assembly Design over COM: products, components, constraints, movement.

Everything before this module worked on one CATPart. An assembly is a different
document type with a different root object — `Document.Product` rather than
`Document.Part` — and that difference is why these tools could not simply be
added to the Part Design mixin: `_part()` raises on a CATProduct, by design, and
`_product()` here is its counterpart.

**The one thing in this file a real CATIA seat has to confirm.** The
`_CONSTRAINT_TYPES` values below are CATIA's `CatConstraintType` enumeration,
and enumeration values are the kind of thing that cannot be checked without the
application: a wrong number does not fail, it silently creates a constraint of
some *other* type, which is far worse than an error. So `constrain` reads back
the name CATIA gives the constraint it made — CATIA names them after their own
type, "Coincidence.1", "Angle.3" — and reports it alongside a
`kind_confirmed` flag. Asking for a coincidence and getting `kind_confirmed:
false` with `catia_name: "Angle.3"` is the mismatch showing itself on the first
call rather than in an assembly that quietly moves the wrong way.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext

logger = logging.getLogger("kryova.catia.com.assembly")

#: `CatConstraintType`. See the module docstring: these are reported back
#: against CATIA's own naming rather than trusted.
_CONSTRAINT_TYPES = {
    "coincidence": 1,
    "contact": 11,
    "offset": 2,
    "angle": 4,
    "parallel": 9,
    "perpendicular": 10,
    "fix": 13,
    "fix_together": 14,
}

#: How many elements each assembly constraint consumes. `fix` pins one
#: component; `fix_together` welds a set; the rest relate exactly two faces.
_CONSTRAINT_ARITY = {
    "coincidence": 2,
    "contact": 2,
    "offset": 2,
    "angle": 2,
    "parallel": 2,
    "perpendicular": 2,
    "fix": 1,
    "fix_together": 2,
}

#: The prefix CATIA puts on a constraint of each kind, in English and in French
#: — the two interface languages the bridge reports. Used only to confirm that
#: the enum value above produced what was asked for.
_CONSTRAINT_NAMES = {
    "coincidence": ("coincidence",),
    "contact": ("contact",),
    "offset": ("offset", "décalage", "decalage"),
    "angle": ("angle",),
    "parallel": ("parallelism", "parallélisme", "parallelisme"),
    "perpendicular": ("perpendicularity", "perpendicularité", "perpendicularite"),
    "fix": ("fix", "fixité", "fixite"),
    "fix_together": ("fixtogether", "fixation"),
}

#: `CatProductSource`: where a component comes from, for the bill of materials.
_SOURCES = {"unknown": 0, "made": 1, "bought": 2}

#: The name of the assembly-constraints connection set on a product.
_CONSTRAINTS_CONNECTION = "CATIAConstraints"


class AssemblyMixin:
    """Build a product structure, place components in it, and constrain them."""

    # -- the active product --------------------------------------------------

    def _product(self: ComContext) -> Any:  # pragma: no cover - Windows only
        """The root product of the active document.

        A CATPart also answers to `.Product` — that is its interface as a
        component — but it has no `Products` collection to add to. Checking for
        the collection rather than for the document type is what makes the
        error say the useful thing: you are in a part, open an assembly.
        """
        document = self._document()
        try:
            product = document.Product
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "The active CATIA document has no product structure. Assembly tools "
                "need a CATProduct — call catia_product_create, or activate one."
            ) from error

        try:
            product.Products.Count  # noqa: B018 - probing for the collection
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "The active document is a part, not an assembly. Assembly tools need "
                "a CATProduct: call catia_product_create to start one, then add this "
                "part to it with catia_component_add."
            ) from error
        return product

    def _component(self: ComContext, name: str) -> Any:  # pragma: no cover - Windows only
        """A component anywhere in the tree, by instance name or part number.

        Searched depth-first over the whole structure rather than the top level
        only, because a constraint between a screw and a bracket three levels
        down is the ordinary case, not an advanced one.
        """
        root = self._product()
        found = _find_component(root, name)
        if found is not None:
            return found
        available = ", ".join(_component_names(root)[:12]) or "(none)"
        raise CatiaOperationError(
            f"No component named {name!r} in this assembly. It contains: {available}. "
            "Use catia_bill_of_materials to see the whole tree."
        )

    # -- structure -----------------------------------------------------------

    def product_create(  # pragma: no cover - Windows only
        self: ComContext, *, name: str, part_number: str = ""
    ) -> dict[str, Any]:
        """Start a new, empty assembly and make it the active document."""
        self._require_closed()
        document = self._app.Documents.Add("Product")
        path = self._free_document_path(name, suffix=".CATProduct")
        document.SaveAs(str(path))

        product = document.Product
        product.PartNumber = part_number or name
        try:
            product.Name = name
        except Exception:  # noqa: BLE001 - CATIA derives Name from PartNumber
            pass

        return {
            "doc_name": name,
            "remote_path": str(path),
            "part_number": str(product.PartNumber),
            "components": 0,
        }

    def component_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        name: str = "",
        document: str = "",
        source: str = "",
        parent: str = "",
        at: list[float] | None = None,
    ) -> dict[str, Any]:
        """Put a component into the assembly, four different ways.

        `instance_of` is the one that earns its place: adding the same part
        twice with `existing` gives two components that happen to reference one
        file, while an instance is a second placement of a component already in
        the tree — which is what a bill of materials counts as quantity two.
        """
        root = self._component(parent) if parent else self._product()
        children = root.Products

        if kind == "new_part":
            component = children.AddNewComponent("Part", name or "")
        elif kind == "new_product":
            component = children.AddNewComponent("Product", name or "")
        elif kind == "existing":
            if not document:
                raise CatiaOperationError(
                    "Adding an existing component needs `document` — the name of a "
                    "document already open in CATIA, or a part previously created here."
                )
            component = self._add_existing(children, document)
        elif kind == "instance_of":
            if not source:
                raise CatiaOperationError(
                    "`instance_of` needs `source` — the name of the component in this "
                    "assembly to make another instance of."
                )
            original = self._component(source)
            component = children.AddComponent(original.ReferenceProduct)
        else:  # pragma: no cover - the table refuses anything else first
            raise CatiaOperationError(f"{kind!r} is not a way to add a component.")

        if name:
            try:
                component.Name = name
            except Exception:  # noqa: BLE001 - a clash keeps CATIA's own name
                pass
        if at:
            _place(component, [float(value) for value in at])

        self._document().Product.Update()
        return {
            "component": str(component.Name),
            "part_number": str(component.PartNumber),
            "kind": kind,
            "components": int(self._product().Products.Count),
        }

    def _add_existing(  # pragma: no cover - Windows only
        self: ComContext, children: Any, document: str
    ) -> Any:
        """Add a document that is already open, or one saved under `documents/`."""
        for index in range(1, int(self._app.Documents.Count) + 1):
            candidate = self._app.Documents.Item(index)
            if str(candidate.Name).lower() in {document.lower(), f"{document.lower()}.catpart"}:
                return children.AddComponent(candidate.Product)

        # Falling back to disk is what makes "make a part, then assemble it"
        # work in one session: the part was saved by `new_part` and may since
        # have been closed.
        for suffix in (".CATPart", ".CATProduct"):
            path = self.documents / f"{document}{suffix}"
            if path.is_file():
                return children.AddExternalComponent(self._app.Documents.Open(str(path)))

        raise CatiaOperationError(
            f"No document named {document!r} is open in CATIA or saved here. Create it "
            "with catia_new_part first, or open it with catia_open_document."
        )

    def component_multi_instantiate(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        component: str,
        count: int,
        spacing_mm: float,
        direction: list[float],
    ) -> dict[str, Any]:
        """Repeat a component in a line — a bolt row, a rack of identical cards."""
        original = self._component(component)
        parent = original.Parent
        step = _unit(direction)

        created: list[str] = []
        for index in range(1, int(count)):
            instance = parent.AddComponent(original.ReferenceProduct)
            offset = float(spacing_mm) * index
            _place(instance, [step[0] * offset, step[1] * offset, step[2] * offset])
            created.append(str(instance.Name))

        self._document().Product.Update()
        return {
            "component": component,
            "created": created,
            # The original counts toward the total the caller asked for; saying
            # both numbers avoids an off-by-one argument about what `count` meant.
            "instances": len(created) + 1,
        }

    def component_replace(  # pragma: no cover - Windows only
        self: ComContext, *, component: str, replacement: str, all_instances: bool = False
    ) -> dict[str, Any]:
        """Swap one component for another, keeping its position and constraints."""
        target = self._component(component)
        path = self._resolve_document_path(replacement)
        try:
            target.ReplaceComponent(str(path), bool(all_instances))
        except AttributeError as error:
            raise CatiaOperationError(
                "This CATIA does not expose component replacement through automation. "
                "Remove the component with catia_component_remove and add the "
                "replacement with catia_component_add."
            ) from error
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused to replace {component!r} with {replacement!r}. The "
                "replacement has to be a saved document, and constraints that "
                f"referenced faces the new part does not have will break. ({error})"
            ) from error

        self._document().Product.Update()
        return {
            "component": component,
            "replacement": replacement,
            "all_instances": bool(all_instances),
        }

    def component_remove(  # pragma: no cover - Windows only
        self: ComContext, *, component: str
    ) -> dict[str, Any]:
        """Take a component out of the assembly.

        Not a destructive-tier operation: the checkpoint taken before every
        write restores it, and the underlying document on disk is untouched
        either way — only the reference to it goes.
        """
        target = self._component(component)
        parent = target.Parent
        parent.Products.Remove(target.Name)
        self._document().Product.Update()
        return {
            "removed": component,
            "components": int(self._product().Products.Count),
        }

    def component_properties(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        component: str,
        part_number: str = "",
        revision: str = "",
        nomenclature: str = "",
        instance_name: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        """Set the identity fields a bill of materials is built from."""
        target = self._component(component)
        changed: dict[str, str] = {}

        # Each is set independently: a caller correcting only a revision must
        # not have the other fields cleared out from under it by the defaults.
        for attribute, value in (
            ("PartNumber", part_number),
            ("Revision", revision),
            ("Nomenclature", nomenclature),
            ("Name", instance_name),
        ):
            if not value:
                continue
            try:
                setattr(target, attribute, value)
                changed[attribute] = value
            except Exception as error:  # noqa: BLE001
                raise CatiaOperationError(
                    f"CATIA refused to set {attribute} to {value!r}. A part number has "
                    f"to be unique within the assembly. ({error})"
                ) from error

        if source:
            target.Source = _SOURCES[source]
            changed["Source"] = source

        return {"component": str(target.Name), "changed": changed}

    # -- constraints ---------------------------------------------------------

    def constrain(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        elements: list[str],
        value: float | None = None,
        angle_deg: float | None = None,
        orientation: str = "",
    ) -> dict[str, Any]:
        """Relate two components' geometry — the mechanism that makes an assembly.

        The `kind_confirmed` flag in the result is not decoration; see the
        module docstring. It is how a wrong enum value announces itself.
        """
        product = self._product()
        wanted = _CONSTRAINT_ARITY[kind]
        if len(elements) != wanted:
            raise CatiaOperationError(
                f"A {kind} constraint takes {wanted} element(s), not {len(elements)}. "
                "Name them as component/geometry, e.g. 'Bracket.1/Pad.1'."
            )

        constraints = product.Connections(_CONSTRAINTS_CONNECTION)
        references = [self._assembly_reference(name) for name in elements]

        try:
            if len(references) == 1:
                constraint = constraints.AddMonoEltCst(
                    _CONSTRAINT_TYPES[kind], references[0]
                )
            else:
                constraint = constraints.AddBiEltCst(
                    _CONSTRAINT_TYPES[kind], references[0], references[1]
                )
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a {kind} constraint between those elements. The two "
                "have to be the right kind of geometry for it — a coincidence needs "
                "axes or planes, a contact needs two faces. "
                f"({error})"
            ) from error

        if value is not None:
            constraint.Dimension.Value = float(value)
        if angle_deg is not None:
            constraint.Dimension.Value = float(angle_deg)
        if orientation:
            _set_orientation(constraint, orientation)

        product.Update()

        catia_name = str(constraint.Name)
        confirmed = any(
            catia_name.lower().startswith(prefix)
            for prefix in _CONSTRAINT_NAMES.get(kind, ())
        )
        if not confirmed:
            logger.warning(
                "Asked CATIA for a %s constraint and it named the result %r — the "
                "CatConstraintType value for %s may be wrong",
                kind,
                catia_name,
                kind,
            )
        return {
            "constraint": catia_name,
            "kind": kind,
            "elements": list(elements),
            # False means CATIA made something other than what was asked for.
            "kind_confirmed": confirmed,
        }

    def _assembly_reference(self: ComContext, name: str) -> Any:  # pragma: no cover
        """A reference to a component, or to geometry inside one.

        `Component/Feature` addresses geometry within a placed part; a bare name
        is the component itself. Splitting on the slash here is what lets a
        single string carry both without a second parameter.
        """
        if "/" in name:
            component_name, _, inner = name.partition("/")
            component = self._component(component_name)
            document = component.ReferenceProduct.Parent
            try:
                element = document.Part.FindObjectByName(inner)
            except Exception as error:  # noqa: BLE001
                raise CatiaOperationError(
                    f"{component_name!r} has nothing named {inner!r} in it. Activate "
                    "that part and call catia_list_features to see what it contains."
                ) from error
            return document.Part.CreateReferenceFromObject(element)

        component = self._component(name)
        return self._document().Product.CreateReferenceFromName(str(component.Name))

    def constraint_update(  # pragma: no cover - Windows only
        self: ComContext, *, component: str = ""
    ) -> dict[str, Any]:
        """Re-solve the constraints and move the components to satisfy them."""
        target = self._component(component) if component else self._product()
        try:
            target.Update()
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "CATIA could not satisfy the constraints. That usually means two of "
                "them contradict each other, or one references geometry that has been "
                f"deleted. Run catia_assembly_analysis for the details. ({error})"
            ) from error
        return {"updated": component or str(self._product().Name)}

    def constraint_set_active(  # pragma: no cover - Windows only
        self: ComContext, *, constraint: str, active: bool
    ) -> dict[str, Any]:
        """Switch one constraint off without deleting it.

        Deactivating is how a mechanism is posed: turn off the constraint that
        holds a lid shut, move it, and turn it back on. Deleting and re-adding
        loses the geometry references.
        """
        constraints = self._product().Connections(_CONSTRAINTS_CONNECTION)
        for index in range(1, int(constraints.Count) + 1):
            candidate = constraints.Item(index)
            if str(candidate.Name) != constraint:
                continue
            # Older releases expose this as a method and newer ones as a
            # property; both spellings mean the same thing here.
            if hasattr(candidate, "Activate"):
                candidate.Activate(bool(active))
            else:
                candidate.Active = bool(active)
            self._product().Update()
            return {"constraint": constraint, "active": bool(active)}

        known = ", ".join(
            str(constraints.Item(i).Name)
            for i in range(1, min(int(constraints.Count), 12) + 1)
        )
        raise CatiaOperationError(
            f"No constraint named {constraint!r} in this assembly. It has: "
            f"{known or '(none)'}."
        )

    # -- placement -----------------------------------------------------------

    def component_move(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        component: str,
        translation: list[float] | None = None,
        axis: str = "",
        angle_deg: float | None = None,
    ) -> dict[str, Any]:
        """Move a component, before or instead of constraining it.

        A moved component stays where it was put only until the constraints are
        re-solved. That is CATIA's behaviour rather than this tool's, and it is
        why the result says which of the two the caller is relying on.
        """
        target = self._component(component)
        if translation is None and angle_deg is None:
            raise CatiaOperationError(
                "Nothing to do: give `translation`, or `axis` and `angle_deg`."
            )

        if angle_deg is not None:
            _rotate(target, axis or "z", float(angle_deg))
        if translation:
            _place(target, [float(value) for value in translation])

        return {
            "component": str(target.Name),
            "translation": list(translation or []),
            "angle_deg": angle_deg,
            "held_by_constraints": _is_constrained(self._product(), str(target.Name)),
        }

    def component_fix(  # pragma: no cover - Windows only
        self: ComContext, *, components: list[str], together: bool = False
    ) -> dict[str, Any]:
        """Pin components in space, or weld them to each other.

        The distinction matters: a fix holds a component at absolute
        coordinates, and a fix-together holds a group in *relative* position
        while leaving the group free to move. Every assembly needs at least one
        of the first, or the whole thing floats.
        """
        constraints = self._product().Connections(_CONSTRAINTS_CONNECTION)
        references = [self._assembly_reference(name) for name in components]

        created: list[str] = []
        if together:
            if len(references) < 2:
                raise CatiaOperationError(
                    "Fixing components together needs at least two of them."
                )
            for other in references[1:]:
                constraint = constraints.AddBiEltCst(
                    _CONSTRAINT_TYPES["fix_together"], references[0], other
                )
                created.append(str(constraint.Name))
        else:
            for reference in references:
                constraint = constraints.AddMonoEltCst(_CONSTRAINT_TYPES["fix"], reference)
                created.append(str(constraint.Name))

        self._product().Update()
        return {"constraints": created, "components": list(components), "together": bool(together)}

    def scene_explode(  # pragma: no cover - Windows only
        self: ComContext, *, depth: str = "first_level", factor: float = 2.0
    ) -> dict[str, Any]:
        """Push components apart from the assembly's centre, to see inside it.

        Built here rather than through DMU's own explode command, which has no
        automation entry point. Each component moves radially away from the
        assembly centre by `factor`, which is what an exploded view is; the
        difference from DMU's is that this one is a real move, so
        `catia_constraint_update` puts everything back.
        """
        root = self._product()
        children = _children(root, recursive=depth == "all_levels")
        if not children:
            raise CatiaOperationError("This assembly has no components to explode.")

        positions = {
            str(child.Name): _position(child) for child in children
        }
        centre = [
            sum(point[axis] for point in positions.values()) / len(positions)
            for axis in range(3)
        ]

        moved = 0
        for child in children:
            origin = positions[str(child.Name)]
            offset = [
                (origin[axis] - centre[axis]) * (float(factor) - 1.0) for axis in range(3)
            ]
            if any(abs(value) > 1e-9 for value in offset):
                _place(child, offset)
                moved += 1

        return {
            "exploded": moved,
            "components": len(children),
            "factor": float(factor),
            # Says plainly that this is undoable, because a DMU explode is a
            # view and this one is not.
            "reversible_with": "catia_constraint_update",
        }


# -- helpers -------------------------------------------------------------------


def _find_component(root: Any, name: str) -> Any | None:  # pragma: no cover - Windows only
    """Depth-first search for a component by instance name or part number."""
    products = root.Products
    for index in range(1, int(products.Count) + 1):
        child = products.Item(index)
        if name in {str(child.Name), str(child.PartNumber)}:
            return child
        found = _find_component(child, name)
        if found is not None:
            return found
    return None


def _component_names(root: Any, limit: int = 40) -> list[str]:  # pragma: no cover
    """Every component name in the tree, for an error message that helps."""
    names: list[str] = []
    for child in _children(root, recursive=True):
        names.append(str(child.Name))
        if len(names) >= limit:
            break
    return names


def _children(root: Any, *, recursive: bool) -> list[Any]:  # pragma: no cover - Windows only
    products = root.Products
    found: list[Any] = []
    for index in range(1, int(products.Count) + 1):
        child = products.Item(index)
        found.append(child)
        if recursive:
            found.extend(_children(child, recursive=True))
    return found


def _position(component: Any) -> list[float]:  # pragma: no cover - Windows only
    """Where a component sits, from the translation part of its placement."""
    matrix = [0.0] * 12
    component.Position.GetComponents(matrix)
    return [matrix[9], matrix[10], matrix[11]]


def _place(component: Any, offset: list[float]) -> None:  # pragma: no cover - Windows only
    """Translate a component by `offset`, keeping its orientation.

    `Move.Apply` takes the full twelve-number placement — nine for rotation,
    three for translation — so the current one is read, the translation added,
    and the whole thing written back. Passing an identity rotation instead would
    silently un-rotate any component that had been turned.
    """
    matrix = [0.0] * 12
    component.Position.GetComponents(matrix)
    matrix[9] += offset[0]
    matrix[10] += offset[1]
    matrix[11] += offset[2]
    component.Move.Apply(matrix)


def _rotate(component: Any, axis: str, angle_deg: float) -> None:  # pragma: no cover
    """Turn a component about one of its own axes, through its current position.

    The placement is nine rotation terms in row-major order followed by three
    translation terms, so the new rotation is `turn · current` and the position
    is carried through untouched — rotating about the component's own centre
    rather than about the assembly origin, which is what "rotate this part"
    means to anyone asking for it.
    """
    index = {"x": 0, "y": 1, "z": 2}.get(axis.lower())
    if index is None:
        raise CatiaOperationError(f"{axis!r} is not an axis. Use 'x', 'y' or 'z'.")

    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    turn = [
        [1.0, 0.0, 0.0, 0.0, cos_a, sin_a, 0.0, -sin_a, cos_a],
        [cos_a, 0.0, -sin_a, 0.0, 1.0, 0.0, sin_a, 0.0, cos_a],
        [cos_a, sin_a, 0.0, -sin_a, cos_a, 0.0, 0.0, 0.0, 1.0],
    ][index]

    matrix = [0.0] * 12
    component.Position.GetComponents(matrix)
    rotated = [
        sum(turn[row * 3 + k] * matrix[k * 3 + column] for k in range(3))
        for row in range(3)
        for column in range(3)
    ]
    component.Move.Apply([*rotated, matrix[9], matrix[10], matrix[11]])


def _unit(direction: list[float]) -> tuple[float, float, float]:
    """A unit vector, refusing the zero one rather than dividing by its length."""
    x, y, z = (float(value) for value in direction)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        raise CatiaOperationError(
            "A direction of [0, 0, 0] has no direction. Give a vector with at least "
            "one non-zero component."
        )
    return (x / length, y / length, z / length)


def _set_orientation(constraint: Any, orientation: str) -> None:  # pragma: no cover
    """Which way round a constraint faces — same, opposite, or let CATIA decide.

    Silently ignored where a release does not expose it, because the constraint
    itself is correct without it and refusing the whole operation over the
    orientation would be worse than leaving CATIA's default in place.
    """
    values = {"same": 1, "opposite": 2, "undefined": 0}
    try:
        constraint.Orientation = values[orientation]
    except Exception:  # noqa: BLE001 - not exposed on every constraint type
        logger.debug("Constraint orientation %r not settable here", orientation)


def _is_constrained(product: Any, name: str) -> bool:  # pragma: no cover - Windows only
    """Whether any constraint references this component.

    Answers the question a caller who just moved something actually has: will
    this stay where I put it, or will the next update pull it back?
    """
    try:
        constraints = product.Connections(_CONSTRAINTS_CONNECTION)
        for index in range(1, int(constraints.Count) + 1):
            constraint = constraints.Item(index)
            for slot in range(1, 3):
                try:
                    reference = constraint.GetConstraintElement(slot)
                except Exception:  # noqa: BLE001 - fewer elements than asked for
                    break
                if name in str(reference.DisplayName):
                    return True
    except Exception:  # noqa: BLE001 - no constraint set yet
        return False
    return False
