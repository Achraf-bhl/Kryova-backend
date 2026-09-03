"""Knowledge Advisor in the mock: parameters, formulas, design tables, checks.

The one domain the mock can model *completely*, because none of it is geometry.
A parameter is a name, a value and a unit; a formula is an expression over other
parameters; a check is a condition that is either satisfied or not. All of that
is exactly representable in memory, so these tools give the same answers here as
they would against CATIA rather than an approximation of them.

Formulas actually evaluate. That is the part worth insisting on: a formula that
is stored but never recomputed looks identical in the tree and silently stops
propagating, which is the failure mode a design table is meant to prevent. When
a parameter a formula depends on changes, every formula that reads it is
re-evaluated here, in dependency order, exactly as CATIA's solver would.

`expressions.py` does the evaluation, and it parses rather than `eval`s — these
strings come from a language model and run inside the process that holds a COM
handle to the engineer's CATIA.
"""

from __future__ import annotations

from typing import Any

from ..backend import CatiaOperationError
from ..expressions import ExpressionError, evaluate, parameter_names

#: The unit each parameter kind carries, so a created parameter is typed the
#: way CATIA types it rather than being a bare number.
_KIND_UNITS = {
    "length": "mm",
    "angle": "deg",
    "real": "",
    "integer": "",
    "boolean": "",
    "string": "",
    "mass": "kg",
}

#: How many times a formula chain may be re-evaluated before it is called
#: circular. A part with fifty chained parameters is unusual but legitimate;
#: fifty *rounds* means A depends on B depends on A.
_MAX_SOLVE_ROUNDS = 50


class MockKnowledgeMixin:
    """Parameters, formulas, design tables, rules and checks — all real."""

    # -- state ---------------------------------------------------------------

    def _knowledge_state(self) -> dict[str, Any]:
        """The knowledge tables, created on first use.

        Lazily rather than in `_reset`, so this mixin adds no state to a mock
        that never touches a formula and older checkpoint files stay readable.
        """
        state = getattr(self, "_knowledge", None)
        if state is None:
            state = {
                "formulas": {},
                "sets": {},
                "tables": {},
                "rules": {},
                "checks": {},
                "measures": {},
            }
            self._knowledge = state
        return state

    # -- parameters ----------------------------------------------------------

    def parameter_create(
        self,
        *,
        name: str,
        kind: str,
        value: float | str | bool,
        set: str = "",  # noqa: A002 - the schema's name
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> dict[str, Any]:
        self._require_document()
        if name in self.parameters:
            raise CatiaOperationError(
                f"This part already has a parameter named {name!r}. Set its value with "
                "catia_set_parameter, or choose another name."
            )
        if not name.replace("_", "").isalnum():
            # Formula expressions are parsed, so a parameter whose name is not
            # an identifier could never be referenced from one.
            raise CatiaOperationError(
                f"{name!r} cannot be used as a parameter name — a formula could never "
                "refer to it. Use letters, digits and underscores."
            )

        stored: Any = value
        if kind in {"length", "angle", "real", "mass"}:
            stored = float(value)
        elif kind == "integer":
            stored = int(value)
        elif kind == "boolean":
            stored = bool(value)
        else:
            stored = str(value)

        if minimum is not None and isinstance(stored, (int, float)) and stored < minimum:
            raise CatiaOperationError(
                f"{stored} is below the minimum of {minimum} you set for {name!r}."
            )
        if maximum is not None and isinstance(stored, (int, float)) and stored > maximum:
            raise CatiaOperationError(
                f"{stored} is above the maximum of {maximum} you set for {name!r}."
            )

        self.parameters[name] = {
            "value": stored,
            "unit": _KIND_UNITS[kind],
            "kind": kind,
            "comment": "",
            "set": set or "",
            "minimum": minimum,
            "maximum": maximum,
        }
        self._write_document()
        return {
            "parameter": {"name": name, "value": stored, "unit": _KIND_UNITS[kind]},
            "kind": kind,
            "set": set or None,
        }

    def parameter_set_create(self, *, name: str, parent: str = "") -> dict[str, Any]:
        """A folder for parameters. Organisation only — it holds no value itself."""
        self._require_document()
        state = self._knowledge_state()
        if name in state["sets"]:
            raise CatiaOperationError(f"A parameter set named {name!r} already exists.")
        if parent and parent not in state["sets"]:
            raise CatiaOperationError(
                f"No parameter set named {parent!r} to nest this one in."
            )
        state["sets"][name] = {"parent": parent or None}
        self._write_document()
        return {"set": name, "parent": parent or None, "sets": len(state["sets"])}

    # -- formulas ------------------------------------------------------------

    def formula_create(
        self, *, parameter: str, expression: str, name: str = "", active: bool = True
    ) -> dict[str, Any]:
        """Drive a parameter from an expression over the others.

        Validated against the parameters that exist *before* it is stored, so a
        typo names the missing parameter. A formula stored unchecked would
        instead sit in the tree looking correct and never produce a value.
        """
        self._require_document()
        if parameter not in self.parameters:
            known = ", ".join(sorted(self.parameters)) or "(none)"
            raise CatiaOperationError(
                f"No parameter named {parameter!r} to drive. This part has: {known}."
            )

        try:
            reads = parameter_names(expression)
        except ExpressionError as error:
            raise CatiaOperationError(str(error)) from error

        if parameter in reads:
            raise CatiaOperationError(
                f"The formula for {parameter!r} reads {parameter!r}, so it defines "
                "itself. A parameter cannot be its own input."
            )
        missing = [read for read in reads if read not in self.parameters]
        if missing:
            raise CatiaOperationError(
                f"The formula refers to {', '.join(missing)}, which "
                f"{'is not a parameter' if len(missing) == 1 else 'are not parameters'} "
                "of this part. Create them first with catia_parameter_create."
            )

        state = self._knowledge_state()
        state["formulas"][parameter] = {
            "name": name or f"Formula.{len(state['formulas']) + 1}",
            "expression": expression,
            "active": bool(active),
            "reads": reads,
        }

        try:
            self._solve_formulas()
        except CatiaOperationError:
            # A formula that cannot be solved is not kept: leaving it would
            # wedge every later parameter change behind the same failure.
            del state["formulas"][parameter]
            raise

        self._write_document()
        return {
            "formula": state["formulas"][parameter]["name"],
            "parameter": parameter,
            "expression": expression,
            "value": self.parameters[parameter]["value"],
            "active": bool(active),
        }

    def _solve_formulas(self) -> int:
        """Re-evaluate every active formula until the values stop changing.

        Iterated rather than topologically sorted because the dependency order
        is already implicit in the expressions, and a chain of N formulas
        settles in N rounds. Running out of rounds means a cycle, which is
        reported as one rather than left to loop.
        """
        state = self._knowledge_state()
        formulas = {
            parameter: formula
            for parameter, formula in state["formulas"].items()
            if formula["active"]
        }
        if not formulas:
            return 0

        for round_number in range(_MAX_SOLVE_ROUNDS):
            values = {
                name: entry["value"]
                for name, entry in self.parameters.items()
                if isinstance(entry["value"], (int, float))
            }
            changed = False
            for parameter, formula in formulas.items():
                try:
                    result = evaluate(formula["expression"], values)
                except ExpressionError as error:
                    raise CatiaOperationError(
                        f"The formula for {parameter!r} could not be evaluated: {error}"
                    ) from error
                if self.parameters[parameter]["value"] != result:
                    self.parameters[parameter]["value"] = result
                    changed = True
            if not changed:
                return round_number + 1

        cycle = ", ".join(sorted(formulas))
        raise CatiaOperationError(
            f"These formulas never settle on a value: {cycle}. They depend on each "
            "other in a loop."
        )

    # -- design tables -------------------------------------------------------

    def design_table_create(
        self,
        *,
        name: str,
        columns: list[str],
        rows: list[list[Any]],
        active_row: int = 1,
    ) -> dict[str, Any]:
        """A table of parameter values, one configuration per row."""
        self._require_document()
        missing = [column for column in columns if column not in self.parameters]
        if missing:
            raise CatiaOperationError(
                f"The table has columns for {', '.join(missing)}, which are not "
                "parameters of this part. Create them first."
            )
        wrong = [
            index for index, row in enumerate(rows, start=1) if len(row) != len(columns)
        ]
        if wrong:
            raise CatiaOperationError(
                f"Row {wrong[0]} has {len(rows[wrong[0] - 1])} values for "
                f"{len(columns)} columns. Every row must fill every column."
            )

        state = self._knowledge_state()
        state["tables"][name] = {"columns": list(columns), "rows": [list(r) for r in rows]}
        applied = self._apply_row(name, int(active_row))
        self._write_document()
        return {
            "table": name,
            "columns": list(columns),
            "configurations": len(rows),
            "active_row": int(active_row),
            "applied": applied,
        }

    def design_table_activate(self, *, table: str, row: int) -> dict[str, Any]:
        """Switch the part to one of the table's configurations."""
        self._require_document()
        applied = self._apply_row(table, int(row))
        self._write_document()
        return {
            "table": table,
            "active_row": int(row),
            "applied": applied,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    def _apply_row(self, table: str, row: int) -> dict[str, Any]:
        """Write one configuration's values into the parameters, then re-solve."""
        state = self._knowledge_state()
        entry = state["tables"].get(table)
        if entry is None:
            known = ", ".join(sorted(state["tables"])) or "(none)"
            raise CatiaOperationError(
                f"No design table named {table!r}. This part has: {known}."
            )
        if not 1 <= row <= len(entry["rows"]):
            raise CatiaOperationError(
                f"Row {row} is outside this table, which has {len(entry['rows'])} "
                "configurations. Rows are numbered from 1."
            )

        applied: dict[str, Any] = {}
        for column, value in zip(entry["columns"], entry["rows"][row - 1], strict=False):
            self.parameters[column]["value"] = value
            applied[column] = value
            # A driving parameter reshapes the solid, exactly as setting it by
            # hand would; a table that only changed numbers would be a table of
            # numbers rather than of configurations.
            if isinstance(value, (int, float)):
                self._apply_driving_parameter(column, float(value))
        self._solve_formulas()
        return applied

    # -- rules and checks ----------------------------------------------------

    def rule_create(self, *, name: str, body: str, active: bool = True) -> dict[str, Any]:
        """Record a rule. Stored and reported, not executed.

        Deliberately inert. A CATIA rule is imperative CATVBS, and running one
        here would mean implementing a second scripting language whose behaviour
        could not match CATIA's — which is the "subtly wrong" the mock exists to
        avoid. It is kept so the tree and `catia_knowledge_report` are accurate.
        """
        self._require_document()
        state = self._knowledge_state()
        state["rules"][name] = {"body": body, "active": bool(active)}
        self._write_document()
        return {
            "rule": name,
            "active": bool(active),
            # Said plainly rather than implied: against CATIA this rule runs.
            "evaluated": False,
            "note": "Recorded. Rule bodies run against CATIA, not against the mock.",
        }

    def check_create(
        self, *, name: str, condition: str, message: str = "", severity: str = "warning"
    ) -> dict[str, Any]:
        """A condition over the parameters, evaluated now and on every change.

        Unlike a rule this *is* evaluated, because a check is an expression
        rather than a script — the same grammar a formula uses, which is already
        implemented and already safe.
        """
        self._require_document()
        try:
            reads = parameter_names(condition)
        except ExpressionError as error:
            raise CatiaOperationError(str(error)) from error
        missing = [read for read in reads if read not in self.parameters]
        if missing:
            raise CatiaOperationError(
                f"The check refers to {', '.join(missing)}, which are not parameters "
                "of this part."
            )

        state = self._knowledge_state()
        state["checks"][name] = {
            "condition": condition,
            "message": message,
            "severity": severity,
        }
        self._write_document()
        satisfied = self._evaluate_check(condition)
        return {
            "check": name,
            "condition": condition,
            "satisfied": satisfied,
            "severity": severity,
            "message": message or None,
        }

    def _evaluate_check(self, condition: str) -> bool:
        values = {
            name: entry["value"]
            for name, entry in self.parameters.items()
            if isinstance(entry["value"], (int, float))
        }
        try:
            return bool(evaluate(condition, values))
        except ExpressionError:
            # A check that cannot be evaluated is a failing check, not an
            # error: the part has moved somewhere the condition cannot describe.
            return False

    def knowledge_report(
        self, *, kind: str = "all", failing_only: bool = False
    ) -> dict[str, Any]:
        """What the part's knowledge currently says about itself."""
        self._require_document()
        state = self._knowledge_state()

        checks = [
            {
                "name": name,
                "condition": entry["condition"],
                "satisfied": self._evaluate_check(entry["condition"]),
                "severity": entry["severity"],
                "message": entry["message"],
            }
            for name, entry in sorted(state["checks"].items())
        ]
        if failing_only:
            checks = [check for check in checks if not check["satisfied"]]

        report: dict[str, Any] = {"kind": kind}
        if kind in {"all", "parameters"}:
            report["parameters"] = len(self.parameters)
        if kind in {"all", "formulas"}:
            report["formulas"] = [
                {
                    "name": entry["name"],
                    "parameter": parameter,
                    "expression": entry["expression"],
                    "active": entry["active"],
                    "value": self.parameters[parameter]["value"],
                }
                for parameter, entry in sorted(state["formulas"].items())
            ]
        if kind in {"all", "checks"}:
            report["checks"] = checks
            report["failing_checks"] = sum(1 for check in checks if not check["satisfied"])
        if kind in {"all", "rules"}:
            report["rules"] = [
                {"name": name, "active": entry["active"]}
                for name, entry in sorted(state["rules"].items())
            ]
        if kind in {"all", "design_tables"}:
            report["design_tables"] = [
                {"name": name, "columns": entry["columns"], "configurations": len(entry["rows"])}
                for name, entry in sorted(state["tables"].items())
            ]
        return report

    # -- published measurements ----------------------------------------------

    def measure_publish(
        self, *, name: str, measurement: str, elements: list[str]
    ) -> dict[str, Any]:
        """Turn a measurement into a live parameter other formulas can read.

        Measured against the mock's box, so the value is as approximate as
        everything else it reports — and it updates, which is the property that
        makes a published measure worth having.
        """
        self._require_solid()
        width, depth, height = self.size  # type: ignore[misc]
        values = {
            "length": max(width, depth, height),
            "width": width,
            "depth": depth,
            "height": height,
            "area": 2 * (width * depth + width * height + depth * height),
            "volume": self._net_volume_mm3(),
            "distance": max(width, depth, height),
            "angle": 90.0,
            "radius": min(width, depth) / 2.0,
        }
        if measurement not in values:
            raise CatiaOperationError(
                f"{measurement!r} is not something this can measure. Available: "
                f"{', '.join(sorted(values))}."
            )

        unit = {"area": "mm2", "volume": "mm3", "angle": "deg"}.get(measurement, "mm")
        self.parameters[name] = {
            "value": round(values[measurement], 6),
            "unit": unit,
            "kind": "measure",
            "comment": f"{measurement} of {', '.join(elements)}",
            "set": "",
            "minimum": None,
            "maximum": None,
        }
        state = self._knowledge_state()
        state["measures"][name] = {"measurement": measurement, "elements": list(elements)}
        self._write_document()
        return {
            "parameter": name,
            "measurement": measurement,
            "value": self.parameters[name]["value"],
            "unit": unit,
            "approximate": True,
        }
