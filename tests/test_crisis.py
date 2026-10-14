"""Acceptance tests (briefing section 9). Standard library only: python3 -m unittest

Each test works in a throw-away project directory: a copy of i18n/en.toml plus the
extra languages (de, ar by default) from the shipped i18n/ folder.
"""
import contextlib
import hashlib
import html
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import crisis  # noqa: E402

CONFIG = """\
organization  = "Example Ltd"
contact_email = "crisis-comms@example.com"
site_url      = "https://status.example.com"
timezone      = "Europe/Berlin"
languages     = ["en", "de"]
rss           = true
official_links = ["https://www.example.com/imprint"]
"""
EN = "title: Limited availability\ndate: 2026-10-12T23:41:19+02:00\n\nWe are investigating.\n"
DE = "title: Eingeschränkte Erreichbarkeit\n\nWir untersuchen.\n"


def toml(value):
    """A TOML basic string (JSON string syntax is a subset)."""
    return json.dumps(value, ensure_ascii=False)


class FakeStdin(io.StringIO):
    def __init__(self, text="", tty=False):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


class Project:
    """A scratch project directory with helpers to run the CLI in-process."""

    def __init__(self, config=CONFIG, langs=("de", "ar")):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "i18n").mkdir()
        shutil.copy(REPO / "i18n" / "en.toml", self.root / "i18n")
        for lang in langs:
            shutil.copy(REPO / "i18n" / f"{lang}.toml", self.root / "i18n")
        if config is not None:
            (self.root / "config.toml").write_text(config, encoding="utf-8")

    def close(self):
        self.tmp.cleanup()

    def run(self, *argv, stdin="", tty=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, "stdin", FakeStdin(stdin, tty)):
            try:
                code = crisis.main(list(argv), root=self.root)
            except SystemExit as e:  # argparse usage errors
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def add_update(self, uid="20261012-234119", en=EN, **others):
        d = self.root / "updates" / uid
        d.mkdir(parents=True, exist_ok=True)
        (d / "en.txt").write_text(en, encoding="utf-8")
        for lang, text in others.items():
            (d / f"{lang}.txt").write_text(text, encoding="utf-8")
        return d

    def build(self):
        code, out, err = self.run("build")
        assert code == 0, err
        return self.root / "public"

    def page(self, rel="index.html"):
        return (self.root / "public" / rel).read_text(encoding="utf-8")

    def snapshot(self):
        public = self.root / "public"
        return {p.relative_to(public).as_posix(): p.read_bytes() for p in sorted(public.rglob("*")) if p.is_file()}


class Case(unittest.TestCase):
    def project(self, *a, **kw):
        p = Project(*a, **kw)
        self.addCleanup(p.close)
        return p

    def fails(self, config, *needles):
        p = self.project(config=config)
        code, out, err = p.run("build")
        self.assertEqual(code, 1, err)
        self.assertTrue(err.startswith("error:"), err)
        self.assertNotIn("Traceback", err)
        for n in needles:
            self.assertIn(n, err)
        self.assertFalse((p.root / "public").exists())


class Collector(HTMLParser):
    """Collects tags/attributes and checks that elements nest correctly."""
    VOID = {"meta", "link", "br", "hr", "img", "input"}

    def __init__(self):
        super().__init__()
        self.tags, self.stack, self.errors = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"unbalanced </{tag}>")

    def anchors(self):
        return [a["href"] for t, a in self.tags if t == "a"]


def parse(markup):
    c = Collector()
    c.feed(markup)
    c.close()
    assert not c.errors and not c.stack, (c.errors, c.stack)
    return c


class InitTests(Case):
    def test_init_creates_template_and_never_overwrites(self):
        p = self.project(config=None)
        code, out, _ = p.run("init")
        self.assertEqual(code, 0)
        self.assertTrue((p.root / "updates").is_dir())
        config = (p.root / "config.toml")
        self.assertIn("contact_email", config.read_text())
        config.write_text('organization = "Mine"\n')  # user edits
        code, out, _ = p.run("init")
        self.assertEqual(code, 0)
        self.assertEqual(config.read_text(), 'organization = "Mine"\n')
        self.assertIn("unchanged", out)

    def test_init_creates_legal_template_without_overwriting_it(self):
        p = self.project(config=None)
        p.run("init")
        legal = p.root / "legal" / "en.txt"
        self.assertIn(crisis.LEGAL_PLACEHOLDER, legal.read_text())
        legal.write_text("My imprint\n")
        p.run("init")
        self.assertEqual(legal.read_text(), "My imprint\n")

    def test_fresh_template_builds(self):
        p = self.project(config=None)
        p.run("init")
        p.build()
        self.assertIn("There are currently no updates.", p.page())


class ConfigTests(Case):
    def test_missing_email(self):
        self.fails(CONFIG.replace('contact_email = "crisis-comms@example.com"', ""), "contact_email")

    def test_bad_email(self):
        self.fails(CONFIG.replace("crisis-comms@example.com", "not-an-address"), "e-mail")

    def test_missing_organization(self):
        self.fails(CONFIG.replace('organization  = "Example Ltd"', ""), "organization")

    def test_bad_timezone(self):
        self.fails(CONFIG.replace("Europe/Berlin", "Mars/Olympus"), "timezone")

    def test_unknown_language(self):
        self.fails(CONFIG.replace('["en", "de"]', '["en", "xx"]'), "unknown language 'xx'")

    def test_rss_without_site_url(self):
        self.fails(CONFIG.replace('site_url      = "https://status.example.com"', ""), "site_url", "rss")

    def test_site_url_trailing_slash(self):
        self.fails(CONFIG.replace("example.com\"\ntimezone", "example.com/\"\ntimezone"), "slash")

    def test_default_language_not_listed(self):
        self.fails(CONFIG.replace('["en", "de"]', '["en"]') + 'default_language = "de"\n', "default_language")

    def test_bad_link(self):
        self.fails(CONFIG.replace("https://www.example.com/imprint", "javascript:alert(1)"), "official_links")

    def test_unknown_setting_and_bad_toml(self):
        self.fails(CONFIG + "rss_enabled = true\n", "unknown setting 'rss_enabled'")
        self.fails("this is = not [ toml", "not valid TOML")

    def test_missing_config(self):
        p = self.project(config=None)
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("init", err)

    def test_missing_i18n_key_aborts_build(self):
        p = self.project()
        path = p.root / "i18n" / "de.toml"
        path.write_text("".join(l for l in path.read_text().splitlines(True) if not l.startswith("no_updates")))
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("no_updates", err)

    def test_i18n_bad_placeholder_and_missing_file(self):
        p = self.project()
        path = p.root / "i18n" / "de.toml"
        path.write_text(path.read_text().replace("{org}", "{org.__class__}", 1))
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("placeholder", err)


class LanguageTests(Case):
    def test_layout_switcher_and_hreflang(self):
        p = self.project(CONFIG.replace('["en", "de"]', '["en", "de", "ar"]'))
        p.add_update(de=DE)
        p.build()
        self.assertEqual(sorted(p.snapshot()), ["SHA256SUMS.txt", "ar/feed.xml", "ar/index.html", "de/feed.xml",
                                                "de/index.html", "favicon.svg", "feed.xml", "index.html", "robots.txt",
                                                "style.css"])
        root, de = p.page(), p.page("de/index.html")
        self.assertIn('<html lang="en" dir="ltr">', root)
        self.assertIn('<html lang="de" dir="ltr">', de)
        self.assertIn('<html lang="ar" dir="rtl">', p.page("ar/index.html"))
        self.assertIn('<link rel="stylesheet" href="style.css">', root)
        self.assertIn('<link rel="stylesheet" href="../style.css">', de)
        self.assertIn('<p class="lang"><strong lang="en">English</strong> | '
                      '<a href="de/index.html" lang="de" hreflang="de">Deutsch</a> | '
                      '<a href="ar/index.html" lang="ar" hreflang="ar">العربية</a></p>', root)
        self.assertIn('<p class="lang"><a href="../index.html" lang="en" hreflang="en">English</a> | '
                      '<strong lang="de">Deutsch</strong> | '
                      '<a href="../ar/index.html" lang="ar" hreflang="ar">العربية</a></p>', de)
        for page, prefix in ((root, ""), (de, "../")):
            self.assertIn(f'<link rel="alternate" hreflang="en" href="{prefix}index.html">', page)
            self.assertIn(f'<link rel="alternate" hreflang="de" href="{prefix}de/index.html">', page)
            self.assertIn(f'<link rel="alternate" hreflang="x-default" href="{prefix}index.html">', page)

    def test_no_switcher_for_single_language(self):
        p = self.project(CONFIG.replace('["en", "de"]', '["en"]'))
        p.build()
        self.assertNotIn('class="lang"', p.page())
        self.assertNotIn("hreflang", p.page())

    def test_translation_used_when_present(self):
        p = self.project()
        p.add_update(de=DE)
        p.build()
        de = p.page("de/index.html")
        self.assertIn("<h2>Eingeschränkte Erreichbarkeit</h2>", de)
        self.assertIn("12. Oktober 2026, 23:41 Uhr (UTC+02:00)", de)
        self.assertNotIn("nicht auf Deutsch", de)
        self.assertIn("12 Oct 2026, 23:41 (UTC+02:00)", p.page())

    def test_missing_translation_falls_back_to_english_with_notice(self):
        p = self.project()
        p.add_update()
        p.build()
        de = p.page("de/index.html")
        self.assertIn('<article id="u-20261012-234119" lang="en" dir="ltr">', de)
        self.assertIn("<h2>Limited availability</h2>", de)
        self.assertIn('lang="de" dir="ltr">Diese Meldung liegt noch nicht auf Deutsch vor.</p>', de)
        feed = ET.fromstring(p.page("de/feed.xml"))
        desc = feed.find("channel/item/description").text
        self.assertIn("Diese Meldung liegt noch nicht auf Deutsch vor.", desc)
        self.assertEqual(feed.find("channel/item/title").text, "Limited availability")

    def test_missing_translation_is_warning_not_error(self):
        p = self.project()
        p.add_update()
        code, out, err = p.run("build")
        self.assertEqual(code, 0)
        self.assertIn("warning:", err)
        self.assertIn("de", err)
        code, out, err = p.run("list")
        self.assertEqual(code, 0)
        self.assertIn("warning:", err)
        self.assertIn("20261012-234119", out)

    def test_translation_for_unconfigured_language_is_ignored(self):
        p = self.project(CONFIG.replace('["en", "de"]', '["en"]'))
        p.add_update(de=DE)
        p.build()
        self.assertEqual(sorted(p.snapshot()), ["SHA256SUMS.txt", "favicon.svg", "feed.xml", "index.html", "robots.txt", "style.css"])

    def test_rtl_article_keeps_ltr_fallback(self):
        p = self.project(CONFIG.replace('["en", "de"]', '["en", "ar"]'))
        p.add_update()
        p.build()
        ar = p.page("ar/index.html")
        self.assertIn('<html lang="ar" dir="rtl">', ar)
        self.assertIn('<article id="u-20261012-234119" lang="en" dir="ltr">', ar)

    def test_default_language_required_in_update(self):
        p = self.project()
        d = p.root / "updates" / "20261012-234119"
        d.mkdir(parents=True)
        (d / "de.txt").write_text(DE)
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("en.txt", err)


class SafetyTests(Case):
    def test_html_is_escaped_everywhere(self):
        p = self.project()
        p.add_update(en='title: <script>alert(1)</script>\ndate: 2026-10-12T23:41:19+02:00\n\n'
                        '<img src=x onerror=alert(1)> & "quotes"\n')
        p.build()
        for rel in ("index.html", "de/index.html"):
            page = p.page(rel)
            self.assertNotIn("<script", page)
            self.assertNotIn("<img", page)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
            self.assertIn("&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quotes&quot;", page)
        feed = ET.fromstring(p.page("feed.xml"))
        self.assertEqual(feed.find("channel/item/title").text, "<script>alert(1)</script>")

    def test_config_values_are_escaped(self):
        p = self.project(CONFIG.replace('"Example Ltd"', toml('A&B <i>"Ltd"</i>')))
        p.build()
        page = p.page()
        self.assertNotIn("<i>", page)
        self.assertIn("A&amp;B &lt;i&gt;&quot;Ltd&quot;&lt;/i&gt;", page)
        ET.fromstring(p.page("feed.xml"))

    def test_only_http_urls_are_linked(self):
        p = self.project()
        p.add_update(en="title: Links\ndate: 2026-10-12T23:41:19+02:00\n\n"
                        "javascript:alert(1) and JAVASCRIPT:alert(2) and data:text/html,x\n"
                        "and ftp://example.com/x and https:// alone and mailto:a@b.cc\n")
        p.build()
        self.assertEqual(parse(p.page()).anchors(),
                         ["de/index.html", "mailto:crisis-comms@example.com", "feed.xml", "#u-20261012-234119",
                          "https://www.example.com/imprint"])  # nothing from the message text
        self.assertNotIn('href="javascript', p.page().lower())

    def test_url_linking_details(self):
        p = self.project()
        p.add_update(en="title: Links\ndate: 2026-10-12T23:41:19+02:00\n\n"
                        'See https://example.com/a?x=1&y=2, and (https://example.com/b), or https://example.com/c.\n'
                        'Quote "https://example.com/d?q=\'x\'" end! http://example.com/(e) <https://example.com/f>\n')
        p.build()
        parsed = parse(p.page())
        links = [h for h in parsed.anchors() if h.startswith("http")]
        self.assertEqual(links, [
            "https://example.com/a?x=1&y=2", "https://example.com/b", "https://example.com/c",
            "https://example.com/d?q='x", "http://example.com/(e)", "https://example.com/f",
            "https://www.example.com/imprint"])
        self.assertIn("x=1&amp;y=2", p.page())  # & escaped in the markup itself
        feed = ET.fromstring(p.page("feed.xml"))  # still well-formed XML; description holds escaped HTML
        self.assertIn('href="https://example.com/a?x=1&amp;y=2"', feed.find("channel/item/description").text)

    def test_paragraphs_and_line_breaks(self):
        self.assertEqual(crisis.render_text("a\nb\n\n\nc"), "<p>a<br>\nb</p>\n<p>c</p>")

    def test_control_characters_rejected(self):
        p = self.project()
        p.add_update(en="title: Bad\ndate: 2026-10-12T23:41:19+02:00\n\nhello\x08world\n")
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("control characters", err)

    def test_bad_update_files(self):
        for text, needle in (("title: x\n\nbody\n", "date"),
                             ("title: x\ndate: 2026-10-12T23:41:19\n\nbody\n", "UTC offset"),
                             ("date: 2026-10-12T23:41:19+02:00\n\nbody\n", "title"),
                             ("title x\n\nbody\n", "header line")):
            p = self.project()
            p.add_update(en=text)
            code, _, err = p.run("build")
            self.assertEqual(code, 1, text)
            self.assertIn(needle, err)

    def test_no_script_inline_style_or_external_resources(self):
        p = self.project(CONFIG.replace('["en", "de"]', '["en", "de", "ar"]'))
        p.add_update(en="title: T\ndate: 2026-10-12T23:41:19+02:00\n\nsee https://example.com/x\n", de=DE)
        public = p.build()
        for path in public.rglob("*.html"):
            markup = path.read_text(encoding="utf-8")
            self.assertNotIn("<script", markup.lower())
            self.assertNotIn("<style", markup.lower())
            for tag, attrs in parse(markup).tags:
                self.assertNotIn("style", attrs)
                self.assertFalse([a for a in attrs if a.startswith("on")], (path, tag))
                self.assertNotIn(tag, {"script", "img", "iframe", "object", "embed", "source", "video", "audio"})
                if tag == "link":
                    self.assertFalse(attrs["href"].startswith(("http:", "https:", "//")), attrs)
        css = (public / "style.css").read_text()
        self.assertLess(len(css.encode()), 2048)
        for forbidden in ("url(", "@import", "@font-face"):
            self.assertNotIn(forbidden, css)

    def test_semantic_structure(self):
        p = self.project()
        p.add_update(de=DE)
        p.build()
        tags = [t for t, _ in parse(p.page()).tags]
        for needed in ("header", "main", "article", "time", "footer", "hr", "h1", "h2"):
            self.assertIn(needed, tags)
        page = p.page()
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', page)
        self.assertIn("This page uses no scripts, cookies or trackers.", page)
        self.assertIn('href="mailto:crisis-comms@example.com"', page)
        self.assertIn("https://www.example.com/imprint", page)
        self.assertIn('<article id="u-20261012-234119">', page)


def luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class DesignTests(Case):
    def test_css_is_classic_and_small(self):
        css = crisis.CSS
        self.assertLess(len(css.encode()), 2048)
        for needed in ('Georgia,"Times New Roman",Times,serif', "#0000EE", "#551A8B", "text-decoration:underline",
                       "max-width:760px"):
            self.assertIn(needed, css)
        for forbidden in ("border-radius", "box-shadow", "text-shadow", "gradient", "animation", "transition",
                          "prefers-color-scheme", "url(", "@import", "@font-face", "!important"):
            self.assertNotIn(forbidden, css)

    def test_text_contrast_meets_wcag_aa(self):
        # every text/background pair used in style.css (links on the tinted boxes included)
        pairs = [("#000000", "#FFFFFF"), ("#000000", "#EEF0F4"), ("#000000", "#C9D3E0"), ("#FFFFFF", "#336699"),
                 ("#0000EE", "#FFFFFF"), ("#0000EE", "#EEF0F4"), ("#0000EE", "#C9D3E0"),
                 ("#551A8B", "#FFFFFF"), ("#551A8B", "#EEF0F4"), ("#551A8B", "#C9D3E0")]
        for fg, bg in pairs:
            self.assertGreaterEqual(contrast(fg, bg), 4.5, (fg, bg))
        for color in ("#EEF0F4", "#C9D3E0", "#336699"):  # the pairs above cover every colour in the file
            self.assertIn(color, crisis.CSS)


class IndexingTests(Case):
    def test_robots_txt_and_noindex_everywhere(self):
        p = self.project()
        p.add_update(de=DE)
        public = p.build()
        self.assertEqual((public / "robots.txt").read_text(), "User-agent: *\nDisallow: /\n")
        self.assertFalse((public / "de" / "robots.txt").exists())
        for path in public.rglob("*.html"):
            self.assertIn('<meta name="robots" content="noindex">', path.read_text(encoding="utf-8"), path)


class ChecksumTests(Case):
    @staticmethod
    def entries(text):
        return [tuple(line.split("  ", 1)) for line in text.splitlines()]  # (hash, path)

    def test_manifest_matches_every_file_and_is_sorted(self):
        p = self.project()
        p.add_update(de=DE)
        public = p.build()
        text = p.page("SHA256SUMS.txt")
        entries = self.entries(text)
        paths = [path for _, path in entries]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(paths, sorted(f.relative_to(public).as_posix() for f in public.rglob("*")
                                       if f.is_file() and f.name != "SHA256SUMS.txt"))
        for digest, path in entries:
            self.assertEqual(digest, hashlib.sha256((public / path).read_bytes()).hexdigest(), path)
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_manifest_follows_content(self):
        p = self.project()
        p.build()
        before = p.page("SHA256SUMS.txt")
        p.add_update()
        p.build()
        after = p.page("SHA256SUMS.txt")
        self.assertNotEqual(before, after)
        old = {path: digest for digest, path in self.entries(before)}
        new = {path: digest for digest, path in self.entries(after)}
        self.assertEqual(old["style.css"], new["style.css"])  # untouched file keeps its hash
        self.assertNotEqual(old["index.html"], new["index.html"])
        (p.root / "config.toml").write_text(CONFIG.replace("rss           = true", "rss = false"))
        p.build()
        self.assertNotIn("feed.xml", p.page("SHA256SUMS.txt"))  # removed files leave the manifest

    def test_build_prints_hash_of_manifest(self):
        p = self.project()
        code, out, _ = p.run("build")
        digest = hashlib.sha256((p.root / "public" / "SHA256SUMS.txt").read_bytes()).hexdigest()
        self.assertEqual(code, 0)
        self.assertIn(f"build hash (sha256 of SHA256SUMS.txt): {digest}", out)

    @unittest.skipUnless(shutil.which("sha256sum"), "sha256sum not installed")
    def test_sha256sum_accepts_the_manifest(self):
        p = self.project()
        p.add_update(de=DE)
        public = p.build()
        ok = subprocess.run(["sha256sum", "-c", "SHA256SUMS.txt"], cwd=public, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        (public / "index.html").write_text("tampered")
        bad = subprocess.run(["sha256sum", "-c", "SHA256SUMS.txt"], cwd=public, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # enough for the content check; not a drawable image
SVG_OK = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
          '<style>.a{fill:#336699}</style><defs><rect id="r" width="4" height="4" class="a"/></defs>'
          '<use xlink:href="#r"/><image href="data:image/png;base64,AAAA"/></svg>')


class I18nFilesTests(Case):
    def test_every_shipped_language_file_is_complete_and_valid(self):
        files = sorted((REPO / "i18n").glob("*.toml"))
        self.assertGreaterEqual(len(files), 20)
        reference = tomllib.loads((REPO / "i18n" / "en.toml").read_text(encoding="utf-8"))
        names, rtl = set(), set()
        for path in files:
            code = path.stem
            with self.subTest(code):
                self.assertTrue(crisis.LANG_RE.fullmatch(code))
                tr = crisis.load_i18n(REPO, code)  # checks keys, placeholders, months, dir
                self.assertEqual(set(tr), set(reference), "same keys as en.toml")
                self.assertNotIn(tr["name"], names)
                names.add(tr["name"])
                if tr["dir"] == "rtl":
                    rtl.add(code)
                self.assertEqual(len(set(tr["months"])), 12)
                first_line = path.read_text(encoding="utf-8").splitlines()[0]
                if code not in ("en", "de"):
                    self.assertIn("DRAFT", first_line)  # machine-drafted: must say so until a native speaker reviews
                date = crisis.format_date(datetime(2026, 10, 12, 23, 41, tzinfo=timezone.utc), tr, timezone.utc)
                self.assertNotIn("{", date)
                for part in ("2026", "23", "41", "UTC+00:00"):
                    self.assertIn(part, date)
                self.assertEqual(len(tr["credits"].split("{link}")), 2)
        self.assertEqual(rtl, {"ar", "he"})

    def test_all_languages_build_together(self):
        codes = sorted(path.stem for path in (REPO / "i18n").glob("*.toml"))
        listed = ", ".join(f'"{c}"' for c in codes)
        p = self.project(CONFIG.replace('["en", "de"]', f"[{listed}]") + "legal_notice = true\n"
                         'credits_link = "https://github.com/dycept/crisispage"\n',
                         langs=[c for c in codes if c != "en"])
        p.add_update()
        (p.root / "legal").mkdir()
        (p.root / "legal" / "en.txt").write_text("Example Ltd\nExample Street 1\n")
        code, out, err = p.run("build")
        self.assertEqual(code, 0, err)
        for lang in codes:
            prefix = "" if lang == "en" else f"{lang}/"
            tr = crisis.load_i18n(REPO, lang)
            page = p.page(prefix + "index.html")
            self.assertIn(f'<html lang="{lang}" dir="{tr["dir"]}">', page)
            self.assertIn(f"<strong lang=\"{lang}\">{tr['name']}</strong>", page)
            self.assertIn(html.escape(tr["not_translated"]) if lang != "en" else "Limited availability", page)
            self.assertIn(tr["legal"], p.page(prefix + "legal.html"))
            before, after = tr["credits"].split("{link}")
            self.assertIn(f'{before}<a href="https://github.com/dycept/crisispage">crisispage</a>{after}', page)
        parse(p.page("de/index.html"))  # still well-formed with 27 languages in the switcher


class OptionalContactTests(Case):
    def test_phone_is_off_by_default(self):
        p = self.project()
        p.build()
        self.assertNotIn("tel:", p.page())
        self.assertNotIn("Phone", p.page())

    def test_phone_row_with_tel_link(self):
        p = self.project(CONFIG + 'contact_phone = "+49 (0)30 1234-567"\n')
        p.build()
        self.assertIn('<tr><th scope="row">Phone</th><td><a href="tel:+49301234567" dir="ltr">'
                      '+49 (0)30 1234-567</a></td></tr>', p.page())
        self.assertIn('<th scope="row">Telefon</th>', p.page("de/index.html"))
        parse(p.page())

    def test_bad_phone_is_rejected(self):
        self.fails(CONFIG + 'contact_phone = "call us <b>now</b>"\n', "contact_phone")
        self.fails(CONFIG + "contact_phone = 12345\n", "contact_phone")


class CreditsTests(Case):
    URL = "https://github.com/dycept/crisispage"

    def test_credits_are_off_by_default(self):
        p = self.project()
        p.build()
        for rel in ("index.html", "de/index.html"):
            self.assertNotIn("crisispage", p.page(rel))
            self.assertNotIn("Powered by", p.page(rel))

    def test_credits_link_in_footer_in_page_language(self):
        p = self.project(CONFIG + f'credits_link = "{self.URL}"\n')
        p.build()
        self.assertIn(f'<p>Powered by <a href="{self.URL}">crisispage</a></p>', p.page())
        self.assertIn(f'<p>Erstellt mit <a href="{self.URL}">crisispage</a></p>', p.page("de/index.html"))

    def test_credits_link_is_validated_and_escaped(self):
        self.fails(CONFIG + 'credits_link = "javascript:alert(1)"\n', "credits_link")
        p = self.project(CONFIG + 'credits_link = "https://example.com/?a=1&b=2"\n')
        p.build()
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', p.page())


class LegalNoticeTests(Case):
    def legal_project(self, **texts):
        p = self.project(CONFIG + "legal_notice = true\n")
        (p.root / "legal").mkdir()
        for lang, text in texts.items():
            (p.root / "legal" / f"{lang}.txt").write_text(text, encoding="utf-8")
        return p

    def test_off_by_default(self):
        p = self.project()
        p.build()
        self.assertNotIn("legal.html", p.snapshot())
        self.assertNotIn("legal.html", p.page())
        self.assertNotIn("Legal notice", p.page())

    def test_pages_footer_links_and_language_fallback(self):
        p = self.legal_project(en="Example Ltd\nExample Street 1\n\nSee <https://example.com/privacy>\n",
                               de="Beispiel GmbH\nBeispielstraße 1\n")
        p.add_update()
        p.build()
        for rel, label in (("index.html", "Legal notice / Imprint"), ("de/index.html", "Impressum / Rechtliche Hinweise")):
            self.assertIn(f'<a href="legal.html">{label}</a>', p.page(rel))
        en, de = p.page("legal.html"), p.page("de/legal.html")
        self.assertIn("<h2>Legal notice / Imprint</h2>", en)
        self.assertIn('<a href="https://example.com/privacy">https://example.com/privacy</a>', en)
        self.assertIn("Example Ltd<br>\nExample Street 1", en)
        self.assertIn('<a href="index.html">Back to the updates</a>', en)
        self.assertIn('<a href="index.html">Zurück zu den Meldungen</a>', de)  # de/legal.html -> de/index.html
        self.assertIn("Beispielstraße 1", de)
        self.assertNotIn("noch nicht auf Deutsch", de)
        # language line and hreflang point at the legal pages
        self.assertIn('<a href="de/legal.html" lang="de" hreflang="de">Deutsch</a>', en)
        self.assertIn('<link rel="alternate" hreflang="x-default" href="../legal.html">', de)
        self.assertIn("noindex", en)
        parse(en), parse(de)

    def test_missing_translation_falls_back_to_default_language_text(self):
        p = self.legal_project(en="Example Ltd, Example Street 1\n")
        p.build()
        de = p.page("de/legal.html")
        self.assertIn('<article id="legal" lang="en" dir="ltr">', de)
        self.assertIn("Diese rechtlichen Hinweise liegen noch nicht auf Deutsch vor.", de)
        self.assertIn("Example Street 1", de)

    def test_text_is_escaped(self):
        p = self.legal_project(en="<script>alert(1)</script> & Co\n")
        p.build()
        self.assertNotIn("<script", p.page("legal.html"))
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; Co", p.page("legal.html"))

    def test_default_language_text_is_required(self):
        p = self.legal_project(de="Nur Deutsch\n")
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn("legal/en.txt is missing", err)
        self.assertFalse((p.root / "public").exists())

    def test_placeholder_from_init_is_never_published(self):
        p = self.legal_project()
        (p.root / "legal" / "en.txt").write_text(crisis.LEGAL_TEMPLATE)
        code, _, err = p.run("build")
        self.assertEqual(code, 1)
        self.assertIn(crisis.LEGAL_PLACEHOLDER, err)

    def test_empty_or_bad_files_are_rejected(self):
        for text, needle in (("  \n\n", "empty"), ("x\x08y", "control characters")):
            p = self.legal_project(en=text)
            code, _, err = p.run("build")
            self.assertEqual(code, 1, text)
            self.assertIn(needle, err)

    def test_legal_pages_are_part_of_the_manifest_and_removed_when_disabled(self):
        p = self.legal_project(en="Example Ltd\n")
        p.build()
        manifest = p.page("SHA256SUMS.txt")
        self.assertIn("  legal.html\n", manifest)
        self.assertIn("  de/legal.html\n", manifest)
        (p.root / "config.toml").write_text(CONFIG)
        p.build()
        self.assertNotIn("legal.html", p.page("SHA256SUMS.txt"))
        self.assertFalse((p.root / "public" / "legal.html").exists())
        self.assertFalse((p.root / "public" / "de" / "legal.html").exists())

    def test_validates_as_html_structure_without_updates(self):
        p = self.legal_project(en="Only text\n")
        p.build()
        tags = [t for t, _ in parse(p.page("legal.html")).tags]
        for needed in ("header", "main", "article", "footer", "h1", "h2"):
            self.assertIn(needed, tags)


class ImageTests(Case):
    def asset(self, p, name, data):
        path = p.root / "assets" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())

    def test_default_favicon_is_built_in(self):
        p = self.project()
        public = p.build()
        self.assertEqual((public / "favicon.svg").read_text(), crisis.DEFAULT_FAVICON)
        ET.fromstring(crisis.DEFAULT_FAVICON)  # well-formed XML
        self.assertIn('<link rel="icon" href="favicon.svg" type="image/svg+xml">', p.page())
        self.assertIn('<link rel="icon" href="../favicon.svg" type="image/svg+xml">', p.page("de/index.html"))
        self.assertNotIn("<img", p.page())  # no logo unless configured
        self.assertNotIn("logo", p.page())

    def test_favicon_can_be_switched_off(self):
        p = self.project(CONFIG + "favicon = false\n")
        p.build()
        self.assertNotIn("favicon.svg", p.snapshot())
        self.assertNotIn('rel="icon"', p.page())

    def test_favicon_can_be_replaced_and_old_one_is_removed(self):
        p = self.project()
        p.build()
        self.assertIn("favicon.svg", p.snapshot())
        self.asset(p, "favicon.png", PNG)
        (p.root / "config.toml").write_text(CONFIG + 'favicon = "assets/favicon.png"\n')
        p.build()
        self.assertEqual((p.root / "public" / "favicon.png").read_bytes(), PNG)
        self.assertNotIn("favicon.svg", p.snapshot())
        self.assertIn('<link rel="icon" href="favicon.png" type="image/png">', p.page())
        self.asset(p, "my.ico", b"\x00\x00\x01\x00" + b"\x00" * 8)
        (p.root / "config.toml").write_text(CONFIG + 'favicon = "assets/my.ico"\n')
        p.build()
        self.assertIn('href="favicon.ico" type="image/x-icon"', p.page())

    def test_logo_is_shown_on_every_page(self):
        p = self.project(CONFIG + 'logo = "assets/logo.png"\nlegal_notice = true\n')
        self.asset(p, "logo.png", PNG)
        (p.root / "legal").mkdir()
        (p.root / "legal" / "en.txt").write_text("Example Ltd\n")
        public = p.build()
        self.assertEqual((public / "logo.png").read_bytes(), PNG)
        self.assertIn('<p class="logo"><img src="logo.png" alt="Example Ltd"></p>', p.page())
        self.assertIn('<p class="logo"><img src="../logo.png" alt="Example Ltd"></p>', p.page("de/index.html"))
        self.assertIn('<img src="logo.png" alt="Example Ltd">', p.page("legal.html"))
        page = p.page()
        self.assertLess(page.index('class="logo"'), page.index("<h1>"))
        parse(page)
        digest = hashlib.sha256(PNG).hexdigest()
        self.assertIn(f"{digest}  logo.png\n", p.page("SHA256SUMS.txt"))

    def test_logo_alt_text(self):
        p = self.project(CONFIG + 'logo = "assets/logo.jpeg"\nlogo_alt = "A&B \\"Ltd\\" logo"\n')
        self.asset(p, "logo.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        p.build()
        self.assertIn('<img src="logo.jpg" alt="A&amp;B &quot;Ltd&quot; logo">', p.page())  # .jpeg -> .jpg
        (p.root / "config.toml").write_text(CONFIG + 'logo = "assets/logo.jpeg"\nlogo_alt = ""\n')
        p.build()
        self.assertIn('<img src="logo.jpg" alt="">', p.page())  # empty alt = decorative

    def test_unusable_images_are_rejected_with_a_clear_message(self):
        big = PNG + b"\x00" * (crisis.MAX_ASSET_BYTES + 1)
        cases = [
            ('favicon = "assets/missing.png"', None, "missing.png"),
            ('logo = "../outside.png"', None, "inside the project"),
            ('logo = "assets/logo.txt"', ("logo.txt", b"hello"), "must be one of"),
            ('logo = "assets/logo.png"', ("logo.png", b"not a png"), "not a valid .png"),
            ('logo = "assets/logo.webp"', ("logo.webp", b"RIFF0000WAVE"), "not a valid .webp"),
            ('logo = "assets/logo.png"', ("logo.png", big), "larger than"),
            ('favicon = "assets/f.gif"', ("f.gif", b"GIF89a"), "must be one of"),  # gif is a logo format only
            ("favicon = 5", None, "favicon"),
        ]
        for line, asset, needle in cases:
            p = self.project(CONFIG + line + "\n")
            if asset:
                self.asset(p, *asset)
            code, _, err = p.run("build")
            self.assertEqual(code, 1, line)
            self.assertIn(needle, err)
            self.assertNotIn("Traceback", err)
            self.assertFalse((p.root / "public").exists(), line)

    def test_svg_with_active_content_is_rejected(self):
        evil = {
            "script": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "event handler": '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            "javascript link": '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
            "external image": '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
            "css import": '<svg xmlns="http://www.w3.org/2000/svg"><style>@import "https://evil.example/a.css";</style></svg>',
            "css url": '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://evil.example/p)"/></svg>',
            "foreignObject": '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p/></foreignObject></svg>',
            "animation": '<svg xmlns="http://www.w3.org/2000/svg"><rect><animate attributeName="x" to="1"/></rect></svg>',
            "entity": '<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
            "not xml": "<svg",
        }
        for name, svg in evil.items():
            p = self.project(CONFIG + 'logo = "assets/logo.svg"\n')
            self.asset(p, "logo.svg", svg)
            code, _, err = p.run("build")
            self.assertEqual(code, 1, name)
            self.assertIn("logo", err, name)
            self.assertFalse((p.root / "public").exists(), name)

    def test_plain_svg_is_accepted_for_logo_and_favicon(self):
        p = self.project(CONFIG + 'logo = "assets/logo.svg"\nfavicon = "assets/fav.svg"\n')
        self.asset(p, "logo.svg", SVG_OK)
        self.asset(p, "fav.svg", SVG_OK)
        public = p.build()
        self.assertEqual((public / "logo.svg").read_text(), SVG_OK)
        self.assertEqual((public / "favicon.svg").read_text(), SVG_OK)

    def test_images_do_not_change_the_text_only_guarantees(self):
        p = self.project(CONFIG + 'logo = "assets/logo.png"\n')
        self.asset(p, "logo.png", PNG)
        p.build()
        for path in (p.root / "public").rglob("*.html"):
            markup = path.read_text(encoding="utf-8")
            self.assertNotIn("<script", markup.lower())
            for tag, attrs in parse(markup).tags:
                self.assertNotIn("style", attrs)
                if tag == "img":
                    self.assertFalse(attrs["src"].startswith(("http:", "https:", "//", "data:")), attrs)


class TemplateTests(Case):
    OPTIONAL = ("contact_phone", "legal_notice", "credits_link", "favicon", "logo", "logo_alt", "deploy_command")

    def test_optional_settings_are_commented_out_in_the_template(self):
        raw = tomllib.loads(crisis.CONFIG_TEMPLATE)
        for key in self.OPTIONAL:
            self.assertNotIn(key, raw)
            self.assertRegex(crisis.CONFIG_TEMPLATE, rf"(?m)^# {key} = ")  # documented, ready to uncomment

    def test_uncommenting_the_optional_settings_gives_a_working_site(self):
        text = crisis.CONFIG_TEMPLATE
        for key in self.OPTIONAL:
            text = re.sub(rf"(?m)^# ({key} = )", r"\1", text, count=1)  # first line only (favicon has two)
        raw = tomllib.loads(text)
        for key in self.OPTIONAL:
            self.assertIn(key, raw)
        p = self.project(text, langs=())
        (p.root / "assets").mkdir()
        (p.root / "assets" / "favicon.svg").write_text(SVG_OK)
        (p.root / "assets" / "logo.png").write_bytes(PNG)
        (p.root / "legal").mkdir()
        (p.root / "legal" / "en.txt").write_text("Example Ltd\n")
        page = p.build() and p.page()
        for needle in ("tel:+49301234567", 'href="legal.html"', "Powered by", 'src="logo.png"', 'href="favicon.svg"'):
            self.assertIn(needle, page)


class RssTests(Case):
    def test_feed_is_valid_xml_with_expected_content(self):
        p = self.project()
        p.add_update(de=DE)
        p.add_update("20261013-080000", en="title: Second & last\ndate: 2026-10-13T08:00:00+02:00\n\nDone.\n")
        p.build()
        feed = ET.fromstring(p.page("de/feed.xml").encode())
        self.assertEqual(feed.tag, "rss")
        self.assertEqual(feed.get("version"), "2.0")
        titles = [i.text for i in feed.findall("channel/item/title")]
        self.assertEqual(titles, ["Second & last", "Eingeschränkte Erreichbarkeit"])
        self.assertEqual(feed.find("channel/language").text, "de")
        self.assertEqual(feed.find("channel/lastBuildDate").text, "Tue, 13 Oct 2026 08:00:00 +0200")
        self.assertEqual(feed.find("channel/item/link").text, "https://status.example.com/de/#u-20261013-080000")
        self.assertEqual(ET.fromstring(p.page("feed.xml").encode()).find("channel/link").text,
                         "https://status.example.com/")

    def test_rss_off_has_no_feed_and_no_references(self):
        p = self.project(CONFIG.replace("rss           = true", "rss           = false"))
        p.add_update(de=DE)
        public = p.build()
        self.assertEqual(list(public.rglob("feed.xml")), [])
        for path in public.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("feed.xml", text)
            self.assertNotIn("rss", text.lower())
            self.assertNotIn("RSS", text)

    def test_switching_rss_off_removes_old_feed(self):
        p = self.project()
        p.build()
        self.assertTrue((p.root / "public" / "de" / "feed.xml").exists())
        (p.root / "config.toml").write_text(CONFIG.replace("rss           = true", "rss = false"))
        p.build()
        self.assertFalse((p.root / "public" / "feed.xml").exists())
        self.assertFalse((p.root / "public" / "de" / "feed.xml").exists())

    def test_rss_works_without_site_url_only_when_disabled(self):
        p = self.project(CONFIG.replace('site_url      = "https://status.example.com"', "")
                         .replace("rss           = true", "rss = false"))
        p.build()


class BuildTests(Case):
    def test_deterministic_output(self):
        a, b = self.project(), self.project()
        for p in (a, b):
            p.add_update(de=DE)
            p.add_update("20261013-080000", en="title: Two\ndate: 2026-10-13T08:00:00+02:00\n\nx\n")
        a.build()
        first = a.snapshot()
        a.build()
        self.assertEqual(first, a.snapshot())
        os.utime(a.root / "updates" / "20261012-234119" / "en.txt", (1, 1))  # different mtimes
        a.build()
        b.build()
        self.assertEqual(first, a.snapshot())
        self.assertEqual(first, b.snapshot())

    def test_newest_first_regardless_of_folder_order(self):
        p = self.project()
        p.add_update("20261012-100000", en="title: Older\ndate: 2026-10-12T10:00:00+02:00\n\nx\n")
        p.add_update("20261012-110000", en="title: Newer\ndate: 2026-10-12T11:00:00+02:00\n\nx\n")
        p.build()
        page = p.page()
        self.assertLess(page.index("Newer"), page.index("Older"))

    def test_timestamps_shown_in_configured_zone(self):
        p = self.project(CONFIG.replace("Europe/Berlin", "America/New_York"))
        p.add_update(en="title: T\ndate: 2026-10-12T23:41:19+02:00\n\nx\n")
        p.build()
        self.assertIn("12 Oct 2026, 17:41 (UTC-04:00)", p.page())
        p = self.project(CONFIG.replace("Europe/Berlin", "UTC"))
        p.add_update(en="title: T\ndate: 2026-10-12T23:41:19+02:00\n\nx\n")
        p.build()
        self.assertIn("12 Oct 2026, 21:41 (UTC+00:00)", p.page())

    def test_keeps_public_dir_and_removes_stale_files(self):
        p = self.project()
        public = p.build()
        inode = public.stat().st_ino
        (public / "old.html").write_text("old")
        (public / ".hidden").write_text("x")
        (public / "xx").mkdir()
        (public / "xx" / "index.html").write_text("old language")
        (public / "de" / "extra.txt").write_text("x")
        os.symlink("/etc", public / "link")
        p.build()
        self.assertEqual(public.stat().st_ino, inode)
        self.assertEqual(sorted(p.snapshot()), ["SHA256SUMS.txt", "de/feed.xml", "de/index.html", "favicon.svg",
                                                "feed.xml", "index.html", "robots.txt", "style.css"])
        self.assertFalse((public / "xx").exists() or (public / "link").is_symlink())
        self.assertTrue(Path("/etc").exists())
        # removing a language removes its folder, but never public/ itself
        (p.root / "config.toml").write_text(CONFIG.replace('["en", "de"]', '["en"]'))
        p.build()
        self.assertFalse((public / "de").exists())
        self.assertTrue(public.is_dir())

    def test_no_temp_files_left_behind(self):
        p = self.project()
        public = p.build()
        self.assertEqual([x.name for x in public.iterdir() if x.name.startswith(".")], [])

    def test_stray_folders_warn(self):
        p = self.project()
        (p.root / "updates" / "notes").mkdir(parents=True)
        (p.root / "updates" / ".gitkeep").write_text("")
        code, _, err = p.run("build")
        self.assertEqual(code, 0)
        self.assertIn("ignoring updates/notes", err)
        self.assertNotIn("gitkeep", err)


class CommandTests(Case):
    def test_new_from_message_file_stdin_and_editor(self):
        p = self.project()
        self.assertEqual(p.run("new", "From message", "-m", "Hello")[0], 0)
        (p.root / "stmt.txt").write_text("From file\n\nSecond paragraph\n")
        self.assertEqual(p.run("new", "From file", "-f", str(p.root / "stmt.txt"))[0], 0)
        self.assertEqual(p.run("new", "From stdin", stdin="From stdin text\n")[0], 0)
        editor = p.root / "editor.py"
        editor.write_text("import sys\nopen(sys.argv[1], 'a').write('Typed in editor')\n")
        env = {"EDITOR": f"{shlex.quote(sys.executable)} {shlex.quote(str(editor))}"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(p.run("new", "From editor", tty=True)[0], 0)
        bodies = sorted(f.read_text() for f in (p.root / "updates").glob("*/en.txt"))
        self.assertEqual(len(bodies), 4)
        joined = "\n".join(bodies)
        for needle in ("Hello", "Second paragraph", "From stdin text", "Typed in editor"):
            self.assertIn(needle, joined)
        for f in (p.root / "updates").glob("*/en.txt"):  # files round-trip through the parser
            title, date, body = crisis.parse_message(f, need_date=True)
            self.assertTrue(title and body and date.utcoffset() is not None)

    def test_new_ids_do_not_collide(self):
        p = self.project()
        fixed = crisis.datetime(2026, 10, 12, 23, 41, 19, tzinfo=crisis.timezone.utc)
        with mock.patch.object(crisis, "datetime") as dt:
            dt.now.return_value = fixed
            for _ in range(3):
                self.assertEqual(p.run("new", "Same second", "-m", "x")[0], 0)
        self.assertEqual(sorted(x.name for x in (p.root / "updates").iterdir()),
                         ["20261012-234119", "20261012-234119-1", "20261012-234119-2"])

    def test_new_rejects_empty_and_bad_input(self):
        p = self.project()
        self.assertEqual(p.run("new", "T", "-m", "  ")[0], 1)
        self.assertEqual(p.run("new", "  ", "-m", "x")[0], 1)
        self.assertEqual(p.run("new", "two\nlines", "-m", "x")[0], 1)
        code, _, err = p.run("new", "T", tty=True)  # no -m/-f, interactive, no $EDITOR
        with mock.patch.dict(os.environ, {"EDITOR": ""}):
            code, _, err = p.run("new", "T", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("EDITOR", err)
        self.assertEqual(p.run("new", "T", "-f", "does-not-exist.txt")[0], 1)
        self.assertFalse(list((p.root / "updates").glob("*")))

    def test_translate(self):
        p = self.project()
        p.add_update()
        code, out, err = p.run("translate", "20261012-234119", "--lang", "de", "Titel", "-m", "Text")
        self.assertEqual(code, 0, err)
        self.assertEqual((p.root / "updates/20261012-234119/de.txt").read_text(), "title: Titel\n\nText\n")
        again = p.run("translate", "20261012-234119", "--lang", "de", "Neu", "-m", "Neu")
        self.assertEqual(again[0], 1)
        self.assertIn("already exists", again[2])
        self.assertIn("Titel", (p.root / "updates/20261012-234119/de.txt").read_text())
        for args in (("20261012-234119", "--lang", "en"), ("20261012-234119", "--lang", "fr"),
                     ("20991231-000000", "--lang", "de"), ("../x", "--lang", "de")):
            self.assertEqual(p.run("translate", *args, "T", "-m", "x")[0], 1, args)

    def test_list(self):
        p = self.project()
        p.add_update(de=DE)
        p.add_update("20261013-080000", en="title: Second\ndate: 2026-10-13T08:00:00+02:00\n\nx\n")
        code, out, err = p.run("list")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("20261013-080000  2026-10-13 08:00:00 +0200  [en]  Second"))
        self.assertTrue(lines[1].startswith("20261012-234119") and "[en,de]" in lines[1])
        self.assertIn("20261013-080000 has no translation for: de", err)

    def test_deploy_runs_without_shell(self):
        p = self.project(CONFIG + f"deploy_command = {toml(self.deploy_cmd())}\n")
        self.assertEqual(p.run("deploy")[0], 1)  # nothing built yet
        p.build()
        code, out, err = p.run("deploy")
        self.assertEqual(code, 0, err)
        self.assertEqual((p.root / "deployed.txt").read_text(), "public/")
        # shell metacharacters are plain arguments, never interpreted
        cmd = self.deploy_cmd() + " '; touch pwned' '$(touch pwned2)'"
        (p.root / "config.toml").write_text(CONFIG + f"deploy_command = {toml(cmd)}\n")
        self.assertEqual(p.run("deploy")[0], 0)
        self.assertFalse((p.root / "pwned").exists() or (p.root / "pwned2").exists())

    @staticmethod
    def deploy_cmd():
        script = "import sys,pathlib; pathlib.Path('deployed.txt').write_text('public/')"
        return shlex.join([sys.executable, "-c", script])

    def test_deploy_errors_are_readable(self):
        p = self.project(CONFIG)
        p.build()
        code, _, err = p.run("deploy")
        self.assertEqual((code, err.startswith("error:")), (1, True))
        for cmd, needle in (("/nonexistent/tool --x", "cannot run"),
                            (f"{shlex.quote(sys.executable)} -c 'import sys; sys.exit(3)'", "status 3"),
                            ("'unterminated", "deploy_command")):
            (p.root / "config.toml").write_text(CONFIG + f"deploy_command = {toml(cmd)}\n")
            code, _, err = p.run("deploy")
            self.assertEqual(code, 1, cmd)
            self.assertIn(needle, err)
            self.assertNotIn("Traceback", err)

    def test_unknown_command_exit_code_nonzero(self):
        self.assertNotEqual(self.project().run("frobnicate")[0], 0)


class PublishTests(Case):
    def project_with_deploy(self):
        marker = "import pathlib; pathlib.Path('deployed.txt').write_text('yes')"
        cmd = shlex.join([sys.executable, "-c", marker])
        return self.project(CONFIG + f"deploy_command = {toml(cmd)}\n")

    def updates(self, p):
        return [x.name for x in (p.root / "updates").iterdir()] if (p.root / "updates").exists() else []

    def test_confirmed_publish_builds_and_deploys(self):
        p = self.project_with_deploy()
        code, out, err = p.run("publish", "Limited availability", "-m", "We are investigating.", stdin="y\n", tty=True)
        self.assertEqual(code, 0, err)
        self.assertIn("--- preview", out)
        self.assertIn("We are investigating.", out)
        self.assertEqual(len(self.updates(p)), 1)
        self.assertIn("Limited availability", p.page())
        self.assertTrue((p.root / "deployed.txt").exists())

    def test_rejected_publish_is_discarded_and_never_built(self):
        for answer in ("n\n", "\n", "", "maybe\n"):
            p = self.project_with_deploy()
            code, out, err = p.run("publish", "Secret draft", "-m", "do not publish", stdin=answer, tty=True)
            self.assertEqual(code, 1, answer)
            self.assertIn("discarded", err)
            self.assertEqual(self.updates(p), [])
            p.build()
            self.assertNotIn("Secret draft", p.page())
            self.assertFalse((p.root / "deployed.txt").exists())

    def test_publish_without_terminal_is_discarded(self):
        p = self.project_with_deploy()
        code, out, err = p.run("publish", "No tty", "-m", "text", stdin="y\n", tty=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.updates(p), [])
        self.assertFalse((p.root / "deployed.txt").exists())

    def test_publish_text_from_stdin_cannot_be_confirmed(self):
        p = self.project_with_deploy()
        code, _, _ = p.run("publish", "Piped", stdin="piped text\n", tty=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.updates(p), [])

    def test_interrupt_at_prompt_discards(self):
        p = self.project_with_deploy()
        with mock.patch.object(FakeStdin, "readline", side_effect=KeyboardInterrupt):
            code, _, _ = p.run("publish", "Ctrl-C", "-m", "x", tty=True)
        self.assertEqual(code, 1)
        self.assertEqual(self.updates(p), [])

    def test_yes_skips_question_even_without_terminal(self):
        p = self.project_with_deploy()
        code, _, err = p.run("publish", "Urgent", "-m", "now", "--yes", tty=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.updates(p)), 1)
        self.assertTrue((p.root / "deployed.txt").exists())

    def test_publish_needs_deploy_command_and_valid_config(self):
        p = self.project(CONFIG)
        code, _, err = p.run("publish", "T", "-m", "x", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("deploy_command", err)
        self.assertEqual(self.updates(p), [])
        p = self.project_with_deploy()
        (p.root / "i18n" / "de.toml").unlink()
        code, _, err = p.run("publish", "T", "-m", "x", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(self.updates(p), [])  # nothing half-created

    def test_failed_deploy_keeps_update_for_retry(self):
        p = self.project(CONFIG + 'deploy_command = "/nonexistent/uploader"\n')
        code, _, err = p.run("publish", "Keep me", "-m", "x", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("cannot run", err)
        self.assertEqual(len(self.updates(p)), 1)  # confirmed: stays; fix deploy and run 'deploy'
        self.assertIn("Keep me", p.page())


class CliProcessTests(Case):
    def test_script_runs_as_a_process_with_exit_codes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        shutil.copy(REPO / "crisis.py", root)
        shutil.copytree(REPO / "i18n", root / "i18n")

        def run(*args):
            return subprocess.run([sys.executable, "-I", str(root / "crisis.py"), *args], cwd=tmp.name,
                                  capture_output=True, text=True, stdin=subprocess.DEVNULL)

        self.assertEqual(run("build").returncode, 1)  # no config yet
        self.assertEqual(run("init").returncode, 0)
        done = run("build")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertTrue((root / "public" / "index.html").is_file())
        bad = run("publish", "T", "-m", "x")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("error:", bad.stderr)


if __name__ == "__main__":
    unittest.main()
