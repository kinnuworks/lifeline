"""A short field alert for SMS or WhatsApp, in English and Nepali.

Built only from the facts ledger and checked by the same number guard as the
copilot, so every figure in it traces back to a map file. Kept under about
400 characters so it fits a couple of SMS segments.
"""
from __future__ import annotations

from pathlib import Path

from ..copilot.guard import check
from ..copilot.ledger import Ledger, build_facts, render_value


def _v(L: Ledger, key: str, lang: str, unit: bool = False) -> str | None:
    f = L.get(key)
    return render_value(f, lang, unit) if f else None


def build_alert(run_dir: str | Path) -> dict:
    run = Path(run_dir)
    L = Ledger.load(run)
    if len(L) == 0:
        L = build_facts(run)
    out = {}
    for lang in ("en", "ne"):
        date = _v(L, "meta.event_date", lang)
        cut, tot = _v(L, "access.villages.cut_off", lang), _v(L, "access.villages.total", lang)
        km2 = _v(L, "flood.area_km2.total", lang, True)
        bld = _v(L, "damage.buildings.destroyed_likely", lang)
        br = _v(L, "damage.bridges.destroyed_likely", lang)
        fix, fixn = _v(L, "chokepoint.1.name", lang), _v(L, "chokepoint.1.villages", lang)
        care = _v(L, "access.health.nearest_for_cut_off", lang)
        lines = []
        if lang == "en":
            lines.append(f"FLOOD ALERT {date}." if date else "FLOOD ALERT.")
            if cut and tot:
                lines.append(f"{cut} of {tot} settlements have no road to a hospital or clinic.")
            if fix and fixn:
                lines.append(f"Reopen first: {fix} (reconnects {fixn} settlements).")
            if km2:
                lines.append(f"{km2} flooded or buried by debris" + (f"; {bld} buildings and {br} bridges likely destroyed." if bld and br else "."))
            if care:
                lines.append(f"Nearest care for most cut-off places: {care}.")
            lines.append("From satellite analysis; not checked on the ground.")
        else:
            lines.append(f"बाढी सूचना {date}।" if date else "बाढी सूचना।")
            if cut and tot:
                lines.append(f"{tot} मध्ये {cut} बस्तीबाट अस्पताल वा स्वास्थ्य संस्थासम्म सडक छैन।")
            if fix and fixn:
                lines.append(f"पहिले खोल्नुहोस्: {fix} ({fixn} बस्ती फेरि जोडिन्छन्)।")
            if km2:
                lines.append(f"{km2} क्षेत्र डुबान वा गेग्रानले पुरिएको" + (f"; {bld} घर र {br} पुल ध्वस्त भएको हुन सक्छ।" if bld and br else "।"))
            if care:
                lines.append(f"धेरैजसो सम्पर्कविहीन बस्तीका लागि नजिकको स्वास्थ्य संस्था: {care}।")
            lines.append("स्याटेलाइट विश्लेषणमा आधारित; जमिनमा जाँच गरिएको छैन।")
        text = " ".join(lines)
        g = check(text, L)
        if not g.ok:   # never ship a figure the guard cannot trace; drop the offending sentences
            text = " ".join(s for s in lines if check(s, L).ok)
        out[lang] = text
    return out
