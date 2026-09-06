"""Every published command id must name an entry that exists, and be reachable.

`COMMAND_IDS` maps a reference *entry key* to the language-independent id
`Application.StartCommand` accepts. `resolve_command` looks it up by the key of
the entry a name resolved to, so a key naming no entry is not a small
inaccuracy -- it is an id that can never be sent, in a table whose whole purpose
is to be the one safe thing to send.

That is what shipped. Both original keys were `infrastructure.*`, and there is
no `infrastructure` module in `app/catia_kb/commands/` -- the entries were never
written. Every lookup missed, `command_ids` came back empty for every phrasing,
and the `StartCommand` fallback in `catia_com.run_command` was dead code that
read as live. It was found only by checking by hand while writing
`test_unknown_command_box.py`, which is not a mechanism.

These two tests are the mechanism. The first says the table is *consistent*
(every key is real); the second says it is *reachable* (some phrasing an agent
would actually use gets the id all the way through `_enrich` to the wire).
Consistency alone would have passed on a correct key for a command no user can
name, which is the same dead code with better spelling.
"""

from __future__ import annotations

from app.catia.dispatch import _enrich
from app.catia.tool_specs import get_spec
from app.catia_kb.registry import registry
from app.catia_kb.ui import COMMAND_IDS, resolve_command


def test_every_key_names_an_entry_that_exists() -> None:
    """The check that was missing. Breaking it: point a key at a name nobody
    wrote and this fails, where before it failed silently on the seat."""
    index = registry()
    missing = [key for key in COMMAND_IDS if index.get(key) is None]
    assert not missing, (
        f"COMMAND_IDS keys naming no reference entry: {missing}. "
        "resolve_command looks the id up by the resolved entry's key, so these "
        "can never be sent -- write the entry, or drop the id."
    )


def test_every_id_is_reachable_from_something_a_user_would_say() -> None:
    """A consistent key is not enough; it has to resolve from real words.

    Each id is exercised through its entry's own display name, which is the
    minimum any phrasing must manage, and asserted to arrive at the far end of
    `_enrich` -- the same payload the daemon receives.
    """
    spec = get_spec("catia_run_command")
    assert spec is not None
    index = registry()

    for key, command_id in COMMAND_IDS.items():
        entry = index.get(key)
        assert entry is not None, key

        target = resolve_command(entry.name)
        assert target.key == key, (
            f"{entry.name!r} resolves to {target.key!r}, not {key!r}; the id "
            f"{command_id!r} is unreachable from its own entry's name."
        )
        assert command_id in target.candidates, target

        built = _enrich(
            None,  # type: ignore[arg-type]
            spec=spec,
            document=None,
            arguments={"command": entry.name},
            language="fr",
        )
        assert command_id in built["command_ids"], built


def test_the_id_is_still_only_ever_a_published_one() -> None:
    """The safety invariant this table exists to hold, restated here because
    making the path live is exactly when it starts to matter.

    A display label reaching `StartCommand` is what raised the modal that killed
    two seat sessions. Making `COMMAND_IDS` reachable must not widen what may be
    sent -- only the values in the table may appear.
    """
    spec = get_spec("catia_run_command")
    assert spec is not None
    published = set(COMMAND_IDS.values())

    for command in ["New Window", "Fastener Pattern", "Close Sketch", "Edge Fillet", "Pad"]:
        built = _enrich(
            None,  # type: ignore[arg-type]
            spec=spec,
            document=None,
            arguments={"command": command},
            language="fr",
        )
        assert set(built["command_ids"]) <= published, built
