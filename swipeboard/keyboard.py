"""The on-screen board: rendering, touch tracking, and gesture dispatch.

The load-bearing property here is `set_accept_focus(False)`. Without it the
board steals X input focus the instant you touch it and every keystroke goes
nowhere. Same trick as ~/.local/bin/dictate-button.py.

Rendering splits in two: key faces are drawn once into a cached ImageSurface
and blitted per frame; only the swipe trail, pressed-key highlight, modifier
state and candidate bar are redrawn live. That keeps the 60 Hz path cheap --
the decoder, which runs once per lifted finger, was never the bottleneck.
"""
from __future__ import annotations

import math
import subprocess
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")  # GTK4 is installed too; gi would pick it otherwise
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .decoder import Decoder
from .layout import GRID_COLS, GRID_ROWS, LAYERS, Key, KeyType, key_at
from .output import ModState, Output

# Dark palette, sized to sit next to the dashboard without clashing.
BG = (0.078, 0.086, 0.102)
KEY_FACE = (0.137, 0.149, 0.176)
KEY_SPECIAL = (0.106, 0.118, 0.141)
KEY_PRESSED = (0.227, 0.255, 0.314)
KEY_ENTER = (0.129, 0.267, 0.420)   # dimmed ACCENT: enter must be findable at a glance
TEXT = (0.902, 0.910, 0.925)
TEXT_DIM = (0.612, 0.647, 0.702)
ACCENT = (0.302, 0.639, 1.0)
LOCKED = (1.0, 0.706, 0.329)

# The whole gesture stays on screen, fading toward the tail; a hard cap chops
# the start off long words, which reads as a rendering glitch.
TRAIL_MAX = 400
N_CANDIDATES = 5

# Fraction of screen height for the whole board, candidate bar included.
BOARD_HEIGHT_FRAC = 0.36

# Two space taps within this window collapse to ". " (phone-style).
DOUBLE_SPACE_S = 0.5


def _mono_layout(cr):
    """Pango layout with greyscale antialiasing.

    The default subpixel (RGB) AA puts orange/blue fringes on thin glyph strokes
    like ⇧ and ⌫ at key-label sizes. Greyscale costs nothing here and keeps the
    labels neutral whatever the panel's subpixel order is -- which matters on a
    tablet that gets mounted rotated.
    """
    layout = PangoCairo.create_layout(cr)
    opts = cairo.FontOptions()
    opts.set_antialias(cairo.ANTIALIAS_GRAY)
    PangoCairo.context_set_font_options(layout.get_context(), opts)
    return layout


def _rounded(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Board(Gtk.Window):
    def __init__(self, decoder: Decoder, out: Output, use_struts: bool = True) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.decoder = decoder
        self.out = out
        self.use_struts = use_struts

        self.layer = "letters"
        self._path: list[tuple[float, float]] = []
        self._press_key: Key | None = None
        self._pressing = False
        self._bar_index: int | None = None
        self._candidates: list[str] = []
        self._last_word: str | None = None
        # Space-key state machine (see _space_tap).
        self._swallow_space = False       # a decoded word just typed its own trailing space
        self._space_at: float | None = None  # when a period-eligible space was last tapped
        self._wordish = False             # last output ended in a letter/digit
        self._static: cairo.ImageSurface | None = None
        self._static_key: tuple | None = None

        self.set_title("swipeboard")
        self.set_decorated(False)
        self.set_accept_focus(False)      # never steal focus from the target field
        self.set_focus_on_map(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_resizable(False)
        self.stick()
        self.set_app_paintable(True)

        self.area = Gtk.DrawingArea()
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.TOUCH_MASK
        )
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("button-release-event", self.on_release)
        self.add(self.area)

        self._compute_geometry()
        self.connect("map-event", self._on_map)

    # -- geometry --------------------------------------------------------

    def _compute_geometry(self) -> None:
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geo = monitor.get_geometry()
        self.mon = geo
        # GDK hands us logical pixels; X properties like _NET_WM_STRUT_PARTIAL
        # are in device pixels. On this 2560x1600 panel that is a factor of 2,
        # and getting it wrong makes the WM carve the workarea sideways instead
        # of reserving the bottom.
        self.scale = monitor.get_scale_factor()

        self.width = geo.width
        self.unit_w = self.width / GRID_COLS
        # Whole board (candidate bar + key rows) takes this much screen height.
        # Kept low deliberately: this gets used over a terminal, and every row
        # the board takes is a row of scrollback you cannot see.
        total = int(geo.height * BOARD_HEIGHT_FRAC)
        self.row_h = total / (GRID_ROWS + 1.0)
        self.bar_h = self.row_h
        self.height = int(self.bar_h + GRID_ROWS * self.row_h)

        self.set_size_request(self.width, self.height)
        self.resize(self.width, self.height)
        self.move(geo.x, geo.y + geo.height - self.height)

    def _to_grid(self, px: float, py: float) -> tuple[float, float]:
        return px / self.unit_w, (py - self.bar_h) / self.row_h

    def _key_rect(self, k: Key) -> tuple[float, float, float, float]:
        return (
            k.x * self.unit_w,
            self.bar_h + k.y * self.row_h,
            k.w * self.unit_w,
            k.h * self.row_h,
        )

    # -- struts ----------------------------------------------------------

    def _apply_struts(self, on: bool) -> None:
        """Reserve screen space so maximised windows stop above the board.

        Done with xprop rather than Gdk.property_change: the introspected
        variant is fussy about 32-bit property packing on 64-bit, and we already
        shell out for xdotool.
        """
        if not self.use_struts:
            return
        gdk_win = self.get_window()
        if gdk_win is None:
            return
        try:
            xid = gdk_win.get_xid()
        except AttributeError:
            return  # not X11
        if on:
            # Everything below is in DEVICE pixels. The unit mixing here is a
            # trap: monitor geometry and window sizes come back from GDK in
            # logical pixels, but the root window and X properties are device.
            # Mixing them fed the WM a bottom strut bigger than the screen,
            # which collapsed the workarea to its minimum instead of erroring.
            s = self.scale
            root_h = self.get_screen().get_root_window().get_height()
            mon_bottom = (self.mon.y + self.mon.height) * s
            below = max(0, root_h - mon_bottom)   # space under us on a taller screen
            vals = [0, 0, 0, self.height * s + below,
                    0, 0, 0, 0, 0, 0,
                    self.mon.x * s, (self.mon.x + self.width) * s - 1]
            args = ["-id", str(xid), "-f", "_NET_WM_STRUT_PARTIAL", "32c",
                    "-set", "_NET_WM_STRUT_PARTIAL", ",".join(str(v) for v in vals)]
        else:
            args = ["-id", str(xid), "-remove", "_NET_WM_STRUT_PARTIAL"]
        try:
            subprocess.run(["xprop", *args], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    def _on_map(self, *_a) -> None:
        self._apply_struts(True)

    def show_board(self) -> None:
        self.show_all()
        self.present()
        GLib.idle_add(self._apply_struts, True)

    def hide_board(self) -> None:
        self._apply_struts(False)
        self.hide()

    def toggle(self) -> None:
        if self.get_visible():
            self.hide_board()
        else:
            self.show_board()

    # -- drawing ---------------------------------------------------------

    def _static_cache_key(self) -> tuple:
        return (self.layer, self.out.shift_active(), self.width, self.height)

    def _face(self, k: Key) -> str:
        if self.out.shift_active():
            if k.shifted:
                return k.shifted
            if k.is_letter:
                return k.payload.upper()
        return k.label

    def _build_static(self) -> cairo.ImageSurface:
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        cr = cairo.Context(surf)
        cr.set_source_rgb(*BG)
        cr.paint()

        layout = _mono_layout(cr)
        for k in LAYERS[self.layer]:
            x, y, w, h = self._key_rect(k)
            pad = max(1.5, self.unit_w * 0.02)
            special = k.kind is not KeyType.CHAR
            enter = k.payload == "Return"
            cr.set_source_rgb(*(KEY_ENTER if enter
                                else KEY_SPECIAL if special else KEY_FACE))
            _rounded(cr, x + pad, y + pad, w - 2 * pad, h - 2 * pad, h * 0.14)
            cr.fill()

            face = self._face(k)
            size = self.row_h * (0.30 if len(face) > 2 else 0.42)
            self._text(cr, layout, face, x, y, w, h, size,
                       TEXT_DIM if special and not enter else TEXT)
        return surf

    def _text(self, cr, layout, s, x, y, w, h, size, color) -> None:
        layout.set_font_description(Pango.FontDescription(f"Sans {size:.0f}px"))
        layout.set_text(s, -1)
        tw, th = layout.get_pixel_size()
        cr.set_source_rgb(*color)
        cr.move_to(x + (w - tw) / 2, y + (h - th) / 2)
        PangoCairo.show_layout(cr, layout)

    def on_draw(self, _w, cr) -> bool:
        ck = self._static_cache_key()
        if self._static is None or self._static_key != ck:
            self._static = self._build_static()
            self._static_key = ck
        cr.set_source_surface(self._static, 0, 0)
        cr.paint()

        layout = _mono_layout(cr)
        self._draw_bar(cr, layout)

        # Modifier state and the key currently under the finger.
        for k in LAYERS[self.layer]:
            x, y, w, h = self._key_rect(k)
            pad = max(1.5, self.unit_w * 0.02)
            if k.kind is KeyType.MOD:
                st = self.out.mods.get(k.payload, ModState.OFF)
                if st is not ModState.OFF:
                    cr.set_source_rgb(*(ACCENT if st is ModState.LATCHED else LOCKED))
                    cr.set_line_width(max(2.0, self.row_h * 0.045))
                    _rounded(cr, x + pad, y + pad, w - 2 * pad, h - 2 * pad, h * 0.14)
                    cr.stroke()
            if self._pressing and k is self._press_key:
                cr.set_source_rgba(*KEY_PRESSED, 0.75)
                _rounded(cr, x + pad, y + pad, w - 2 * pad, h - 2 * pad, h * 0.14)
                cr.fill()
                self._text(cr, layout, self._face(k), x, y, w, h,
                           self.row_h * (0.30 if len(self._face(k)) > 2 else 0.42), TEXT)

        self._draw_trail(cr)
        return False

    def _draw_trail(self, cr) -> None:
        if len(self._path) < 2:
            return
        pts = self._path[-TRAIL_MAX:]
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        n = len(pts)
        for i in range(1, n):
            a = i / n
            cr.set_source_rgba(*ACCENT, 0.15 + 0.65 * a)
            cr.set_line_width(max(2.0, self.row_h * 0.10 * a))
            x0 = pts[i - 1][0] * self.unit_w
            y0 = self.bar_h + pts[i - 1][1] * self.row_h
            x1 = pts[i][0] * self.unit_w
            y1 = self.bar_h + pts[i][1] * self.row_h
            cr.move_to(x0, y0)
            cr.line_to(x1, y1)
            cr.stroke()

    def _draw_bar(self, cr, layout) -> None:
        cr.set_source_rgb(*KEY_SPECIAL)
        cr.rectangle(0, 0, self.width, self.bar_h)
        cr.fill()
        if not self._candidates:
            return
        slot = self.width / N_CANDIDATES
        for i, word in enumerate(self._candidates[:N_CANDIDATES]):
            if i == self._bar_index:
                cr.set_source_rgba(*ACCENT, 0.25)
                cr.rectangle(i * slot, 0, slot, self.bar_h)
                cr.fill()
            self._text(cr, layout, word, i * slot, 0, slot, self.bar_h,
                       self.bar_h * 0.38, TEXT if i == 0 else TEXT_DIM)
            if i:
                cr.set_source_rgba(*TEXT_DIM, 0.25)
                cr.set_line_width(1)
                cr.move_to(i * slot, self.bar_h * 0.2)
                cr.line_to(i * slot, self.bar_h * 0.8)
                cr.stroke()

    # -- input -----------------------------------------------------------

    def on_press(self, _w, ev) -> bool:
        self._pressing = True
        self._path = []
        self._press_key = None
        self._bar_index = None

        if ev.y < self.bar_h:
            slot = self.width / N_CANDIDATES
            idx = int(ev.x / slot)
            if idx < len(self._candidates):
                self._bar_index = idx
            self.area.queue_draw()
            return True

        gx, gy = self._to_grid(ev.x, ev.y)
        self._press_key = key_at(self.layer, gx, gy)
        self._path = [(gx, gy)]
        self.area.queue_draw()
        return True

    def on_motion(self, _w, ev) -> bool:
        if not self._pressing or self._bar_index is not None:
            return True
        gx, gy = self._to_grid(ev.x, ev.y)
        if self._path:
            lx, ly = self._path[-1]
            if abs(gx - lx) < 0.02 and abs(gy - ly) < 0.02:
                return True
        self._path.append((gx, gy))
        self.area.queue_draw()
        return True

    def on_release(self, _w, ev) -> bool:
        self._pressing = False

        if self._bar_index is not None:
            self._pick_candidate(self._bar_index)
            self._bar_index = None
            self._path = []
            self.area.queue_draw()
            return True

        key = self._press_key
        swiped = (
            key is not None
            and key.is_letter
            and len(self._path) > 1
            and not self.decoder.is_tap(self._path)
        )
        if swiped:
            self._commit_swipe()
        elif key is not None:
            self._activate(key)

        self._path = []
        self._press_key = None
        self.area.queue_draw()
        return True

    # -- actions ---------------------------------------------------------

    def _activate(self, k: Key) -> None:
        if k.kind is KeyType.CHAR and k.payload == " ":
            self._space_tap()
            return
        if k.kind is KeyType.CHAR:
            face = k.shifted if (self.out.shift_active() and k.shifted) else k.payload
            if self.out.shift_active() and k.is_letter:
                face = k.payload.upper()
            self.out.char(face)
            self._last_word = None
            self._candidates = []
            self._wordish = face.isalnum()
            self._swallow_space = False
            self._space_at = None
        elif k.kind is KeyType.KEYSYM:
            self.out.key(k.payload)
            if k.payload == "BackSpace":
                self._last_word = None
            else:
                self._candidates = []
            self._wordish = False
            self._swallow_space = False
            self._space_at = None
        elif k.kind is KeyType.MOD:
            self.out.cycle_mod(k.payload)
        elif k.kind is KeyType.LAYER:
            self.layer = k.payload
        elif k.kind is KeyType.ACTION and k.payload == "hide":
            self.hide_board()

    def _now(self) -> float:
        return time.monotonic()  # separate so tests can fake the clock

    def _space_tap(self) -> None:
        """Phone-style space:

        - Ctrl/alt/super+space always passes straight through -- that combo is
          load-bearing in terminals (tmux prefix, emacs set-mark).
        - The first space after a decoded word is swallowed: the word already
          typed its trailing space, so the habitual tap would double it. The
          buffer is untouched, which also keeps candidate correction valid.
        - Two taps inside DOUBLE_SPACE_S after a letter/digit collapse the
          pending space to ". ". Only one space is ever in the buffer at that
          point, so a single backspace suffices.
        """
        if any(m != "shift" for m in self.out.active_mods()):
            self.out.char(" ")
            self._swallow_space = False
            self._space_at = None
            self._last_word = None
            self._candidates = []
            return
        now = self._now()
        if self._swallow_space:
            self._swallow_space = False
            self._space_at = now
            return
        if self._space_at is not None and now - self._space_at <= DOUBLE_SPACE_S:
            self.out.backspace(1)
            self.out.text(". ")
            self._space_at = None
            self._wordish = False
            self._last_word = None
            self._candidates = []
            return
        self.out.char(" ")
        self._space_at = now if self._wordish else None
        self._last_word = None
        self._candidates = []

    def _commit_swipe(self) -> None:
        ranked = self.decoder.decode(self._path, top=N_CANDIDATES)
        if not ranked:
            return
        words = [w for w, _ in ranked]
        if self.out.shift_active():
            words = [w.capitalize() for w in words]
        self._candidates = words
        self._insert_word(words[0])

    def _insert_word(self, word: str) -> None:
        self.out.text(word + " ")
        self._last_word = word
        self._swallow_space = True
        self._wordish = True
        self._space_at = None

    def _pick_candidate(self, idx: int) -> None:
        if idx >= len(self._candidates):
            return
        word = self._candidates[idx]
        if self._last_word is not None:
            self.out.backspace(len(self._last_word) + 1)
        self._insert_word(word)
        # Keep the bar up so a second correction is one tap away.
