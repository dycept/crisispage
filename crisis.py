#!/usr/bin/env python3
"""crisispage - a tiny static "official statements" page for use during a cyber incident.

Standard library only (Python >= 3.11). No network access, no shell, no web admin.
Updates are plain text files in updates/<ID>/<lang>.txt; `build` turns them into
static HTML (+ optional RSS) in public/. The build is deterministic: same input,
byte-identical output (no build time, no locale, no randomness).

Layout of this file: errors -> config -> i18n -> updates -> rendering -> build -> commands.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Config, i18n/, updates/ and public/ all live next to this script.
ROOT = Path(__file__).resolve().parent

ID_RE = re.compile(r"\d{8}-\d{6}(?:-\d+)?")  # folder name = update ID
LANG_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}")  # ASCII (punycode for IDN)
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)  # candidate link inside update text
CONFIG_URL_RE = re.compile(r"https?://[^\s<>\"']+")
BAD_CHARS = re.compile("[\x00-\x08\x0b-\x1f\x7f￾￿]")  # control chars (illegal in XML)

CONFIG_KEYS = {"organization", "contact_email", "contact_phone", "site_url", "timezone", "languages",
               "default_language", "rss", "official_links", "deploy_command", "intro", "legal_notice",
               "credits_link", "favicon", "logo", "logo_alt"}
I18N_STRINGS = ("name", "title", "heading", "default_intro", "contact", "phone", "last_updated", "no_updates",
                "feed", "not_translated", "legal", "legal_not_translated", "back", "verify", "credits", "footer",
                "date_format")
I18N_PLACEHOLDERS = {"title": {"org"}, "heading": {"org"}, "default_intro": {"org"}, "credits": {"link"},
                     "date_format": {"day", "month", "year", "hour", "minute"}}  # all other texts: none allowed
PHONE_RE = re.compile(r"\+?[0-9][0-9 ()./\-]{3,28}[0-9]")
LEGAL_PLACEHOLDER = "TODO-REPLACE"  # init writes it into legal/en.txt; the build refuses to publish it

# Images (favicon, logo): only same-origin files, copied into public/. Checked by extension and content.
ASSET_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
FAVICON_EXTS = {".svg", ".png", ".ico"}
LOGO_EXTS = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".ico": b"\x00\x00\x01\x00", ".jpg": b"\xff\xd8\xff",
         ".jpeg": b"\xff\xd8\xff", ".gif": b"GIF8"}
MAX_ASSET_BYTES = 512 * 1024
SVG_FORBIDDEN_TAGS = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video", "set", "animate",
                      "animatetransform", "animatemotion"}
# Built-in favicon: a flat blue square with a white "i". Replace it via 'favicon' in config.toml.
DEFAULT_FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                   '<rect width="32" height="32" fill="#336699"/><rect x="14" y="13" width="4" height="11" fill="#fff"/>'
                   '<rect x="14" y="7" width="4" height="4" fill="#fff"/></svg>\n')

CONFIG_TEMPLATE = '''\
# crisispage configuration. Every value is validated on each build.

organization     = "Example Ltd"
contact_email    = "crisis-comms@example.com"   # required; shown as mailto: link
site_url         = "https://status.example.com" # no trailing slash; required when rss = true
timezone         = "Europe/Berlin"              # IANA name; timestamps are shown in this zone
languages        = ["en"]                       # e.g. ["en", "de", "fr"]; each needs i18n/<code>.toml
default_language = "en"                         # served at the site root; must be in languages
rss              = true                         # one RSS 2.0 feed per language

# Channels on which the address of this page is officially announced (optional).
official_links = ["https://www.example.com/imprint"]

# Phone number shown next to the contact e-mail (optional).
# contact_phone = "+49 30 1234567"

# Legal notice / imprint page, linked from every footer (optional). The text lives in
# legal/<code>.txt, one file per language; the file of the default language is required.
# legal_notice = true

# Favicon: the built-in icon is used unless you name your own file (.svg, .png or .ico)
# or switch it off. Logo: shown above the heading (.svg, .png, .jpg, .gif or .webp, max 512 KB).
# favicon = "assets/favicon.svg"
# favicon = false
# logo = "assets/logo.png"
# logo_alt = "Example Ltd logo"   # default: the organization name; "" for a purely decorative logo

# Credits: a small "Powered by crisispage" link in the footer (optional). Keep commented out to hide it.
# credits_link = "https://github.com/dycept/crisispage"

# Upload command, run WITHOUT a shell from this directory (optional).
# deploy_command = "rsync -a --delete public/ deploy@status.example.com:./"

# Own introduction text per language (optional; empty = default text from i18n/<code>.toml).
[intro]
en = ""
'''

CSS = """\
body{font-family:Georgia,"Times New Roman",Times,serif;color:#000;background:#fff;max-width:760px;margin:0;padding:8px 12px;line-height:1.4}
a{text-decoration:underline}
a:link{color:#0000EE}
a:visited{color:#551A8B}
h1{font-size:1.8em;margin:.3em 0}
h1,h2,li,p,td,th{overflow-wrap:anywhere}
header,article,footer{border:1px solid #336699;background:#EEF0F4;margin:12px 0}
header,footer{padding:4px 12px}
.lang{font-size:.9em;margin:4px 0}
table{border-collapse:collapse;margin:8px 0;background:#fff}
th,td{border:1px solid #336699;padding:3px 8px;text-align:start;vertical-align:top}
th{background:#C9D3E0}
hr{border:0;border-top:1px solid #336699}
main hr{display:none}
h2{margin:0;padding:4px 12px;background:#336699;color:#fff;font-size:1.1em}
article p{margin:0;padding:6px 12px}
article .when{background:#C9D3E0;border-bottom:1px solid #336699;font-size:.9em}
.note{font-style:italic}
.logo{margin:6px 0}
.logo img{display:block;max-width:100%;max-height:96px;width:auto;height:auto}
footer{font-size:.9em}
@media print{.lang{display:none}a{color:#000}h2{background:none;color:#000;border-bottom:1px solid #000}a[href^="http"]:after,a[href^="mailto"]:after{content:" (" attr(href) ")"}}
"""

LEGAL_TEMPLATE = f"""\
{LEGAL_PLACEHOLDER}: delete this line and replace everything below with your legal notice / imprint.
The build refuses to publish this file while the line above is present.

Example Ltd
Example Street 1
12345 Example City

Represented by: [authorised representative]
Commercial register: [court and number]
VAT ID: [number]
E-mail: crisis-comms@example.com
"""

ROBOTS = "User-agent: *\nDisallow: /\n"  # served at the site root only; pages also carry noindex

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # RSS dates are always English (RFC 822)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class CrisisError(Exception):
    """Expected failure: shown to the user as 'error: ...', exit code 1."""


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- config

@dataclass
class Config:
    organization: str
    contact_email: str
    site_url: str
    tzname: str
    tz: tzinfo
    languages: list[str]
    default_language: str
    rss: bool
    official_links: list[str]
    deploy_command: str
    intro: dict[str, str] = field(default_factory=dict)
    contact_phone: str = ""
    legal_notice: bool = False
    credits_link: str = ""
    favicon_on: bool = True  # False: no favicon at all
    favicon: str = ""  # path of a replacement for the built-in favicon
    logo: str = ""
    logo_alt: str = ""


def read_toml(path: Path, what: str) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CrisisError(f"cannot read {what}: {e.strerror}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise CrisisError(f"{what} is not valid TOML: {e}") from None


def load_config(root: Path) -> Config:
    path = root / "config.toml"
    if not path.is_file():
        raise CrisisError("config.toml not found - run 'python3 crisis.py init' first")
    raw = read_toml(path, "config.toml")
    errors: list[str] = []

    def string(key: str, required: bool = False, default: str = "") -> str:
        v = raw.get(key, default)
        if not isinstance(v, str) or BAD_CHARS.search(v) or "\n" in v:
            errors.append(f"'{key}' must be a single-line text")
            return default
        v = v.strip()
        if required and not v:
            errors.append(f"'{key}' is required")
        return v

    def strings(key: str, default: list[str]) -> list[str]:
        v = raw.get(key, default)
        if not isinstance(v, list) or not all(isinstance(i, str) for i in v):
            errors.append(f"'{key}' must be a list of texts")
            return default
        return v

    for key in sorted(set(raw) - CONFIG_KEYS):
        errors.append(f"unknown setting '{key}'")

    organization = string("organization", required=True)
    email = string("contact_email", required=True)
    if email and not EMAIL_RE.fullmatch(email):
        errors.append(f"'contact_email' does not look like an e-mail address: {email!r}")

    site_url = string("site_url")
    if site_url and not CONFIG_URL_RE.fullmatch(site_url):
        errors.append("'site_url' must start with http:// or https://")
    elif site_url.endswith("/"):
        errors.append("'site_url' must not end with a slash")

    tzname = string("timezone", default="UTC") or "UTC"
    tz: tzinfo = timezone.utc
    try:
        if tzname != "UTC":  # UTC works even without system tz data
            tz = ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        errors.append(f"'timezone' is not a valid IANA time zone: {tzname!r} (is tzdata installed?)")

    languages = strings("languages", ["en"])
    default_language = string("default_language", default="en") or "en"
    if not languages:
        errors.append("'languages' must not be empty")
    for lang in languages:
        if not LANG_RE.fullmatch(lang):
            errors.append(f"'languages': invalid language code {lang!r} (lowercase, e.g. 'en' or 'pt-br')")
        elif not (root / "i18n" / f"{lang}.toml").is_file():
            errors.append(f"'languages': unknown language {lang!r} (no i18n/{lang}.toml)")
    if len(set(languages)) != len(languages):
        errors.append("'languages' contains duplicates")
    if default_language not in languages:
        errors.append(f"'default_language' {default_language!r} must be listed in 'languages'")

    rss = raw.get("rss", False)
    if not isinstance(rss, bool):
        errors.append("'rss' must be true or false")
        rss = False
    if rss and not site_url:
        errors.append("'site_url' is required when rss = true (feeds need absolute links)")

    links = strings("official_links", [])
    for link in links:
        if not CONFIG_URL_RE.fullmatch(link):
            errors.append(f"'official_links': must start with http:// or https://: {link!r}")

    deploy_command = string("deploy_command")
    if deploy_command:
        try:
            shlex.split(deploy_command)
        except ValueError as e:
            errors.append(f"'deploy_command' cannot be parsed: {e}")

    intro = raw.get("intro", {})
    if not isinstance(intro, dict) or not all(isinstance(v, str) and not BAD_CHARS.search(v.replace("\n", ""))
                                              for v in intro.values()):
        errors.append("[intro] must map language codes to texts")
        intro = {}

    phone = string("contact_phone")
    if phone and not PHONE_RE.fullmatch(phone):
        errors.append(f"'contact_phone' does not look like a phone number: {phone!r}")

    legal_notice = raw.get("legal_notice", False)
    if not isinstance(legal_notice, bool):
        errors.append("'legal_notice' must be true or false")
        legal_notice = False

    credits_link = string("credits_link")
    if credits_link and not CONFIG_URL_RE.fullmatch(credits_link):
        errors.append("'credits_link' must start with http:// or https://")

    favicon_on, favicon = True, ""
    if isinstance(raw.get("favicon", True), bool):
        favicon_on = raw.get("favicon", True)
    elif isinstance(raw["favicon"], str):
        favicon = raw["favicon"].strip()
    else:
        errors.append("'favicon' must be the path of an image file, or false")
    logo = string("logo")
    logo_alt = string("logo_alt", default=organization)

    if errors:
        raise CrisisError("invalid config.toml:\n  - " + "\n  - ".join(errors))
    return Config(organization, email, site_url, tzname, tz, languages, default_language, rss, links,
                  deploy_command, {k: v.strip() for k, v in intro.items()}, phone, legal_notice, credits_link,
                  favicon_on, favicon, logo, logo_alt)


# --------------------------------------------------------------------------- i18n

def fill(template: str, **values: str) -> str:
    """Replace {name} placeholders. Not str.format: translation files must not be able to do anything else."""
    return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], template)


def load_i18n(root: Path, lang: str) -> dict:
    path = root / "i18n" / f"{lang}.toml"
    where = f"i18n/{lang}.toml"
    raw = read_toml(path, where)
    errors = [f"missing or empty key '{k}'" for k in I18N_STRINGS
              if not isinstance(raw.get(k), str) or not raw[k].strip()]
    months = raw.get("months")
    if not (isinstance(months, list) and len(months) == 12 and all(isinstance(m, str) and m for m in months)):
        errors.append("'months' must be a list of 12 names")
    if raw.get("dir") not in ("ltr", "rtl"):
        errors.append("'dir' must be \"ltr\" or \"rtl\"")
    for key in I18N_STRINGS:
        if isinstance(raw.get(key), str):
            bad = set(re.findall(r"\{([^{}]*)\}", raw[key])) - I18N_PLACEHOLDERS.get(key, set())
            if bad:
                errors.append(f"'{key}' uses unknown placeholder(s): {', '.join(sorted(bad))}")
    if isinstance(raw.get("credits"), str) and raw["credits"].count("{link}") != 1:
        errors.append("'credits' must contain {link} exactly once")
    if any(isinstance(v, str) and BAD_CHARS.search(v) for v in raw.values()):
        errors.append("contains control characters")
    if errors:
        raise CrisisError(f"{where} is invalid:\n  - " + "\n  - ".join(errors))
    return raw


def utc_label(d: datetime) -> str:
    minutes = int(d.utcoffset().total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def format_date(dt: datetime, tr: dict, tz: tzinfo) -> str:
    """'12 Oct 2026, 23:41 (UTC+02:00)' - names and layout come from the language file, not the locale."""
    d = dt.astimezone(tz)
    text = fill(tr["date_format"], day=str(d.day), month=tr["months"][d.month - 1], year=str(d.year),
                hour=f"{d.hour:02d}", minute=f"{d.minute:02d}")
    return f"{text} ({utc_label(d)})"


# --------------------------------------------------------------------------- updates

@dataclass
class Update:
    id: str
    date: datetime  # timezone-aware; authoritative, from the default-language file
    texts: dict[str, tuple[str, str]]  # language -> (title, body)


def read_text_file(path: Path, rel: str) -> str:
    """UTF-8 text with normalised line breaks; control characters (illegal in XML) are rejected."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except OSError as e:
        raise CrisisError(f"cannot read {rel}: {e.strerror}") from None
    except UnicodeDecodeError:
        raise CrisisError(f"{rel} is not valid UTF-8") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if BAD_CHARS.search(text):
        raise CrisisError(f"{rel} contains control characters")
    return text


def parse_message(path: Path, need_date: bool) -> tuple[str, datetime | None, str]:
    """File format: 'key: value' header lines, one blank line, then the text."""
    rel = f"updates/{path.parent.name}/{path.name}"
    text = read_text_file(path, rel)
    head, *rest = re.split(r"\n[ \t]*\n", text.lstrip("\n"), maxsplit=1)
    body = rest[0] if rest else ""
    fields: dict[str, str] = {}
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        key = key.strip().lower()
        if not colon or key not in ("title", "date"):
            raise CrisisError(f"{rel}: header line {line!r} is not 'title: ...' or 'date: ...'")
        fields[key] = value.strip()
    if not fields.get("title"):
        raise CrisisError(f"{rel}: missing 'title:' line")
    date = None
    if need_date:
        try:
            date = datetime.fromisoformat(fields.get("date", ""))
        except ValueError:
            raise CrisisError(f"{rel}: 'date:' must look like 2026-10-12T23:41:19+02:00") from None
        if date.utcoffset() is None:
            raise CrisisError(f"{rel}: 'date:' needs a UTC offset, e.g. +02:00 or Z")
    return fields["title"], date, body.strip()


def load_updates(root: Path, cfg: Config) -> list[Update]:
    """All updates, newest first. Files for languages that are not configured are ignored."""
    base = root / "updates"
    found: list[Update] = []
    for folder in sorted(base.iterdir()) if base.is_dir() else []:
        if folder.name.startswith("."):
            continue
        if not folder.is_dir() or not ID_RE.fullmatch(folder.name):
            warn(f"ignoring updates/{folder.name}: not an update folder (expected YYYYMMDD-HHMMSS)")
            continue
        default_file = folder / f"{cfg.default_language}.txt"
        if not default_file.is_file():
            raise CrisisError(f"updates/{folder.name}/{cfg.default_language}.txt is missing "
                              "(the default language is required)")
        title, date, body = parse_message(default_file, need_date=True)
        texts = {cfg.default_language: (title, body)}
        for lang in cfg.languages:
            if lang != cfg.default_language and (folder / f"{lang}.txt").is_file():
                t, _, b = parse_message(folder / f"{lang}.txt", need_date=False)
                texts[lang] = (t, b)
        found.append(Update(folder.name, date, texts))
    found.sort(key=lambda u: (u.date, u.id), reverse=True)
    return found


def missing_translations(u: Update, cfg: Config) -> list[str]:
    return [lang for lang in cfg.languages if lang not in u.texts]


# --------------------------------------------------------------------------- legal notice, images

def load_legal(root: Path, cfg: Config) -> dict[str, str]:
    """legal/<lang>.txt per configured language (plain text, like an update body). Default language required."""
    texts: dict[str, str] = {}
    for lang in cfg.languages:
        path, rel = root / "legal" / f"{lang}.txt", f"legal/{lang}.txt"
        if not path.is_file():
            if lang == cfg.default_language:
                raise CrisisError(f"legal_notice = true, but {rel} is missing (the default language is required)")
            continue
        text = read_text_file(path, rel).strip()
        if LEGAL_PLACEHOLDER in text:
            raise CrisisError(f"{rel} still contains the {LEGAL_PLACEHOLDER} placeholder - write your legal notice first")
        if not text:
            raise CrisisError(f"{rel} is empty")
        texts[lang] = text
    return texts


def check_svg(data: bytes, what: str, rel: str) -> None:
    """An SVG is XML and can carry scripts or load other files: only plain graphics are accepted."""
    fail = lambda why: CrisisError(f"'{what}': {rel!r} is not accepted: {why}")  # noqa: E731
    if b"<!ENTITY" in data:
        raise fail("entity declarations")
    try:
        tree = ET.fromstring(data)
    except ET.ParseError as e:
        raise CrisisError(f"'{what}': {rel!r} is not valid SVG: {e}") from None
    css_external = re.compile(r"@import|url\(\s*['\"]?(?!#|data:image/)", re.I)
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""
        if tag in SVG_FORBIDDEN_TAGS:
            raise fail(f"<{tag}> element")
        if tag == "style" and css_external.search(el.text or ""):
            raise fail("<style> loading other files")
        for name, value in el.attrib.items():
            name = name.rsplit("}", 1)[-1].lower()
            if name.startswith("on"):
                raise fail(f"event attribute {name!r}")
            if "javascript:" in value.lower() or (name == "style" and css_external.search(value)):
                raise fail(f"attribute {name!r}")
            if name == "href" and not value.strip().lower().startswith(("#", "data:image/")):
                raise fail("link to another file")


def load_asset(root: Path, rel: str, what: str, allowed: set[str]) -> tuple[str, bytes]:
    """Read and check an image named in config.toml. Returns (extension, bytes)."""
    path = (root / rel).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise CrisisError(f"'{what}': {rel!r} must be an existing file inside the project directory")
    ext = path.suffix.lower()
    if ext not in allowed:
        raise CrisisError(f"'{what}': {rel!r} must be one of: {', '.join(sorted(allowed))}")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise CrisisError(f"cannot read {rel}: {e.strerror}") from None
    if len(data) > MAX_ASSET_BYTES:
        raise CrisisError(f"'{what}': {rel!r} is larger than {MAX_ASSET_BYTES // 1024} KB (a crisis page must load fast)")
    if ext == ".svg":
        check_svg(data, what, rel)
    elif not (data[:4] == b"RIFF" and data[8:12] == b"WEBP" if ext == ".webp" else data.startswith(MAGIC[ext])):
        raise CrisisError(f"'{what}': {rel!r} is not a valid {ext} image")
    return (".jpg" if ext == ".jpeg" else ext), data


def load_assets(root: Path, cfg: Config) -> dict[str, tuple[str, bytes]]:
    """{'favicon': (file name in public/, bytes), 'logo': ...} for the images that are switched on."""
    assets = {}
    if cfg.favicon_on:
        ext, data = load_asset(root, cfg.favicon, "favicon", FAVICON_EXTS) if cfg.favicon else (
            ".svg", DEFAULT_FAVICON.encode())
        assets["favicon"] = (f"favicon{ext}", data)
    if cfg.logo:
        ext, data = load_asset(root, cfg.logo, "logo", LOGO_EXTS)
        assets["logo"] = (f"logo{ext}", data)
    return assets


# --------------------------------------------------------------------------- rendering

esc = html.escape  # always quote=True: safe in text and in attribute values


def trim_url(url: str) -> str:
    """Sentence punctuation after a URL is not part of it; neither is an unbalanced ')'."""
    while url:
        if url[-1] in ".,;:!?'’”»]}":
            url = url[:-1]
        elif url[-1] == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            break
    return url


def linkify(line: str) -> str:
    """Escape a line of text; turn http(s):// addresses into links. Nothing else is ever linked."""
    out, pos = [], 0
    for m in URL_RE.finditer(line):
        url = trim_url(m.group())
        if not re.sub(r"^https?://", "", url, flags=re.I):
            continue
        out.append(esc(line[pos:m.start()]))
        out.append(f'<a href="{esc(url)}">{esc(url)}</a>')
        pos = m.start() + len(url)
    out.append(esc(line[pos:]))
    return "".join(out)


def render_text(text: str) -> str:
    """Plain text -> <p> per paragraph (blank-line separated), <br> for single line breaks."""
    paragraphs = re.split(r"\n[ \t]*\n\s*", text.strip())
    return "\n".join("<p>" + "<br>\n".join(linkify(line.rstrip()) for line in p.split("\n")) + "</p>"
                     for p in paragraphs if p.strip())


@dataclass
class Item:
    """One update as shown in one language (possibly the fallback)."""
    id: str
    title: str
    body: str
    date: datetime
    lang: str  # language of title/body
    fallback: bool


def items_for(updates: list[Update], lang: str, cfg: Config) -> list[Item]:
    items = []
    for u in updates:
        used = lang if lang in u.texts else cfg.default_language
        title, body = u.texts[used]
        items.append(Item(u.id, title, body, u.date, used, used != lang))
    return items


def href(frm: str, to: str, cfg: Config, name: str = "index.html") -> str:
    """Relative path between language versions; the default language lives in the site root."""
    return ("" if frm == cfg.default_language else "../") + (name if to == cfg.default_language
                                                              else f"{to}/{name}")


def page_top(cfg: Config, lang: str, trs: dict[str, dict], assets: dict[str, str], name: str,
             title: str) -> list[str]:
    """Everything from <!DOCTYPE> to the heading area of <header> (language line, logo), for page `name`."""
    tr = trs[lang]
    up = "" if lang == cfg.default_language else "../"
    h = ["<!DOCTYPE html>", f'<html lang="{esc(lang)}" dir="{tr["dir"]}">', "<head>", '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         '<meta name="robots" content="noindex">', f"<title>{esc(title)}</title>",
         f'<link rel="stylesheet" href="{up}style.css">']
    if "favicon" in assets:
        h.append(f'<link rel="icon" href="{up}{assets["favicon"]}" '
                 f'type="{ASSET_TYPES[Path(assets["favicon"]).suffix]}">')
    if len(cfg.languages) > 1:
        for other in cfg.languages:
            h.append(f'<link rel="alternate" hreflang="{esc(other)}" href="{esc(href(lang, other, cfg, name))}">')
        h.append(f'<link rel="alternate" hreflang="x-default" '
                 f'href="{esc(href(lang, cfg.default_language, cfg, name))}">')
    if cfg.rss:
        h.append(f'<link rel="alternate" type="application/rss+xml" title="{esc(tr["feed"])}" href="feed.xml">')
    h += ["</head>", "<body>", "<header>"]
    if len(cfg.languages) > 1:
        parts = [f'<strong lang="{esc(c)}">{esc(trs[c]["name"])}</strong>' if c == lang else
                 f'<a href="{esc(href(lang, c, cfg, name))}" lang="{esc(c)}" hreflang="{esc(c)}">'
                 f'{esc(trs[c]["name"])}</a>' for c in cfg.languages]
        h.append('<p class="lang">' + " | ".join(parts) + "</p>")
    if "logo" in assets:
        h.append(f'<p class="logo"><img src="{up}{assets["logo"]}" alt="{esc(cfg.logo_alt)}"></p>')
    return h


def page_bottom(cfg: Config, lang: str, trs: dict[str, dict]) -> list[str]:
    """Footer, shared by all pages: official channels, legal notice link, credits."""
    tr = trs[lang]
    h = ["<hr>", "<footer>"]
    if cfg.official_links:
        h.append(f"<p>{esc(tr['verify'])}</p>")
        h.append("<ul>" + "".join(f'<li><a href="{esc(u)}">{esc(u)}</a></li>' for u in cfg.official_links) + "</ul>")
    h.append(f"<p>{esc(tr['footer'])}</p>")
    extra = []
    if cfg.legal_notice:
        extra.append(f'<a href="legal.html">{esc(tr["legal"])}</a>')
    if cfg.credits_link:
        before, after = tr["credits"].split("{link}")
        extra.append(f'{esc(before)}<a href="{esc(cfg.credits_link)}">crisispage</a>{esc(after)}')
    if extra:
        h.append("<p>" + " | ".join(extra) + "</p>")
    return h + ["</footer>", "</body>", "</html>"]


def render_page(cfg: Config, lang: str, trs: dict[str, dict], updates: list[Update],
                assets: dict[str, str]) -> str:
    tr = trs[lang]
    items = items_for(updates, lang, cfg)
    org = cfg.organization
    h = page_top(cfg, lang, trs, assets, "index.html", fill(tr["title"], org=org))
    h.append(f"<h1>{esc(fill(tr['heading'], org=org))}</h1>")
    h.append(render_text(cfg.intro.get(lang) or fill(tr["default_intro"], org=org)))
    h.append("<table>")
    h.append(f'<tr><th scope="row">{esc(tr["contact"])}</th>'
             f'<td><a href="mailto:{esc(cfg.contact_email)}" dir="ltr">{esc(cfg.contact_email)}</a></td></tr>')
    if cfg.contact_phone:
        tel = re.sub(r"[^0-9+]", "", cfg.contact_phone.replace("(0)", ""))  # "+49 (0)30 1" dials +49301
        h.append(f'<tr><th scope="row">{esc(tr["phone"])}</th>'
                 f'<td><a href="tel:{esc(tel)}" dir="ltr">{esc(cfg.contact_phone)}</a></td></tr>')
    if items:
        newest = items[0].date.astimezone(cfg.tz)
        h.append(f'<tr><th scope="row">{esc(tr["last_updated"])}</th>'
                 f'<td><time datetime="{newest.isoformat()}">{esc(format_date(newest, tr, cfg.tz))}</time></td></tr>')
    h.append("</table>")
    if cfg.rss:
        h.append(f'<p><a href="feed.xml">{esc(tr["feed"])}</a></p>')
    h += ["</header>", "<hr>", "<main>"]
    if not items:
        h.append(f"<p>{esc(tr['no_updates'])}</p>")
    for n, it in enumerate(items):
        if n:
            h.append("<hr>")
        d = it.date.astimezone(cfg.tz)
        art_tr = trs[it.lang]
        attrs = f' lang="{esc(it.lang)}" dir="{art_tr["dir"]}"' if it.fallback else ""
        h.append(f'<article id="u-{it.id}"{attrs}>')
        h.append(f"<h2>{esc(it.title)}</h2>")
        h.append(f'<p class="when"><a href="#u-{it.id}"><time datetime="{d.isoformat()}">'
                 f'{esc(format_date(d, art_tr, cfg.tz))}</time></a></p>')
        if it.fallback:
            h.append(f'<p class="note" lang="{esc(lang)}" dir="{tr["dir"]}">{esc(tr["not_translated"])}</p>')
        h.append(render_text(it.body))
        h.append("</article>")
    h.append("</main>")
    return "\n".join(h + page_bottom(cfg, lang, trs)) + "\n"


def render_legal(cfg: Config, lang: str, trs: dict[str, dict], legal: dict[str, str],
                 assets: dict[str, str]) -> str:
    """legal.html: the legal notice / imprint in `lang`, or in the default language with a notice."""
    tr = trs[lang]
    used = lang if lang in legal else cfg.default_language
    h = page_top(cfg, lang, trs, assets, "legal.html", f"{tr['legal']} \u2013 {cfg.organization}")
    h.append(f"<h1>{esc(fill(tr['heading'], org=cfg.organization))}</h1>")
    h.append(f'<p><a href="index.html">{esc(tr["back"])}</a></p>')
    h += ["</header>", "<hr>", "<main>"]
    h.append('<article id="legal"' + (f' lang="{esc(used)}" dir="{trs[used]["dir"]}"' if used != lang else "") + ">")
    h.append(f"<h2>{esc(tr['legal'])}</h2>")
    if used != lang:
        h.append(f'<p class="note" lang="{esc(lang)}" dir="{tr["dir"]}">{esc(tr["legal_not_translated"])}</p>')
    h += [render_text(legal[used]), "</article>", "</main>"]
    return "\n".join(h + page_bottom(cfg, lang, trs)) + "\n"


def rfc822(dt: datetime, tz: tzinfo) -> str:
    d = dt.astimezone(tz)
    off = d.utcoffset().total_seconds() // 60
    zone = f"{'+' if off >= 0 else '-'}{abs(int(off)) // 60:02d}{abs(int(off)) % 60:02d}"
    return (f"{WEEKDAYS[d.weekday()]}, {d.day:02d} {MONTHS[d.month - 1]} {d.year} "
            f"{d.hour:02d}:{d.minute:02d}:{d.second:02d} {zone}")


def render_feed(cfg: Config, lang: str, trs: dict[str, dict], updates: list[Update]) -> str:
    tr = trs[lang]
    base = cfg.site_url + "/" + ("" if lang == cfg.default_language else f"{lang}/")
    org = cfg.organization
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">', "<channel>",
         f"<title>{esc(fill(tr['title'], org=org))}</title>", f"<link>{esc(base)}</link>",
         f"<description>{esc(cfg.intro.get(lang) or fill(tr['default_intro'], org=org))}</description>",
         f"<language>{esc(lang)}</language>"]
    items = items_for(updates, lang, cfg)
    if items:
        x.append(f"<lastBuildDate>{rfc822(items[0].date, cfg.tz)}</lastBuildDate>")
    x.append(f'<atom:link href="{esc(base)}feed.xml" rel="self" type="application/rss+xml"/>')
    for it in items:
        link = f"{base}#u-{it.id}"
        note = f'<p>{esc(tr["not_translated"])}</p>\n' if it.fallback else ""
        x += ["<item>", f"<title>{esc(it.title)}</title>", f"<link>{esc(link)}</link>",
              f'<guid isPermaLink="true">{esc(link)}</guid>', f"<pubDate>{rfc822(it.date, cfg.tz)}</pubDate>",
              f"<description>{esc(note + render_text(it.body))}</description>", "</item>"]
    x += ["</channel>", "</rss>"]
    return "\n".join(x) + "\n"


# --------------------------------------------------------------------------- build

def manifest(files: dict[str, bytes]) -> bytes:
    """SHA256SUMS.txt in `sha256sum -c` format: '<hash>  <path>' per file, sorted, without itself."""
    return "".join(f"{hashlib.sha256(files[rel]).hexdigest()}  {rel}\n" for rel in sorted(files)).encode()


def render_site(root: Path) -> tuple[Config, list[Update], dict[str, bytes]]:
    """Everything that ends up in public/, as {relative path: bytes}. Pure function of the input files."""
    cfg = load_config(root)
    trs = {lang: load_i18n(root, lang) for lang in cfg.languages}
    updates = load_updates(root, cfg)
    legal = load_legal(root, cfg) if cfg.legal_notice else {}
    files = {"style.css": CSS.encode(), "robots.txt": ROBOTS.encode()}
    assets = {}
    for kind, (name, data) in load_assets(root, cfg).items():
        files[name], assets[kind] = data, name
    for lang in cfg.languages:
        prefix = "" if lang == cfg.default_language else f"{lang}/"
        files[prefix + "index.html"] = render_page(cfg, lang, trs, updates, assets).encode()
        if cfg.legal_notice:
            files[prefix + "legal.html"] = render_legal(cfg, lang, trs, legal, assets).encode()
        if cfg.rss:
            files[prefix + "feed.xml"] = render_feed(cfg, lang, trs, updates).encode()
    files["SHA256SUMS.txt"] = manifest(files)
    return cfg, updates, files


def write_atomic(path: Path, data: bytes) -> None:
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        return  # unchanged: keep mtime so rsync/etag stay quiet
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".crisis-tmp-")  # hidden: never served
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def remove_stale(public: Path, keep: set[str]) -> None:
    """Delete everything in public/ that this build did not produce. public/ itself stays (bind mounts)."""
    for dirpath, dirnames, filenames in os.walk(public, topdown=False):
        here = Path(dirpath)
        for name in filenames + [d for d in dirnames if (here / d).is_symlink()]:
            p = here / name
            if p.relative_to(public).as_posix() not in keep:
                p.unlink()
        if here != public and not any(here.iterdir()):
            here.rmdir()


def build(root: Path) -> int:
    cfg, updates, files = render_site(root)
    for u in updates:
        gaps = missing_translations(u, cfg)
        if gaps:
            warn(f"update {u.id} has no translation for: {', '.join(gaps)} (default language shown instead)")
    public = root / "public"
    public.mkdir(exist_ok=True)
    for rel, data in files.items():
        write_atomic(public / rel, data)
    remove_stale(public, set(files))
    print(f"built {len(updates)} update(s) in {len(cfg.languages)} language(s) into public/")
    print(f"build hash (sha256 of SHA256SUMS.txt): {hashlib.sha256(files['SHA256SUMS.txt']).hexdigest()}")
    return 0


# --------------------------------------------------------------------------- commands

def check_title(title: str) -> str:
    title = title.strip()
    if not title or "\n" in title or "\r" in title or BAD_CHARS.search(title):
        raise CrisisError("the title must be a single non-empty line of text")
    return title


def read_text_source(args: argparse.Namespace) -> str:
    """Message text from -m, -f, stdin (if piped) or $EDITOR (if interactive)."""
    if args.message is not None:
        text = args.message
    elif args.file is not None:
        try:
            text = Path(args.file).read_text(encoding="utf-8-sig")
        except OSError as e:
            raise CrisisError(f"cannot read {args.file}: {e.strerror}") from None
        except UnicodeDecodeError:
            raise CrisisError(f"{args.file} is not valid UTF-8") from None
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        editor = os.environ.get("EDITOR", "").strip()
        if not editor:
            raise CrisisError("no text given: use -m, -f, pipe text via stdin, or set $EDITOR")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "message.txt"
            path.write_text("", encoding="utf-8")
            try:
                code = subprocess.run([*shlex.split(editor), str(path)]).returncode
            except (OSError, ValueError) as e:
                raise CrisisError(f"cannot run editor {editor!r}: {e}") from None
            if code != 0:
                raise CrisisError(f"editor exited with status {code}; nothing was created")
            text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise CrisisError("the message text is empty; nothing was created")
    if BAD_CHARS.search(text):
        raise CrisisError("the message text contains control characters")
    return text


def cmd_init(root: Path, args: argparse.Namespace) -> int:
    config = root / "config.toml"
    if config.exists():
        print("config.toml already exists - left unchanged")
    else:
        config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print("created config.toml - edit it before the first build")
    (root / "updates").mkdir(exist_ok=True)
    legal = root / "legal" / "en.txt"
    if not legal.exists():  # only used when legal_notice = true; never overwritten
        legal.parent.mkdir(exist_ok=True)
        legal.write_text(LEGAL_TEMPLATE, encoding="utf-8")
    return 0


def create_update(root: Path, cfg: Config, title: str, text: str) -> Path:
    """Create updates/<ID>/<default>.txt with the current time; returns the new folder."""
    now = datetime.now(cfg.tz).replace(microsecond=0)
    base = now.strftime("%Y%m%d-%H%M%S")
    (root / "updates").mkdir(exist_ok=True)
    folder, n = root / "updates" / base, 0
    while True:
        try:
            folder.mkdir()  # atomic: fails if the ID is taken
            break
        except FileExistsError:
            n += 1
            folder = root / "updates" / f"{base}-{n}"
    (folder / f"{cfg.default_language}.txt").write_text(
        f"title: {title}\ndate: {now.isoformat()}\n\n{text}\n", encoding="utf-8")
    return folder


def cmd_new(root: Path, args: argparse.Namespace) -> int:
    cfg = load_config(root)
    title = check_title(args.title)
    folder = create_update(root, cfg, title, read_text_source(args))
    print(f"created updates/{folder.name}/{cfg.default_language}.txt")
    return 0


def cmd_translate(root: Path, args: argparse.Namespace) -> int:
    cfg = load_config(root)
    if not ID_RE.fullmatch(args.id) or not (root / "updates" / args.id).is_dir():
        raise CrisisError(f"no update with ID {args.id!r} (see 'python3 crisis.py list')")
    if args.lang not in cfg.languages:
        raise CrisisError(f"language {args.lang!r} is not in 'languages' of config.toml")
    if args.lang == cfg.default_language:
        raise CrisisError("that is the default language; create updates with 'new'")
    target = root / "updates" / args.id / f"{args.lang}.txt"
    title, text = check_title(args.title), read_text_source(args)
    try:
        with open(target, "x", encoding="utf-8") as f:  # 'x': never overwrite a published translation
            f.write(f"title: {title}\n\n{text}\n")
    except FileExistsError:
        raise CrisisError(f"{target.relative_to(root)} already exists; "
                          "published updates are not edited (delete the file by hand if you must)") from None
    print(f"created {target.relative_to(root)}")
    return 0


def cmd_build(root: Path, args: argparse.Namespace) -> int:
    return build(root)


def cmd_list(root: Path, args: argparse.Namespace) -> int:
    cfg = load_config(root)
    for u in load_updates(root, cfg):
        title = u.texts[cfg.default_language][0]
        have = ",".join(lang for lang in cfg.languages if lang in u.texts)
        print(f"{u.id}  {u.date.astimezone(cfg.tz):%Y-%m-%d %H:%M:%S %z}  [{have}]  {title}")
        gaps = missing_translations(u, cfg)
        if gaps:
            warn(f"update {u.id} has no translation for: {', '.join(gaps)}")
    return 0


def run_deploy(root: Path, cfg: Config) -> None:
    if not cfg.deploy_command:
        raise CrisisError("no 'deploy_command' in config.toml")
    if not (root / "public").is_dir():
        raise CrisisError("public/ does not exist - run 'python3 crisis.py build' first")
    cmd = shlex.split(cfg.deploy_command)  # no shell, ever
    if not cmd:
        raise CrisisError("'deploy_command' is empty")
    print("deploying: " + shlex.join(cmd))
    try:
        code = subprocess.run(cmd, cwd=root).returncode
    except OSError as e:
        raise CrisisError(f"cannot run deploy command {cmd[0]!r}: {e.strerror}") from None
    if code != 0:
        raise CrisisError(f"deploy command failed with exit status {code}")
    print("deployed")


def cmd_deploy(root: Path, args: argparse.Namespace) -> int:
    run_deploy(root, load_config(root))
    return 0


def confirm(prompt: str) -> bool:
    """Ask on the terminal. No terminal, EOF or Ctrl-C all count as 'no'."""
    if not sys.stdin.isatty():
        return False
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return sys.stdin.readline().strip().lower() in ("y", "yes")
    except KeyboardInterrupt:
        print()
        return False


def cmd_publish(root: Path, args: argparse.Namespace) -> int:
    cfg = load_config(root)
    if not cfg.deploy_command:
        raise CrisisError("publish needs 'deploy_command' in config.toml")
    title = check_title(args.title)
    text = read_text_source(args)  # read first: stdin is consumed before we need a terminal
    render_site(root)  # fail now (bad config/i18n/old update), before anything is created
    folder = create_update(root, cfg, title, text)
    try:
        print(f"--- preview ({cfg.default_language}) ---\n{title}\n\n{text}\n--- end ---")
        if not args.yes and not confirm("Publish this update now? [y/N] "):
            raise CrisisError("not confirmed - the update was discarded")
    except BaseException:
        shutil.rmtree(folder)  # a rejected update must never go online with the next one
        raise
    build(root)
    run_deploy(root, cfg)
    return 0


# --------------------------------------------------------------------------- cli

def add_text_options(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("-m", "--message", help="message text")
    g.add_argument("-f", "--file", help="read the message text from a file")


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crisis.py", description="Static crisis communication page.")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("init", help="create config.toml and updates/ (never overwrites)").set_defaults(run=cmd_init)
    s = sub.add_parser("new", help="create a new update in the default language")
    s.add_argument("title")
    add_text_options(s)
    s.set_defaults(run=cmd_new)
    s = sub.add_parser("translate", help="add a translation to an existing update")
    s.add_argument("id")
    s.add_argument("--lang", required=True)
    s.add_argument("title")
    add_text_options(s)
    s.set_defaults(run=cmd_translate)
    sub.add_parser("build", help="generate public/").set_defaults(run=cmd_build)
    sub.add_parser("list", help="list updates").set_defaults(run=cmd_list)
    sub.add_parser("deploy", help="upload public/ with deploy_command").set_defaults(run=cmd_deploy)
    s = sub.add_parser("publish", help="new + preview + confirm + build + deploy")
    s.add_argument("title")
    add_text_options(s)
    s.add_argument("--yes", action="store_true", help="skip the confirmation question")
    s.set_defaults(run=cmd_publish)
    return p


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    args = make_parser().parse_args(argv)
    try:
        return args.run(root, args)
    except CrisisError as e:
        print(f"error: {e}", file=sys.stderr)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
