"""The number guard.

Finds every numeric expression in an English or Nepali text and checks it against
the fact ledger. A number passes only if it equals a ledger fact (within that
fact's display rounding, with a compatible unit) or is a harmless token such as
"Sentinel-1", activation codes, the year 2026, a fact ID like [F012] or a list marker.

Extracted: Latin and Devanagari digits, decimals, comma groups (1,234 and
1,23,456), percentages, English and Nepali number words (twelve, a dozen,
बाह्र, तीन सय, पाँचवटा), romanised Nepali number words, ordinals, dates, times,
vague magnitudes (hundreds, हजारौं), ratios (half, twice, दोब्बर), comparative
qualifiers (more than 12, १२ भन्दा बढी) and numbers next to casualty words.

Rules:
- casualty: any number near a casualty word, and any casualty statement that is
  not a refusal, is a violation. The system has no casualty data.
- vague / ratio: always a violation (not a ledger figure).
- qualifier: "more than", "over", "at least", "nearly" ... next to a number is a
  violation, because it changes the meaning of a ledger figure.
- time of day: always a violation (no ledger fact is a clock time).
- date: must equal a date fact in the ledger.
- other numbers: must match a fact in the allowed set (default: the whole
  ledger) with a compatible unit. Counts match exactly. Measures match if the
  fact rounded to the number of decimals written equals the written number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterable

from .ledger import Ledger, is_numeric_fact
from .nepali import (MONTHS_BS, MONTHS_EN, MONTHS_NE, NE_FRACTIONS, NE_MULT_VALUES, NE_ORDINAL_VALUES,
                     NE_WORD_VALUES, norm_ne, to_latin)

WHITELIST_YEARS = {2026}

FACT_UNIT_CLASS = {
    "villages": "settlement", "people": "people", "buildings": "building", "bridges": "bridge",
    "km": "km", "ha": "ha", "km2": "km2", "percent": "percent", "minutes": "minutes",
    "facilities": "facility", "hospitals": "facility", "chokepoints": "chokepoint", "rank": "rank", "track": "track",
}

UNIT_WORDS = {
    # english
    "village": "settlement", "villages": "settlement", "settlement": "settlement", "settlements": "settlement",
    "community": "settlement", "communities": "settlement", "hamlet": "settlement", "hamlets": "settlement",
    "town": "settlement", "towns": "settlement",
    "people": "people", "person": "people", "persons": "people", "resident": "people", "residents": "people",
    "inhabitants": "people", "individuals": "people", "villagers": "people",
    "family": "household", "families": "household", "household": "household", "households": "household",
    "building": "building", "buildings": "building", "house": "building", "houses": "building",
    "home": "building", "homes": "building", "structure": "building", "structures": "building",
    "bridge": "bridge", "bridges": "bridge",
    "km": "km", "kms": "km", "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "kmq": "km2",
    "ha": "ha", "hectare": "ha", "hectares": "ha",
    "%": "percent", "percent": "percent", "pct": "percent",
    "minute": "minutes", "minutes": "minutes", "min": "minutes", "mins": "minutes",
    "hour": "duration", "hours": "duration", "hrs": "duration", "hr": "duration", "day": "duration",
    "days": "duration", "week": "duration", "weeks": "duration", "month": "duration", "months": "duration",
    "year": "duration", "years": "duration",
    "facility": "facility", "facilities": "facility", "hospital": "facility", "hospitals": "facility",
    "clinic": "facility", "clinics": "facility",
    "road": "road", "roads": "road", "metres": "metre", "meters": "metre", "m": "metre",
    "chokepoint": "chokepoint", "chokepoints": "chokepoint",
    # romanised nepali
    "gaun": "settlement", "gau": "settlement", "gaon": "settlement", "basti": "settlement",
    "jana": "people", "manche": "people", "manchhe": "people", "ghar": "building", "pul": "bridge",
    # nepali
    "गाउँ": "settlement", "गाउँहरू": "settlement", "गाउ": "settlement", "बस्ती": "settlement", "बस्तीहरू": "settlement",
    "जना": "people", "मानिस": "people", "व्यक्ति": "people", "जनता": "people", "बासिन्दा": "people",
    "परिवार": "household", "घरधुरी": "household",
    "घर": "building", "घरहरू": "building", "भवन": "building", "भवनहरू": "building", "संरचना": "building",
    "पुल": "bridge", "पुलहरू": "bridge",
    "किलोमिटर": "km", "किमि": "km", "हेक्टर": "ha", "प्रतिशत": "percent", "मिनेट": "minutes",
    "घण्टा": "duration", "घन्टा": "duration", "दिन": "duration", "हप्ता": "duration", "महिना": "duration", "वर्ष": "duration",
    "अस्पताल": "facility", "स्वास्थ्य": "facility",
    "सडक": "road", "बाटो": "road", "मिटर": "metre",
}
CLASSIFIERS = {"वटा", "ओटा", "टा", "wata", "ota", "ota", "vata"}
SKIP_WORDS = {
    "of", "the", "a", "an", "more", "other", "total", "in", "all", "these", "those", "are", "were", "separate",
    "additional", "likely", "possibly", "remaining", "small", "large", "major", "main", "damaged", "destroyed",
    "cut", "off", "isolated", "flooded", "affected", "named", "mapped", "road", "health", "and", "or",
    "square", "sq", "वर्ग", "थप", "ठूला", "साना", "प्रभावित", "भएका", "ध्वस्त",
}

EN_SMALL = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
           "eighty": 80, "ninety": 90}
EN_MULT = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "crore": 10_000_000,
           "crores": 10_000_000, "million": 1_000_000, "billion": 1_000_000_000, "dozen": 12,
           "k": 1000}
EN_VAGUE = {"hundreds", "thousands", "dozens", "millions", "scores", "tens", "lakhs_of"}
EN_RATIO = {"half": 0.5, "twice": 2, "double": 2, "doubled": 2, "triple": 3, "tripled": 3, "quarter": 0.25,
            "thrice": 3, "halved": 0.5}
EN_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
          "ninth": 9, "tenth": 10}
ROMAN_NE = {"ek": 1, "dui": 2, "tin": 3, "teen": 3, "char": 4, "chaar": 4, "panch": 5, "paanch": 5, "chha": 6,
            "saat": 7, "sat": 7, "aath": 8, "nau": 9, "das": 10, "dus": 10, "eghara": 11, "egara": 11, "barha": 12,
            "baara": 12, "terha": 13, "chaudha": 14, "pandhra": 15, "sorha": 16, "satra": 17, "athara": 18,
            "unnais": 19, "bis": 20, "bees": 20, "tis": 30, "chalis": 40, "pachas": 50, "saathi": 60,
            "sattari": 70, "asi": 80, "nabbe": 90}
ROMAN_MULT = {"saya": 100, "say": 100, "sae": 100, "hajar": 1000, "hazar": 1000, "hajaar": 1000, "lakh": 100_000}
# Words that are numbers only when a unit, classifier or multiplier follows.
AMBIGUOUS = {"one", "एक", "छ", "नौ", "सत्र", "ek", "tin", "char", "sat", "das", "bis", "tis", "asi", "nau", "say"}
NE_VAGUE = {norm_ne(w) for w in ("सयौं", "सयौँ", "हजारौं", "हजारौँ", "लाखौं", "लाखौँ", "दर्जनौं", "दर्जनौँ", "करोडौं")}
NE_RATIO = {norm_ne(w): v for w, v in (("आधा", 0.5), ("दोब्बर", 2), ("तेब्बर", 3), ("चौथाइ", 0.25))}
NE_SUFFIXES = ("वटा", "ओटा", "जना", "टा", "वटै", "ओटै", "टै")

CASUALTY = {
    "dead", "death", "deaths", "died", "die", "dies", "killed", "kill", "fatalities", "fatality", "fatal",
    "casualties", "casualty", "injured", "injuries", "injury", "missing", "bodies", "deceased", "victims",
    "toll", "drowned", "perished",
    "मृत्यु", "मृतक", "मरे", "मरेका", "मर्यो", "मारिए", "घाइते", "बेपत्ता", "हताहत", "शव", "ज्यान", "मृत",
    "marey", "mareko", "mare", "mrityu", "ghaite", "bepatta", "maryo", "marechan",
}
_CAS_NORM = {norm_ne(c) for c in CASUALTY}
REFUSAL_MARKERS_EN = ("cannot", "can't", "can not", "unable", "not available", "don't have", "do not have",
                      "does not show", "doesn't show", "no data", "no information", "not in the", "not part of",
                      "do not report", "don't report", "won't", "will not", "not something")
REFUSAL_MARKERS_NE = ("सक्दिनँ", "सक्दिन", "सक्दैन", "सक्दैनौं", "छैन", "देखाउँदैन", "दिँदैन", "गर्दैन", "उपलब्ध छैन")

QUAL_BEFORE = {
    ("more", "than"): "more than", ("over",): "over", ("above",): "above", ("at", "least"): "at least",
    ("nearly",): "nearly", ("almost",): "almost", ("under",): "under", ("fewer", "than"): "fewer than",
    ("less", "than"): "less than", ("up", "to"): "up to", ("upwards", "of"): "upwards of",
    ("close", "to"): "close to", ("at", "most"): "at most", ("beyond",): "beyond", ("exceeds",): "exceeds",
    ("exceeding",): "exceeding", ("कम्तीमा",): "कम्तीमा", ("झण्डै",): "झण्डै", ("झन्डै",): "झन्डै",
    ("बढीमा",): "बढीमा", ("नजिक",): "नजिक",
}
QUAL_AFTER = {("भन्दा", "बढी"): "भन्दा बढी", ("भन्दा", "धेरै"): "भन्दा धेरै", ("भन्दा", "कम"): "भन्दा कम",
              ("+",): "+", ("or", "more"): "or more", ("plus",): "plus", ("or", "so"): "or so"}

_MONTHS_EN = {m.lower(): i + 1 for i, m in enumerate(MONTHS_EN)}
_MONTHS_EN.update({m.lower()[:3]: i + 1 for i, m in enumerate(MONTHS_EN)})
_MONTHS_EN["sept"] = 9
del _MONTHS_EN["may"]  # "12 may be cut off" is not a date; ISO dates still cover May
_MONTHS_NE = {m: i + 1 for i, m in enumerate(MONTHS_NE)}
_MONTHS_NE.update({"अगष्ट": 8, "अगस्त": 8, "सेप्टेम्बर": 9, "सेप्टेम्वर": 9, "अक्टूबर": 10, "जनवरि": 1})
_MONTH_ALT = "|".join(sorted([re.escape(m) for m in list(_MONTHS_EN) + list(_MONTHS_NE)], key=len, reverse=True))
_BS_ALT = "|".join(sorted(map(re.escape, MONTHS_BS), key=len, reverse=True))
_NB = r"(?![\wऀ-ॿ])"   # word end that also works after Devanagari marks
_NA = r"(?<![\wऀ-ॿ])"  # word start

_DATE_PATTERNS = [
    ("iso", re.compile(rf"{_NA}(\d{{4}})-(\d{{1,2}})-(\d{{1,2}}){_NB}")),
    ("dmy", re.compile(rf"{_NA}(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_ALT})\.?{_NB}(?:,?\s+(\d{{4}}){_NB})?", re.I)),
    ("mdy", re.compile(rf"{_NA}({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?{_NB}(?:,?\s+(\d{{4}}){_NB})?", re.I)),
    ("my", re.compile(rf"{_NA}({_MONTH_ALT})\.?,?\s+(\d{{4}}){_NB}", re.I)),
    ("num", re.compile(rf"{_NA}(\d{{1,2}})[/.](\d{{1,2}})[/.](\d{{2,4}}){_NB}")),
    ("bs", re.compile(rf"{_NA}(?:(\d{{1,4}})\s+)?({_BS_ALT}){_NB}(?:\s+(\d{{1,4}}))?(?:\s+(\d{{1,2}}))?")),
]
_TIME_PATTERNS = [
    re.compile(rf"{_NA}\d{{1,2}}:\d{{2}}(?:\s*(?:am|pm|a\.m\.|p\.m\.))?", re.I),
    re.compile(rf"{_NA}\d{{1,2}}\s*(?:am|pm|a\.m\.|p\.m\.|o'clock){_NB}", re.I),
    re.compile(r"\d{1,2}\s*बजे"),
]
_WHITELIST = [
    re.compile(r"\bF\d{3,}\b"),
    re.compile(r"\bsentinel[-\s]?\d[a-d]?\b", re.I),
    re.compile(r"\bS-?1[A-D]?\b"),
    re.compile(r"\bS-?2[A-C]?\b"),
    re.compile(r"\b[A-Z]{4}\d{3}\b"),  # activation or product codes (four capitals + three digits)
    re.compile(r"\b(?:GLO|COP|WorldDEM|DEM)[-\s]?\d+\b", re.I),
    re.compile(r"\bEPSG:?\s?\d+\b", re.I),
    re.compile(r"\blandsat[-\s]?\d+\b", re.I),
    re.compile(r"सेन्टिनेल[-\s]?\d[a-dA-D]?"),
    re.compile(r"\b[A-Z]{1,3}\d{1,3}\b"),  # road refs like H21, F20 style codes are not quantities
]
_LIST_MARKER = re.compile(r"(?m)^\s*\(?([1-9])[.)]\s")

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.\d+)?"
_TOKEN = re.compile(
    rf"(?P<num>{_NUM})(?P<ordsuf>(?:st|nd|rd|th){_NB})?"
    r"|(?P<word>[A-Za-z]+(?:'[a-z]+)?)"
    r"|(?P<dev>[ऀ-ॣ॰-ॿ]+)"
    r"|(?P<sym>[%²+])"
    r"|(?P<stop>[.!?।॥\n;:])"
    r"|(?P<hyph>[-–])"
)


@dataclass
class Mention:
    start: int
    end: int
    text: str
    value: float | None
    kind: str  # number, percent, ordinal, date, time, vague, ratio, zero
    unit: str | None = None
    decimals: int = 0
    qualifier: str | None = None
    casualty: bool = False
    date: tuple | None = None


@dataclass
class Violation:
    start: int
    end: int
    text: str
    reason: str
    detail: str = ""


@dataclass
class GuardResult:
    ok: bool
    mentions: list[Mention] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    matched: dict = field(default_factory=dict)   # "start:end" -> fact id
    cited: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "violations": [asdict(v) for v in self.violations],
                "matched": self.matched, "cited": self.cited,
                "mentions": [asdict(m) for m in self.mentions]}

    def feedback(self) -> str:
        """Human-readable violation list, used to re-prompt the model once."""
        return "; ".join(f'"{v.text}" ({v.reason}{": " + v.detail if v.detail else ""})' for v in self.violations)


# ---------------------------------------------------------------- extraction

def _blank(buf: list[str], s: int, e: int) -> None:
    for i in range(s, e):
        if buf[i] != "\n":
            buf[i] = " "


def _tokens(work: str) -> list[dict]:
    out = []
    for m in _TOKEN.finditer(work):
        kind = m.lastgroup if m.lastgroup != "ordsuf" else "num"
        if m.group("num") is not None:
            kind = "num"
        out.append({"kind": kind, "text": m.group(0), "start": m.start(), "end": m.end(),
                    "low": m.group(0).lower(), "ordsuf": m.group("ordsuf")})
    return out


def _ne_word_value(tok: str) -> tuple[str, float | None, str | None]:
    """Classify a Devanagari token. Returns (role, value, attached_classifier)."""
    n = norm_ne(tok)
    if n in NE_VAGUE:
        return "vague", None, None
    if n in NE_RATIO:
        return "ratio", NE_RATIO[n], None
    if tok in NE_FRACTIONS:
        return "small", NE_FRACTIONS[tok], None
    if n in NE_ORDINAL_VALUES:
        return "ordinal", NE_ORDINAL_VALUES[n], None
    if n in NE_MULT_VALUES:
        return "mult", NE_MULT_VALUES[n], None
    if n in NE_WORD_VALUES:
        return "small", NE_WORD_VALUES[n], None
    if tok in ("एउटा", "एउटै", "एकवटा"):
        return "small", 1, "वटा"
    for suf in NE_SUFFIXES:
        if tok.endswith(suf) and len(tok) > len(suf):
            stem = norm_ne(tok[: -len(suf)])
            if stem in NE_WORD_VALUES:
                return "small", NE_WORD_VALUES[stem], suf
    # ordinal suffix: पाँचौं, बीसौं
    for suf in ("औं", "औँ", "ौं", "ौँ"):
        if tok.endswith(suf):
            stem = norm_ne(tok[: -len(suf)])
            if stem in NE_WORD_VALUES:
                return "ordinal", NE_WORD_VALUES[stem], None
    return "", None, None


def _classify(tok: dict) -> tuple[str, float | None, str | None]:
    """role in {digit, small, tens, mult, vague, ratio, ordinal, ''}."""
    k, low = tok["kind"], tok["low"]
    if k == "num":
        v = float(low.replace(",", ""))
        if tok.get("ordsuf"):
            return "ordinal", v, None
        return "digit", v, None
    if k == "word":
        if low in EN_SMALL:
            return "small", EN_SMALL[low], None
        if low in EN_TENS:
            return "tens", EN_TENS[low], None
        if low in EN_MULT:
            return "mult", EN_MULT[low], None
        if low in EN_VAGUE:
            return "vague", None, None
        if low in EN_RATIO:
            return "ratio", EN_RATIO[low], None
        if low in EN_ORD:
            return "ordinal", EN_ORD[low], None
        if low in ROMAN_NE:
            return "small", ROMAN_NE[low], None
        if low in ROMAN_MULT:
            return "mult", ROMAN_MULT[low], None
        return "", None, None
    if k == "dev":
        return _ne_word_value(tok["text"])
    return "", None, None


def _unit_after(toks: list[dict], j: int) -> tuple[str | None, int]:
    """Look up to 4 tokens ahead for a unit word. Returns (unit, index after unit)."""
    seen = 0
    i = j
    while i < len(toks) and seen < 5:
        t = toks[i]
        if t["kind"] in ("stop", "num"):
            return None, j
        low = t["low"]
        if t["kind"] == "sym":
            if low == "%":
                return "percent", i + 1
            if low == "²":
                return "km2", i + 1
            i += 1
            continue
        if low in ("per",) and i + 1 < len(toks) and toks[i + 1]["low"] == "cent":
            return "percent", i + 2
        if low in ("km", "kilometre", "kilometres", "kilometer", "kilometers") and i + 1 < len(toks) and toks[i + 1]["low"] == "²":
            return "km2", i + 2
        if low in ("square", "sq", "वर्ग") and i + 1 < len(toks) and UNIT_WORDS.get(toks[i + 1]["low"]) == "km":
            return "km2", i + 2
        if low in UNIT_WORDS:
            return UNIT_WORDS[low], i + 1
        if t["kind"] == "dev":
            for suf in ("हरू", "मा", "को", "का", "की", "बाट", "लाई", "ले"):
                if low.endswith(suf) and low[: -len(suf)] in UNIT_WORDS:
                    return UNIT_WORDS[low[: -len(suf)]], i + 1
        if low in CLASSIFIERS:
            i += 1
            continue
        if t["kind"] == "dev" and low.startswith(("वटा", "ओटा")):
            return None, j  # "५ वटाको": the noun was said earlier, so no unit here
        role, _, _ = _classify(t)
        if role:
            return None, j
        if low in SKIP_WORDS or seen < 2:
            seen += 1
            i += 1
            continue
        return None, j
    return None, j


def _qualifier(toks: list[dict], i: int, j: int) -> str | None:
    lows_before = [t["low"] for t in toks[max(0, i - 2):i]]
    for pat, name in QUAL_BEFORE.items():
        if len(pat) <= len(lows_before) and tuple(lows_before[-len(pat):]) == pat:
            return name
    lows_after = [t["low"] for t in toks[j:j + 3] if t["low"] not in CLASSIFIERS]
    for pat, name in QUAL_AFTER.items():
        if tuple(lows_after[:len(pat)]) == pat:
            return name
    return None


def extract(text: str, extra_whitelist: Iterable[str] = ()) -> list[Mention]:
    """Extract every numeric expression from text."""
    norm = to_latin(text)
    norm = re.sub(r"(?i)\b(k?m)(?:2|²)\b", lambda m: m.group(1) + "q" if m.group(1).lower() == "km" else m.group(0), norm)
    buf = list(norm)
    mentions: list[Mention] = []

    for w in extra_whitelist:
        if w and re.search(r"\d", to_latin(w)):
            for m in re.finditer(re.escape(to_latin(w)), norm):
                _blank(buf, m.start(), m.end())
    for pat in _WHITELIST:
        for m in pat.finditer("".join(buf)):
            _blank(buf, m.start(), m.end())
    for m in _LIST_MARKER.finditer("".join(buf)):
        _blank(buf, m.start(1), m.end(1))

    # dates
    for kind, pat in _DATE_PATTERNS:
        work = "".join(buf)
        for m in pat.finditer(work):
            g = m.groups()
            y = mo = d = None
            try:
                if kind == "iso":
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                elif kind == "dmy":
                    d, mo, y = int(g[0]), _month(g[1]), int(g[2]) if g[2] else None
                elif kind == "mdy":
                    mo, d, y = _month(g[0]), int(g[1]), int(g[2]) if g[2] else None
                elif kind == "my":
                    mo, y = _month(g[0]), int(g[1])
                elif kind == "num":
                    d, mo, y = int(g[0]), int(g[1]), int(g[2])
                    if mo > 12 and d <= 12:
                        d, mo = mo, d
                    if y < 100:
                        y += 2000
                elif kind == "bs":
                    if not any(g[i] for i in (0, 2, 3)):
                        continue
            except (TypeError, ValueError):
                continue
            mentions.append(Mention(m.start(), m.end(), text[m.start():m.end()], None,
                                    "date_bs" if kind == "bs" else "date", date=(y, mo, d)))
            _blank(buf, m.start(), m.end())
    for pat in _TIME_PATTERNS:
        for m in pat.finditer("".join(buf)):
            mentions.append(Mention(m.start(), m.end(), text[m.start():m.end()], None, "time"))
            _blank(buf, m.start(), m.end())

    work = "".join(buf)
    toks = _tokens(work)
    lows = [t["low"] for t in toks]
    i = 0
    while i < len(toks):
        role, val, attached = _classify(toks[i])
        # "a dozen", "a hundred", "a few"
        pre_one = False
        if not role and lows[i] == "a" and i + 1 < len(toks):
            r2, v2, _ = _classify(toks[i + 1])
            if r2 == "mult":
                role, val, pre_one = "small", 1, True
            elif lows[i + 1] in ("few", "couple"):
                mentions.append(Mention(toks[i]["start"], toks[i + 1]["end"], text[toks[i]["start"]:toks[i + 1]["end"]],
                                        None, "vague"))
                i += 2
                continue
        if not role and lows[i] in ("no", "zero") and i + 1 < len(toks):
            unit, after = _unit_after(toks, i + 1)
            if lows[i] == "no" and unit in ("settlement", "building", "bridge", "people", "facility") and after == i + 2:
                mentions.append(Mention(toks[i]["start"], toks[i + 1]["end"], text[toks[i]["start"]:toks[i + 1]["end"]],
                                        0.0, "zero", unit))
                i += 2
                continue
        if not role:
            i += 1
            continue
        start = toks[i]["start"]
        if role in ("vague", "ratio"):
            # "a third", "two thirds" are ratios; "hundreds of" is vague
            mentions.append(Mention(start, toks[i]["end"], text[start:toks[i]["end"]], val, role))
            i += 1
            continue
        if role == "ordinal":
            unit, _ = _unit_after(toks, i + 1)
            mentions.append(Mention(start, toks[i]["end"], text[start:toks[i]["end"]], val, "ordinal", unit))
            i += 1
            continue
        # number sequence
        total, current, decimals = 0.0, None, 0
        j = i
        last_role = None
        if pre_one:
            current, j, last_role = 1.0, i + 1, "small"
        while j < len(toks):
            r, v, att = _classify(toks[j])
            if att:
                attached = att
            if r == "digit":
                if current is not None:
                    break
                current = v
                txt = toks[j]["text"]
                decimals = len(txt.split(".")[1]) if "." in txt else 0
            elif r in ("small", "tens"):
                if current is None:
                    current = v
                elif last_role == "tens" and r == "small" and v < 10:
                    current += v
                elif last_role == "mult":
                    current = v if current == 0 else current + v
                else:
                    break
            elif r == "mult":
                if current is None:
                    break
                if v == 100:
                    current = (current or 1) * 100
                elif v == 12:
                    current = (current or 1) * 12
                else:
                    total += (current or 1) * v
                    current = 0
                decimals = 0
            elif toks[j]["kind"] == "hyph" or (lows[j] == "and" and current is not None):
                if j + 1 < len(toks) and _classify(toks[j + 1])[0] in ("small", "tens", "mult"):
                    j += 1
                    continue
                break
            else:
                break
            last_role = r
            j += 1
        if current is None and total == 0:
            i += 1
            continue
        value = total + (current or 0)
        end = toks[j - 1]["end"]
        unit, _ = _unit_after(toks, j)
        if attached in ("जना",):
            unit = unit or "people"
        word_seq = [lows[k] for k in range(i, j)]
        if len(word_seq) == 1 and word_seq[0] in AMBIGUOUS and not attached:
            nxt_unit = unit
            if not nxt_unit:
                i = j
                continue
        if toks[j - 1]["kind"] == "num" and j < len(toks) and lows[j] in ("%",):
            unit = "percent"
        kind = "percent" if unit == "percent" else "number"
        # years
        m = Mention(start, end, text[start:end], value, kind, unit, decimals)
        m.qualifier = _qualifier(toks, i, j)
        mentions.append(m)
        i = j

    # casualty proximity
    cas_idx = [k for k, t in enumerate(toks) if t["low"] in CASUALTY or norm_ne(t["low"]) in _CAS_NORM]
    if cas_idx:
        pos = {t["start"]: k for k, t in enumerate(toks)}
        sent_of, n_s = [], 0
        for t in toks:
            sent_of.append(n_s)
            if t["kind"] == "stop" and t["low"] in (".", "!", "?", "।", "॥", "\n"):
                n_s += 1
        for m in mentions:
            k = pos.get(m.start)
            if k is None:
                k = min(range(len(toks)), key=lambda q: abs(toks[q]["start"] - m.start))
            if any(abs(k - c) <= 6 and sent_of[k] == sent_of[c] for c in cas_idx):
                m.casualty = True
    mentions.sort(key=lambda m: m.start)
    return mentions


def _month(s: str) -> int:
    s2 = s.lower().rstrip(".")
    if s2 in _MONTHS_EN:
        return _MONTHS_EN[s2]
    return _MONTHS_NE[s]


# ---------------------------------------------------------------- matching

def _unit_ok(m_unit: str | None, fact: dict) -> bool:
    fc = FACT_UNIT_CLASS.get(fact.get("unit") or "")
    if m_unit is None:
        return True
    if fc is None:
        return m_unit not in ("percent", "km", "ha", "km2", "minutes", "duration", "metre")
    return m_unit == fc


def _value_ok(m: Mention, fact: dict) -> bool:
    v = float(fact["value"])
    x = float(m.value)
    if abs(v - x) < 1e-9:
        return True
    if fact.get("kind") in ("count", "rank") or (float(v).is_integer() and (fact.get("decimals") or 0) == 0
                                                 and fact.get("kind") != "measure"):
        return False
    return abs(round(v, m.decimals) - x) < 1e-9


def match_number(m: Mention, facts: Iterable[dict]) -> tuple[dict | None, bool]:
    """Return (matching fact, value matched but unit did not)."""
    unit_miss = False
    for f in facts:
        if not is_numeric_fact(f):
            continue
        if _value_ok(m, f):
            if _unit_ok(m.unit, f):
                return f, False
            unit_miss = True
    return None, unit_miss


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, s = [], 0
    for m in re.finditer(r"[.!?](?=\s|$)|[।॥\n]", text):
        e = m.end()
        if text[s:e].strip():
            spans.append((s, e))
        s = e
    if text[s:].strip():
        spans.append((s, len(text)))
    return spans


def _is_refusal_sentence(sent: str) -> bool:
    low = sent.lower()
    toks = set(re.findall(r"[\u0900-\u0963\u0970-\u097F]+", sent))
    return any(k in low for k in REFUSAL_MARKERS_EN) or bool(toks & set(REFUSAL_MARKERS_NE))


def check(text: str, ledger: Ledger, allowed_ids: Iterable[str] | None = None) -> GuardResult:
    """Check every number in text against the ledger.

    allowed_ids restricts numeric matches to those facts (for example the facts
    the model actually looked up) plus any fact the text cites as [Fnnn]. Dates
    and ranks are checked against the whole ledger.
    """
    cited = re.findall(r"\bF\d{3,}\b", to_latin(text))
    violations: list[Violation] = []
    for c in dict.fromkeys(cited):
        if ledger.get_id(c) is None:
            for m in re.finditer(rf"\b{c}\b", text):
                violations.append(Violation(m.start(), m.end(), c, "bad_citation", "no such fact"))
    if allowed_ids is None:
        pool = ledger.numeric_facts()
    else:
        ids = set(allowed_ids) | {c for c in cited if ledger.get_id(c)}
        pool = [ledger.get_id(i) for i in ids if ledger.get_id(i)]
        pool = [f for f in pool if is_numeric_fact(f)]
    ranks = [f for f in ledger.numeric_facts() if f.get("kind") == "rank" or f.get("unit") == "rank"]
    dates = [f for f in ledger if f.get("kind") == "date"]
    names = [str(f.get(k)) for f in ledger for k in ("value", "value_ne") if isinstance(f.get(k), str)]
    names += [str(x) for f in ledger for k in ("value", "value_ne") if isinstance(f.get(k), list) for x in f[k]]

    mentions = extract(text, extra_whitelist=names)
    matched: dict = {}
    for m in mentions:
        span = (m.start, m.end, m.text)
        if m.casualty:
            violations.append(Violation(*span, "casualty", "the system has no casualty figures"))
            continue
        if m.kind in ("vague", "ratio"):
            violations.append(Violation(*span, m.kind, "not a ledger figure"))
            continue
        if m.kind == "time":
            violations.append(Violation(*span, "time", "no ledger fact is a clock time"))
            continue
        if m.kind == "date_bs":
            violations.append(Violation(*span, "date_not_in_ledger", "Bikram Sambat dates are not in the ledger"))
            continue
        if m.kind == "date":
            y, mo, d = m.date
            hit = None
            for f in dates:
                fm = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(f["value"]))
                if fm and (y is None or int(fm[1]) == y) and (mo is None or int(fm[2]) == mo) and (d is None or int(fm[3]) == d):
                    hit = f
                    break
            if hit is None and d is None and y in WHITELIST_YEARS:
                continue
            if hit:
                matched[f"{m.start}:{m.end}"] = hit["id"]
            else:
                violations.append(Violation(*span, "date_not_in_ledger"))
            continue
        if m.qualifier:
            violations.append(Violation(*span, "qualifier", f'"{m.qualifier}" changes a ledger figure'))
            continue
        if m.kind == "ordinal":
            f, _ = match_number(m, ranks + pool)
            if f:
                matched[f"{m.start}:{m.end}"] = f["id"]
            else:
                violations.append(Violation(*span, "not_in_ledger", "ordinal"))
            continue
        if m.kind == "zero":
            f, _ = match_number(m, pool)
            if f:
                matched[f"{m.start}:{m.end}"] = f["id"]
            else:
                violations.append(Violation(*span, "not_in_ledger", f"claims zero {m.unit}"))
            continue
        if m.value is not None and float(m.value).is_integer() and int(m.value) in WHITELIST_YEARS and m.unit is None:
            continue
        f, unit_miss = match_number(m, pool)
        if f is None and m.unit is None and allowed_ids is not None:
            f, _ = match_number(m, ranks)
        if f:
            matched[f"{m.start}:{m.end}"] = f["id"]
        else:
            violations.append(Violation(*span, "unit_mismatch" if unit_miss else "not_in_ledger",
                                        f"unit {m.unit}" if m.unit else ""))

    # casualty statements without numbers ("no one died") are claims too
    for s, e in _sentences(text):
        sent = text[s:e]
        words = set(re.findall(r"[a-z']+|[ऀ-ॣ॰-ॿ]+", sent.lower()))
        if words & CASUALTY and not _is_refusal_sentence(sent):
            if not any(v.start >= s and v.end <= e and v.reason == "casualty" for v in violations):
                violations.append(Violation(s, e, sent.strip(), "casualty_claim", "casualty statement"))

    violations.sort(key=lambda v: v.start)
    return GuardResult(ok=not violations, mentions=mentions, violations=violations, matched=matched,
                       cited=list(dict.fromkeys(cited)))


def drop_violating_sentences(text: str, result: GuardResult) -> str:
    """Remove every sentence that contains a violation."""
    keep = []
    for s, e in _sentences(text):
        if not any(v.start < e and v.end > s for v in result.violations):
            keep.append(text[s:e].strip())
    return " ".join(k for k in keep if k)
