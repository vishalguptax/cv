#!/usr/bin/env python3
"""Build the CV website from the PDF résumé in CV/.

The PDF is the single source of truth. This script reads its text with
PyMuPDF (keeping bold/italic, links and layout), turns it into a small data
model, and writes a static site:

  index.html  semantic résumé page (JSON-LD, Open Graph, light/dark, print CSS)
  resume.pdf  the PDF itself, at a stable URL
  resume.md, resume.txt, resume.json (JSON Resume), resume.docx (if pandoc
  is installed), vishal-gupta.vcf, llms.txt, robots.txt, sitemap.xml,
  og.png, icons, 404.html and CNAME.

Usage: python build/build.py [--pdf CV/file.pdf] [--out _site]
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import html.parser
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
SITE_URL = os.environ.get("SITE_URL", "https://cv.vishalg.in").rstrip("/")
DOMAIN = re.sub(r"^https?://", "", SITE_URL)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# --------------------------------------------------------------------------
# 1. Read the PDF into rows of styled spans
# --------------------------------------------------------------------------

@dataclass
class Seg:
    text: str
    bold: bool = False
    italic: bool = False
    url: str | None = None


@dataclass
class Row:
    page: int
    y: float
    x0: float
    size: float
    segs: list[Seg]
    right: list[Seg] = field(default_factory=list)  # right-aligned text (dates | place)
    bullet: bool = False

    @property
    def text(self) -> str:
        return norm("".join(s.text for s in self.segs))

    @property
    def right_text(self) -> str:
        return norm("".join(s.text for s in self.right))

    @property
    def all_bold(self) -> bool:
        return all(s.bold for s in self.segs if s.text.strip())

    @property
    def all_italic(self) -> bool:
        return all(s.italic for s in self.segs if s.text.strip())


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


BULLETS = "•●▪◦‣–-*"


def read_rows(pdf: Path) -> tuple[list[Row], float, dict]:
    doc = pymupdf.open(pdf)
    rows: list[Row] = []
    sizes: Counter = Counter()
    width = doc[0].rect.width
    for pno, page in enumerate(doc):
        links = [(pymupdf.Rect(l["from"]), l["uri"]) for l in page.get_links() if l.get("uri")]

        def link_at(bbox):
            r = pymupdf.Rect(bbox)
            c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            for rect, uri in links:
                if c in rect:
                    return uri
            return None

        pieces = []  # (y, x0, size, segs, bullet)
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                segs, bullet, big, fx = [], False, 0.0, None
                for s in line["spans"]:
                    t = s["text"].replace("\xa0", " ")
                    if s["size"] < 3:  # tiny glyphs: list markers and spacers
                        bullet = bullet or any(b in t for b in "•●▪")
                        continue
                    if not segs and t.strip() and t.strip()[0] in "•●▪" and len(t.strip()) <= 2:
                        bullet = True
                        continue
                    f = s["font"].lower()
                    segs.append(Seg(t, bool(s["flags"] & 16) or "bold" in f or "semibold" in f,
                                    bool(s["flags"] & 2) or "italic" in f or "oblique" in f,
                                    link_at(s["bbox"])))
                    big = max(big, s["size"])
                    if fx is None and t.strip():
                        fx = s["bbox"][0]
                    sizes[round(s["size"], 1)] += len(t)
                if segs and norm("".join(x.text for x in segs)):
                    pieces.append((line["bbox"][1], fx if fx is not None else line["bbox"][0], big, segs, bullet))
                elif bullet:
                    pieces.append((line["bbox"][1], line["bbox"][0], 0.0, [], True))

        # Bullets drawn as small vector shapes rather than text glyphs
        dots = [d["rect"] for d in page.get_drawings()
                if d["rect"].width < 6 and d["rect"].height < 6 and abs(d["rect"].width - d["rect"].height) < 1.5]

        # Merge pieces that sit on the same baseline into one row; text that starts in the
        # right part of the page after a gap is kept apart as the row's right-hand column.
        pieces.sort(key=lambda p: (round(p[0]), p[1]))
        for y, x0, size, segs, bullet in pieces:
            prev = rows[-1] if rows and rows[-1].page == pno else None
            if prev and abs(prev.y - y) < 2.5:
                if not prev.segs:  # a lone bullet marker followed by its text
                    prev.x0, prev.segs, prev.size = x0, segs, size
                    continue
                if x0 > width * 0.55 and prev.x0 < width * 0.2:
                    prev.right.extend(segs)
                else:
                    prev.segs.extend(segs)
                prev.bullet = prev.bullet or bullet
                prev.size = max(prev.size, size)
                continue
            rows.append(Row(pno, y, x0, size, list(segs), bullet=bullet))
        for r in rows:
            if r.page == pno and not r.bullet and any(
                    r.x0 - 16 < d.x1 <= r.x0 + 1 and r.y - 1 < (d.y0 + d.y1) / 2 < r.y + r.size + 3 for d in dots):
                r.bullet = True
    meta = doc.metadata or {}
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    return [r for r in rows if r.segs], body, {"meta": meta, "width": width}


# --------------------------------------------------------------------------
# 2. Turn rows into a résumé model
# --------------------------------------------------------------------------

def merge_segs(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s in segs:
        t = s.text.replace("\xa0", " ")
        if out and (out[-1].bold, out[-1].italic, out[-1].url) == (s.bold, s.italic, s.url):
            out[-1].text += t
        else:
            out.append(Seg(t, s.bold, s.italic, s.url))
    for s in out:
        s.text = re.sub(r"\s+", " ", s.text)
    if out:
        out[0].text = out[0].text.lstrip()
        out[-1].text = out[-1].text.rstrip()
    return [s for s in out if s.text]


def append_line(rich: list[Seg], segs: list[Seg]) -> None:
    """Join a wrapped line onto the text before it (handles hyphenated breaks)."""
    if not rich:
        rich.extend(segs)
        return
    last = rich[-1].text.rstrip()
    first = next((s.text.lstrip() for s in segs if s.text.strip()), "")
    if re.search(r"[A-Za-z]-$", last) and first[:1].isalpha():
        rich[-1].text = last  # "on-" + "site" -> "on-site"
    else:
        rich[-1].text = last + " "
    rich.extend(Seg(s.text.lstrip() if i == 0 else s.text, s.bold, s.italic, s.url)
                for i, s in enumerate(segs))


def plain(rich: list[Seg]) -> str:
    return norm("".join(s.text for s in rich))


SECTION_WORDS = re.compile(
    r"^(profile|summary|professional summary|about( me)?|objective|(professional |work )?experience|employment"
    r"( history)?|education|skills|technical skills|core skills|key skills|projects|certifications?|awards"
    r"|achievements|publications|languages|interests|volunteering|references|courses|training)$", re.I)


def is_heading(row: Row, body: float) -> bool:
    t = row.text
    if row.bullet or row.right or len(t) > 40:
        return False
    letters = re.sub(r"[^A-Za-z]", "", t)
    if len(letters) >= 3 and t.upper() == t and row.all_bold and row.size >= body:
        return True  # "PROFESSIONAL EXPERIENCE"
    # "Experience", "Technical Skills": a known section name set apart by weight or size
    return bool(SECTION_WORDS.match(t.strip(" :"))) and (row.all_bold or row.size >= body * 1.15)


def title_case(s: str) -> str:
    small = {"and", "of", "the", "in", "for", "to", "a", "an", "&"}
    words = s.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def section_id(title: str) -> str:
    t = title.lower()
    for key, sid in [("experience", "experience"), ("employment", "experience"), ("skill", "skills"),
                     ("education", "education"), ("profile", "summary"), ("summary", "summary"),
                     ("about", "summary"), ("project", "projects"), ("certif", "certifications")]:
        if key in t:
            return sid
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "section"


def parse_period(s: str):
    """'Nov 2025 – Present | Mumbai, India' -> ((2025, 11), None, 'Nov 2025 – Present', 'Mumbai, India')."""
    parts = [norm(p) for p in s.split("|")]
    dates = next((p for p in parts if re.search(r"\b(19|20)\d{2}\b", p)), "")
    place = " | ".join(p for p in parts if p != dates)

    def ym(txt):
        m = re.search(r"([A-Za-z]{3,})\.?\s+((?:19|20)\d{2})", txt)
        if m and m.group(1)[:3].lower() in MONTHS:
            return (int(m.group(2)), MONTHS[m.group(1)[:3].lower()])
        m = re.search(r"\b((?:19|20)\d{2})\b", txt)
        return (int(m.group(1)), 1) if m else None

    halves = re.split(r"\s+[–—-]\s+|\s+to\s+", dates, maxsplit=1)
    start = ym(halves[0]) if dates else None
    end = None
    current = bool(re.search(r"present|current|now", dates, re.I))
    if len(halves) > 1 and not current:
        end = ym(halves[1])
    return {"start": start, "end": end, "current": current, "dates": dates, "location": place}


def build_model(rows: list[Row], body: float) -> dict:
    first_h = next((i for i, r in enumerate(rows) if is_heading(r, body)), len(rows))
    head = rows[:first_h]
    name_row = max(head, key=lambda r: r.size)
    rest = [r for r in head if r is not name_row]
    title = rest[0].text if rest else ""
    contacts = []
    for r in rest[1:]:
        segs = merge_segs(r.segs + r.right)
        # split on "|" (or "·") separators while keeping each part's link
        cur_text, cur_url = "", None
        for s in segs + [Seg("|")]:
            for i, chunk in enumerate(re.split(r"\s*[|·•]\s*", s.text)):
                if i > 0 and norm(cur_text):
                    contacts.append(classify_contact(norm(cur_text), cur_url))
                    cur_text, cur_url = "", None
                elif i > 0:
                    cur_text, cur_url = "", None
                cur_text += chunk
                if chunk.strip() and s.url:
                    cur_url = s.url

    sections = []
    for r in rows[first_h:]:
        if is_heading(r, body):
            raw = r.text
            sections.append({"title": title_case(raw), "id": section_id(raw), "rows": []})
        else:
            sections[-1]["rows"].append(r)

    for sec in sections:
        sec["items"] = parse_section(sec.pop("rows"), body)
        if sec["id"] == "skills" or all(i["type"] == "list" for i in sec["items"]):
            for it in sec["items"]:
                if it["type"] == "list":
                    for b in it["items"]:
                        split_label(b)
    used = Counter()
    for sec in sections:  # keep ids unique
        used[sec["id"]] += 1
        if used[sec["id"]] > 1:
            sec["id"] += f"-{used[sec['id']]}"
    return {"name": name_row.text, "title": title, "contacts": contacts, "sections": sections}


def classify_contact(text: str, url: str | None) -> dict:
    u = (url or "").lower()
    if u.startswith("mailto:") or re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", text):
        return {"kind": "email", "text": text, "url": url or f"mailto:{text}"}
    if u.startswith("tel:") or re.fullmatch(r"\+?[\d\s().-]{7,}", text):
        return {"kind": "phone", "text": text, "url": url or "tel:" + re.sub(r"[^\d+]", "", text)}
    if "github.com" in u:
        return {"kind": "github", "text": text, "url": url}
    if "linkedin.com" in u:
        return {"kind": "linkedin", "text": text, "url": url}
    if url:
        return {"kind": "website", "text": text, "url": url}
    return {"kind": "location", "text": text, "url": None}


def split_label(item: dict) -> None:
    """'**Frontend:** React, Next.js' -> label 'Frontend', values ['React', 'Next.js']."""
    rich = item["rich"]
    if rich and rich[0].bold and ":" in rich[0].text:
        label, _, tail = rich[0].text.partition(":")
        rest = plain([Seg(tail)] + rich[1:])
        item["label"] = norm(label)
        item["values"] = [v.strip() for v in re.split(r",(?![^()]*\))", rest) if v.strip()]


def parse_section(rows: list[Row], body: float) -> list[dict]:
    items: list[dict] = []
    last_row: Row | None = None
    bullet_x = None  # x of the open bullet's text, while wrapped lines may follow

    def target_list():
        """The bullet list new bullets go into: the open entry's, or a loose list."""
        if items and items[-1]["type"] == "entry":
            e = items[-1]
            return (e["roles"][-1] if e["roles"] else e)["bullets"]
        if items and items[-1]["type"] == "list":
            return items[-1]["items"]
        items.append({"type": "list", "items": []})
        return items[-1]["items"]

    for r in rows:
        gap = r.y - last_row.y if last_row and last_row.page == r.page else 99
        close = gap < max(r.size, body) * 1.6
        segs = r.segs
        if r.bullet:
            target_list().append({"rich": merge_segs(segs)})
            bullet_x = r.x0
        elif bullet_x is not None and close and not r.right and abs(r.x0 - bullet_x) < 3:
            lst = target_list()
            append_line(lst[-1]["rich"], segs)
            lst[-1]["rich"] = merge_segs(lst[-1]["rich"])
        elif r.right or (segs[0].bold and not r.bullet and len(r.text) < 90):
            bullet_x = None
            head = merge_segs(segs)
            bold = norm("".join(s.text for s in head if s.bold)).rstrip(",;:—– ")
            rest = norm("".join(s.text for s in head if not s.bold)).strip(",;:—– ")
            org_url = next((s.url for s in head if s.url), None)
            entry = {"type": "entry", "title": bold or plain(head), "org": rest, "org_url": org_url,
                     "x0": r.x0, "bullets": [], "roles": [], **parse_period(r.right_text)}
            prev = items[-1] if items and items[-1]["type"] == "entry" else None
            if prev and r.x0 > prev["x0"] + 4 and not prev["bullets"] and (prev["roles"] or not prev["dates"]):
                prev["roles"].append(entry)  # a role inside a company group
            elif prev and prev["roles"] and r.x0 > prev["x0"] + 4:
                prev["roles"].append(entry)
            else:
                items.append(entry)
        elif r.all_italic and items and items[-1]["type"] == "entry" and close:
            e = items[-1]
            e["org"] = norm((e["org"] + ", " if e["org"] else "") + r.text)
            e["org_url"] = e["org_url"] or next((s.url for s in segs if s.url), None)
        elif items and items[-1]["type"] == "para" and close:
            append_line(items[-1]["rich"], segs)
            items[-1]["rich"] = merge_segs(items[-1]["rich"])
        else:
            bullet_x = None
            items.append({"type": "para", "rich": merge_segs(segs)})
        last_row = r

    for it in items:  # a group's own period spans its roles
        if it["type"] == "entry" and it["roles"]:
            starts = [x["start"] for x in it["roles"] if x["start"]]
            ends = [x["end"] for x in it["roles"] if x["end"]]
            it["start"] = it["start"] or (min(starts) if starts else None)
            it["current"] = it["current"] or any(x["current"] for x in it["roles"])
            it["end"] = None if it["current"] else (it["end"] or (max(ends) if ends else None))
            locs = {x["location"] for x in it["roles"] if x["location"]}
            it["location"] = it["location"] or (locs.pop() if len(locs) == 1 else "")
            if not it["dates"] and it["start"]:
                it["dates"] = fmt_ym(it["start"]) + " – " + ("Present" if it["current"] else fmt_ym(it["end"]) if it["end"] else "")
    return items


# --------------------------------------------------------------------------
# 3. Renderers
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def rich_html(rich: list[Seg]) -> str:
    out = []
    for s in rich:
        t = esc(s.text)
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.url and s.url.startswith(("http", "mailto:")):
            t = f'<a href="{esc(s.url)}">{t}</a>'
        out.append(t)
    return "".join(out)


def rich_md(rich: list[Seg]) -> str:
    out = []
    for s in rich:
        lead = s.text[: len(s.text) - len(s.text.lstrip())]
        trail = s.text[len(s.text.rstrip()):]
        core = re.sub(r"([*_`\[\]])", r"\\\1", s.text.strip())
        if core and s.bold:
            core = f"**{core}**"
        if core and s.italic:
            core = f"*{core}*"
        out.append(lead + core + trail)
    return "".join(out)


def fmt_ym(ym) -> str:
    return dt.date(ym[0], ym[1], 1).strftime("%b %Y")


def ym_str(ym) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}" if ym else ""


def months_between(a, b) -> int:
    return (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1


def duration(n: int) -> str:
    y, m = divmod(n, 12)
    parts = []
    if y:
        parts.append(f"{y} yr" + ("s" if y > 1 else ""))
    if m:
        parts.append(f"{m} mo" + ("s" if m > 1 else ""))
    return " ".join(parts) or "1 mo"


def period_html(e: dict) -> str:
    bits = []
    if e["dates"]:
        d = esc(e["dates"])
        if e["start"]:
            d = re.sub(r"^([A-Za-z]+\.?\s+\d{4}|\d{4})",
                       lambda m: f'<time datetime="{ym_str(e["start"])}">{m.group(1)}</time>', d, count=1)
        if e["end"]:
            d = re.sub(r"(?<=[–—-] )([A-Za-z]+\.?\s+\d{4}|\d{4})$",
                       lambda m: f'<time datetime="{ym_str(e["end"])}">{m.group(1)}</time>', d, count=1)
        bits.append(f'<span class="dates">{d}</span>')
        if e["start"] and e["end"]:
            bits.append(f'<span class="dur">{duration(months_between(e["start"], e["end"]))}</span>')
        elif e["start"] and e["current"]:
            bits.append(f'<span class="dur" data-since="{ym_str(e["start"])}"></span>')
    if e["location"]:
        bits.append(f'<span class="loc">{esc(e["location"])}</span>')
    return f'<p class="meta">{"".join(bits)}</p>' if bits else ""


def bullets_html(bullets: list[dict]) -> str:
    if not bullets:
        return ""
    return "<ul>" + "".join(f"<li>{rich_html(b['rich'])}</li>" for b in bullets) + "</ul>"


def org_html(e: dict) -> str:
    if not e["org"]:
        return ""
    o = esc(e["org"])
    if e["org_url"]:
        o = f'<a href="{esc(e["org_url"])}">{o}</a>'
    return f'<p class="org">{o}</p>'


def entry_html(e: dict) -> str:
    if e["roles"]:
        roles = "".join(
            f'<li class="role"><div class="role-head"><h4>{esc(r["title"])}</h4>{period_html(r)}</div>'
            f'{bullets_html(r["bullets"])}</li>' for r in e["roles"])
        n = len(e["roles"])
        return (f'<article class="entry group"><div class="entry-head"><div><h3>{org_link(e)}</h3>'
                f'<p class="org">{n} role{"s" if n > 1 else ""}</p></div>{period_html(e)}</div>'
                f'{bullets_html(e["bullets"])}<ol class="roles" reversed>{roles}</ol></article>')
    return (f'<article class="entry"><div class="entry-head"><div><h3>{esc(e["title"])}</h3>{org_html(e)}</div>'
            f'{period_html(e)}</div>{bullets_html(e["bullets"])}</article>')


def org_link(e: dict) -> str:
    t = esc(e["title"])
    return f'<a href="{esc(e["org_url"])}">{t}</a>' if e["org_url"] else t


def section_html(sec: dict) -> str:
    parts = []
    for it in sec["items"]:
        if it["type"] == "para":
            parts.append(f"<p>{rich_html(it['rich'])}</p>")
        elif it["type"] == "entry":
            parts.append(entry_html(it))
        elif it["type"] == "list":
            if all("label" in b for b in it["items"]):
                parts.append('<dl class="skills">' + "".join(
                    f'<div><dt>{esc(b["label"])}</dt><dd><ul>'
                    + "".join(f"<li>{esc(v)}</li>" for v in b["values"]) + "</ul></dd></div>"
                    for b in it["items"]) + "</dl>")
            else:
                parts.append(bullets_html(it["items"]))
    sid = sec["id"]
    return (f'<section id="{sid}" aria-labelledby="{sid}-h"><h2 id="{sid}-h">{esc(sec["title"])}</h2>'
            + "".join(parts) + "</section>")


def to_markdown(cv: dict, updated: dt.date) -> str:
    L = [f"# {cv['name']}", "", f"**{cv['title']}**", ""]
    L.append(" | ".join(f"[{c['text']}]({c['url']})" if c["url"] and c["kind"] not in ("email", "phone")
                        else c["text"] for c in cv["contacts"]))
    L.append("")
    for sec in cv["sections"]:
        L += [f"## {sec['title']}", ""]
        for it in sec["items"]:
            if it["type"] == "para":
                L += [rich_md(it["rich"]), ""]
            elif it["type"] == "list":
                for b in it["items"]:
                    L.append(f"- **{b['label']}:** {', '.join(b['values'])}" if "label" in b
                             else f"- {rich_md(b['rich'])}")
                L.append("")
            else:
                def md_entry(e, level):
                    head = e["title"] + (f" — {e['org']}" if e["org"] else "")
                    L.append(f"{'#' * level} {head}")
                    meta = " | ".join(x for x in (e["dates"], e["location"]) if x)
                    if meta:
                        L.extend(["", f"*{meta}*"])
                    L.append("")
                    for b in e["bullets"]:
                        L.append(f"- {rich_md(b['rich'])}")
                    if e["bullets"]:
                        L.append("")
                    for r in e["roles"]:
                        md_entry(r, level + 1)
                md_entry(it, 3)
    L += ["---", "", f"Web: {SITE_URL}/ · PDF: {SITE_URL}/resume.pdf · Updated {updated.isoformat()}", ""]
    return "\n".join(L)


def to_text(cv: dict, updated: dt.date) -> str:
    L = [cv["name"].upper(), cv["title"], ""]
    for c in cv["contacts"]:
        label = {"email": "Email", "phone": "Phone", "location": "Location", "github": "GitHub",
                 "linkedin": "LinkedIn"}.get(c["kind"], c["text"])
        value = c["text"] if c["kind"] in ("email", "phone", "location") else c["url"]
        L.append(f"{label}: {value}")
    L.append(f"Website: {SITE_URL}/")
    for sec in cv["sections"]:
        L += ["", sec["title"].upper(), "-" * len(sec["title"])]
        for it in sec["items"]:
            if it["type"] == "para":
                L.append(plain(it["rich"]))
            elif it["type"] == "list":
                for b in it["items"]:
                    L.append(f"- {b['label']}: {', '.join(b['values'])}" if "label" in b else f"- {plain(b['rich'])}")
            else:
                def txt_entry(e, group=None):
                    L.append("")
                    head = [e["title"]] + ([e["org"]] if e["org"] else []) + ([group] if group else [])
                    L.append(" | ".join(head))
                    meta = " | ".join(x for x in (e["dates"], e["location"]) if x)
                    if meta:
                        L.append(meta)
                    for b in e["bullets"]:
                        L.append(f"- {plain(b['rich'])}")
                    for r in e["roles"]:
                        txt_entry(r, e["title"])
                if it["roles"]:
                    for r in it["roles"]:
                        txt_entry(r, it["title"])
                else:
                    txt_entry(it)
    L += ["", f"PDF: {SITE_URL}/resume.pdf", f"Updated: {updated.isoformat()}", ""]
    return "\n".join(L)


def contact(cv, kind):
    return next((c for c in cv["contacts"] if c["kind"] == kind), None)


def find_section(cv, sid):
    return next((s for s in cv["sections"] if s["id"] == sid), None)


def summary_text(cv) -> str:
    s = find_section(cv, "summary")
    return " ".join(plain(i["rich"]) for i in s["items"] if i["type"] == "para") if s else cv["title"]


def skills_flat(cv) -> list[str]:
    s = find_section(cv, "skills")
    out = []
    for it in (s["items"] if s else []):
        for b in it.get("items", []):
            out += b.get("values", [])
    return out


def entries(cv, sid):
    s = find_section(cv, sid)
    return [i for i in (s["items"] if s else []) if i["type"] == "entry"]


def split_location(text: str):
    parts = [p.strip() for p in text.split(",")]
    return (parts[0], parts[-1]) if len(parts) > 1 else (text, "")


def to_json_resume(cv: dict, updated: dt.date) -> dict:
    loc = contact(cv, "location")
    city, country = split_location(loc["text"]) if loc else ("", "")
    profiles = [{"network": {"github": "GitHub", "linkedin": "LinkedIn"}[k], "username": c["url"].rstrip("/").rsplit("/", 1)[-1],
                 "url": c["url"]} for k in ("github", "linkedin") if (c := contact(cv, k))]
    work = []
    for e in entries(cv, "experience"):
        for r in (e["roles"] or [e]):
            org = e["title"] if e["roles"] else (e["org"] or e["title"])
            work.append({"name": org, "position": r["title"] if e["roles"] or e["org"] else "",
                         "url": e["org_url"] or None, "location": r["location"] or None,
                         "startDate": ym_str(r["start"]) or None,
                         "endDate": ym_str(r["end"]) or None,
                         "highlights": [plain(b["rich"]) for b in r["bullets"]]})
    edu = [{"institution": e["org"] or e["title"], "studyType": e["title"].split(",")[0] if e["org"] else "",
            "area": e["title"].split(",", 1)[1].strip() if e["org"] and "," in e["title"] else "",
            "url": e["org_url"] or None, "startDate": ym_str(e["start"]) or None, "endDate": ym_str(e["end"]) or None}
           for e in entries(cv, "education")]
    sk = find_section(cv, "skills")
    skills = [{"name": b["label"], "keywords": b["values"]} for it in (sk["items"] if sk else [])
              for b in it.get("items", []) if "label" in b]
    clean = lambda d: {k: v for k, v in d.items() if v not in (None, "", [])}
    return {
        "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
        "basics": clean({"name": cv["name"], "label": cv["title"],
                         "email": (contact(cv, "email") or {}).get("text"),
                         "phone": (contact(cv, "phone") or {}).get("text"),
                         "url": SITE_URL + "/", "summary": summary_text(cv),
                         "location": clean({"city": city, "countryCode": country_code(country)}),
                         "profiles": profiles}),
        "work": [clean(w) for w in work],
        "education": [clean(e) for e in edu],
        "skills": skills,
        "meta": {"canonical": SITE_URL + "/resume.json", "lastModified": updated.isoformat(),
                 "source": SITE_URL + "/resume.pdf"},
    }


def country_code(name: str) -> str:
    return {"india": "IN", "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB",
            "germany": "DE", "canada": "CA", "singapore": "SG", "uae": "AE"}.get(name.lower(), name)


def to_jsonld(cv: dict, updated: dt.date) -> dict:
    loc = contact(cv, "location")
    city, country = split_location(loc["text"]) if loc else ("", "")
    person = {
        "@type": "Person", "@id": SITE_URL + "/#person", "name": cv["name"], "jobTitle": cv["title"],
        "url": SITE_URL + "/", "description": summary_text(cv),
        "sameAs": [c["url"] for k in ("github", "linkedin", "website") if (c := contact(cv, k))],
        "knowsAbout": skills_flat(cv),
    }
    if (c := contact(cv, "email")):
        person["email"] = "mailto:" + c["text"]
    if (c := contact(cv, "phone")):
        person["telephone"] = c["text"]
    if loc:
        person["address"] = {"@type": "PostalAddress", "addressLocality": city,
                             "addressCountry": country_code(country)}
    current = next((e for e in entries(cv, "experience") if e["current"]), None)
    if current:
        org = current["title"] if current["roles"] else (current["org"] or current["title"])
        person["worksFor"] = {"@type": "Organization", "name": org, **({"url": current["org_url"]} if current["org_url"] else {})}
    alumni = [{"@type": "EducationalOrganization", "name": e["org"] or e["title"],
               **({"url": e["org_url"]} if e["org_url"] else {})} for e in entries(cv, "education")]
    if alumni:
        person["alumniOf"] = alumni
    return {
        "@context": "https://schema.org", "@type": "ProfilePage", "@id": SITE_URL + "/",
        "url": SITE_URL + "/", "name": f"{cv['name']} — {cv['title']}", "dateModified": updated.isoformat(),
        "mainEntity": person,
        "associatedMedia": {"@type": "MediaObject", "contentUrl": SITE_URL + "/resume.pdf",
                            "encodingFormat": "application/pdf", "name": f"{cv['name']} résumé (PDF)"},
    }


def to_vcard(cv: dict) -> str:
    first, _, last = cv["name"].partition(" ")
    L = ["BEGIN:VCARD", "VERSION:3.0", f"N:{last};{first};;;", f"FN:{cv['name']}", f"TITLE:{cv['title']}"]
    current = next((e for e in entries(cv, "experience") if e["current"]), None)
    if current:
        L.append(f"ORG:{current['title'] if current['roles'] else (current['org'] or current['title'])}")
    if (c := contact(cv, "email")):
        L.append(f"EMAIL;TYPE=INTERNET:{c['text']}")
    if (c := contact(cv, "phone")):
        L.append(f"TEL;TYPE=CELL:{c['text']}")
    if (c := contact(cv, "location")):
        city, country = split_location(c["text"])
        L.append(f"ADR;TYPE=HOME:;;;{city};;;{country}")
    L.append(f"URL:{SITE_URL}/")
    for k in ("github", "linkedin"):
        if (c := contact(cv, k)):
            L.append(f"X-SOCIALPROFILE;TYPE={k}:{c['url']}")
    L.append("END:VCARD")
    return "\r\n".join(L) + "\r\n"


def to_llms(cv: dict, updated: dt.date, has_docx: bool) -> str:
    cur = next((e for e in entries(cv, "experience") if e["current"]), None)
    loc = contact(cv, "location")
    facts = [f"{cv['name']} is a {cv['title']}" + (f" based in {loc['text']}" if loc else "") + "."]
    if cur:
        role = cur["roles"][0]["title"] if cur["roles"] else cur["title"]
        org = cur["title"] if cur["roles"] else cur["org"]
        facts.append(f"Current role: {role} at {org} (since {cur['dates'].split('–')[0].strip()}).")
    if (c := contact(cv, "email")):
        facts.append(f"Contact: {c['text']}.")
    sk = skills_flat(cv)
    if sk:
        facts.append("Key skills: " + ", ".join(sk[:18]) + ".")
    L = [f"# {cv['name']} — {cv['title']}", "", f"> {summary_text(cv)}", "", " ".join(facts), "",
         f"This is the official résumé of {cv['name']}, generated from the PDF on {updated.isoformat()}. "
         "The text versions below contain the complete résumé; prefer them over parsing the PDF.", "",
         "## Résumé", "",
         f"- [Résumé in Markdown]({SITE_URL}/resume.md): the full résumé, best for reading and quoting",
         f"- [Résumé in plain text]({SITE_URL}/resume.txt): ATS-friendly plain text",
         f"- [Résumé as JSON Resume]({SITE_URL}/resume.json): structured data (jsonresume.org schema)",
         f"- [Résumé PDF]({SITE_URL}/resume.pdf): the original, printable document"]
    if has_docx:
        L.append(f"- [Résumé in Word format]({SITE_URL}/resume.docx): editable .docx")
    L += [f"- [Web page]({SITE_URL}/): the résumé as an HTML page", "", "## Profiles", ""]
    for k, label in (("github", "GitHub"), ("linkedin", "LinkedIn")):
        if (c := contact(cv, k)):
            L.append(f"- [{label}]({c['url']})")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
# 4. Page assembly
# --------------------------------------------------------------------------

ICONS = {  # 24x24 stroke icons (Lucide-style), drawn inline so there are no extra requests
    "email": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    "location": '<path d="M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
    "github": '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>',
    "website": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "download": '<path d="M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
    "theme": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>',
    "file": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
}


def icon(name: str, cls: str = "i") -> str:
    return (f'<svg class="{cls}" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def human_size(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def render_index(cv, updated, pdf_size, pages, has_docx) -> str:
    tpl = (BUILD / "template.html").read_text()
    css = (BUILD / "style.css").read_text()
    js = (BUILD / "app.js").read_text()

    contacts = []
    for c in cv["contacts"]:
        label = {"github": "GitHub", "linkedin": "LinkedIn"}.get(c["kind"], c["text"])
        sr = {"email": "Email: ", "phone": "Phone: ", "location": "Location: "}.get(c["kind"], "")
        inner = f'{icon(c["kind"])}<span><span class="sr">{sr}</span>{esc(label)}</span>'
        if c["url"]:
            rel = ' rel="me noopener"' if c["kind"] in ("github", "linkedin", "website") else ""
            inner = f'<a href="{esc(c["url"])}"{rel}>{inner}</a>'
        contacts.append(f"<li>{inner}</li>")

    formats = [("resume.docx", "Word", ".docx", has_docx), ("resume.md", "Markdown", ".md", True),
               ("resume.txt", "Plain text", ".txt", True), ("resume.json", "JSON Resume", ".json", True),
               ("vishal-gupta.vcf", "Contact card", ".vcf", True)]
    fmt_html = "".join(f'<li><a href="{h}" download>{icon("file")}{esc(t)}<span class="ext">{e}</span></a></li>'
                       for h, t, e, ok in formats if ok)
    fmt_html = fmt_html.replace('href="vishal-gupta.vcf" download', f'href="{vcf_name(cv)}" download')

    nav = "".join(f'<li><a href="#{s["id"]}">{esc(short_title(s["title"]))}</a></li>' for s in cv["sections"])
    body = "".join(section_html(s) for s in cv["sections"])
    desc = summary_text(cv)
    meta_desc = desc if len(desc) <= 155 else desc[:152].rsplit(" ", 1)[0].rstrip(",.;") + "…"
    first, _, last = cv["name"].partition(" ")
    title = f"{cv['name']} — {cv['title']} | Résumé"
    pdf_name = re.sub(r"\s+", "_", cv["name"]) + "_Resume.pdf"

    values = {
        "TITLE": esc(title), "DESC": esc(meta_desc), "URL": SITE_URL + "/", "SITE": SITE_URL,
        "NAME": esc(cv["name"]), "FIRST": esc(first), "LAST": esc(last), "JOB": esc(cv["title"]),
        "INITIALS": esc("".join(w[0] for w in cv["name"].split()[:2]).upper()),
        "CONTACTS": "".join(contacts), "FORMATS": fmt_html, "NAV": nav, "BODY": body,
        "UPDATED_ISO": updated.isoformat(), "UPDATED": updated.strftime("%-d %b %Y"),
        "PDF_META": f"PDF · {pages} page{'s' if pages > 1 else ''} · {human_size(pdf_size)}",
        "PDF_NAME": pdf_name, "CSS": css, "JS": js,
        "JSONLD": json.dumps(to_jsonld(cv, updated), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"),
        "I_DOWNLOAD": icon("download"), "I_SHARE": icon("share"), "I_THEME": icon("theme"),
        "I_FILE": icon("file"),
    }
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], tpl)
    return out


def short_title(t: str) -> str:
    return {"Professional Experience": "Experience", "Work Experience": "Experience"}.get(t, t)


def vcf_name(cv) -> str:
    return re.sub(r"[^a-z0-9]+", "-", cv["name"].lower()).strip("-") + ".vcf"


def git_date(path: Path) -> dt.date | None:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return dt.datetime.fromisoformat(out).date() if out else None
    except Exception:
        return None


def pdf_date(meta: dict) -> dt.date | None:
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", meta.get("modDate") or meta.get("creationDate") or "")
    return dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None


def find_pdf() -> Path:
    pdfs = sorted((ROOT / "CV").glob("**/*.pdf"))
    if not pdfs:
        sys.exit("No PDF found in CV/")
    if len(pdfs) == 1:
        return pdfs[0]
    # several PDFs: use the one committed most recently
    return max(pdfs, key=lambda p: (git_date(p) or dt.date.min, p.stat().st_mtime))


def make_images(pdf: Path, out: Path, crop_y: float | None = None) -> None:
    doc = pymupdf.open(pdf)
    page = doc[0]
    w = page.rect.width
    # Open Graph card: the header and summary of the real CV, above a band with the URL
    bottom = min(crop_y or w * 0.5, page.rect.height)
    clip = pymupdf.Rect(18, 12, w - 18, bottom)
    card = pymupdf.open()
    cp = card.new_page(width=1200, height=630)
    cp.draw_rect(cp.rect, color=None, fill=(1, 1, 1))
    scale = 1200 / clip.width
    h = min(clip.height * scale, 470)
    top = max(16, (520 - h) / 2)
    cp.show_pdf_page(pymupdf.Rect(0, top, 1200, top + h), doc, 0, clip=clip, keep_proportion=True)
    band = pymupdf.Rect(0, 520, 1200, 630)
    cp.draw_rect(band, color=None, fill=(0x8F / 255, 0x1D / 255, 0x2C / 255))
    cp.insert_text((64, 586), DOMAIN, fontname="hebo", fontsize=34, color=(1, 1, 1))
    tag = "Résumé · PDF · Word · Markdown · JSON"
    tw = pymupdf.get_text_length(tag, fontname="helv", fontsize=26)
    cp.insert_text((1200 - 84 - tw, 584), tag, fontname="helv", fontsize=26, color=(1, 1, 1))
    cp.get_pixmap(alpha=False).save(out / "og.png")
    # Apple touch icon from the SVG favicon
    svg = pymupdf.open(stream=(out / "favicon.svg").read_bytes(), filetype="svg")
    svg[0].get_pixmap(matrix=pymupdf.Matrix(180 / svg[0].rect.width, 180 / svg[0].rect.width),
                      alpha=False).save(out / "apple-touch-icon.png")


# --------------------------------------------------------------------------
# 5. Verification: refuse to publish if any output lost text from the PDF
# --------------------------------------------------------------------------

SKIP_TOKENS = {"", "|", "•", "●", "▪", "-", "–", "—", "·"}


def tokens(text: str) -> list[str]:
    out = []
    for w in re.split(r"[\s|•●▪·/–—-]+", text.replace("\xa0", " ").lower()):
        w = w.strip(".,;:()[]{}\"'“”‘’!?*`\\")
        if w not in SKIP_TOKENS:
            out.append(w)
    return out


class _Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(src: str) -> str:
    p = _Text()
    p.feed(src)
    return "".join(p.parts)


def docx_text(path: Path) -> str:
    import zipfile
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf8")
    xml = re.sub(r"</w:p>|<w:tab/>|<w:br/>", " ", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def verify(pdf: Path, out: Path, cv: dict, require_docx: bool) -> list[str]:
    """Return a list of problems; an empty list means every check passed."""
    problems, notes = [], []
    doc = pymupdf.open(pdf)
    source = Counter(t for page in doc for w in page.get_text("words") for t in tokens(w[4]))

    outputs = {"index.html": html_text((out / "index.html").read_text()),
               "resume.md": re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1 \2", (out / "resume.md").read_text()),
               "resume.txt": (out / "resume.txt").read_text()}
    if (out / "resume.docx").exists():
        outputs["resume.docx"] = docx_text(out / "resume.docx")
    elif require_docx:
        problems.append("resume.docx was not generated")
    for name, text in outputs.items():
        missing = source - Counter(tokens(text))
        if missing:
            problems.append(f"{name} is missing text from the PDF: "
                            + ", ".join(f"{w!r}×{n}" if n > 1 else repr(w) for w, n in missing.most_common(15)))

    # JSON Resume and JSON-LD are restructured, so check they carry the content, not the layout
    content = []
    for sec in cv["sections"]:
        for it in sec["items"]:
            if it["type"] == "para":
                content.append(plain(it["rich"]))
            for e in [it] + it.get("roles", []):
                content += [plain(b["rich"]) for b in e.get("bullets", [])]
            for b in it.get("items", []):
                content += b.get("values") or [plain(b["rich"])]
    json_text = json.dumps(json.loads((out / "resume.json").read_text()), ensure_ascii=False)
    lost = set(tokens(" ".join(content))) - set(tokens(json_text))
    if lost:
        problems.append("resume.json is missing: " + ", ".join(sorted(lost)[:15]))
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', (out / "index.html").read_text(), re.S)
    try:
        ld = json.loads(m.group(1))
        if ld["mainEntity"]["name"] != cv["name"]:
            problems.append("JSON-LD name does not match the PDF")
    except Exception as e:  # noqa: BLE001
        problems.append(f"JSON-LD is not valid JSON: {e}")

    # Structure: the things a recruiter or an ATS must be able to find
    if not cv["name"] or len(cv["name"].split()) > 5:
        problems.append(f"name looks wrong: {cv['name']!r}")
    if not cv["title"]:
        problems.append("job title not found under the name")
    if not contact(cv, "email"):
        problems.append("no email address found in the header")
    exp = entries(cv, "experience")
    if not exp:
        problems.append("no Experience section with entries was found")
    for e in exp:
        for r in (e["roles"] or [e]):
            if not r["start"]:
                problems.append(f"experience entry without dates: {r['title']!r}")
            if not r["title"]:
                problems.append("experience entry without a title")
    for sec in cv["sections"]:
        for it in sec["items"]:
            if it["type"] == "list":
                for b in it["items"]:
                    if not plain(b["rich"]):
                        problems.append(f"empty bullet in {sec['title']}")
            if it["type"] == "para" and sec["id"] in ("experience", "education"):
                notes.append(f"unstructured line in {sec['title']}: {plain(it['rich'])[:70]!r}")
    for f in ("index.html", "resume.pdf", "resume.md", "resume.txt", "resume.json", "llms.txt",
              "robots.txt", "sitemap.xml", "og.png", "CNAME", vcf_name(cv)):
        if not (out / f).exists() or (out / f).stat().st_size == 0:
            problems.append(f"{f} is missing or empty")

    words = sum(source.values())
    report = [f"## CV build: {'❌ failed' if problems else '✅ passed'}", "",
              f"- Source: `{pdf.name}` ({doc.page_count} page{'s' if doc.page_count > 1 else ''}, {words} words)",
              f"- Name: **{cv['name']}** — {cv['title']}",
              "- Sections: " + ", ".join(f"{s['title']} ({len(s['items'])})" for s in cv["sections"]),
              f"- Experience: {sum(len(e['roles']) or 1 for e in exp)} roles at {len(exp)} organisations",
              f"- Checked every word of the PDF is present in: {', '.join(outputs)}", ""]
    report += [f"- ❌ {p}" for p in problems] + [f"- ⚠️ {n}" for n in notes]
    print("\n".join(report))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("\n".join(report) + "\n")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--dump", action="store_true", help="print the parsed model and exit")
    args = ap.parse_args()

    pdf = (args.pdf or find_pdf()).resolve()
    rows, body, info = read_rows(pdf)
    cv = build_model(rows, body)
    if args.dump:
        print(json.dumps(cv, default=lambda o: o.__dict__, indent=1, ensure_ascii=False))
        return
    if not cv["name"] or not cv["sections"]:
        sys.exit("Could not find a name and sections in the PDF; refusing to publish an empty page.")

    updated = git_date(pdf) or pdf_date(info["meta"]) or dt.date.today()
    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copy(pdf, out / "resume.pdf")
    shutil.copytree(BUILD / "static", out, dirs_exist_ok=True)
    (out / "fonts").mkdir(exist_ok=True)
    for f in (BUILD / "fonts").glob("*.woff2"):
        shutil.copy(f, out / "fonts" / f.name)

    md = to_markdown(cv, updated)
    (out / "resume.md").write_text(md)
    (out / "resume.txt").write_text(to_text(cv, updated))
    (out / "resume.json").write_text(json.dumps(to_json_resume(cv, updated), indent=2, ensure_ascii=False) + "\n")
    (out / vcf_name(cv)).write_text(to_vcard(cv))

    has_docx = False
    if shutil.which("pandoc"):
        r = subprocess.run(["pandoc", "-f", "markdown", "-t", "docx", "-o", str(out / "resume.docx"),
                            "--metadata", f"title={cv['name']} — Résumé"], input=md, text=True,
                           capture_output=True)
        has_docx = r.returncode == 0
        if not has_docx:
            print("pandoc failed, skipping resume.docx:", r.stderr, file=sys.stderr)
    else:
        print("pandoc not installed, skipping resume.docx", file=sys.stderr)

    pages = pymupdf.open(pdf).page_count
    (out / "index.html").write_text(render_index(cv, updated, pdf.stat().st_size, pages, has_docx))
    (out / "llms.txt").write_text(to_llms(cv, updated, has_docx))
    (out / "CNAME").write_text(DOMAIN + "\n")
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE_URL}/</loc><lastmod>{updated.isoformat()}</lastmod></url>\n"
        f"  <url><loc>{SITE_URL}/resume.pdf</loc><lastmod>{updated.isoformat()}</lastmod></url>\n"
        "</urlset>\n")
    robots = (BUILD / "robots.txt").read_text().replace("{{SITE}}", SITE_URL)
    (out / "robots.txt").write_text(robots)
    page404 = (out / "404.html").read_text().replace("{{NAME}}", esc(cv["name"]))
    (out / "404.html").write_text(page404)
    (out / ".nojekyll").write_text("")
    heads = [r.y for r in rows if r.page == 0 and is_heading(r, body)]
    make_images(pdf, out, heads[1] - 8 if len(heads) > 1 else None)
    if verify(pdf, out, cv, require_docx=bool(os.environ.get("CI"))):
        sys.exit("Verification failed: not publishing. The live site is unchanged.")
    print(f"Built {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} from {pdf.name} "
          f"({cv['name']}, {len(cv['sections'])} sections, updated {updated})")


if __name__ == "__main__":
    main()
