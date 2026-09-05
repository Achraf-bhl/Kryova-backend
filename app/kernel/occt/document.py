"""A part being built: the OCAF document, its feature labels, and its current shape.

`PartDocument` is what an operation runs against. It owns three things and nothing else:

* the OCAF document, where persistent names live (`naming.py`);
* an ordered list of features, each owning its label triple for the life of the
  document — the contract that makes regeneration possible at all;
* the current result shape, which is what gets measured, exported and asserted on.

**Why features are objects rather than just calls.** A plan replayed twice must reuse
its labels (naming rule 2), so something has to remember which labels belong to
`plate.body` between generations. That is this class. The design IR's semantic name is
the key, so the two naming systems — the design's and OCCT's — meet in exactly one
place instead of being correlated by position anywhere else.

**Materials.** Density is held on the document rather than threaded through every
measurement call, because a part has one material at a time and passing it around is how
a mass eventually gets computed against the wrong one. `app.solve.materials` is the
single source of truth for the numbers; this stores only what was chosen.

**The measurement cache is a latency decision, not a nicety.** Every mutating operation
returns post-state, and a plan is 10⁵–10⁶ operations. Measuring integrates over the
whole shape, so re-measuring an unchanged part is the most expensive way to learn
nothing. A feature's shape is immutable once built, which makes the cache trivially
correct: it is keyed on the feature and dropped the moment the part changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.kernel.errors import GeometryError, NamingError
from app.kernel.measurement import Detail
from app.kernel.occt import metrology
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.naming import (
    FeatureLabels,
    NameRegistry,
    allocate_feature_labels,
    descendants_of,
)
from app.kernel.occt.reference import AxisSystem, ReferencePlane, ReferencePoint

#: OCAF storage format. "BinOcaf" is the binary one; "XmlOcaf" is readable, slower and
#: larger. Nothing persists documents to disk yet, so this only decides what a future
#: save would write.
OCAF_FORMAT = "BinOcaf"

#: CATIA's name for the body every part starts with. A design that never mentions bodies
#: works entirely inside this one, which is what keeps multi-body from changing the
#: meaning of any existing plan.
DEFAULT_BODY = "PartBody"


@dataclass
class Feature:
    """One built element of the part, and the labels that keep its name stable."""

    name: str
    tool: str
    labels: FeatureLabels
    shape: Any = None

    #: The faces and edges of the part that this feature contributed — what
    #: `feature#selector` resolves against (master plan 2.2). Recorded at build time from
    #: OCCT's own history, because it cannot be recovered afterwards: once a boolean has
    #: run, nothing in the result says which faces came from which argument.
    #:
    #: `None` means *not recorded*, which is deliberately different from an empty list.
    #: Empty says the feature contributed no surviving face; None says this operation
    #: does not yet report its contribution, and a selector against it is refused rather
    #: than silently falling back to the whole part.
    contributed_faces: list[Any] | None = None
    contributed_edges: list[Any] | None = None

    #: What CATIA would have called it. Reported back to the executor so a plan's
    #: late-bound `Created(feature)` resolves identically on either backend — the design
    #: layer must not be able to tell which kernel ran.
    catia_style_name: str = ""

    #: Which body this feature was built in, recorded at build time. A feature cannot be
    #: moved between bodies afterwards, so this is a fact about the build rather than a
    #: mutable property — and it is what lets a listing say where each feature went.
    body: str = DEFAULT_BODY

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool": self.tool,
            "catia_name": self.catia_style_name,
            "body": self.body,
        }


@dataclass
class PartDocument:
    """One part, mid-construction."""

    name: str
    material: str | None = None
    density_kg_m3: float | None = None

    _application: Any = field(default=None, repr=False)
    _document: Any = field(default=None, repr=False)
    _names: NameRegistry | None = field(default=None, repr=False)
    _features: list[Feature] = field(default_factory=list, repr=False)
    _by_name: dict[str, Feature] = field(default_factory=dict, repr=False)
    #: Bodies by name, each holding its own shape. `PartBody` is CATIA's default name
    #: for the one every part starts with, and a design that never mentions bodies uses
    #: only that one — which is why every operation can go on reading `document.shape`.
    _bodies: dict[str, Any] = field(default_factory=lambda: {DEFAULT_BODY: None}, repr=False)
    _active_body: str = field(default=DEFAULT_BODY, repr=False)
    _planes: dict[str, ReferencePlane] = field(default_factory=dict, repr=False)
    _points: dict[str, ReferencePoint] = field(default_factory=dict, repr=False)
    _axis_systems: dict[str, AxisSystem] = field(default_factory=dict, repr=False)
    #: Geometrical sets by name → whether the set is ordered. Organisation only: this
    #: backend addresses construction geometry by name rather than by tree position, so
    #: a set records the grouping a design asked for without changing what resolves.
    _sets: dict[str, bool] = field(default_factory=dict, repr=False)
    _tool_counters: dict[str, int] = field(default_factory=dict, repr=False)
    _measurement_cache: dict[Detail, dict[str, Any]] = field(default_factory=dict, repr=False)

    #: Sketches by the design's own name. A sketch is not geometry — it is a profile a
    #: later feature consumes — so it lives beside the features rather than among them.
    #: Keyed by name because `catia_pad(sketch=@plate.profile)` addresses it by name;
    #: resolving "the last sketch drawn" is how a second sketch steals a pad.
    _sketches: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        require()
        text = symbol("TCollection_ExtendedString")
        self._application = symbol("TDocStd_Application")()
        self._document = symbol("TDocStd_Document")(text(OCAF_FORMAT))
        self._application.NewDocument(text(OCAF_FORMAT), self._document)
        self._names = NameRegistry(self._document.Main())

    # -- structure -----------------------------------------------------------

    @property
    def root(self) -> Any:
        return self._document.Main()

    @property
    def names(self) -> NameRegistry:
        if self._names is None:  # pragma: no cover - set in __post_init__
            raise GeometryError("This document was not initialised.")
        return self._names

    @property
    def shape(self) -> Any:
        """The **active body** as it currently stands. None until something is built.

        A part holds several bodies (master plan 2.5) and features land in whichever one
        is active, exactly as CATIA's *Define In Work Object* decides. Every operation
        reads and writes the part through this property, so making it the active body
        rather than a single shape is what makes multi-body work without every operation
        learning about bodies.
        """
        return self._bodies.get(self._active_body)

    @property
    def active_body(self) -> str:
        """Which body new features are added to."""
        return self._active_body

    @property
    def sets(self) -> dict[str, bool]:
        """Geometrical sets by name, mapped to whether each is ordered."""
        return self._sets

    def add_set(self, name: str, *, ordered: bool = False) -> str:
        """Record a geometrical set. Re-declaring one is a no-op, not an error, because
        a regeneration re-runs the call that made it and a set holds no geometry to lose."""
        self._sets[name] = ordered
        return name

    def set_names(self) -> list[str]:
        return list(self._sets)

    def body_names(self) -> list[str]:
        """Every body in the part, in creation order — the first is CATIA's `PartBody`."""
        return list(self._bodies)

    def add_body(self, name: str, *, activate: bool = True) -> str:
        """Create an empty body. Re-creating one by the same name is refused.

        Refused rather than replaced, unlike sketches and planes: a body holds built
        geometry, so silently emptying one on a regeneration would delete a part of the
        design that nothing else records.
        """
        if name in self._bodies:
            raise GeometryError(
                f"This part already has a body called {name!r}. Bodies hold built "
                f"geometry, so this one is not replaced — activate it with "
                f"catia_body_activate, or choose another name. Bodies so far: "
                f"{', '.join(self._bodies)}."
            )
        self._bodies[name] = None
        if activate:
            self._active_body = name
        return name

    def activate_body(self, name: str) -> str:
        """Choose which body new features are added to."""
        if name not in self._bodies:
            known = ", ".join(self._bodies)
            raise NamingError(
                f"No body called {name!r} in {self.name}. Bodies here: {known}."
            )
        self._active_body = name
        # The measurement cache is keyed by detail level, not by body, so switching
        # bodies must clear it or the next `measure()` reports the previous body's
        # numbers under the new body's name.
        self._measurement_cache.clear()
        return name

    def __iter__(self) -> Iterator[Feature]:
        return iter(self._features)

    def __len__(self) -> int:
        return len(self._features)

    def feature(self, name: str) -> Feature:
        try:
            return self._by_name[name]
        except KeyError:
            known = ", ".join(f.name for f in self._features) or "nothing yet"
            raise NamingError(
                f"No feature called {name!r} in {self.name}. Built so far: {known}."
            ) from None

    def has_feature(self, name: str) -> bool:
        return name in self._by_name

    def feature_labels(self) -> list[Any]:
        """Every label the current generation wrote — what `NameRegistry.resolve` needs."""
        labels: list[Any] = []
        for feature in self._features:
            labels.extend(feature.labels.all())
        return labels

    def feature_names(self) -> list[str]:
        return [feature.catia_style_name for feature in self._features]

    # -- sketches and bodies -------------------------------------------------

    @property
    def sketches(self) -> dict[str, Any]:
        """Open sketches, by the design's own name for each."""
        return self._sketches

    def add_sketch(self, sketch: Any) -> Any:
        """Open a sketch. Re-opening one by the same name replaces its profiles.

        Replacement rather than refusal because a regeneration re-runs
        `catia_sketch_create` for a sketch that already exists, and the second run must
        start from an empty profile list — otherwise every rebuild doubles the outline
        and the pad silently comes out with two overlapping boundaries.
        """
        self._sketches[sketch.name] = sketch
        return sketch

    def sketch(self, name: str) -> Any:
        try:
            return self._sketches[name]
        except KeyError:
            known = ", ".join(self.sketch_names()) or "none"
            raise NamingError(
                f"No sketch called {name!r} in {self.name}. Open sketches: {known}."
            ) from None

    def sketch_names(self) -> list[str]:
        return sorted(self._sketches)

    # -- reference geometry ---------------------------------------------------

    def add_plane(self, plane: ReferencePlane) -> ReferencePlane:
        """Record a constructed plane under the design's own name.

        Replaces one of the same name for the reason `add_sketch` does: a regeneration
        re-runs the operation that made it, and refusing the second run would make every
        rebuild fail on the first constructed plane.
        """
        self._planes[plane.name] = plane
        return plane

    def plane(self, name: str) -> ReferencePlane:
        try:
            return self._planes[name]
        except KeyError:
            known = ", ".join(self.plane_names()) or "none"
            raise NamingError(
                f"No constructed plane called {name!r} in {self.name}. The origin planes "
                f"are XY, YZ and ZX; constructed planes here: {known}."
            ) from None

    def has_plane(self, name: str) -> bool:
        return name in self._planes

    def plane_names(self) -> list[str]:
        return sorted(self._planes)

    def add_point(self, point: ReferencePoint) -> ReferencePoint:
        self._points[point.name] = point
        return point

    def point(self, name: str) -> ReferencePoint:
        try:
            return self._points[name]
        except KeyError:
            known = ", ".join(self.point_names()) or "none"
            raise NamingError(
                f"No point called {name!r} in {self.name}. Points here: {known}."
            ) from None

    def has_point(self, name: str) -> bool:
        return name in self._points

    def point_names(self) -> list[str]:
        return sorted(self._points)

    def add_axis_system(self, system: AxisSystem) -> AxisSystem:
        self._axis_systems[system.name] = system
        return system

    def axis_system(self, name: str) -> AxisSystem:
        try:
            return self._axis_systems[name]
        except KeyError:
            known = ", ".join(self.axis_system_names()) or "none"
            raise NamingError(
                f"No axis system called {name!r} in {self.name}. Axis systems here: "
                f"{known}."
            ) from None

    def has_axis_system(self, name: str) -> bool:
        return name in self._axis_systems

    def axis_system_names(self) -> list[str]:
        return sorted(self._axis_systems)

    def body(self, name: str) -> Any:
        """A named body's shape, or a named feature's — what `tool_body` refers to.

        **Bodies are checked first**, because that is what the word means: a design that
        built `Body.2` and asks a boolean to subtract `Body.2` means the body, not some
        feature that happens to share the name. A feature name still resolves, which is
        what it meant before multi-body existed and what a single-body design relies on.
        """
        if name in self._bodies:
            shape = self._bodies[name]
            if shape is None:
                raise NamingError(
                    f"The body {name!r} is empty — nothing has been built in it yet, so "
                    "it cannot be combined with anything."
                )
            return shape

        feature = self.feature(name)
        if feature.shape is None:
            raise NamingError(
                f"{name!r} exists but produced no geometry, so it cannot be used as a "
                "body in a boolean."
            )
        return feature.shape

    # -- building ------------------------------------------------------------

    def add_feature(self, name: str, tool: str) -> Feature:
        """Reserve a feature's labels. Once per feature per document, forever.

        Re-adding an existing name returns the same `Feature` — that is a regeneration
        rewriting its own labels, which is required. What it must never do is allocate a
        second label triple for the same name: the selectors recorded against the first
        set would stop resolving, and `Solve()` would not say so.
        """
        existing = self._by_name.get(name)
        if existing is not None:
            if existing.tool != tool:
                raise GeometryError(
                    f"{name!r} was built by {existing.tool} and is now being rebuilt by "
                    f"{tool}. A feature keeps its operation across a regeneration; "
                    "changing it makes a different feature, which needs a different name."
                )
            return existing

        feature = Feature(
            name=name,
            tool=tool,
            labels=allocate_feature_labels(self.root),
            catia_style_name=self._next_catia_name(tool),
        )
        self._features.append(feature)
        self._by_name[name] = feature
        return feature

    def set_result(
        self,
        feature: Feature,
        shape: Any,
        *,
        contributed: tuple[list[Any], list[Any]] | None = None,
        evolved_by: Any = None,
    ) -> None:
        """Record what a feature produced and make it the part's current shape.

        `contributed` is the (faces, edges) this feature added to the part, from
        `naming.contribution_of`. Passed in rather than derived here because only the
        operation holds the algorithm whose history answers it, and that history is gone
        the moment the operation returns.

        `evolved_by` is that same algorithm, and it does the other half of the job:
        **every earlier feature's contribution is carried through it.** Without this, a
        recorded contribution is only true until the next operation touches it — fusing a
        boss onto a pad replaces the pad's top face with an annulus, and the pad would go
        on holding the face that no longer exists, so `pad#top` would resolve to nothing
        while looking perfectly healthy. This is the topological naming problem in
        miniature, and the same answer applies: follow the history rather than keep the
        old handle.
        """
        if evolved_by is not None:
            for earlier in self._features:
                if earlier is feature or earlier.contributed_faces is None:
                    continue
                earlier.contributed_faces = descendants_of(
                    evolved_by, earlier.contributed_faces, kind="face"
                )
                earlier.contributed_edges = descendants_of(
                    evolved_by, earlier.contributed_edges or [], kind="edge"
                )
        if shape is None or shape.IsNull():
            raise GeometryError(
                f"{feature.tool} produced no geometry for {feature.name!r}. The inputs "
                "were accepted but the kernel returned an empty shape — check that the "
                "operation's dimensions are compatible with the geometry it acts on."
            )
        feature.shape = shape
        feature.body = self._active_body
        if contributed is not None:
            feature.contributed_faces, feature.contributed_edges = contributed
        self._bodies[self._active_body] = shape
        self._measurement_cache.clear()

    def _next_catia_name(self, tool: str) -> str:
        """Imitate CATIA's `Pad.1`, `Pad.2` numbering so both backends report alike.

        Not cosmetic. `app.design.compile` emits a `catia_feature_rename` whose target
        is the late-bound name the creating call reported. A backend reporting something
        differently shaped would make the design layer behave differently depending on
        which kernel ran — the one thing the two-backend design exists to prevent.
        """
        stem = tool.removeprefix("catia_").replace("_", " ").title().replace(" ", "")
        self._tool_counters[stem] = self._tool_counters.get(stem, 0) + 1
        return f"{stem}.{self._tool_counters[stem]}"

    # -- reading -------------------------------------------------------------

    def measure(self, *, detail: Detail = Detail.FULL) -> dict[str, Any]:
        """The measurement payload for the part as it stands.

        The *geometric* half is cached per detail level and invalidated by `set_result`,
        because a feature's shape does not change once built and re-integrating over it
        is pure cost.

        **Only the geometric half.** The feature list and the material are not functions
        of the shape, and caching them alongside it made them stale after any operation
        that changes a name without changing geometry — which is exactly what
        `catia_feature_rename` does, and the compiler emits one after almost every
        feature. The symptom was a payload that reported `SurfacePrimitive.1` for a
        feature the design had already renamed `plate.body`: correct geometry, wrong
        names, and nothing to indicate which of the two to believe. So they are overlaid
        fresh on every call, onto a copy the caller may mutate freely.
        """
        payload: dict[str, Any]
        shape = self.shape
        if shape is None:
            payload = {"has_solid": False}
        else:
            cached = self._measurement_cache.get(detail)
            if cached is None:
                cached = metrology.measure(
                    shape, density_kg_m3=self.density_kg_m3, detail=detail
                )
                self._measurement_cache[detail] = cached
            payload = dict(cached)

        payload["features"] = self.feature_names()
        if self.material is not None:
            payload["material"] = self.material
        if len(self._bodies) > 1:
            # Reported only when there is more than one, so a single-body design's
            # payload is unchanged and no assertion written against it starts failing.
            payload["bodies"] = self.body_names()
            payload["active_body"] = self._active_body
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.name,
            "material": self.material,
            "features": [feature.to_dict() for feature in self._features],
            "named": list(self.names.names()),
        }


__all__ = ["DEFAULT_BODY", "OCAF_FORMAT", "Feature", "PartDocument"]
