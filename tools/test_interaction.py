#!/usr/bin/env python3
"""End-to-end input tests with no window and no real keystrokes.

Feeds synthetic press/motion/release events straight into Board's handlers with
Output in dry-run mode, then asserts on the exact xdotool argv that would have
been sent. Covers the paths that are awkward to check by hand on a touchscreen:
tap-vs-swipe discrimination, latched vs locked modifiers, layer switching, and
candidate replacement arithmetic.

    python3 tools/test_interaction.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swipeboard import lexicon  # noqa: E402
from swipeboard.decoder import Decoder  # noqa: E402
from swipeboard.keyboard import Board  # noqa: E402
from swipeboard.layout import LAYERS, letter_centers  # noqa: E402
from swipeboard.output import ModState, Output  # noqa: E402
from tools.eval_decoder import synth_swipe  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")
        FAILURES.append(name)


class Harness:
    def __init__(self):
        self.centers = letter_centers()
        self.out = Output(dry_run=True)
        self.board = Board(Decoder(lexicon.load(), self.centers), self.out,
                           use_struts=False)

    def px(self, gx: float, gy: float) -> tuple[float, float]:
        b = self.board
        return gx * b.unit_w, b.bar_h + gy * b.row_h

    def key_named(self, label: str):
        for k in LAYERS[self.board.layer]:
            if k.label == label:
                return k
        raise KeyError(label)

    def tap_key(self, label: str) -> None:
        k = self.key_named(label)
        x, y = self.px(k.cx, k.cy)
        self.board.on_press(None, SimpleNamespace(x=x, y=y))
        self.board.on_release(None, SimpleNamespace(x=x, y=y))

    def swipe(self, word: str, seed: int = 7) -> None:
        path = synth_swipe(word, self.centers, random.Random(seed))
        x, y = self.px(*path[0])
        self.board.on_press(None, SimpleNamespace(x=x, y=y))
        for gx, gy in path[1:]:
            px, py = self.px(gx, gy)
            self.board.on_motion(None, SimpleNamespace(x=px, y=py))
        px, py = self.px(*path[-1])
        self.board.on_release(None, SimpleNamespace(x=px, y=py))

    def tap_candidate(self, idx: int) -> None:
        slot = self.board.width / 5
        x, y = (idx + 0.5) * slot, self.board.bar_h * 0.5
        self.board.on_press(None, SimpleNamespace(x=x, y=y))
        self.board.on_release(None, SimpleNamespace(x=x, y=y))

    def drain(self) -> list[str]:
        s = self.out.sent[:]
        self.out.sent.clear()
        return s


def main() -> int:
    h = Harness()

    print("taps")
    h.tap_key("a")
    check("plain letter", h.drain(), ["type --clearmodifiers --delay 0 -- a"])

    h.tap_key("⇧")
    check("shift latches", h.out.mods["shift"], ModState.LATCHED)
    h.tap_key("a")
    check("shifted letter", h.drain(), ["type --clearmodifiers --delay 0 -- A"])
    check("shift auto-clears", h.out.mods["shift"], ModState.OFF)

    h.tap_key("⌫")
    check("backspace", h.drain(), ["key --clearmodifiers BackSpace"])
    h.tap_key("→")
    check("arrow", h.drain(), ["key --clearmodifiers Right"])

    print("modifiers")
    h.tap_key("ctrl")
    h.tap_key("c")
    check("ctrl+c", h.drain(), ["key --clearmodifiers ctrl+c"])
    check("ctrl clears after use", h.out.mods["ctrl"], ModState.OFF)

    h.tap_key("ctrl")
    h.tap_key("ctrl")
    check("second tap locks", h.out.mods["ctrl"], ModState.LOCKED)
    h.tap_key("c")
    h.tap_key("v")
    check("locked ctrl persists", h.drain(),
          ["key --clearmodifiers ctrl+c", "key --clearmodifiers ctrl+v"])
    h.tap_key("ctrl")
    check("third tap clears", h.out.mods["ctrl"], ModState.OFF)

    h.tap_key("ctrl")
    h.tap_key("→")
    check("ctrl+arrow", h.drain(), ["key --clearmodifiers ctrl+Right"])

    print("layers")
    h.tap_key("?123")
    check("switched to symbols", h.board.layer, "symbols")
    h.tap_key("5")
    check("digit", h.drain(), ["type --clearmodifiers --delay 0 -- 5"])
    h.tap_key("$")
    check("punctuation", h.drain(), ["type --clearmodifiers --delay 0 -- $"])
    h.tap_key("pgdn")
    check("paging key", h.drain(), ["key --clearmodifiers Next"])
    h.tap_key("abc")
    check("back to letters", h.board.layer, "letters")

    print("swipe")
    h.swipe("important")
    sent = h.drain()
    check("swipe types word + space", sent,
          ["type --clearmodifiers --delay 0 -- important "])
    check("candidates offered", len(h.board._candidates), 5)

    print("candidate correction")
    h.swipe("hello")
    first = h.board._candidates[0]
    h.drain()
    second = h.board._candidates[1]
    h.tap_candidate(1)
    sent = h.drain()
    expect = ["key --clearmodifiers BackSpace"] * (len(first) + 1)
    expect.append(f"type --clearmodifiers --delay 0 -- {second} ")
    check(f"replace {first!r} -> {second!r}", sent, expect)

    print("swipe vs tap discrimination")
    k = h.key_named("g")
    x, y = h.px(k.cx, k.cy)
    h.board.on_press(None, SimpleNamespace(x=x, y=y))
    h.board.on_motion(None, SimpleNamespace(x=x + h.board.unit_w * 0.15, y=y))
    h.board.on_release(None, SimpleNamespace(x=x + h.board.unit_w * 0.15, y=y))
    check("small drag is still a tap", h.drain(),
          ["type --clearmodifiers --delay 0 -- g"])

    print("shift + swipe")
    h.tap_key("⇧")
    h.swipe("important")
    check("capitalised word", h.drain(),
          ["type --clearmodifiers --delay 0 -- Important "])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        return 1
    print("all interaction tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
