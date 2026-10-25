#!/usr/bin/env python3
"""Build the CV website from the PDF résumé in CV/.

The PDF is the single source of truth. pdfparse.py reads it into a model,
render.py writes every format, and verify.py checks each one against the
PDF. If any check fails, nothing is written to the output directory, so the
deploy step has nothing to publish and the live site stays as it was.

Usage: python build/build.py [--pdf CV/file.pdf] [--out _site] [--dump]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling modules, however the script is run

import render  # noqa: E402
from pdfparse import ParseError, heading_rows, parse_pdf  # noqa: E402
from verify import verify  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DEFAULT_SITE = "https://cv.vishalg.in"


def find_pdf(folder: Path) -> Path:
    pdfs = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise ParseError(f"no PDF found in {folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder}/")
    if len(pdfs) == 1:
        return pdfs[0]
    # several PDFs: the one committed most recently, then the newest file
    return max(pdfs, key=lambda p: (git_time(p) or 0, p.stat().st_mtime))


def git_time(path: Path) -> int | None:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=20, check=False).stdout.strip()
        return int(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def updated_date(path: Path, doc) -> dt.date:
    """The PDF's commit date, else its own modification date, else today."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=20, check=False).stdout.strip()
        if out:
            return dt.datetime.fromisoformat(out).date()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", (doc.metadata or {}).get("modDate") or "")
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return dt.date.today()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20,
                              check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def github_repo(remote: str) -> str | None:
    """owner/repo from a GitHub remote URL (https or ssh)."""
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", remote)
    return m[1] if m else None


def upload_url() -> str | None:
    """GitHub's upload page for CV/, so /upload can send you there. In Actions it comes from the
    workflow's environment, locally from the git remote; None outside a GitHub checkout."""
    repo = os.environ.get("GITHUB_REPOSITORY") or github_repo(git("remote", "get-url", "origin"))
    branch = os.environ.get("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD")
    if not repo or not branch or branch == "HEAD":
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server}/{repo}/upload/{quote(branch, safe='/')}/CV"


def find_pandoc() -> str:
    exe = os.environ.get("PANDOC") or shutil.which("pandoc")
    if not exe:
        raise ParseError("pandoc is not installed (needed for resume.docx and to check resume.md)")
    return exe


def make_images(doc, layout, out: Path, site_domain: str, formats_label: str) -> None:
    """og.png: the CV's header and summary above a band with the URL. apple-touch-icon.png from the favicon."""
    page = doc[0]
    w = page.rect.width
    section_tops = [r.y - r.size for r in heading_rows(layout.rows, layout.body) if r.page == 0]
    bottom = section_tops[1] - 4 if len(section_tops) > 1 else w * 0.5
    clip = pymupdf.Rect(18, 8, w - 18, min(bottom, page.rect.height))
    card = pymupdf.open()
    cp = card.new_page(width=1200, height=630)
    cp.draw_rect(cp.rect, color=None, fill=(1, 1, 1))
    h = min(clip.height * 1200 / clip.width, 480)
    top = max(16, (520 - h) / 2)
    cp.show_pdf_page(pymupdf.Rect(0, top, 1200, top + h), doc, 0, clip=clip, keep_proportion=True)
    cp.draw_rect(pymupdf.Rect(0, 520, 1200, 630), color=None, fill=(0x8F / 255, 0x1D / 255, 0x2C / 255))
    cp.insert_text((64, 586), site_domain, fontname="hebo", fontsize=34, color=(1, 1, 1))
    tw = pymupdf.get_text_length(formats_label, fontname="helv", fontsize=26)
    cp.insert_text((1200 - 64 - tw, 584), formats_label, fontname="helv", fontsize=26, color=(1, 1, 1))
    cp.get_pixmap(alpha=False).save(out / "og.png")
    svg = pymupdf.open(stream=(out / "favicon.svg").read_bytes(), filetype="svg")
    z = 180 / svg[0].rect.width
    svg[0].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).save(out / "apple-touch-icon.png")


FORMATS = [  # (file, label, description for llms.txt)
    ("resume.pdf", "PDF", "the original, printable document"),
    ("resume.docx", "Word", "editable Word document"),
    ("resume.md", "Markdown", "the full résumé, best for reading and quoting"),
    ("resume.txt", "Plain text", "plain text for applicant tracking systems"),
    ("resume.json", "JSON Resume", "structured data (jsonresume.org schema)"),
]


def build(pdf: Path, out: Path, site_url: str, pandoc: str) -> tuple[dict, list[str], list[str], dict]:
    cv, layout, doc = parse_pdf(pdf)
    updated = updated_date(pdf, doc)
    domain = re.sub(r"^https?://", "", site_url)
    out.mkdir(parents=True)

    shutil.copyfile(pdf, out / "resume.pdf")
    (out / "fonts").mkdir()
    for f in sorted((BUILD / "fonts").glob("*.woff2")):
        shutil.copyfile(f, out / "fonts" / f.name)
    shutil.copyfile(BUILD / "fonts" / "OFL.txt", out / "fonts" / "OFL.txt")

    md = render.to_markdown(cv, site_url, updated)
    (out / "resume.md").write_text(md)
    (out / "resume.txt").write_text(render.to_text(cv, site_url, updated))
    (out / "resume.json").write_text(json.dumps(render.to_json_resume(cv, site_url, updated), indent=2,
                                                ensure_ascii=False) + "\n")
    (out / render.vcf_name(cv)).write_bytes(render.to_vcard(cv, site_url).encode("utf8"))
    subprocess.run([pandoc, "-f", "gfm", "-t", "docx", "-o", str(out / "resume.docx")], input=md, text=True, capture_output=True, check=True)

    formats = FORMATS + [(render.vcf_name(cv), "Contact card", "contact details as a vCard")]
    (out / "index.html").write_text(render.render_index(cv, site_url, updated, pdf_size=pdf.stat().st_size,
                                                        pages=doc.page_count, formats=formats))
    (out / "404.html").write_text(render.render_404(cv))
    upload = upload_url()
    if upload:
        (out / "upload").mkdir()
        (out / "upload" / "index.html").write_text(render.render_upload(cv, upload))
    (out / "llms.txt").write_text(render.to_llms(cv, site_url, updated, formats[:5]))
    (out / "sitemap.xml").write_text(render.sitemap(site_url, updated))
    (out / "robots.txt").write_text((BUILD / "robots.txt").read_text().replace("{{SITE}}", site_url))
    (out / "favicon.svg").write_text(render.favicon_svg(cv))
    (out / "site.webmanifest").write_text(render.manifest(cv))
    (out / "CNAME").write_text(domain + "\n")
    make_images(doc, layout, out, domain, "Résumé · PDF · Word · Markdown · JSON")

    problems, notes = verify(doc, layout, cv, out, pandoc)
    if not upload:
        notes.append("not in a GitHub checkout, so /upload (the link to GitHub's upload page) was not made")
    stats = {"pdf": pdf.name, "pages": doc.page_count, "updated": updated.isoformat()}
    return cv, problems, notes, stats


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def report(cv, problems, notes, stats) -> str:
    exp = render.entries(cv, "experience") if cv else []
    lines = [f"## CV build: {'❌ failed, the live site was not changed' if problems else '✅ passed'}", ""]
    if cv:
        lines += [f"- Source: `{stats['pdf']}` ({stats['pages']} page{'s' if stats['pages'] > 1 else ''}), "
                  f"updated {stats['updated']}",
                  f"- Name: **{cv['name']}** — {cv['title']}",
                  "- Sections: " + ", ".join(f"{s['title']}" for s in cv["sections"]),
                  f"- Experience: {plural(sum(len(e['roles']) or 1 for e in exp), 'role')} at "
                  f"{plural(len(exp), 'organisation')}",
                  "- Every word of the PDF checked in the web page, Markdown, plain text and Word files"]
    lines += [""] + [f"- ❌ {p}" for p in problems] + [f"- ⚠️ {n}" for n in notes]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pdf", type=Path, help="PDF to build from (default: the PDF in CV/)")
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL", DEFAULT_SITE))
    ap.add_argument("--dump", action="store_true", help="print the parsed résumé as JSON and exit")
    args = ap.parse_args()
    site_url = args.site_url.rstrip("/")

    cv, problems, notes, stats = None, [], [], {}
    try:
        pdf = (args.pdf or find_pdf(ROOT / "CV")).resolve()
        if args.dump:
            cv, _, _ = parse_pdf(pdf)
            print(json.dumps(cv, default=lambda o: o.__dict__, indent=1, ensure_ascii=False))
            return 0
        pandoc = find_pandoc()
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "site"
            cv, problems, notes, stats = build(pdf, staging, site_url, pandoc)
            if not problems:
                if args.out.exists():
                    shutil.rmtree(args.out)
                shutil.copytree(staging, args.out)
    except ParseError as e:
        problems = [str(e)]
    except subprocess.CalledProcessError as e:
        problems = [f"{Path(e.cmd[0]).name} failed: {(e.stderr or '').strip()[:300]}"]

    text = report(cv, problems, notes, stats)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf8") as fh:
            fh.write(text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
