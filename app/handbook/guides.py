"""Task-oriented documentation (P10.2).

**Task-oriented, which means each guide is a thing somebody wants to have done**
— "get a stress number out of a CAD file", "connect a CATIA seat" — not a tour
of a feature. The distinction is the difference between documentation somebody
finishes and documentation somebody closes.

**Every step that names a route is checked against the running router.** A
`Step` may carry a `method` and `path`, and `tests/test_docs.py` asserts that
each one exists in `app.main.app`. That is the only thing standing between this
file and the ordinary fate of a docs site, which is to describe the product as
it was: a guide that tells somebody to `POST /api/v1/projects/{id}/simulate` is
worse than no guide, because they will believe it and conclude the product is
broken.

The paths below are written **without** the `/api/v1` prefix, the way the
routers declare them, so the check compares like with like and a prefix change
does not have to be re-typed into forty strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final


@dataclass(frozen=True)
class Step:
    """One thing to do, and the call it corresponds to if there is one."""

    text: str
    method: str | None = None
    #: Router-relative, no `/api/v1`. See the module docstring.
    path: str | None = None

    def __post_init__(self) -> None:
        if (self.method is None) != (self.path is None):
            raise ValueError(
                f"Step {self.text!r} names a method or a path but not both. The pair "
                "is what the route check compares, so half of it cannot be verified "
                "and would publish an unchecked claim."
            )

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "method": self.method, "path": self.path}


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    #: Who this is for and what they will have when they finish. Present tense,
    #: second person, one sentence — if it needs two, it is two guides.
    outcome: str
    steps: tuple[Step, ...]
    #: What this guide deliberately does not cover, so a reader does not finish
    #: it believing they have done something they have not. The same discipline
    #: `Mission.unproven` applies to the ladder.
    not_covered: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "outcome": self.outcome,
            "steps": [step.to_dict() for step in self.steps],
            "not_covered": list(self.not_covered),
        }


GUIDES: Final[tuple[Guide, ...]] = (
    Guide(
        slug="first-stress-number",
        title="Get a stress number out of a CAD file",
        outcome=(
            "You have uploaded a part, defined how it is held and pushed, and read a "
            "von Mises stress you can trace back to the mesh that produced it."
        ),
        steps=(
            Step("Create a project to hold the part and its runs.", "POST", "/projects"),
            Step(
                "Upload the CAD file. STEP and STL are both read; the file is stored "
                "as a geometry version, so a later run can name which one it used.",
                "POST",
                "/projects/{project_id}/geometry",
            ),
            Step(
                "Define the load case: which faces are fixed, what force or pressure "
                "acts where, and which material. Units are mm-N-MPa throughout and "
                "nothing is converted on the way in or out.",
            ),
            Step(
                "Start the run. It returns immediately with a job to poll — meshing "
                "and solving happen off the request.",
                "POST",
                "/projects/{project_id}/simulations",
            ),
            Step(
                "Poll the job until it succeeds, fails, or you stop it.",
                "GET",
                "/projects/{project_id}/simulations/{simulation_id}",
            ),
            Step(
                "Read the surface field to see stress on the part. This answers 409 "
                "until the job has succeeded, which is deliberate: there is no field "
                "to draw before there is a result.",
                "GET",
                "/projects/{project_id}/simulations/{simulation_id}/surface",
            ),
        ),
        not_covered=(
            "Whether the number is converged. One solve holds no evidence about its "
            "own discretisation error — ask for grids: 3 and read the convergence "
            "verdict, or treat the figure as indicative.",
            "Welds, bolts and joints. A solid stress run says nothing about them.",
        ),
    ),
    Guide(
        slug="stop-a-run",
        title="Stop a run you no longer want",
        outcome=(
            "You have stopped a simulation or an agent turn, and you know what that "
            "did and did not stop."
        ),
        steps=(
            Step(
                "Stop a simulation. If it was queued it is cancelled outright. If it "
                "is already running it stops at its next stage boundary — after "
                "meshing, or between the grids of a study.",
                "POST",
                "/projects/{project_id}/simulations/{simulation_id}/cancel",
            ),
            Step(
                "Stop an agent turn. It ends at its next step, after any tool call "
                "already in flight finishes, so nothing is left half-applied.",
                "POST",
                "/ai/conversations/{conversation_id}/cancel",
            ),
        ),
        not_covered=(
            "Getting the compute back. A solve already inside CalculiX runs to "
            "completion and the machine time it used is billed — stopping is not a "
            "refund, and this product will not imply that it is.",
        ),
    ),
    Guide(
        slug="connect-a-catia-seat",
        title="Connect a CATIA workstation",
        outcome=(
            "You have paired your CATIA seat with your account, and the agent can drive it."
        ),
        steps=(
            Step(
                "Register the device. You get a pairing token once, and only once.",
                "POST",
                "/catia/devices",
            ),
            Step(
                "Run the bridge daemon on the Windows machine with that token. It "
                "dials out to this API — there is no port to open and no inbound "
                "connection to the workstation.",
            ),
            Step(
                "Check that it arrived. This is the only truth about whether a seat "
                "is live; the browser never talks to the workstation.",
                "GET",
                "/catia/status",
            ),
        ),
        not_covered=(
            "Running without CATIA. The open kernel (GEOMETRY_BACKEND=occt) needs no "
            "seat at all and is the right choice for most deployments.",
        ),
    ),
    Guide(
        slug="ask-for-a-sign-off",
        title="Ask somebody to approve a change",
        outcome=(
            "You have a decision on the record with the evidence its decider was "
            "shown, instead of in a chat message that scrolled away."
        ),
        steps=(
            Step(
                "Raise the gate against the thing being changed. Any member may "
                "raise one; it asks a question and grants nothing.",
                "POST",
                "/organisations/{organisation_id}/gates",
            ),
            Step(
                "The reviewer opens the queue and reads the diff, including what the "
                "change reaches but nobody edited.",
                "GET",
                "/organisations/{organisation_id}/gates",
            ),
            Step(
                "They approve or reject. Deciding needs the reviewer domain role, a "
                "rejection needs a reason, and an approval is refused if the design "
                "moved while the gate was pending.",
                "POST",
                "/organisations/{organisation_id}/gates/{gate_id}/decision",
            ),
        ),
        not_covered=(
            "Enforcing the gate. Nothing yet blocks work on a pending gate — the "
            "record exists and the decision is real, but wiring it into the agent's "
            "control flow is E16's half of this.",
        ),
    ),
    Guide(
        slug="check-what-is-validated",
        title="Check what Kryova has actually validated",
        outcome=(
            "You know which analyses are validated against which benchmarks, and "
            "which are not — before you rely on one."
        ),
        steps=(
            Step(
                "Read the validation register. It needs no account, on purpose: a "
                "page claiming verification is the product, behind a login, can only "
                "be read by people who already bought.",
                "GET",
                "/trust/validation-register",
            ),
            Step(
                "Read what this product will not claim. Twelve commitments, each "
                "either mechanical with the files that enforce it, or policy with "
                "none — and a mechanical one naming no file is refused at "
                "construction.",
                "GET",
                "/trust/commitments",
            ),
            Step(
                "Read the accuracy changelog, which names changes that move numbers.",
                "GET",
                "/trust/changelog",
            ),
        ),
    ),
)


def guide_by_slug(slug: str) -> Guide | None:
    return next((guide for guide in GUIDES if guide.slug == slug), None)


__all__ = ["GUIDES", "Guide", "Step", "guide_by_slug"]
