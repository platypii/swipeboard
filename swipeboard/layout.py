"""Keyboard geometry and layers.

All coordinates are in *grid units*, not pixels: the board is exactly
GRID_COLS wide and one row tall per row. The widget scales grid -> pixels at
draw time, which keeps the decoder resolution-independent -- tuning done at one
window size transfers to any other.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

GRID_COLS = 10.0


class KeyType(Enum):
    CHAR = "char"        # types a literal character
    KEYSYM = "keysym"    # xdotool key <payload>
    MOD = "mod"          # latching modifier
    LAYER = "layer"      # switch to layer <payload>
    ACTION = "action"    # app-level: hide, etc.


@dataclass(frozen=True)
class Key:
    label: str
    kind: KeyType
    payload: str
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    # Shifted face, for CHAR keys that have one. Letters derive theirs.
    shifted: str | None = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    def contains(self, gx: float, gy: float) -> bool:
        return self.x <= gx < self.x + self.w and self.y <= gy < self.y + self.h

    @property
    def is_letter(self) -> bool:
        return self.kind is KeyType.CHAR and self.payload.isalpha() and len(self.payload) == 1


def _row(y: float, specs: list[tuple], x0: float = 0.0) -> list[Key]:
    """Build a row. Each spec is (label, kind, payload[, width[, shifted]])."""
    keys: list[Key] = []
    x = x0
    for spec in specs:
        label, kind, payload = spec[0], spec[1], spec[2]
        w = spec[3] if len(spec) > 3 else 1.0
        shifted = spec[4] if len(spec) > 4 else None
        keys.append(Key(label, kind, payload, x, y, w, shifted=shifted))
        x += w
    return keys


def _chars(s: str) -> list[tuple]:
    return [(c, KeyType.CHAR, c) for c in s]


def _pairs(faces: list[tuple[str, str]]) -> list[tuple]:
    return [(a, KeyType.CHAR, a, 1.0, b) for a, b in faces]


# The bottom row is shared between layers, so muscle memory holds. Only the
# four arrow slots change face on the symbols layer (arrows -> paging keys).
def _system_row(paging: bool) -> list[Key]:
    nav = (
        # Spelled out: ⇞/⇟ are almost identical at key-label size.
        [("home", KeyType.KEYSYM, "Home"), ("pgdn", KeyType.KEYSYM, "Next"),
         ("pgup", KeyType.KEYSYM, "Prior"), ("end", KeyType.KEYSYM, "End")]
        if paging else
        [("←", KeyType.KEYSYM, "Left"), ("↓", KeyType.KEYSYM, "Down"),
         ("↑", KeyType.KEYSYM, "Up"), ("→", KeyType.KEYSYM, "Right")]
    )
    return _row(4, [
        ("esc", KeyType.KEYSYM, "Escape"),
        ("tab", KeyType.KEYSYM, "Tab"),
        ("ctrl", KeyType.MOD, "ctrl"),
        ("alt", KeyType.MOD, "alt"),
        ("super", KeyType.MOD, "super"),
        *nav,
        ("▼", KeyType.ACTION, "hide"),
    ])


def _letters_layer() -> list[Key]:
    keys: list[Key] = []
    keys += _row(0, _chars("qwertyuiop"))
    keys += _row(1, _chars("asdfghjkl"), x0=0.5)
    keys += _row(2, [
        ("⇧", KeyType.MOD, "shift", 1.5),
        *_chars("zxcvbnm"),
        ("⌫", KeyType.KEYSYM, "BackSpace", 1.5),
    ])
    keys += _row(3, [
        ("?123", KeyType.LAYER, "symbols", 1.5),
        (",", KeyType.CHAR, ",", 1.0, "<"),
        ("space", KeyType.CHAR, " ", 5.0),
        (".", KeyType.CHAR, ".", 1.0, ">"),
        ("⏎", KeyType.KEYSYM, "Return", 1.5),
    ])
    keys += _system_row(paging=False)
    return keys


def _symbols_layer() -> list[Key]:
    keys: list[Key] = []
    keys += _row(0, _chars("1234567890"))
    keys += _row(1, _chars("!@#$%^&*()"))
    keys += _row(2, [
        *_chars("-_=+[]{}"),
        ("⌫", KeyType.KEYSYM, "BackSpace", 2.0),
    ])
    keys += _row(3, [
        ("abc", KeyType.LAYER, "letters", 1.5),
        *_chars(";'\""),
        ("space", KeyType.CHAR, " ", 2.0),
        *_chars("/\\"),
        ("⏎", KeyType.KEYSYM, "Return", 1.5),
    ])
    keys += _system_row(paging=True)
    return keys


LAYERS: dict[str, list[Key]] = {
    "letters": _letters_layer(),
    "symbols": _symbols_layer(),
}

GRID_ROWS = 5.0


def letter_centers() -> dict[str, tuple[float, float]]:
    """Letter -> key centre in grid units. The decoder's entire view of the board."""
    return {k.payload: (k.cx, k.cy) for k in LAYERS["letters"] if k.is_letter}


def key_at(layer: str, gx: float, gy: float) -> Key | None:
    for k in LAYERS[layer]:
        if k.contains(gx, gy):
            return k
    return None


# Nominal key size in grid units, used as the decoder's distance yardstick so
# every tolerance is expressed as a fraction of a key.
KEY_UNIT = 1.0
