"""ROAD_TO_10 9.7: MCP as a curated channel.

The set is data, so the tests hold it to the three claims its docstring makes -- every name is a
real tool, the size stays between 20 and 40, and nothing withheld has slipped in -- and the
instructions to what an outside caller needs to be told. They need no database: the tool box is
built with stand-ins and only its vocabulary is read.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.ai import mcp, mcp_tools
from app.ai.tools import ToolBox
from app.core.config import Settings
from app.verify.standards import NOT_VALIDATED


@pytest.fixture(scope="module")
def vocabulary() -> dict[str, bool]:
    """Every tool the agent's box holds -> whether it changes state."""
    box = ToolBox(db=MagicMock(), user=MagicMock())
    return {tool.name: tool.mutating for tool in box.every_tool()}


class TestTheSetIsHonest:
    def test_every_curated_name_is_a_real_tool(self, vocabulary: dict[str, bool]) -> None:
        assert [name for name in mcp_tools.CURATED if name not in vocabulary] == []

    def test_every_withheld_name_is_a_real_tool(self, vocabulary: dict[str, bool]) -> None:
        # A withheld name that no longer exists is a guard watching nothing.
        assert [name for name in mcp_tools.WITHHELD if name not in vocabulary] == []

    def test_it_is_between_twenty_and_forty_with_no_duplicates(self) -> None:
        assert mcp_tools.MIN_CURATED <= len(mcp_tools.CURATED) <= mcp_tools.MAX_CURATED
        assert len(set(mcp_tools.CURATED)) == len(mcp_tools.CURATED)

    def test_nothing_withheld_is_in_it(self) -> None:
        assert set(mcp_tools.CURATED) & set(mcp_tools.WITHHELD) == set()

    def test_it_is_a_small_fraction_of_the_registry(self, vocabulary: dict[str, bool]) -> None:
        assert len(mcp_tools.CURATED) * 4 < len(vocabulary)

    def test_it_can_build_a_part_and_read_a_result_end_to_end(self) -> None:
        # The path a client needs: make a part, pad it, measure it, solve it, read it back.
        needed = {
            "catia_new_part",
            "catia_sketch_create",
            "catia_pad",
            "catia_measure",
            "run_simulation",
            "get_simulation",
            "list_materials",
        }
        assert needed <= set(mcp_tools.CURATED)

    def test_no_destructive_or_ui_driving_tool_is_in_it(self) -> None:
        for name in mcp_tools.CURATED:
            assert not name.startswith("delete_"), name
            assert name not in {"catia_run_command", "catia_press_key", "catia_dialog_action"}


class TestWhatTheClientIsTold:
    def test_the_instructions_state_units_consent_and_the_honesty_of_a_number(self) -> None:
        text = mcp_tools.INSTRUCTIONS
        assert "millimetres, newtons and megapascals" in text
        assert mcp.META_ALLOW_MUTATIONS in text
        assert "single-grid" in text and "grids: 3" in text
        assert "UNMEASURED" in text

    def test_the_scope_statement_is_the_one_server_string_not_a_reworded_copy(self) -> None:
        # CLAUDE.md: `NOT_VALIDATED` is one string; a statement with two wordings has two
        # standards.
        assert NOT_VALIDATED in mcp_tools.INSTRUCTIONS

    def test_initialize_hands_them_over(self) -> None:
        assert mcp.INSTRUCTIONS == mcp_tools.INSTRUCTIONS

    def test_the_consent_rule_names_the_meta_key_the_protocol_reads(self) -> None:
        assert mcp.META_ALLOW_MUTATIONS == "kryova/allowMutations"


class TestTheSettingIsValidated:
    def _build(self, value: str) -> Settings:
        from typing import Any, cast

        factory = cast(Any, Settings)
        return factory(
            _env_file=None,
            database_url="postgresql://user:pw@example.neon.tech/db",
            secret_key="x" * 48,
            mcp_tool_set=value,
        )

    def test_the_default_is_curated(self) -> None:
        assert self._build("curated").mcp_tool_set == "curated"

    def test_full_is_accepted_in_any_case(self) -> None:
        assert self._build(" FULL ").mcp_tool_set == "full"

    def test_a_typo_fails_at_startup_not_as_a_surface_nobody_chose(self) -> None:
        with pytest.raises(ValueError, match="MCP_TOOL_SET"):
            self._build("curtated")

    def test_the_known_sets_are_the_two_the_validator_accepts(self) -> None:
        assert mcp_tools.TOOL_SETS == ("curated", "full")
