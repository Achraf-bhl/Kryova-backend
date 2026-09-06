"""The seam an engine drops into, and the honest account of which ones are behind it.

One ABC, `DynamicsEngine`, with two methods: say whether you can run, and run. It is the
same seam `app.solve.Solver` is, for the same reason -- a surrogate solver must drop in
without the API knowing, and a contact-resolving multibody engine must drop in here
without a caller knowing either.

**Two implementations, and the difference between them is the whole of Phase 9's honest
status.**

* `KinematicEngine` -- **implemented and verified against closed form.** Exact
  serial-chain kinematics plus Newton-Euler inverse dynamics. It answers positions,
  velocities, accelerations and joint reactions for a prescribed-motion tree chain, and
  those are exact rather than integrated. It cannot do contact, friction, springs, end
  stops, closed loops, or any case where the motion is an output rather than an input.
* `ChronoEngine` -- **a seam, not a capability, and it says so at every entry point.**
  Its `availability()` probe is real, tested and load-bearing; its `simulate()` refuses.
  See that class's docstring for why, and for the PyPI finding behind it.

`resolve()` picks the best available engine and is the function callers should use. It
never returns an engine that cannot run, so nothing downstream has to check.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.dynamics.errors import EngineUnavailable
from app.dynamics.types import Mechanism, MechanismResult, MotionRange


@dataclass(frozen=True)
class EngineAvailability:
    """Whether an engine can run here, and if not, exactly why not.

    `reason` is required when unavailable, for the reason
    `app.kernel.provenance.unavailable` requires one: an engine that is simply absent,
    with no explanation, is indistinguishable from one nobody asked for, and the caller
    cannot tell whether to install something or to ask a different question.
    """

    engine: str
    available: bool
    version: str = ""
    reason: str = ""

    #: What this engine can answer that the others cannot -- shown when a caller has to
    #: choose, and when one refuses.
    covers: str = ""

    def __post_init__(self) -> None:
        if not self.available and not self.reason.strip():
            raise ValueError(
                "An unavailable engine must say why. 'Not available' with no reason "
                "leaves the caller with nothing to do about it."
            )


class DynamicsEngine(ABC):
    """Bodies, joints, drivers and a range in; motion and joint reactions out."""

    #: Short stable identifier, recorded on every result it produces.
    name: str = "engine"

    @abstractmethod
    def availability(self) -> EngineAvailability:
        """Whether this engine can run on this machine, with a reason when it cannot.

        Must not raise, and must not be expensive: callers probe every engine to choose
        one. A probe that imports a 400 MB library is a probe nobody runs.
        """

    @abstractmethod
    def simulate(self, mechanism: Mechanism, motion: MotionRange) -> MechanismResult:
        """Run the mechanism through the range.

        Raises `EngineUnavailable` when `availability()` is false, and `MechanismError`
        when the mechanism is fine but poses something this engine cannot do. The two
        are separate because the recoveries are: install something, versus ask something
        else.
        """

    def require(self) -> None:
        """Raise `EngineUnavailable` unless this engine can run. Call it first."""
        status = self.availability()
        if not status.available:
            raise EngineUnavailable(
                f"The {status.engine} engine is not available: {status.reason}"
            )


class KinematicEngine(DynamicsEngine):
    """Exact kinematics and inverse dynamics for a prescribed-motion tree chain.

    Always available: it is arithmetic, with no dependency beyond the standard library.
    That is the point of it -- Phase 9 has a working, verifiable deliverable on a machine
    where no multibody library could be installed, in the same spirit as Decision 1's
    "geometry must be buildable headless, free and in CI".

    Its limits are precise, and every one of them is refused by name rather than
    approximated: no closed loops (`closures.py` covers the two canonical planar ones),
    no spherical joints, no contact, no springs, no friction, no end stops. Every one of
    those makes the motion an *output*, and this engine only integrates motions that are
    inputs -- which is to say, it does not integrate at all.
    """

    name = "kinematic"

    def availability(self) -> EngineAvailability:
        return EngineAvailability(
            engine=self.name,
            available=True,
            version="in-tree",
            covers=(
                "prescribed-motion tree chains: exact poses, velocities, accelerations "
                "and joint reactions. No contact, friction, springs, end stops or "
                "closed loops."
            ),
        )

    def simulate(self, mechanism: Mechanism, motion: MotionRange) -> MechanismResult:
        from app.dynamics import kinematics, reactions

        path = kinematics.evaluate(mechanism, motion)
        found = reactions.compute(mechanism, path)
        return MechanismResult(
            mechanism=mechanism.name,
            engine=self.name,
            path=path,
            reactions=found,
            warnings=path.warnings,
        )


#: Symbols every real PyChrono build has exposed since Chrono 4.0. Used structurally --
#: no version string is parsed and no module name is trusted -- for the same reason
#: `app/ai/vision.py` gates on Ollama's reported capabilities rather than on a list of
#: model names: a name list rots, and a structural signal does not.
_CHRONO_REQUIRED_SYMBOLS = ("ChSystemNSC", "ChBody")

#: Either spelling of the vector type. Chrono renamed ChVectorD to ChVector3d at 8.0.
_CHRONO_VECTOR_SYMBOLS = ("ChVector3d", "ChVectorD")


class ChronoEngine(DynamicsEngine):
    """Project Chrono, behind the seam -- **a seam, and not yet a capability**.

    The technology register names Project Chrono (BSD-3, UW-Madison) as this phase's
    engine, and it is still the right choice: multibody plus FEA plus FSI, a Python API,
    and template vehicle models. The licence permits importing it in-process, unlike
    MBDyn, which is GPL and under Decision 4 could only ever be run as a separate
    process across a file boundary.

    **The finding, measured 2026-09-06 on this machine (Python 3.14.3):**

    1. `pip install pychrono` **succeeds and installs something else entirely.** The
       PyPI name `pychrono` belongs to an unrelated MIT-licensed timing and scheduling
       utility -- delays, retries, throttling -- with no simulation code in it. It is a
       pure-Python `py3-none-any` wheel of about 11 kB. `import pychrono` then works,
       and a naive availability probe would report the engine as present.
    2. The name `projectchrono` on PyPI is the project's own **reservation placeholder**,
       version 0.0.0, whose description says in as many words: *"Version 0.0.0 contains
       no simulation code. It is a placeholder published while binary wheels are being
       prepared,"* and directs the reader to conda.
    3. So PyChrono is **conda-only today**. This is the same finding Decision 1 already
       recorded for `pythonocc-core`, and it gets the same answer: conda is not forced
       into this deployment. `cadquery-ocp` was chosen precisely to avoid that, and
       adding a conda channel for the dynamics engine would give the whole product the
       install story the geometry kernel was designed to escape.

    Consequence, and it is deliberate: **nothing here is added to `requirements.txt`.**
    `pychrono` must never appear there -- it would install the wrong package, silently.
    When the project's own wheels land on PyPI under `projectchrono`, this class gets its
    `simulate` written *against a library that can be run and tested*, and this docstring
    gets shorter.

    Until then `availability()` is real work and `simulate()` refuses. Writing an
    untested translation of `Mechanism` into Chrono's API would be exactly the failure
    CLAUDE.md's "don't claim a capability the code does not have" rule names, and this
    repository has made it twice already (tet10, the SQLite refusal).
    """

    name = "chrono"

    #: Overridable so the probe can be tested without installing anything. The tests
    #: hand it a stand-in module -- including one shaped like the PyPI impostor, which
    #: is the guard being verified by breaking the thing it guards.
    def _import_module(self) -> object | None:
        try:
            import pychrono  # type: ignore[import-not-found]
        except ImportError:
            return None
        return pychrono

    def availability(self) -> EngineAvailability:
        covers = (
            "closed loops, contact, friction, springs, end stops and flexible bodies -- "
            "everything the kinematic engine refuses"
        )
        module = self._import_module()
        if module is None:
            return EngineAvailability(
                engine=self.name,
                available=False,
                covers=covers,
                reason=(
                    "PyChrono is not installed. It is not pip-installable: the PyPI "
                    "name 'pychrono' belongs to an unrelated timing utility, and "
                    "'projectchrono' is a 0.0.0 placeholder with no simulation code. "
                    "The project ships through conda only, which this deployment does "
                    "not use (Decision 1). Use the kinematic engine for prescribed "
                    "motion; a closed loop or a contact needs an engine that is not "
                    "here yet."
                ),
            )

        missing = [s for s in _CHRONO_REQUIRED_SYMBOLS if not hasattr(module, s)]
        has_vector = any(hasattr(module, s) for s in _CHRONO_VECTOR_SYMBOLS)
        if missing or not has_vector:
            return EngineAvailability(
                engine=self.name,
                available=False,
                covers=covers,
                reason=(
                    "a module named 'pychrono' imported, but it is not Project Chrono: "
                    f"it has no {', '.join(missing) or 'ChVector3d/ChVectorD'}. The PyPI "
                    "package called 'pychrono' is an unrelated timing and scheduling "
                    "utility, and installing it does not install a multibody solver. "
                    "Uninstall it (pip uninstall pychrono) so it cannot be mistaken for "
                    "one; PyChrono itself comes from conda."
                ),
            )

        version = str(getattr(module, "__version__", "") or "unknown")
        return EngineAvailability(
            engine=self.name,
            available=True,
            version=version,
            covers=covers,
        )

    def simulate(self, mechanism: Mechanism, motion: MotionRange) -> MechanismResult:
        """Refuses, always, and says which half of the refusal applies.

        Two different refusals on purpose. When Chrono is absent the caller needs to know
        it cannot be installed with pip; when Chrono is *present* the caller needs to
        know that the translation from `Mechanism` to a Chrono system has not been
        written, because it could not be written against a library nobody here could run.
        Reporting the second as the first would send someone to install what they already
        have.
        """
        self.require()
        raise EngineUnavailable(
            "PyChrono is installed, but Kryova's translation of a Mechanism into a "
            "Chrono system has not been written: it could not be developed or tested "
            "against a library that was not installable on this deployment (see "
            "ChronoEngine's docstring). Nothing here has ever run a Chrono solve, and "
            "code that pretended otherwise would be worse than this refusal. Use the "
            "kinematic engine for prescribed motion in the meantime."
        )


def engines() -> tuple[DynamicsEngine, ...]:
    """Every engine this build knows about, best-covered first.

    Order is by capability, not by preference: Chrono covers strictly more than the
    kinematic engine, so it leads and `resolve()` falls through to what actually runs.
    """
    return (ChronoEngine(), KinematicEngine())


def resolve(name: str | None = None) -> DynamicsEngine:
    """The engine to use, or a refusal listing what each one said.

    With no name, the first engine that reports itself available. With a name, that
    engine or a refusal quoting its own reason -- never a silent substitution, because a
    result labelled `chrono` that came from the kinematic engine would be exactly the
    provenance failure Decision 3 forbids.
    """
    available = list(engines())
    if name is not None:
        for engine in available:
            if engine.name == name:
                engine.require()
                return engine
        known = ", ".join(e.name for e in available)
        raise EngineUnavailable(
            f"There is no dynamics engine called {name!r}. Known engines: {known}."
        )

    reasons: list[str] = []
    for engine in available:
        status = engine.availability()
        if status.available:
            return engine
        reasons.append(f"{status.engine}: {status.reason}")
    raise EngineUnavailable(
        "No dynamics engine is available. " + " ".join(reasons)
    )


__all__ = [
    "ChronoEngine",
    "DynamicsEngine",
    "EngineAvailability",
    "KinematicEngine",
    "engines",
    "resolve",
]
