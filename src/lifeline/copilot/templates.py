"""Deterministic English and Nepali answers and report text, filled only from the ledger.

Every sentence that carries a number cites the fact IDs it used, like [F012].

NOTE: the Nepali wording here was written to be plain and clear for a district
disaster officer, but a native Nepali speaker must review it before any real use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .branding import PRODUCT_NAME, PRODUCT_NAME_NE
from .ledger import list_join, Ledger, render_value, fmt_number

# ---------------------------------------------------------------- intent detection

_KW = {
    "casualty": {
        "en": r"\b(dead|deaths?|died|die|dying|killed|kill|fatalit\w*|casualt\w*|injur\w*|missing (people|persons)|bodies|body count|deceased|victims?|toll|drown\w*|perished|lives lost|survivors?)\b",
        "ne": r"(मृत्यु|मृतक|मरे|मरेका|मर्‍यो|मर्यो|मारिए|घाइते|बेपत्ता|हताहत|शव|ज्यान गयो|ज्यान गुमा)",
        "rom": r"\b(marey|mareko|mare|maryo|marechan|mrityu|ghaite|bepatta|lash|laash)\b",
    },
    "future": {
        "en": r"\b(when will|how long (until|before|will|to)|how soon|will (it|they|the|roads?|bridges?) (be )?(re)?open|predict\w*|forecast\w*|tomorrow|next (week|month|days?)|expected to|going to (re)?open|by when|in the coming|future)\b",
        "ne": r"(कहिले\s*(सम्म)?\s*\S*\s*(खुल्छ|खुल्ला|खुल्नेछ|बन्छ|बन्ला|बन्नेछ|हुन्छ|होला|सकिन्छ|आउँछ)|भोलि|पर्सि|अर्को हप्ता|पूर्वानुमान|कति दिनमा|कति समयमा|हुनेछ|खुल्नेछ)",
        "rom": r"\b(kahile|bholi|kati din ma)\b",
    },
    "weather": {
        "en": r"\b(weather|rain\w*|monsoon|forecast|temperature|storm|cloudburst)\b",
        "ne": r"(मौसम|वर्षा|पानी पर्छ|पानी पर्ला|झरी|तापक्रम)",
        "rom": r"\b(mausam|pani parcha|jhari)\b",
    },
    "out_of_scope": {
        "en": r"\b(money|cost|costs|budget|rupees?|funds?|funding|tents?|relief|food|rations|livestock|animals|cattle|helicopters?|water level|river level|metres|meters|rescue teams?|aid|donations?|crops?)\b",
        "ne": r"(रुपैयाँ|पैसा|खर्च|बजेट|राहत|खाद्यान्न|पशु|चौपाया|हेलिकप्टर|पानीको सतह|उद्धार टोली|बाली)",
        "rom": r"\b(paisa|rupiya|rahat|helicopter)\b",
    },
    "inject": {
        "en": r"\b(ignore|disregard|forget (your|the|all)|system prompt|your (rules|instructions|prompt)|pretend|role-?play|act as|developer mode|jailbreak|override|bypass|no longer bound|new instructions?)\b",
        "ne": r"(नियम बिर्स|बिर्सनुहोस्|बेवास्ता गर|निर्देशन बिर्स|नाटक गर)",
        "rom": r"\b(niyam birsa|birsanu)\b",
    },
    "assert": {
        "en": r"\b(say (that|it'?s|there|\d)|write (that|it)|tell (them|everyone|the press)|round(ed|ing)? .{0,40}\b(up|down|off|nearest)\b|nearest (hundred|thousand)|exaggerate|inflate|make up|invent|just (estimate|guess)|best guess|your guess|guess|assume|hypothetical\w*|ballpark|rough estimate|confirm that|isn't it|right\?)\b",
        "ne": r"(भनिदिनुहोस्|भन्नुहोस्|लेखिदिनुहोस्|मान्नुहोस्|अनुमान गर्नुहोस्|बढाएर|राउन्ड|पूर्णाङ्क|मिलाएर भन)",
        "rom": r"\b(bhanidinus|lekhidinus|anuman)\b",
    },
}
_INTENTS = {
    "flood": r"\b(flood\w*|inundat\w*|water|area|hectares?|where|debris|scour|submerged|underwater|km2|km²)\b|डुबान|डुबेको|डुब्यो|बाढी|पानी|क्षेत्र|हेक्टर|कहाँ|गेग्रान|\b(dubeko|badhi|baadhi)\b",
    "buildings": r"\b(buildings?|houses?|homes?|structures?|shelters?)\b|घर|भवन|\bghar\b",
    "roads": r"\b(roads?|highways?|km of|tracks?)\b|सडक|राजमार्ग|\b(sadak)\b",
    "bridges": r"\bbridges?\b|पुल|\bpul\b",
    "damage": r"\b(damage\w*|destroy\w*|hit|affected|broken|collapsed|washed)\b|क्षति|ध्वस्त|भत्कि|बग्यो|\b(nuksan|bhatkiyo)\b",
    "cutoff": r"\b(cut ?-?off|isolated|stranded|unreachable|reach\w*|access\w*|cut)\b|सम्पर्क|पुग्न|टुटेको|एक्लिए|\b(katiyeko|banda|sampark)\b",
    "people": r"\b(people|population|residents|persons|inhabitants)\b|मानिस|जनसंख्या|जना\b|\b(manche|jana)\b",
    "health": r"\b(hospitals?|health|clinics?|medical|doctors?)\b|अस्पताल|स्वास्थ्य|\b(aspatal|haspatal)\b",
    "chokepoint": r"\b(repair\w*|priorit\w*|reopen\w*|fix\w*|reconnect\w*|chokepoints?|first)\b|मर्मत|प्राथमिकता|खोल्दा|जोड्|\b(marmat)\b",
    "dates": r"\b(when|date|dates|image|images|taken|pass|acquired)\b|मिति|तस्बिर|लिइएको",
    "method": r"\b(how do you know|how does|method|accura\w*|confiden\w*|trust|sure|radar|sentinel|reliable|uncertain\w*)\b|कसरी|विधि|भरोसा|विश्वास|राडार",
    "summary": r"\b(summary|summari[sz]e|overview|situation|sitrep|report|what happened|brief)\b|सारांश|अवस्था|स्थिति|रिपोर्ट|के भयो",
}
_ROMAN_NE = r"\b(kati|cha|chha|gaun|gau|ghar|pul|bato|sadak|marey|mareko|kahile|ko|ma|bhayo|bhanidinus|aspatal)\b"


def detect_lang(question: str) -> str:
    if re.search(r"[ऀ-ॿ]", question):
        return "ne"
    if len(re.findall(_ROMAN_NE, question.lower())) >= 2:
        return "ne"
    return "en"


@dataclass
class Intent:
    data: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    settlements: list[dict] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)


def classify(question: str, ledger: Ledger) -> Intent:
    from .guard import check  # local import to avoid a cycle
    q = question.lower()
    it = Intent()
    for name, pats in _KW.items():
        if any(re.search(p, q if k != "ne" else question) for k, p in pats.items()):
            it.refusals.append(name)
    # "when was the image taken" is a dates question, not a future one
    if "future" in it.refusals and re.search(r"\b(when was|when were|taken|acquired)\b|लिइएको|खिचिएको", q):
        it.refusals.remove("future")
    # "weather" without a question about it, e.g. radar sees through cloud
    for name, pat in _INTENTS.items():
        if re.search(pat, q if not re.search(r"[ऀ-ॿ]", pat) else question, re.I):
            it.data.append(name)
    if "damage" in it.data:
        for k in ("buildings", "roads", "bridges"):
            if k not in it.data:
                it.data.append(k)
    if "dates" in it.data and any(r in it.refusals for r in ("future",)):
        it.data.remove("dates")
    # named settlements
    seen = set()
    for f in ledger.prefix("settlement."):
        if not f["key"].endswith(".name"):
            continue
        for nm in (f.get("value"), f.get("value_ne")):
            if nm and len(str(nm)) >= 3 and re.search(rf"(?<![\wऀ-ॿ]){re.escape(str(nm).lower())}", q) and f["key"] not in seen:
                it.settlements.append(f)
                seen.add(f["key"])
    # numbers the user typed that are not in the ledger
    g = check(question, ledger)
    it.unverified_numbers = [v.text for v in g.violations if v.reason not in ("casualty_claim",)]
    if it.unverified_numbers and "assert" not in it.refusals:
        it.refusals.append("assert")
    return it


# ---------------------------------------------------------------- helpers

class _Doc:
    """Collect sentences and the fact IDs they cite."""

    def __init__(self, L: Ledger, lang: str):
        self.L, self.lang = L, lang
        self.lines: list[str] = []
        self.ids: list[str] = []

    def f(self, key: str) -> dict | None:
        return self.L.get(key)

    def has(self, *keys: str) -> bool:
        return all(self.L.get(k) is not None for k in keys)

    def v(self, key: str, unit: bool = True) -> str:
        fact = self.L.get(key)
        if fact is None:
            return "?"
        self.ids.append(fact["id"])
        return render_value(fact, self.lang, unit)

    def cite(self, *keys: str) -> str:
        ids = [self.L.get(k)["id"] for k in keys if self.L.get(k)]
        return " " + "".join(f"[{i}]" for i in dict.fromkeys(ids)) if ids else ""

    def add(self, text: str) -> None:
        self.lines.append(text)

    def names(self, key: str, n: int = 5) -> str:
        fact = self.L.get(key)
        if not fact:
            return ""
        self.ids.append(fact["id"])
        vals = fact.get("value_ne") if self.lang == "ne" and fact.get("value_ne") else fact.get("value")
        return list_join(list(vals)[:n])

    def text(self) -> str:
        return "\n".join(self.lines)


def _end(lang: str) -> str:
    return "।" if lang == "ne" else "."


# ---------------------------------------------------------------- section builders

def flood_lines(d: _Doc) -> bool:
    L, ne = d.L, d.lang == "ne"
    if not d.has("flood.area_ha.total"):
        return False
    t, tk = d.v("flood.area_ha.total"), d.v("flood.area_km2.total") if d.has("flood.area_km2.total") else None
    both = f"{t} ({tk})" if tk else t
    c = d.cite("flood.area_ha.total", "flood.area_km2.total")
    if ne:
        d.add(f"भू-उपग्रह तस्बिर अनुसार {both} क्षेत्र डुबानमा परेको वा गेग्रानले पुरिएको देखिन्छ{c}।")
    else:
        d.add(f"Satellite images show {both} flooded or buried by debris{c}.")
    if d.has("flood.area_ha.water", "flood.area_ha.debris"):
        w, b = d.v("flood.area_ha.water"), d.v("flood.area_ha.debris")
        c = d.cite("flood.area_ha.water", "flood.area_ha.debris")
        d.add(f"यसमध्ये {w} खुला पानी र {b} गेग्रान वा कटान हो{c}।" if ne
              else f"Of this, {w} is open water and {b} is debris or scour{c}.")
    if d.has("flood.area_ha.unobserved") and float(L.value("flood.area_ha.unobserved")) > 0:
        u = d.v("flood.area_ha.unobserved")
        c = d.cite("flood.area_ha.unobserved")
        d.add(f"पहाडको छायाँका कारण {u} क्षेत्र राडारले देख्न सकेन, त्यहाँको अवस्था थाहा छैन{c}।" if ne
              else f"The radar could not see {u} because of terrain shadow, so conditions there are unknown{c}.")
    if d.has("damage.places_hit_names"):
        names = d.names("damage.places_hit_names", 5)
        c = d.cite("damage.places_hit_names")
        d.add(f"सबैभन्दा धेरै घर ध्वस्त भएको हुन सक्ने ठाउँ: {names}{c}।" if ne
              else f"The places with the most buildings likely destroyed: {names}{c}.")
    return True


def buildings_lines(d: _Doc) -> bool:
    k = "damage.buildings."
    if not d.has(k + "total"):
        return False
    t, x, p = d.v(k + "total", False), d.v(k + "destroyed_likely", False), d.v(k + "damaged_possible", False)
    c = d.cite(k + "total", k + "destroyed_likely", k + "damaged_possible")
    if d.lang == "ne":
        d.add(f"घर/भवन: नक्सामा भएका {t} वटामध्ये {x} वटा ध्वस्त भएको हुन सक्छ र {p} वटामा क्षति भएको हुन सक्छ{c}।")
    else:
        d.add(f"Buildings: {x} likely destroyed and {p} possibly damaged, out of {t} mapped{c}.")
    return True


def roads_lines(d: _Doc) -> bool:
    k = "damage.roads_km."
    if not d.has(k + "total"):
        return False
    t, x, p = d.v(k + "total"), d.v(k + "destroyed_likely"), d.v(k + "damaged_possible")
    c = d.cite(k + "total", k + "destroyed_likely", k + "damaged_possible")
    if d.lang == "ne":
        d.add(f"सडक: नक्सामा भएको {t} मध्ये {x} ध्वस्त भएको हुन सक्छ र {p} मा क्षति भएको हुन सक्छ{c}।")
    else:
        d.add(f"Roads: {x} likely destroyed and {p} possibly damaged, out of {t} mapped{c}.")
    return True


def bridges_lines(d: _Doc) -> bool:
    k = "damage.bridges."
    if not d.has(k + "total"):
        return False
    t, x, p = d.v(k + "total", False), d.v(k + "destroyed_likely", False), d.v(k + "damaged_possible", False)
    names = d.names(k + "destroyed_names", 4)
    c = d.cite(k + "total", k + "destroyed_likely", k + "damaged_possible", k + "destroyed_names")
    if d.lang == "ne":
        nm = f" ({names})" if names else ""
        d.add(f"पुल: नक्सामा भएका {t} वटामध्ये {x} वटा ध्वस्त भएको हुन सक्छ{nm} र {p} वटामा क्षति भएको हुन सक्छ{c}।")
    else:
        nm = f" ({names})" if names else ""
        d.add(f"Bridges: {x} likely destroyed{nm} and {p} possibly damaged, out of {t} mapped{c}.")
    return True


def cutoff_lines(d: _Doc, n_names: int = 5) -> bool:
    k = "access.villages."
    if not d.has(k + "cut_off"):
        return False
    n = d.v(k + "cut_off", False)
    t = d.v(k + "total", False) if d.has(k + "total") else None
    c = d.cite(k + "cut_off", k + "total")
    ne = d.lang == "ne"
    if float(d.L.value(k + "cut_off")) == 0:
        d.add(f"विश्लेषण गरिएका {t} वटा बस्तीमध्ये कुनैको पनि सडक सम्पर्क टुटेको देखिँदैन{c}।" if ne
              else f"None of the {t} settlements analysed appear cut off by road{c}.")
        return True
    if ne:
        d.add(f"विश्लेषण गरिएका {t} वटा बस्तीमध्ये {n} वटाको सडक सम्पर्क टुटेको हुन सक्छ{c}।" if t
              else f"{n} वटा बस्तीको सडक सम्पर्क टुटेको हुन सक्छ{c}।")
    else:
        d.add(f"{n} of {t} settlements are likely cut off by road{c}." if t
              else f"{n} settlements are likely cut off by road{c}.")
    if d.has(k + "cut_off_names"):
        names = d.names(k + "cut_off_names", n_names)
        c = d.cite(k + "cut_off_names")
        d.add(f"तीमध्ये: {names}{c}।" if ne else f"Among them: {names}{c}.")
    if d.has(k + "road_cut_walkable") and float(d.L.value(k + "road_cut_walkable")) > 0:
        w = d.v(k + "road_cut_walkable", False)
        c = d.cite(k + "road_cut_walkable")
        d.add(f"अरू {w} वटा बस्तीको सडक टुटे पनि घण्टाभित्रै हिँडेर स्वास्थ्य संस्था पुग्न सकिन्छ{c}।" if ne
              else (f"{w} more settlement has lost its road but can still walk to a health facility within an hour{c}."
                    if float(d.L.value(k + "road_cut_walkable")) == 1 else
                    f"{w} more settlements have lost their road but can still walk to a health facility within an hour{c}."))
    if d.has("access.people.cut_off"):
        p = d.v("access.people.cut_off")
        c = d.cite("access.people.cut_off")
        d.add(f"यी बस्तीमा {p} बस्छन् (OpenStreetMap मा जनसंख्या उल्लेख भएका बस्ती मात्र गनिएको){c}।" if ne
              else f"{p} live in them, counting only places where OpenStreetMap records a population{c}.")
    return True


def health_lines(d: _Doc) -> bool:
    ne = d.lang == "ne"
    ok = False
    if d.has("access.health.nearest_for_cut_off"):
        h = d.v("access.health.nearest_for_cut_off")
        c = d.cite("access.health.nearest_for_cut_off", "access.health.nearest_for_cut_off_villages")
        if d.has("access.health.nearest_for_cut_off_villages"):
            n = d.v("access.health.nearest_for_cut_off_villages", False)
            d.add(f"सम्पर्क टुटेका {n} वटा बस्तीको सबैभन्दा नजिकको स्वास्थ्य संस्था {h} हो{c}।" if ne
                  else f"The nearest health facility for {n} of the cut-off settlements is {h}{c}.")
        else:
            d.add(f"सम्पर्क टुटेका धेरैजसो बस्तीको नजिकको स्वास्थ्य संस्था {h} हो{c}।" if ne
                  else f"The nearest health facility for most cut-off settlements is {h}{c}.")
        ok = True
    if d.has("osm.health.total", "osm.health.hospitals"):
        t, hs = d.v("osm.health.total", False), d.v("osm.health.hospitals", False)
        c = d.cite("osm.health.total", "osm.health.hospitals")
        d.add(f"OpenStreetMap मा यस क्षेत्रमा {t} वटा स्वास्थ्य संस्था (अस्पताल, स्वास्थ्य चौकी, क्लिनिक) छन्; यी नाम र प्रकार प्रमाणित गरिएका छैनन्{c}।" if ne
              else f"OpenStreetMap lists {t} health facilities in the area (hospitals, health posts and clinics); its labels are not verified{c}.")
        ok = True
    return ok


def chokepoint_lines(d: _Doc, n: int = 3, header: bool = True) -> bool:
    if not d.has("chokepoint.count") or not d.L.value("chokepoint.count"):
        return False
    ne = d.lang == "ne"
    if header:
        d.add("मर्मत प्राथमिकता (फेरि जोडिने बस्तीको संख्याका आधारमा):" if ne
              else "Repair priority, ranked by settlements reconnected:")
    for r in range(1, int(d.L.value("chokepoint.count")) + 1):
        if r > n:
            break
        k = f"chokepoint.{r}."
        if not d.has(k + "name"):
            continue
        name = d.v(k + "name")
        kind = d.v(k + "kind") if d.has(k + "kind") else ""
        rank = d.v(k + "rank", False) if d.has(k + "rank") else str(r)
        vil = d.v(k + "villages", False) if d.has(k + "villages") else None
        ppl = d.v(k + "people", False) if d.has(k + "people") else None
        c = d.cite(k + "rank", k + "name", k + "villages", k + "people")
        if ne:
            extra = f" ({ppl} जना)" if ppl else ""
            d.add(f"{rank}. {name} ({kind}) खोल्दा {vil} वटा बस्ती{extra} फेरि जोडिन्छन्{c}।")
        else:
            kind_en = f" ({kind})" if kind and kind.lower() not in name.lower() else ""
            extra = f" ({ppl} people)" if ppl else ""
            word = "settlement" if vil == "1" else "settlements"
            d.add(f"{rank}. Reopening {name}{kind_en} reconnects {vil} {word}{extra}{c}.")
    return True


def dates_lines(d: _Doc) -> bool:
    ne = d.lang == "ne"
    ok = False
    if d.has("meta.event_date"):
        e = d.v("meta.event_date")
        c = d.cite("meta.event_date")
        d.add(f"घटना मिति: {e}{c}।" if ne else f"Event date: {e}{c}.")
        ok = True
    i = 1
    while d.has(f"meta.radar.{i}.after") or d.has(f"meta.radar.{i}.before"):
        k = f"meta.radar.{i}."
        b = d.v(k + "before") if d.has(k + "before") else "?"
        a = d.v(k + "after") if d.has(k + "after") else "?"
        tr = d.v(k + "track", False) if d.has(k + "track") else None
        c = d.cite(k + "before", k + "after", k + "track")
        if ne:
            trs = f" (ट्र्याक {tr})" if tr else ""
            d.add(f"राडार तस्बिर{trs}: पहिले {b}, पछि {a}{c}।")
        else:
            trs = f" (track {tr})" if tr else ""
            d.add(f"Radar images{trs}: before {b}, after {a}{c}.")
        ok = True
        i += 1
    return ok


METHOD = {
    "en": (f"{PRODUCT_NAME} compares Sentinel-1 radar images taken before and after the event to find new water and debris. "
           "It then checks which mapped roads, bridges and buildings they touch, and which settlements can no longer "
           "reach a health facility by road. Radar sees through cloud but cannot see into steep valleys in shadow. "
           "These numbers have not been checked on the ground."),
    "ne": (f"{PRODUCT_NAME_NE}ले घटनाअघि र पछि लिइएका सेन्टिनेल-१ राडार तस्बिर तुलना गरेर नयाँ पानी र गेग्रान पत्ता लगाउँछ। "
           "त्यसपछि कुन सडक, पुल र घरमा असर पर्‍यो, र कुन बस्तीबाट सडकमार्फत स्वास्थ्य संस्थासम्म पुग्न सकिँदैन भनेर हेर्छ। "
           "राडारले बादल छेडेर देख्छ, तर छायाँ परेका गहिरा खोंचहरू देख्दैन। "
           "यी अंक जमिनमा गएर जाँच गरिएका छैनन्।"),
}

REFUSALS = {
    "casualty": {
        "en": ("Satellites cannot see deaths, injuries or missing people, so this map has no such figures. "
               "The District Emergency Operations Centre and Nepal Police keep those counts."),
        "ne": ("भू-उपग्रहले मृत्यु, घाइते वा बेपत्ता मानिस देख्न सक्दैन, त्यसैले यो नक्सामा त्यस्तो तथ्याङ्क छैन। "
               "त्यो संख्या जिल्ला आपतकालीन कार्यसञ्चालन केन्द्र र नेपाल प्रहरीसँग हुन्छ।"),
    },
    "future": {
        "en": ("I can't predict when roads or bridges will reopen or what will happen next. "
               "The map only shows conditions on the dates the radar images were taken."),
        "ne": ("सडक वा पुल कहिले खुल्छ, वा अब के हुन्छ भनेर म अनुमान गर्न सक्दिनँ। "
               "नक्साले राडार तस्बिर लिइएको मितिको अवस्था मात्र देखाउँछ।"),
    },
    "weather": {
        "en": "I don't have weather data. Please check the Department of Hydrology and Meteorology forecast.",
        "ne": "मसँग मौसमको तथ्याङ्क छैन। कृपया जल तथा मौसम विज्ञान विभागको पूर्वानुमान हेर्नुहोस्।",
    },
    "inject": {
        "en": "I answer only from this map's figures. Ask me about flooding, damage, cut-off villages or repairs.",
        "ne": "म मेरो काम गर्ने तरिका बदल्न सक्दिनँ। म यो नक्साको तथ्याङ्क मात्र बताउँछु।",
    },
    "assert": {
        "en": "I only give figures that come from this map. Here is what it shows:",
        "ne": "म यो नक्साको तथ्याङ्क मात्र दोहोर्‍याउन सक्छु। अंक बदल्न, मिलाउन, अनुमान गर्न वा थप्न सक्दिनँ।",
    },
    "out_of_scope": {
        "en": ("That is not in this map's data. I can tell you where it flooded, which buildings, roads and bridges "
               "were hit, which settlements are cut off, the nearest health facility, and which repairs reconnect "
               "the most settlements."),
        "ne": ("यो जानकारी यो नक्साको तथ्याङ्कमा छैन। म कहाँ डुबान भयो, कुन घर, सडक र पुलमा क्षति भयो, कुन बस्तीको "
               "सम्पर्क टुट्यो, नजिकको स्वास्थ्य संस्था कुन हो, र कुन मर्मतले धेरै बस्ती जोड्छ भन्ने बताउन सक्छु।"),
    },
}

MISSING = {
    "en": "This run has no {topic} results yet.",
    "ne": "यो विश्लेषणमा {topic} को नतिजा अझै छैन।",
}
TOPICS = {
    "flood": ("flood area", "डुबान क्षेत्र"), "buildings": ("building damage", "घर क्षति"),
    "roads": ("road damage", "सडक क्षति"), "bridges": ("bridge damage", "पुल क्षति"),
    "cutoff": ("road access", "सडक पहुँच"), "health": ("health facility", "स्वास्थ्य संस्था"),
    "chokepoint": ("repair priority", "मर्मत प्राथमिकता"), "dates": ("image date", "तस्बिर मिति"),
}


def settlement_lines(d: _Doc, name_fact: dict) -> bool:
    k = name_fact["key"].rsplit(".", 1)[0] + "."
    ne = d.lang == "ne"
    name = d.v(k + "name")
    status = d.L.value(k + "status")
    pct = d.v(k + "p_cutoff_pct") if d.has(k + "p_cutoff_pct") else None
    c = d.cite(k + "name", k + "status", k + "p_cutoff_pct")
    if status == "cut_off":
        if ne:
            d.add(f"{name}को सडक सम्पर्क टुटेको हुन सक्छ" + (f" (सम्भावना {pct})" if pct else "") + f"{c}।")
        else:
            d.add(f"{name} is likely cut off by road" + (f" ({pct} chance)" if pct else "") + f"{c}.")
    else:
        if ne:
            d.add(f"{name} मा अझै सडकबाट पुग्न सकिने देखिन्छ" + (f" (सम्पर्क टुट्ने सम्भावना {pct})" if pct else "") + f"{c}।")
        else:
            d.add(f"{name} still appears reachable by road" + (f" ({pct} chance of being cut off)" if pct else "") + f"{c}.")
    if d.has(k + "nearest_health"):
        h = d.v(k + "nearest_health")
        mb = d.v(k + "minutes_before") if d.has(k + "minutes_before") else None
        ma = d.v(k + "minutes_after") if d.has(k + "minutes_after") else None
        wa = d.v(k + "walk_minutes_after") if d.has(k + "walk_minutes_after") else None
        c = d.cite(k + "nearest_health", k + "minutes_before", k + "minutes_after", k + "walk_minutes_after")
        if ne:
            s = f"नजिकको स्वास्थ्य संस्था {h} हो।"
            if mb:
                s += f" बाढीअघि गाडीमा {mb} लाग्थ्यो।"
            if ma:
                s += f" अहिले गाडीमा {ma} लाग्छ।"
            elif status == "cut_off":
                s += " अहिले सडक मार्ग देखिँदैन।"
            if wa:
                s += f" पैदल {wa} लाग्छ।"
            d.add(s[:-1] + f"{c}।")
        else:
            s = f"Its nearest health facility is {h}."
            if mb:
                s += f" Before the flood the drive took {mb}."
            if ma:
                s += f" Now the drive takes {ma}."
            elif status == "cut_off":
                s += " There is no road route now."
            if wa:
                s += f" Walking takes {wa}."
            d.add(s[:-1] + f"{c}.")
    return True


def summary_lines(d: _Doc) -> bool:
    ok = False
    for fn in (flood_lines, buildings_lines, roads_lines, bridges_lines, cutoff_lines):
        ok = fn(d) or ok
    if chokepoint_lines(d, n=1, header=False):
        ok = True
    return ok


_BUILDERS = {
    "flood": flood_lines, "buildings": buildings_lines, "roads": roads_lines, "bridges": bridges_lines,
    "cutoff": cutoff_lines, "people": cutoff_lines, "health": health_lines, "chokepoint": chokepoint_lines,
    "dates": dates_lines, "summary": summary_lines,
}


def answer_template(question: str, ledger: Ledger, lang: str = "auto", intent: Intent | None = None) -> dict:
    """Deterministic answer. Returns {answer, citations, refused, refusal_reasons, intents}."""
    if lang not in ("en", "ne"):
        lang = detect_lang(question)
    it = intent or classify(question, ledger)
    d = _Doc(ledger, lang)
    refusals = list(dict.fromkeys(it.refusals))
    for r in ("casualty", "inject", "assert", "future", "weather", "out_of_scope"):
        if r in refusals:
            d.add(REFUSALS[r][lang])
    data = [x for x in it.data if not (x == "flood" and "weather" in refusals and len(it.data) == 1)]
    # "people" alone next to a casualty question means deaths, not population
    if "casualty" in refusals:
        data = [x for x in data if x not in ("people", "damage")]
    if "method" in data and not it.settlements:
        d.add(METHOD[lang])
    done, used = set(), set()
    if it.settlements:
        for f in it.settlements[:3]:
            settlement_lines(d, f)
        done.update({"cutoff", "people", "health", "summary", "flood"})
    for x in data:
        fn = _BUILDERS.get(x)
        if fn is None or fn in used or x in done:
            continue
        used.add(fn)
        if not fn(d):
            topic = TOPICS.get(x)
            if topic:
                d.add(MISSING[lang].format(topic=topic[1] if lang == "ne" else topic[0]))
    answered = bool(d.lines) and any(l not in [REFUSALS[r][lang] for r in REFUSALS] for l in d.lines)
    if not d.lines or (not answered and not refusals):
        d.add(REFUSALS["out_of_scope"][lang])
        if "out_of_scope" not in refusals:
            refusals.append("out_of_scope")
    return {"answer": d.text(), "citations": list(dict.fromkeys(d.ids)), "refused": bool(refusals),
            "refusal_reasons": refusals, "intents": it.data, "lang": lang}


# ---------------------------------------------------------------- report sections

def report_sections(ledger: Ledger, lang: str) -> dict:
    """Text blocks for the situation report. Each value: {"lines": [...], "ids": [...]}."""
    out = {}
    for name, fns in (("flooded", [flood_lines]), ("hit", [buildings_lines, roads_lines, bridges_lines]),
                      ("cutoff", [cutoff_lines, health_lines]), ("chokepoints", [lambda d: chokepoint_lines(d, 5, False)]),
                      ("dates", [dates_lines])):
        d = _Doc(ledger, lang)
        for fn in fns:
            fn(d)
        out[name] = {"lines": d.lines, "ids": list(dict.fromkeys(d.ids))}
    return out
