"""Text output via xdotool, plus the latching-modifier state machine.

xdotool sends real X keystrokes, which is why this works in the snap-packaged
Firefox where AT-SPI based keyboards (Onboard) cannot see the focused field at
all. We never take input focus, so there is no window to re-activate first --
the target still has focus when the keystroke lands.
"""
from __future__ import annotations

import subprocess
from enum import Enum

MODIFIERS = ("shift", "ctrl", "alt", "super")

# Characters whose X keysym name differs from the character itself.
_KEYSYMS = {
    " ": "space", ",": "comma", ".": "period", "!": "exclam", "@": "at",
    "#": "numbersign", "$": "dollar", "%": "percent", "^": "asciicircum",
    "&": "ampersand", "*": "asterisk", "(": "parenleft", ")": "parenright",
    "-": "minus", "_": "underscore", "=": "equal", "+": "plus",
    "[": "bracketleft", "]": "bracketright", "{": "braceleft", "}": "braceright",
    ";": "semicolon", "'": "apostrophe", '"': "quotedbl", "/": "slash",
    "\\": "backslash", "<": "less", ">": "greater", "?": "question",
    ":": "colon", "|": "bar", "~": "asciitilde", "`": "grave",
}


class ModState(Enum):
    OFF = 0
    LATCHED = 1   # applies to the next key, then clears
    LOCKED = 2    # stays until tapped again


class Output:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.mods: dict[str, ModState] = {m: ModState.OFF for m in MODIFIERS}
        self.sent: list[str] = []  # for tests / --dry-run

    # -- modifiers -------------------------------------------------------

    def cycle_mod(self, name: str) -> ModState:
        """off -> latched -> locked -> off."""
        cur = self.mods[name]
        nxt = {
            ModState.OFF: ModState.LATCHED,
            ModState.LATCHED: ModState.LOCKED,
            ModState.LOCKED: ModState.OFF,
        }[cur]
        self.mods[name] = nxt
        return nxt

    def active_mods(self) -> list[str]:
        return [m for m in MODIFIERS if self.mods[m] is not ModState.OFF]

    def shift_active(self) -> bool:
        return self.mods["shift"] is not ModState.OFF

    def _consume_latched(self) -> None:
        for m, st in self.mods.items():
            if st is ModState.LATCHED:
                self.mods[m] = ModState.OFF

    # -- sending ---------------------------------------------------------

    def _run(self, args: list[str]) -> None:
        self.sent.append(" ".join(args))
        if self.dry_run:
            return
        try:
            subprocess.run(["xdotool", *args], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass  # a dropped keystroke must never take the keyboard down

    def key(self, keysym: str, extra_mods: list[str] | None = None) -> None:
        """Press a named key (BackSpace, Return, Left, ...) with active modifiers."""
        mods = self.active_mods()
        if extra_mods:
            mods = sorted(set(mods) | set(extra_mods))
        combo = "+".join([*mods, keysym]) if mods else keysym
        self._run(["key", "--clearmodifiers", combo])
        self._consume_latched()

    def char(self, ch: str) -> None:
        """Type one character.

        With a non-shift modifier held this has to go through `key` so that
        Ctrl+C stays Ctrl+C; otherwise `type` is used, which handles any
        character without a keysym lookup. Shift is applied by picking the
        shifted face upstream, not by holding shift -- more reliable for
        punctuation across layouts.
        """
        non_shift = [m for m in self.active_mods() if m != "shift"]
        if non_shift:
            self._run(["key", "--clearmodifiers", "+".join([*non_shift, _KEYSYMS.get(ch, ch)])])
        else:
            self._run(["type", "--clearmodifiers", "--delay", "0", "--", ch])
        self._consume_latched()

    def text(self, s: str) -> None:
        """Type a whole string in one call -- used for decoded words."""
        if not s:
            return
        self._run(["type", "--clearmodifiers", "--delay", "0", "--", s])
        self._consume_latched()

    def backspace(self, n: int = 1) -> None:
        for _ in range(n):
            self._run(["key", "--clearmodifiers", "BackSpace"])
