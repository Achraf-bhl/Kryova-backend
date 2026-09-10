"""A streamed tool call must survive, wherever in the stream it arrives.

**This is the defect that made the whole local-model path do nothing**, found by
driving the real GUI on the Windows seat, 2026-09-10.

`stream_chat` assembled its answer from the chunk carrying `done: true`, on the
documented belief that "tool calls arrive on the final object". Measured against
Ollama and `qwen3.5:9b`: a 102-chunk reply carried the tool call on chunk **101**
with `done: false`, and the `done` chunk that followed carried no `tool_calls` at
all. So every call was discarded, the agent saw a text-only turn, and the user
got a confident "I'll create the part..." with **zero steps run** -- on a request
as simple as a 40 mm cube.

Why nothing caught it:

* the non-streaming `chat` path is fine, because the whole body carries the
  calls, and that is the path every provider test drove;
* a mocked stream written to the old assumption passes against the old code;
* nothing goes red. The turn completes, the answer reads as deliberate, and only
  the verification footnote ("nothing measured this") hints that no work
  happened.

That is the shape of every defect this project has found between the model and
the tools, and it is why the ladder is driven through the browser.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ai.provider import Finished, TextDelta
from app.ai.providers.ollama import OllamaProvider

MODEL = "qwen3.5:9b"


class _Stream:
    """Stands in for `httpx.stream`, replaying a scripted NDJSON reply."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.entered = 0

    def __call__(self, method: str, url: str, **kwargs: Any) -> _Stream:
        self.entered += 1
        return self

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for chunk in self._chunks:
            yield json.dumps(chunk)


def _provider() -> OllamaProvider:
    return OllamaProvider("http://localhost:11434", MODEL, 60.0)


def _show(url: str, **kwargs: Any) -> httpx.Response:
    """`/api/show`, so `_context_window` resolves without a real Ollama."""
    return httpx.Response(
        200,
        request=httpx.Request("POST", url),
        json={"model_info": {"qwen3.context_length": 131_072}},
    )


CALL = {
    "id": "call_s1ewttw4",
    "function": {"index": 0, "name": "catia_new_part", "arguments": {"name": "Steel Cube"}},
}


def _reply(*, calls_on_done: bool) -> list[dict[str, Any]]:
    """The shape Ollama really sends, with the call placed either way."""
    text = [
        {"message": {"content": "I'll create "}, "done": False},
        {"message": {"content": "a steel cube."}, "done": False},
    ]
    carrier = {"message": {"content": "", "tool_calls": [CALL]}, "done": False}
    done: dict[str, Any] = {
        "message": {"content": ""},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 900,
        "eval_count": 40,
    }
    if calls_on_done:
        done["message"] = {"content": "", "tool_calls": [CALL]}
        return [*text, done]
    return [*text, carrier, done]


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", _show)


def _finished(chunks: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(httpx, "stream", _Stream(chunks))
    events = list(
        _provider().stream_chat(
            system="s", messages=[{"role": "user", "content": "cube"}], tools=[], max_tokens=512
        )
    )
    assert isinstance(events[-1], Finished)
    return events[-1].turn


class TestAToolCallSurvivesWhereverItArrives:
    def test_a_call_on_a_non_done_chunk_is_not_thrown_away(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The measured shape. This is what was broken."""
        turn = _finished(_reply(calls_on_done=False), monkeypatch)

        assert [c.name for c in turn.tool_calls] == ["catia_new_part"], (
            "the tool call arrived on the chunk before `done` and was discarded, "
            "which is what made every local-model turn run zero steps"
        )
        assert turn.tool_calls[0].arguments == {"name": "Steel Cube"}

    def test_a_call_on_the_done_chunk_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The old assumption is not forbidden, just no longer required -- a
        build that does put them on the last chunk must keep working."""
        turn = _finished(_reply(calls_on_done=True), monkeypatch)

        assert [c.name for c in turn.tool_calls] == ["catia_new_part"]

    def test_the_call_is_not_counted_twice_when_it_is_on_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A chunk carrying the call *and* a `done` chunk repeating it must not
        make the agent run the operation twice -- a doubled `catia_pad` is a
        part 2x too thick, and it would look like the model asked for it."""
        chunks = _reply(calls_on_done=False)
        chunks[-1]["message"] = {"content": "", "tool_calls": [CALL]}

        turn = _finished(chunks, monkeypatch)

        assert len(turn.tool_calls) == 1

    def test_the_narration_is_still_the_answer_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recovering the calls must not cost the deltas: the text the model
        wrote alongside them is still assembled from the stream."""
        turn = _finished(_reply(calls_on_done=False), monkeypatch)

        assert turn.text == "I'll create a steel cube."

    def test_the_deltas_are_still_yielded_for_display(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx, "stream", _Stream(_reply(calls_on_done=False)))
        events = list(
            _provider().stream_chat(
                system="s", messages=[{"role": "user", "content": "cube"}], tools=[], max_tokens=512
            )
        )

        assert [e.text for e in events if isinstance(e, TextDelta)] == [
            "I'll create ",
            "a steel cube.",
        ]
