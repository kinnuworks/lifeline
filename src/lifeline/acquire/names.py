"""Latin-script names for places mapped only in Devanagari.

Most villages in the Nepal hills are tagged in OpenStreetMap with a Devanagari
`name` and no `name:en`. English answers, the map and the report need a
readable Latin form, so we romanise the way Nepali place names are usually
spelt on road signs (Bidur, Dhunche, Gatlang): no long-vowel marks, the
inherent vowel dropped at the end of a word and between a vowel and a
consonant-vowel pair. The Devanagari original is kept as `name_ne`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "w", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "ऱ": "r",
}
VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
MATRA = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o"}
VIRAMA, NUKTA = "्", "़"
NASAL = {"ं", "ँ"}
DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _word(w: str) -> str:
    # syllables as [consonants, vowel, tail]; vowel None means the inherent "a"
    syl: list[list] = []
    i = 0
    while i < len(w):
        ch = w[i]
        if ch in CONS:
            cons = CONS[ch]
            i += 1
            if i < len(w) and w[i] == NUKTA:
                i += 1
            if syl and syl[-1][1] == "" and not syl[-1][2]:   # previous ended in a virama: a cluster
                syl[-1][0] += cons
                syl[-1][1] = None
                syl[-1][3] = True
            else:
                syl.append([cons, None, "", False])
        elif ch in MATRA and syl:
            syl[-1][1] = MATRA[ch]
            i += 1
        elif ch == VIRAMA and syl:
            syl[-1][1] = ""
            i += 1
        elif ch in VOWELS:
            syl.append(["", VOWELS[ch], "", False])
            i += 1
        elif ch in NASAL and syl:
            syl[-1][2] += "n"
            i += 1
        elif ch == "ः" and syl:
            syl[-1][2] += "h"
            i += 1
        else:
            syl.append([ch.translate(DIGITS), "", "", False])
            i += 1

    n = len(syl)
    keep = [True] * n
    for k in range(n - 1, -1, -1):   # right to left, as the inherent vowel is dropped in speech
        c, v, tail, cluster = syl[k]
        if v is not None or tail or not c or cluster:
            continue
        if k == n - 1 and n > 1:
            keep[k] = False
        elif 0 < k < n - 1:
            nxt = syl[k + 1]
            next_voiced = nxt[0] and (nxt[1] not in (None, "") or (nxt[1] is None and keep[k + 1]))
            if syl[k - 1][1] != "" and next_voiced:
                keep[k] = False
    out = []
    for k, (c, v, tail, _) in enumerate(syl):
        vowel = v if v is not None else ("a" if keep[k] else "")
        # a nasal sign before a nasal consonant is already spoken by that consonant
        if tail == "n" and k + 1 < n and syl[k + 1][0][:1] in ("n", "m"):
            tail = ""
        out.append(c + vowel + tail)
    return "".join(out)


def romanise(text: str) -> str:
    text = text.replace("\u200c", "").replace("\u200d", "")
    parts = re.split(r"(\s+|[-/(),])", text)
    words = [_word(p) if DEVANAGARI.search(p) else p for p in parts]
    s = "".join(words).replace("nyj", "nj").replace("nych", "nch")
    return " ".join(w[:1].upper() + w[1:] if w[:1].isalpha() else w for w in s.split(" "))


def latin_name(name: str | None, name_en: str | None = None) -> tuple[str, str]:
    """(latin name, devanagari name) for a raw OSM name."""
    name = (name or "").strip()
    if name_en and name_en.strip():
        return name_en.strip(), name if DEVANAGARI.search(name) else ""
    if DEVANAGARI.search(name):
        return romanise(name), name
    return name, ""


def normalise_file(path: Path) -> int:
    """Rewrite a places or health GeoJSON in place; returns how many names were romanised."""
    if not path.exists():
        return 0
    fc = json.loads(path.read_text())
    changed = 0
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        nm = p.get("name") or ""
        if p.get("name_auto") and DEVANAGARI.search(p.get("name_ne") or ""):
            nm = p["name_ne"]   # romanised earlier: redo it with the current rules
        if DEVANAGARI.search(nm):
            latin, ne = latin_name(nm)
            p["name"] = latin
            p["name_auto"] = True
            if not p.get("name_ne"):
                p["name_ne"] = ne
            changed += 1
    if path.name == "places.geojson":
        before = len(fc.get("features", []))
        fc["features"] = _dedupe(fc.get("features", []))
        changed += before - len(fc["features"])
    if changed:
        path.write_text(json.dumps(fc))
    return changed


RANK = {"city": 0, "town": 1, "village": 2, "suburb": 3, "hamlet": 4, "isolated_dwelling": 5, "locality": 6, "neighbourhood": 7}


def _dedupe(feats: list, within_m: float = 500.0) -> list:
    """The same place mapped twice (a village node and a hamlet node 20 m apart) counts once."""
    import math

    keep: list = []
    for f in sorted(feats, key=lambda f: RANK.get((f.get("properties") or {}).get("place"), 9)):
        p = f.get("properties") or {}
        x, y = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        dup = False
        for g in keep:
            q = g.get("properties") or {}
            if (q.get("name") or "").lower() != (p.get("name") or "").lower():
                continue
            gx, gy = g["geometry"]["coordinates"][:2]
            if x is not None and math.hypot((x - gx) * 111320 * math.cos(math.radians(y)), (y - gy) * 110540) <= within_m:
                dup = True
                break
        if not dup:
            keep.append(f)
    return keep
