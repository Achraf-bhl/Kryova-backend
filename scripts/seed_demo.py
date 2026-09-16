"""Seed a demonstration organisation with people, roles and projects (P9 task 4).

The task asks for "staging with seeded demo orgs". Staging itself needs an
environment this deployment has not got; the seed does not, and it is useful
on its own — a fresh clone, a GUI ladder run, a walkthrough for somebody who
has never seen the product. This is that half.

**It seeds people and structure, never results.** No simulation rows, no
`StaticResult`, no benchmark outcome, no verification standing. That is not
tidiness: Decision 3 says an unmeasured claim is never a pass, and a demo
database carrying a stress figure nobody solved for would put a fabricated
number behind the product's own provenance machinery, where every downstream
surface is built to trust it. A demo that ends at "here is a project, now run
something" is honest; one that opens on a green result is a lie with a
screenshot. `app/verify/` exists to keep those apart.

**Both role axes are populated, because one of them is the point.** P2.2 made
`OrgRole` (what you may do to the organisation) and `DomainRole` (what you may
do to the engineering work) separate on purpose — an owner is not
automatically a reviewer — and a demo where everybody is an owner shows none
of it. `DomainRole` is nullable and `None` means *not stated*, so one member
deliberately has none: that is the state P5.5's approval gates refuse, and it
is worth being able to see.

**Idempotent**, like `create_admin.py`, because a script nobody dares re-run
is a script nobody runs. Re-running reconciles: people who exist keep their
ids, roles are brought to what this file says, and nothing is duplicated.

    venv/bin/python -m scripts.seed_demo
    venv/bin/python -m scripts.seed_demo --slug acme --clear

Refuses a non-local `DATABASE_URL` without `--i-know`, for `create_admin.py`'s
reason: the blast radius is rows in somebody's tenant.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.organisation import DomainRole, Membership, OrgRole, Organisation
from app.models.project import Project
from app.models.user import User

#: Hosts the seed may write to without `--i-know`. Matched against the URL's
#: host, not the whole string, so a password containing "localhost" cannot talk
#: its way past the check (`create_admin.py` has the same guard and the same
#: reason).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})

#: The demo password. Short on purpose — the API's own policy needs eight
#: characters and this is created through the session, exactly as
#: `create_admin.py` explains. It is written here rather than generated because
#: a demo account nobody can sign into is not a demo; `_harden_production`
#: refuses the settings these accounts assume, and the guard above refuses the
#: database.
DEMO_PASSWORD = "demo"

DEFAULT_SLUG = "demo-engineering"


@dataclass(frozen=True)
class Person:
    """One seeded member, with both role axes stated or deliberately absent."""

    email: str
    full_name: str
    role: OrgRole
    #: `None` means *not stated*, which is a real state: P5.5 refuses a
    #: sign-off from a member whose domain role nobody has set, and a demo
    #: where that cannot happen hides the rule.
    domain_role: DomainRole | None
    why: str


PEOPLE: tuple[Person, ...] = (
    Person(
        "owner@demo.kryova.test",
        "Dana Okoye",
        OrgRole.OWNER,
        DomainRole.ENGINEER,
        "Owns the organisation and does engineering work. The two are separate "
        "columns and this person happens to hold both.",
    ),
    Person(
        "orgadmin@demo.kryova.test",
        "Marek Dvorak",
        OrgRole.ADMIN,
        None,
        "Runs the organisation — invites, billing, member list — and holds no "
        "domain role at all. `OrgRole` is an ordered ladder and every check is "
        "\"at least this role\", so a demo that skips a rung shows less of it.",
    ),
    Person(
        "reviewer@demo.kryova.test",
        "Iris Lindqvist",
        OrgRole.MEMBER,
        DomainRole.REVIEWER,
        "A reviewer who is *not* an organisation admin — the case P2.2 split "
        "the axes for, and the only person here who can decide a gate.",
    ),
    Person(
        "engineer@demo.kryova.test",
        "Tom Abadie",
        OrgRole.MEMBER,
        DomainRole.ENGINEER,
        "The ordinary case: does the work, cannot sign it off.",
    ),
    Person(
        "newstarter@demo.kryova.test",
        "Priya Raman",
        OrgRole.MEMBER,
        None,
        "Domain role not stated. Nothing defaults it to ENGINEER, so this "
        "member cannot review — visible proof that the null means what the "
        "column's docstring says.",
    ),
    Person(
        "auditor@demo.kryova.test",
        "Henrik Sole",
        OrgRole.VIEWER,
        None,
        "Read-only. Exists so a walkthrough can show that a viewer sees the "
        "work and cannot change it.",
    ),
)


@dataclass(frozen=True)
class DemoProject:
    name: str
    description: str
    owner_email: str


PROJECTS: tuple[DemoProject, ...] = (
    DemoProject(
        "Stamping press — C-frame",
        "The frame of a 40 t press. Nothing has been solved: open it and run "
        "a linear static to see a real result appear.",
        "engineer@demo.kryova.test",
    ),
    DemoProject(
        "Conveyor drive bracket",
        "A bracket carrying a gearmotor. Deliberately empty of results — a "
        "seeded number would be a stress nobody computed.",
        "engineer@demo.kryova.test",
    ),
    DemoProject(
        "Robot arm — link 2",
        "Owned by the organisation owner rather than the engineer, so a "
        "walkthrough can show that access comes from membership and not from "
        "`Project.owner_id`.",
        "owner@demo.kryova.test",
    ),
)


@dataclass(frozen=True)
class SeedResult:
    organisation_id: str
    slug: str
    created_users: tuple[str, ...]
    updated_users: tuple[str, ...]
    created_projects: tuple[str, ...]
    organisation_created: bool

    def report(self) -> str:
        lines = [
            f"organisation: {self.slug} ({self.organisation_id})"
            f" — {'created' if self.organisation_created else 'already there'}",
            f"people: {len(self.created_users)} created, "
            f"{len(self.updated_users)} reconciled",
            f"projects: {len(self.created_projects)} created",
            f"sign in with any of the addresses below / {DEMO_PASSWORD}",
        ]
        lines.extend(f"    {person.email:<34} {person.role.value:<7} "
                     f"{person.domain_role.value if person.domain_role else '(not stated)'}"
                     for person in PEOPLE)
        lines.append(
            "no simulation, result or verification row was written: a seeded "
            "number is a stress nobody computed, and every surface downstream "
            "of one is built to trust it."
        )
        return "\n".join(lines)


def _host_of(url: str) -> str:
    from sqlalchemy.engine import make_url

    return (make_url(url).host or "").lower()


def seed(slug: str = DEFAULT_SLUG, *, clear: bool = False) -> SeedResult:
    """Create or reconcile the demo organisation. Safe to re-run."""
    created_users: list[str] = []
    updated_users: list[str] = []
    created_projects: list[str] = []

    with SessionLocal() as session:
        organisation = session.scalar(
            select(Organisation).where(Organisation.slug == slug)
        )
        organisation_created = organisation is None
        if organisation is None:
            organisation = Organisation(
                name="Demo Engineering Ltd",
                slug=slug,
                # Not personal: a personal organisation is created *for* a user
                # and the UI hides its member list, which is the one thing this
                # seed exists to show.
                is_personal=False,
            )
            session.add(organisation)
        # The id carries a Python-side default applied *during* the flush, so
        # reading it before this line gives None — and every Membership below
        # is a foreign key to it. CLAUDE.md records this biting three times.
        session.flush()

        if clear:
            for project in session.scalars(
                select(Project).where(Project.organisation_id == organisation.id)
            ):
                session.delete(project)
            session.flush()

        by_email: dict[str, User] = {}
        for person in PEOPLE:
            user = session.scalar(select(User).where(User.email == person.email))
            if user is None:
                user = User(
                    email=person.email,
                    hashed_password=hash_password(DEMO_PASSWORD),
                    full_name=person.full_name,
                )
                session.add(user)
                created_users.append(person.email)
            else:
                user.hashed_password = hash_password(DEMO_PASSWORD)
                user.full_name = person.full_name
                user.is_active = True
                updated_users.append(person.email)
            session.flush()
            by_email[person.email] = user

            membership = session.scalar(
                select(Membership).where(
                    Membership.organisation_id == organisation.id,
                    Membership.user_id == user.id,
                )
            )
            if membership is None:
                session.add(
                    Membership(
                        organisation_id=organisation.id,
                        user_id=user.id,
                        role=person.role,
                        domain_role=person.domain_role,
                    )
                )
            else:
                # Reconcile rather than leave: a re-run whose roles drifted
                # would demonstrate whatever the last run happened to leave.
                membership.role = person.role
                membership.domain_role = person.domain_role

        existing = {
            project.name
            for project in session.scalars(
                select(Project).where(Project.organisation_id == organisation.id)
            )
        }
        for wanted in PROJECTS:
            if wanted.name in existing:
                continue
            session.add(
                Project(
                    name=wanted.name,
                    description=wanted.description,
                    owner_id=by_email[wanted.owner_email].id,
                    organisation_id=organisation.id,
                )
            )
            created_projects.append(wanted.name)

        session.commit()
        return SeedResult(
            organisation_id=organisation.id,
            slug=slug,
            created_users=tuple(created_users),
            updated_users=tuple(updated_users),
            created_projects=tuple(created_projects),
            organisation_created=organisation_created,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument(
        "--clear",
        action="store_true",
        help="delete the organisation's projects first, then re-create them",
    )
    parser.add_argument(
        "--i-know",
        action="store_true",
        help="seed even though DATABASE_URL is not a local host",
    )
    args = parser.parse_args(argv)

    host = _host_of(settings.database_url)
    if host not in LOCAL_HOSTS and not args.i_know:
        print(
            f"Refusing: DATABASE_URL points at {host!r}, not a local server.\n"
            "This writes users, memberships and projects into a tenant. If that\n"
            "is genuinely what you want on this database, pass --i-know.",
            file=sys.stderr,
        )
        return 2

    print(seed(args.slug, clear=args.clear).report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
