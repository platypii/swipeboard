"""SHARK²-style swipe decoder.

Implements Kristensson & Zhai (UIST 2004): sample -> prune -> shape channel +
location channel -> integrate with a frequency prior.

This module is deliberately pure: points in, ranked words out. It imports no
GTK and knows nothing about pixels -- everything is in grid units, where 1.0 is
one key width. That keeps tuning resolution-independent, lets the template
cache live forever (geometry never changes), and leaves the door open to
swapping this for a compiled extension without touching the UI.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]

N_SAMPLES = 64


@dataclass
class Params:
    """Defaults tuned against tools/eval_decoder.py. See README for the numbers.

    The frequency prior (lam) and the location tunnel both trade rare-word
    accuracy against common-word accuracy; these values sit near the knee, since
    real typing is frequency-distributed but rare words still have to feel
    reachable via the candidate bar.
    """
    # Pruning
    r_endpoint: float = 1.2    # gesture start/end must be this close to first/last key
    r_corridor: float = 1.2    # a key counts as "passed through" within this radius
    min_candidates: int = 5    # below this, fall back to the endpoint-only set
    # Channels
    w_shape: float = 1.0
    w_location: float = 1.0
    r_tunnel: float = 0.6      # location cost is free inside this radius
    # Frequency prior
    lam: float = 0.03
    # Tap discrimination
    tap_path_len: float = 0.5  # shorter than this is a keypress, not a gesture


def _resample(pts: list[Point], n: int = N_SAMPLES) -> list[Point]:
    """Arc-length resample a polyline to exactly n equidistant points."""
    if not pts:
        return [(0.0, 0.0)] * n
    if len(pts) == 1:
        return [pts[0]] * n

    seg = [0.0]
    total = 0.0
    for i in range(1, len(pts)):
        total += math.dist(pts[i - 1], pts[i])
        seg.append(total)
    if total < 1e-9:
        return [pts[0]] * n

    step = total / (n - 1)
    out: list[Point] = [pts[0]]
    j = 1
    for i in range(1, n - 1):
        target = i * step
        while j < len(seg) - 1 and seg[j] < target:
            j += 1
        prev = seg[j - 1]
        span = seg[j] - prev
        t = 0.0 if span < 1e-12 else (target - prev) / span
        x0, y0 = pts[j - 1]
        x1, y1 = pts[j]
        out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    out.append(pts[-1])
    return out


def _normalize(pts: list[Point]) -> list[Point]:
    """Translate to centroid and scale to unit extent -- the shape channel's view,
    which deliberately discards where on the board the gesture happened."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    if extent < 1e-9:
        extent = 1.0
    s = 1.0 / extent
    return [((p[0] - cx) * s, (p[1] - cy) * s) for p in pts]


def path_length(pts: list[Point]) -> float:
    return sum(math.dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def _collapse(word: str) -> str:
    """Consecutive duplicate letters share a key, so the finger cannot express
    them: hello -> helo. This makes hello/helo shape-identical and lets the
    frequency prior pick, which is the correct behaviour."""
    out = [word[0]]
    for ch in word[1:]:
        if ch != out[-1]:
            out.append(ch)
    return "".join(out)


class Decoder:
    def __init__(
        self,
        words: list[tuple[str, int]],
        centers: dict[str, Point],
        params: Params | None = None,
    ) -> None:
        self.p = params or Params()
        self.centers = centers
        self._letters = list(centers.items())

        self.words: list[str] = []
        self.counts: list[float] = []
        self.collapsed: list[str] = []
        # (first, last) -> [index]. Turns endpoint pruning into a dict hit
        # instead of a 29k linear scan.
        self.index: dict[tuple[str, str], list[int]] = {}

        for word, count in words:
            # word may carry display casing (Kenny); geometry is lowercase-only.
            col = _collapse(word.lower())
            if len(col) < 2 or any(c not in centers for c in col):
                continue
            i = len(self.words)
            self.words.append(word)
            self.counts.append(math.log(count + 1.0))
            self.collapsed.append(col)
            self.index.setdefault((col[0], col[-1]), []).append(i)

        # Raw *and* normalised, because the board geometry never changes: a
        # template computed once is valid for the life of the process.
        self._template_cache: dict[str, tuple[list[Point], list[Point]]] = {}

    # -- templates -------------------------------------------------------

    def _template(self, collapsed: str) -> tuple[list[Point], list[Point]]:
        t = self._template_cache.get(collapsed)
        if t is None:
            raw = _resample([self.centers[c] for c in collapsed])
            t = (raw, _normalize(raw))
            self._template_cache[collapsed] = t
        return t

    def warm_cache(self) -> None:
        """Precompute every template. ~1 s, worth paying at startup so the first
        swipe is as fast as the thousandth."""
        for col in self.collapsed:
            self._template(col)

    # -- pruning ---------------------------------------------------------

    def _letters_near(self, pt: Point, radius: float) -> list[str]:
        r2 = radius * radius
        x, y = pt
        return [
            ch for ch, (cx, cy) in self._letters
            if (cx - x) ** 2 + (cy - y) ** 2 <= r2
        ]

    def _prune_endpoints(self, path: list[Point]) -> list[int]:
        starts = self._letters_near(path[0], self.p.r_endpoint)
        ends = self._letters_near(path[-1], self.p.r_endpoint)
        out: list[int] = []
        for s in starts:
            for e in ends:
                out.extend(self.index.get((s, e), ()))
        return out

    def _prune_corridor(self, cands: list[int], path: list[Point]) -> list[int]:
        """Keep words whose letters appear, in order, among the keys the path
        passed near. Greedy pointer walk -- a key may be matched at any point
        at or after the previous one."""
        near = [set(self._letters_near(pt, self.p.r_corridor)) for pt in path]
        out: list[int] = []
        for i in cands:
            col = self.collapsed[i]
            k = 0
            for s in near:
                if col[k] in s:
                    k += 1
                    if k == len(col):
                        break
            if k == len(col):
                out.append(i)
        return out

    # -- channels --------------------------------------------------------

    def _score(self, i: int, g_raw: list[Point], g_norm: list[Point]) -> float:
        t_raw, t_norm = self._template(self.collapsed[i])

        n = len(g_raw)
        shape = 0.0
        for a, b in zip(g_norm, t_norm):
            shape += math.dist(a, b)
        shape /= n

        loc = 0.0
        r = self.p.r_tunnel
        for a, b in zip(g_raw, t_raw):
            d = math.dist(a, b)
            if d > r:
                loc += d - r
        loc /= n

        return (
            self.p.w_shape * shape
            + self.p.w_location * loc
            - self.p.lam * self.counts[i]
        )

    # -- public ----------------------------------------------------------

    def is_tap(self, path: list[Point]) -> bool:
        return path_length(path) < self.p.tap_path_len

    def decode(self, path: list[Point], top: int = 5) -> list[tuple[str, float]]:
        """Rank words for a gesture. Lower score is better."""
        if len(path) < 2:
            return []

        g_raw = _resample(path)
        endpoint_set = self._prune_endpoints(g_raw)
        if not endpoint_set:
            return []

        cands = self._prune_corridor(endpoint_set, g_raw)
        if len(cands) < self.p.min_candidates:
            # A clipped corner should not cost us the word.
            cands = endpoint_set

        g_norm = _normalize(g_raw)
        scored = [(self.words[i], self._score(i, g_raw, g_norm)) for i in cands]
        scored.sort(key=lambda t: t[1])
        return scored[:top]
