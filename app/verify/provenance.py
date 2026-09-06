"""What produced a number, carried with the number.

Master plan 7.3: every result permanently bound to geometry version, mesh
settings, material, load case, solver name **and version**, and the convergence
evidence. Without that binding a stress figure is a rumour — it cannot be
reproduced, it cannot be re-checked after a mesh setting changes, and no
engineer can sign it.

**This is `app.kernel.provenance` applied to a simulation, not a second
provenance model.** That module already settled the vocabulary — measured /
approximated / unavailable-with-a-reason, kept as a *sidecar* so the values stay
plain — and two provenance vocabularies in one codebase is a defect on its own:
a reader would have to learn which one a payload came from before knowing what
`"basis": "measured"` meant. So the bases, the `Record`, the `provenance` key and
`attach()` are imported from there and nothing here redefines them.

What this module adds is the *shape* of a simulation's record, and one honest
answer to a question the kernel never had to ask:

**Which version of the solver?** A `Solver` is an object, not an executable, and
none of the in-process ones declares a version string. Inventing one — the
package version, today's date, "1.0" — would be exactly the fabrication
Decision 3 exists to prevent. So the record carries two fields:

* `version` — whatever the solver declares as `.version`, and `UNAVAILABLE` with
  a reason when it declares nothing. A subprocess solver that can ask its binary
  (`ccx -v`) has a real version and should expose one.
* `code_digest` — the SHA-256 of the source of the module the solver class is
  defined in. Always available, always exact, and it changes when the solver
  changes, which is the property a version string is *wanted* for. It is not a
  substitute for a version number in a support conversation; it is a better one
  in a provenance record.

The environment (Python, numpy, scipy) is recorded beside them, because a direct
sparse solve is as much scipy's answer as it is ours.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from app.kernel.provenance import Record, attach, measured, unavailable
from app.mesh.types import TetMesh, quality
from app.verify.convergence import ConvergenceStudy

#: Cheap and stable: the mesh digest hashes the node coordinates and the
#: connectivity, so two meshes with the same geometry and the same numbering
#: agree and anything else does not.
_DIGEST_PREFIX = "sha256:"


def _sha256(*chunks: bytes) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return _DIGEST_PREFIX + digest.hexdigest()


def mesh_digest(mesh: TetMesh) -> str:
    """A content digest of the discretised geometry a result was computed on.

    Node coordinates and connectivity only — not the quality summary, which is
    derived from them, and not the file the mesh came from, which may not exist
    (a primitive is built in memory). Two runs on the same mesh get the same
    digest; a re-mesh at a different element size does not, which is the whole
    point: a result and the mesh it was computed on travel together or the
    result is worthless.
    """
    return _sha256(
        np.ascontiguousarray(mesh.nodes, dtype=np.float64).tobytes(),
        np.ascontiguousarray(mesh.tets, dtype=np.int64).tobytes(),
        b"" if mesh.midside is None else np.ascontiguousarray(mesh.midside).tobytes(),
    )


def case_digest(case: BaseModel) -> str:
    """A content digest of a load case, modal case or buckling case.

    Taken over the model's canonical JSON, so it covers the material, the
    fixtures, every load and the temperature change — everything that decides
    the answer — and nothing that does not.
    """
    return _sha256(case.model_dump_json().encode("utf-8"))


@dataclass(frozen=True)
class SolverIdentity:
    """Which solver, and which *build* of it, produced a result."""

    name: str
    version: str | None
    code_digest: str
    #: Why `version` is absent, when it is. Empty when a version was declared.
    version_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "code_digest": self.code_digest}
        if self.version is not None:
            payload["version"] = self.version
        return payload

    def version_record(self) -> Record:
        if self.version is None:
            return unavailable(self.version_reason)
        return measured("declared by the solver")


def identify_solver(solver: Any) -> SolverIdentity:
    """Name, declared version and code digest of a solver object.

    Duck-typed on `.version` rather than requiring it on the `Solver` ABC: the
    ABC is deliberately narrow (mesh in, case in, fields out) and widening it to
    demand a version would break every surrogate the seam exists to admit. A
    solver that has one declares it; one that does not is recorded as not having
    one, with the reason spelled out rather than filled in.
    """
    name = str(getattr(solver, "name", type(solver).__name__))
    declared = getattr(solver, "version", None)
    version = str(declared) if declared is not None else None

    try:
        source = Path(inspect.getfile(type(solver)))
        code_digest = _sha256(source.read_bytes())
    except (TypeError, OSError) as exc:  # built-in, or source not on disk
        code_digest = f"unavailable: {exc}"

    return SolverIdentity(
        name=name,
        version=version,
        code_digest=code_digest,
        version_reason=(
            ""
            if version is not None
            else (
                f"The {name!r} solver declares no version string. It runs in this "
                "process, so `solver.code_digest` identifies the code exactly; a "
                "subprocess solver should report its binary's own version here."
            )
        ),
    )


def environment() -> dict[str, str]:
    """The library versions a numerical answer also depends on.

    scipy is in here because a direct sparse solve is SuperLU's answer as much
    as ours, and a SuperLU change moves the last digits of every result.
    """
    import scipy

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "platform": sys.platform,
    }


@dataclass(frozen=True)
class RunProvenance:
    """The complete chain behind one simulation result.

    Assembled by the caller that ran the analysis, because only it knows the
    geometry's origin — a media blob's SHA-256, a design plan digest, or "a
    primitive built in memory". `geometry_source` is free text for exactly that
    reason and `geometry_digest` is the mesh digest, which is always available.
    """

    analysis: str
    quantity: str
    unit: str
    value: float | None
    geometry_source: str
    mesh: TetMesh
    case: BaseModel
    solver: SolverIdentity
    element_size_mm: float | None = None
    convergence: ConvergenceStudy | None = None
    #: Free-form, for anything the caller must record and nothing else models —
    #: the benchmark id, the mission rung, the job row id.
    notes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Refuse a record that cannot say what it is or what produced it.

        7.3 is the binding, and a record with an empty `analysis` or an empty
        `geometry_source` is not a weak binding, it is none: nobody reading it
        later can tell which run it describes. The mesh, the case and the solver
        cannot be empty — they are typed — so these four strings are the whole of
        what a caller can leave blank, and leaving one blank must cost the record
        rather than be discovered by a reader a year later.

        The value is checked against the convergence study for the same reason.
        `stated_value` is the only number a study permits out, so a record
        carrying a converged study *and a different number* is asserting one
        thing in `value` and another in `convergence`, and the whole point of the
        record is that those cannot come apart.
        """
        missing = [
            field_name
            for field_name, text in (
                ("analysis", self.analysis),
                ("quantity", self.quantity),
                ("unit", self.unit),
                ("geometry_source", self.geometry_source),
            )
            if not text.strip()
        ]
        if missing:
            raise ValueError(
                f"A provenance record must say what produced it; {', '.join(missing)} "
                "is blank. A result that cannot name its analysis, its quantity, its "
                "unit and where its geometry came from is not evidence — it is a "
                "number with a hash beside it. Use a phrase like 'primitive box mesh "
                "built in memory' or a media blob's sha256 for geometry_source."
            )

        study = self.convergence
        if study is None or self.value is None:
            return
        stated = study.stated_value
        if stated is None:
            return
        if not math.isclose(self.value, stated, rel_tol=1e-12, abs_tol=0.0):
            raise ValueError(
                f"The recorded value {self.value!r} is not the value its own "
                f"convergence study permits ({stated!r}). A record whose number and "
                "whose evidence disagree is worse than one with no evidence: it reads "
                "as fully substantiated. Record the study's stated value, or record "
                "the study that actually produced this number."
            )

    def to_dict(self) -> dict[str, Any]:
        """The record, with an `app.kernel.provenance` sidecar over its paths.

        Values stay plain and addressable (`mesh.node_count` is a number where a
        reader expects one); how each was arrived at lives under `provenance`.
        `measurable_paths`-style walkers skip the sidecar for free because its
        entries are dicts of strings rather than numbers.
        """
        stats = quality(self.mesh)
        material = getattr(self.case, "material", None)

        payload: dict[str, Any] = {
            "analysis": self.analysis,
            "quantity": self.quantity,
            "unit": self.unit,
            "geometry": {
                "source": self.geometry_source,
                "digest": mesh_digest(self.mesh),
            },
            "mesh": {
                "element_type": self.mesh.element_type,
                "element_order": self.mesh.element_order,
                "node_count": self.mesh.node_count,
                "element_count": self.mesh.tet_count,
                "volume_mm3": stats["volume_mm3"],
                "min_quality": stats["min_quality"],
                "sliver_count": stats["sliver_count"],
            },
            "material": (
                material.model_dump() if isinstance(material, BaseModel) else None
            ),
            "load_case": {
                "name": getattr(self.case, "name", ""),
                "type": type(self.case).__name__,
                "digest": case_digest(self.case),
            },
            "solver": self.solver.to_dict(),
            "environment": environment(),
        }
        if self.notes:
            payload["notes"] = dict(self.notes)

        attach(payload, "geometry.digest", measured("sha256 over node coordinates and connectivity"))
        attach(payload, "mesh.volume_mm3", measured("sum of signed tetrahedron volumes"))
        attach(payload, "load_case.digest", measured("sha256 over the case's canonical JSON"))
        attach(payload, "solver.code_digest", measured("sha256 of the solver module's source"))
        attach(payload, "solver.version", self.solver.version_record())

        if self.element_size_mm is None:
            attach(
                payload,
                "mesh.element_size_mm",
                unavailable(
                    "No target element size was given; the mesh was supplied directly "
                    "rather than generated to a size. `mesh.digest` still identifies it "
                    "exactly."
                ),
            )
        else:
            payload["mesh"]["element_size_mm"] = self.element_size_mm
            attach(
                payload,
                "mesh.element_size_mm",
                measured("the target size requested of the mesher"),
            )

        if material is None:
            attach(
                payload,
                "material",
                unavailable(f"{type(self.case).__name__} carries no material."),
            )

        self._attach_value(payload)
        self._attach_convergence(payload)
        return payload

    def _attach_value(self, payload: dict[str, Any]) -> None:
        """Record the result — or record that there is not one, and why.

        A run whose convergence study did not converge has **no value**, and the
        record says so with the study's own reason. That is 7.2's refusal
        reaching the provenance layer: the number is not merely flagged as
        doubtful, it is absent.
        """
        study = self.convergence
        if study is not None and study.stated_value is None:
            attach(
                payload,
                "value",
                unavailable(
                    "The convergence study did not permit a value to be stated. "
                    + study.reason
                ),
            )
            return
        if self.value is None:
            attach(
                payload,
                "value",
                unavailable("The analysis produced no value for this quantity."),
            )
            return
        payload["value"] = self.value
        attach(
            payload,
            "value",
            measured(
                f"{self.solver.name} on {self.mesh.tet_count} {self.mesh.element_type} "
                "elements"
                if study is None
                else f"{self.solver.name}, converged over {len(study.levels)} grids"
            ),
        )

    def _attach_convergence(self, payload: dict[str, Any]) -> None:
        if self.convergence is None:
            attach(
                payload,
                "convergence",
                unavailable(
                    "No convergence study was run, so this result is a single-grid "
                    "answer and the discretisation error is unknown. Run "
                    "`app.verify.convergence.run_study` over at least three grids "
                    "before quoting it."
                ),
            )
            return
        payload["convergence"] = self.convergence.to_dict()
        attach(
            payload,
            "convergence",
            measured(
                f"Grid Convergence Index over {len(self.convergence.levels)} grids "
                "(Celik et al. 2008 / ASME V&V 20)"
            ),
        )


__all__ = [
    "RunProvenance",
    "SolverIdentity",
    "case_digest",
    "environment",
    "identify_solver",
    "mesh_digest",
]
