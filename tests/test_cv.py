"""Tests for the CV builder: parsing, every output format, the verifier and failure modes.

Run: python -m unittest discover -s tests -v   (needs pandoc and tests/requirements.txt)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import jsonschema
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "build"))

import build  # noqa: E402
import render  # noqa: E402
import verify  # noqa: E402
from pdfparse import (ParseError, Seg, classify_contact, parse_pdf, parse_period, parse_ym,  # noqa: E402
                      plain)

PANDOC = os.environ.get("PANDOC") or shutil.which("pandoc")
SITE = "https://cv.example.test"
SCHEMA = json.loads((FIX / "jsonresume-schema-1.0.0.json").read_text())


def gfm_plain(md: str) -> str:
    return subprocess.run([PANDOC, "-f", "gfm", "-t", "plain", "--wrap=none"], input=md, text=True,
                          capture_output=True, check=True).stdout.strip()


class Built:
    """Build a fixture once into a temp dir and keep the results."""

    _cache: dict = {}

    @classmethod
    def get(cls, name: str):
        if name not in cls._cache:
            tmp = Path(tempfile.mkdtemp(prefix=f"cv-{name}-"))
            out = tmp / "site"
            cv, problems, notes, stats = build.build(FIX / f"{name}.pdf", out, SITE, PANDOC)
            cls._cache[name] = (cv, problems, notes, out)
        return cls._cache[name]


def section(cv, sid):
    return next(s for s in cv["sections"] if s["id"] == sid)


def bold_parts(rich):
    return [s.text for s in rich if s.bold]


# --------------------------------------------------------------------------
# The real CV: exact structure
# --------------------------------------------------------------------------

class RealCV(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cv, cls.problems, cls.notes, cls.out = Built.get("real")

    def test_passes_verification_cleanly(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.notes, [])

    def test_header(self):
        self.assertEqual(self.cv["name"], "Vishal Gupta")
        self.assertEqual(self.cv["title"], "Senior Frontend Engineer")
        self.assertFalse(self.cv["title_derived"])
        self.assertEqual([(c["kind"], c["text"], c["url"]) for c in self.cv["contacts"]], [
            ("email", "vishalgupta26x@gmail.com", "mailto:vishalgupta26x@gmail.com"),
            ("phone", "+91-7000971726", "tel:+91-7000971726"),
            ("location", "Ambikapur, India", None),
            ("github", "GitHub", "https://github.com/vishalguptax"),
            ("linkedin", "LinkedIn", "https://linkedin.com/in/vishalgupta26"),
        ])

    def test_sections(self):
        self.assertEqual([(s["title"], s["id"]) for s in self.cv["sections"]], [
            ("Profile", "summary"), ("Professional Experience", "experience"), ("Education", "education"),
            ("Skills", "skills")])
        summary = section(self.cv, "summary")["items"]
        self.assertEqual(len(summary), 1)
        self.assertTrue(plain(summary[0]["rich"]).startswith("Senior frontend engineer with 4+ years"))
        self.assertTrue(plain(summary[0]["rich"]).endswith("mentored 10–15 interns and junior engineers."))

    def test_experience(self):
        items = section(self.cv, "experience")["items"]
        self.assertEqual([i["type"] for i in items], ["entry"] * 4)
        bv, vd, bc, gg = items
        self.assertEqual((bv["title"], bv["org"], bv["org_url"]),
                         ("Senior Frontend Engineer", "Binaryveda", "https://www.binaryveda.com/"))
        self.assertEqual((bv["dates"], bv["start"], bv["end"], bv["current"], bv["location"]),
                         ("Nov 2025 – Present", (2025, 11), None, True, "Mumbai, India"))
        self.assertEqual(len(bv["bullets"]), 5)
        self.assertEqual(bold_parts(bv["bullets"][0]["rich"]), ["Svelte 5", "TypeScript micro-frontend",
                                                               "Module Federation"])
        self.assertIn("enabling remote, on-site, and provisioning control", plain(bv["bullets"][2]["rich"]))
        self.assertEqual(bold_parts(bv["bullets"][3]["rich"]), ["Awilix", "Exome"])  # wrapped line starts bold
        self.assertIn("role-based access control", plain(bv["bullets"][4]["rich"]))

        self.assertEqual((vd["title"], vd["org_url"], vd["dates"], vd["location"]),
                         ("vDoIT Technologies Ltd.", "https://vdoitech.com/", "Aug 2022 – Oct 2025", "Gurgaon, India"))
        self.assertEqual([(r["title"], r["dates"], len(r["bullets"])) for r in vd["roles"]], [
            ("Tech Lead", "Apr 2025 – Oct 2025", 5), ("Senior IT Associate", "Apr 2023 – Mar 2025", 0),
            ("IT Associate", "Aug 2022 – Mar 2023", 0)])
        self.assertEqual((bc["title"], bc["org"], bc["dates"], bc["location"], len(bc["bullets"])),
                         ("Frontend Developer (Intern)", "BinCraft Technologies Pvt. Ltd.", "Mar 2021 – Jan 2022",
                          "Remote", 2))
        self.assertEqual((gg["title"], gg["org"], gg["start"], gg["end"], gg["location"], len(gg["bullets"])),
                         ("Web Developer (Freelance)", "Gorilla Graphics & Advertisements", (2018, 3), (2021, 2),
                          "Raipur, India", 1))

    def test_education_and_skills(self):
        (edu,) = section(self.cv, "education")["items"]
        self.assertEqual((edu["title"], edu["org"], edu["org_url"], edu["dates"], edu["location"]),
                         ("B.E., Computer Science & Engineering", "Vishwavidyalaya Engineering College, Ambikapur",
                          "https://www.vecambikapur.ac.in/", "Jul 2014 – May 2018", "Ambikapur, India"))
        (skills,) = section(self.cv, "skills")["items"]
        self.assertEqual([(b["label"], len(b["values"])) for b in skills["items"]], [
            ("Frontend", 10), ("Backend & Real-time", 9), ("Engineering Practices", 5),
            ("Agentic Development", 7), ("LLM & RAG", 5)])
        self.assertIn("JavaScript (ES6+)", skills["items"][0]["values"])
        self.assertIn("Vector Search (PostgreSQL)", skills["items"][4]["values"])

    def test_json_resume_is_valid(self):
        data = json.loads((self.out / "resume.json").read_text())
        jsonschema.validate(data, SCHEMA)
        self.assertEqual([(w["name"], w["position"], w.get("endDate")) for w in data["work"]], [
            ("Binaryveda", "Senior Frontend Engineer", None),
            ("vDoIT Technologies Ltd.", "Tech Lead", "2025-10"),
            ("vDoIT Technologies Ltd.", "Senior IT Associate", "2025-03"),
            ("vDoIT Technologies Ltd.", "IT Associate", "2023-03"),
            ("BinCraft Technologies Pvt. Ltd.", "Frontend Developer (Intern)", "2022-01"),
            ("Gorilla Graphics & Advertisements", "Web Developer (Freelance)", "2021-02")])
        self.assertEqual(data["education"][0]["studyType"], "B.E.")
        self.assertEqual(data["education"][0]["area"], "Computer Science & Engineering")
        self.assertEqual(data["basics"]["location"], {"city": "Ambikapur", "countryCode": "IN"})

    def test_json_ld(self):
        page = (self.out / "index.html").read_text()
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
        p = ld["mainEntity"]
        self.assertEqual(ld["@type"], "ProfilePage")
        self.assertEqual((p["name"], p["jobTitle"], p["email"], p["telephone"]),
                         ("Vishal Gupta", "Senior Frontend Engineer", "mailto:vishalgupta26x@gmail.com",
                          "+91-7000971726"))
        self.assertEqual(p["address"], {"@type": "PostalAddress", "addressLocality": "Ambikapur",
                                        "addressCountry": "IN"})
        self.assertEqual(p["sameAs"], ["https://github.com/vishalguptax", "https://linkedin.com/in/vishalgupta26"])
        self.assertEqual(p["worksFor"], {"@type": "Organization", "name": "Binaryveda",
                                         "url": "https://www.binaryveda.com/"})
        self.assertEqual(len(p["knowsAbout"]), 36)

    def test_vcard(self):
        vcf = (self.out / "vishal-gupta.vcf").read_bytes().decode()
        self.assertEqual(vcf, "\r\n".join([
            "BEGIN:VCARD", "VERSION:3.0", "N:Gupta;Vishal;;;", "FN:Vishal Gupta", "TITLE:Senior Frontend Engineer",
            "ORG:Binaryveda", "EMAIL;TYPE=INTERNET:vishalgupta26x@gmail.com", "TEL;TYPE=CELL:+91-7000971726",
            "ADR;TYPE=HOME:;;;Ambikapur;;;India", f"URL:{SITE}/",
            "X-SOCIALPROFILE;TYPE=github:https://github.com/vishalguptax",
            "X-SOCIALPROFILE;TYPE=linkedin:https://linkedin.com/in/vishalgupta26", "END:VCARD", ""]))

    def test_crawl_files_and_images(self):
        self.assertEqual((self.out / "CNAME").read_text(), "cv.example.test\n")
        self.assertIn(f"Sitemap: {SITE}/sitemap.xml", (self.out / "robots.txt").read_text())
        self.assertIn(f"<loc>{SITE}/resume.pdf</loc>", (self.out / "sitemap.xml").read_text())
        llms = (self.out / "llms.txt").read_text()
        for f in ("resume.md", "resume.txt", "resume.json", "resume.pdf", "resume.docx"):
            self.assertIn(f"]({SITE}/{f})", llms)
        og = pymupdf.Pixmap(str(self.out / "og.png"))
        self.assertEqual((og.width, og.height), (1200, 630))
        icon = pymupdf.Pixmap(str(self.out / "apple-touch-icon.png"))
        self.assertEqual((icon.width, icon.height), (180, 180))
        self.assertEqual((self.out / "resume.pdf").read_bytes(), (FIX / "real.pdf").read_bytes())

    def test_page_links_and_downloads(self):
        page = (self.out / "index.html").read_text()
        self.assertIn('href="/resume.pdf" download="Vishal_Gupta_Resume.pdf"', page)
        self.assertIn(f'<link rel="canonical" href="{SITE}/">', page)
        self.assertIn(f'<meta property="og:image" content="{SITE}/og.png">', page)
        self.assertIn('<time datetime="2025-11">Nov 2025</time>', page)


# --------------------------------------------------------------------------
# Edge cases: two pages, headers/footers, many layouts in one file
# --------------------------------------------------------------------------

class EdgeCV(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cv, cls.problems, cls.notes, cls.out = Built.get("edge")
        cls.exp = section(cls.cv, "experience")["items"]

    def test_passes_and_drops_page_furniture(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.notes, ["ignored page headers/footers: 'Page 1 of 2', 'Page 2 of 2', 'Priya Sharma — CV'"])

    def test_contacts_split_on_gaps_and_plain_text_urls(self):
        self.assertEqual([(c["kind"], c["text"], c["url"]) for c in self.cv["contacts"]], [
            ("email", "priya.sharma@example.com", "mailto:priya.sharma@example.com"),
            ("phone", "+44 7700 900123", "tel:+447700900123"),
            ("location", "London, United Kingdom", None),
            ("github", "github.com/priyash", "https://github.com/priyash"),
            ("website", "www.priya.dev", "https://www.priya.dev")])

    def test_summary_keeps_every_character(self):
        expected = ("Staff engineer building reliable, efficient distributed systems for fintech and logistics teams "
                    "across Europe. Comfortable with C++ & Rust; I write about <design docs> and costs (~$5 vs ~$9 per "
                    "run). Notes: 1. not a list; # not a heading; *stars* and _underscores_ stay literal, as do "
                    "`backticks` and [brackets]. “Smart quotes” and it’s fine; naïve café résumé façade; the office "
                    "workflow is efficient and fluffy.")
        (para,) = section(self.cv, "summary")["items"]
        self.assertEqual(plain(para["rich"]), expected)  # ligatures (ffi, fl) expanded, accents intact
        self.assertEqual(bold_parts(para["rich"]), ["Staff engineer"])
        self.assertIn(expected, gfm_plain((self.out / "resume.md").read_text()))
        self.assertIn(expected, (self.out / "resume.txt").read_text())

    def test_entries(self):
        got = [(e["title"], e["org"], e["dates"], e["start"], e["end"], e["current"], e["duration"], e["location"],
                len(e["roles"]), len(e["bullets"]), len(e["paras"])) for e in self.exp]
        self.assertEqual(got, [
            ("Staff Engineer", "Acme Corp", "Sept 2021 – Current", (2021, 9), None, True, "", "London, UK", 0, 5, 0),
            ("Senior Software Engineer", "Globex Ltd, Manchester", "06/2018 – 08/2021", (2018, 6), (2021, 8), False,
             "", "", 0, 2, 1),
            ("Globex Ltd", "", "Sep 2015 – 2018", (2015, 9), (2018, None), False, "", "Manchester", 2, 0, 0),
            ("Contract Developer", "Initech", "Jan 2015 – Aug 2015", (2015, 1), (2015, 8), False, "8 mos", "",
             0, 1, 0),
            ("Research Intern", "Hooli Labs", "May 2014 – Aug 2014", (2014, 5), (2014, 8), False, "", "Remote", 0, 3, 0),
        ])
        self.assertEqual([r["title"] for r in self.exp[2]["roles"]], ["Software Engineer", "Graduate Engineer"])

    def test_dash_bullets_and_description(self):
        e = self.exp[1]
        self.assertEqual(plain(e["paras"][0]["rich"]),
                         "Globex builds logistics software for 300 warehouses in the UK and Ireland.")
        self.assertTrue(plain(e["bullets"][0]["rich"]).startswith("Owned the routing engine (Go, PostgreSQL)"))

    def test_bullet_continues_across_page_break(self):
        first = plain(self.exp[4]["bullets"][0]["rich"])
        self.assertIn("on write-heavy workloads, compaction strategies", first)
        self.assertTrue(first.endswith("wrote up results for the team."))

    def test_link_covers_only_the_linked_words(self):
        rich = self.exp[0]["bullets"][0]["rich"]
        self.assertEqual([(s.text, s.url) for s in rich if s.url],
                         [("engineering blog post", "https://acme.example/blog/payments")])
        self.assertIn("see the [engineering blog post](https://acme.example/blog/payments).",
                      (self.out / "resume.md").read_text())

    def test_projects_education_skills_languages_certifications(self):
        (proj,) = section(self.cv, "projects")["items"]
        self.assertEqual((proj["title"], proj["org_url"], proj["start"]),
                         ("cvgen", "https://github.com/priyash/cvgen", (2024, None)))
        leeds, mumbai = section(self.cv, "education")["items"]
        self.assertEqual((leeds["title"], leeds["org"], leeds["details"]),
                         ("MSc Computer Science", "University of Leeds",
                          ["Distinction; dissertation on consensus protocols"]))
        self.assertEqual((mumbai["title"], [r["title"] for r in mumbai["roles"]]),
                         ("University of Mumbai", ["B.Tech, Information Technology"]))
        skills = section(self.cv, "skills")["items"][0]["items"]
        self.assertEqual([b["label"] for b in skills], ["Languages", "Cloud", "Practices"])
        self.assertEqual(skills[0]["values"][-1], "and a little Haskell for fun on weekends")  # hanging indent
        self.assertEqual(skills[1]["values"][0], "AWS (Lambda, ECS, DynamoDB)")  # commas inside brackets
        langs = section(self.cv, "languages")["items"][0]["items"][0]["values"]
        self.assertEqual(langs, ["English (Native)", "Hindi (Fluent)", "German – Basic"])
        (cert,) = section(self.cv, "certifications")["items"]
        self.assertEqual((cert["title"], cert["org"], cert["start"]),
                         ("AWS Solutions Architect – Professional", "Amazon Web Services", (2023, 3)))

    def test_json_resume_is_valid_and_complete(self):
        data = json.loads((self.out / "resume.json").read_text())
        jsonschema.validate(data, SCHEMA)
        self.assertEqual([e["institution"] for e in data["education"]], ["University of Leeds", "University of Mumbai"])
        self.assertEqual(data["languages"], [{"language": "English", "fluency": "Native"},
                                             {"language": "Hindi", "fluency": "Fluent"},
                                             {"language": "German", "fluency": "Basic"}])
        self.assertEqual(data["certificates"][0]["date"], "2023-03")
        self.assertEqual(data["projects"][0]["url"], "https://github.com/priyash/cvgen")
        self.assertEqual(data["basics"]["location"], {"city": "London", "countryCode": "GB"})


class MinimalCV(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cv, cls.problems, cls.notes, cls.out = Built.get("minimal")

    def test_no_title_line_uses_current_role(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(self.cv["name"], "JOSÉ ÁLVAREZ-RUIZ")
        self.assertEqual((self.cv["title"], self.cv["title_derived"]), ("Backend Developer", True))
        self.assertEqual(self.notes, ["no job title under the name; using the current role: 'Backend Developer'"])

    def test_caps_headings_typed_bullets_and_hyphen_dates(self):
        self.assertEqual([s["title"] for s in self.cv["sections"]], ["Experience", "Education"])
        (e,) = section(self.cv, "experience")["items"]
        self.assertEqual((e["title"], e["joiner"], e["org"], e["dates"], e["start"], e["current"]),
                         ("Backend Developer", "at", "Fintechly", "2019 - Present", (2019, None), True))
        self.assertEqual([plain(b["rich"]) for b in e["bullets"]],
                         ["Built APIs in Python and Django for 50k users.", "Cut AWS costs by 30%."])

    def test_at_is_kept_in_every_format(self):
        self.assertIn("Backend Developer at Fintechly", (self.out / "resume.txt").read_text())
        self.assertIn("### Backend Developer at Fintechly", (self.out / "resume.md").read_text())
        self.assertIn('<p class="org">at Fintechly</p>', (self.out / "index.html").read_text())

    def test_linked_contacts(self):
        self.assertEqual([(c["kind"], c["url"]) for c in self.cv["contacts"]], [
            ("email", "mailto:jose@example.org"), ("phone", "tel:+34600123456"), ("location", None),
            ("linkedin", "https://linkedin.com/in/josealvarez")])
        jsonschema.validate(json.loads((self.out / "resume.json").read_text()), SCHEMA)

    def test_vcard_unicode_and_folding(self):
        vcf = (self.out / "jose-alvarez-ruiz.vcf").read_bytes().decode()
        self.assertIn("N:ÁLVAREZ-RUIZ;JOSÉ;;;", vcf)
        self.assertTrue(all(len(line.encode()) <= 75 for line in vcf.split("\r\n")))


# --------------------------------------------------------------------------
# PDFs that must be refused, with a clear reason
# --------------------------------------------------------------------------

class Refusals(unittest.TestCase):
    def assertRefused(self, pdf: Path, message: str):
        with self.assertRaises(ParseError) as ctx:
            parse_pdf(pdf)
        self.assertIn(message, str(ctx.exception))

    def test_multi_column(self):
        self.assertRefused(FIX / "sidebar.pdf", "multi-column layout")

    def test_no_headings(self):
        self.assertRefused(FIX / "noheadings.pdf", "no section headings found")

    def test_scanned_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = pymupdf.open(FIX / "real.pdf")
            pix = src[0].get_pixmap(dpi=100)
            doc = pymupdf.open()
            page = doc.new_page(width=src[0].rect.width, height=src[0].rect.height)
            page.insert_image(page.rect, pixmap=pix)
            doc.save(Path(tmp) / "scan.pdf")
            self.assertRefused(Path(tmp) / "scan.pdf", "has no text")

    def test_password_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = pymupdf.open(FIX / "real.pdf")
            doc.save(Path(tmp) / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
            self.assertRefused(Path(tmp) / "locked.pdf", "password-protected")

    def test_not_a_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "cv.pdf").write_text("this is not a pdf")
            self.assertRefused(Path(tmp) / "cv.pdf", "can't open the PDF")

    def test_failed_build_leaves_output_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "_site"
            out.mkdir()
            (out / "index.html").write_text("live site")
            env = dict(os.environ, PANDOC=PANDOC)
            r = subprocess.run([sys.executable, str(ROOT / "build" / "build.py"), "--pdf", str(FIX / "sidebar.pdf"),
                                "--out", str(out)], capture_output=True, text=True, env=env, check=False)
            self.assertEqual(r.returncode, 1)
            self.assertIn("the live site was not changed", r.stdout)
            self.assertEqual((out / "index.html").read_text(), "live site")


# --------------------------------------------------------------------------
# The verifier catches broken output
# --------------------------------------------------------------------------

class VerifierCatchesBreakage(unittest.TestCase):
    def setUp(self):
        cv, problems, _, out = Built.get("real")
        self.assertEqual(problems, [])
        self.tmp = Path(tempfile.mkdtemp(prefix="cv-mut-"))
        self.out = self.tmp / "site"
        shutil.copytree(out, self.out)
        self.cv, layout, self.doc = parse_pdf(FIX / "real.pdf")
        self.layout = layout

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def problems(self):
        return verify.verify(self.doc, self.layout, self.cv, self.out, PANDOC)[0]

    def edit(self, name, old, new):
        p = self.out / name
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new, 1))

    def assertCaught(self, fragment):
        probs = self.problems()
        self.assertTrue(any(fragment in p for p in probs), f"expected a problem about {fragment!r}, got {probs}")

    def test_unmodified_output_passes(self):
        self.assertEqual(self.problems(), [])

    def test_dropped_bullet_in_html(self):
        self.edit("index.html", "Shipped web, Android, and iOS apps", "")
        self.assertCaught("index.html is missing text")

    def test_broken_markdown_emphasis(self):
        self.edit("resume.md", "**Svelte 5**", "**Svelte 5*")
        self.assertCaught("resume.md is missing text")

    def test_missing_word_in_text(self):
        self.edit("resume.txt", "Awilix", "")
        self.assertCaught("resume.txt is missing text")

    def test_missing_word_in_docx(self):
        p = self.out / "resume.docx"
        with zipfile.ZipFile(p) as z:
            files = {n: z.read(n) for n in z.namelist()}
        files["word/document.xml"] = files["word/document.xml"].replace(b"Capacitor", b"", 1)
        with zipfile.ZipFile(p, "w") as z:
            for n, data in files.items():
                z.writestr(n, data)
        self.assertCaught("resume.docx is missing text")

    def test_missing_highlight_in_json(self):
        data = json.loads((self.out / "resume.json").read_text())
        data["work"][0]["highlights"].pop(1)
        (self.out / "resume.json").write_text(json.dumps(data))
        self.assertCaught("resume.json is missing")

    def test_wrong_json_ld(self):
        self.edit("index.html", '"jobTitle":"Senior Frontend Engineer"', '"jobTitle":"Engineer"')
        self.assertCaught("JSON-LD")

    def test_duplicate_id_and_dead_anchor(self):
        self.edit("index.html", 'id="skills"', 'id="summary"')
        probs = self.problems()
        self.assertTrue(any("duplicate ids" in p for p in probs))
        self.assertTrue(any("missing anchor: #skills" in p for p in probs))

    def test_vcard_line_endings(self):
        p = self.out / "vishal-gupta.vcf"
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
        self.assertCaught("vCard")

    def test_missing_file(self):
        (self.out / "llms.txt").unlink()
        self.assertCaught("llms.txt is missing")


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

class Markdown(unittest.TestCase):
    TRICKY = ["1. not a list", "2) nor this", "# not a heading", "- not a bullet", "+ nor this", "> not a quote",
              "a*b*c and *d*", "snake_case and _e_", "~a~ and ~~b~~", "about ~5 and ~9", "<div> & <b>x</b>",
              "&amp; &#169;", "`code` and ``x``", "[x](y) and ![i](j)", "back\\slash", "| a | b |", "C++ & C#",
              "100% *", "=== not setext", "<https://example.com>", "a  two  spaces", "trailing \\"]

    def test_escaped_text_renders_literally(self):
        for s in self.TRICKY:
            with self.subTest(s=s):
                self.assertEqual(gfm_plain(render.md_escape(s, line_start=True, tildes=True)),
                                 re.sub(r"\s+", " ", s).strip())

    def test_emphasis_next_to_punctuation_and_other_emphasis(self):
        cases = [
            [Seg("Node.js:", bold=True), Seg("Express")],
            [Seg("say "), Seg("(hi)", italic=True), Seg("there")],
            [Seg("bold", bold=True), Seg("italic", italic=True), Seg(" end")],
            [Seg("**literal**", bold=True)],
            [Seg("1.", bold=True), Seg(" first")],
        ]
        for rich in cases:
            with self.subTest(rich=[s.text for s in rich]):
                self.assertEqual(gfm_plain(render.rich_md(rich)), plain(rich))

    def test_link_with_spaces_in_url(self):
        md = render.rich_md([Seg("see "), Seg("docs", url="https://x.test/a b(1)")])
        self.assertEqual(md, "see [docs](<https://x.test/a b(1)>)")


class Dates(unittest.TestCase):
    def test_parse_ym(self):
        for text, want in [("Nov 2025", (2025, 11)), ("November 2025", (2025, 11)), ("Sept 2020", (2020, 9)),
                           ("Sep. 2020", (2020, 9)), ("11/2025", (2025, 11)), ("1/2020", (2020, 1)),
                           ("2025-11", (2025, 11)), ("2019", (2019, None)), ("13/2020", None), ("Foo 2020", None),
                           ("June, 2021", (2021, 6))]:
            with self.subTest(text=text):
                self.assertEqual(parse_ym(text), want)

    def test_parse_period(self):
        p = parse_period("Nov 2025 – Present | Mumbai, India")
        self.assertEqual((p["start"], p["end"], p["current"], p["location"]), ((2025, 11), None, True, "Mumbai, India"))
        p = parse_period("Jan 2020 - Mar 2022 · 2 yrs 3 mos · Remote")
        self.assertEqual((p["start"], p["end"], p["duration"], p["location"]),
                         ((2020, 1), (2022, 3), "2 yrs 3 mos", "Remote"))
        p = parse_period("2016 to 2018")
        self.assertEqual((p["start"], p["end"]), ((2016, None), (2018, None)))
        p = parse_period("Berlin, Germany")
        self.assertEqual((p["dates"], p["location"]), ("", "Berlin, Germany"))
        p = parse_period("Mar 2023")
        self.assertEqual((p["start"], p["end"], p["current"]), ((2023, 3), None, False))
        p = parse_period("Since Jan 2024 | Pune")
        self.assertEqual((p["start"], p["location"]), ((2024, 1), "Pune"))

    def test_durations_in_html(self):
        e = {"dates": "Jan 2020 – Mar 2022", "start": (2020, 1), "end": (2022, 3), "start_text": "Jan 2020",
             "end_text": "Mar 2022", "duration": "", "location": "", "current": False}
        self.assertIn('<span class="dur">2 yrs 3 mos</span>', render.period_html(e, True))
        e |= {"start": (2020, None), "end": (2022, None), "start_text": "2020", "end_text": "2022", "dates": "2020 – 2022"}
        self.assertNotIn("dur", render.period_html(e, True))  # years only: no made-up month count


class Contacts(unittest.TestCase):
    def test_classify(self):
        for text, url, kind in [("me@x.io", None, "email"), ("Email", "mailto:me@x.io", "email"),
                                ("+1 (555) 010-9999", None, "phone"), ("github.com/me", None, "github"),
                                ("GitHub", "https://github.com/me", "github"), ("in/me", "https://www.linkedin.com/in/me", "linkedin"),
                                ("me.dev", None, "website"), ("https://me.dev/blog", None, "website"),
                                ("Pune, India", None, "text"), ("Open to relocation", None, "text")]:
            with self.subTest(text=text):
                self.assertEqual(classify_contact(text, url)["kind"], kind)


class VCard(unittest.TestCase):
    def test_escaping_and_folding(self):
        self.assertEqual(render.vcard_escape("a,b;c\\d"), "a\\,b\\;c\\\\d")
        line = "NOTE:" + "é" * 60
        folded = render._fold(line)
        self.assertTrue(all(len(p.encode()) <= 75 for p in folded.split("\r\n")))
        self.assertEqual(folded.replace("\r\n ", ""), line)


class CleanText(unittest.TestCase):
    def test_odd_spaces_zero_width_and_icon_glyphs(self):
        from pdfparse import clean
        cases = {"a" + chr(0xA0) + "b": "a b", "a" + chr(0x2009) + "b": "a b", "a" + chr(0x200B) + "b": "ab",
                 "a" + chr(0xFEFF) + "b": "ab", "x" + chr(0xE001) + "y": "xy", "e" + chr(0x301): chr(0xE9)}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean(raw), want)

    def test_soft_hyphen_joins_word(self):
        from pdfparse import append_line
        rich = append_line([Seg("develop" + chr(0xAD))], [Seg("ment plan")])
        self.assertEqual(plain(rich), "development plan")


class Tokens(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(verify.tokens("Office ﬁles — “quoted”, on-site/remote"),
                         ["office", "files", "quoted", "on", "site", "remote"])


class FindPdf(unittest.TestCase):
    def test_uppercase_extension_and_empty_folder(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            folder = Path(tmp)
            with self.assertRaises(ParseError):
                build.find_pdf(folder)
            shutil.copyfile(FIX / "minimal.pdf", folder / "CV.PDF")
            self.assertEqual(build.find_pdf(folder).name, "CV.PDF")


class Dates2(unittest.TestCase):
    def test_updated_date_falls_back_to_pdf_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = pymupdf.open(FIX / "real.pdf")
            doc.set_metadata({"modDate": "D:20250102030405Z"})
            path = Path(tmp) / "x.pdf"
            doc.save(path)
            self.assertEqual(build.updated_date(path, pymupdf.open(path)), dt.date(2025, 1, 2))


if __name__ == "__main__":
    unittest.main()
