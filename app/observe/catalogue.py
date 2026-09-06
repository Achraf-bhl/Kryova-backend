"""Every span this system can produce, declared — including the ones nothing
emits yet.

This is `app.kernel.contract` applied to timings, and it is here for the same
reason: a report that lists only what it happened to see cannot tell you what it
*failed* to see. Without a declared set of sites, "there is no CalculiX span"
and "CalculiX was never called" and "nobody wired a hook into the CalculiX
runner" are the same silence — and the first two are answers while the third is
a hole in the instrument.

So each site says whether a hook is actually installed. A site with
`wired=False` is a hole, named, with the reason and the file it belongs in; the
report prints it as `UNMEASURED` rather than leaving it out, which is the same
rule `app.design.assertions` applies to a claim nobody measured and
`app.dynamics.clearance` applies to a pose nobody looked at.

Two tests keep this from becoming decoration (`tests/test_observe_report.py`):
every `span("literal")` in `app/` must be declared here, and every site declared
`wired=True` must have its name appear somewhere in `app/`. Break either — add a
span without declaring it, or claim a site is wired when the hook was removed —
and the suite goes red.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Site:
    """One place work can be timed."""

    name: str
    #: The module the hook lives in (or belongs in, when it is not wired yet).
    module: str
    #: What the duration means, in the words an operator would use.
    what: str
    #: Field names the span carries. These are what make a duration explicable:
    #: "40 seconds" against "40 seconds for 1.2 M nodes".
    fields: tuple[str, ...] = ()
    #: False when the site is declared but no hook calls it. The report says so.
    wired: bool = True
    #: Why it is not wired, and what wiring it would take. Required when
    #: `wired` is False, because "not instrumented" with no next step is a
    #: complaint rather than a task.
    not_wired_because: str = ""

    def __post_init__(self) -> None:
        if not self.wired and not self.not_wired_because:
            raise ValueError(f"{self.name} is declared unwired with no reason given")
        if self.wired and self.not_wired_because:
            raise ValueError(f"{self.name} is wired; it needs no excuse")


SITES: Final[tuple[Site, ...]] = (
    Site(
        name="mesh.gmsh.wait",
        module="app.mesh.gmsh_session",
        what="time blocked waiting for the process-global gmsh lock",
        fields=(),
    ),
    Site(
        name="mesh.gmsh.session",
        module="app.mesh.gmsh_session",
        what="time holding the gmsh lock — the window in which nothing else can mesh",
        fields=(),
    ),
    Site(
        name="media.write",
        module="app.media.store",
        what="streaming a blob into the content-addressed store, hashing as it goes",
        fields=("bytes", "deduplicated"),
    ),
    Site(
        name="media.read",
        module="app.media.store",
        what="streaming a blob out of the store, chunk by chunk",
        fields=("bytes", "chunks"),
    ),
    Site(
        name="media.verify",
        module="app.media.store",
        what="re-hashing a stored blob to check it still matches its name",
        fields=("bytes", "intact"),
    ),
    Site(
        name="jobs.wait",
        module="app.observe.queue",
        what="time a job spent queued before a worker picked it up",
        fields=("queue",),
    ),
    Site(
        name="jobs.run",
        module="app.observe.queue",
        what="time a job spent running, whether it succeeded or raised",
        fields=("queue",),
    ),
    # ---- declared, not wired -------------------------------------------------
    # These are the spans Phase 15's question ("why was that slow") will ask for
    # first. They are declared now so the report names them as holes rather than
    # omitting them, and so wiring each one is a two-line change with the name
    # already agreed.
    Site(
        name="solve.calculix.run",
        module="app.solve.calculix.run",
        what="one `ccx` subprocess: argv in, .frd out",
        fields=("degrees_of_freedom", "threads", "returncode"),
        wired=False,
        not_wired_because=(
            "app/solve/ is outside this change's lane. The hook is one `with "
            "span(...)` around the `subprocess.run` in `run.py`; the runner "
            "already measures `seconds` itself, so the span only has to carry it."
        ),
    ),
    Site(
        name="solve.linear_static",
        module="app.solve.linear_static",
        what="in-house assembly, factorisation and stress recovery",
        fields=("nodes", "elements", "degrees_of_freedom"),
        wired=False,
        not_wired_because=(
            "app/solve/ is outside this change's lane. Assembly and solve want "
            "separate spans — a slow factorisation and a slow assembly have "
            "different fixes, and one span over both cannot tell them apart."
        ),
    ),
    Site(
        name="kernel.rebuild",
        module="app.kernel.occt.document",
        what="one OCCT regeneration of a part from its plan",
        fields=("operations", "faces", "solids"),
        wired=False,
        not_wired_because=(
            "app/kernel/ is outside this change's lane. This is the span an "
            "optimisation loop needs most — hundreds of rebuilds where only the "
            "per-rebuild cost says whether a sweep is affordable."
        ),
    ),
    Site(
        name="kernel.measure",
        module="app.kernel.measurement",
        what="computing a measurement payload at a given Detail level",
        fields=("detail", "paths"),
        wired=False,
        not_wired_because=(
            "app/kernel/ is outside this change's lane. `Detail` levels exist "
            "for latency, and nothing currently measures whether the cheap level "
            "is actually cheap."
        ),
    ),
)


BY_NAME: Final[Mapping[str, Site]] = MappingProxyType({site.name: site for site in SITES})

if len(BY_NAME) != len(SITES):  # pragma: no cover - a duplicate is a typo, caught at import
    raise ValueError("two sites share a name")


def wired_names() -> frozenset[str]:
    return frozenset(site.name for site in SITES if site.wired)


def unwired() -> tuple[Site, ...]:
    return tuple(site for site in SITES if not site.wired)
