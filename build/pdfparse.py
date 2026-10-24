"""Read a résumé PDF into a structured model.

The parser works from the PDF's layout, not just its text. What it relies on:

- Header: the largest text above the first section is the name. The next line, if it
  isn't contact details, is the job title. Contact details are split on | · • or on
  wide gaps. mailto:, tel: and web links come from the PDF's link annotations, or
  from the text itself.
- Section headings: a line set apart from body text. That means ALL CAPS with
  larger size, bold or a rule underneath; or a known section name (Experience,
  Skills, Projects …) in bold, larger or underlined; or bold text at least 1.3×
  body size.
- Entries: a line starting with bold text, or a line with a right-aligned
  "dates | place" column. Bold text is the title and the rest is the
  organisation. An italic line right below adds detail (for example the
  institution under a degree).
- Roles at one organisation: an undated, bullet-less entry followed by indented
  dated entries.
- Bullets: •, ●, ▪, ◦, -, – … glyphs, or small drawn dots. Wrapped lines are joined,
  including across page breaks.
- "Label: values" lines (skills) become labelled lists.

Anything the parser can't place is kept as a plain paragraph, so no text is lost.
verify.py then checks every output against the PDF's words.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import pymupdf


class ParseError(Exception):
    """The PDF can't be turned into a résumé page; the message says why."""


TEXT_FLAGS = (pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES & ~pymupdf.TEXT_PRESERVE_IMAGES)
WORD_FLAGS = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_PRESERVE_LIGATURES

BULLET_GLYPHS = "•●▪◦‣○■□►▸➢➤✓✔∙"
BULLET_TEXT = re.compile(rf"^\s*([{BULLET_GLYPHS}]|[-–*])(\s+|$)")
SEPARATORS = re.compile(r"\s*(?:\||·|•|◆|⋅)\s*")
PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,3}(\s*(/|of)\s*\d{1,3})?$", re.I)
SECTION_WORDS = re.compile(
    r"^(profile|summary|professional summary|career summary|about( me)?|objective|career objective"
    r"|(professional |work |relevant )?experience|employment( history)?|work history|education"
    r"|(technical |core |key )?skills|skills( & | and )\w+|tech(nical)? stack|projects|(selected |key |personal )?projects"
    r"|certifications?|licen[cs]es( & certifications)?|awards|honou?rs|achievements|publications|languages"
    r"|interests|hobbies|volunteer(ing)?|volunteer experience|references|courses|training|open source|talks)$",
    re.I)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DATE = r"(?:[A-Za-z]{3,9}\.?,?\s+(?:19|20)\d{2}|\d{1,2}[/.](?:19|20)\d{2}|(?:19|20)\d{2}[-/]\d{1,2}|(?:19|20)\d{2})"
_NOW = r"(?:present|current|now|ongoing|till date|to date|today)"
DATE_RANGE = re.compile(rf"(?P<start>{_DATE})(?:\s*(?:–|—|-|to|until)\s*(?P<end>{_DATE}|{_NOW}))?", re.I)
DURATION = re.compile(r"^\(?\s*\d+\s*(?:yrs?|years?|mos?|months?)\b[\w\s]*\)?$", re.I)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

def clean(text: str) -> str:
    """Normalise text from the PDF without changing its meaning."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]", " ", text)  # odd spaces -> space
    text = re.sub(r"[\u200b-\u200d\u2060\ufeff]", "", text)  # zero-width characters
    return re.sub(r"[\ue000-\uf8ff]", "", text)  # private-use glyphs (icon fonts)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", clean(text)).strip()


@dataclass
class Seg:
    text: str
    bold: bool = False
    italic: bool = False
    url: str | None = None


def plain(rich: list[Seg]) -> str:
    return norm("".join(s.text for s in rich))


def merge_segs(segs: list[Seg]) -> list[Seg]:
    """Join neighbouring segments with the same style and collapse whitespace."""
    out: list[Seg] = []
    for s in segs:
        if out and (out[-1].bold, out[-1].italic, out[-1].url) == (s.bold, s.italic, s.url):
            out[-1].text += s.text
        else:
            out.append(Seg(s.text, s.bold, s.italic, s.url))
    for s in out:
        s.text = re.sub(r"\s+", " ", s.text)
    for i in range(1, len(out)):  # one space between segments, never two
        if out[i - 1].text.endswith(" ") and out[i].text.startswith(" "):
            out[i].text = out[i].text[1:]
    if out:
        out[0].text = out[0].text.lstrip()
        out[-1].text = out[-1].text.rstrip()
    return [s for s in out if s.text]


def append_line(rich: list[Seg], segs: list[Seg]) -> list[Seg]:
    """Join a wrapped line onto the text before it."""
    if not rich:
        return merge_segs(segs)
    last = rich[-1].text.rstrip()
    first = next((s.text.lstrip() for s in segs if s.text.strip()), "")
    if last.endswith("\u00ad"):  # soft hyphen: the word continues
        rich[-1].text = last[:-1]
    elif re.search(r"\w-$", last) and first[:1].isalpha():
        rich[-1].text = last  # "on-" + "site" -> "on-site" (the hyphen is real text)
    else:
        rich[-1].text = last + " "
    return merge_segs(rich + segs)


# --------------------------------------------------------------------------
# Layout: pieces of text -> rows
# --------------------------------------------------------------------------

@dataclass
class Piece:
    x0: float
    x1: float
    segs: list[Seg]


@dataclass
class Row:
    page: int
    y: float  # baseline
    size: float
    pieces: list[Piece]
    right: list[Seg] = field(default_factory=list)
    bullet: bool = False
    rule_below: bool = False

    @property
    def x0(self) -> float:
        return self.pieces[0].x0

    @property
    def x1(self) -> float:
        return self.pieces[-1].x1

    @property
    def segs(self) -> list[Seg]:
        out: list[Seg] = []
        for i, p in enumerate(self.pieces):
            if i and out and not out[-1].text.endswith(" ") and not p.segs[0].text.startswith(" "):
                out.append(Seg(" "))
            out.extend(p.segs)
        return out

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

    def gaps(self) -> list[float]:
        return [b.x0 - a.x1 for a, b in zip(self.pieces, self.pieces[1:], strict=False)]


@dataclass
class Layout:
    rows: list[Row]
    body: float  # most common font size
    width: float
    right_edge: float  # right edge of the text column (where lines wrap)
    furniture: list[str]  # page headers/footers that were dropped


def _is_bold(span) -> bool:
    f = span["font"].lower()
    return bool(span["flags"] & 16) or any(w in f for w in ("bold", "black", "heavy", "semibold", "demi"))


def _is_italic(span) -> bool:
    f = span["font"].lower()
    return bool(span["flags"] & 2) or "italic" in f or "oblique" in f


def _link_at(links, bbox) -> str | None:
    r = pymupdf.Rect(bbox)
    centre = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
    return next((uri for rect, uri in links if centre in rect), None)


def _link_runs(chars, links) -> list[tuple[str, str | None]]:
    """Split a span into runs of text by the link each character sits in."""
    runs: list[list] = []
    for c in chars:
        url = _link_at(links, c["bbox"])
        if runs and runs[-1][1] == url:
            runs[-1][0] += c["c"]
        else:
            runs.append([c["c"], url])
    return [(clean(t), u) for t, u in runs if clean(t)]


def _strip_bullet(runs):
    """Remove a typed bullet ("• ", "- ", "– ") from the start of a line."""
    text = "".join(t for t, _ in runs)
    m = BULLET_TEXT.match(text)
    if not m:
        return runs, False
    cut, out = m.end(), []
    for t, u in runs:
        if cut >= len(t):
            cut -= len(t)
            continue
        out.append((t[cut:], u))
        cut = 0
    return out, True


def read_layout(doc: pymupdf.Document) -> Layout:
    if doc.needs_pass:
        raise ParseError("the PDF is password-protected; export it without a password")
    if doc.page_count == 0:
        raise ParseError("the PDF has no pages")
    sizes: Counter = Counter()
    rows: list[Row] = []
    width = doc[0].rect.width
    all_chars = bad_chars = 0

    for pno, page in enumerate(doc):
        links = [(pymupdf.Rect(link["from"]), link["uri"]) for link in page.get_links() if link.get("uri")]
        drawings = page.get_drawings()
        dots = [d["rect"] for d in drawings
                if 1 <= d["rect"].width < 6 and 1 <= d["rect"].height < 6
                and abs(d["rect"].width - d["rect"].height) < 1.5 and d.get("fill") is not None]
        rules = [d["rect"] for d in drawings if d["rect"].height <= 2.5 and d["rect"].width >= width * 0.4]


        pieces = []  # (baseline, size, Piece, bullet)
        for block in page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]:
            for line in block.get("lines", []):
                if abs(line["dir"][1]) > 0.1:  # rotated text: not part of the reading flow
                    continue
                segs, bullet, size, x0, x1, base = [], False, 0.0, None, None, None
                for s in line["spans"]:
                    raw = "".join(c["c"] for c in s["chars"])
                    all_chars += len(raw)
                    bad_chars += sum(1 for ch in raw if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n"))
                    if s["size"] < 3:  # tiny glyphs: list markers and spacers
                        bullet = bullet or any(g in raw for g in BULLET_GLYPHS)
                        continue
                    runs = _link_runs(s["chars"], links)
                    if not segs and not bullet:
                        runs, bullet = _strip_bullet(runs)
                    bold, italic = _is_bold(s), _is_italic(s)
                    for text, url in runs:
                        if not segs and not text.strip():
                            continue  # leading spaces
                        segs.append(Seg(text, bold, italic, url))
                    visible = [c for c in s["chars"] if c["c"].strip()]
                    if visible and segs:
                        sizes[round(s["size"], 1)] += len(visible)
                        size = max(size, s["size"])
                        x0 = visible[0]["bbox"][0] if x0 is None else x0
                        x1 = visible[-1]["bbox"][2]
                        base = s["origin"][1] if base is None else base
                if segs and x0 is not None:
                    pieces.append((base, size, Piece(x0, x1, segs), bullet))
                elif bullet:
                    pieces.append((line["spans"][0]["origin"][1], 0.0, None, True))

        # Cluster pieces that share a baseline into rows, then order each row left to right.
        pieces.sort(key=lambda p: p[0])
        clusters: list[list] = []
        for p in pieces:
            if clusters and abs(p[0] - clusters[-1][0][0]) <= 0.4 * max(p[1], clusters[-1][0][1], 6):
                clusters[-1].append(p)
            else:
                clusters.append([p])
        page_rows = []
        text_right = max((p[2].x1 for p in pieces if p[2] is not None), default=width)
        for cl in clusters:
            bullet = any(p[3] for p in cl)
            real = sorted((p for p in cl if p[2] is not None), key=lambda p: p[2].x0)
            if not real:
                continue
            row = Row(pno, real[0][0], max(p[1] for p in real), [p[2] for p in real], bullet=bullet)
            wide = [i for i, g in enumerate(row.gaps(), 1) if g > 2 * max(row.size, 6)]
            if (len(wide) == 1 and row.pieces[wide[0]].x0 >= width * 0.5 and row.pieces[0].x0 < width * 0.5
                    and row.pieces[-1].x1 >= text_right - 2 * max(row.size, 6)):
                for i, pc in enumerate(row.pieces[wide[0]:]):  # "dates | place" aligned to the right
                    row.right.extend(([Seg(" ")] if i else []) + pc.segs)
                row.pieces = row.pieces[:wide[0]]
            for d in dots:  # a drawn dot just left of the text marks a bullet
                cy = (d.y0 + d.y1) / 2
                if row.x0 - 3 * row.size < d.x1 <= row.x0 + 1 and row.y - row.size <= cy <= row.y + 2:
                    row.bullet = True
            row.rule_below = any(row.y < r.y0 + r.height / 2 < row.y + 1.0 * row.size + 4 for r in rules)
            page_rows.append(row)
        rows.extend(page_rows)

    if not rows:
        raise ParseError("the PDF has no text (is it a scanned image?); export it from the original document")
    if all_chars and bad_chars / all_chars > 0.01:
        raise ParseError("the PDF's text can't be read reliably (fonts without a Unicode map); "
                         "export it again, for example with 'Save as PDF' instead of 'Print'")

    rows, furniture = _drop_furniture(doc, rows)
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    problems = _layout_problems(rows, body, width)
    if problems:
        raise ParseError("; ".join(problems))
    right_edge = max((r.x1 for r in rows), default=width)
    return Layout(rows, body, width, right_edge, furniture)


def _drop_furniture(doc, rows: list[Row]) -> tuple[list[Row], list[str]]:
    """Remove page numbers and running headers/footers."""
    def in_margin(r: Row) -> bool:
        h = doc[r.page].rect.height
        return r.y < h * 0.07 or r.y > h * 0.93

    seen: dict[str, set[int]] = {}
    for r in rows:
        if in_margin(r):
            seen.setdefault(r.text.lower(), set()).add(r.page)
    name_row = max((r for r in rows if r.page == 0), key=lambda r: r.size, default=None)
    keep, dropped = [], []
    for r in rows:
        repeated = in_margin(r) and len(seen.get(r.text.lower(), ())) >= 2
        page_no = in_margin(r) and PAGE_NUMBER.match(r.text)
        if (repeated or page_no) and r is not name_row:
            dropped.append(r.text)
        else:
            keep.append(r)
    return keep, dropped


def _layout_problems(rows: list[Row], body: float, width: float) -> list[str]:
    problems = []
    wide_gaps = [r for r in rows if any(g > 3 * max(r.size, body) for g in r.gaps())]
    heads = heading_rows(rows, body)
    first_heading = rows.index(heads[0]) if heads else len(rows)
    body_gaps = [r for r in wide_gaps if rows.index(r) > first_heading]
    if len(body_gaps) >= 3:
        problems.append("the PDF looks like a multi-column layout (text side by side on "
                        f"{len(body_gaps)} lines, e.g. {body_gaps[0].text[:60]!r}); "
                        "only single-column résumés are supported")
    odd_right = [r for r in rows if r.right_text and not DATE_RANGE.search(r.right_text) and len(r.right_text) > 45]
    if odd_right:
        problems.append("text in the right-hand column that isn't a date or place: "
                        f"{odd_right[0].right_text[:60]!r} (multi-column layout?)")
    return problems


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def is_heading(row: Row, body: float) -> bool:
    t = row.text.strip(" :")
    if row.bullet or row.right or not t or len(t) > 50 or len(t.split()) > 6:
        return False
    if len(re.sub(r"[^A-Za-z]", "", t)) < 3:
        return False
    caps = t.upper() == t
    bigger = row.size >= body * 1.1
    known = bool(SECTION_WORDS.match(t))
    if caps and (bigger or row.rule_below or (row.all_bold and known)):
        return True
    if known and (row.all_bold or bigger or row.rule_below or caps):
        return True
    return row.all_bold and row.size >= body * 1.3


def find_name_row(rows: list[Row]) -> Row:
    """The name: the largest text on the first page (the first such line if several tie)."""
    page0 = [r for r in rows if r.page == 0]
    if not page0:
        raise ParseError("the first page has no text")
    return max(page0, key=lambda r: (r.size, -r.y))


def heading_rows(rows: list[Row], body: float) -> list[Row]:
    """Section headings, in reading order. Nothing above or on the name line counts."""
    name = find_name_row(rows)
    start = rows.index(name) + 1
    return [r for r in rows[start:] if is_heading(r, body)]


def title_case(s: str) -> str:
    if s.upper() != s:
        return s  # keep the author's casing
    small = {"and", "of", "the", "in", "for", "to", "a", "an", "&", "on", "at"}
    words = s.lower().split()
    return " ".join(w if (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


SECTION_IDS = [("experience", "experience"), ("employment", "experience"), ("work history", "experience"),
               ("skill", "skills"), ("stack", "skills"), ("education", "education"), ("profile", "summary"),
               ("summary", "summary"), ("about", "summary"), ("objective", "summary"), ("project", "projects"),
               ("certif", "certifications"), ("licen", "certifications"), ("award", "awards"),
               ("honor", "awards"), ("honour", "awards"), ("achievement", "awards"), ("publication", "publications"),
               ("language", "languages"), ("interest", "interests"), ("hobbies", "interests"),
               ("volunteer", "volunteer"), ("reference", "references"), ("course", "courses"),
               ("training", "courses"), ("open source", "projects"), ("talks", "talks")]


def section_id(title: str) -> str:
    t = title.lower()
    for key, sid in SECTION_IDS:
        if key in t:
            return sid
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "section"


def parse_ym(text: str):
    """'Nov 2025' / '11/2025' / '2025-11' / '2025' -> (year, month or None)."""
    t = text.strip().rstrip(".,")
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+((?:19|20)\d{2})", t)
    if m and m.group(1)[:3].lower() in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1)[:3].lower()]
    m = re.fullmatch(r"(\d{1,2})[/.]((?:19|20)\d{2})", t)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"((?:19|20)\d{2})[-/](\d{1,2})", t)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"((?:19|20)\d{2})", t)
    if m:
        return int(m.group(1)), None
    return None


def fmt_ym(ym) -> str:
    return f"{MONTH_NAMES[ym[1] - 1]} {ym[0]}" if ym[1] else str(ym[0])


def parse_period(text: str) -> dict:
    """Split 'Nov 2025 – Present | Mumbai, India' into dates, duration and place."""
    out = {"dates": "", "start": None, "end": None, "current": False, "start_text": "", "end_text": "",
           "duration": "", "location": ""}
    places = []
    for part in [p for p in SEPARATORS.split(text) if p.strip()]:
        part = part.strip()
        m = DATE_RANGE.search(part) if not out["dates"] else None
        if m and parse_ym(m.group("start")):
            out["start_text"] = m.group("start")
            out["start"] = parse_ym(m.group("start"))
            end = m.group("end")
            if end:
                out["end_text"] = end
                if re.fullmatch(_NOW, end, re.I):
                    out["current"] = True
                else:
                    out["end"] = parse_ym(end)
            # the whole chunk is the date text ("Since Jan 2024"), minus a duration written after it
            tail = part[m.end():]
            d = re.match(r"^\s*\(?\s*(\d+\s*(?:yrs?|years?|mos?|months?)\b[\w\s]*?)\s*\)?\s*$", tail, re.I)
            if d:
                out["duration"] = d.group(1).strip()
                part = part[:m.end()]
            out["dates"] = part.strip()
        elif DURATION.match(part):
            out["duration"] = part.strip("()")
        else:
            places.append(part)
    out["location"] = " | ".join(places)
    return out


def classify_contact(text: str, url: str | None) -> dict:
    u = (url or "").lower()
    bare = text.strip()
    if u.startswith("mailto:") or re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", bare):
        return {"kind": "email", "text": bare, "url": url or f"mailto:{bare}"}
    if u.startswith("tel:") or re.fullmatch(r"\+?[\d][\d\s().-]{6,}\d", bare):
        return {"kind": "phone", "text": bare, "url": url or "tel:" + re.sub(r"[^\d+]", "", bare)}
    looks_url = re.fullmatch(r"(https?://)?(www\.)?[\w-]+(\.[\w-]+)+(/\S*)?", bare)
    href = url or (("" if bare.startswith("http") else "https://") + bare if looks_url else None)
    if href and "github.com" in href.lower():
        return {"kind": "github", "text": bare, "url": href}
    if href and "linkedin.com" in href.lower():
        return {"kind": "linkedin", "text": bare, "url": href}
    if href and href.startswith(("http://", "https://")):
        return {"kind": "website", "text": bare, "url": href}
    return {"kind": "text", "text": bare, "url": None}


def _contact_chunks(row: Row) -> list[tuple[str, str | None]]:
    """Split a contact row on separators and on wide gaps, keeping each part's link."""
    runs: list[list[Seg]] = []
    prev = None
    for p in row.pieces:
        if prev is None or p.x0 - prev.x1 > 1.2 * max(row.size, 6):
            runs.append([])
        elif runs[-1] and not runs[-1][-1].text.endswith(" ") and not p.segs[0].text.startswith(" "):
            runs[-1].append(Seg(" "))
        runs[-1].extend(p.segs)
        prev = p
    if row.right:
        runs.append(row.right)
    chunks: list[tuple[str, str | None]] = []
    for run in runs:
        text, url = "", None
        for s in run + [Seg("|")]:
            for i, part in enumerate(SEPARATORS.split(s.text)):
                if i > 0:
                    if norm(text):
                        chunks.append((norm(text), url))
                    text, url = "", None
                text += part
                if part.strip() and s.url:
                    url = url or s.url
    return chunks


def _is_contact_row(row: Row) -> bool:
    t = row.text
    wide_gap = any(g > 1.2 * max(row.size, 6) for g in row.gaps())
    return bool("@" in t or any(s.url for s in row.segs) or SEPARATORS.search(t) or wide_gap
                or re.search(r"\+?\d[\d\s().-]{7,}\d", t) or re.search(r"\b[\w-]+\.(com|in|io|dev|me|org|net)\b", t))


def build_model(layout: Layout) -> dict:
    rows, body = layout.rows, layout.body
    name_row = find_name_row(rows)
    headings = heading_rows(rows, body)
    if not headings:
        raise ParseError("no section headings found below the name (e.g. EXPERIENCE, SKILLS); "
                         "headings must be bold, larger, ALL CAPS or underlined")
    first_h = rows.index(headings[0])
    head = rows[:first_h]
    after = head[head.index(name_row) + 1:]
    before = head[:head.index(name_row)]
    title, tagline, contacts = "", [], []
    prev_contact: Row | None = None
    for r in before + after:
        if _is_contact_row(r):
            chunks = _contact_chunks(r)
            # a contact line that ran out of room continues on the next line
            if (prev_contact is not None and chunks and contacts and not r.pieces[0].segs[0].text.startswith("|")
                    and not SEPARATORS.search(prev_contact.text[-2:]) and _wraps(prev_contact, r, layout.right_edge)):
                last = contacts.pop()
                text, url = chunks.pop(0)
                chunks.insert(0, (norm(last["text"] + " " + text), last["url"] or url))
            for text, url in chunks:
                contacts.append(classify_contact(text, url))
            prev_contact = r
        elif not title and r in after:
            title = r.text
        else:
            tagline.append(r.text)
    # the first plain-text contact that reads like a place is the location
    for c in contacts:
        if c["kind"] == "text" and re.fullmatch(r"[A-Za-zÀ-ÿ .'-]+(,\s*[A-Za-zÀ-ÿ .'-]+)*", c["text"]):
            c["kind"] = "location"
            break

    sections: list[dict] = []
    heading_ids = {id(r) for r in headings}
    for r in rows[first_h:]:
        if id(r) in heading_ids:
            raw = r.text.strip(" :")
            sections.append({"title": title_case(raw), "id": section_id(raw), "rows": []})
        else:
            sections[-1]["rows"].append(r)
    used: Counter = Counter()
    for sec in sections:
        used[sec["id"]] += 1
        if used[sec["id"]] > 1:
            sec["id"] = f"{sec['id']}-{used[sec['id']]}"
        sec["items"] = parse_section(sec.pop("rows"), body, layout.right_edge, labelled=sec["id"] in ("skills", "languages", "interests"),
                                     prose=sec["id"] == "summary")

    cv = {"name": name_row.text, "title": title, "title_derived": False, "tagline": tagline,
          "contacts": contacts, "sections": sections}
    if not title:  # no title line under the name: use the current (or latest) role
        exp = [i for s in sections if s["id"] == "experience" for i in s["items"] if i["type"] == "entry"]
        if exp:
            e = exp[0]
            cv["title"] = (e["roles"][0]["title"] if e["roles"] else e["title"])
            cv["title_derived"] = True
    return cv


def _starts_bold(row: Row) -> bool:
    segs = [s for s in row.segs if s.text.strip()]
    return bool(segs) and segs[0].bold and len(segs[0].text.strip()) >= 2


def _mostly_bold(row: Row) -> bool:
    """An entry title line is mostly bold; a sentence that opens with a bold phrase is not."""
    text = row.text
    bold = norm("".join(s.text for s in row.segs if s.bold))
    return len(text) <= 70 or len(bold) >= 0.3 * len(text)


def _labelled(row_segs: list[Seg]) -> bool:
    """'**Frontend:** React, Next.js' — a bold label ending in a colon, then values."""
    segs = [s for s in row_segs if s.text.strip()]
    if len(segs) < 2 or not segs[0].bold:
        return False
    return segs[0].text.rstrip().endswith(":") or segs[1].text.lstrip().startswith(":")


def _wraps(prev: Row, r: Row, right_edge: float) -> bool:
    """Did the line above end because the next word didn't fit?"""
    words = r.text.split()
    if not words:
        return False
    need = pymupdf.get_text_length(words[0], fontname="helv", fontsize=r.size)
    return right_edge - prev.x1 < need + 0.6 * r.size


def _continues(block, prev: Row | None, r: Row, close: bool, right_edge: float) -> bool:
    """Is this row the next line of the open bullet, paragraph or labelled line?"""
    if not block or prev is None or not close or r.right or r.bullet or not _wraps(prev, r, right_edge):
        return False
    kind, _, x = block
    if kind == "label":  # wraps to the label's indent or to a hanging indent; a new label starts a new line
        return not _labelled(r.segs) and not _starts_bold(r)
    return abs(r.x0 - x) < 3  # bullets and paragraphs wrap to the left edge of their text


def parse_section(rows: list[Row], body: float, right_edge: float, labelled: bool = False,
                  prose: bool = False) -> list[dict]:
    items: list[dict] = []
    open_block = None  # (kind, target dict holding "rich", x of its text)
    last: Row | None = None

    def current_entry():
        if items and items[-1]["type"] == "entry":
            e = items[-1]
            return e["roles"][-1] if e["roles"] else e
        return None

    def bullet_list():
        e = current_entry()
        if e is not None:
            return e["bullets"]
        if items and items[-1]["type"] == "list":
            return items[-1]["items"]
        items.append({"type": "list", "items": []})
        return items[-1]["items"]

    for r in rows:
        if last is None:
            close = False
        elif r.page == last.page:
            close = 0 < r.y - last.y < 1.75 * max(r.size, last.size, body)
        else:  # first line on a new page may continue the last line of the previous one
            close = r.page == last.page + 1 and r is next(x for x in rows if x.page == r.page)
        segs = r.segs

        if r.bullet:
            item = {"rich": merge_segs(segs)}
            bullet_list().append(item)
            open_block = ("bullet", item, r.x0)
        elif _continues(open_block, last, r, close, right_edge):
            open_block[1]["rich"] = append_line(open_block[1]["rich"], segs)
        elif _labelled(segs) or (labelled and not r.right):
            item = {"rich": merge_segs(segs)}
            if items and items[-1]["type"] == "list" and current_entry() is None:
                items[-1]["items"].append(item)
            else:
                items.append({"type": "list", "items": [item]})
            open_block = ("label", item, r.x0)
        elif r.right or (_starts_bold(r) and not prose and _mostly_bold(r)):
            entry = _entry(r)
            group = items[-1] if items and items[-1]["type"] == "entry" else None
            if group and _joins_group(group, entry):
                group["roles"].append(entry)
            else:
                items.append(entry)
            open_block = None
        elif current_entry() is not None and close and r.all_italic and not current_entry()["bullets"] \
                and not current_entry()["paras"]:
            e = current_entry()
            if e["org"]:
                e["details"].append(r.text)
            else:
                e["org"] = r.text
                e["org_url"] = e["org_url"] or next((s.url for s in segs if s.url), None)
        elif current_entry() is not None and close:
            item = {"rich": merge_segs(segs)}
            current_entry()["paras"].append(item)
            open_block = ("para", item, r.x0)
        else:
            item = {"type": "para", "rich": merge_segs(segs)}
            items.append(item)
            open_block = ("para", item, r.x0)
        last = r

    for it in items:
        if it["type"] == "list":
            for b in it["items"]:
                _split_label(b, labelled)
        if it["type"] == "entry" and it["roles"]:
            _group_period(it)
        if it["type"] == "entry":
            del it["x0"]
            for r in it["roles"]:
                del r["x0"]
    return items


def _joins_group(group: dict, entry: dict) -> bool:
    """Is this entry a role inside the organisation entry before it?

    The organisation line has no dates, bullets or text of its own. Its roles are
    either indented below it, or at the same indent with dates and no organisation
    of their own (a line with its own organisation starts a new entry).
    """
    if group["bullets"] or group["paras"] or group["details"]:
        return False
    if not group["roles"] and group["dates"]:
        return False
    if entry["x0"] > group["x0"] + 4:
        return True
    return abs(entry["x0"] - group["x0"]) < 4 and bool(entry["dates"]) and not entry["org"]


def _entry(r: Row) -> dict:
    head = merge_segs(r.segs)
    bold = norm("".join(s.text for s in head if s.bold))
    rest = norm("".join(s.text for s in head if not s.bold))
    period_text = r.right_text
    if not period_text:  # dates written inline: "Tech Lead, Acme — Jan 2020 – Mar 2022"
        m = DATE_RANGE.search(rest)
        if m and parse_ym(m.group("start")):
            period_text = rest[m.start():]
            rest = rest[:m.start()]
    title = bold.strip(" ,;:—–|-") or rest.strip(" ,;:—–|-")
    org = rest.strip(" ,;:—–|-") if bold else ""
    joiner = ""
    if (m := re.match(r"^(at|@)\s+(.+)$", org, re.I)):  # "Backend Developer at Fintechly"
        joiner, org = m.group(1), m.group(2)
    org_url = next((s.url for s in head if s.url and not s.bold), None) or next((s.url for s in head if s.url), None)
    return {"type": "entry", "title": title, "org": org, "joiner": joiner, "org_url": org_url, "details": [],
            "x0": r.x0, "bullets": [], "paras": [], "roles": [], **parse_period(period_text)}


def _group_period(it: dict) -> None:
    roles = it["roles"]
    starts = [r["start"] for r in roles if r["start"]]
    ends = [r["end"] for r in roles if r["end"]]

    def key(ym):
        return ym[0], ym[1] or 0

    it["current"] = it["current"] or any(r["current"] for r in roles)
    it["start"] = it["start"] or (min(starts, key=key) if starts else None)
    it["end"] = None if it["current"] else (it["end"] or (max(ends, key=key) if ends else None))
    locs = {r["location"] for r in roles if r["location"]}
    it["location"] = it["location"] or (locs.pop() if len(locs) == 1 else "")
    if not it["dates"] and it["start"]:
        it["start_text"] = fmt_ym(it["start"])
        it["end_text"] = "Present" if it["current"] else (fmt_ym(it["end"]) if it["end"] else "")
        it["dates"] = it["start_text"] + (f" – {it['end_text']}" if it["end_text"] else "")


def _split_values(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch in "([{"
        depth -= ch in ")]}"
        if depth == 0 and ch in ",;|·•":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [v.strip() for v in out if v.strip()]


def _split_label(item: dict, labelled_section: bool) -> None:
    rich = [s for s in item["rich"] if s.text.strip()]
    if rich and rich[0].bold and (":" in rich[0].text or (labelled_section and len(rich) > 1)):
        label, _, tail = rich[0].text.partition(":")
        rest = plain([Seg(tail)] + rich[1:]).lstrip(": ")
        if rest:
            item["label"] = norm(label)
            item["values"] = _split_values(rest)
            return
    if labelled_section:
        item["values"] = _split_values(plain(item["rich"]))


def parse_pdf(path) -> tuple[dict, Layout, pymupdf.Document]:
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001 - any open failure means the file isn't a usable PDF
        raise ParseError(f"can't open the PDF: {e}") from e
    if not doc.is_pdf:
        raise ParseError("the file is not a PDF")
    layout = read_layout(doc)
    return build_model(layout), layout, doc
