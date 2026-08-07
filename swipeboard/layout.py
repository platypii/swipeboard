"""Keyboard geometry and layers.

All coordinates are in *grid units*, not pixels: the board is exactly
GRID_COLS wide and one row tall per row. The widget scales grid -> pixels at
draw time, which keeps the decoder resolution-independent -- tuning done at one
window size transfers to any other.

Shape is driven by terminal work: esc / tab / shift / ctrl form a left column
aligned to the letter rows the way a real keyboard has them, and the arrows sit
in a proper inverted-T on the right rather than strung along a bottom row. That
costs width, so the letters block is narrower than the board -- but the letters
keep their standard QWERTY stagger and spacing relative to each other, which is
all the decoder ever sees.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# 1.5 left column + 10 letter columns + 3 nav columns.
LEFT_W = 1.5
LETTERS_X0 = LEFT_W
NAV_X0 = LETTERS_X0 + 10.0        # 11.5
GRID_COLS = NAV_X0 + 3.0          # 14.5
GRID_ROWS = 4.0


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


def _pairs(spec: str) -> list[tuple]:
    """"a<,b>" -> keys a (shift: <) and b (shift: >)."""
    return [(spec[i], KeyType.CHAR, spec[i], 1.0, spec[i + 1])
            for i in range(0, len(spec), 2)]


def _left_column() -> list[Key]:
    """esc / tab / shift / ctrl, one per letter row -- same as a real keyboard,
    and the four keys terminal work leans on hardest."""
    return [
        Key("esc", KeyType.KEYSYM, "Escape", 0.0, 0.0, LEFT_W),
        Key("tab", KeyType.KEYSYM, "Tab", 0.0, 1.0, LEFT_W),
        Key("⇧", KeyType.MOD, "shift", 0.0, 2.0, LEFT_W),
        Key("ctrl", KeyType.MOD, "ctrl", 0.0, 3.0, LEFT_W),
    ]


def _nav_cluster(paging: bool) -> list[Key]:
    """Inverted-T arrows with a paired top row. Arrows never move between
    layers; only the two outer keys above them swap."""
    left_top, right_top = (
        (("pgup", KeyType.KEYSYM, "Prior"), ("pgdn", KeyType.KEYSYM, "Next"))
        if paging else
        (("home", KeyType.KEYSYM, "Home"), ("end", KeyType.KEYSYM, "End"))
    )
    return [
        *_row(2, [left_top, ("↑", KeyType.KEYSYM, "Up"), right_top], x0=NAV_X0),
        *_row(3, [("←", KeyType.KEYSYM, "Left"), ("↓", KeyType.KEYSYM, "Down"),
                  ("→", KeyType.KEYSYM, "Right")], x0=NAV_X0),
    ]


def _letters_layer() -> list[Key]:
    keys = _left_column()
    keys += _row(0, _chars("qwertyuiop"), x0=LETTERS_X0)
    keys += _row(1, _chars("asdfghjkl"), x0=LETTERS_X0 + 0.5)
    # Rows 1 and 2 keep the standard QWERTY stagger (+0.5, +1.0). It has to be
    # spelled out now that shift lives in the left column instead of indenting
    # the row itself -- without it z lands left of a, which is both wrong for
    # muscle memory and measurably worse for the decoder.
    # The +1.0 stagger leaves a full key of dead space at the left of this row.
    # Pipe fills it -- the terminal character most missing from this layer, and
    # it keeps the letters themselves properly staggered behind it.
    keys += _row(2, [("|", KeyType.CHAR, "|", 1.0, "\\")], x0=LETTERS_X0)
    keys += _row(2, [*_chars("zxcvbnm"), *_pairs(",<.>")], x0=LETTERS_X0 + 1.0)
    keys += _row(3, [
        ("?123", KeyType.LAYER, "symbols", 1.5),
        ("alt", KeyType.MOD, "alt"),
        ("super", KeyType.MOD, "super"),
        ("space", KeyType.CHAR, " ", 4.5),
        # - and / earn their place on the primary layer for terminal work.
        *_pairs("-_/?"),
    ], x0=LETTERS_X0)
    # Right column: backspace and hide up top, enter below, then the nav block.
    keys += _row(0, [("⌫", KeyType.KEYSYM, "BackSpace", 2.0),
                     ("▼", KeyType.ACTION, "hide", 1.0)], x0=NAV_X0)
    keys += _row(1, [("⏎", KeyType.KEYSYM, "Return", 3.0)], x0=NAV_X0)
    keys += _nav_cluster(paging=False)
    return keys


def _symbols_layer() -> list[Key]:
    keys = _left_column()
    keys += _row(0, _chars("1234567890"), x0=LETTERS_X0)
    keys += _row(1, _chars("!@#$%^&*()"), x0=LETTERS_X0)
    keys += _row(2, _chars("-_=+[]{}\\|"), x0=LETTERS_X0)
    keys += _row(3, [
        ("abc", KeyType.LAYER, "letters", 1.5),
        ("alt", KeyType.MOD, "alt"),
        ("super", KeyType.MOD, "super"),
        ("space", KeyType.CHAR, " ", 2.5),
        *_pairs("/?;:'\"`~"),
    ], x0=LETTERS_X0)
    keys += _row(0, [("⌫", KeyType.KEYSYM, "BackSpace", 2.0),
                     ("▼", KeyType.ACTION, "hide", 1.0)], x0=NAV_X0)
    keys += _row(1, [("⏎", KeyType.KEYSYM, "Return", 3.0)], x0=NAV_X0)
    keys += _nav_cluster(paging=True)
    return keys


LAYERS: dict[str, list[Key]] = {
    "letters": _letters_layer(),
    "symbols": _symbols_layer(),
}


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
