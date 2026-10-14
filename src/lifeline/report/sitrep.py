"""One-page A4 situation report in English and Nepali, built from the ledger only.

    from lifeline.report.sitrep import build_sitrep
    build_sitrep("runs/<id>")   # writes sitrep_en.pdf, sitrep_ne.pdf, sitrep.json

Every line with a number comes from lifeline.copilot.templates and is checked by
the number guard before it is drawn. A line that fails the guard is left out
and listed in sitrep.json.

NOTE: a native Nepali speaker must review the Nepali headings and sentences.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from lifeline.copilot.branding import PRODUCT_NAME, PRODUCT_NAME_NE
from lifeline.copilot.guard import check
from lifeline.copilot.ledger import Ledger, build_facts, render_value
from lifeline.copilot.templates import report_sections

FONT_DIR = Path(__file__).parent / "fonts"
ACCENT = colors.HexColor("#14324F")
INK = colors.HexColor("#1A1A1A")
MUTED = colors.HexColor("#5A5A5A")
RULE = colors.HexColor("#B8B8B8")

ATTRIBUTIONS = [
    "Contains modified Copernicus Sentinel data 2026.",
    "Produced using Copernicus WorldDEM-30 © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018 "
    "provided under COPERNICUS by the European Union and ESA; all rights reserved.",
    "© OpenStreetMap contributors.",
]

TEXT = {
    "en": {
        "title": "Flood situation report",
        "area": "Area", "event": "Event date", "latest": "Latest radar image",
        "s1": "1. Where it flooded", "s2": "2. What was hit", "s3": "3. Who is cut off",
        "s4": "Repair priority", "map": "Radar change map, before and after the event.",
        "method_h": "Method",
        "method": ("Sentinel-1 radar images from before and after the event are compared to map new water and debris. "
                   "Pre-event OpenStreetMap roads, bridges and buildings are overlaid, and a road network model finds "
                   "settlements that can no longer reach a health facility."),
        "disclaimer": ("Educational prototype, not an operational tool. Numbers come from satellite analysis and have "
                       "not been checked on the ground."),
        "ids": "Grey codes such as F012 point to entries in facts.json.",
        "none": "No results in this run.",
    },
    "ne": {
        "title": "बाढी स्थिति प्रतिवेदन",
        "area": "क्षेत्र", "event": "घटना मिति", "latest": "पछिल्लो राडार तस्बिर",
        "s1": "१. कहाँ डुबान भयो", "s2": "२. के क्षति भयो", "s3": "३. कुन बस्तीको सम्पर्क टुट्यो",
        "s4": "मर्मत प्राथमिकता", "map": "घटनाअघि र पछिको राडार परिवर्तन नक्सा।",
        "method_h": "विधि",
        "method": ("घटनाअघि र पछिका सेन्टिनेल-१ राडार तस्बिर तुलना गरेर नयाँ पानी र गेग्रान नक्सामा देखाइएको छ। "
                   "घटनाअघिको OpenStreetMap का सडक, पुल र घर मिलाएर, सडक सञ्जाल विश्लेषणले स्वास्थ्य संस्थासम्म "
                   "पुग्न नसक्ने बस्ती पत्ता लगाइएको छ।"),
        "disclaimer": ("शैक्षिक नमुना मात्र, आपतकालीन सञ्चालनमा प्रयोग गर्ने उपकरण होइन। अंकहरू भू-उपग्रह "
                       "विश्लेषणबाट आएका हुन् र जमिनमा गएर जाँच गरिएका छैनन्।"),
        "ids": "F012 जस्ता खैरो कोडले facts.json को तथ्य जनाउँछन्।",
        "none": "यो विश्लेषणमा नतिजा छैन।",
    },
}


def _register_fonts() -> dict:
    fonts = {}
    for fam, base in (("en", "NotoSans"), ("ne", "NotoSansDevanagari")):
        reg, bold = f"Lifeline-{base}", f"Lifeline-{base}-Bold"
        if reg not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(reg, str(FONT_DIR / f"{base}-Regular.ttf")))
            pdfmetrics.registerFont(TTFont(bold, str(FONT_DIR / f"{base}-Bold.ttf")))
            pdfmetrics.registerFontFamily(reg, normal=reg, bold=bold, italic=reg, boldItalic=bold)
            pdfmetrics.registerFontFamily(bold, normal=bold, bold=bold, italic=bold, boldItalic=bold)
        fonts[fam] = (reg, bold)
    return fonts


def _styles(lang: str) -> dict:
    reg, bold = _register_fonts()[lang]
    shaping = 1 if lang == "ne" else 0
    lead = 1.32 if lang == "ne" else 1.25

    def st(name, size, font=reg, color=INK, **kw):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=size * lead, textColor=color,
                              alignment=TA_LEFT, shaping=shaping, **kw)

    return {
        "title": st("title", 20, bold, ACCENT),
        "meta": st("meta", 8.6, reg, MUTED),
        "h": st("h", 11.4, bold, ACCENT, spaceBefore=6, spaceAfter=2),
        "body": st("body", 9.6, spaceAfter=2),
        "small": st("small", 7.2, reg, MUTED),
        "tiny": st("tiny", 6.2, reg, MUTED),
        "warn": st("warn", 8.2, bold, INK),
    }


def _cite_markup(line: str, lang: str) -> str:
    """Escape a template line and turn [F012][F013] into small grey codes."""
    ids: list[str] = []

    def grab(m):
        ids.extend(re.findall(r"F\d{3,}", m.group(0)))
        return ""

    text = re.sub(r"\s*(\[F\d{3,}\])+", grab, line)
    out = escape(text)
    if ids:
        out += f' <font size="5.6" color="#8A8A8A">{" ".join(ids)}</font>'
    return out


def _guarded(lines: list[str], L: Ledger, dropped: list) -> list[str]:
    keep = []
    for ln in lines:
        g = check(ln, L)
        if g.ok:
            keep.append(ln)
        else:
            dropped.append({"line": ln, "violations": [v.__dict__ for v in g.violations]})
    return keep


def _header_meta(L: Ledger, lang: str) -> tuple[str, list[str]]:
    t = TEXT[lang]
    bits, ids = [], []
    for key, lab in (("meta.area_name", t["area"]), ("meta.event_date", t["event"]), ("meta.latest_image", t["latest"])):
        f = L.get(key)
        if f:
            bits.append(f"{lab}: {render_value(f, lang)}")
            ids.append(f["id"])
    return "   |   ".join(bits), ids


def _section(title: str, lines: list[str], S: dict, lang: str) -> list:
    out = [Paragraph(escape(title), S["h"])]
    if not lines:
        out.append(Paragraph(escape(TEXT[lang]["none"]), S["body"]))
    for ln in lines:
        out.append(Paragraph(_cite_markup(ln, lang), S["body"]))
    return out


def render_pdf(L: Ledger, run: Path, lang: str, out_path: Path) -> dict:
    S = _styles(lang)
    t = TEXT[lang]
    secs = report_sections(L, lang)
    dropped: list = []
    for k in secs:
        secs[k]["lines"] = _guarded(secs[k]["lines"], L, dropped)
    meta_line, meta_ids = _header_meta(L, lang)
    g = check(meta_line, L)
    if not g.ok:
        dropped.append({"line": meta_line, "violations": [v.__dict__ for v in g.violations]})
        meta_line = ""

    W, H = A4
    margin = 15 * mm
    body_w = W - 2 * margin
    footer_h = 24 * mm
    name = PRODUCT_NAME_NE if lang == "ne" else PRODUCT_NAME

    story: list = [Paragraph(escape(t["title"]), S["title"]), Spacer(1, 1.5 * mm)]
    if meta_line:
        story.append(Paragraph(escape(meta_line), S["meta"]))
    story.append(Spacer(1, 1 * mm))
    rule = Table([[""]], colWidths=[body_w], rowHeights=[0.8])
    rule.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 1.2, ACCENT)]))
    story += [rule, Spacer(1, 1.5 * mm)]

    left = []
    left += _section(t["s1"], secs["flooded"]["lines"], S, lang)
    left += _section(t["s2"], secs["hit"]["lines"], S, lang)
    left += _section(t["s3"], secs["cutoff"]["lines"], S, lang)

    img_path = run / "rasters" / "preview_change.png"
    right: list = []
    right_w = 62 * mm
    if img_path.exists():
        try:
            from PIL import Image as PILImage
            with PILImage.open(img_path) as im:
                iw, ih = im.size
            h = min(right_w * ih / iw, 80 * mm)
            w = h * iw / ih
            right = [Spacer(1, 4 * mm), Image(str(img_path), width=w, height=h),
                     Spacer(1, 1 * mm), Paragraph(escape(t["map"]), S["small"])]
        except Exception:
            right = []
    if right:
        cols = Table([[KeepInFrame(body_w - right_w - 6 * mm, 150 * mm, left, mode="shrink"), right]],
                     colWidths=[body_w - right_w - 6 * mm, right_w + 6 * mm])
        cols.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (0, 0), 6 * mm), ("RIGHTPADDING", (1, 0), (1, 0), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        story.append(cols)
    else:
        story += left

    story += _section(t["s4"], secs["chokepoints"]["lines"], S, lang)
    story += [Paragraph(escape(t["method_h"]), S["h"]), Paragraph(escape(t["method"]), S["body"]),
              Spacer(1, 2 * mm)]
    warn = Table([[Paragraph(escape(t["disclaimer"]), S["warn"])]], colWidths=[body_w])
    warn.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.9, INK), ("LEFTPADDING", (0, 0), (-1, -1), 5),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [warn, Spacer(1, 1.5 * mm), Paragraph(escape(t["ids"]), S["tiny"])]

    frame_h = H - 2 * margin - footer_h
    content = [KeepInFrame(body_w, frame_h, story, mode="shrink")]

    en_reg = _register_fonts()["en"][0]

    def footer(canv, doc):
        canv.saveState()
        y = margin + footer_h - 4 * mm
        canv.setStrokeColor(RULE)
        canv.setLineWidth(0.5)
        canv.line(margin, y + 2.5 * mm, W - margin, y + 2.5 * mm)
        canv.setFillColor(MUTED)
        canv.setFont(en_reg, 6.3)
        from reportlab.lib.utils import simpleSplit
        for a in ATTRIBUTIONS:
            for part in simpleSplit(a, en_reg, 6.3, body_w):
                canv.drawString(margin, y, part)
                y -= 7.6
        canv.setFont(en_reg, 6.8)
        canv.setFillColor(ACCENT)
        canv.drawString(margin, margin - 2 * mm, f"{PRODUCT_NAME} situation report, generated from facts.json")
        canv.restoreState()

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin + footer_h,
                            title=f"{PRODUCT_NAME}: {TEXT['en']['title']}", author=PRODUCT_NAME,
                            subject=t["title"])
    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    ids = sorted({i for s in secs.values() for i in s["ids"]} | set(meta_ids))
    return {"file": out_path.name, "sections": {k: v["lines"] for k, v in secs.items()},
            "fact_ids": ids, "dropped_lines": dropped, "name": name}


def build_sitrep(run_dir: str | Path, langs: tuple[str, ...] = ("en", "ne")) -> dict:
    """Write sitrep_en.pdf, sitrep_ne.pdf and sitrep.json into run_dir. Returns the sitrep.json content."""
    run = Path(run_dir)
    L = Ledger.load(run)
    if len(L) == 0:
        L = build_facts(run)
    out = {"product": PRODUCT_NAME, "source": "facts.json", "attributions": ATTRIBUTIONS,
           "disclaimer": TEXT["en"]["disclaimer"], "languages": {}}
    for lang in langs:
        out["languages"][lang] = render_pdf(L, run, lang, run / f"sitrep_{lang}.pdf")
    (run / "sitrep.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    res = build_sitrep(sys.argv[1])
    for lang, r in res["languages"].items():
        print(lang, r["file"], len(r["fact_ids"]), "facts,", len(r["dropped_lines"]), "lines dropped")
