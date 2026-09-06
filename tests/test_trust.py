"""The public trust surface — master plan P10.3.

Three things are under test here and they pull in opposite directions, which is
why they are in one file.

**It must be reachable without an account.** The claim Kryova is sold on is that
verification is the product, and a page making that claim behind a login can
only be read by people who already bought. The person it has to convince is the
one without a token.

**Therefore it must carry nothing that belongs to anybody.** The rest of this API
returns 404 rather than 403 so ids cannot be enumerated across accounts; opening
one router to the world is a decision that has to be paid for. It is paid for by
the payload being module constants and recorded benchmark outcomes, with
provenance reduced to an allowlist and every free-text field scrubbed. The tests
below try to get tenant-shaped data out of it and fail to.

**And the commitments page has to stay true.** Every commitment claiming
mechanical enforcement names files; those files are asserted to exist, so the
page cannot rot into claims about code that was renamed.

Offline: a bare app carrying only this router, so a passing test cannot be
resting on a fixture that supplied auth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import api_router
from app.api.routes import trust
from app.verify import commitments
from app.verify.benchmarks import BenchmarkOutcome, Outcome, Target, TargetBasis
from app.verify.commitments import COMMITMENTS, Commitment, Enforcement
from app.verify.register import ANALYSES, Register, _public_provenance

REPO_ROOT = Path(__file__).resolve().parents[1]

PAGES = (
    "/trust",
    "/trust/validation-register",
    "/trust/commitments",
    "/trust/changelog",
)


@pytest.fixture
def anonymous() -> TestClient:
    """A client with no credentials against an app with no auth wired at all.

    Deliberately not the suite's `client` fixture: that one builds the whole
    application and a database, and a public route proved public through it
    would only be proved public *there*.
    """
    app = FastAPI()
    app.include_router(trust.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Public, and wired
# ---------------------------------------------------------------------------


class TestThePagesAreReachableWithoutAnAccount:
    @pytest.mark.parametrize("path", PAGES)
    def test_no_token_is_needed(self, anonymous: TestClient, path: str) -> None:
        response = anonymous.get(path)

        assert response.status_code == 200, response.text
        assert response.json()

    @pytest.mark.parametrize("path", PAGES)
    def test_the_pages_are_cacheable(self, anonymous: TestClient, path: str) -> None:
        """A trust page that cannot be cached falls over the first time somebody
        links to it from outside."""
        assert anonymous.get(path).headers["cache-control"] == "public, max-age=300"

    def test_the_router_is_wired_into_the_application(self) -> None:
        """An unwired router is invisible everywhere, including /docs.

        Read off the OpenAPI schema rather than off `api_router.routes`:
        FastAPI defers inclusion, so the router's own `routes` list holds
        opaque `_IncludedRouter` entries until a schema or a request forces it.
        Going through the schema also checks the thing the docstring claims —
        that these pages appear in `/docs`.
        """
        probe = FastAPI()
        probe.include_router(api_router)
        schema = TestClient(probe).get("/openapi.json").json()

        assert set(PAGES) <= set(schema["paths"])

    def test_no_route_here_depends_on_a_session_or_a_user(self) -> None:
        """The structural version of the assertion above.

        Checking response bodies proves today's payload is clean; checking the
        dependency graph proves the next edit cannot quietly make one of these
        endpoints tenant-aware. `DbSession` and `CurrentUser` are the two
        annotated dependencies every owned resource in this API goes through.
        """
        forbidden = {"get_db", "get_session_scope", "get_current_user", "get_owned_project"}

        for route in trust.router.routes:
            dependant = getattr(route, "dependant", None)
            assert dependant is not None
            names = {
                getattr(sub.call, "__name__", "")
                for sub in dependant.dependencies
            } | {getattr(dependant.call, "__name__", "")}
            assert not (names & forbidden), route.path
            assert getattr(dependant, "security_requirements", []) == [], route.path
            assert getattr(route, "dependencies", []) == [], route.path


# ---------------------------------------------------------------------------
# 2. It leaks nothing
# ---------------------------------------------------------------------------


#: Words that would only appear in this payload if something tenant-shaped had
#: leaked into it. `user`/`org` are checked as whole words: `answers` contains
#: neither, but a substring search for "user" would hit nothing here and a
#: careless one for "org" would hit "organisation" if it ever appeared.
_TENANT_WORDS = re.compile(
    r"\b(user_id|users?|org|orgs?|organisation|organization|tenant|project_id|"
    r"conversation|email|token|api_key|password)\b",
    re.IGNORECASE,
)

_PATHISH = re.compile(r"[A-Za-z]:[\\/]|\\\\[^\s\\]+\\|/home/|/Users/|/var/lib/")


class TestThePayloadCarriesNothingTenantSpecific:
    @pytest.mark.parametrize("path", PAGES)
    def test_no_tenant_vocabulary_appears_anywhere(
        self, anonymous: TestClient, path: str
    ) -> None:
        body = anonymous.get(path).text

        found = _TENANT_WORDS.findall(body)
        assert not found, f"{path} mentions {sorted(set(found))}"

    @pytest.mark.parametrize("path", PAGES)
    def test_no_filesystem_path_appears_anywhere(
        self, anonymous: TestClient, path: str
    ) -> None:
        assert not _PATHISH.search(anonymous.get(path).text), path

    def test_provenance_is_reduced_to_an_allowlist(self) -> None:
        """`geometry.source` is documented free text and a customer's file name
        lands in it; `load_case.name` and a custom material name are user-typed;
        `notes` is free text. None of them survives."""
        record = {
            "geometry": {
                "source": r"C:\Users\a-customer\Desktop\confidential-frame.CATPart",
                "digest": "sha256:abc",
            },
            "mesh": {"node_count": 1200, "min_quality": 0.31},
            "material": {"name": "CustomerAlloy-7", "youngs_modulus_mpa": 210000},
            "load_case": {"name": "customer duty cycle 3", "type": "LoadCase", "digest": "s:1"},
            "solver": {"name": "internal", "code_digest": "sha256:def"},
            "environment": {"python": "3.14.0", "platform": "win32"},
            "notes": {"operator": "a.customer@example.com"},
        }

        public = _public_provenance(record)

        assert public is not None
        blob = json.dumps(public)
        assert "confidential-frame" not in blob
        assert "CustomerAlloy-7" not in blob
        assert "duty cycle" not in blob
        assert "example.com" not in blob
        # And the evidence that is genuinely about the computation survives.
        assert public["mesh"]["node_count"] == 1200
        assert public["geometry"]["digest"] == "sha256:abc"
        assert public["solver"]["name"] == "internal"

    def test_an_exception_message_carrying_a_path_is_withheld(self) -> None:
        """The live leak vector, not a hypothetical: `run_benchmark` records an
        ERRORED case's `detail` as the exception's own message, and a
        `FileNotFoundError` on the Windows seat puts the customer's home
        directory in one."""
        errored = BenchmarkOutcome(
            benchmark_id="case-1",
            title="a case",
            analysis="linear-static",
            outcome=Outcome.ERRORED,
            target=Target(basis=TargetBasis.UNKNOWN, unit="MPa", reason="not looked up"),
            detail=r"FileNotFoundError: C:\Users\a-customer\meshes\frame.msh",
        )

        payload = json.dumps(Register.build([errored]).to_dict())

        assert "a-customer" not in payload
        assert "withheld" in payload

    def test_breaking_it_the_same_leak_is_visible_without_the_scrub(self) -> None:
        """The guard watched from the other side: the raw outcome really does
        carry the path, so the assertion above is about the scrub and not about
        the string never having been there."""
        raw = BenchmarkOutcome(
            benchmark_id="case-1",
            title="a case",
            analysis="linear-static",
            outcome=Outcome.ERRORED,
            target=Target(basis=TargetBasis.UNKNOWN, unit="MPa", reason="not looked up"),
            detail=r"FileNotFoundError: C:\Users\a-customer\meshes\frame.msh",
        )

        assert "a-customer" in json.dumps(raw.to_dict())


# ---------------------------------------------------------------------------
# 3. The register page says what is not validated
# ---------------------------------------------------------------------------


class TestTheRegisterPage:
    def test_it_publishes_the_unvalidated_analyses(self, anonymous: TestClient) -> None:
        payload = anonymous.get("/trust/validation-register").json()

        assert {row["id"] for row in payload["not_validated"]} == {a.id for a in ANALYSES}
        assert payload["complete"] is False
        assert payload["summary"]["analyses_validated"] == 0

    def test_the_index_repeats_the_headline(self, anonymous: TestClient) -> None:
        """A reader who follows one link and no further still leaves knowing how
        much is not validated."""
        index = anonymous.get("/trust").json()

        assert f"0 of {len(ANALYSES)}" in index["validation_headline"]
        assert index["everything_validated"] is False

    def test_it_serves_the_accuracy_changes_that_supersede_it(
        self, anonymous: TestClient
    ) -> None:
        payload = anonymous.get("/trust/validation-register").json()

        assert "accuracy_changes_since_generated" in payload


# ---------------------------------------------------------------------------
# 4. The commitments page
# ---------------------------------------------------------------------------


class TestWhatKryovaWillNotClaim:
    def test_decision_five_is_reproduced_and_is_the_page_s_scope(
        self, anonymous: TestClient
    ) -> None:
        payload = anonymous.get("/trust/commitments").json()

        assert "structural, kinematic and packaging" in payload["scope"]
        assert "a licensed engineer can review and sign" in payload["scope"]

    def test_unattended_sign_off_is_refused_in_words(self, anonymous: TestClient) -> None:
        """Decision 5's hard limit. If this test ever has to be deleted, that is
        a change to what the product is, not a change to a test."""
        payload = anonymous.get("/trust/commitments").json()
        entry = next(c for c in payload["commitments"] if c["id"] == "no-unattended-sign-off")

        assert "will not" in entry["we_will_not"]
        assert "unattended sign-off" in entry["we_will_not"]
        assert "licensed engineer signs" in payload["sign_off"]

    def test_the_unmeasured_rule_is_a_public_commitment(self, anonymous: TestClient) -> None:
        payload = anonymous.get("/trust/commitments").json()
        entry = next(c for c in payload["commitments"] if c["id"] == "unmeasured-is-never-a-pass")

        assert entry["enforcement"] == "mechanical"
        assert "app/design/assertions.py" in entry["enforced_by"]

    def test_every_named_enforcement_file_exists(self) -> None:
        """What makes this page checkable rather than persuasive. A commitment
        pointing at a file that was renamed is a claim nobody can verify, which
        is the failure mode of every trust page ever published."""
        for commitment in COMMITMENTS:
            for relative in commitment.enforced_by:
                assert (REPO_ROOT / relative).exists(), f"{commitment.id} -> {relative}"

    def test_a_mechanical_claim_with_no_file_is_refused(self) -> None:
        """Breaking it: 'the software enforces this' with nothing to point at is
        a policy with better marketing."""
        with pytest.raises(ValueError) as refused:
            Commitment(
                id="we-are-careful",
                we_will_not="We will not be careless.",
                why="because",
                enforcement=Enforcement.MECHANICAL,
            )

        assert "marketing" in str(refused.value)

    def test_a_policy_commitment_is_allowed_to_name_nothing_and_says_so(self) -> None:
        """The honest half of the split. No assertion can enforce a statement
        about what a product is for, and dressing one up with a file would be
        the first untrue thing on the page."""
        payload = commitments.to_dict()
        policy = [c for c in payload["commitments"] if c["enforcement"] == "policy"]

        assert policy
        assert all(c["enforced_by"] == [] for c in policy)
        assert payload["summary"]["policy_only"] == len(policy)
        assert payload["summary"]["mechanically_enforced"] == len(COMMITMENTS) - len(policy)

    def test_every_commitment_is_stated_in_the_negative(self) -> None:
        """'We are honest about coverage' is a slogan. 'We will not report an
        unmeasured claim as a pass' is a commitment, because it names something
        that would cost us."""
        for commitment in COMMITMENTS:
            assert "will not" in commitment.we_will_not, commitment.id


# ---------------------------------------------------------------------------
# 5. The changelog page
# ---------------------------------------------------------------------------


class TestTheAccuracyChangelogPage:
    def test_every_entry_says_what_to_do(self, anonymous: TestClient) -> None:
        payload = anonymous.get("/trust/changelog").json()

        assert payload["changes"]
        for change in payload["changes"]:
            assert change["what_to_do"].strip(), change["date"]
            assert change["analyses"], change["date"]

    def test_it_says_where_the_record_starts(self, anonymous: TestClient) -> None:
        """An empty tail must not read as 'nothing changed before this'."""
        payload = anonymous.get("/trust/changelog").json()

        assert payload["record_begins"]
        assert "does not reach back" in payload["note"]

    def test_a_results_changing_entry_is_flagged_as_one(self, anonymous: TestClient) -> None:
        payload = anonymous.get("/trust/changelog").json()

        assert any(change["changes_results"] for change in payload["changes"])
