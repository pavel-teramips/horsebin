"""Word phrases for horsebin: generation + forgiving resolution of what a human typed.

Wordlist: EFF short wordlist #2, trimmed to words of <= 8 letters (949 words).
Every word is uniquely identified by its first three letters, so "fru lad oys"
resolves to "fruit ladder oyster". Typos are fixed by edit distance.
"""
import math
import re
import secrets
from pathlib import Path

WORDS = [w.strip() for w in (Path(__file__).with_name("words.txt")).read_text().split() if w.strip()]
WORDSET = set(WORDS)
PREFIX = {w[:3]: w for w in WORDS}
assert len(PREFIX) == len(WORDS), "wordlist lost its unique-3-letter-prefix property"
BITS_PER_WORD = math.log2(len(WORDS))


def generate(n=3):
    return [secrets.choice(WORDS) for _ in range(n)]


def _dist(a, b, cap=3):
    """Damerau-ish Levenshtein (with adjacent transposition), early-out above cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def resolve_token(tok):
    """One typed token -> a list word, or None."""
    if tok in WORDSET:
        return tok
    if len(tok) < 3:
        return None
    w = PREFIX.get(tok[:3])
    if w and (w.startswith(tok) or _dist(tok, w) <= max(1, len(w) // 3)):
        return w
    # first three letters are themselves mistyped: nearest word by edit distance
    cap = 1 if len(tok) <= 4 else 2
    best = [(d, w) for w in WORDS if (d := _dist(tok, w, cap)) <= cap]
    if not best:
        return None
    best.sort()
    if len(best) > 1 and best[0][0] == best[1][0]:
        return None  # ambiguous
    return best[0][1]


def _segment(s):
    """'fruitladderoyster' -> ['fruit','ladder','oyster'] using whole words only."""
    n = len(s)
    best = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for L in range(3, 9):
            if i + L <= n and s[i:i + L] in WORDSET and best[i + L] is None:
                best[i + L] = best[i] + [s[i:i + L]]
    return best[n]


def tokens(text):
    return re.findall(r"[a-z]+", text.lower())


def resolve(text):
    """Free-form typed phrase -> list of canonical words, or None."""
    toks = tokens(text)
    if len(toks) == 1 and len(toks[0]) > 8:
        seg = _segment(toks[0])
        if seg:
            toks = seg
    if not 3 <= len(toks) <= 4:
        return None
    out = [resolve_token(t) for t in toks]
    return None if None in out else out


def is_xkcd(text):
    return "".join(tokens(text)).startswith("correcthorse")


TEMPLATES3 = [
    "Picture a {0} wearing a {1} while riding a {2}.",
    "A {0} and a {1} walk into a {2}.",
    "Somewhere, a giant {0} is chasing a {1} through a {2}.",
    "Your {0} traded its {1} for a {2}. Bad deal.",
    "Breaking news: {0} found inside {1}, police blame {2}.",
    "The {0} hid the {1} under a {2}.",
]
TEMPLATES4 = [
    "A {0} and a {1} walk into a {2}. The {3} is the bartender.",
    "Picture a {0} wearing a {1}, riding a {2}, waving at a {3}.",
    "Breaking news: {0} found inside {1}, police blame {2} and {3}.",
    "The {0} hid the {1} under a {2}, but the {3} saw everything.",
]


def mnemonic(words):
    t = TEMPLATES4 if len(words) == 4 else TEMPLATES3
    h = sum(map(ord, "".join(words)))
    return re.sub(r"\b([Aa]) ([aeiou])", r"\1n \2", t[h % len(t)].format(*words))
