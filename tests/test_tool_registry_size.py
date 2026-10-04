"""The tool registry is resent on every agent step, so it may not quietly grow back.

ROAD_TO_10 1.6. 245 schemas are about 67k estimated tokens (`scripts/schema_report.py`), and on
a hosted model that is billed again at every step of every turn -- a tool added casually is a
cost on every conversation, forever, that no test would otherwise notice. The caps below are the
measured sizes, rounded up, so that adding to the registry is a decision somebody makes and
writes down, not a side effect.

**These tests do not say the registry is small.** They say it has not grown. Shrinking it needs
a live accuracy comparison (a shorter description is only a saving if the model still calls the
tool correctly), which is the ladder's job (ROAD_TO_10 1.5), and not something to do blind: the
longest descriptions here carry measured guidance -- "`all` on a flange rounded the bore and all
four hole rims too" -- that exists because the short version cost a part.

Offline: the registry is read with no database.
"""

from __future__ import annotations

import json

import httpx

from scripts import schema_report

#: History of the caps, so a raise is visible next to the reason for it:
#:   239,100 / 33,000  the first measurement (243 tools).
#:   239,600 / 33,500  +`recall_earlier_result` (495 B): a digested tool result in the replay
#:                     points at it, and a digest with no way back would be a summary.
#:   240,500 / 33,500  +`build_design` (835 B, ~230 tokens a step, billed at the cache price): it
#:                     runs a whole recorded design in one step, so a twenty-feature part is one
#:                     model step and one transcript resend instead of twenty. Mutating, so the
#:                     read-only cap is unmoved.
#:
#: Measured 2026-10-04, compact JSON. To raise one: run `venv/bin/python -m scripts.schema_report`,
#: set the new figure here rounded up to the next 100, and say in the commit what the new tool
#: buys that is worth its share of every step's bill.
TOTAL_BYTES_ALL_TOOLS = 240_500
TOTAL_BYTES_READ_ONLY = 33_500
LARGEST_SINGLE_TOOL_BYTES = 3_200

HOW_TO_RAISE = (
    "Every byte here is sent again on every agent step. If the growth is intended, run "
    "`venv/bin/python -m scripts.schema_report`, raise the cap in tests/test_tool_registry_size.py "
    "to the new figure rounded up to the next 100, and justify it in the commit."
)


class TestTheRegistryHasNotGrown:
    def test_all_tools_together(self) -> None:
        total = schema_report.total_bytes(schema_report.measure(include_mutating=True))
        assert total <= TOTAL_BYTES_ALL_TOOLS, (
            f"The full registry is {total:,} bytes, over the {TOTAL_BYTES_ALL_TOOLS:,} cap "
            f"(~{schema_report.estimated_tokens(total):,} tokens per step). {HOW_TO_RAISE}"
        )

    def test_the_read_only_set_that_every_first_turn_sends(self) -> None:
        total = schema_report.total_bytes(schema_report.measure(include_mutating=False))
        assert total <= TOTAL_BYTES_READ_ONLY, (
            f"The read-only registry is {total:,} bytes, over the {TOTAL_BYTES_READ_ONLY:,} cap. "
            + HOW_TO_RAISE
        )

    def test_no_single_tool_balloons(self) -> None:
        """A total cap alone lets one tool absorb the headroom the others left."""
        worst = schema_report.measure(include_mutating=True)[0]
        assert worst.bytes <= LARGEST_SINGLE_TOOL_BYTES, (
            f"{worst.name} is {worst.bytes:,} bytes (prose {worst.description_bytes:,}, "
            f"parameters {worst.parameter_bytes:,}), over the {LARGEST_SINGLE_TOOL_BYTES:,} cap "
            "for one tool. Long guidance belongs in the knowledge base the agent can look up "
            "(app/catia_kb/), not in a schema resent on every step. " + HOW_TO_RAISE
        )

    def test_the_read_only_set_is_a_subset_of_the_whole(self) -> None:
        whole = {size.name for size in schema_report.measure(include_mutating=True)}
        read_only = {size.name for size in schema_report.measure(include_mutating=False)}
        assert read_only <= whole and len(read_only) < len(whole)


class TestTheReportCountsWhatIsSent:
    def test_compact_json_is_the_number_of_bytes_the_http_client_puts_on_the_wire(self) -> None:
        """The report's unit is "bytes as they travel". Check it against the client that
        sends them rather than against our own idea of compact."""
        from typing import Any, cast

        from app.ai.tools import ToolBox

        schemas = ToolBox(db=cast(Any, None), user=cast(Any, None)).schemas(include_mutating=True)
        sent = httpx.Request("POST", "https://example.invalid/", json=schemas).content
        assert len(sent) == len(schema_report.compact(schemas).encode())

    def test_padded_json_would_read_larger_which_is_why_the_unit_is_stated(self) -> None:
        from typing import Any, cast

        from app.ai.tools import ToolBox

        schemas = ToolBox(db=cast(Any, None), user=cast(Any, None)).schemas(include_mutating=True)
        assert len(json.dumps(schemas)) > len(json.dumps(schemas, separators=(",", ":")))

    def test_the_sizes_add_up_to_their_parts(self) -> None:
        for size in schema_report.measure(include_mutating=False)[:5]:
            # The tool's envelope (type, name, braces) is the remainder: small and positive.
            envelope = size.bytes - size.description_bytes - size.parameter_bytes
            assert 0 < envelope < 200, size

    def test_the_token_figure_is_labelled_an_estimate(self) -> None:
        assert "estimated" in schema_report.report(3)
