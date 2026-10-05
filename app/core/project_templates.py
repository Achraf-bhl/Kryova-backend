"""Start a project from a rung of the mission ladder (ROAD_TO_10 7.7).

A template is a rung in `app.design.missions.LADDER` that **builds** -- a rung that waits on a
capability (`needs`) has nothing to start from, and offering it would be offering an empty
project under a machine's name. The catalogue is derived from the ladder on every call, so a
rung that advances appears here without anyone editing this file, and one that regresses to
pending vanishes.

What a project started from a rung holds:

* **A design, where the rung is one part.** A single-part rung and a folded sheet carry one
  `DesignSpec`; it is saved as revision 1 of a new design in a new conversation, authored by
  the user who asked for it. An assembly or a mechanism is a *product graph* of several specs
  and there is one design document per conversation, so those rungs start a project with **no**
  design -- `Started.design` says so, and the response says why, rather than seeding the first
  part and presenting it as the machine.
* **The claims and the caveats, in the description.** Requirements are not stored per project
  (`POST /kernel/conversations/{id}/requirements` parses a `.kreq` and reports; it persists
  nothing), so the rung's assertions are listed as text, and **`Mission.unproven` is printed
  in the description with the same prominence as the claims**: the same reason
  `app/handbook/gallery.py` refuses to publish a pass without its caveats.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core import designs
from app.design.missions import LADDER, Mission, mission
from app.design.spec import DesignSpec
from app.models import Conversation, Project, User


class UnknownTemplate(KeyError):
    pass


@dataclass(frozen=True)
class Template:
    key: str
    title: str
    era: str
    kind: str
    hard: str
    claims: tuple[str, ...]
    unproven: tuple[str, ...]
    #: True when starting from it creates a design; false for a product graph.
    seeds_a_design: bool


@dataclass
class Started:
    project: Project
    conversation: Conversation | None
    template: Template
    #: Why there is no design, when there is none. Empty when one was created.
    design_note: str = ""


def _kind(rung: Mission) -> str:
    if rung.spec is not None:
        return "part"
    if rung.folded is not None:
        return "folded sheet"
    if rung.moving is not None:
        return "mechanism"
    return "assembly"


def _spec_of(rung: Mission) -> DesignSpec | None:
    if rung.spec is not None:
        return rung.spec
    if rung.folded is not None:
        return rung.folded.spec
    return None


def _describe(rung: Mission) -> Template:
    return Template(
        key=rung.rung,
        title=rung.title,
        era=rung.era,
        kind=_kind(rung),
        hard=rung.hard,
        claims=tuple(
            f"{a.name}: {a.measure} {a.comparison} {a.bound}" + (f" -- {a.note}" if a.note else "")
            for a in rung.assertions
        ),
        unproven=tuple(rung.unproven),
        seeds_a_design=_spec_of(rung) is not None,
    )


def catalogue() -> list[Template]:
    """Every rung that builds, in ladder order."""
    return [_describe(rung) for rung in LADDER if rung.buildable]


def template(key: str) -> Template:
    try:
        rung = mission(key)
    except KeyError as missing:
        raise UnknownTemplate(str(missing)) from missing
    if not rung.buildable:
        raise UnknownTemplate(
            f"{key} is not a template: it waits on {', '.join(rung.needs)} and has no design "
            "to start from. Templates are the rungs that build."
        )
    return _describe(rung)


def description_for(found: Template) -> str:
    lines = [
        f"Started from mission {found.key}, {found.title} ({found.kind}). {found.hard}",
        "",
        "What this design claims:",
        *[f"  - {claim}" for claim in found.claims],
        "",
        "What it does NOT claim:",
        *([f"  - {item}" for item in found.unproven] or ["  - (the rung lists nothing)"]),
    ]
    return "\n".join(lines)


def start(db: Session, user: User, key: str, *, name: str | None = None) -> Started:
    """A new project from rung `key`, owned by `user`. Nothing is committed."""
    found = template(key)
    rung = mission(key)
    project = Project(
        name=(name or found.title)[:255],
        description=description_for(found),
        owner_id=user.id,
        template_key=found.key,
    )
    db.add(project)
    db.flush()

    spec = _spec_of(rung)
    if spec is None:
        return Started(
            project=project,
            conversation=None,
            template=found,
            design_note=(
                f"{found.key} is a {found.kind}: a product graph of several parts, and a "
                "conversation holds one design. The project starts with no design; ask the "
                "agent to build the parts, or open the mission in the gallery."
            ),
        )
    conversation = Conversation(
        owner_id=user.id, project_id=project.id, title=f"{found.title} (from {found.key})"[:255]
    )
    db.add(conversation)
    db.flush()
    designs.save(
        db,
        conversation,
        spec,
        summary=f"Started from mission {found.key}.",
        author=designs.AUTHOR_USER,
        author_id=user.id,
    )
    return Started(project=project, conversation=conversation, template=found)
