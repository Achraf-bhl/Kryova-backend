"""Reading and driving CATIA's window tree, without touching the mouse.

CATIA's COM automation surface covers geometry. It does not cover the interface:
there is no `Dialog` object, no way to ask what dialog is open, and no way to
press its OK button. Everything an engineer does through a dialog -- and that is
most of CATIA -- is unreachable from `pywin32`.

This module reaches it the other way, through Win32 itself. CATIA V5's menu bar
is a native Win32 menu and its dialogs are native windows, so `GetMenu` reads
the real menu of the real seat and `EnumChildWindows` reads the real dialog. Two
properties follow, and both are the reason this exists:

**It works in any language.** Nothing here has a table of English labels to
match. It reads what this installation actually shows -- `Kantenverrundung`,
`Congé d'arête`, a Japanese seat's kanji -- and hands the strings up. The
reference package translates *intent* into a label to look for; this reports
what is really there, which is the only thing that is correct on a seat whose
language nobody anticipated.

**It works while COM is blocked.** A modal dialog stops CATIA's automation
server answering: that is the daemon's "CATIA is not responding, dismiss the
dialog" error, and until now the only cure was a human hand. Window messages are
not COM and are delivered by the dialog's own message loop, so the tools built
on this module are exactly the ones that keep working when the rest have wedged.
`session.py` routes them past the COM health probe for that reason.

**Nothing here touches the pointer or the foreground.** No `SetCursorPos`, no
`SendInput`, no `SetForegroundWindow`, no synthetic mouse. Every action is a
message posted to a specific window, so an engineer typing in another
application is not interrupted and their cursor does not jump. "Like a human,
without touching anything" is a literal description of the mechanism.

**Every send has a timeout.** `SendMessageTimeoutW` with `SMTO_ABORTIFHUNG`
throughout, never bare `SendMessage`: a window that has stopped pumping messages
would otherwise block the daemon thread forever, which is the failure this
module was built to escape.

Windows only. On any other platform `AVAILABLE` is False, every entry point
raises `UiUnavailable`, and the module still imports -- the Linux test suite
drives the mock instead (`mock_ui.py`), which implements the same shapes.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

AVAILABLE = sys.platform == "win32"


class UiUnavailable(RuntimeError):
    """The interface could not be read or driven, with the reason."""


# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

WM_COMMAND = 0x0111
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETTEXT = 0x000C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SETFOCUS = 0x0007

BM_CLICK = 0x00F5
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_CHECKED = 1
BST_UNCHECKED = 0
BN_CLICKED = 0

CB_GETCOUNT = 0x0146
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
CB_SETCURSEL = 0x014E
CBN_SELCHANGE = 1

EM_SETSEL = 0x00B1

GW_ENABLEDPOPUP = 6

GWL_STYLE = -16
BS_TYPE_MASK = 0x0F
BS_PUSHBUTTON = 0x00
BS_DEFPUSHBUTTON = 0x01
BS_CHECKBOX = 0x02
BS_AUTOCHECKBOX = 0x03
BS_RADIOBUTTON = 0x04
BS_3STATE = 0x05
BS_AUTO3STATE = 0x06
BS_GROUPBOX = 0x07
BS_AUTORADIOBUTTON = 0x09

MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
MF_POPUP = 0x0010

MIIM_STATE = 0x0001
MIIM_ID = 0x0002
MIIM_SUBMENU = 0x0004
MIIM_STRING = 0x0040

SMTO_ABORTIFHUNG = 0x0002
SMTO_NORMAL = 0x0000

#: Long enough for a busy window to answer, short enough that a wedged one does
#: not eat the daemon's 25 s operation budget in a single call.
SEND_TIMEOUT_MS = 2_000

#: Guards against a pathological or hostile window tree. A CATIA dialog has tens
#: of controls and its menu has hundreds of items; these are three orders of
#: magnitude above anything real.
MAX_CONTROLS = 400
MAX_MENU_ITEMS = 2_000
MAX_MENU_DEPTH = 4
MAX_TEXT = 512

#: The keys a tool may send. Deliberately not "any key": these are the ones
#: CATIA commands ask for -- confirm, abandon, next field, remove the selected
#: item -- and an open enumeration would be a keylogger in reverse.
KEYS: dict[str, int] = {
    "enter": 0x0D,
    "escape": 0x1B,
    "tab": 0x09,
    "delete": 0x2E,
    "space": 0x20,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Control:
    """One widget inside a dialog, as the agent sees it."""

    #: `text`, `button`, `checkbox`, `radio`, `choice`, `list`, `label`, `group`
    #: or `other`. `other` is honest rather than a guess: CATIA draws some of
    #: its own widgets and they are reported with their window class so a
    #: Windows session can tell us what they are.
    kind: str
    #: The label a human reads. For an edit box this is the nearest static text
    #: to its left, which is how the dialog itself labels it.
    label: str
    value: str = ""
    enabled: bool = True
    control_id: int = 0
    handle: int = 0
    #: Left, top, right, bottom in screen pixels. Used to pair labels with
    #: fields; reported because it is also how a human says "the top one".
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    options: tuple[str, ...] = ()
    checked: bool | None = None
    window_class: str = ""

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "label": self.label}
        if self.value:
            out["value"] = self.value
        if self.options:
            out["options"] = list(self.options)
        if self.checked is not None:
            out["checked"] = self.checked
        if not self.enabled:
            out["enabled"] = False
        return out


@dataclass(frozen=True, slots=True)
class Dialog:
    """A popup CATIA is waiting on."""

    title: str
    handle: int
    controls: tuple[Control, ...] = ()
    window_class: str = ""

    def fields(self) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.kind in {"text", "choice", "checkbox", "radio"})

    def buttons(self) -> tuple[Control, ...]:
        return tuple(c for c in self.controls if c.kind == "button")

    def describe(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "fields": [c.describe() for c in self.fields()],
            "buttons": [c.label for c in self.buttons() if c.label],
        }


@dataclass(slots=True)
class MenuItem:
    """One entry of the live menu bar, with the path a human would read aloud."""

    label: str
    #: Menu path from the bar down, this item last. Already in the seat's
    #: language, because it was read off the seat.
    path: tuple[str, ...]
    #: What `WM_COMMAND` invokes. Zero for a submenu, which is not invocable.
    command_id: int = 0
    enabled: bool = True
    checked: bool = False
    children: list["MenuItem"] = field(default_factory=list)

    @property
    def is_submenu(self) -> bool:
        return bool(self.children) or self.command_id == 0

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"label": self.label, "path": " > ".join(self.path)}
        if not self.enabled:
            out["enabled"] = False
        if self.checked:
            out["checked"] = True
        return out

    def walk(self) -> Iterator["MenuItem"]:
        yield self
        for child in self.children:
            yield from child.walk()


# ---------------------------------------------------------------------------
# Win32 plumbing
# ---------------------------------------------------------------------------


class _MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP),
        ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.c_void_p),
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("hbmpItem", wintypes.HBITMAP),
    ]


def _user32() -> Any:
    if not AVAILABLE:  # pragma: no cover - the Linux path raises before this
        raise UiUnavailable(
            "Reading CATIA's interface needs Windows. This bridge is running on "
            f"{sys.platform}, so the interactive tools are unavailable."
        )
    return ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Owner-drawn menus: reading the labels Windows does not keep
# ---------------------------------------------------------------------------
#
# CATIA draws its own menus. Every item on a V5-R33 seat comes back from
# `GetMenuItemInfoW` with `fType = MFT_OWNERDRAW` and `cch = 0`, and
# `GetMenuStringW` returns nothing: for an owner-drawn item Windows stores no
# string at all, because the application paints the text itself and keeps it in
# its own structure. `dwItemData` points at that structure, in CATIA's address
# space, so it is not ours to read either.
#
# Measured on a real V5-R33, French seat: 13 menu-bar items, all
# `fType = 0x100`, all `cch = 0`. That is why the whole menu came back with
# blank labels and why the interface language could not be detected -- not a
# missing window, not a permissions problem, and not something a longer buffer
# fixes.
#
# What Windows *does* keep is the accessibility tree. CATIA answers
# `WM_GETOBJECT` for `OBJID_MENU`, so MSAA hands back the same menu bar with
# every label populated, in the seat's own language. It costs one COM call per
# menu bar, needs no extra dependency (oleacc ships with Windows), and -- proven
# on this machine -- moves no cursor and changes no foreground window.
#
# The limit is real and worth stating: this recovers the *menu bar* only. An
# unopened popup has no accessibility object, because Windows creates one only
# when the menu is actually dropped down, so submenu items stay unnamed. Their
# structure (command id, submenu, greyed state) still reads correctly from the
# HMENU -- it is only the text that is missing.

OBJID_MENU = 0xFFFFFFFD
STATE_SYSTEM_GRAYED = 0x8
VT_I4 = 3


class _VARIANT(ctypes.Structure):
    """Enough of a VARIANT to carry a VT_I4 child id (24 bytes on x64)."""

    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("_r1", ctypes.c_ushort),
        ("_r2", ctypes.c_ushort),
        ("_r3", ctypes.c_ushort),
        ("value", ctypes.c_longlong),
        ("_pad", ctypes.c_longlong),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


#: IID_IAccessible {618736E0-3C3D-11CF-810C-00AA00389B71}
_IID_IACCESSIBLE = _GUID(
    0x618736E0,
    0x3C3D,
    0x11CF,
    (ctypes.c_ubyte * 8)(0x81, 0x0C, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71),
)

#: IAccessible vtable slots: IUnknown holds 0-2, IDispatch 3-6, IAccessible
#: starts at 7 (get_accParent). Called through the vtable rather than through
#: comtypes so the daemon keeps shipping with no dependency beyond pywin32.
_SLOT_RELEASE = 2
_SLOT_CHILD_COUNT = 8
_SLOT_NAME = 10
_SLOT_STATE = 14


def _child_id(index: int) -> _VARIANT:
    var = _VARIANT()
    var.vt = VT_I4
    var.value = index
    return var


def _msaa_menu_labels(hwnd: int, expected: int) -> dict[int, str]:
    """Menu-bar labels by HMENU position, or `{}` if they cannot be trusted.

    MSAA numbers the bar's children from 1 in the same order as the HMENU, so
    child *i* is position *i - 1*. That mapping is only safe while the two agree
    on how many items there are, so a mismatch returns nothing rather than
    labelling the wrong commands -- naming the wrong menu item is worse than
    naming none, because the caller would go on to press it.
    """
    if not AVAILABLE:  # pragma: no cover - Windows only
        return {}
    try:
        oleacc = ctypes.windll.oleacc  # type: ignore[attr-defined]
        oleaut32 = ctypes.windll.oleaut32  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no accessibility layer is not an error
        return {}

    acc = ctypes.c_void_p()
    try:
        hr = oleacc.AccessibleObjectFromWindow(
            wintypes.HWND(hwnd),
            ctypes.c_ulong(OBJID_MENU),
            ctypes.byref(_IID_IACCESSIBLE),
            ctypes.byref(acc),
        )
    except Exception:  # noqa: BLE001
        return {}
    if hr != 0 or not acc:
        return {}

    try:
        vtable = ctypes.cast(acc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        get_count = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long)
        )(vtable[_SLOT_CHILD_COUNT])
        get_name = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, _VARIANT, ctypes.POINTER(ctypes.c_void_p)
        )(vtable[_SLOT_NAME])

        count = ctypes.c_long()
        if get_count(acc, ctypes.byref(count)) != 0:
            return {}
        if count.value != expected:
            return {}

        labels: dict[int, str] = {}
        for index in range(1, count.value + 1):
            text = ctypes.c_void_p()
            if get_name(acc, _child_id(index), ctypes.byref(text)) != 0 or not text:
                continue
            try:
                labels[index - 1] = ctypes.wstring_at(text)
            finally:
                oleaut32.SysFreeString(text)
        return labels
    except Exception:  # noqa: BLE001 - a seat we cannot read is not a failure
        return {}
    finally:
        try:
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
                ctypes.cast(acc, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0][
                    _SLOT_RELEASE
                ]
            )
            release(acc)
        except Exception:  # noqa: BLE001
            pass


def _send(hwnd: int, message: int, wparam: int, lparam: Any) -> int:
    """`SendMessageTimeoutW`, always. See the module docstring."""
    user32 = _user32()
    result = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(
        wintypes.HWND(hwnd),
        wintypes.UINT(message),
        wintypes.WPARAM(wparam),
        lparam,
        wintypes.UINT(SMTO_NORMAL | SMTO_ABORTIFHUNG),
        wintypes.UINT(SEND_TIMEOUT_MS),
        ctypes.byref(result),
    )
    if not ok:
        raise UiUnavailable(
            "A CATIA window stopped responding to messages. It is busy or being "
            "moved; try again in a moment."
        )
    return int(result.value)


def _post(hwnd: int, message: int, wparam: int, lparam: int) -> None:
    user32 = _user32()
    if not user32.PostMessageW(
        wintypes.HWND(hwnd), wintypes.UINT(message), wintypes.WPARAM(wparam), wintypes.LPARAM(lparam)
    ):
        raise UiUnavailable("CATIA refused the message; the window may have just closed.")


def _window_text(hwnd: int) -> str:
    user32 = _user32()
    length = int(user32.GetWindowTextLengthW(wintypes.HWND(hwnd)))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(min(length, MAX_TEXT) + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value


def _control_text(hwnd: int) -> str:
    """`WM_GETTEXT`, which reads an edit box's *current* contents.

    `GetWindowTextW` reads the same thing for a control in this process and
    returns nothing for one in another process, which every CATIA control is.
    """
    length = _send(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if length <= 0:
        return ""
    size = min(int(length), MAX_TEXT) + 1
    buffer = ctypes.create_unicode_buffer(size)
    _send(hwnd, WM_GETTEXT, size, buffer)
    return buffer.value


def _class_name(hwnd: int) -> str:
    user32 = _user32()
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    user32 = _user32()
    box = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(box)):
        return (0, 0, 0, 0)
    return (box.left, box.top, box.right, box.bottom)


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if AVAILABLE else None


def _enum_children(parent: int) -> list[int]:
    user32 = _user32()
    found: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if len(found) < MAX_CONTROLS:
            found.append(int(hwnd))
            return True
        return False

    assert _ENUM_PROC is not None  # noqa: S101 - AVAILABLE was checked in _user32
    user32.EnumChildWindows(wintypes.HWND(parent), _ENUM_PROC(callback), wintypes.LPARAM(0))
    return found


def _enum_top_level() -> list[int]:
    user32 = _user32()
    found: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        found.append(int(hwnd))
        return True

    assert _ENUM_PROC is not None  # noqa: S101 - AVAILABLE was checked in _user32
    user32.EnumWindows(_ENUM_PROC(callback), wintypes.LPARAM(0))
    return found


def _process_of(hwnd: int) -> int:
    user32 = _user32()
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


# ---------------------------------------------------------------------------
# Finding CATIA
# ---------------------------------------------------------------------------

#: CATIA V5's frame window class. Checked first because the window *title* is
#: the open document's name and therefore says nothing reliable about which
#: application it belongs to.
_FRAME_CLASSES = ("CNS_XCAD_MAIN_WINDOW", "CATIAV5FrameWindow", "AfxFrameOrView")


def main_window(pid: int | None = None) -> int:
    """The CATIA frame window, by process id when we know it.

    `pid` comes from the COM connection and is by far the most reliable filter:
    two CATIA sessions on one workstation are ordinary, and driving the wrong
    one would be silent. Without it the search falls back to the window class
    and then to a title match, and refuses rather than guesses when more than
    one candidate survives.
    """
    candidates: list[int] = []
    for hwnd in _enum_top_level():
        user32 = _user32()
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            continue
        if pid is not None and _process_of(hwnd) != pid:
            continue
        cls = _class_name(hwnd)
        title = _window_text(hwnd)
        if pid is not None or cls in _FRAME_CLASSES or "CATIA" in title.upper():
            # A frame window has a menu bar; a splash screen or a tooltip does
            # not, and that is a sharper test than any name matching.
            if user32.GetMenu(wintypes.HWND(hwnd)):
                candidates.append(hwnd)
    if not candidates:
        raise UiUnavailable(
            "No CATIA main window was found. CATIA may be starting up, or running "
            "in a different Windows session from this bridge."
        )
    if len(candidates) > 1 and pid is None:
        raise UiUnavailable(
            f"{len(candidates)} CATIA windows are open and the bridge cannot tell "
            "which one to drive. Close the ones you are not working in."
        )
    return candidates[0]


def window_titled(title: str) -> int:
    """The visible top-level window with exactly this title, or 0.

    `Application.Caption` is CATIA's own name for its frame window, so matching
    on it identifies the right session even when two are open -- which window
    class matching cannot do. It is the preferred route and needs one COM read,
    which is why the caller caches the result.
    """
    if not title:
        return 0
    user32 = _user32()
    for hwnd in _enum_top_level():
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            continue
        if _window_text(hwnd) == title and user32.GetMenu(wintypes.HWND(hwnd)):
            return hwnd
    return 0


def active_dialog(main_hwnd: int) -> Dialog | None:
    """The dialog CATIA is waiting on, or `None` when it is waiting on nothing.

    `GW_ENABLEDPOPUP` is the exact question being asked -- "which popup owned by
    this window is currently enabled" -- and it is why this does not need to
    guess from window styles or z-order. It answers `None` for an owner with no
    popup and, usefully, for a *disabled* popup, which is what a dialog looks
    like when it has in turn put up a dialog of its own.
    """
    user32 = _user32()
    popup = user32.GetWindow(wintypes.HWND(main_hwnd), wintypes.UINT(GW_ENABLEDPOPUP))
    if not popup:
        return None
    handle = int(popup)
    if handle == main_hwnd:
        return None
    return read_dialog(handle)


def read_dialog(hwnd: int) -> Dialog:
    """Everything on one dialog: its title, its fields and its buttons."""
    controls = _read_controls(hwnd)
    return Dialog(
        title=_window_text(hwnd),
        handle=hwnd,
        controls=controls,
        window_class=_class_name(hwnd),
    )


def _button_kind(hwnd: int) -> str:
    user32 = _user32()
    style = int(user32.GetWindowLongW(wintypes.HWND(hwnd), ctypes.c_int(GWL_STYLE)))
    match style & BS_TYPE_MASK:
        case v if v in (BS_CHECKBOX, BS_AUTOCHECKBOX, BS_3STATE, BS_AUTO3STATE):
            return "checkbox"
        case v if v in (BS_RADIOBUTTON, BS_AUTORADIOBUTTON):
            return "radio"
        case v if v == BS_GROUPBOX:
            return "group"
        case _:
            return "button"


def _classify(hwnd: int, cls: str) -> str:
    lowered = cls.lower()
    if "combobox" in lowered:
        return "choice"
    if lowered.startswith("edit") or "richedit" in lowered:
        return "text"
    if lowered == "button":
        return _button_kind(hwnd)
    if lowered == "static":
        return "label"
    if "listbox" in lowered or "listview" in lowered or "treeview" in lowered:
        return "list"
    return "other"


def _combo_options(hwnd: int) -> tuple[tuple[str, ...], str]:
    count = _send(hwnd, CB_GETCOUNT, 0, 0)
    if count <= 0 or count > 500:
        return (), ""
    options: list[str] = []
    for index in range(int(count)):
        length = _send(hwnd, CB_GETLBTEXTLEN, index, 0)
        if length <= 0 or length > MAX_TEXT:
            options.append("")
            continue
        buffer = ctypes.create_unicode_buffer(int(length) + 1)
        _send(hwnd, CB_GETLBTEXT, index, buffer)
        options.append(buffer.value)
    selected = _send(hwnd, CB_GETCURSEL, 0, 0)
    current = options[selected] if 0 <= selected < len(options) else ""
    return tuple(options), current


def _read_controls(dialog_hwnd: int) -> tuple[Control, ...]:
    user32 = _user32()
    raw: list[Control] = []
    for hwnd in _enum_children(dialog_hwnd):
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            continue
        cls = _class_name(hwnd)
        kind = _classify(hwnd, cls)
        enabled = bool(user32.IsWindowEnabled(wintypes.HWND(hwnd)))
        control_id = int(user32.GetDlgCtrlID(wintypes.HWND(hwnd)))
        box = _rect(hwnd)
        text = ""
        options: tuple[str, ...] = ()
        checked: bool | None = None
        try:
            if kind == "choice":
                options, text = _combo_options(hwnd)
            elif kind in {"checkbox", "radio"}:
                text = _control_text(hwnd)
                checked = _send(hwnd, BM_GETCHECK, 0, 0) == BST_CHECKED
            else:
                text = _control_text(hwnd)
        except UiUnavailable:
            # One unresponsive control must not cost the whole description. An
            # agent that can see nine of ten fields can still work; one that
            # gets an error sees none of them.
            text = ""
        raw.append(
            Control(
                kind=kind,
                label=text if kind in {"button", "checkbox", "radio", "label", "group"} else "",
                value="" if kind in {"button", "label", "group"} else text,
                enabled=enabled,
                control_id=control_id,
                handle=hwnd,
                rect=box,
                options=options,
                checked=checked,
                window_class=cls,
            )
        )
    return _label_fields(raw)


def _label_fields(controls: list[Control]) -> tuple[Control, ...]:
    """Give every unlabelled field the static text that sits beside it.

    A Win32 edit box does not know its own name. What a human reads as the
    label is a separate `Static` control placed to its left, or above it when
    the dialog stacks rather than tabulates. Pairing them geometrically is how
    every accessibility tool does this, and it is the difference between the
    agent seeing `Length` and seeing an anonymous box it has to guess at.
    """
    labels = [c for c in controls if c.kind in {"label", "group"} and c.label.strip()]
    out: list[Control] = []
    for control in controls:
        if control.kind not in {"text", "choice", "list"} or control.label:
            out.append(control)
            continue
        left, top, _right, bottom = control.rect
        best: Control | None = None
        best_gap: float = 1e18
        for candidate in labels:
            c_left, c_top, c_right, c_bottom = candidate.rect
            overlaps_row = c_top < bottom and c_bottom > top
            if overlaps_row and c_right <= left + 4:
                gap = float(left - c_right)
            elif c_bottom <= top + 4 and abs(c_left - left) < 220:
                # Stacked layout: the label above, plus a penalty so a
                # same-row label always wins over one on the line above.
                gap = float(top - c_bottom) + 1_000.0
            else:
                continue
            if gap < best_gap:
                best, best_gap = candidate, gap
        if best is not None:
            control = Control(
                kind=control.kind,
                label=best.label.strip().rstrip(":"),
                value=control.value,
                enabled=control.enabled,
                control_id=control.control_id,
                handle=control.handle,
                rect=control.rect,
                options=control.options,
                checked=control.checked,
                window_class=control.window_class,
            )
        out.append(control)
    return tuple(out)


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------


def click(dialog_hwnd: int, control: Control) -> None:
    """Press a button the way its own dialog would be told it was pressed.

    `BM_CLICK` alone is not always enough: CATIA's dialogs are not all built on
    the standard button class, and a custom control may ignore it while still
    handling the `WM_COMMAND` its parent would receive. Both are sent, in that
    order, because a real button handling both is idempotent -- it is the same
    notification twice and the dialog has already closed by the second.
    """
    if not control.enabled:
        raise UiUnavailable(f"{control.label or 'That button'} is greyed out in this dialog.")
    if control.handle:
        try:
            _send(control.handle, BM_CLICK, 0, 0)
            return
        except UiUnavailable:
            pass
    if not control.control_id:
        raise UiUnavailable(
            f"{control.label or 'That button'} has no control id, so it cannot be "
            "pressed by message. Use catia_press_key instead."
        )
    _post(
        dialog_hwnd,
        WM_COMMAND,
        (BN_CLICKED << 16) | (control.control_id & 0xFFFF),
        control.handle,
    )


def set_text(control: Control, value: str) -> None:
    """Put `value` in a text field and tell the dialog it changed.

    `WM_SETTEXT` writes the characters but generates no `EN_CHANGE`, so a
    dialog that recomputes on edit -- which in CATIA is most of them, that is
    what drives the preview -- would keep the old number and apply it on OK.
    The notification is posted explicitly for that reason. This is the step
    most likely to need adjusting against a live seat; see the setup doc.
    """
    if not control.enabled:
        raise UiUnavailable(f"{control.label or 'That field'} is greyed out in this dialog.")
    if control.kind != "text":
        raise UiUnavailable(
            f"{control.label or 'That field'} is a {control.kind}, not a text box."
        )
    buffer = ctypes.create_unicode_buffer(value)
    _send(control.handle, WM_SETTEXT, 0, buffer)
    user32 = _user32()
    parent = int(user32.GetParent(wintypes.HWND(control.handle)) or 0)
    if parent and control.control_id:
        # EN_CHANGE is 0x0300; the dialog reads it as "this field was edited".
        _post(parent, WM_COMMAND, (0x0300 << 16) | (control.control_id & 0xFFFF), control.handle)


def set_choice(control: Control, value: str) -> None:
    """Select an option in a dropdown by its visible text."""
    if not control.enabled:
        raise UiUnavailable(f"{control.label or 'That list'} is greyed out in this dialog.")
    if control.kind != "choice":
        raise UiUnavailable(f"{control.label or 'That field'} is not a dropdown.")
    wanted = value.strip().casefold()
    index = next(
        (i for i, option in enumerate(control.options) if option.strip().casefold() == wanted),
        -1,
    )
    if index < 0:
        options = ", ".join(option for option in control.options if option) or "none"
        raise UiUnavailable(
            f"{value!r} is not one of the options for {control.label or 'that list'}. "
            f"It offers: {options}."
        )
    _send(control.handle, CB_SETCURSEL, index, 0)
    user32 = _user32()
    parent = int(user32.GetParent(wintypes.HWND(control.handle)) or 0)
    if parent and control.control_id:
        _post(
            parent, WM_COMMAND, (CBN_SELCHANGE << 16) | (control.control_id & 0xFFFF), control.handle
        )


def set_checked(control: Control, checked: bool) -> None:
    """Tick or untick a checkbox, and notify as a real click would."""
    if not control.enabled:
        raise UiUnavailable(f"{control.label or 'That box'} is greyed out in this dialog.")
    if control.kind not in {"checkbox", "radio"}:
        raise UiUnavailable(f"{control.label or 'That field'} is not a checkbox.")
    if control.checked is checked:
        return
    # BM_CLICK rather than BM_SETCHECK: setting the state directly changes the
    # tick without telling the dialog, and a dialog that enables three other
    # fields when this is ticked would not enable them.
    _send(control.handle, BM_CLICK, 0, 0)


def press_key(hwnd: int, key: str) -> None:
    """Send one keystroke to a window, down then up, as a keyboard would."""
    code = KEYS.get(key.strip().lower())
    if code is None:
        raise UiUnavailable(
            f"{key!r} is not a key this bridge sends. Available: {', '.join(sorted(KEYS))}."
        )
    _post(hwnd, WM_KEYDOWN, code, 1)
    _post(hwnd, WM_KEYUP, code, 0xC0000001)


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------


def read_menu(main_hwnd: int, *, max_depth: int = MAX_MENU_DEPTH) -> list[MenuItem]:
    """The whole live menu bar, in this seat's own language.

    This is the answer to "what can I press right now", and it is the only one
    that is correct on every installation: it is read from the running
    application rather than assembled from a table that has to be kept in step
    with fourteen languages and thirty releases.

    Greyed items are included, marked `enabled: false`. That is deliberate --
    "Pocket is there but greyed out because nothing is selected" is a far more
    useful answer than "Pocket is not in this menu", and it is the difference
    between the agent selecting a profile and the agent giving up.
    """
    user32 = _user32()
    bar = user32.GetMenu(wintypes.HWND(main_hwnd))
    if not bar:
        raise UiUnavailable(
            "The CATIA window has no menu bar. It may be showing a full-screen "
            "viewer, or the frame window found is not the main one."
        )
    budget = [MAX_MENU_ITEMS]
    # CATIA's menus are owner-drawn, so USER32 has no text for them. MSAA does,
    # for the bar; see `_msaa_menu_labels`.
    labels = _msaa_menu_labels(main_hwnd, int(user32.GetMenuItemCount(bar)))
    return _read_menu_level(bar, (), max_depth, budget, labels)


def _read_menu_level(
    hmenu: Any,
    path: tuple[str, ...],
    depth: int,
    budget: list[int],
    labels: dict[int, str] | None = None,
) -> list[MenuItem]:
    if depth <= 0 or budget[0] <= 0:
        return []
    user32 = _user32()
    count = int(user32.GetMenuItemCount(hmenu))
    if count <= 0:
        return []
    items: list[MenuItem] = []
    for index in range(count):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        info = _MENUITEMINFOW()
        info.cbSize = ctypes.sizeof(_MENUITEMINFOW)
        info.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING
        info.dwTypeData = None
        info.cch = 0
        if not user32.GetMenuItemInfoW(hmenu, index, True, ctypes.byref(info)):
            continue
        # Only for real menus. The bar's trailing entries -- minimise, restore,
        # close -- are window buttons that MSAA also names, and promoting those
        # into the command list would offer the agent a "Fermer" to press.
        supplied = (labels or {}).get(index, "") if info.hSubMenu else ""
        if info.cch == 0 and not info.hSubMenu:
            continue  # separator, or an item drawn by the owner with no text
        label = supplied
        if info.cch:
            size = min(int(info.cch), MAX_TEXT) + 1
            buffer = ctypes.create_unicode_buffer(size)
            info.dwTypeData = ctypes.cast(buffer, wintypes.LPWSTR)
            info.cch = size
            info.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING
            if user32.GetMenuItemInfoW(hmenu, index, True, ctypes.byref(info)):
                label = buffer.value
        # The ampersand is the keyboard accelerator marker, not part of the
        # name a user reads or a name anything should be matched against.
        clean = label.replace("&", "").strip()
        if not clean and not info.hSubMenu:
            continue
        here = (*path, clean)
        children: list[MenuItem] = []
        if info.hSubMenu:
            children = _read_menu_level(info.hSubMenu, here, depth - 1, budget)
        items.append(
            MenuItem(
                label=clean,
                path=here,
                command_id=0 if info.hSubMenu else int(info.wID),
                enabled=not bool(info.fState & (MF_GRAYED | MF_DISABLED)),
                checked=bool(info.fState & MF_CHECKED),
                children=children,
            )
        )
    return items


#: Menu-bar titles that identify an interface language, folded for comparison.
#:
#: Deliberately the menu *bar* and not deeper items: the bar has seven or eight
#: entries that every V5 seat has, in every workbench, and they are the most
#: stable strings in the product. Deeper menus change with the workbench, so a
#: detector built on them would answer differently depending on what the
#: engineer happened to be doing.
MENU_BAR_LANGUAGES: dict[str, tuple[str, ...]] = {
    "en": ("file", "edit", "view", "insert", "tools", "window", "help"),
    "fr": ("fichier", "edition", "affichage", "insertion", "outils", "fenetre", "aide"),
    "de": ("datei", "bearbeiten", "ansicht", "einfugen", "extras", "fenster", "hilfe"),
    "it": ("file", "modifica", "visualizza", "inserisci", "strumenti", "finestra", "guida"),
    "es": ("archivo", "edicion", "ver", "insertar", "herramientas", "ventana", "ayuda"),
}


def detect_language(items: list[MenuItem]) -> str:
    """Which language this menu bar is in, or empty when it is not one we know.

    Empty is the important case and it is handled properly upstream: the server
    resolves commands to their English names and the daemon finds the real label
    by reading this same menu. A seat in Japanese therefore works -- one round
    trip slower and with no translation table -- which is the whole reason the
    live menu is the primary mechanism and this table is only an optimisation.
    """
    labels = {_fold_label(item.label) for item in items}
    best, score = "", 0
    for code, titles in MENU_BAR_LANGUAGES.items():
        hits = sum(1 for title in titles if title in labels)
        if hits > score:
            best, score = code, hits
    # Two is enough to separate the languages in the table and high enough that
    # a coincidence cannot reach it: `File` alone is English *and* Italian, so
    # a one-hit answer would be a guess dressed as a detection.
    return best if score >= 2 else ""


def _fold_label(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.replace("&", "").strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch) and ch.isalnum())


def invoke_menu(main_hwnd: int, item: MenuItem) -> None:
    """Choose a menu item, exactly as clicking it would.

    `WM_COMMAND` with the item's id is what Windows itself posts when a user
    releases the mouse over a menu entry, so CATIA cannot tell the difference --
    and no pointer moved and no window came to the front to receive it.
    """
    if item.command_id <= 0:
        raise UiUnavailable(f"{item.label!r} is a submenu, not a command.")
    if not item.enabled:
        raise UiUnavailable(
            f"{item.label!r} is greyed out right now. CATIA disables a command when "
            "its preconditions are not met -- usually nothing is selected, or the "
            "active workbench does not own it."
        )
    _post(main_hwnd, WM_COMMAND, item.command_id & 0xFFFF, 0)


def find_menu_item(
    items: list[MenuItem], predicate: Callable[[MenuItem], bool]
) -> MenuItem | None:
    """First item anywhere in the tree that `predicate` accepts."""
    for top in items:
        for item in top.walk():
            if predicate(item):
                return item
    return None
