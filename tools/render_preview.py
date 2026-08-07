#!/usr/bin/env python3
"""Render the board to a PNG without mapping a window.

Lets layout, palette and trail be checked without a full-width keyboard taking
over the live screen.

    python3 tools/render_preview.py out.png --word hello --layer letters
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from swipeboard import lexicon  # noqa: E402
from swipeboard.decoder import Decoder  # noqa: E402
from swipeboard.keyboard import Board  # noqa: E402
from swipeboard.layout import letter_centers  # noqa: E402
from swipeboard.output import ModState, Output  # noqa: E402
from tools.eval_decoder import synth_swipe  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="preview.png")
    ap.add_argument("--word", default="hello")
    ap.add_argument("--layer", default="letters", choices=["letters", "symbols"])
    ap.add_argument("--shift", action="store_true")
    ap.add_argument("--ctrl-locked", action="store_true")
    args = ap.parse_args()

    centers = letter_centers()
    dec = Decoder(lexicon.load(), centers)
    out = Output(dry_run=True)
    board = Board(dec, out, use_struts=False)
    board.layer = args.layer

    if args.shift:
        out.mods["shift"] = ModState.LATCHED
    if args.ctrl_locked:
        out.mods["ctrl"] = ModState.LOCKED

    if args.word and args.layer == "letters":
        path = synth_swipe(args.word, centers, random.Random(7))
        board._path = path
        ranked = dec.decode(path, top=5)
        board._candidates = [w for w, _ in ranked]
        print(f"swipe {args.word!r} -> {board._candidates}")

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, board.width, board.height)
    cr = cairo.Context(surf)
    board.on_draw(None, cr)
    surf.write_to_png(args.out)
    print(f"wrote {args.out} ({board.width}x{board.height}, "
          f"key {board.unit_w:.0f}x{board.row_h:.0f}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
