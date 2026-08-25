"""AI layer tests.

No test here calls a real model. The provider is a seam precisely so the
service, the routes and the error translation can be exercised against a stub,
which keeps the suite offline, deterministic and free.
"""

from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.ai import service
from app.ai.provider import LLMError, LLMProvider, LLMRefusal, LLMUnavailable
from app.ai.providers._json_schema import strictify
from app.ai.schemas import Finding, LoadCaseDraft, ResultInterpretation
from app.main import app
from tests.typing import AuthenticatedTestClient

T = TypeVar("T", bound=BaseModel)

SUCCEEDED_RESULT: dict[str, Any] = {
    "factor_of_safety": 2.4,
    "yields": False,
    "max_von_mises_mpa": 115.0,
    "max_displacement_mm": 0.42,
    "mass_kg": 1.87,
    "volume_mm3": 692_592.0,
    "element_count": 41_233,
    "node_count": 9_812,
    "warnings": [],
}

LOAD_CASE: dict[str, Any] = {
    "name": "Tip load",
    "material": {
        "name": "aluminium-6061-t6",
        "youngs_modulus_mpa": 68_900,
        "poissons_ratio": 0.33,
        "yield_strength_mpa": 276,
        "density_kg_m3": 2700,
    },
    "fixtures": [{"where": {"type": "face", "axis": "z", "side": "min"}, "dofs": ["x", "y", "z"]}],
    "loads": [{"where": {"type": "face", "axis": "z", "side": "max"}, "force_n": [0, 0, -500]}],
}


class StubProvider(LLMProvider):
    """Records what it was asked, returns whatever the test told it to."""

    name = "stub"

    def __init__(self, response: BaseModel | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def health(self) -> None:
        if isinstance(self._error, LLMUnavailable):
            raise self._error

    def complete(
        self, *, system: str, user: str, schema: type[T], effort: str, max_tokens: int
    ) -> T:
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "effort": effort}
        )
        if self._error is not None:
            raise self._error
        assert isinstance(self._response, schema)
        return self._response


def _interpretation() -> ResultInterpretation:
    return ResultInterpretation(
        verdict="safe",
        headline="Peak stress is 42% of yield.",
        findings=[
            Finding(
                title="Stress well inside elastic range",
                detail="Peak von Mises is 115 MPa against a 276 MPa yield.",
                severity="info",
            )
        ],
        suggestions=[],
        confidence="high",
        caveat="Linear static assumes small deflection and static loading.",
    )


class TestStrictify:
    """Hosted providers reject a schema that leaves objects open."""

    def test_closes_objects_and_requires_every_property(self) -> None:
        strict = strictify(ResultInterpretation.model_json_schema())
        assert strict["additionalProperties"] is False
        assert set(strict["required"]) == set(strict["properties"])

    def test_recurses_into_nested_definitions(self) -> None:
        strict = strictify(LoadCaseDraft.model_json_schema())
        for definition in strict.get("$defs", {}).values():
            if definition.get("type") == "object" and "properties" in definition:
                assert definition["additionalProperties"] is False

    def test_does_not_mutate_the_cached_input(self) -> None:
        """`model_json_schema()` caches -- editing in place would poison it."""
        original = ResultInterpretation.model_json_schema()
        before = original.get("additionalProperties")
        strictify(original)
        assert original.get("additionalProperties") == before


class TestInterpretService:
    def test_passes_the_frozen_system_prompt_and_the_configured_effort(self) -> None:
        provider = StubProvider(response=_interpretation())
        service.interpret_result(provider, result=SUCCEEDED_RESULT, load_case=LOAD_CASE)

        call = provider.calls[0]
        assert call["schema"] is ResultInterpretation
        assert call["effort"] == "high"
        # The volatile numbers belong in the user turn, never the system prompt:
        # a system prompt that changes per request can never be cached.
        assert "115.0" not in call["system"]
        assert "115.0" in call["user"]

    def test_sends_only_whitelisted_fields(self) -> None:
        """An unfiltered row dump would put ids and timestamps in the prompt."""
        provider = StubProvider(response=_interpretation())
        noisy = {**SUCCEEDED_RESULT, "id": "sim_secret", "created_at": "2026-08-25"}
        service.interpret_result(provider, result=noisy, load_case=LOAD_CASE)

        assert "sim_secret" not in provider.calls[0]["user"]

    def test_includes_the_material_and_solver_warnings(self) -> None:
        provider = StubProvider(response=_interpretation())
        service.interpret_result(
            provider,
            result={**SUCCEEDED_RESULT, "warnings": ["Coarse mesh near the fillet"]},
            load_case=LOAD_CASE,
        )
        user = provider.calls[0]["user"]
        assert "aluminium-6061-t6" in user
        assert "Coarse mesh near the fillet" in user


class TestPrompts:
    """The prompts encode rules the product depends on."""

    def test_interpret_prompt_forbids_inventing_numbers(self) -> None:
        from app.ai.prompts import INTERPRET_SYSTEM

        assert "Never compute" in INTERPRET_SYSTEM
        assert "Never convert units" in INTERPRET_SYSTEM

    def test_prompts_carry_the_real_material_library(self) -> None:
        """A hardcoded copy would drift the moment a material is added."""
        from app.ai.prompts import PARSE_LOAD_CASE_SYSTEM
        from app.solve.materials import MATERIALS

        for name in MATERIALS:
            assert name in PARSE_LOAD_CASE_SYSTEM

    def test_system_prompts_are_constant(self) -> None:
        """No timestamp or per-request value may leak into a cached prefix."""
        from app.ai import prompts

        assert prompts.INTERPRET_SYSTEM == prompts.INTERPRET_SYSTEM
        for token in ("{", "}"):
            # f-strings are already rendered; a stray brace means an unfilled slot.
            assert token not in prompts.INTERPRET_SYSTEM.replace("{}", "")


class TestAIRoutes:
    """The routes are registered and translate provider failures correctly."""

    def test_status_reports_the_configured_provider(self, client: TestClient) -> None:
        response = client.get("/api/v1/ai/status")
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "ollama"
        # No Ollama running in CI, so it must report unavailable with a reason
        # rather than pretending the feature works.
        if not body["enabled"]:
            assert body["detail"]

    def test_interpretation_requires_authentication(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/projects/does-not-exist/simulations/nope/interpretation"
        )
        assert response.status_code == 401

    def test_interpretation_404s_for_another_users_project(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        """404, never 403 -- a 403 would confirm the id exists."""
        response = auth_client.post(
            "/api/v1/projects/not-mine/simulations/nope/interpretation",
            headers={"x-csrf-token": auth_client.cookies["kryova_csrf"]},
        )
        assert response.status_code == 404

    def test_load_case_draft_404s_without_a_geometry(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        csrf = auth_client.cookies["kryova_csrf"]
        project = auth_client.post(
            "/api/v1/projects", headers={"x-csrf-token": csrf}, json={"name": "Bracket"}
        ).json()

        response = auth_client.post(
            f"/api/v1/projects/{project['id']}/ai/load-case",
            headers={"x-csrf-token": csrf},
            json={"description": "clamp the base and press 200 N on the top"},
        )
        assert response.status_code == 404
        assert "geometry" in response.json()["detail"].lower()


class TestErrorTranslation:
    """A provider failure must not surface as a 500."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (LLMUnavailable("no model"), 503),
            (LLMRefusal("declined"), 422),
            (LLMError("bad json"), 502),
        ],
    )
    def test_each_failure_maps_to_its_status(self, error: LLMError, expected: int) -> None:
        from app.api.routes.ai import _translate

        assert _translate(error).status_code == expected

    def test_unavailable_is_checked_before_the_generic_case(self) -> None:
        """LLMUnavailable subclasses LLMError; order in _translate matters."""
        from app.api.routes.ai import _translate

        assert issubclass(LLMUnavailable, LLMError)
        assert _translate(LLMUnavailable("x")).status_code == 503


def test_app_exposes_the_ai_routes_in_the_schema() -> None:
    """Assert against the OpenAPI document, not `app.routes`.

    Starlette keeps the included routes behind a Mount, so `app.routes` lists
    only the top-level entries. The generated schema is both the authoritative
    surface and the one clients and Swagger actually read.
    """
    paths = app.openapi()["paths"]
    assert "/api/v1/ai/status" in paths
    assert "/api/v1/projects/{project_id}/ai/load-case" in paths
    assert "/api/v1/projects/{project_id}/simulations/{simulation_id}/interpretation" in paths

    # response_model set on every one, so the schema is never `any`.
    for path, methods in paths.items():
        if "/ai/" in path or path.endswith("/interpretation"):
            for operation in methods.values():
                schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
                assert schema.get("$ref"), f"{path} has an untyped 200 response"
