"""Check every published file against the PDF before anything is deployed.

Content checks are word-for-word. Every word on the PDF's pages (except page
numbers and running headers) must appear, as many times as in the PDF, in:
- the web page,
- the Markdown file as a Markdown reader renders it,
- the plain text,
- the Word file.

The JSON Resume must carry every summary line, bullet and skill. On top of that
there are structural checks for what a recruiter or an ATS has to find, and
validity checks for each file format.
"""

from __future__ import annotations

import html
import html.parser
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

from pdfparse import WORD_FLAGS, clean, plain
from render import contact, entries, vcf_name

STRIP = ".,;:()[]{}\"'“”‘’!?"


def tokens(text: str) -> list[str]:
    """Words for comparison: lower-cased, split on spaces and dashes, outer punctuation removed."""
    out = []
    for w in re.split(r"[\s/|•●▪·–—-]+", unicodedata.normalize("NFKC", clean(text)).lower()):
        w = w.strip(STRIP)
        if w:
            out.append(w)
    return out


class _HTMLText(html.parser.HTMLParser):
    """Visible text of a page, plus the facts the structural checks need."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden, self.ids, self.hrefs, self.h1, self.headings = [], 0, [], [], 0, []
        self._stack: list[str] = []
        self._heading = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "head", "template"):
            self.hidden += 1
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if tag == "h1":
            self.h1 += 1
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = [int(tag[1]), ""]
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "template"):
            self.hidden -= 1
        if re.fullmatch(r"h[1-6]", tag) and self._heading:
            self.headings.append(tuple(self._heading))
            self._heading = None
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self._heading:
                self._heading[1] += data


def page_facts(src: str) -> _HTMLText:
    p = _HTMLText()
    p.feed(src)
    p.close()
    return p


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf8")
    xml = re.sub(r"</w:p>|<w:tab/>|<w:br/>|<w:cr/>", " ", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def markdown_as_read(md: str, pandoc: str) -> str:
    """What a Markdown reader shows: render GFM to plain text with pandoc."""
    r = subprocess.run([pandoc, "-f", "gfm", "-t", "plain", "--wrap=none"], input=md, text=True,
                       capture_output=True, check=True)
    return r.stdout


def source_words(doc, furniture: list[str]) -> Counter:
    words = Counter(t for page in doc for w in page.get_text("words", flags=WORD_FLAGS) for t in tokens(w[4]))
    words.subtract(Counter(t for line in furniture for t in tokens(line)))
    return +words


def _missing(source: Counter, text: str) -> str:
    missing = source - Counter(tokens(text))
    return ", ".join(f"{w!r}×{n}" if n > 1 else repr(w) for w, n in missing.most_common(12))


def verify(doc, layout, cv: dict, out: Path, pandoc: str) -> tuple[list[str], list[str]]:
    """Return (problems, notes). Any problem blocks the deploy."""
    problems: list[str] = []
    notes = []
    source = source_words(doc, layout.furniture)
    if layout.furniture:
        notes.append("ignored page headers/footers: " + ", ".join(repr(f) for f in sorted(set(layout.furniture))))

    # 1. Every word of the PDF, in every human-readable format
    page = (out / "index.html").read_text()
    facts = page_facts(page)
    readable = {
        "index.html": "".join(facts.parts),
        "resume.md": markdown_as_read((out / "resume.md").read_text(), pandoc),
        "resume.txt": (out / "resume.txt").read_text(),
        "resume.docx": docx_text(out / "resume.docx"),
    }
    for name, text in readable.items():
        if (m := _missing(source, text)):
            problems.append(f"{name} is missing text from the PDF: {m}")

    # 2. Structured formats carry the content
    content = [cv["name"], cv["title"]]
    for sec in cv["sections"]:
        for it in sec["items"]:
            if it["type"] == "para":
                content.append(plain(it["rich"]))
            for e in [it] + it.get("roles", []):
                content += [e.get("title", ""), e.get("org", ""), *e.get("details", [])]
                content += [plain(b["rich"]) for b in e.get("bullets", []) + e.get("paras", [])]
            for b in it.get("items", []):
                content += b.get("values") or [plain(b["rich"])]
    try:
        resume = json.loads((out / "resume.json").read_text())
        lost = set(tokens(" ".join(content))) - set(tokens(json.dumps(resume, ensure_ascii=False)))
        if lost:
            problems.append("resume.json is missing: " + ", ".join(sorted(lost)[:12]))
        if resume.get("basics", {}).get("name") != cv["name"]:
            problems.append("resume.json basics.name does not match the PDF")
    except json.JSONDecodeError as e:
        problems.append(f"resume.json is not valid JSON: {e}")

    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    try:
        ld = json.loads(m.group(1)) if m else None
        person = ld["mainEntity"] if ld else {}
        if not ld or person.get("name") != cv["name"] or person.get("jobTitle") != cv["title"]:
            problems.append("JSON-LD is missing or does not match the PDF's name and title")
        if (c := contact(cv, "email")) and person.get("email") != "mailto:" + c["text"]:
            problems.append("JSON-LD email does not match the PDF")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        problems.append(f"JSON-LD is not valid: {e}")

    # 3. Structure a recruiter or an ATS depends on
    if not cv["name"] or len(cv["name"].split()) > 5 or len(cv["name"]) > 60:
        problems.append(f"the name looks wrong: {cv['name']!r}")
    if not cv["title"]:
        problems.append("no job title: add one under your name")
    elif cv["title_derived"]:
        notes.append(f"no job title under the name; using the current role: {cv['title']!r}")
    if not contact(cv, "email"):
        problems.append("no email address found in the header")
    exp = entries(cv, "experience")
    if not exp:
        problems.append("no Experience section with entries was found")
    for e in exp:
        for r in (e["roles"] or [e]):
            if not r["title"]:
                problems.append("an experience entry has no title")
            if not r["start"]:
                problems.append(f"experience entry without readable dates: {r['title']!r} ({r['dates'] or 'none'})")
    for sec in cv["sections"]:
        for it in sec["items"]:
            if it["type"] == "para" and re.sub(r"-\d+$", "", sec["id"]) in ("experience", "education"):
                notes.append(f"line kept as plain text in {sec['title']}: {plain(it['rich'])[:70]!r}")

    # 4. The page itself
    if facts.h1 != 1:
        problems.append(f"index.html has {facts.h1} <h1> elements (expected 1)")
    dupes = [i for i, n in Counter(facts.ids).items() if n > 1]
    if dupes:
        problems.append(f"index.html has duplicate ids: {dupes}")
    for href in facts.hrefs:
        if href.startswith("#") and len(href) > 1 and href[1:] not in facts.ids:
            problems.append(f"index.html links to a missing anchor: {href}")
        elif href.startswith("/") and not (out / (href.lstrip("/").split("#")[0] or "index.html")).is_file():
            problems.append(f"index.html links to a missing file: {href}")
        elif not re.match(r"^(https?:|mailto:|tel:|#|/)", href):
            problems.append(f"index.html has an unexpected link: {href}")
    fmt = re.search(r'<ul class="fmt">(.*?)</ul>', page, re.S)
    listed = re.findall(r'href="/([^"#]+)"', fmt.group(1)) if fmt else []
    wanted = ["resume.pdf", "resume.docx", "resume.md", "resume.txt", "resume.json", vcf_name(cv)]
    if listed != wanted:
        problems.append(f"the download list should offer {wanted} once each, in order; it has {listed}")
    levels = [lvl for lvl, _ in facts.headings]
    for a, b in zip(levels, levels[1:], strict=False):
        if b > a + 1:
            problems.append(f"index.html skips a heading level (h{a} → h{b})")
            break
    if any(not t.strip() for _, t in facts.headings):
        problems.append("index.html has an empty heading")

    # 5. Every published file is there and well-formed
    expected = ["index.html", "404.html", "resume.pdf", "resume.md", "resume.txt", "resume.json", "resume.docx",
                "llms.txt", "robots.txt", "sitemap.xml", "og.png", "apple-touch-icon.png", "favicon.svg",
                "site.webmanifest", "CNAME", vcf_name(cv), "fonts/crimson-pro-semibold.woff2",
                "fonts/crimson-pro-italic.woff2"]
    for f in expected:
        if not (out / f).is_file() or (out / f).stat().st_size == 0:
            problems.append(f"{f} is missing or empty")
    vcf = (out / vcf_name(cv)).read_bytes().decode("utf8") if (out / vcf_name(cv)).exists() else ""
    if not (vcf.startswith("BEGIN:VCARD\r\n") and vcf.endswith("END:VCARD\r\n")) or \
            any(len(line.encode()) > 75 for line in vcf.split("\r\n")):
        problems.append("the vCard is malformed")
    for f in ("site.webmanifest",):
        try:
            json.loads((out / f).read_text())
        except (OSError, json.JSONDecodeError):
            problems.append(f"{f} is not valid JSON")
    return problems, notes
