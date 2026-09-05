"""Phase 4.2 — the visual check.

Offline throughout: no database, no network, no model. The provider is a stub
that records what it was asked and answers what the test tells it to, which is
the only way to pin the behaviour that matters here — what happens when the
model is wrong, unsure, missing, or answering about a picture it never saw.

Every guard below was verified by breaking the thing it guards and watching the
named test fail.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai import vision
from app.ai.provider import (
    AssistantTurn,
    Completion,
    LLMError,
    LLMProvider,
    LLMUnavailable,
    TokenUsage,
    VisionUnsupported,
)
from app.ai.providers.ollama import IMAGE_PROMPT_TOKENS, OllamaProvider
from app.ai.schemas import Discrepancy, VisualCheck
from app.render import Frame, Projection, Render

# ---------------------------------------------------------------------------
# Stubs.
# ---------------------------------------------------------------------------


class _Blind(LLMProvider):
    """A provider that has not overridden `look` — the ABC's default answer."""

    name = "blind"

    def complete(self, **kwargs: Any) -> Completion[Any]:  # pragma: no cover - unused
        raise NotImplementedError

    def chat(self, **kwargs: Any) -> AssistantTurn:  # pragma: no cover - unused
        raise NotImplementedError

    def health(self) -> None:  # pragma: no cover - unused
        return None


class _Seeing(_Blind):
    """A provider that answers whatever the test hands it, and records the call."""

    name = "seeing"

    def __init__(self, answer: VisualCheck | Exception) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def look(
        self,
        *,
        system: str,
        user: str,
        images: Any,
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> Completion[Any]:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "images": list(images),
                "schema": schema,
                "effort": effort,
                "max_tokens": max_tokens,
            }
        )
        if isinstance(self.answer, Exception):
            raise self.answer
        return Completion(value=self.answer, usage=TokenUsage(prompt_tokens=7, completion_tokens=3))


def _render(view: str, png: bytes = b"\x89PNG-not-real", blank: bool = False) -> Render:
    """A `Render` without going near OCCT — this module never looks at pixels."""
    return Render(
        view=view,
        png=png,
        width=8,
        height=8,
        frame=Frame(width=8, height=8, scale=1.0, origin_mm=(0.0, 0.0)),
        projection=Projection(
            view=view,
            visible=() if blank else ((((0.0, 0.0), (1.0, 1.0))),),
            hidden=(),
            extent=(0.0, 0.0, 1.0, 1.0),
        ),
    )


def _check(
    verdict: str, discrepancies: list[Discrepancy] | None = None, describes: str = "A flat plate."
) -> VisualCheck:
    return VisualCheck(
        describes=describes,
        verdict=verdict,  # type: ignore[arg-type]
        discrepancies=discrepancies or [],
        confidence="medium",
    )


def _gross(what: str = "No hole anywhere.", view: str = "top") -> Discrepancy:
    return Discrepancy(what=what, view=view, severity="gross")


# ---------------------------------------------------------------------------


class TestAnUnrunCheckIsNeverAPass:
    """The rule the whole module is arranged around."""

    def test_a_provider_that_cannot_see_is_unchecked_and_says_so(self) -> None:
        review = vision.review("a plate", [_render("front")], provider=_Blind())
        assert review.outcome == "unchecked"
        assert not review.checked
        assert not review.objected
        # The reason names the provider, so the message tells the user what to fix.
        assert "blind" in review.reason

    def test_the_model_being_unsure_is_unchecked_not_a_pass(self) -> None:
        provider = _Seeing(_check("unsure", describes="Only an outline is visible."))
        review = vision.review("a plate with a hole", [_render("front")], provider=provider)
        assert review.outcome == "unchecked"
        # The description survives: it is what tells the caller which view to add.
        assert "Only an outline" in review.reason
        assert review.check is not None

    def test_differs_with_nothing_named_is_unchecked_not_an_objection(self) -> None:
        """An unlocatable complaint cannot be acted on, so it must not block a build."""
        provider = _Seeing(_check("differs", discrepancies=[]))
        review = vision.review("a plate", [_render("front")], provider=provider)
        assert review.outcome == "unchecked"
        assert not review.objected
        assert "named nothing specific" in review.reason

    def test_a_model_failure_is_unchecked_and_never_raises(self) -> None:
        provider = _Seeing(LLMError("the model fell over"))
        review = vision.review("a plate", [_render("front")], provider=provider)
        assert review.outcome == "unchecked"
        assert "fell over" in review.reason

    def test_an_unreachable_provider_is_unchecked_and_never_raises(self) -> None:
        provider = _Seeing(LLMUnavailable("nothing is listening"))
        review = vision.review("a plate", [_render("front")], provider=provider)
        assert review.outcome == "unchecked"

    def test_nothing_drawn_is_not_sent_to_a_model_at_all(self) -> None:
        """A blank render is a picture of nothing; asking about it invites a guess."""
        provider = _Seeing(_check("matches"))
        review = vision.review(
            "a plate", [_render("front", blank=True), _render("top", blank=True)], provider=provider
        )
        assert review.outcome == "unchecked"
        assert provider.calls == []
        # Still bound to what was looked at, so the record says which views were empty.
        assert review.views == ("front", "top")

    def test_one_blank_view_among_several_still_runs(self) -> None:
        """A part genuinely invisible from one direction is normal, not a failure."""
        provider = _Seeing(_check("matches"))
        review = vision.review(
            "a plate", [_render("front", blank=True), _render("iso")], provider=provider
        )
        assert review.outcome == "matches"

    def test_no_renders_at_all_is_unchecked(self) -> None:
        assert vision.review("a plate", [], provider=_Blind()).outcome == "unchecked"


class TestTheVerdictIsBoundToTheImages:
    """Decision 3: a result is bound to what produced it."""

    def test_views_and_digests_record_exactly_what_was_looked_at(self) -> None:
        provider = _Seeing(_check("matches"))
        first, second = _render("front", b"one"), _render("iso", b"two")
        review = vision.review("a plate", [first, second], provider=provider)
        assert review.views == ("front", "iso")
        assert review.digests == (first.digest, second.digest)

    def test_the_images_are_sent_in_the_order_the_prompt_names(self) -> None:
        """The order is the only thing tying an image to what it is a picture of."""
        provider = _Seeing(_check("matches"))
        renders = [_render("front", b"F"), _render("top", b"T"), _render("iso", b"I")]
        vision.review("a plate", renders, provider=provider)

        call = provider.calls[0]
        assert call["images"] == [b"F", b"T", b"I"]
        named = call["user"].split("attached in this order: ")[1]
        assert named.startswith("front, top, iso")

    def test_the_request_goes_in_the_user_turn_not_the_system_prompt(self) -> None:
        """A system prompt that changes per request can never be cached."""
        from app.ai import prompts

        provider = _Seeing(_check("matches"))
        vision.review("a 60x40 plate", [_render("front")], provider=provider)
        call = provider.calls[0]
        assert call["system"] == prompts.VISUAL_CHECK_SYSTEM
        assert "60x40" in call["user"]
        assert "60x40" not in call["system"]

    def test_a_long_request_is_truncated_before_it_crowds_out_the_images(self) -> None:
        provider = _Seeing(_check("matches"))
        vision.review("x" * 50_000, [_render("front")], provider=provider)
        assert len(provider.calls[0]["user"]) < vision.REQUEST_MAX_CHARS + 500


class TestWhatACallerMayActOn:
    def test_a_named_disagreement_is_an_objection(self) -> None:
        provider = _Seeing(_check("differs", [_gross()]))
        review = vision.review("a plate with a hole", [_render("top")], provider=provider)
        assert review.outcome == "differs"
        assert review.objected
        assert review.checked
        assert review.gross == ("No hole anywhere.",)

    def test_a_minor_note_is_an_objection_but_not_gross(self) -> None:
        minor = Discrepancy(what="The fillet looks large.", view="iso", severity="minor")
        provider = _Seeing(_check("differs", [minor]))
        review = vision.review("a plate", [_render("iso")], provider=provider)
        assert review.objected
        assert review.gross == ()

    def test_matches_is_corroboration_and_there_is_no_pass_flag_to_misread(self) -> None:
        """A filter, never a sign-off — so the API offers nothing to gate a release on."""
        provider = _Seeing(_check("matches"))
        review = vision.review("a plate", [_render("front")], provider=provider)
        assert review.outcome == "matches"
        assert not review.objected
        for forbidden in ("approved", "passed", "ok", "signed_off"):
            assert not hasattr(review, forbidden)

    def test_usage_is_carried_so_the_check_can_be_metered(self) -> None:
        provider = _Seeing(_check("matches"))
        review = vision.review("a plate", [_render("front")], provider=provider)
        assert review.usage.total_tokens == 10

    def test_the_summary_never_invents_a_verdict(self) -> None:
        assert "Not visually checked" in vision._unchecked("no model").summary()
        provider = _Seeing(_check("differs", [_gross()]))
        review = vision.review("a plate", [_render("top")], provider=provider)
        assert "1 respect" in review.summary()


class TestTheDefaultViews:
    def test_three_views_by_default_not_all_eight(self) -> None:
        """Images dominate the cost; three perpendicular ones fix the silhouette."""
        assert vision.DEFAULT_VIEWS == ("front", "top", "iso")

    def test_a_render_failure_is_unchecked_rather_than_a_raised_exception(self) -> None:
        review = vision.review_shape("a plate", object(), provider=_Blind())
        assert review.outcome == "unchecked"
        assert "could not be rendered" in review.reason


class TestOllamaWillNotAnswerAboutAnImageItCannotSee:
    """The single most dangerous case: Ollama drops the image and answers anyway."""

    def _provider(self, monkeypatch: pytest.MonkeyPatch, show: dict[str, Any]) -> OllamaProvider:
        def fake_post(url: str, **kwargs: Any) -> Any:
            assert url.endswith("/api/show")
            return httpx.Response(200, json=show, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        return OllamaProvider("http://localhost:11434", "qwen2.5-coder:7b", 5.0)

    def test_a_model_reporting_vision_can_see(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider(monkeypatch, {"capabilities": ["completion", "vision"]})
        assert provider._sees() is True

    def test_a_projector_block_alone_is_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only a multimodal model has a projector; it *is* the vision encoder."""
        provider = self._provider(monkeypatch, {"projector_info": {"clip.has_vision_encoder": True}})
        assert provider._sees() is True

    def test_a_text_only_model_is_refused_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider(monkeypatch, {"capabilities": ["completion", "tools"]})
        with pytest.raises(VisionUnsupported) as raised:
            provider.look(
                system="s",
                user="u",
                images=[b"png"],
                schema=VisualCheck,
                effort="low",
                max_tokens=100,
            )
        message = str(raised.value)
        assert "qwen2.5-coder:7b" in message
        assert "AI_VISION_MODEL" in message

    def test_the_capability_is_probed_once_not_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def fake_post(url: str, **kwargs: Any) -> Any:
            calls.append(url)
            return httpx.Response(
                200, json={"capabilities": ["vision"]}, request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        provider = OllamaProvider("http://localhost:11434", "llava", 5.0)
        assert provider._sees() and provider._sees() and provider._sees()
        assert len(calls) == 1

    def test_the_vision_model_setting_is_what_gets_probed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[dict[str, Any]] = []

        def fake_post(url: str, **kwargs: Any) -> Any:
            asked.append(kwargs.get("json") or {})
            return httpx.Response(
                200, json={"capabilities": ["vision"]}, request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        provider = OllamaProvider(
            "http://localhost:11434", "qwen2.5-coder:7b", 5.0, vision_model="llava"
        )
        provider._sees()
        assert asked[0]["model"] == "llava"


class TestOllamaSizesItsWindowForThePictures:
    def test_the_window_leaves_room_for_every_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sized from the text alone, the images fall off the front of the prompt."""
        sent: list[dict[str, Any]] = []

        def fake_post(url: str, **kwargs: Any) -> Any:
            payload = kwargs.get("json") or {}
            if url.endswith("/api/show"):
                return httpx.Response(
                    200, json={"capabilities": ["vision"]}, request=httpx.Request("POST", url)
                )
            sent.append(payload)
            answer = _check("matches").model_dump_json()
            return httpx.Response(
                200,
                json={"message": {"content": answer}, "prompt_eval_count": 12},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        provider = OllamaProvider("http://localhost:11434", "llava", 5.0)
        provider._num_ctx = 8_192

        provider.look(
            system="s",
            user="u",
            images=[b"a", b"b", b"c"],
            schema=VisualCheck,
            effort="low",
            max_tokens=1_200,
        )
        payload = sent[0]
        assert payload["options"]["num_ctx"] >= 3 * IMAGE_PROMPT_TOKENS + 1_200

    def test_the_images_ride_on_the_user_message_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import base64

        sent: list[dict[str, Any]] = []

        def fake_post(url: str, **kwargs: Any) -> Any:
            if url.endswith("/api/show"):
                return httpx.Response(
                    200, json={"capabilities": ["vision"]}, request=httpx.Request("POST", url)
                )
            sent.append(kwargs.get("json") or {})
            return httpx.Response(
                200,
                json={"message": {"content": _check("matches").model_dump_json()}},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        provider = OllamaProvider("http://localhost:11434", "llava", 5.0)
        provider._num_ctx = 8_192
        provider.look(
            system="s",
            user="u",
            images=[b"first", b"second"],
            schema=VisualCheck,
            effort="low",
            max_tokens=100,
        )
        message = sent[0]["messages"][-1]
        assert message["role"] == "user"
        assert message["images"] == [
            base64.b64encode(b"first").decode(),
            base64.b64encode(b"second").decode(),
        ]
        # Constrained decoding, same as every other structured call here.
        assert json.dumps(sent[0]["format"])


class TestThePromptSaysWhatTheModelMustNotDo:
    def test_it_forbids_reading_a_dimension_off_the_picture(self) -> None:
        """A drawing has no scale; a number from the model would read as a measurement."""
        from app.ai.prompts import VISUAL_CHECK_SYSTEM

        assert "no scale" in VISUAL_CHECK_SYSTEM
        assert "never estimate one" in VISUAL_CHECK_SYSTEM

    def test_it_explains_the_drawing_it_is_looking_at(self) -> None:
        from app.ai.prompts import VISUAL_CHECK_SYSTEM

        assert "dashed" in VISUAL_CHECK_SYSTEM
        assert "not a photograph" in VISUAL_CHECK_SYSTEM

    def test_it_treats_the_request_as_data_not_instruction(self) -> None:
        from app.ai.prompts import VISUAL_CHECK_SYSTEM

        assert "data, not instruction" in VISUAL_CHECK_SYSTEM

    def test_the_schema_makes_it_describe_before_it_judges(self) -> None:
        """Field order is generation order under a constrained decoder."""
        fields = list(VisualCheck.model_fields)
        assert fields.index("describes") < fields.index("verdict")
