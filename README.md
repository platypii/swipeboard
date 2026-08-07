# Swipeboard

A swipe-typing on-screen keyboard for X11, built for the `krunner` car tablet.

Onboard has no glide typing and upstream is dormant. The only real swipe stack on Linux
(`wvkbd -O | swipeGuess`) is a wlroots client and cannot attach to an X11 session. So this is
a complete OSK: swipe for words, tap for everything else, including modifiers, arrows and
Tab/Esc.

Output goes through `xdotool`, i.e. real X keystrokes — which is why it works in the
snap-packaged Firefox, where AT-SPI-based keyboards can't see the focused field at all.

## Requirements

Everything is already on Ubuntu 26.04 / MATE:

- `python3-gi` (GTK 3), `python3-cairo`
- `xdotool` (output), `xprop` (struts)
- `onboard-data` — for `/usr/share/onboard/models/en_US.lm`, read at runtime as the word list.
  Falls back to `/usr/share/dict/american-english` (no frequencies) if absent.

No numpy, no build step, no Rust toolchain.

## Run

```bash
./bin/swipeboard              # floating ⌨ toggle; board hidden until tapped
./bin/swipeboard --show       # start with the board up
./bin/swipeboard --dry-run    # print keystrokes instead of sending them
./bin/swipeboard --no-struts  # overlay instead of reserving screen space
```

`./install.sh` adds an autostart entry and a `.desktop` you can drag onto the MATE panel.

## Using it

- **Swipe** a word: press on its first letter, glide through the rest, lift. The word is
  inserted with a trailing space and five candidates appear in the top bar.
- **Tap a candidate** to correct: it backspaces the inserted word and types the new one.
- **Tap** any key normally — short drags still count as taps.
- **Modifiers latch**: tap `ctrl` → applies to the next key (blue ring); tap again → locked
  (amber ring); tap a third time → off. So Ctrl+T is two taps, not a chord.
- **Shift** applies to the next tap, or capitalises the next swiped word.
- `?123` switches layers; on the symbols layer `home`/`end` become `pgup`/`pgdn`. The arrows
  never move.
- `▼` hides the board; the floating ⌨ brings it back.

## Layout

Shaped for terminal work:

- **Left column** — `esc`, `tab`, `⇧`, `ctrl`, one per letter row, where a real keyboard has
  them. Ctrl-chords are two taps and never leave the left edge.
- **Right** — `⌫` and `▼` top, `⏎` below, then a proper inverted-T arrow cluster with
  `home`/`end` above it. Arrows stay put across layers.
- **Primary layer** carries `-`, `/` and `|` as well as `,` `.`, so paths, flags and pipes
  don't need a layer switch. Shift gives `_ ? \ < >`.
- The letters keep the standard QWERTY stagger (+0.5, +1.0). That has to be stated explicitly
  now that shift sits in the left column rather than indenting the row itself — get it wrong
  and `z` lands left of `a`, which costs both muscle memory and about 1.5 points of decoder
  accuracy.

The board is 4 key rows plus the candidate bar, at `BOARD_HEIGHT_FRAC` (0.36) of screen
height — deliberately low, since every row it occupies is a row of scrollback you can't see.

## How the decoder works

`decoder.py` implements SHARK² (Kristensson & Zhai, UIST 2004) and is deliberately pure —
points in, ranked words out, no GTK, no pixels. Everything is in *grid units* where one key
is 1×1, so tuning is resolution-independent and templates stay valid for the life of the
process.

1. **Endpoint prune** — first/last letter must be within `r_endpoint` of the gesture's start
   and end. A `(first, last) -> [words]` index makes this a dict hit, not a 29k scan.
2. **Corridor prune** — the word's letters must appear, in order, among the keys the path
   passed within `r_corridor` of. Falls back to the endpoint set if it leaves under 5
   candidates, so a clipped corner doesn't cost you the word.
3. **Shape channel** — both paths resampled to 64 points, centroid-translated and
   scale-normalised, mean per-point distance.
4. **Location channel** — same points, unnormalised, free inside `r_tunnel` and charged on
   the excess. This is what stops a short high-frequency word from matching a long gesture.
5. **Integrate** — `w_shape·shape + w_location·location − lam·log(count)`.

Consecutive duplicate letters collapse (`hello` → `h,e,l,o`), since a finger can't express
them. That makes `hello`/`helo` shape-identical and lets frequency break the tie, which is
correct.

## Accuracy

`python3 tools/eval_decoder.py` generates synthetic swipes — jittered control points,
Catmull-Rom rounded corners, realistic sample density, per-sample noise — and scores the
decoder against them.

At the tuned defaults (n=2000):

| sample | top-1 | top-3 | top-5 | decode |
|---|---|---|---|---|
| frequency-weighted | 86.2% | 95.2% | 97.0% | 3.9 ms mean, 12.3 ms p95 |
| uniform over vocabulary | 70.7% | 88.1% | 92.7% | 6.5 ms mean |

Frequency-weighted is what typing actually feels like, since you write `the` far more often
than `thew`. Uniform is the rare-word case, where the candidate bar does the work.

`lam` and `r_tunnel` both trade rare-word accuracy against common-word accuracy; the defaults
sit near the knee. Sweep any parameter with:

```bash
python3 tools/eval_decoder.py --sweep lam
python3 tools/eval_decoder.py --diag    # pruning recall, i.e. the accuracy ceiling
```

`--diag` is the one to reach for first when accuracy regresses: a word dropped by pruning can
never be ranked, so if corridor recall falls, no amount of channel tuning will help.

## Tools

| | |
|---|---|
| `tools/eval_decoder.py` | accuracy + latency + parameter sweeps + pruning diagnostics |
| `tools/test_interaction.py` | end-to-end input tests — synthetic events in, asserted xdotool argv out; no window, no real keystrokes |
| `tools/render_preview.py` | render the board to a PNG without mapping a window |

## Notes

- **Onboard is left installed and still autostarts.** Swipeboard has been tested headlessly
  and launched live, but not driven by a real finger on the touchscreen. Once you've
  confirmed it works in the car, disable the old one with
  `rm ~/.config/autostart/onboard.desktop`.
- **GTK3 specifically**, not GTK4: `set_accept_focus`, `set_keep_above` and window type hints
  were all removed in GTK4, and `set_accept_focus(False)` is the property the whole design
  rests on — without it the board takes X focus the moment you touch it and every keystroke
  goes nowhere. Both modules pin `gi.require_version("Gdk", "3.0")` because GTK4 is also
  installed and `gi` would otherwise pick it.
- The `en_US.lm` dependency is a read of an installed system file. If this is ever published,
  check the licensing of that data first (Onboard is GPLv3) or rebuild the frequency list
  from a permissive corpus.
