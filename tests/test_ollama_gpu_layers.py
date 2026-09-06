"""Every layer on the GPU, because one layer off it costs half the speed.

Measured on the Windows seat, 2026-09-06. An 8 GB RTX 5070 Laptop shared with
CATIA, qwen3.5:9b, the full 32,768-token window:

    Ollama's own choice   33 of 34 layers   76% GPU    25.7 tok/s
    AI_GPU_LAYERS=all     34 of 34 layers   100% GPU   59.0 tok/s

Ollama's estimator holds a margin against a card it is sharing, and leaves a
layer behind. One layer on the CPU is not one thirty-fourth of the cost --
every token crosses the bus twice -- and a CATIA build is tens of turns, so
this is the difference between a product an engineer will use and a demo.

Two decisions here are worth pinning, because both were arrived at by
measurement after the obvious version was wrong:

* **`all` sends a sentinel, not a count.** Counting the layers from
  `/api/show` was tried first: `block_count` reports 32, so `block_count + 1`
  gave 33 -- and 33 leaves the model at 89% GPU and 48.6 tok/s. llama.cpp
  clamps `n_gpu_layers` to the real total, so asking for more than any model
  has means "all of it" and cannot go stale on the next model.
* **It is off by default.** Forcing more layers than a card can hold makes the
  load fail. A machine nobody has measured is better served by Ollama's guess,
  and this is opt-in for the machine that has been.

Offline: the provider is asked what it would send, with no Ollama running.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai.providers.ollama import EVERY_LAYER, GPU_LAYERS_ALL, OllamaProvider

SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "ai" / "providers" / "ollama.py"
).read_text(encoding="utf-8")


def provider(monkeypatch: pytest.MonkeyPatch, configured: str | None) -> OllamaProvider:
    """A provider that will not reach the network, with the setting applied."""
    from app.core import config

    monkeypatch.setattr(config.settings, "ai_gpu_layers", configured or "", raising=False)
    monkeypatch.delenv("AI_GPU_LAYERS", raising=False)
    return OllamaProvider(
        base_url="http://127.0.0.1:11434", model="qwen3.5:9b", timeout_seconds=5.0
    )


class TestWhatItSends:
    def test_all_means_more_layers_than_any_model_has(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert provider(monkeypatch, GPU_LAYERS_ALL)._gpu_layers() == EVERY_LAYER

    def test_the_sentinel_is_larger_than_a_real_model(self) -> None:
        """The whole argument for it. The largest open model in use is well
        under a hundred layers; this is comfortably past that and llama.cpp
        clamps, so it stays right as models grow."""
        assert EVERY_LAYER > 100

    def test_a_number_is_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A machine with a smaller card can pin an exact split."""
        assert provider(monkeypatch, "20")._gpu_layers() == 20

    def test_unset_leaves_the_decision_to_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default, and the right one for a machine nobody has measured:
        asking for more layers than the card holds fails the load."""
        assert provider(monkeypatch, None)._gpu_layers() is None

    def test_a_typo_is_ignored_rather_than_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A mistyped environment variable must not take the assistant down."""
        assert provider(monkeypatch, "lots")._gpu_layers() is None

    def test_the_environment_is_read_when_settings_has_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`Settings` is configured with extra="ignore", so a variable it does
        not declare is dropped silently. The same trap `AI_MAX_STEPS` has, and
        the same workaround."""
        from app.core import config

        monkeypatch.setattr(config.settings, "ai_gpu_layers", "", raising=False)
        monkeypatch.setenv("AI_GPU_LAYERS", GPU_LAYERS_ALL)
        instance = OllamaProvider(
            base_url="http://127.0.0.1:11434", model="qwen3.5:9b", timeout_seconds=5.0
        )
        assert instance._gpu_layers() == EVERY_LAYER

    def test_it_is_resolved_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every request would otherwise re-read the setting; the value cannot
        change under a running process, and the log line would repeat per call."""
        instance = provider(monkeypatch, GPU_LAYERS_ALL)
        assert instance._gpu_layers() == EVERY_LAYER
        monkeypatch.setattr(instance, "_num_gpu", 7)
        assert instance._gpu_layers() == 7


class TestWhereItGoes:
    def test_the_option_is_added_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = provider(monkeypatch, GPU_LAYERS_ALL)._with_gpu_layers({"num_ctx": 32768})
        assert options == {"num_ctx": 32768, "num_gpu": EVERY_LAYER}

    def test_nothing_is_added_when_it_is_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unset knob must send no option at all, not `num_gpu: null`, which
        Ollama would have to interpret."""
        options = provider(monkeypatch, None)._with_gpu_layers({"num_ctx": 32768})
        assert options == {"num_ctx": 32768}

    @pytest.mark.parametrize("method", ["chat", "complete", "look"])
    def test_every_request_carries_it(self, method: str) -> None:
        """Three call sites build their own options. A tool call on the GPU and
        a vision call on the CPU would be a confusing pair of measurements."""
        tree = ast.parse(SOURCE)
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == method
        )
        body = ast.get_source_segment(SOURCE, node) or ""
        assert "_with_gpu_layers(" in body, f"{method}() does not pass num_gpu"


class TestItIsWrittenDown:
    def test_the_measurement_is_beside_the_constant(self) -> None:
        """A bare `num_gpu = 999` reads as superstition and gets removed. The
        numbers are why it stays."""
        assert "tok/s" in SOURCE
        assert "100% GPU" in SOURCE

    def test_the_setting_exists_on_settings(self) -> None:
        from app.core.config import Settings

        assert "ai_gpu_layers" in Settings.model_fields


class TestTheContextWindowIsUnchanged:
    """The window was never the lever, and shrinking it was the wrong answer.

    Measured the same day: qwen3.5:9b fits the card entirely at num_ctx=8192,
    and a real CATIA turn's prompt is larger than that -- so a full-GPU run at
    8k would die partway through on the loud truncation refusal, which is the
    one failure mode `_context_window` exists to produce rather than hide.
    Forcing the layers keeps the full window AND the whole model on the card.
    """

    def test_the_ceiling_is_still_the_full_window(self) -> None:
        from app.ai.providers.ollama import MAX_CONTEXT_WINDOW

        assert MAX_CONTEXT_WINDOW == 32_768

    def test_the_floor_did_not_move_either(self) -> None:
        from app.ai.providers.ollama import MIN_CONTEXT_WINDOW

        assert MIN_CONTEXT_WINDOW == 8_192

    def test_the_out_of_memory_retry_still_exists(self) -> None:
        """Forcing layers makes an out-of-memory load more likely on a machine
        that was not measured, so the path that catches it matters more now,
        not less."""
        assert "_retry_with_smaller_window" in SOURCE
