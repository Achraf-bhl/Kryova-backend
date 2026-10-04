"""PyChrono in its container, run for real (master plan E9.1, THE QUEUE G6 step 1).

`tests/test_dynamics_chrono.py` is 77 tests against a **stub** — two of them, one
spelled the Chrono 8 way and one the Chrono 9 way, so `_call`'s name resolution
is pinned on both. That is the right shape for the translation and it proves
nothing at all about the engine: a mock of an API is a copy of what its author
believed it to be, which is the lesson `stream_chat` cost a whole class of defect
to learn.

So this file runs the real image. Every test here is skipped where there is no
Docker and no `kryova-chrono:9.0.1`, **and a skip is not a pass** — CI has
neither, so CI learns nothing from this file and the claims below stand on the
machines that do.

**What these tests are for, and it is not the physics.** The closed-form oracles
were run on Linux on 2026-09-16 and are recorded in `_entrypoint.py`. What was
never run is the *Windows* half, which is where the three differences live, and
each is invisible from Linux by construction:

* the bind mount is a **Windows path**, `-v C:\\...:/work`;
* there is no `os.getuid`, so `run_mechanism` sends **no `--user`** and the
  container writes into the caller's directory as root — a file the server may
  then be unable to delete;
* the entry point must arrive with **LF** endings, because a Linux interpreter
  reads it. `run.py` writes it with an explicit `newline="\\n"` for that reason,
  and on Windows the platform default would rewrite every line.

The pendulum is here as the *smoke* that the whole path carries a number, not as
new physics: if the mount, the ownership or the line endings were wrong, this is
what would fail, and it would fail with a plausible-looking error about something
else.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

import pytest

from app.dynamics.chrono.run import DEFAULT_IMAGE, availability, engine_identity, run_mechanism

_MISSING = availability("docker", DEFAULT_IMAGE)
needs_chrono = pytest.mark.skipif(
    _MISSING is not None, reason=f"No Chrono image to run against: {_MISSING}"
)

#: 1 kg on a 0.5 m revolute, released from horizontal. At the bottom of the swing
#: the pivot carries **3mg** — mg to hold the weight and 2mg of centripetal force,
#: because energy gives v² = 2gL and the centripetal term is mv²/L = 2mg. It is
#: the oracle `_entrypoint.py`'s `SOLVER_TYPE` note was measured against: 29.42 N
#: with a direct solver, 4286 N with Chrono's default iterative one.
THREE_MG_N = 3.0 * 9.80665
PENDULUM_RADIUS_MM = 500.0


def pendulum(samples: int = 201, duration_s: float = 1.0) -> dict[str, Any]:
    """A free pendulum, as the wire spells it.

    Built as a payload rather than through `Mechanism`, deliberately: an
    **undriven** revolute is refused by name upstream (`app/dynamics` will not
    integrate a coordinate whose motion is an output of the forces on it), and
    that refusal is right. This test is below that boundary, asking what the
    engine does when something does hand it one.
    """
    return {
        "wire_version": 1,
        "name": "pendulum",
        "gravity_mm_s2": [0.0, 0.0, -9806.65],
        "bodies": [
            {
                "name": "bob",
                "mass_kg": 1.0,
                "centre_of_mass_mm": [PENDULUM_RADIUS_MM, 0.0, 0.0],
                "has_inertia_tensor": False,
            }
        ],
        "joints": [
            {
                "name": "pivot",
                "kind": "revolute",
                "body": "bob",
                "parent": None,
                "origin_mm": [0.0, 0.0, 0.0],
                "axis": [0.0, 1.0, 0.0],
            }
        ],
        "drivers": [],
        "motion": {"duration_s": duration_s, "samples": samples},
    }


@needs_chrono
class TestTheContainerRunsOnThisMachine:
    """G6 step 1: does it work at all on the platform the product ships on?"""

    def test_a_mechanism_runs_and_comes_back(self, tmp_path: Path) -> None:
        run, output = run_mechanism(tmp_path, pendulum(samples=11))

        assert run.returncode == 0
        assert run.launcher == "docker"
        assert output["wire_version"] == 1
        assert len(output["times_s"]) == 11

    def test_the_bind_mount_carries_a_windows_path(self, tmp_path: Path) -> None:
        """`tmp_path` is `C:\\...` here and `/tmp/...` on Linux.

        There is nothing to assert about the string — the claim is that a run
        started from this directory reads its own `input.json` back, which is
        only true if the mount resolved.
        """
        run_mechanism(tmp_path, pendulum(samples=3))

        assert (tmp_path / "input.json").exists()
        assert (tmp_path / "output.json").exists()

    def test_the_entry_point_is_written_with_lf(self, tmp_path: Path) -> None:
        """A CRLF shebang line is `#!/usr/bin/env python3\\r`, and nothing says so."""
        run_mechanism(tmp_path, pendulum(samples=3))

        written = (tmp_path / "chrono_run.py").read_bytes()
        assert b"\r\n" not in written

    def test_the_host_can_delete_what_the_container_wrote(self, tmp_path: Path) -> None:
        """The finding this test exists for, and it is a measurement not a hope.

        On Windows `os.getuid` does not exist, so `run_mechanism` sends no
        `--user` and the container runs as root. A server that could not remove
        its own scratch directory would leak one per mechanism, and would do it
        silently. Docker Desktop's mount translation gives the host full control
        of the file whatever uid wrote it — measured 2026-09-20, the same answer
        OpenFOAM's F1 gave.
        """
        work = tmp_path / "run"
        work.mkdir()
        run_mechanism(work, pendulum(samples=3))

        assert sorted(p.name for p in work.iterdir()) == [
            "chrono_run.py",
            "input.json",
            "output.json",
        ]
        shutil.rmtree(work)
        assert not work.exists()


@needs_chrono
class TestTheEngineIsNamedByItsImage:
    """What a result may say produced it."""

    def test_the_identity_is_the_image_content_id(self) -> None:
        identity = engine_identity("docker", DEFAULT_IMAGE)

        assert identity is not None
        assert identity.startswith(f"docker {DEFAULT_IMAGE} sha256:")

    def test_pychrono_carries_no_version_of_its_own(self, tmp_path: Path) -> None:
        """`chrono_version` reads `unknown`, and that is the honest answer.

        Measured 2026-09-20: the conda-forge `pychrono` package has **no**
        `__version__` attribute at all — `dir(pychrono)` offers only
        `ChMatrix_dense_version_tag`, which is a matrix format tag and not the
        build. So the `getattr(..., "unknown")` fallback is not defensive
        padding, it is the only reachable answer, and it is why the module
        docstring's "the image is the version pin" is load-bearing rather than
        stylistic: the **image content id** is the only thing that names what
        answered. `cache.engine_for` already keys on exactly that.
        """
        _, output = run_mechanism(tmp_path, pendulum(samples=3))

        assert output["chrono_version"] == "unknown"
        assert "SPARSE_QR" in output["method"]


@needs_chrono
class TestThePendulumStillAgreesWithItsClosedForm:
    """The oracle, re-run here. Recorded on Linux 2026-09-16; never on Windows."""

    def test_the_peak_pivot_reaction_is_three_mg(self, tmp_path: Path) -> None:
        _, output = run_mechanism(tmp_path, pendulum())

        forces = output["reactions"]["pivot"]["force_n"]
        peak = max(math.dist((0.0, 0.0, 0.0), f) for f in forces)

        # 0.1% rather than the 0.003% actually measured: the peak is a maximum
        # **over the sampled instants**, so the agreement depends on how near a
        # sample lands to the bottom of the swing. Pinning the measured figure
        # would make `samples` load-bearing for a claim that is not about it.
        assert peak == pytest.approx(THREE_MG_N, rel=1e-3)

    def test_the_revolute_constraint_actually_holds(self, tmp_path: Path) -> None:
        """The check that separates an answer from noise.

        With Chrono's default iterative solver this same model reports 4286 N
        and the radius drifts from 500 to 736 mm — the constraint is simply not
        satisfied, and *the force is still a number of plausible shape*. So the
        radius is the tell, and it is asserted separately from the force: a test
        that only checked the force would pass on a solver change that made the
        model meaningless.
        """
        _, output = run_mechanism(tmp_path, pendulum())

        radii = [
            math.dist((0.0, 0.0, 0.0), p)
            for p in output["bodies"]["bob"]["frame_origin_mm"]
        ]
        assert max(radii) == pytest.approx(PENDULUM_RADIUS_MM, abs=0.01)
        assert min(radii) == pytest.approx(PENDULUM_RADIUS_MM, abs=0.01)

    def test_the_first_sample_is_flagged_as_unmeasured(self, tmp_path: Path) -> None:
        """Chrono forms no constraint force until it has taken a step.

        The first sample is therefore a zero that means "not yet computed" and
        not "this joint carries nothing" — the distinction the whole of
        `app/verify/` is about, and the run says so in its own warnings rather
        than leaving a reader to discover it from a suspicious plot.
        """
        _, output = run_mechanism(tmp_path, pendulum(samples=11))

        first = output["reactions"]["pivot"]["force_n"][0]
        assert math.dist((0.0, 0.0, 0.0), first) == pytest.approx(0.0, abs=1e-9)
        assert any("first sample" in warning for warning in output["warnings"])

    def test_a_body_with_no_tensor_says_what_that_costs(self, tmp_path: Path) -> None:
        """A point mass gets a negligible isotropic inertia, and is told about it.

        A zero tensor makes the mass matrix singular — CLAUDE.md records the
        radius wandering 100 -> 46 -> 114 mm on exactly that — so the substitute
        is necessary. What must not happen is it being silent, because every
        moment that depends on rotational inertia is then missing from a result
        that looks complete.
        """
        _, output = run_mechanism(tmp_path, pendulum(samples=3))

        assert any("negligible isotropic inertia" in w for w in output["warnings"])
        assert any("bob" in w for w in output["warnings"])
