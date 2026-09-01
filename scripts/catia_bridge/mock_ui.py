"""A CATIA interface that exists only in memory, so Linux can test the real loop.

`ui_automation.py` can only run on Windows beside a live CATIA. Without a stand-in
for it, every interactive tool -- the command runner, the dialog reader, the
dialog filler, the button presser -- would be code that is first executed in
front of a user, on a workstation, against their part. This is that stand-in.

It is a *simulator*, not a stub. It has a menu tree with real CATIA menu paths,
commands that are greyed out until their preconditions are met, dialogs with
fields and defaults, buttons that carry roles, and an OK that actually commits
the operation to the mock part. The tools therefore run the same sequence on
Linux that they will run on Windows -- press the command, read the dialog it
opened, fill a field, press OK, get a feature -- and the server-side code, the
schemas, the tier table and the agent prompts are all exercised for real.

**It runs in a language.** `MockUi(language="de")` labels its menus and buttons
in German, because "works in any interface language" is the requirement most
easily broken by an English-only test. A test that drives the German mock and
expects a Pad is a test that would have caught a hardcoded `"OK"`.

What it deliberately does not simulate: the Win32 layer itself. Whether CATIA's
dialogs really answer `WM_GETTEXT`, whether `EN_CHANGE` is really needed, what
its window classes really are -- none of that can be known from Linux, and
pretending otherwise here would turn an honest unknown into a passing test.
Those are listed in the setup doc as the things a Windows session verifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .ui_automation import Control, Dialog, MenuItem, UiUnavailable

#: Labels this mock knows how to say in more than English. Small on purpose:
#: enough to prove that nothing in the tool layer assumes English, not an
#: attempt to be a translation table (that lives in `app/catia_kb/languages.py`,
#: on the server side, which is where it belongs).
_LABELS: dict[str, dict[str, str]] = {
    "en": {},
    "fr": {
        "Start": "Démarrer",
        "File": "Fichier",
        "Edit": "Edition",
        "View": "Affichage",
        "Insert": "Insertion",
        "Tools": "Outils",
        "Window": "Fenêtre",
        "Help": "Aide",
        "Sketch-Based Features": "Composants issus d'un contour",
        "Dress-Up Features": "Composants d'habillage",
        "Mechanical Design": "Conception mécanique",
        "Part Design": "Conception de pièces",
        "Assembly Design": "Conception d'assemblage",
        "Generative Shape Design": "Generative Shape Design",
        "Sketch": "Esquisse",
        "Pad": "Extrusion",
        "Pocket": "Poche",
        "Shaft": "Révolution",
        "Edge Fillet": "Congé d'arête",
        "Chamfer": "Chanfrein",
        "Draft Angle": "Dépouille",
        "Update": "Mettre à jour",
        "Fit All In": "Tout tenir dans",
        "OK": "OK",
        "Cancel": "Annuler",
        "Apply": "Appliquer",
        "Preview": "Aperçu",
        "Length": "Longueur",
        "Depth": "Profondeur",
        "Radius": "Rayon",
        "Type": "Type",
        "Profile": "Profil",
        "Selection": "Sélection",
        "Reverse Direction": "Inverser la direction",
        "Mirrored extent": "Extension miroir",
        "Dimension": "Cote",
        "Up to last": "Jusqu'au dernier",
        "Definition": "Définition",
    },
    "de": {
        "Start": "Start",
        "File": "Datei",
        "Edit": "Bearbeiten",
        "View": "Ansicht",
        "Insert": "Einfügen",
        "Tools": "Extras",
        "Window": "Fenster",
        "Help": "Hilfe",
        "Sketch-Based Features": "Skizzierte Elemente",
        "Dress-Up Features": "Ausformungselemente",
        "Mechanical Design": "Mechanische Konstruktion",
        "Part Design": "Teilekonstruktion",
        "Assembly Design": "Baugruppenkonstruktion",
        "Generative Shape Design": "Generative Shape Design",
        "Sketch": "Skizze",
        "Pad": "Block",
        "Pocket": "Tasche",
        "Shaft": "Welle",
        "Edge Fillet": "Kantenverrundung",
        "Chamfer": "Fase",
        "Draft Angle": "Formschräge",
        "Update": "Aktualisieren",
        "Fit All In": "Alles einpassen",
        "OK": "OK",
        "Cancel": "Abbrechen",
        "Apply": "Anwenden",
        "Preview": "Vorschau",
        "Length": "Länge",
        "Depth": "Tiefe",
        "Radius": "Radius",
        "Type": "Typ",
        "Profile": "Profil",
        "Selection": "Auswahl",
        "Reverse Direction": "Richtung umkehren",
        "Mirrored extent": "Gespiegelte Länge",
        "Dimension": "Bemaßung",
        "Up to last": "Bis Letztes",
        "Definition": "Definition",
    },
}


@dataclass(slots=True)
class _Field:
    label: str
    kind: str = "text"
    value: str = ""
    options: tuple[str, ...] = ()
    checked: bool | None = None
    enabled: bool = True


@dataclass(slots=True)
class _Dialog:
    """A modelled dialog: what it is called, what it asks, what OK does."""

    command: str
    title: str
    fields: list[_Field]
    buttons: tuple[str, ...] = ("OK", "Cancel")
    #: Called with `{field label in English: value}` when OK is pressed. The
    #: return value becomes part of the tool result, so a mock Pad really does
    #: appear in the mock part's feature tree.
    commit: Callable[[dict[str, str]], dict[str, Any]] | None = None
    #: A command the part must already have for this dialog to be reachable.
    needs_solid: bool = False


#: One menu entry: English label, then either a command name or a submenu.
_MENU: tuple[tuple[str, tuple[Any, ...]], ...] = (
    (
        "Start",
        (
            ("Mechanical Design", (("Part Design", ()), ("Assembly Design", ()))),
            ("Shape", (("Generative Shape Design", ()),)),
        ),
    ),
    ("File", (("New...", ()), ("Open...", ()), ("Save", ()), ("Save As...", ()), ("Exit", ()))),
    ("Edit", (("Undo", ()), ("Redo", ()), ("Delete", ()), ("Properties", ()))),
    ("View", (("Fit All In", ()), ("Normal View", ()), ("Isometric View", ()))),
    (
        "Insert",
        (
            ("Sketch", ()),
            ("Sketch-Based Features", (("Pad", ()), ("Pocket", ()), ("Shaft", ()))),
            ("Dress-Up Features", (("Edge Fillet", ()), ("Chamfer", ()), ("Draft Angle", ()))),
        ),
    ),
    ("Tools", (("Options...", ()), ("Customize...", ()), ("Macro", (("Macros...", ()),)))),
    ("Window", (("Tile Horizontally", ()),)),
    ("Help", (("CATIA V5 Help", ()), ("About CATIA V5", ()))),
)


class MockUi:
    """An in-memory CATIA interface: a menu, a dialog stack, and a language."""

    def __init__(self, language: str = "en") -> None:
        self.language = language if language in _LABELS else "en"
        self._dialog: _Dialog | None = None
        self._menu_ids: dict[int, tuple[str, ...]] = {}
        #: Set by the owner (`MockCatia`) so greying can depend on the model.
        self.has_document: Callable[[], bool] = lambda: True
        self.has_solid: Callable[[], bool] = lambda: True
        self.commits: dict[str, Callable[[dict[str, str]], dict[str, Any]]] = {}
        self.workbench = "Part Design"
        self.last_command = ""

    # -- language ------------------------------------------------------------

    def say(self, english: str) -> str:
        """`english` as this seat displays it."""
        return _LABELS[self.language].get(english, english)

    def _english(self, shown: str) -> str:
        """Back from a displayed label to the English name, for the commit map."""
        table = _LABELS[self.language]
        folded = shown.strip().casefold()
        for english, translated in table.items():
            if translated.casefold() == folded:
                return english
        return shown.strip()

    # -- menu ----------------------------------------------------------------

    def read_menu(self) -> list[MenuItem]:
        self._menu_ids = {}
        counter = [1000]
        return [self._menu_node(label, children, (), counter) for label, children in _MENU]

    def _menu_node(
        self, english: str, children: tuple[Any, ...], parent: tuple[str, ...], counter: list[int]
    ) -> MenuItem:
        label = self.say(english.rstrip(".").strip()) if english.endswith("...") else self.say(english)
        if english.endswith("..."):
            label = f"{label}..."
        path = (*parent, label)
        if children:
            return MenuItem(
                label=label,
                path=path,
                command_id=0,
                enabled=True,
                children=[
                    self._menu_node(child, grandchildren, path, counter)
                    for child, grandchildren in children
                ],
            )
        counter[0] += 1
        self._menu_ids[counter[0]] = (english,)
        return MenuItem(
            label=label,
            path=path,
            command_id=counter[0],
            enabled=self._enabled(english),
            children=[],
        )

    def _enabled(self, english: str) -> bool:
        if english in {"Pad", "Pocket", "Shaft"}:
            return self.has_document()
        if english in {"Edge Fillet", "Chamfer", "Draft Angle"}:
            return self.has_solid()
        return True

    def invoke_menu(self, command_id: int) -> str:
        names = self._menu_ids.get(command_id)
        if names is None:
            raise UiUnavailable("That menu item is not on this menu.")
        return names[0]

    # -- commands ------------------------------------------------------------

    #: Commands that open a dialog rather than acting at once.
    _DIALOG_COMMANDS = {"Pad", "Pocket", "Shaft", "Edge Fillet", "Chamfer", "Draft Angle"}

    def start_command(self, label: str) -> bool:
        """Run a command by the label this seat shows. False if unrecognised.

        Returning False rather than raising is the honest model of
        `StartCommand`, which accepts any string and silently does nothing with
        the ones it does not know. That silence is the failure the tool layer
        has to detect, so the mock reproduces it rather than helpfully raising.
        """
        english = self._english(label.rstrip(".").strip())
        known = {
            item
            for top, children in _MENU
            for item in _flatten(top, children)
        }
        if english not in known:
            return False
        self.last_command = english
        if english in self._DIALOG_COMMANDS:
            if english in {"Edge Fillet", "Chamfer", "Draft Angle"} and not self.has_solid():
                raise UiUnavailable(
                    f"{self.say(english)} needs a solid to work on, and this part has none yet."
                )
            self._dialog = self._build_dialog(english)
        return True

    def _build_dialog(self, english: str) -> _Dialog:
        match english:
            case "Pad":
                fields = [
                    _Field(
                        "Type",
                        "choice",
                        self.say("Dimension"),
                        (self.say("Dimension"), self.say("Up to last")),
                    ),
                    _Field("Length", "text", "10mm"),
                    _Field("Profile", "text", ""),
                    _Field("Mirrored extent", "checkbox", checked=False),
                    _Field("Reverse Direction", "checkbox", checked=False),
                ]
            case "Pocket":
                fields = [
                    _Field("Type", "choice", self.say("Dimension"), (self.say("Dimension"),)),
                    _Field("Depth", "text", "10mm"),
                    _Field("Profile", "text", ""),
                ]
            case "Shaft":
                fields = [_Field("Angle", "text", "360deg"), _Field("Profile", "text", "")]
            case "Edge Fillet":
                fields = [_Field("Radius", "text", "5mm"), _Field("Selection", "text", "")]
            case "Chamfer":
                fields = [_Field("Length", "text", "2mm"), _Field("Selection", "text", "")]
            case _:
                fields = [_Field("Angle", "text", "5deg"), _Field("Selection", "text", "")]
        return _Dialog(
            command=english,
            title=f"{self.say(english)} {self.say('Definition')}",
            fields=fields,
            buttons=("OK", "Cancel", "Preview"),
            commit=self.commits.get(english),
        )

    # -- dialogs -------------------------------------------------------------

    @property
    def dialog_open(self) -> bool:
        return self._dialog is not None

    def active_dialog(self) -> Dialog | None:
        if self._dialog is None:
            return None
        controls: list[Control] = []
        for index, item in enumerate(self._dialog.fields):
            controls.append(
                Control(
                    kind=item.kind,
                    label=self.say(item.label),
                    value=item.value,
                    enabled=item.enabled,
                    control_id=100 + index,
                    handle=100 + index,
                    options=item.options,
                    checked=item.checked,
                    window_class="Edit" if item.kind == "text" else "Button",
                )
            )
        for index, button in enumerate(self._dialog.buttons):
            controls.append(
                Control(
                    kind="button",
                    label=self.say(button),
                    control_id=1 if button == "OK" else 2 if button == "Cancel" else 200 + index,
                    handle=200 + index,
                    window_class="Button",
                )
            )
        return Dialog(
            title=self._dialog.title, handle=42, controls=tuple(controls), window_class="#32770"
        )

    def _field(self, name: str) -> _Field:
        assert self._dialog is not None  # noqa: S101 - callers check dialog_open
        wanted = name.strip().casefold()
        for item in self._dialog.fields:
            if item.label.casefold() == wanted or self.say(item.label).casefold() == wanted:
                return item
        offered = ", ".join(self.say(f.label) for f in self._dialog.fields)
        raise UiUnavailable(
            f"{name!r} is not a field of the {self._dialog.title!r} dialog. It has: {offered}."
        )

    def fill(self, name: str, value: str) -> str:
        item = self._field(name)
        if not item.enabled:
            raise UiUnavailable(f"{self.say(item.label)} is greyed out in this dialog.")
        if item.kind == "choice":
            if value.strip().casefold() not in {o.casefold() for o in item.options}:
                raise UiUnavailable(
                    f"{value!r} is not an option for {self.say(item.label)}. "
                    f"It offers: {', '.join(item.options)}."
                )
            item.value = value.strip()
        elif item.kind == "checkbox":
            item.checked = value.strip().casefold() in {"true", "1", "yes", "on", "checked"}
            item.value = "checked" if item.checked else ""
        else:
            item.value = value.strip()
        return self.say(item.label)

    def press(self, label_or_role: str) -> tuple[str, dict[str, Any]]:
        """Press a button. Returns `(role, whatever committing produced)`."""
        if self._dialog is None:
            raise UiUnavailable("No CATIA dialog is open, so there is no button to press.")
        wanted = label_or_role.strip().casefold()
        for button in self._dialog.buttons:
            if wanted in {button.casefold(), self.say(button).casefold()}:
                return self._act(button)
        offered = ", ".join(self.say(b) for b in self._dialog.buttons)
        raise UiUnavailable(
            f"The {self._dialog.title!r} dialog has no {label_or_role!r} button. It has: {offered}."
        )

    def _act(self, button: str) -> tuple[str, dict[str, Any]]:
        assert self._dialog is not None  # noqa: S101 - checked by the caller
        dialog = self._dialog
        if button == "Preview":
            return "preview", {"previewed": dialog.command}
        if button == "Cancel":
            self._dialog = None
            return "cancel", {"cancelled": dialog.command}
        values = {item.label: (item.value or "") for item in dialog.fields}
        for item in dialog.fields:
            if item.kind == "checkbox":
                values[item.label] = "true" if item.checked else "false"
        self._dialog = None
        if dialog.commit is None:
            return "ok", {"command": dialog.command}
        return "ok", dialog.commit(values)

    def press_key(self, key: str) -> tuple[str, dict[str, Any]]:
        """Enter confirms and Escape abandons, exactly as they do in CATIA."""
        lowered = key.strip().lower()
        if self._dialog is None:
            return "none", {}
        if lowered == "enter":
            return self._act("OK")
        if lowered == "escape":
            return self._act("Cancel")
        return "none", {}


def _flatten(label: str, children: tuple[Any, ...]) -> list[str]:
    if not children:
        return [label.rstrip(".").strip()]
    out: list[str] = []
    for child, grandchildren in children:
        out.extend(_flatten(child, grandchildren))
    return out
