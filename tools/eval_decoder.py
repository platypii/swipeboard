#!/usr/bin/env python3
"""Measure decoder accuracy against synthetic swipes.

Real fingers do not trace polylines through key centres: they overshoot, cut
corners, and round every turn. The generator here models that -- jittered
control points, Catmull-Rom rounding, realistic sample density, per-sample
noise -- so weights and radii can be tuned before any UI exists.

Two numbers matter. Uniform sampling says how the decoder does across the whole
vocabulary; frequency-weighted sampling says what typing actually feels like,
because you write "the" far more often than "thew".

    python3 tools/eval_decoder.py                 # default run
    python3 tools/eval_decoder.py --n 4000 --sweep lam
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swipeboard import lexicon  # noqa: E402
from swipeboard.decoder import Decoder, Params, _collapse  # noqa: E402
from swipeboard.layout import letter_centers  # noqa: E402

Point = tuple[float, float]


def _catmull_rom(pts: list[Point], per_seg: int) -> list[Point]:
    """Round the corners. Duplicated endpoints give natural start/end tangents."""
    if len(pts) < 2:
        return list(pts)
    ext = [pts[0]] + list(pts) + [pts[-1]]
    out: list[Point] = []
    for i in range(len(ext) - 3):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for s in range(per_seg):
            t = s / per_seg
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
            ))
    out.append(pts[-1])
    return out


def synth_swipe(
    word: str,
    centers: dict[str, Point],
    rng: random.Random,
    vertex_sigma: float = 0.35,
    sample_sigma: float = 0.05,
) -> list[Point]:
    col = _collapse(word)
    ctrl = []
    for ch in col:
        cx, cy = centers[ch]
        ctrl.append((cx + rng.gauss(0, vertex_sigma), cy + rng.gauss(0, vertex_sigma)))

    # ~6 samples per key hop, i.e. a ~60Hz panel and a brisk swipe.
    pts = _catmull_rom(ctrl, per_seg=6)
    return [(x + rng.gauss(0, sample_sigma), y + rng.gauss(0, sample_sigma)) for x, y in pts]


def sample_words(
    words: list[tuple[str, int]], n: int, rng: random.Random, weighted: bool
) -> list[str]:
    pool = [(w, c) for w, c in words if len(_collapse(w)) >= 2]
    if weighted:
        weights = [c for _, c in pool]
        return [w for w, _ in rng.choices(pool, weights=weights, k=n)]
    return [w for w, _ in rng.sample(pool, min(n, len(pool)))]


def evaluate(dec: Decoder, targets: list[str], centers, rng: random.Random) -> dict:
    hits = [0, 0, 0]  # top-1, top-3, top-5
    cand_counts = []
    latencies = []
    misses: list[tuple[str, list[str]]] = []

    for word in targets:
        path = synth_swipe(word, centers, rng)
        t0 = time.perf_counter()
        ranked = dec.decode(path, top=5)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        names = [w for w, _ in ranked]
        cand_counts.append(len(names))
        if names[:1] == [word]:
            hits[0] += 1
        if word in names[:3]:
            hits[1] += 1
        if word in names[:5]:
            hits[2] += 1
        elif len(misses) < 15:
            misses.append((word, names[:3]))

    n = len(targets)
    latencies.sort()
    return {
        "n": n,
        "top1": hits[0] / n,
        "top3": hits[1] / n,
        "top5": hits[2] / n,
        "lat_mean": sum(latencies) / n,
        "lat_p95": latencies[int(0.95 * (n - 1))],
        "lat_max": latencies[-1],
        "misses": misses,
    }


def diagnose(dec: Decoder, targets: list[str], centers, rng: random.Random) -> None:
    """Pruning is a hard ceiling: a word dropped here can never be ranked. Report
    survival at each stage so tuning effort goes where the loss actually is."""
    idx = {w: i for i, w in enumerate(dec.words)}
    ep_hit = co_hit = 0
    ep_sizes, co_sizes = [], []

    for word in targets:
        path = synth_swipe(word, centers, rng)
        from swipeboard.decoder import _resample
        g = _resample(path)
        ep = dec._prune_endpoints(g)
        co = dec._prune_corridor(ep, g)
        ep_sizes.append(len(ep))
        co_sizes.append(len(co))
        i = idx.get(word)
        if i is not None and i in ep:
            ep_hit += 1
            if i in co:
                co_hit += 1

    n = len(targets)
    print(f"  endpoint prune: recall {ep_hit / n:.1%}  mean {sum(ep_sizes) / n:.0f} cands")
    print(f"  corridor prune: recall {co_hit / n:.1%}  mean {sum(co_sizes) / n:.0f} cands")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260806)
    ap.add_argument("--sweep", choices=["lam", "w_location", "r_corridor", "r_tunnel"])
    ap.add_argument("--diag", action="store_true", help="report pruning recall ceiling")
    args = ap.parse_args()

    centers = letter_centers()
    words = lexicon.load()
    print(f"lexicon: {len(words)} words")

    t0 = time.perf_counter()
    dec = Decoder(words, centers)
    print(f"index built in {(time.perf_counter() - t0) * 1000:.0f} ms "
          f"({len(dec.words)} swipeable)\n")

    rng = random.Random(args.seed)
    uniform = sample_words(words, args.n, rng, weighted=False)
    weighted = sample_words(words, args.n, rng, weighted=True)

    if args.sweep:
        grids = {
            "lam": [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12],
            "w_location": [0.0, 0.2, 0.4, 0.6, 1.0, 1.5],
            "r_corridor": [0.6, 0.75, 0.85, 1.0, 1.2],
            "r_tunnel": [0.0, 0.25, 0.5, 0.75, 1.0],
        }
        print(f"{args.sweep:>12}  uni-top1  wtd-top1  wtd-top3   ms")
        for v in grids[args.sweep]:
            p = Params()
            setattr(p, args.sweep, v)
            dec.p = p
            u = evaluate(dec, uniform, centers, random.Random(args.seed))
            w = evaluate(dec, weighted, centers, random.Random(args.seed))
            print(f"{v:>12}  {u['top1']:>8.1%}  {w['top1']:>8.1%}  "
                  f"{w['top3']:>8.1%}  {w['lat_mean']:>4.1f}")
        return 0

    for label, targets in (("uniform", uniform), ("frequency-weighted", weighted)):
        if args.diag:
            print(f"--- {label} pruning ---")
            diagnose(dec, targets, centers, random.Random(args.seed))
            print()
            continue
        r = evaluate(dec, targets, centers, random.Random(args.seed))
        print(f"--- {label} (n={r['n']}) ---")
        print(f"  top-1 {r['top1']:.1%}   top-3 {r['top3']:.1%}   top-5 {r['top5']:.1%}")
        print(f"  decode  mean {r['lat_mean']:.1f} ms   p95 {r['lat_p95']:.1f} ms   "
              f"max {r['lat_max']:.1f} ms")
        if r["misses"]:
            print("  sample misses (target -> top3):")
            for w, got in r["misses"][:8]:
                print(f"    {w:<16} {got}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
