"""The daemon's tool table must match the server's registry, byte for byte.

The daemon validates every call against its own shipped table rather than
against anything on the wire, and that is a security property worth keeping.
The cost of it used to be a hand-written second copy that drifted, and a drifted
daemon fails *closed* — it refuses a tool the server has started sending, which
reaches the user as a broken product rather than as a caught mistake.

So the table is generated, and this test is the thing that makes "generated"
true rather than aspirational: it regenerates in memory and fails if the
checked-in file differs. Run

    venv/bin/python scripts/gen_bridge_tools.py

after touching anything under `app/catia/ops/`.
"""

import subprocess
import sys
from pathlib import Path

from app.catia.ops import OPERATIONS
from app.catia.tool_specs import CATIA_TOOL_SPECS

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "scripts" / "catia_bridge" / "generated_tools.py"


def _bridge_table():
    """Import the daemon's table the way the daemon does — no server imports."""
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from catia_bridge import tool_table

    return tool_table


def test_the_checked_in_table_is_what_the_generator_produces():
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(REPO_ROOT / "scripts" / "gen_bridge_tools.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"{GENERATED.relative_to(REPO_ROOT)} is stale.\n"
        "Run: venv/bin/python scripts/gen_bridge_tools.py\n"
        f"{result.stderr}"
    )


def test_the_daemon_knows_every_tool_the_server_can_send():
    """Server vocabulary minus server-only == daemon vocabulary, exactly.

    Both directions matter. A tool the server can send and the daemon does not
    know is refused at the workstation; a tool the daemon accepts and the server
    never sends is dead surface that still has to be reviewed.
    """
    tool_table = _bridge_table()
    sendable = {op.name for op in OPERATIONS if not op.server_only}
    assert set(tool_table.TOOLS) == sendable


def test_tiers_agree_between_the_two_sides():
    """A tier mismatch is an authorisation hole, not a cosmetic difference."""
    tool_table = _bridge_table()
    for spec in CATIA_TOOL_SPECS:
        if spec.name in tool_table.SERVER_ONLY:
            continue
        assert tool_table.tier_of(spec.name) == spec.tier.value, (
            f"{spec.name}: server says {spec.tier.value}, daemon says "
            f"{tool_table.tier_of(spec.name)}. The daemon's answer is the one that "
            "enforces, so this is a real divergence in what needs approval."
        )


def test_every_model_facing_field_is_accepted_by_the_daemon():
    """Anything the model may send must survive the daemon's own schema.

    The failure this catches is the one that actually happened before: a field
    present on one side and absent on the other, so every live call came back
    'unknown field' while the tests — which drove only one side — passed.
    """
    tool_table = _bridge_table()
    for operation in OPERATIONS:
        if operation.server_only:
            continue
        daemon_schema = tool_table.TOOLS[operation.name][1]
        daemon_fields = set(daemon_schema["properties"])
        server_fields = set(operation.server_fields)
        for param in operation.params:
            if param.consumed_by_server:
                # The server resolves it into a server_field and never forwards
                # it, so the daemon must NOT expect it.
                assert param.name not in daemon_fields, (
                    f"{operation.name}.{param.name} is consumed by the server but the "
                    "daemon still expects it; every call would be refused."
                )
                continue
            assert param.name in daemon_fields or param.name in server_fields, (
                f"{operation.name}.{param.name} is model-facing but the daemon's "
                "schema has no such field, so the daemon would reject it as unknown."
            )
