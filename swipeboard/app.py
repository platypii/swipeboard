"""Entry point: the floating toggle and the board, in one process.

One process means no IPC between the launcher button and the keyboard -- the
button just calls toggle(). Same focus-safe window recipe as the board itself.
"""
from __future__ import annotations

import argparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")  # GTK4 is installed too; gi would pick it otherwise
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import lexicon
from .decoder import Decoder
from .keyboard import Board
from .layout import letter_centers
from .output import Output


class ToggleButton(Gtk.Window):
    """Always-on-top ⌨ button. accept_focus=False is what keeps tapping it from
    stealing focus from whatever you were typing into."""

    def __init__(self, board: Board) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.board = board
        self.set_title("swipeboard-toggle")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_resizable(False)
        self.stick()

        btn = Gtk.Button(label="⌨")
        btn.set_size_request(64, 64)
        btn.connect("clicked", lambda _b: self.board.toggle())
        self.add(btn)
        self.connect("destroy", Gtk.main_quit)


def main() -> int:
    ap = argparse.ArgumentParser(prog="swipeboard")
    ap.add_argument("--no-struts", action="store_true",
                    help="overlay windows instead of reserving screen space")
    ap.add_argument("--no-button", action="store_true",
                    help="skip the floating toggle (board starts visible)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print keystrokes instead of sending them")
    ap.add_argument("--show", action="store_true", help="start with the board visible")
    args = ap.parse_args()

    words = lexicon.load()
    if not words:
        print("no lexicon found (install onboard-data)", flush=True)
        return 1

    decoder = Decoder(words, letter_centers())
    out = Output(dry_run=args.dry_run)
    board = Board(decoder, out, use_struts=not args.no_struts)

    if args.no_button or args.show:
        board.show_board()

    if not args.no_button:
        btn = ToggleButton(board)
        btn.show_all()
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        w, _h = btn.get_size()
        # Under the dictation button, which sits at y+48.
        btn.move(geo.x + geo.width - w - 16, geo.y + 120)

    # Templates are cheap but not free; build them once the UI is already up so
    # the first swipe is as quick as the thousandth.
    GLib.idle_add(decoder.warm_cache)

    Gtk.main()
    return 0
