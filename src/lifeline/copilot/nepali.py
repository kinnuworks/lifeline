"""Nepali number helpers shared by the ledger, the guard and the templates.

Devanagari digits, lakh-style grouping, number words (1-99 plus सय, हजार, लाख,
करोड) and month names.

NOTE: a native Nepali speaker must review the word lists and month spellings.
"""

from __future__ import annotations

DEV_DIGITS = "०१२३४५६७८९"
_TO_DEV = str.maketrans("0123456789", DEV_DIGITS)
_TO_LAT = str.maketrans(DEV_DIGITS, "0123456789")


def to_dev(s: str) -> str:
    """Latin digits to Devanagari digits (same length)."""
    return s.translate(_TO_DEV)


def to_latin(s: str) -> str:
    """Devanagari digits to Latin digits (same length, so spans are preserved)."""
    return s.translate(_TO_LAT)


def group_lakh(int_part: str) -> str:
    """'1234567' -> '12,34,567' (South Asian grouping)."""
    neg = int_part.startswith("-")
    s = int_part.lstrip("-")
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts + [tail])
    return ("-" if neg else "") + out


# 0-99. Index = value.
NE_WORDS_0_99 = [
    "शून्य", "एक", "दुई", "तीन", "चार", "पाँच", "छ", "सात", "आठ", "नौ",
    "दश", "एघार", "बाह्र", "तेह्र", "चौध", "पन्ध्र", "सोह्र", "सत्र", "अठार", "उन्नाइस",
    "बीस", "एक्काइस", "बाइस", "तेइस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताइस", "अठ्ठाइस", "उनन्तीस",
    "तीस", "एकतीस", "बत्तीस", "तेत्तीस", "चौँतीस", "पैँतीस", "छत्तीस", "सैँतीस", "अठतीस", "उनन्चालीस",
    "चालीस", "एकचालीस", "बयालीस", "त्रियालीस", "चवालीस", "पैँतालीस", "छयालीस", "सतचालीस", "अठचालीस", "उनन्चास",
    "पचास", "एकाउन्न", "बाउन्न", "त्रिपन्न", "चवन्न", "पचपन्न", "छपन्न", "सन्ताउन्न", "अन्ठाउन्न", "उनन्साठी",
    "साठी", "एकसट्ठी", "बयसट्ठी", "त्रिसट्ठी", "चौसट्ठी", "पैँसट्ठी", "छयसट्ठी", "सतसट्ठी", "अठसट्ठी", "उनन्सत्तरी",
    "सत्तरी", "एकहत्तर", "बहत्तर", "त्रिहत्तर", "चौहत्तर", "पचहत्तर", "छयहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "असी", "एकासी", "बयासी", "त्रियासी", "चौरासी", "पचासी", "छयासी", "सतासी", "अठासी", "उनान्नब्बे",
    "नब्बे", "एकानब्बे", "बयानब्बे", "त्रियानब्बे", "चौरानब्बे", "पन्चानब्बे", "छयानब्बे", "सन्तानब्बे", "अन्ठानब्बे", "उनान्सय",
]

NE_MULTIPLIERS = {"सय": 100, "हजार": 1000, "लाख": 100_000, "करोड": 10_000_000, "अर्ब": 1_000_000_000}


def norm_ne(word: str) -> str:
    """Spelling-insensitive key: drop chandrabindu, anusvara and nukta, unify दस/दश."""
    w = word.replace("ँ", "").replace("ं", "").replace("़", "")
    w = w.replace("श", "स").replace("ई", "इ").replace("ी", "ि").replace("ू", "ु")
    return w


# Lookup by normalised spelling, with common variants.
NE_WORD_VALUES: dict[str, int] = {norm_ne(w): i for i, w in enumerate(NE_WORDS_0_99)}
for _v, _alts in {
    2: ["दुइ"], 5: ["पाच"], 10: ["दस"], 12: ["बार्ह", "बाह्र"], 13: ["तेर्ह"], 15: ["पन्द्र"], 16: ["सोर्ह"],
    18: ["अठाह्र", "अठार"], 19: ["उन्नीस"], 28: ["अठाइस"], 99: ["उनान्सय", "उनान्सय"],
}.items():
    for _a in _alts:
        NE_WORD_VALUES[norm_ne(_a)] = _v
NE_MULT_VALUES = {norm_ne(k): v for k, v in NE_MULTIPLIERS.items()}

NE_ORDINALS = {
    "पहिलो": 1, "दोस्रो": 2, "तेस्रो": 3, "चौथो": 4, "पाँचौं": 5, "छैटौं": 6, "सातौं": 7,
    "आठौं": 8, "नवौं": 9, "दशौं": 10,
}
NE_ORDINAL_VALUES = {norm_ne(k): v for k, v in NE_ORDINALS.items()}

NE_FRACTIONS = {"आधा": 0.5, "डेढ": 1.5, "अढाइ": 2.5, "अढाई": 2.5}


def ne_words(n: int) -> str:
    """Integer to Nepali words: 1234 -> 'एक हजार दुई सय चौँतीस'."""
    if n < 0:
        return "माइनस " + ne_words(-n)
    if n < 100:
        return NE_WORDS_0_99[n]
    parts = []
    for name, size in (("करोड", 10_000_000), ("लाख", 100_000), ("हजार", 1000), ("सय", 100)):
        q, n = divmod(n, size)
        if q:
            parts.append(f"{ne_words(q)} {name}")
    if n:
        parts.append(NE_WORDS_0_99[n])
    return " ".join(parts)


MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
MONTHS_NE = ["जनवरी", "फेब्रुअरी", "मार्च", "अप्रिल", "मे", "जुन", "जुलाई", "अगस्ट",
             "सेप्टेम्बर", "अक्टोबर", "नोभेम्बर", "डिसेम्बर"]
# Bikram Sambat months. The guard flags BS dates unless the ledger carries them.
MONTHS_BS = ["बैशाख", "वैशाख", "जेठ", "असार", "साउन", "श्रावण", "भदौ", "भाद्र", "असोज", "आश्विन",
             "कात्तिक", "कार्तिक", "मंसिर", "मङ्सिर", "पुस", "पौष", "माघ", "फागुन", "फाल्गुन", "चैत", "चैत्र"]
