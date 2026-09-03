"""The design specification — CAD treated as a compilation target, not a document.

**What changes here.** Until now the agent has edited geometry the way a person
does: issue an operation, look at what came back, issue the next one. That model
has no artefact. There is nothing to diff, nothing to replay, nothing to test,
and — the failure that actually bites — no way to change a dimension without
patching a feature tree whose downstream references the change has just
invalidated.

A `DesignSpec` is that missing artefact. It is a complete, declarative
description of a part: the parameters it is built from, the features it is made
of, and the semantic names those features are known by. It is compiled
(`app.design.compile`) into an ordered plan of registry operations. **Nothing
edits a feature tree; a change edits the spec and the part is regenerated.**

What that buys is not subtle:

* meaningful diffs and version control on a design, because a design is text;
* deterministic replay — the same spec compiles to the same plan, byte for byte;
* automated regression tests over designs, because a spec can be compiled
  without a CATIA seat anywhere near it;
* immunity to topological naming, because there is no downstream edit to break;
* and the agent edits *text*, which is the one thing a language model is
  reliably good at, instead of blind geometry.

**This module is deliberately ignorant of the operation registry.** It knows a
feature has an operation *name* and a bag of arguments; it does not know whether
`catia_pad` exists or what it takes. That separation is what lets a spec be
loaded, diffed and stored by code that has no business importing the CATIA
layer — and the registry check is not lost, it happens in `compile`, which is
the only place that can do it properly anyway because half the arguments are
expressions that need resolving first.

**Serialisation is canonical.** `to_dict` emits keys in a fixed order and omits
nothing that would change meaning, so `digest()` is stable across machines and
Python versions. A digest that moved when a dict happened to be built in a
different order would make roadmap I5 (same spec ⇒ same geometry) unfalsifiable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.design.errors import FeatureError, SpecError
from app.design.names import SemanticName
from app.design.params import Parameter, ParameterSet, Unit

#: Bumped when the serialised shape changes in a way an older reader would get
#: wrong. Refused rather than guessed at on load, for the same reason the name
#: table refuses one: a half-understood design is worse than no design.
FORMAT_VERSION: Final = 1

#: How an argument says "this is an expression, not a literal". Borrowed from
#: every spreadsheet and from CATIA's own formula editor, because an argument
#: value is often a plain string (`"XY"`, a sketch name) and the marker has to
#: be unambiguous against those.
EXPRESSION_PREFIX: Final = "="

#: How an argument says "this is another feature in this design". Without a
#: marker, `{"sketch": "plate.profile"}` is ambiguous between a semantic name
#: and a CATIA element that happens to be called that — and resolving it the
#: wrong way puts the feature on the wrong geometry without erroring, which is
#: the whole class of failure this package exists to remove.
REFERENCE_PREFIX: Final = "@"


@dataclass(frozen=True)
class FeatureSpec:
    """One element the design creates, and how.

    `note` is not decoration. It is the rationale slot the roadmap asks for in
    H5 — "why is this rib here" must have an answer in six months, and the
    place that answer survives is next to the rib, in the artefact that is
    version-controlled, not in a chat transcript that gets trimmed.
    """

    name: str
    op: str
    args: Mapping[str, Any] = field(default_factory=dict)

    #: An expression which, when false, suppresses this feature. The feature
    #: stays in the spec — that is the point. A design with a lightening pocket
    #: that only exists above a certain plate thickness should say so, rather
    #: than existing in two half-maintained variants of the spec.
    when: str | None = None

    #: Why this feature exists. Free text, carried through to the plan.
    note: str = ""

    def __post_init__(self) -> None:
        # Parsing here rather than storing a SemanticName keeps the dataclass
        # trivially serialisable, and still refuses a malformed name at the
        # point it is written rather than at compile time.
        SemanticName.parse(self.name)
        if not isinstance(self.op, str) or not self.op:
            raise FeatureError(f"{self.name}: needs an operation name, e.g. 'catia_pad'.")
        if not isinstance(self.args, Mapping):
            raise FeatureError(f"{self.name}: arguments must be a mapping, got {type(self.args)}.")
        for key in self.args:
            if not isinstance(key, str):
                raise FeatureError(f"{self.name}: argument names must be strings, got {key!r}.")

    @property
    def semantic_name(self) -> SemanticName:
        return SemanticName.parse(self.name)

    def to_dict(self) -> dict[str, Any]:
        """Canonical form: fixed key order, optional keys omitted when unset."""
        out: dict[str, Any] = {"name": self.name, "op": self.op}
        if self.args:
            out["args"] = {key: self.args[key] for key in sorted(self.args)}
        if self.when is not None:
            out["when"] = self.when
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FeatureSpec:
        unknown = set(data) - {"name", "op", "args", "when", "note"}
        if unknown:
            raise FeatureError(
                f"Feature {data.get('name')!r} carries unknown keys {sorted(unknown)}. A "
                "key this build does not understand is one it would silently drop."
            )
        return cls(
            name=str(data["name"]),
            op=str(data["op"]),
            args=dict(data.get("args") or {}),
            when=data.get("when"),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class DesignSpec:
    """A complete, compilable description of one part.

    Feature order is the build order and is significant — a pad cannot precede
    the sketch it extrudes. It is kept as written rather than derived from the
    reference graph, because a topological sort of features would silently
    reorder two independent features and produce a different feature tree from
    the same spec on a different day. Order is the author's, and the compiler
    only checks that references point backwards.
    """

    name: str
    parameters: ParameterSet = field(default_factory=ParameterSet)
    features: tuple[FeatureSpec, ...] = ()

    #: The material the part is made of, by `app.solve.materials` key. Optional
    #: because a spec may be geometry-only, but a spec that is going to be
    #: simulated or weighed needs it — and `catia_set_material` silently falls
    #: back to steel on an unrecognised slug, so a wrong key here comes back as
    #: a mass roughly three times too heavy rather than as an error.
    material: str | None = None

    #: Free text: what this part is for. Read by nothing, carried everywhere.
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise SpecError("A design needs a name; it becomes the CATIA document name.")
        seen: dict[str, int] = {}
        for index, feature in enumerate(self.features):
            if feature.name in seen:
                raise FeatureError(
                    f"Two features are both called {feature.name!r} (positions "
                    f"{seen[feature.name]} and {index}). A design refers to its own "
                    "elements by name, so a duplicate makes every reference to it a "
                    "coin flip."
                )
            seen[feature.name] = index

    # -- construction --------------------------------------------------------

    @classmethod
    def of(
        cls,
        name: str,
        *,
        parameters: Iterable[Parameter] = (),
        features: Iterable[FeatureSpec] = (),
        material: str | None = None,
        description: str = "",
    ) -> DesignSpec:
        """The ergonomic constructor: takes plain iterables, builds the frozen form."""
        return cls(
            name=name,
            parameters=ParameterSet.of(parameters),
            features=tuple(features),
            material=material,
            description=description,
        )

    def with_features(self, features: Iterable[FeatureSpec]) -> DesignSpec:
        """A copy with a different feature list. Specs are frozen; edits are copies."""
        return DesignSpec(
            name=self.name,
            parameters=self.parameters,
            features=tuple(features),
            material=self.material,
            description=self.description,
        )

    def with_parameters(self, parameters: Iterable[Parameter]) -> DesignSpec:
        return DesignSpec(
            name=self.name,
            parameters=ParameterSet.of(parameters),
            features=self.features,
            material=self.material,
            description=self.description,
        )

    def set_parameter(self, name: str, value: float) -> DesignSpec:
        """A copy with one decision changed — the edit a regeneration exists for.

        Refuses a derived parameter: overwriting a consequence with a literal
        would leave the formula in the spec, unused and untrue, which is the
        worst of both. Change what it is derived *from*.
        """
        found = False
        updated: list[Parameter] = []
        for parameter in self.parameters:
            if parameter.name != name:
                updated.append(parameter)
                continue
            if parameter.is_derived:
                raise SpecError(
                    f"{name} is derived from {parameter.expression!r}, so setting it to a "
                    "number would leave a formula in the spec that no longer describes "
                    "the value. Change one of the parameters it reads instead."
                )
            found = True
            updated.append(
                Parameter(
                    name=parameter.name,
                    unit=parameter.unit,
                    value=float(value),
                    description=parameter.description,
                )
            )
        if not found:
            known = ", ".join(self.parameters.names()) or "none"
            raise SpecError(f"No parameter called {name!r} in this design. Declared: {known}.")
        return self.with_parameters(updated)

    # -- reading -------------------------------------------------------------

    def __iter__(self) -> Iterator[FeatureSpec]:
        return iter(self.features)

    def feature(self, name: str) -> FeatureSpec:
        for candidate in self.features:
            if candidate.name == name:
                return candidate
        known = ", ".join(f.name for f in self.features) or "none"
        raise FeatureError(f"No feature called {name!r} in this design. Features: {known}.")

    def feature_names(self) -> tuple[str, ...]:
        """In build order, not sorted — the order is the design."""
        return tuple(feature.name for feature in self.features)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The canonical serialised form. Key order is fixed, not incidental."""
        out: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "name": self.name,
        }
        if self.description:
            out["description"] = self.description
        if self.material is not None:
            out["material"] = self.material
        out["parameters"] = [_parameter_to_dict(p) for p in self.parameters]
        out["features"] = [feature.to_dict() for feature in self.features]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DesignSpec:
        version = data.get("format_version")
        if version != FORMAT_VERSION:
            raise SpecError(
                f"This design is format version {version!r}; this build reads version "
                f"{FORMAT_VERSION}. Refusing to guess at the difference."
            )
        unknown = set(data) - {
            "format_version",
            "name",
            "description",
            "material",
            "parameters",
            "features",
        }
        if unknown:
            raise SpecError(
                f"This design carries unknown top-level keys {sorted(unknown)}. A key "
                "this build does not understand is one it would silently drop."
            )
        return cls.of(
            name=str(data["name"]),
            parameters=[_parameter_from_dict(p) for p in data.get("parameters") or ()],
            features=[FeatureSpec.from_dict(f) for f in data.get("features") or ()],
            material=data.get("material"),
            description=str(data.get("description") or ""),
        )

    def to_json(self) -> str:
        """Stable JSON: sorted nowhere it would change meaning, sorted everywhere else."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    @classmethod
    def from_json(cls, text: str) -> DesignSpec:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SpecError(f"This design is not valid JSON: {exc}.") from None
        if not isinstance(data, dict):
            raise SpecError("A serialised design is a JSON object.")
        return cls.from_dict(data)

    def digest(self) -> str:
        """A content hash of the design. Two specs with this digest are the same design.

        Used as the identity of a design version — what a simulation result is
        bound to (roadmap D11) and what determinism is checked against (I5). It
        covers everything `to_dict` covers, which is everything that changes
        what gets built, and nothing else: two specs that differ only in the
        order a `dict` literal was written have the same digest, because
        `to_dict` sorts argument keys.
        """
        canonical = json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- parameter serialisation ------------------------------------------------
#
# Kept as functions here rather than as methods on `Parameter` so that
# `app.design.params` stays a module about arithmetic and dimensions, with no
# opinion about the file format a design happens to be stored in.


def _parameter_to_dict(parameter: Parameter) -> dict[str, Any]:
    out: dict[str, Any] = {"name": parameter.name}
    if parameter.unit is not Unit.NONE:
        out["unit"] = parameter.unit.value
    if parameter.expression is not None:
        out["expression"] = parameter.expression
    else:
        out["value"] = parameter.value
    if parameter.description:
        out["description"] = parameter.description
    return out


def _parameter_from_dict(data: Mapping[str, Any]) -> Parameter:
    unknown = set(data) - {"name", "unit", "value", "expression", "description"}
    if unknown:
        raise SpecError(
            f"Parameter {data.get('name')!r} carries unknown keys {sorted(unknown)}."
        )
    raw_unit = data.get("unit", "")
    try:
        unit = Unit(raw_unit)
    except ValueError:
        allowed = ", ".join(repr(u.value) for u in Unit)
        raise SpecError(
            f"Parameter {data.get('name')!r} is declared in {raw_unit!r}, which is not a "
            f"unit a CATIA parameter can carry. Allowed: {allowed}."
        ) from None
    return Parameter(
        name=str(data["name"]),
        unit=unit,
        value=data.get("value"),
        expression=data.get("expression"),
        description=str(data.get("description") or ""),
    )


# -- argument helpers -------------------------------------------------------
#
# Three one-line predicates, in the module that owns the markers, so no consumer
# has to re-derive "does a leading '=' mean expression" from the docstring.


def is_expression(value: Any) -> bool:
    """Is this argument an expression to evaluate rather than a literal?"""
    return isinstance(value, str) and value.startswith(EXPRESSION_PREFIX)


def is_reference(value: Any) -> bool:
    """Is this argument a reference to another feature in the same design?"""
    return isinstance(value, str) and value.startswith(REFERENCE_PREFIX)


def reference_target(value: str) -> str:
    """The semantic name inside a `@reference`."""
    return value[len(REFERENCE_PREFIX) :].strip()


def expression_source(value: str) -> str:
    """The formula inside an `=expression`, without the marker."""
    return value[len(EXPRESSION_PREFIX) :].strip()


def expr(formula: str) -> str:
    """Write an expression argument. `expr("wall_mm * 2")` → `"=wall_mm * 2"`."""
    return f"{EXPRESSION_PREFIX}{formula}"


def ref(name: str | SemanticName) -> str:
    """Write a reference argument. `ref("plate.profile")` → `"@plate.profile"`."""
    return f"{REFERENCE_PREFIX}{name}"


def refs(names: Sequence[str | SemanticName]) -> list[str]:
    """Write a list of reference arguments, for the operations that take several."""
    return [ref(name) for name in names]
