# Vishal Gupta — CV

**Live: [cv.vishalg.in](https://cv.vishalg.in)**

The PDF in [`CV/`](CV/) is the single source of truth. Every push that changes it
rebuilds and redeploys the site through GitHub Actions; the URLs never change.

## Updating the CV

1. Replace `CV/Vishal_Gupta_CV_2026.pdf` with the new PDF (any filename in `CV/` works;
   if there are several, the most recently committed one is used).
2. Commit and push to `main`. The site is live again in about a minute.

That's it. You can also re-run the build by hand from the **Actions** tab → *Build and deploy CV* → *Run workflow*.

## Safety check

Every build verifies its own output before deploying:

- every word in the PDF must appear in `index.html`, `resume.md`, `resume.txt` and `resume.docx`;
- `resume.json` must contain every bullet, summary line and skill;
- the name, job title, email and Experience section must be found, and every role must have dates;
- every published file must exist and be non-empty, and the JSON-LD must be valid.

If any check fails, the workflow fails, **nothing is deployed and the live site stays as it was**.
GitHub emails you about the failed run; the run's summary page lists exactly what was wrong.
A passing run's summary shows what was parsed (sections, roles, word count).

## What gets published

| URL | What it is |
|---|---|
| [`/`](https://cv.vishalg.in/) | Résumé page: semantic HTML, JSON-LD (`ProfilePage` + `Person`), Open Graph, light/dark, print-friendly |
| [`/resume.pdf`](https://cv.vishalg.in/resume.pdf) | The PDF, at a stable link |
| [`/resume.docx`](https://cv.vishalg.in/resume.docx) | Word version (generated with pandoc) |
| [`/resume.md`](https://cv.vishalg.in/resume.md) | Markdown, for AI assistants |
| [`/resume.txt`](https://cv.vishalg.in/resume.txt) | Plain text, for applicant tracking systems |
| [`/resume.json`](https://cv.vishalg.in/resume.json) | [JSON Resume](https://jsonresume.org/schema) |
| [`/vishal-gupta.vcf`](https://cv.vishalg.in/vishal-gupta.vcf) | Contact card |
| [`/llms.txt`](https://cv.vishalg.in/llms.txt) | Summary and links for AI crawlers |
| `/robots.txt`, `/sitemap.xml`, `/og.png` | Crawling and link previews |

## How it works

`build/build.py` reads the PDF with [PyMuPDF](https://pymupdf.readthedocs.io/), keeping
bold/italic text, links and layout, and detects the structure from the PDF itself:
section headings (bold capitals), entries (bold title + right-aligned `dates | place`),
roles grouped under one company (indented), bullets and `Label: values` skill lines.
Keep that layout in future versions of the PDF and the page will follow.

Build locally:

```bash
python3 -m venv .venv && .venv/bin/pip install -r build/requirements.txt
.venv/bin/python build/build.py          # writes _site/
.venv/bin/python build/build.py --dump   # prints the parsed data, to check a new PDF
python3 -m http.server -d _site 8000
```

Files: `build/template.html` (page), `build/style.css`, `build/app.js` (theme, share,
PDF preview), `build/static/` (copied as-is), `build/fonts/` (Crimson Pro, subset, OFL).
