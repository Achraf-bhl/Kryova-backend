"""Driving CATIA's own interface, for everything the typed operations miss.

Every other module in this package goes through COM and describes geometry.
These go through the window tree and describe the *interface*, which is a
different surface with a different failure mode: it keeps working while a modal
dialog has COM blocked, which is exactly when it is needed most.

This is the escape hatch, and it stays. However many operations the registry
grows, CATIA has ~100 workbenches and 900+ documented commands, and a typed
tool for each is neither achievable nor desirable — most are used once a year.
`catia_run_command` reaches all of them; the typed operations exist for the
ones used constantly, where a real schema beats a menu label.

The refusal list (`scripts/catia_bridge/ui_policy.py`) is applied here by the
daemon on its own terms, using its own table, and it refuses what no checkpoint
could undo: Macro, Tools>Options, Customize, Save As, Save Management, Exit,
Licence. That ceiling is deliberate. It also means literal parity with "every
command in CATIA" is not reachable through this path, which is a product
decision and not a defect to route around.
"""

from __future__ import annotations

from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    flag,
    one_of,
    optional,
    raw,
    required,
    text,
)

_WB = Workbench.INFRASTRUCTURE

#: What a dialog button does, independent of what this seat's language calls it.
BUTTON_ROLES = ("ok", "apply", "cancel", "close", "preview", "yes", "no")

#: Keys that can be sent to CATIA. Deliberately a small closed set: arbitrary
#: keystroke injection into a CAD session is a way to destroy a model with no
#: audit trail, and every key here is one a dialog legitimately needs.
KEYS = (
    "enter", "escape", "tab", "delete", "space",
    "up", "down", "left", "right", "home", "end",
)

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_list_commands",
        summary=(
            "List the commands on the live menus, with this seat's own labels.\n"
            "Use it when a command you need has no typed tool, to find what it is "
            "actually called on this installation — which may be French or German. "
            "Searching by an English word still works: the server maps it."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("search", text("Only report commands matching this word.", maximum=60)),
            optional("menu", text("Only report commands under this top-level menu.", maximum=60)),
        ),
    ),
    Operation(
        name="catia_run_command",
        summary=(
            "Press one command on CATIA's menus, and report the dialog it opens.\n"
            "The way to reach any of CATIA's hundreds of commands that has no typed "
            "tool. Name it in English; the server resolves it into this seat's own "
            "labels. Follow with catia_describe_dialog to see what it is asking for.\n"
            "Prefer a typed tool wherever one exists: it validates its arguments, it "
            "does not depend on menu wording, and it works the same on every language "
            "installation."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("command", text("The command's English name, e.g. 'Edge Fillet'.", maximum=120)),
        ),
        server_fields=("candidates", "command_name", "command_key", "menu_hint"),
    ),
    Operation(
        name="catia_describe_dialog",
        summary=(
            "Report the open dialog's title, fields and buttons, or that none is open.\n"
            "Always call this before catia_fill_dialog: field labels differ between "
            "releases and languages, and filling a field by a guessed name silently "
            "does nothing."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(),
    ),
    Operation(
        name="catia_fill_dialog",
        summary=(
            "Set fields in the open dialog, by the labels catia_describe_dialog reported.\n"
            "Values are strings because that is what a dialog field holds; write them "
            "as the dialog expects, including the unit if it shows one."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "fields",
                raw(
                    {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                                "value": {"type": "string", "maxLength": 200},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                        "description": "Each field to set, by its displayed label, and its new value.",
                    }
                ),
            ),
        ),
    ),
    Operation(
        name="catia_dialog_action",
        summary=(
            "Press a button in the open dialog, by what it does rather than what it "
            "says.\n"
            "'ok' commits and closes, 'apply' commits and stays open, 'cancel' discards. "
            "Give `button` with an exact label only when the dialog has a button that "
            "is none of these."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("action", one_of(BUTTON_ROLES, "What the button should do.")),
            optional("button", text("An exact button label, when the role is not enough.", maximum=120)),
        ),
        server_fields=("labels",),
    ),
    Operation(
        name="catia_press_key",
        summary=(
            "Send one keystroke to whatever CATIA is currently showing.\n"
            "Mostly for escaping a state nothing else can reach — a dialog with no "
            "recognised buttons, a command waiting for input. Escape is the safe one."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("key", one_of(KEYS, "Which key to press.")),),
    ),
    Operation(
        name="catia_switch_workbench",
        summary=(
            "Activate a workbench, so its commands become available.\n"
            "Most commands only exist in their own workbench: Pad needs Part Design, "
            "Extrude needs Generative Shape Design, a drawing view needs Drafting.\n"
            "The result reports the licence the workbench needs. A seat without that "
            "licence cannot open it however the menu looks, and that is worth telling "
            "the user plainly rather than retrying."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "workbench",
                text(
                    "The workbench's English name: 'Part Design', 'Generative Shape "
                    "Design', 'Aerospace Sheet Metal Design'.",
                    maximum=120,
                ),
            ),
        ),
        server_fields=("workbench_id", "workbench_name", "menu_path", "licence"),
    ),
    Operation(
        name="catia_view_control",
        summary=(
            "Change how the 3D view is displayed — fit, zoom, rotate to a standard "
            "viewpoint, or switch render mode.\n"
            "Affects only what is on screen, never the model, so it is always safe. "
            "Use it before catia_capture_view when the default framing hides what you "
            "want to show."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "action",
                one_of(
                    ("fit", "zoom_in", "zoom_out", "viewpoint", "render_mode", "hide", "show", "isolate"),
                    "What to change.",
                ),
            ),
            optional("viewpoint", text("Which standard viewpoint, for the viewpoint action.", maximum=40)),
            optional(
                "render_mode",
                one_of(
                    ("shaded", "shaded_with_edges", "wireframe", "hidden_line", "transparent"),
                    "Display mode, for the render_mode action.",
                ),
            ),
            optional("elements", raw({
                "type": "array",
                "maxItems": 50,
                "items": {"type": "string", "minLength": 1, "maxLength": 120},
                "description": "What to hide, show or isolate.",
            })),
        ),
    ),
    Operation(
        name="catia_graphic_properties",
        summary=(
            "Set colour, transparency, line weight or layer on features or components.\n"
            "Transparency in particular is a working tool, not decoration: making an "
            "outer housing 70% transparent is how the parts inside it become visible in "
            "a capture."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", raw({
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {"type": "string", "minLength": 1, "maxLength": 120},
                "description": "The features or components to restyle.",
            })),
            optional("colour", text("Colour name or #rrggbb hex.", maximum=40)),
            optional(
                "transparency",
                raw({
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Transparency percentage; 0 is opaque, 100 invisible.",
                }),
            ),
            optional("line_weight", raw({
                "type": "integer", "minimum": 1, "maximum": 63,
                "description": "Line thickness index.",
            })),
            optional("layer", text("Layer name or number.", maximum=40)),
            optional("show", flag("Show or hide the elements.")),
        ),
    ),
)
