"""Turn the résumé model from pdfparse.py into every published format."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import unicodedata
from pathlib import Path

from pdfparse import Seg, fmt_ym, plain

BUILD = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# Model helpers
# --------------------------------------------------------------------------

def contact(cv, kind):
    return next((c for c in cv["contacts"] if c["kind"] == kind), None)


def find_section(cv, sid):
    return next((s for s in cv["sections"] if s["id"] == sid), None)


def entries(cv, sid):
    s = find_section(cv, sid)
    return [i for i in (s["items"] if s else []) if i["type"] == "entry"]


def summary_text(cv) -> str:
    s = find_section(cv, "summary")
    text = " ".join(plain(i["rich"]) for i in s["items"] if i["type"] == "para") if s else ""
    return text or cv["title"]


def skills_flat(cv) -> list[str]:
    out = []
    for sec in cv["sections"]:
        if sec["id"] == "skills":
            for it in sec["items"]:
                for b in it.get("items", []):
                    out += b.get("values", [])
    return out


def current_job(cv):
    """(role title, organisation, organisation url, entry) for the current position, if any."""
    for e in entries(cv, "experience"):
        if e["roles"]:
            for r in e["roles"]:
                if r["current"]:
                    return r["title"], e["title"], e["org_url"], r
        elif e["current"]:
            return e["title"], e["org"], e["org_url"], e
    return None


def split_location(text: str):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) > 1:
        return parts[0], parts[-1]
    return text.strip(), ""


COUNTRY_CODES = {"india": "IN", "united states": "US", "usa": "US", "us": "US", "united kingdom": "GB",
                 "uk": "GB", "england": "GB", "germany": "DE", "canada": "CA", "singapore": "SG",
                 "united arab emirates": "AE", "uae": "AE", "australia": "AU", "netherlands": "NL",
                 "france": "FR", "ireland": "IE", "japan": "JP", "remote": ""}


def ym_iso(ym) -> str:
    if not ym:
        return ""
    return f"{ym[0]:04d}-{ym[1]:02d}" if ym[1] else f"{ym[0]:04d}"


def months_between(a, b) -> int | None:
    if not a or not b or not a[1] or not b[1]:
        return None
    return (b[0] - a[0]) * 12 + (b[1] - a[1]) + 1


def duration_text(n: int) -> str:
    y, m = divmod(n, 12)
    parts = ([f"{y} yr" + ("s" if y > 1 else "")] if y else []) + ([f"{m} mo" + ("s" if m > 1 else "")] if m else [])
    return " ".join(parts)


def ascii_fold(text: str) -> str:
    """'José Álvarez' -> 'Jose Alvarez' (for file names and URL slugs)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ascii_fold(text).lower()).strip("-")


def file_stem(cv) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_fold(cv["name"])).strip("_") or "Resume"


def vcf_name(cv) -> str:
    return slug(cv["name"]) + ".vcf"


def initials(cv) -> str:
    words = [w for w in re.split(r"\s+", cv["name"]) if w[:1].isalpha()]
    return "".join(w[0] for w in (words[:1] + words[-1:] if len(words) > 1 else words)).upper()


def meta_parts(e: dict) -> list[str]:
    parts = [e["dates"]] if e["dates"] else []
    if e["duration"]:
        parts.append(e["duration"])
    if e["location"]:
        parts.append(e["location"])
    return parts


# --------------------------------------------------------------------------
# Markdown (GitHub-flavoured, as AI tools and GitHub read it)
# --------------------------------------------------------------------------

def md_escape(text: str, *, line_start: bool = False, tildes: bool = False) -> str:
    """Escape text so CommonMark/GFM shows it literally."""
    out = re.sub(r"([\\`*_\[\]])", r"\\\1", text)
    out = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", out)
    out = re.sub(r"&(?=#?\w+;)", r"\\&", out)
    if tildes:
        out = out.replace("~", "\\~")
    if line_start:
        out = re.sub(r"^(\s*)(\d+)([.)])(?=\s|$)", r"\1\2\\\3", out)
        out = re.sub(r"^(\s*)([#>+=|-])", r"\1\\\2", out)
    return out


def md_url(url: str) -> str:
    """A link destination; angle brackets only when the URL has spaces or brackets."""
    return f"<{url}>" if re.search(r"[\s()<>]", url) else url


def rich_md(rich: list[Seg], *, line_start: bool = True) -> str:
    tildes = plain(rich).count("~") >= 2  # a pair of single tildes would strike text through
    out = ""
    for i, s in enumerate(rich):
        text = s.text
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        core = text.strip()
        if not core:
            out += text
            continue
        pre = post = ""
        if s.bold or s.italic:
            # emphasis must start and end next to a letter, not punctuation, when a word touches it
            nxt = rich[i + 1].text[:1] if i + 1 < len(rich) and not trail else ""
            prv = out[-1:] if not lead else ""
            while core and not core[-1].isalnum() and nxt.isalnum():
                post = core[-1] + post
                core = core[:-1]
            while core and not core[0].isalnum() and prv.isalnum():
                pre += core[0]
                core = core[1:]
        start = line_start and not out.strip()
        body = md_escape(core, line_start=start and not pre, tildes=tildes)
        if core and (s.bold or s.italic):
            mark = ("**" if s.bold else "") + ("*" if s.italic else "")
            if (out + lead + md_escape(pre)).endswith("*"):  # touching another emphasis run
                mark = mark.replace("*", "_")
            body = mark + body + mark[::-1]
        if s.url and core and s.url.startswith(("http://", "https://", "mailto:")):
            body = f"[{body}]({md_url(s.url)})"
        out += lead + md_escape(pre, line_start=start, tildes=tildes) + body + md_escape(post, tildes=tildes) + trail
    return out


def to_markdown(cv: dict, site_url: str, updated: dt.date) -> str:
    L = [f"# {md_escape(cv['name'], line_start=True)}", "", f"**{md_escape(cv['title'])}**", ""]
    for t in cv["tagline"]:
        L += [md_escape(t, line_start=True), ""]
    bits = []
    for c in cv["contacts"]:
        if c["kind"] in ("github", "linkedin", "website") and c["url"]:
            bits.append(f"[{md_escape(c['text'])}]({md_url(c['url'])})")
        else:
            bits.append(md_escape(c["text"]))
    if bits:
        L += [" · ".join(bits), ""]
    for sec in cv["sections"]:
        L += [f"## {md_escape(sec['title'])}", ""]
        for it in sec["items"]:
            if it["type"] == "para":
                L += [rich_md(it["rich"]), ""]
            elif it["type"] == "list":
                for b in it["items"]:
                    if "label" in b:
                        L.append(f"- **{md_escape(b['label'])}:** {md_escape(', '.join(b['values']))}")
                    else:
                        L.append(f"- {rich_md(b['rich'], line_start=True)}")
                L.append("")
            else:
                _md_entry(L, it, 3)
    L += ["---", "", f"Web: {site_url}/ · PDF: {site_url}/resume.pdf · Updated {updated.isoformat()}", ""]
    return "\n".join(L)


def _md_entry(L: list[str], e: dict, level: int) -> None:
    title = md_escape(e["title"])
    if e["org"]:
        org = md_escape(e["org"])
        title += (f" {md_escape(e['joiner'])} " if e["joiner"] else " — ") + (
            f"[{org}]({md_url(e['org_url'])})" if e["org_url"] else org)
    elif e["org_url"]:
        title = f"[{title}]({md_url(e['org_url'])})"
    L.append(f"{'#' * level} {title}")
    L.append("")
    meta = meta_parts(e)
    if meta:
        L += [" · ".join(md_escape(m) for m in meta), ""]
    for d in e["details"]:
        L += [md_escape(d, line_start=True), ""]
    for p in e["paras"]:
        L += [rich_md(p["rich"]), ""]
    for b in e["bullets"]:
        L.append(f"- {rich_md(b['rich'])}")
    if e["bullets"]:
        L.append("")
    for r in e["roles"]:
        _md_entry(L, r, level + 1)


# --------------------------------------------------------------------------
# Plain text (applicant tracking systems)
# --------------------------------------------------------------------------

def rich_txt(rich: list[Seg]) -> str:
    out = ""
    for s in rich:
        out += s.text
        url = s.url or ""
        if url.startswith(("http://", "https://")) and s.text.strip() and slug(s.text) not in slug(url):
            out += f" ({url})"
    return re.sub(r"\s+", " ", out).strip()


def to_text(cv: dict, site_url: str, updated: dt.date) -> str:
    L = [cv["name"], cv["title"], *cv["tagline"], ""]
    labels = {"email": "Email", "phone": "Phone", "location": "Location", "github": "GitHub",
              "linkedin": "LinkedIn", "website": "Website"}
    for c in cv["contacts"]:
        if c["kind"] in labels:
            value = c["url"] if c["kind"] in ("github", "linkedin", "website") else c["text"]
            extra = f" ({c['text']})" if c["kind"] in ("github", "linkedin", "website") and slug(c["text"]) not in slug(value) else ""
            L.append(f"{labels[c['kind']]}: {value}{extra}")
        else:
            L.append(c["text"])
    L.append(f"Online résumé: {site_url}/")
    for sec in cv["sections"]:
        L += ["", sec["title"].upper(), "=" * len(sec["title"])]
        for it in sec["items"]:
            if it["type"] == "para":
                L += [rich_txt(it["rich"])]
            elif it["type"] == "list":
                for b in it["items"]:
                    L.append(f"- {b['label']}: {', '.join(b['values'])}" if "label" in b else f"- {rich_txt(b['rich'])}")
            elif it["roles"]:
                L += ["", it["title"] + (f" ({it['org_url']})" if it["org_url"] else "")]
                if it["org"]:
                    L.append(it["org"])
                L += [" | ".join(meta_parts(it))] if meta_parts(it) else []
                _txt_body(L, it)
                for r in it["roles"]:
                    L += ["", f"{r['title']} | {it['title']}"]
                    _txt_entry_rest(L, r)
            else:
                L += ["", it["title"] + ((f" {it['joiner']} " if it["joiner"] else " | ") + it["org"] if it["org"] else "")]
                _txt_entry_rest(L, it)
    L += ["", f"PDF: {site_url}/resume.pdf", f"Updated: {updated.isoformat()}", ""]
    return "\n".join(L)


def _txt_entry_rest(L, e):
    if e["org_url"]:
        L.append(e["org_url"])
    if meta_parts(e):
        L.append(" | ".join(meta_parts(e)))
    _txt_body(L, e)


def _txt_body(L, e):
    L += e["details"]
    L += [rich_txt(p["rich"]) for p in e["paras"]]
    L += [f"- {rich_txt(b['rich'])}" for b in e["bullets"]]


# --------------------------------------------------------------------------
# JSON Resume (https://jsonresume.org/schema)
# --------------------------------------------------------------------------

def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _entry_summary(e) -> str:
    return " ".join(e["details"] + [plain(p["rich"]) for p in e["paras"]])


def _location_obj(text: str) -> dict:
    city, country = split_location(text)
    code = COUNTRY_CODES.get(country.lower()) if country else None
    return _clean({"city": city, "countryCode": code, "region": country if country and code is None else ""})


def to_json_resume(cv: dict, site_url: str, updated: dt.date) -> dict:
    loc = contact(cv, "location")
    profiles = []
    for k, network in (("github", "GitHub"), ("linkedin", "LinkedIn")):
        c = contact(cv, k)
        if c:
            profiles.append({"network": network, "username": c["url"].rstrip("/").rsplit("/", 1)[-1], "url": c["url"]})
    basics = _clean({
        "name": cv["name"], "label": cv["title"], "email": (contact(cv, "email") or {}).get("text"),
        "phone": (contact(cv, "phone") or {}).get("text"), "url": site_url + "/", "summary": summary_text(cv),
        "location": _location_obj(loc["text"]) if loc else None, "profiles": profiles,
    })
    out = {"$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
           "basics": basics}
    work, education, projects, certificates, awards, languages, interests, other = ([] for _ in range(8))
    for sec in cv["sections"]:
        kind = re.sub(r"-\d+$", "", sec["id"])
        for it in sec["items"]:
            if kind == "experience" and it["type"] == "entry":
                for r in (it["roles"] or [it]):
                    org = it["title"] if it["roles"] else it["org"]
                    work.append(_clean({
                        "name": org, "position": r["title"], "url": it["org_url"] or r["org_url"],
                        "location": r["location"], "startDate": ym_iso(r["start"]), "endDate": ym_iso(r["end"]),
                        "summary": " ".join(filter(None, [_entry_summary(it) if it["roles"] and r is it["roles"][0] else "",
                                                          _entry_summary(r)])),
                        "highlights": [plain(b["rich"]) for b in (it["bullets"] if it["roles"] and r is it["roles"][0] else []) + r["bullets"]],
                    }))
            elif kind == "education" and it["type"] == "entry":
                for r in (it["roles"] or [it]):
                    institution = it["title"] if it["roles"] else (it["org"] or it["title"])
                    degree = r["title"] if (it["roles"] or it["org"]) else ""
                    m = re.match(r"^([A-Z][\w.]*(?:\s?[A-Z][\w.]*)?)\s*(?:,|in|of)\s+(.+)$", degree)
                    study, area = (m.group(1), m.group(2)) if m and len(m.group(1)) <= 12 else ("", degree)
                    education.append(_clean({
                        "institution": institution, "url": it["org_url"] or r["org_url"], "area": area,
                        "studyType": study, "startDate": ym_iso(r["start"]), "endDate": ym_iso(r["end"]),
                        "score": " ".join(r["details"]),
                        "courses": [plain(b["rich"]) for b in r["bullets"]] + [plain(p["rich"]) for p in r["paras"]],
                    }))
            elif kind == "projects" and it["type"] == "entry":
                projects.append(_clean({
                    "name": it["title"], "description": " ".join(filter(None, [it["org"], _entry_summary(it)])),
                    "url": it["org_url"], "startDate": ym_iso(it["start"]), "endDate": ym_iso(it["end"]),
                    "highlights": [plain(b["rich"]) for b in it["bullets"]],
                }))
            elif kind == "certifications" and it["type"] == "entry":
                certificates.append(_clean({"name": it["title"], "issuer": " ".join(filter(None, [it["org"], *it["details"]])),
                                            "date": ym_iso(it["end"] or it["start"]), "url": it["org_url"]}))
            elif kind == "awards" and it["type"] == "entry":
                awards.append(_clean({"title": it["title"], "awarder": it["org"], "date": ym_iso(it["end"] or it["start"]),
                                      "summary": " ".join([_entry_summary(it)] + [plain(b["rich"]) for b in it["bullets"]]).strip()}))
            elif kind == "languages" and it["type"] == "list":
                for b in it["items"]:
                    for v in b.get("values") or [plain(b["rich"])]:
                        m = re.match(r"^(.*?)\s*[(:–-]\s*(.+?)\)?$", v)
                        languages.append(_clean({"language": m.group(1), "fluency": m.group(2)} if m else {"language": v}))
            elif kind == "interests" and it["type"] == "list":
                for b in it["items"]:
                    interests.append(_clean({"name": b.get("label") or ", ".join(b.get("values") or [plain(b["rich"])]),
                                             "keywords": b.get("values") if b.get("label") else []}))
            elif kind in ("summary", "skills"):
                continue
            else:
                other.append({"section": sec["title"], "text": _item_text(it)})
    skills = []
    for sec in cv["sections"]:
        if sec["id"] == "skills":
            for it in sec["items"]:
                for b in it.get("items", []):
                    skills.append(_clean({"name": b.get("label") or "Skills", "keywords": b.get("values", [])}))
    for key, val in (("work", work), ("education", education), ("skills", skills), ("projects", projects),
                     ("certificates", certificates), ("awards", awards), ("languages", languages),
                     ("interests", interests)):
        if val:
            out[key] = val
    out["meta"] = _clean({"canonical": site_url + "/resume.json", "lastModified": updated.isoformat(),
                          "source": site_url + "/resume.pdf", "otherSections": other})
    return out


def _item_text(it) -> str:
    if it["type"] == "para":
        return plain(it["rich"])
    if it["type"] == "list":
        return " ".join((b.get("label", "") + ": " if b.get("label") else "") + ", ".join(b["values"]) if "values" in b
                        else plain(b["rich"]) for b in it["items"])
    parts = [it["title"], it["org"], *meta_parts(it), _entry_summary(it)] + [plain(b["rich"]) for b in it["bullets"]]
    for r in it["roles"]:
        parts.append(_item_text(r | {"type": "entry"}))
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# schema.org JSON-LD
# --------------------------------------------------------------------------

def to_jsonld(cv: dict, site_url: str, updated: dt.date) -> dict:
    person = {"@type": "Person", "@id": site_url + "/#person", "name": cv["name"], "jobTitle": cv["title"],
              "url": site_url + "/", "description": summary_text(cv)}
    same = [c["url"] for c in cv["contacts"] if c["kind"] in ("github", "linkedin", "website") and c["url"]]
    if same:
        person["sameAs"] = same
    if (c := contact(cv, "email")):
        person["email"] = "mailto:" + c["text"]
    if (c := contact(cv, "phone")):
        person["telephone"] = c["text"]
    if (c := contact(cv, "location")):
        city, country = split_location(c["text"])
        person["address"] = _clean({"@type": "PostalAddress", "addressLocality": city,
                                    "addressCountry": COUNTRY_CODES.get(country.lower()) or country})
    skills = skills_flat(cv)
    if skills:
        person["knowsAbout"] = skills
    job = current_job(cv)
    if job and job[1]:
        person["worksFor"] = _clean({"@type": "Organization", "name": job[1], "url": job[2]})
    alumni = [_clean({"@type": "EducationalOrganization", "name": e["org"] or e["title"], "url": e["org_url"]})
              for e in entries(cv, "education")]
    if alumni:
        person["alumniOf"] = alumni
    return {"@context": "https://schema.org", "@type": "ProfilePage", "@id": site_url + "/", "url": site_url + "/",
            "name": f"{cv['name']} — {cv['title']}", "dateModified": updated.isoformat(),
            "mainEntity": person,
            "associatedMedia": {"@type": "MediaObject", "contentUrl": site_url + "/resume.pdf",
                                "encodingFormat": "application/pdf", "name": f"{cv['name']} résumé (PDF)"}}


# --------------------------------------------------------------------------
# vCard 3.0 (RFC 2426)
# --------------------------------------------------------------------------

def vcard_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold lines longer than 75 octets, never splitting a UTF-8 character."""
    out, cur = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((cur + ch).encode("utf8")) > limit:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def to_vcard(cv: dict, site_url: str) -> str:
    words = cv["name"].split()
    family = words[-1] if len(words) > 1 else ""
    given = words[0]
    middle = " ".join(words[1:-1])
    L = ["BEGIN:VCARD", "VERSION:3.0",
         "N:" + ";".join(vcard_escape(x) for x in (family, given, middle, "", "")),
         "FN:" + vcard_escape(cv["name"]), "TITLE:" + vcard_escape(cv["title"])]
    job = current_job(cv)
    if job and job[1]:
        L.append("ORG:" + vcard_escape(job[1]))
    if (c := contact(cv, "email")):
        L.append("EMAIL;TYPE=INTERNET:" + vcard_escape(c["text"]))
    if (c := contact(cv, "phone")):
        L.append("TEL;TYPE=CELL:" + vcard_escape(c["text"]))
    if (c := contact(cv, "location")):
        city, country = split_location(c["text"])
        L.append("ADR;TYPE=HOME:;;;" + vcard_escape(city) + ";;;" + vcard_escape(country))
    L.append("URL:" + site_url + "/")
    for k in ("github", "linkedin"):
        if (c := contact(cv, k)):
            L.append(f"X-SOCIALPROFILE;TYPE={k}:" + c["url"])
    L.append("END:VCARD")
    return "".join(_fold(line) + "\r\n" for line in L)


# --------------------------------------------------------------------------
# llms.txt, crawl files, icons
# --------------------------------------------------------------------------

def to_llms(cv: dict, site_url: str, updated: dt.date, formats: list[tuple[str, str, str]]) -> str:
    facts = [f"{cv['name']} is a {cv['title']}"]
    if (loc := contact(cv, "location")):
        facts[0] += f" based in {loc['text']}"
    facts[0] += "."
    if (job := current_job(cv)) and job[1]:
        since = f" (since {job[3]['start_text']})" if job[3]["start_text"] else ""
        facts.append(f"Current role: {job[0]} at {job[1]}{since}.")
    if (c := contact(cv, "email")):
        facts.append(f"Email: {c['text']}.")
    skills = skills_flat(cv)
    if skills:
        facts.append("Skills include " + ", ".join(skills[:20]) + ".")
    L = [f"# {md_escape(cv['name'])} — {md_escape(cv['title'])}", "",
         f"> {md_escape(summary_text(cv))}", "", md_escape(" ".join(facts)), "",
         f"This is the official résumé of {md_escape(cv['name'])}, generated from the PDF on {updated.isoformat()}. "
         "The Markdown and plain-text versions contain the complete résumé; prefer them over parsing the PDF.", "",
         "## Résumé", ""]
    for href, label, desc in formats:
        L.append(f"- [{label}]({site_url}/{href}): {desc}")
    L.append(f"- [Web page]({site_url}/): the résumé as an HTML page")
    links = [(c["text"] if c["kind"] == "website" else c["kind"].replace("github", "GitHub").replace("linkedin", "LinkedIn"), c["url"])
             for c in cv["contacts"] if c["kind"] in ("github", "linkedin", "website")]
    if links:
        L += ["", "## Profiles", ""] + [f"- [{md_escape(t)}]({u})" for t, u in links]
    return "\n".join(L) + "\n"


def sitemap(site_url: str, updated: dt.date) -> str:
    d = updated.isoformat()
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{site_url}/</loc><lastmod>{d}</lastmod></url>\n"
            f"  <url><loc>{site_url}/resume.pdf</loc><lastmod>{d}</lastmod></url>\n</urlset>\n")


def favicon_svg(cv) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" '
            'fill="#8f1d2c"/><text x="32" y="43" text-anchor="middle" font-family="Georgia,\'Times New Roman\',serif" '
            f'font-size="30" font-weight="700" fill="#fff">{html.escape(initials(cv))}</text></svg>\n')


def manifest(cv) -> str:
    return json.dumps({"name": f"{cv['name']} — Résumé", "short_name": cv["name"].split()[0] + " CV",
                       "start_url": "/", "display": "browser", "background_color": "#f7f5f0",
                       "theme_color": "#8f1d2c",
                       "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
                                 {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]},
                      ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# HTML page
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=True)


SAFE_URL = re.compile(r"^(https?:|mailto:|tel:)", re.I)


def rich_html(rich: list[Seg]) -> str:
    out = []
    for s in rich:
        t = esc(s.text)
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.url and SAFE_URL.match(s.url) and s.text.strip():
            t = f'<a href="{esc(s.url)}">{t}</a>'
        out.append(t)
    return "".join(out)


ICONS = {  # 24px stroke icons drawn inline, so there are no extra requests
    "email": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "phone": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    "location": '<path d="M12 21s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
    "github": '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-4 0v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>',
    "website": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "text": '<circle cx="12" cy="12" r="1.5"/>',
    "download": '<path d="M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
    "theme": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>',
    "file": '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
}


def icon(name: str) -> str:
    return ('<svg class="i" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def _time(text: str, ym) -> str:
    return f'<time datetime="{ym_iso(ym)}">{esc(text)}</time>' if ym else esc(text)


def period_html(e: dict, show_duration: bool) -> str:
    bits = []
    if e["dates"]:
        d = esc(e["dates"])
        if e["start_text"] and e["start"]:
            d = d.replace(esc(e["start_text"]), _time(e["start_text"], e["start"]), 1)
        if e["end_text"] and e["end"]:
            i = d.rfind(esc(e["end_text"]))
            if i >= 0:
                d = d[:i] + _time(e["end_text"], e["end"]) + d[i + len(esc(e["end_text"])):]
        bits.append(f'<span class="dates">{d}</span>')
    if e["duration"]:
        bits.append(f'<span class="dur">{esc(e["duration"])}</span>')
    elif show_duration and (n := months_between(e["start"], e["end"])):
        bits.append(f'<span class="dur">{duration_text(n)}</span>')
    elif show_duration and e["current"] and e["start"] and e["start"][1]:
        bits.append(f'<span class="dur" data-since="{ym_iso(e["start"])}"></span>')
    if e["location"]:
        bits.append(f'<span class="loc">{esc(e["location"])}</span>')
    return f'<p class="meta">{"".join(bits)}</p>' if bits else ""


def _body_html(e: dict) -> str:
    out = "".join(f'<p class="detail">{esc(d)}</p>' for d in e["details"])
    out += "".join(f"<p>{rich_html(p['rich'])}</p>" for p in e["paras"])
    if e["bullets"]:
        out += "<ul>" + "".join(f"<li>{rich_html(b['rich'])}</li>" for b in e["bullets"]) + "</ul>"
    return out


def _link(text: str, url: str | None) -> str:
    return f'<a href="{esc(url)}">{esc(text)}</a>' if url and SAFE_URL.match(url) else esc(text)


def entry_html(e: dict, show_duration: bool) -> str:
    if e["roles"]:
        roles = "".join(
            f'<li class="role"><div class="role-head"><h4>{_link(r["title"], r["org_url"])}</h4>'
            f'{period_html(r, show_duration)}</div>{_body_html(r)}</li>' for r in e["roles"])
        n = len(e["roles"])
        sub = esc(e["org"]) if e["org"] else f"{n} role{'s' if n > 1 else ''}"
        return (f'<article class="entry group"><div class="entry-head"><div><h3>{_link(e["title"], e["org_url"])}</h3>'
                f'<p class="org">{sub}</p></div>{period_html(e, show_duration)}</div>{_body_html(e)}'
                f'<ol class="roles">{roles}</ol></article>')
    joiner = esc(e["joiner"]) + " " if e["joiner"] else ""
    org = f'<p class="org">{joiner}{_link(e["org"], e["org_url"])}</p>' if e["org"] else ""
    title = _link(e["title"], e["org_url"]) if e["org_url"] and not e["org"] else esc(e["title"])
    return (f'<article class="entry"><div class="entry-head"><div><h3>{title}</h3>{org}</div>'
            f'{period_html(e, show_duration)}</div>{_body_html(e)}</article>')


def _chips(values: list[str]) -> str:
    return '<ul class="chips">' + "".join(f"<li>{esc(v)}</li>" for v in values) + "</ul>"


def _values_html(items: list[dict]) -> str:
    """Labelled groups ("Frontend: …") as a definition list, unlabelled ones as plain lists, in order."""
    out, run = [], []
    for b in items + [{}]:
        if b.get("label"):
            run.append(f"<div><dt>{esc(b['label'])}</dt><dd>{_chips(b['values'])}</dd></div>")
            continue
        if run:
            out.append('<dl class="skills">' + "".join(run) + "</dl>")
            run = []
        if b:
            out.append(_chips(b["values"]))
    return "".join(out)


def section_html(sec: dict) -> str:
    show_duration = re.sub(r"-\d+$", "", sec["id"]) in ("experience", "volunteer")
    parts = []
    for it in sec["items"]:
        if it["type"] == "para":
            parts.append(f"<p>{rich_html(it['rich'])}</p>")
        elif it["type"] == "entry":
            parts.append(entry_html(it, show_duration))
        elif all("values" in b for b in it["items"]):
            parts.append(_values_html(it["items"]))
        else:
            parts.append('<ul class="list">' + "".join(f"<li>{rich_html(b['rich'])}</li>" for b in it["items"]) + "</ul>")
    sid = esc(sec["id"])
    return (f'<section id="{sid}" aria-labelledby="{sid}-h"><h2 id="{sid}-h">{esc(sec["title"])}</h2>'
            + "".join(parts) + "</section>")


def short_title(t: str) -> str:
    return re.sub(r"^(professional|work|relevant|technical|core|key)\s+", "", t, flags=re.I)


def human_size(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def render_index(cv: dict, site_url: str, updated: dt.date, *, pdf_size: int, pages: int,
                 formats: list[tuple[str, str, str]]) -> str:
    tpl = (BUILD / "template.html").read_text()
    contacts = []
    for c in cv["contacts"]:
        label = c["text"]
        sr = {"email": "Email: ", "phone": "Phone: ", "location": "Location: "}.get(c["kind"], "")
        inner = f'{icon(c["kind"])}<span>{f"<span class=sr>{sr}</span>" if sr else ""}{esc(label)}</span>'
        if c["url"] and SAFE_URL.match(c["url"]):
            rel = ' rel="me"' if c["kind"] in ("github", "linkedin", "website") else ""
            inner = f'<a href="{esc(c["url"])}"{rel}>{inner}</a>'
        contacts.append(f"<li>{inner}</li>")
    pdf_name = file_stem(cv) + "_Resume.pdf"
    fmt = [f'<li><a href="/resume.pdf" download="{esc(pdf_name)}">{icon("download")}PDF<span class="ext">.pdf</span></a></li>']
    for href, label, _ in formats:
        if href.endswith(".pdf"):
            continue
        ext = "." + href.rsplit(".", 1)[-1]
        fmt.append(f'<li><a href="/{esc(href)}" download>{icon("file")}{esc(label)}<span class="ext">{ext}</span></a></li>')
    desc = summary_text(cv)
    meta_desc = desc if len(desc) <= 155 else desc[:152].rsplit(" ", 1)[0].rstrip(",.;:—–-") + "…"
    words = cv["name"].split()
    pdf_meta = f"PDF · {pages} page{'s' if pages > 1 else ''} · {human_size(pdf_size)}"
    jsonld = json.dumps(to_jsonld(cv, site_url, updated), ensure_ascii=False, separators=(",", ":"))
    values = {
        "TITLE": esc(f"{cv['name']} — {cv['title']} | Résumé"), "DESC": esc(meta_desc), "URL": site_url + "/",
        "SITE": site_url, "NAME": esc(cv["name"]), "FIRST": esc(words[0]), "LAST": esc(words[-1] if len(words) > 1 else ""),
        "JOB": esc(cv["title"]), "INITIALS": esc(initials(cv)),
        "TAGLINE": "".join(f'<p class="tagline">{esc(t)}</p>' for t in cv["tagline"]),
        "CONTACTS": "".join(contacts), "FORMATS": "".join(fmt),
        "NAV": "".join(f'<li><a href="#{esc(s["id"])}">{esc(short_title(s["title"]))}</a></li>' for s in cv["sections"]),
        "BODY": "".join(section_html(s) for s in cv["sections"]),
        "UPDATED_ISO": updated.isoformat(), "UPDATED": f"{updated.day} {fmt_ym((updated.year, updated.month))}",
        "PDF_META": pdf_meta, "PDF_NAME": esc(pdf_name),
        "CSS": (BUILD / "style.css").read_text().strip(), "JS": (BUILD / "app.js").read_text().strip(),
        "JSONLD": jsonld.replace("<", "\\u003c"),
        "I_DOWNLOAD": icon("download"), "I_SHARE": icon("share"), "I_THEME": icon("theme"), "I_FILE": icon("file"),
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], tpl)


def render_404(cv: dict) -> str:
    return (BUILD / "404.html").read_text().replace("{{NAME}}", esc(cv["name"]))


def render_upload(cv: dict, upload_url: str) -> str:
    return (BUILD / "upload.html").read_text().replace("{{NAME}}", esc(cv["name"])) \
        .replace("{{URL}}", esc(upload_url)).replace("{{URL_JS}}", json.dumps(upload_url))
