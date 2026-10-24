# Vishal Gupta — CV

**Live: [cv.vishalg.in](https://cv.vishalg.in)**

The PDF in [`CV/`](CV/) is the single source of truth. Every push that changes it
rebuilds the site, checks every output against the PDF, and deploys it. The URLs never change.

## Updating the CV

1. Replace `CV/Vishal_Gupta_CV_2026.pdf` with the new PDF. Any file name works. If the folder holds
   several PDFs, the most recently committed one is used, so it's cleaner to replace the old one.
   From the browser: open the `CV` folder → **Add file → Upload files** → commit.
2. Wait about a minute. A green tick under **Actions** means the new version is live.

If the run fails, nothing was deployed and the old version is still live. GitHub emails you, and
the run's summary page says exactly what was wrong.

## What gets published

| URL | What it is |
|---|---|
| [`/`](https://cv.vishalg.in/) | Résumé page: semantic HTML, JSON-LD (`ProfilePage` + `Person`), Open Graph, light/dark, print layout |
| [`/resume.pdf`](https://cv.vishalg.in/resume.pdf) | The PDF, at a stable link |
| [`/resume.docx`](https://cv.vishalg.in/resume.docx) | Word version |
| [`/resume.md`](https://cv.vishalg.in/resume.md) | Markdown, for AI assistants |
| [`/resume.txt`](https://cv.vishalg.in/resume.txt) | Plain text, for applicant tracking systems |
| [`/resume.json`](https://cv.vishalg.in/resume.json) | [JSON Resume](https://jsonresume.org/schema) (validated against schema v1.0.0) |
| [`/vishal-gupta.vcf`](https://cv.vishalg.in/vishal-gupta.vcf) | Contact card |
| [`/llms.txt`](https://cv.vishalg.in/llms.txt) | Summary and links for AI crawlers |
| `/robots.txt`, `/sitemap.xml`, `/og.png` | Crawling and link previews |

## What the build checks before deploying

- **Every word of the PDF** (except page numbers and running headers) appears, as many times as in
  the PDF, in the web page, in the Markdown *as a Markdown reader renders it*, in the plain text and
  in the Word file.
- **The JSON Resume** carries every summary line, bullet and skill. **The JSON-LD** matches the
  name, title and email.
- **What recruiters and ATS need is there:** the name, a job title, an email address, an Experience
  section, and readable dates on every role.
- **The page is sound:** exactly one `<h1>`, no skipped heading levels, unique ids, no broken
  internal links, and every published file present and non-empty.
- **The formats are valid:** the vCard follows RFC 2426 (CRLF line endings, folded lines) and the
  manifest is valid JSON.

The builder also has its own test suite (`tests/`), which runs before every build. It covers your
real CV field by field, a two-page edge-case CV, a minimal CV, and PDFs that must be refused. It
also checks that deliberately broken output is caught by the checks above.

## PDF layouts that work

The parser reads the PDF's layout, not just its text (see `build/pdfparse.py`). It handles:

- **Name and header:** the name is the largest text on page 1. The job title is the line under it
  (if there isn't one, the current role is used). Contact details can be split by `|` `·` `•` or by
  wide gaps, written as text or as links.
- **Section headings:** ALL CAPS, bold, larger or underlined text, or a known section name
  (Experience, Skills, Projects, Certifications, Languages …).
- **Entries:** a bold title with the organisation after it ("Role, Company", "Role at Company") or
  in italics on the next line. Dates go in a right-aligned column or inline. Accepted date forms:
  `Nov 2025`, `November 2025`, `11/2025`, `2025-11` or `2025`, then `– Present` / `Current` / an
  end date. A written duration like `(8 mos)` is kept.
- **Several roles at one company:** the company on its own line, with the roles indented under it
  or listed under it without a company of their own.
- **Bullets and wrapping:** `•`, `-`, `–` or drawn bullets. Wrapped lines are joined, including
  across a page break. Bold, italic and links are kept, down to the exact linked words.
- **Skills:** `Label: value, value` lines, wrapped or with a hanging indent.
- **Two-page PDFs:** page numbers and repeated headers/footers are removed.

**Refused, with a clear message** (the live site stays unchanged): two-column/sidebar layouts,
scanned (image-only) PDFs, password-protected PDFs, PDFs whose fonts have no text mapping, and
PDFs with no recognisable section headings.

## Working on the builder

```bash
python3 -m venv .venv
.venv/bin/pip install -r build/requirements.txt -r tests/requirements.txt
# pandoc 3.12.1 is needed: https://github.com/jgm/pandoc/releases/tag/3.12.1
.venv/bin/python build/build.py                         # build and verify into _site/
.venv/bin/python build/build.py --pdf other.pdf --dump  # print what the parser read
.venv/bin/python -m unittest discover -s tests -v       # tests
.venv/bin/ruff check build tests                        # lint
python3 -m http.server -d _site 8000                    # preview
```

| File | Role |
|---|---|
| `build/pdfparse.py` | PDF → résumé model (layout, headings, entries, bullets, contacts) |
| `build/render.py` | Model → HTML, Markdown, text, JSON Resume, JSON-LD, vCard, llms.txt |
| `build/verify.py` | Checks every output against the PDF |
| `build/build.py` | Command line: build into a staging folder, verify, then publish |
| `build/template.html`, `style.css`, `app.js` | The page (no framework; inline CSS and JS) |
| `tests/` | Test suite; `make_fixtures.py` regenerates the fixture PDFs from `fixtures/src/` with Chrome |

Tool versions are pinned (PyMuPDF, pandoc, jsonschema, ruff, the GitHub Actions by commit, the
runner image) so a build can't change behaviour without a commit here. Fonts: Crimson Pro (SIL Open
Font License, `build/fonts/OFL.txt`).
