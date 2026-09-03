"""Knowledgeware over COM: parameters, formulas, design tables, rules, checks.

All of it hangs off `Part.Parameters` and `Part.Relations`, both of which are
plain collections with `Create*` methods — which makes this the least exotic
module in the package and the one with the highest ratio of capability to code.

The ordering constraint worth knowing: a formula can only drive a parameter
that exists, and `catia_sketch_dimension`'s `parameter_name` is what publishes a
sketch dimension under a name a formula can reach. A formula written against
CATIA's own generated dimension name compiles and then breaks the next time the
feature is rebuilt, because that name is not stable.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext, resolve_element

logger = logging.getLogger("kryova.catia.com.knowledge")

#: Which `Parameters` factory call creates each kind, and the unit its value
#: carries. The unit matters: `CreateDimension` needs the magnitude up front and
#: gets it wrong silently if told the wrong one.
_PARAMETER_KINDS = {
    "length": ("CreateDimension", "LENGTH"),
    "angle": ("CreateDimension", "ANGLE"),
    "mass": ("CreateDimension", "MASS"),
    "real": ("CreateReal", None),
    "integer": ("CreateInteger", None),
    "boolean": ("CreateBoolean", None),
    "string": ("CreateString", None),
}

#: What each measurement reads off a `Measurable`, for `measure_publish`.
_MEASUREMENTS = {
    "area": ("Area", "mm2"),
    "volume": ("Volume", "mm3"),
    "length": ("Length", "mm"),
}


class KnowledgeMixin:
    """Parameters and the relations that drive them."""

    def parameter_create(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        name: str,
        kind: str,
        value: Any,
        set: str = "",  # noqa: A002 - the protocol field is named this
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> dict[str, Any]:
        part = self._part()
        call_name, magnitude = _PARAMETER_KINDS[kind]
        collection = part.Parameters
        if set:
            try:
                collection = resolve_element(part, set).DirectParameters
            except Exception as exc:  # noqa: BLE001
                raise CatiaOperationError(
                    f"No parameter set named {set!r}. Create one with "
                    "catia_parameter_set_create first."
                ) from exc

        factory = getattr(collection, call_name)
        parameter = (
            factory(name, magnitude, float(value))
            if magnitude
            else factory(name, value)
        )
        if minimum is not None:
            parameter.SetMinValue(float(minimum))
        if maximum is not None:
            parameter.SetMaxValue(float(maximum))
        part.Update()
        return {"parameter": str(parameter.Name), "kind": kind, "value": value}

    def parameter_set_create(  # pragma: no cover - Windows only
        self: ComContext, *, name: str, parent: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        owner = resolve_element(part, parent) if parent else part.Parameters.RootParameterSet
        created = owner.ParameterSets.CreateSetOfParameters(owner)
        try:
            created.Name = name
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        return {"set": str(created.Name)}

    def formula_create(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        parameter: str,
        expression: str,
        name: str = "",
        active: bool = True,
    ) -> dict[str, Any]:
        part = self._part()
        target = resolve_element(part, parameter)
        formula = part.Relations.CreateFormula(
            name or f"Formula_{parameter}", "", target, expression
        )
        formula.Activate() if active else formula.Deactivate()
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            # A bad expression leaves a relation that fails every later update,
            # so it is removed rather than left to poison the part.
            try:
                part.Relations.Remove(formula.Name)
            except Exception:  # noqa: BLE001
                logger.warning("Could not remove the failed formula", exc_info=True)
            raise CatiaOperationError(
                f"The formula did not compile: {exc}. Expressions reference parameters "
                "by their published names — call catia_list_parameters to see them."
            ) from exc
        return {
            "formula": str(formula.Name),
            "drives": str(target.Name),
            "expression": expression,
            "note": (
                f"{target.Name} is now driven by this formula and can no longer be set "
                "directly with catia_set_parameter."
            ),
        }

    def design_table_create(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        name: str,
        columns: list[str],
        rows: list[list[Any]],
        active_row: int = 1,
    ) -> dict[str, Any]:
        """Drive parameters from a table of configurations.

        CATIA's design table is backed by a file — `CreateDesignTable` takes a
        path to a tab-separated sheet. The rows arrive here as data, so the
        sheet is written into the daemon's own working directory first. The
        model never names that path and never sees it, which is the same rule
        every other transfer in this bridge follows.
        """
        part = self._part()
        for column in columns:
            if len(column) > 80:
                raise CatiaOperationError(f"Column name {column!r} is too long.")
        width = len(columns)
        for index, row in enumerate(rows, start=1):
            if len(row) != width:
                raise CatiaOperationError(
                    f"Row {index} has {len(row)} values but there are {width} columns. "
                    "Every row must give a value for every column."
                )

        path = self.workdir / f"design_table_{name}.txt"
        lines = ["\t".join(columns)]
        lines.extend("\t".join(str(value) for value in row) for row in rows)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        table = part.Relations.CreateDesignTable(name, "", True, str(path))
        try:
            for index, column in enumerate(columns, start=1):
                table.AddNewRelationBetweenParamAndColumn(
                    resolve_element(part, column), index
                )
            table.Configuration = int(active_row)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"The design table could not be linked: {exc}. Every column name must "
                "match an existing parameter exactly — create them first with "
                "catia_parameter_create."
            ) from exc
        return {
            "design_table": str(table.Name),
            "configurations": len(rows),
            "active_row": int(active_row),
        }

    def design_table_activate(  # pragma: no cover - Windows only
        self: ComContext, *, table: str, row: int
    ) -> dict[str, Any]:
        part = self._part()
        target = resolve_element(part, table)
        total = int(target.ConfigurationsNb)
        if not 1 <= int(row) <= total:
            raise CatiaOperationError(
                f"Row {row} is out of range: the table has {total} configurations."
            )
        target.Configuration = int(row)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"Configuration {row} did not rebuild: {exc}. That row's dimensions "
                "are geometrically impossible for this model."
            ) from exc
        return {"design_table": str(target.Name), "active_row": int(row)}

    def rule_create(  # pragma: no cover - Windows only
        self: ComContext, *, name: str, body: str, active: bool = True
    ) -> dict[str, Any]:
        part = self._part()
        rule = part.Relations.CreateProgram(name, "", body)
        rule.Activate() if active else rule.Deactivate()
        return {"rule": str(rule.Name), "active": bool(active)}

    def check_create(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        name: str,
        condition: str,
        message: str = "",
        severity: str = "warning",
    ) -> dict[str, Any]:
        part = self._part()
        check = part.Relations.CreateCheck(name, message, condition)
        try:
            check.Severity = {"information": 0, "warning": 1, "error": 2}[severity]
        except Exception:  # noqa: BLE001 - not every release exposes it
            pass
        part.Update()
        return {
            "check": str(check.Name),
            "passes": bool(check.Evaluate()),
            "severity": severity,
        }

    def knowledge_report(  # pragma: no cover - Windows only
        self: ComContext, *, kind: str = "all", failing_only: bool = False
    ) -> dict[str, Any]:
        part = self._part()
        relations = part.Relations
        wanted = {
            "all": None,
            "formulas": "Formula",
            "rules": "Program",
            "checks": "Check",
            "design_tables": "DesignTable",
        }[kind]

        entries: list[dict[str, Any]] = []
        for index in range(1, int(relations.Count) + 1):
            relation = relations.Item(index)
            kind_name = _relation_kind(relation)
            if wanted is not None and kind_name != wanted:
                continue
            entry: dict[str, Any] = {"name": str(relation.Name), "kind": kind_name}
            try:
                entry["active"] = bool(relation.Activated)
            except Exception:  # noqa: BLE001
                pass
            if kind_name == "Check":
                try:
                    entry["passes"] = bool(relation.Evaluate())
                except Exception:  # noqa: BLE001 - an uncheckable check
                    entry["passes"] = None
                if failing_only and entry.get("passes") is not False:
                    continue
            elif failing_only:
                continue
            entries.append(entry)

        return {"relations": entries, "count": len(entries)}

    def measure_publish(  # pragma: no cover - Windows only
        self: ComContext, *, name: str, measurement: str, elements: list[str]
    ) -> dict[str, Any]:
        """Publish a measurement as a parameter that updates with the model.

        CATIA's own "measure into a parameter" is an interactive command with no
        automation equivalent, so this creates the parameter and writes the
        measured value into it. The difference is real and is reported: the
        value is correct now and does *not* follow later edits by itself.
        Pretending otherwise would be the worse failure — a check written
        against a stale number reads as passing.
        """
        part = self._part()
        workbench = part.Parent.GetWorkbench("SPAWorkbench")
        target = resolve_element(part, elements[0])
        measurable = workbench.GetMeasurable(part.CreateReferenceFromObject(target))

        if measurement in _MEASUREMENTS:
            attribute, unit = _MEASUREMENTS[measurement]
            value = float(getattr(measurable, attribute))
        elif measurement == "distance":
            if len(elements) < 2:
                raise CatiaOperationError("A distance measurement needs two elements.")
            other = resolve_element(part, elements[1])
            value = float(
                measurable.GetMinimumDistance(part.CreateReferenceFromObject(other))
            )
            unit = "mm"
        elif measurement == "mass":
            value = float(part.Parent.GetItem("Inertia").Mass)
            unit = "kg"
        else:
            raise CatiaOperationError(
                f"{measurement!r} cannot be published as a parameter by this bridge. "
                "Use area, volume, length, distance or mass."
            )

        magnitude = {"mm": "LENGTH", "mm2": "AREA", "mm3": "VOLUME", "kg": "MASS"}[unit]
        parameter = part.Parameters.CreateDimension(name, magnitude, value)
        part.Update()
        return {
            "parameter": str(parameter.Name),
            "value": round(value, 6),
            "unit": unit,
            "note": (
                "The value is measured now and stored; it does not re-measure itself "
                "when the model changes. Re-publish after an edit."
            ),
        }


def _relation_kind(relation: Any) -> str:  # pragma: no cover - Windows only
    """Which sort of relation this is, from the COM type name.

    `Relations` is heterogeneous and its items carry no `Kind`, so the type name
    is what distinguishes a formula from a check. Falls back to the raw name
    rather than guessing, so an unrecognised relation is visible in the report
    instead of being silently filed under the wrong heading.
    """
    for candidate in ("Formula", "Check", "DesignTable", "Program", "Rule", "Law"):
        if candidate.lower() in type(relation).__name__.lower():
            return candidate
    try:
        return str(relation.Type)
    except Exception:  # noqa: BLE001
        return "Relation"
