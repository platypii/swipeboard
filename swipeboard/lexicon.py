"""Vocabulary + word frequencies.

Source is Onboard's language model, an ARPA-style text file that ships with the
`onboard-data` package. We only want the unigram section: ~29.5k lowercase
words with real corpus counts (the=83.8M, hello=765), which is exactly the
frequency prior a swipe decoder needs.

Read at runtime from the installed system path -- nothing is vendored here.
"""
from __future__ import annotations

import os
import pickle
import re
from pathlib import Path

LM_PATH = Path("/usr/share/onboard/models/en_US.lm")
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "swipeboard"
CACHE_PATH = CACHE_DIR / "lexicon.pkl"

# Unigram lines look like "83800117 the". Anything with capitals, digits or
# punctuation (<unk>, <num>, proper nouns, acronyms) is not swipeable on a
# letters-only board, so it is dropped.
_UNIGRAM = re.compile(r"^(\d+) ([a-z]+)$")

MIN_LEN = 2


def _parse(path: Path) -> list[tuple[str, int]]:
    words: list[tuple[str, int]] = []
    in_unigrams = False
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("\\"):
                if line.startswith("\\1-grams"):
                    in_unigrams = True
                    continue
                if in_unigrams:
                    break  # hit \2-grams: or \end\
                continue
            if not in_unigrams:
                continue
            m = _UNIGRAM.match(line)
            if m and len(m.group(2)) >= MIN_LEN:
                words.append((m.group(2), int(m.group(1))))
    return words


def load(path: Path = LM_PATH) -> list[tuple[str, int]]:
    """[(word, count)], newest-cache-wins. Falls back to /usr/share/dict if the
    Onboard model is missing (no frequencies there, so counts are flat)."""
    if not path.exists():
        return _fallback_wordlist()

    st = path.stat()
    stamp = (str(path), st.st_mtime_ns, st.st_size)
    try:
        with CACHE_PATH.open("rb") as fh:
            cached_stamp, cached_words = pickle.load(fh)
        if cached_stamp == stamp:
            return cached_words
    except (OSError, EOFError, ValueError, pickle.UnpicklingError):
        pass

    words = _parse(path)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".pkl.tmp")
        with tmp.open("wb") as fh:
            pickle.dump((stamp, words), fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(CACHE_PATH)
    except OSError:
        pass  # cache is an optimisation, not a requirement
    return words


def _fallback_wordlist() -> list[tuple[str, int]]:
    p = Path("/usr/share/dict/american-english")
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            w = line.strip()
            if len(w) >= MIN_LEN and w.isalpha() and w.islower():
                out.append((w, 1))
    return out


if __name__ == "__main__":
    ws = load()
    print(f"{len(ws)} words")
    print("most frequent:", [w for w, _ in sorted(ws, key=lambda t: -t[1])[:12]])
