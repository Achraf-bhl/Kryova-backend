"""The docs site and the public status page (P10.2, P10.4).

Two things are tested harder than the rest, because they are the two ways a
docs site and a status page go wrong:

**Documentation stops being true silently.** A guide is correct when written and
slightly less so after every change, and nothing notices. So every step that
names a route is checked against the running application's own router, and the
mission gallery is derived from `LADDER` rather than typed up beside it.

**A public page publishes something it should not.** `GET /admin/health` carries
queue depth, storage bytes, live sessions and user totals. On a public URL those
are the size of the business and the hours nobody is watching. The status page
must carry none of them, and that is asserted by name rather than by reading the
current output and agreeing with it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import lifecycle
from app.core.status import HISTORY_DAYS, ServiceState, incident_history, read_status
from app.design.missions import LADDER
from app.handbook.gallery import entry_for, gallery, headline
from app.handbook.guides import GUIDES, Guide, Step
from app.handbook.reference import reference
from app.main import app
from app.models import Announcement, AnnouncementLevel, MaintenanceWindow
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _declared_paths() -> set[tuple[str, str]]:
    """Every `(METHOD, router-relative path)` this build actually serves.

    The walk itself moved to `tests/routes.py` when a third test needed it. Its
    docstring records why `app.routes` cannot be iterated directly, and it
    asserts it found something, so this cannot pass by finding nothing.
    """
    from tests.routes import leaf_routes

    return {
        (method.upper(), route.path) for route in leaf_routes() for method in route.methods
    }


class TestTheGuidesDescribeThisBuild:
    def test_every_step_that_names_a_route_names_one_that_exists(self) -> None:
        """The only thing standing between the guides and the usual fate of docs.

        A guide telling somebody to POST to an endpoint this build does not
        serve is worse than no guide: they will believe it, it will fail, and
        they will conclude the product is broken.
        """
        declared = _declared_paths()
        missing = [
            (guide.slug, step.method, step.path)
            for guide in GUIDES
            for step in guide.steps
            if step.path is not None and (step.method, step.path) not in declared
        ]
        assert not missing, f"guides name routes this build does not serve: {missing}"

    def test_a_step_cannot_name_half_a_route(self) -> None:
        # Half a pair cannot be verified, so it would publish an unchecked
        # claim while looking exactly like a checked one.
        with pytest.raises(ValueError, match="not both"):
            Step("Do the thing", method="POST", path=None)
        with pytest.raises(ValueError, match="not both"):
            Step("Do the thing", method=None, path="/projects")

    def test_every_guide_has_an_outcome_in_the_second_person(self) -> None:
        # Task-oriented means each guide is a thing somebody wants to have
        # done, not a tour of a feature. "You have ..." is that, mechanically.
        for guide in GUIDES:
            assert guide.outcome.startswith("You "), guide.slug

    def test_slugs_are_unique(self) -> None:
        slugs = [guide.slug for guide in GUIDES]
        assert len(slugs) == len(set(slugs))

    def test_the_guides_that_promise_least_say_so(self) -> None:
        """`not_covered` is the same discipline `Mission.unproven` applies.

        Specifically checked on the stop guide, because that is the one whose
        omission would cost somebody money: stopping a run is not a refund, and
        a guide that let a reader assume it was would be teaching a false thing
        about their bill.
        """
        stop = next(guide for guide in GUIDES if guide.slug == "stop-a-run")
        assert stop.not_covered
        assert any("billed" in line for line in stop.not_covered)


class TestTheGalleryIsDerived:
    def test_every_rung_appears(self) -> None:
        # Typed up beside the ladder, this list goes stale the day a rung
        # changes -- publishing a claim the code no longer makes, on the page
        # whose whole purpose is showing what the code does.
        assert [entry.rung for entry in gallery()] == [mission.rung for mission in LADDER]

    def test_what_a_rung_does_not_claim_travels_with_it(self) -> None:
        """The field that stops a gallery being a brochure.

        M2's frame is geometry and its welds are not sized, so "M2 passed" must
        never be readable as "the welds are sized". A gallery printing the
        passes and dropping the caveats would be the most misleading page in the
        product, because it would be the most convincing one.
        """
        for mission in LADDER:
            entry = entry_for(mission)
            assert entry.not_claimed == tuple(mission.unproven), mission.rung

        m2 = next(entry for entry in gallery() if entry.rung == "M2")
        assert m2.not_claimed, "M2 builds and carries caveats; the gallery dropped them"

    def test_a_rung_that_cannot_be_built_says_what_it_waits_on(self) -> None:
        pending = [entry for entry in gallery() if not entry.buildable]
        assert pending, "the ladder has unreachable rungs; the gallery hid them"
        for entry in pending:
            assert entry.waiting_on, entry.rung
            assert entry.assertions == 0 or entry.waiting_on

    def test_the_headline_carries_the_denominator(self) -> None:
        # "Four missions build" invites the reader to supply their own idea of
        # how many there are. The same discipline as the validation register's
        # headline.
        line = headline()
        assert f"of the {len(LADDER)}" in line

    def test_builds_distinguishes_a_part_from_a_product(self) -> None:
        kinds = {entry.builds for entry in gallery()}
        assert "part" in kinds
        assert "pending" in kinds
        # Two different demonstrations, and a reader deciding whether this
        # product suits them cares which.
        assert kinds <= {"part", "assembly", "sheet", "pending"}


class TestTheReferenceComesFromTheSchema:
    def test_it_groups_this_build_s_own_operations(self) -> None:
        result = reference(app.openapi())
        assert result["operation_count"] > 0
        tags = {group["tag"] for group in result["groups"]}
        assert "projects" in tags

    def test_it_marks_the_public_routes_as_public(self) -> None:
        result = reference(app.openapi())
        by_path = {
            (op["method"], op["path"]): op
            for group in result["groups"]
            for op in group["operations"]
        }
        assert by_path[("GET", "/api/v1/trust/commitments")]["public"] is True
        # Marked rather than filtered out: somebody deciding whether this
        # product fits needs the shape of the whole API.
        assert by_path[("POST", "/api/v1/projects")]["public"] is False

    def test_it_invents_no_prose(self) -> None:
        """Summaries come from route docstrings, which is where they already are.

        Two descriptions of one endpoint is one too many, and the second is the
        one that goes stale.
        """
        schema = {
            "info": {"title": "T", "version": "1"},
            "paths": {"/api/v1/x": {"get": {"summary": "The only summary.", "tags": ["x"]}}},
        }
        result = reference(schema)
        assert result["groups"][0]["operations"][0]["summary"] == "The only summary."


class TestTheStatusPageIsPublicAndSaysLittle:
    def test_it_answers_with_no_account(self, client: AuthenticatedTestClient) -> None:
        # A status page only its operator can read is a private dashboard.
        response = client.get(f"{API}/status")
        assert response.status_code == 200
        assert response.json()["state"] == "operational"

    def test_it_carries_none_of_the_fleet_s_numbers(
        self, client: AuthenticatedTestClient
    ) -> None:
        """Asserted by name, not by reading the output and agreeing with it.

        Each of these is on `GET /admin/health` and each is either the size of
        the business or the hours nobody is watching. None answers "is it
        working".
        """
        body = client.get(f"{API}/status").text
        for forbidden in (
            "queue_depth",
            "storage_bytes",
            "live_sessions",
            "users_total",
            "users_suspended",
            "success_rate",
            "jobs_in_window",
        ):
            assert forbidden not in body, f"the public status page leaked {forbidden}"

    def test_it_is_cacheable_but_not_for_long(self, client: AuthenticatedTestClient) -> None:
        # This is the page people refresh *during* an incident. Five-minute
        # staleness there is the page telling somebody the outage is still on
        # after it was fixed.
        response = client.get(f"{API}/status")
        assert response.headers["cache-control"] == "public, max-age=30"

    def test_a_maintenance_window_is_reported_with_its_message(
        self, client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(
            MaintenanceWindow(
                reason="migrating the primary database",
                message="Kryova is read-only while we move the database.",
                started_at=lifecycle.utcnow(),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()

        body = client.get(f"{API}/status").json()

        assert body["state"] == "maintenance"
        assert "read-only" in body["notice"]
        # The operator's own note is never a customer's to read: it is not
        # actionable and it names infrastructure to whoever is asking.
        assert "migrating the primary database" not in client.get(f"{API}/status").text

    def test_a_critical_announcement_alone_degrades_the_state(
        self, db_session: Session, current_user_id: str
    ) -> None:
        """How an operator says "something is wrong" without going read-only.

        Without this the page would read "operating normally" above a banner
        saying solves are failing — the exact contradiction a status page exists
        to prevent.
        """
        db_session.add(
            Announcement(
                message="Solves are failing on the EU worker pool.",
                level=AnnouncementLevel.CRITICAL,
                starts_at=lifecycle.utcnow() - timedelta(minutes=1),
                published_by_id=current_user_id,
            )
        )
        db_session.flush()

        assert read_status(db_session).state is ServiceState.DEGRADED

    def test_an_ordinary_announcement_does_not_degrade_anything(
        self, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(
            Announcement(
                message="New viewer rolling out this week.",
                level=AnnouncementLevel.INFO,
                starts_at=lifecycle.utcnow() - timedelta(minutes=1),
                published_by_id=current_user_id,
            )
        )
        db_session.flush()

        assert read_status(db_session).state is ServiceState.OPERATIONAL

    def test_degraded_is_never_inferred_from_a_failure_rate(self) -> None:
        """There is deliberately no threshold in this module.

        A rule nobody agreed to would put this product on a public outage page
        for a quiet hour with two bad runs. A window is declared by a person,
        who also has to write the sentence explaining it.
        """
        import ast
        import inspect

        import app.core.status as status_module

        # Over the *names in the code*, not over the source text. A string scan
        # trips on this module's own docstring, which has to be allowed to
        # explain the rule it is enforcing.
        tree = ast.parse(inspect.getsource(status_module))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for smell in ("success_rate", "failure_rate", "SimulationJob", "UsageRecord"):
            assert smell not in names, (
                f"{smell!r} is referenced in core/status.py -- the status page has "
                "started inferring health from run outcomes. See the module docstring."
            )


class TestIncidentHistory:
    def test_it_reports_a_finished_window_with_its_duration(
        self, db_session: Session, current_user_id: str
    ) -> None:
        started = lifecycle.utcnow() - timedelta(hours=3)
        db_session.add(
            MaintenanceWindow(
                reason="r",
                message="Planned upgrade.",
                started_at=started,
                ended_at=started + timedelta(minutes=42),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()

        history = incident_history(db_session)

        assert len(history) == 1
        assert history[0].minutes == 42
        assert history[0].to_dict()["ongoing"] is False

    def test_an_open_incident_says_so_rather_than_showing_a_null(
        self, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(
            MaintenanceWindow(
                reason="r",
                message="Investigating.",
                started_at=lifecycle.utcnow() - timedelta(minutes=10),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()

        entry = incident_history(db_session)[0].to_dict()

        assert entry["ongoing"] is True
        assert entry["minutes"] is None

    def test_it_does_not_reach_back_forever(
        self, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(
            MaintenanceWindow(
                reason="r",
                message="Ancient.",
                started_at=lifecycle.utcnow() - timedelta(days=HISTORY_DAYS + 5),
                ended_at=lifecycle.utcnow() - timedelta(days=HISTORY_DAYS + 4),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()

        assert incident_history(db_session) == ()


class TestTheDocsRoutesTakeNoPrincipal:
    def test_no_handbook_route_can_reach_the_current_user(self) -> None:
        """The `trust` module's guard, applied to the second public surface.

        Walks each route's dependency graph rather than reading the source, so
        a dependency added three layers down still fails this.
        """
        from app.api.deps import get_current_user
        from tests.routes import leaf_routes

        def reachable(dependant) -> set[object]:  # type: ignore[no-untyped-def]
            found = {dependant.call}
            for sub in dependant.dependencies:
                found |= reachable(sub)
            return found

        checked = 0
        for route in leaf_routes():
            path = route.path
            if not path.startswith("/handbook") and path != "/status":
                continue
            checked += 1
            assert get_current_user not in reachable(route.dependant), path
        assert checked >= 5, "found no public docs routes to check"

    def test_the_handbook_touches_no_database_at_all(self) -> None:
        """Stricter than `status`, and deliberately so.

        `status` reads operator-declared rows and argues for it. The handbook
        serves module constants and a schema this process generated, so there is
        nothing for it to legitimately read — and a `DbSession` appearing here
        would be a public route over a tenant-scoped session.
        """
        from app.core.database import get_db
        from tests.routes import leaf_routes

        def reachable(dependant) -> set[object]:  # type: ignore[no-untyped-def]
            found = {dependant.call}
            for sub in dependant.dependencies:
                found |= reachable(sub)
            return found

        checked = 0
        for route in leaf_routes():
            if not route.path.startswith("/handbook"):
                continue
            checked += 1
            assert get_db not in reachable(route.dependant), route.path
        assert checked >= 4, "found no handbook routes to check"


class TestTheGuideRoutes:
    def test_the_index_lists_every_guide(self, client: AuthenticatedTestClient) -> None:
        body = client.get(f"{API}/handbook").json()
        assert len(body["guides"]) == len(GUIDES)
        assert body["mission_count"] == len(LADDER)

    def test_an_unknown_guide_is_a_404_and_not_an_empty_page(
        self, client: AuthenticatedTestClient
    ) -> None:
        assert client.get(f"{API}/handbook/guides/no-such-guide").status_code == 404

    def test_a_guide_round_trips(self, client: AuthenticatedTestClient) -> None:
        body = client.get(f"{API}/handbook/guides/first-stress-number").json()
        assert body["title"] == "Get a stress number out of a CAD file"
        assert any(step["path"] == "/projects" for step in body["steps"])
        assert body["not_covered"]

    def test_the_gallery_route_publishes_the_caveats(
        self, client: AuthenticatedTestClient
    ) -> None:
        missions = client.get(f"{API}/handbook/gallery").json()["missions"]
        m2 = next(m for m in missions if m["rung"] == "M2")
        assert m2["not_claimed"]


def test_a_guide_with_no_steps_is_still_representable() -> None:
    # Not a rule, a check that the dataclass does not require steps -- a guide
    # that is purely "here is what this does not do" is legitimate.
    guide = Guide(slug="x", title="X", outcome="You have read this.", steps=())
    assert guide.to_dict()["steps"] == []
