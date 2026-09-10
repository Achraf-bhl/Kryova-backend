"""Approval gates: the record, the rules, and who may decide (P5.5).

The gate exists so that "who signed this off and what did they see" has an
answer. Most of what is worth testing is therefore about the ways an approval
can be made to mean less than it looks like it means — an approval of something
that has since changed, a rejection with no reason, a sign-off from somebody
with no standing to give one — because each of those leaves a row that reads
exactly like a real approval.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import gates, lifecycle
from app.models import ApprovalGate, GateState, Membership, Organisation, User
from app.models.organisation import DomainRole, OrgRole
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


@pytest.fixture
def organisation(db_session: Session) -> Organisation:
    org = Organisation(name="Kryova Engineering", slug="kryova-eng", is_personal=False)
    db_session.add(org)
    db_session.flush()
    return org


@pytest.fixture
def engineer(db_session: Session, organisation: Organisation) -> User:
    return _member(db_session, organisation, "engineer@kryova.dev", DomainRole.ENGINEER)


@pytest.fixture
def reviewer(db_session: Session, organisation: Organisation) -> User:
    return _member(db_session, organisation, "reviewer@kryova.dev", DomainRole.REVIEWER)


def _member(
    db: Session, organisation: Organisation, email: str, domain_role: DomainRole | None
) -> User:
    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    db.add(
        Membership(
            organisation_id=organisation.id,
            user_id=user.id,
            role=OrgRole.MEMBER,
            domain_role=domain_role,
        )
    )
    db.flush()
    return user


SPEC = {"thickness_mm": 4.0, "material": "AISI 316L"}


def _gate(db: Session, organisation: Organisation, requester: User, subject=SPEC) -> ApprovalGate:
    return gates.raise_gate(
        db,
        organisation_id=organisation.id,
        requested_by=requester,
        title="Wall thickness",
        question="Drop the wall from 6 mm to 4 mm?",
        subject_type="design_spec",
        subject_id="spec-1",
        subject=subject,
    )


class TestTheSubjectIsPinned:
    def test_an_approval_is_against_the_thing_as_it_was_shown(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)
        decision = gates.decide(db_session, gate, approve=True, by=reviewer, subject=SPEC)

        assert decision.ok
        assert gate.state is GateState.APPROVED
        assert gate.decided_by_id == reviewer.id

    def test_a_subject_that_moved_cannot_be_approved(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        """The whole reason the digest is stored.

        Without this, approving "the design spec" approves whatever the spec has
        since become -- a signature on a blank page. The reviewer read 4 mm; by
        the time they clicked, it was 3 mm.
        """
        gate = _gate(db_session, organisation, engineer)
        moved = SPEC | {"thickness_mm": 3.0}

        decision = gates.decide(db_session, gate, approve=True, by=reviewer, subject=moved)

        assert not decision.ok
        assert decision.refusal is gates.GateRefusal.SUBJECT_MOVED
        assert gate.state is GateState.PENDING, "a refused decision must leave the gate open"

    def test_key_order_is_not_a_change(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        # Otherwise every gate refuses itself on a serialisation detail, and the
        # staleness check becomes noise people learn to click through.
        gate = _gate(db_session, organisation, engineer)
        reordered = {"material": "AISI 316L", "thickness_mm": 4.0}

        assert gates.decide(db_session, gate, approve=True, by=reviewer, subject=reordered).ok


class TestARejectionCarriesAReason:
    def test_a_bare_no_is_refused(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)

        decision = gates.decide(db_session, gate, approve=False, by=reviewer, subject=SPEC)

        assert decision.refusal is gates.GateRefusal.NO_REASON_GIVEN
        assert gate.state is GateState.PENDING

    def test_whitespace_is_not_a_reason(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)
        decision = gates.decide(
            db_session, gate, approve=False, by=reviewer, subject=SPEC, note="   \n "
        )
        assert decision.refusal is gates.GateRefusal.NO_REASON_GIVEN

    def test_a_reason_is_kept_verbatim(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)
        gates.decide(
            db_session,
            gate,
            approve=False,
            by=reviewer,
            subject=SPEC,
            note="4 mm is under the fastener head bearing area.",
        )
        assert gate.state is GateState.REJECTED
        assert gate.decision_note == "4 mm is under the fastener head bearing area."

    def test_an_approval_needs_no_reason(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        # Asymmetric on purpose: "yes" is complete on its own, "no" is not,
        # because what happens next depends entirely on why.
        gate = _gate(db_session, organisation, engineer)
        assert gates.decide(db_session, gate, approve=True, by=reviewer, subject=SPEC).ok


class TestSelfApproval:
    def test_the_requester_cannot_approve_their_own_gate_by_default(
        self, db_session: Session, organisation: Organisation, engineer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)

        decision = gates.decide(db_session, gate, approve=True, by=engineer, subject=SPEC)

        assert decision.refusal is gates.GateRefusal.SELF_APPROVAL

    def test_a_one_person_deployment_can_opt_out(
        self, db_session: Session, organisation: Organisation, engineer: User
    ) -> None:
        """Most self-hosted installs have exactly one engineer.

        A review process that cannot be completed is one people route around
        entirely -- losing the record as well as the second opinion. The gate
        still records who approved and against what.
        """
        gate = _gate(db_session, organisation, engineer)

        decision = gates.decide(
            db_session, gate, approve=True, by=engineer, subject=SPEC, allow_self_approval=True
        )

        assert decision.ok
        assert gate.decided_by_id == engineer.id

    def test_the_requester_may_always_reject_their_own_gate(
        self, db_session: Session, organisation: Organisation, engineer: User
    ) -> None:
        # Withdrawing your own request needs no second pair of eyes. Refusing
        # this would leave a gate nobody can close.
        gate = _gate(db_session, organisation, engineer)
        decision = gates.decide(
            db_session, gate, approve=False, by=engineer, subject=SPEC, note="Changed my mind."
        )
        assert decision.ok
        assert gate.state is GateState.REJECTED


class TestExpiry:
    def test_an_expired_gate_is_undecided_rather_than_refused(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        """`EXPIRED` is not a fourth way of saying no.

        Folding it into `REJECTED` would put a refusal on record that nobody
        made -- the same class of error as an approval nobody gave.
        """
        gate = _gate(db_session, organisation, engineer)
        gate.expires_at = lifecycle.utcnow() - timedelta(minutes=1)

        decision = gates.decide(db_session, gate, approve=True, by=reviewer, subject=SPEC)

        assert decision.refusal is gates.GateRefusal.EXPIRED
        assert gate.state is GateState.EXPIRED
        assert gate.decided_by_id is None, "nobody decided this; it must name nobody"
        assert gate.decided_at is None

    def test_the_sweep_settles_what_nobody_got_to(
        self, db_session: Session, organisation: Organisation, engineer: User
    ) -> None:
        stale = _gate(db_session, organisation, engineer)
        stale.expires_at = lifecycle.utcnow() - timedelta(days=1)
        live = _gate(db_session, organisation, engineer)
        db_session.flush()

        assert gates.expire_due(db_session) == 1
        assert stale.state is GateState.EXPIRED
        assert live.state is GateState.PENDING

    def test_a_decided_gate_is_never_reopened_by_the_sweep(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        gate = _gate(db_session, organisation, engineer)
        gates.decide(db_session, gate, approve=True, by=reviewer, subject=SPEC)
        gate.expires_at = lifecycle.utcnow() - timedelta(days=1)
        db_session.flush()

        gates.expire_due(db_session)

        assert gate.state is GateState.APPROVED


class TestADecisionIsFinal:
    def test_an_approved_gate_cannot_be_flipped(
        self, db_session: Session, organisation: Organisation, engineer: User, reviewer: User
    ) -> None:
        # Somebody may already have acted on the yes. Changing the record
        # afterwards makes the record the least reliable account of what
        # happened, which is the opposite of what it is for.
        gate = _gate(db_session, organisation, engineer)
        gates.decide(db_session, gate, approve=True, by=reviewer, subject=SPEC)

        again = gates.decide(
            db_session, gate, approve=False, by=reviewer, subject=SPEC, note="Actually, no."
        )

        assert again.refusal is gates.GateRefusal.ALREADY_DECIDED
        assert gate.state is GateState.APPROVED


class TestTheRoutes:
    """Where the domain role is read.

    `Membership.domain_role` has existed since P2.2 with a docstring saying
    "16.5/P5 read this column", and until this route nothing ever did.
    """

    @pytest.fixture
    def signed_in(
        self, client: AuthenticatedTestClient, db_session: Session, organisation: Organisation
    ) -> User:
        client.post(
            f"{API}/auth/register",
            json={"email": "owner@kryova.dev", "password": "a-long-enough-password"},
        )
        user = db_session.query(User).filter_by(email="owner@kryova.dev").one()
        user.email_verified_at = lifecycle.utcnow()
        db_session.add(
            Membership(
                organisation_id=organisation.id,
                user_id=user.id,
                role=OrgRole.MEMBER,
                domain_role=None,
            )
        )
        db_session.flush()
        client.post(
            f"{API}/auth/login",
            data={"username": "owner@kryova.dev", "password": "a-long-enough-password"},
        )
        client.headers["x-csrf-token"] = client.cookies["kryova_csrf"]
        return user

    def _raise(self, client: AuthenticatedTestClient, organisation: Organisation) -> str:
        response = client.post(
            f"{API}/organisations/{organisation.id}/gates",
            json={
                "title": "Wall thickness",
                "question": "Drop the wall from 6 mm to 4 mm?",
                "subject_type": "design_spec",
                "subject_id": "spec-1",
                "subject": SPEC,
                "evidence": {"changed": ["thickness_mm"]},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def test_any_member_may_raise_one(
        self,
        client: AuthenticatedTestClient,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        # Raising a gate asks a question and grants nothing. A permission check
        # here would mean the person who noticed the problem cannot report it.
        gate_id = self._raise(client, organisation)
        read = client.get(f"{API}/organisations/{organisation.id}/gates/{gate_id}")
        assert read.status_code == 200
        assert read.json()["state"] == "pending"
        assert read.json()["evidence"] == {"changed": ["thickness_mm"]}

    def test_a_member_without_the_reviewer_role_cannot_decide(
        self,
        client: AuthenticatedTestClient,
        db_session: Session,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        """`domain_role = None` means *not stated*, and does not qualify.

        The column is nullable precisely so that "nobody has said" is
        representable. Reading it as consent would silently qualify every member
        of every organisation.
        """
        gate_id = self._raise(client, organisation)

        response = client.post(
            f"{API}/organisations/{organisation.id}/gates/{gate_id}/decision",
            json={"approve": True, "subject": SPEC},
        )

        assert response.status_code == 403
        assert "reviewer" in response.json()["detail"]

    def test_a_reviewer_decides_and_the_actor_is_recorded(
        self,
        client: AuthenticatedTestClient,
        db_session: Session,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        gate_id = self._raise(client, organisation)
        # Somebody else raised it, so this is not a self-approval.
        gate = db_session.get(ApprovalGate, gate_id)
        assert gate is not None
        other = _member(db_session, organisation, "someone@kryova.dev", DomainRole.ENGINEER)
        gate.requested_by_id = other.id
        membership = (
            db_session.query(Membership)
            .filter_by(organisation_id=organisation.id, user_id=signed_in.id)
            .one()
        )
        membership.domain_role = DomainRole.REVIEWER
        db_session.flush()

        response = client.post(
            f"{API}/organisations/{organisation.id}/gates/{gate_id}/decision",
            json={"approve": True, "subject": SPEC},
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "approved"
        assert response.json()["decided_by_id"] == signed_in.id

    def test_a_stale_subject_is_a_409_and_says_why(
        self,
        client: AuthenticatedTestClient,
        db_session: Session,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        gate_id = self._raise(client, organisation)
        gate = db_session.get(ApprovalGate, gate_id)
        assert gate is not None
        gate.requested_by_id = _member(
            db_session, organisation, "another@kryova.dev", DomainRole.ENGINEER
        ).id
        membership = (
            db_session.query(Membership)
            .filter_by(organisation_id=organisation.id, user_id=signed_in.id)
            .one()
        )
        membership.domain_role = DomainRole.REVIEWER
        db_session.flush()

        response = client.post(
            f"{API}/organisations/{organisation.id}/gates/{gate_id}/decision",
            json={"approve": True, "subject": SPEC | {"thickness_mm": 3.0}},
        )

        assert response.status_code == 409
        assert "changed since it was raised" in response.json()["detail"]

    def test_a_gate_in_another_organisation_is_404_not_403(
        self,
        client: AuthenticatedTestClient,
        db_session: Session,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        elsewhere = Organisation(name="Other", slug="other-co", is_personal=False)
        db_session.add(elsewhere)
        db_session.flush()
        stranger = _member(db_session, elsewhere, "stranger@kryova.dev", DomainRole.REVIEWER)
        theirs = _gate(db_session, elsewhere, stranger)
        db_session.flush()

        response = client.get(f"{API}/organisations/{organisation.id}/gates/{theirs.id}")

        # The service-wide rule: ids must not be enumerable across tenants.
        assert response.status_code == 404

    def test_the_list_is_paginated_and_filterable(
        self,
        client: AuthenticatedTestClient,
        organisation: Organisation,
        signed_in: User,
    ) -> None:
        for _ in range(3):
            self._raise(client, organisation)

        page = client.get(
            f"{API}/organisations/{organisation.id}/gates",
            params={"state": "pending", "page_size": 2},
        )

        assert page.status_code == 200
        body = page.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2
        assert body["page_size"] == 2


class TestTheStateSurvivesTheDatabase:
    def test_a_gate_read_back_carries_the_enum_not_a_string(
        self, db_session: Session, organisation: Organisation, engineer: User
    ) -> None:
        """The `MessageRoleType` landmine, met a second time.

        A bare `String(16)` column gives the enum on a row written in this
        session and a plain `str` on one loaded from the database, so
        `gate.state.is_decided` raises `AttributeError` on every gate a reviewer
        actually opens -- while passing in any test that writes and reads
        without an expire. `models/types.EnumText` is the fix; this is what
        keeps it.
        """
        gate = _gate(db_session, organisation, engineer)
        db_session.commit()
        db_session.expire_all()

        reloaded = db_session.get(ApprovalGate, gate.id)
        assert reloaded is not None
        assert isinstance(reloaded.state, GateState)
        # The call that raised. `==` would have passed either way, which is why
        # this bug is invisible until something reaches for a member.
        assert reloaded.state.is_decided is False


class TestTheDigest:
    def test_two_equal_subjects_digest_the_same(self) -> None:
        assert gates.digest_of({"a": 1, "b": [2, 3]}) == gates.digest_of({"b": [2, 3], "a": 1})

    def test_a_changed_number_changes_the_digest(self) -> None:
        assert gates.digest_of({"a": 1.0}) != gates.digest_of({"a": 1.5})

    def test_an_unserialisable_subject_still_digests_rather_than_raising(self) -> None:
        """`default=repr` -- worse than a real encoding, never silently equal.

        A gate that raised here would fail at the moment somebody tried to ask
        for a review, which is the worst time for this to be the thing that
        breaks.
        """
        assert gates.digest_of({"when": object()}) != gates.digest_of({"when": object()})
