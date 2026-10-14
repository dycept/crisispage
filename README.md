# crisispage

An independent, official information page for the public during a cyber attack (for example ransomware).

When your website, e-mail and internal systems are compromised or switched off, customers, press and
authorities still need one trustworthy place with official statements in chronological order.
**crisispage** builds exactly that: a plain static website, published from a command line, hosted
away from your own IT. Set it up *before* you need it.

- **Operators** (IT, security, communications) set it up and publish statements.
- **Readers** (customers, press, partners, authorities) read it and check that it is genuine.
- **Reviewers** audit the code before use. It is a single Python file you can read in under an hour.

## Principles

1. **No login, no web admin.** There is nothing to log in to. Statements are created locally on the command line.
2. **Static files only.** No JavaScript, cookies, trackers, database or external resources (no web fonts, no CDN).
   The only images are the optional logo and favicon you supply yourself, served from the same site.
3. **Minimal dependencies.** One script, `crisis.py`, Python 3.11+ standard library only.
4. **Deterministic build.** The same input gives byte-identical output; no build time is written into the files
   (time zone conversion uses the system's tz database, so compare builds made with the same `tzdata`).
5. **Independently operable.** Runs on any static host or in a hardened Docker container, separate from company IT.
6. **Easy to recognise as genuine.** Plain, informative page with a clear contact address and a list of places
   where the page's address is officially published.

Deliberately *not* included: user accounts, comments, forms, newsletters, e-mail subscriptions, uptime
monitoring or status badges, search, categories/tags, image uploads (beyond your own logo and favicon),
automatic translation, analytics.

## Quick start

```sh
git clone https://github.com/dycept/crisispage.git && cd crisispage
python3 crisis.py init                  # creates config.toml and updates/
$EDITOR config.toml                     # organization, contact e-mail, site_url, timezone ...
python3 crisis.py new "We are investigating an incident" -m "Short, factual text."
python3 crisis.py build                 # writes public/
python3 -m http.server -d public 8000   # look at it: http://localhost:8000
```

Upload the contents of `public/` to any static host. That is all.

Keep your `config.toml` and `updates/` in your own (private) fork or repository. Do not commit a statement
to a public repository before you have published it.

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create `config.toml` (commented template), `updates/` and a `legal/en.txt` template. Never overwrites anything. |
| `new "Title"` | Create an update in the default language. Text from `-m "text"`, `-f file`, stdin, or `$EDITOR`. |
| `translate ID --lang de "Title"` | Add a translation to an existing update (text sources as for `new`). |
| `build` | Regenerate `public/` (including `SHA256SUMS.txt`) and print the build hash. |
| `list` | List updates with ID, date, title and available languages; warns about missing translations. |
| `deploy` | Upload `public/` using `deploy_command`. |
| `publish "Title"` | `new` + preview + confirmation `[y/N]` + `build` + `deploy`. |

Behaviour worth knowing:

- `publish` shows a text preview and asks for confirmation. If you decline, or there is no terminal to ask on
  (for example piped input), **the new update is deleted again**, so it cannot go online with your next
  publication by accident. `--yes` skips the question.
- If you confirm and a later step fails (for example the upload), the update is kept. Fix the problem and run
  `python3 crisis.py deploy`.
- `deploy_command` runs **without a shell** (`shlex.split`, no `shell=True`), in the project directory.
- Every error ends with a readable message and a non-zero exit code. Missing translations are warnings, never
  errors, so a translation can never block an urgent statement.
- Published updates are not edited. **Publish a correction as a new update.** (A recommendation; it is not
  enforced technically.)

## Configuration (`config.toml`)

```toml
organization     = "Example Ltd"
contact_email    = "crisis-comms@example.com"   # required, validated
site_url         = "https://status.example.com" # no trailing slash; required when rss = true
timezone         = "Europe/Berlin"              # IANA name, default "UTC"
languages        = ["en", "de"]
default_language = "en"                         # default "en"; must be in languages
rss              = true                         # default false
official_links   = ["https://www.example.com/imprint"]
deploy_command   = "rsync -a --delete public/ deploy@status.example.com:./"

# Optional page elements - all off unless you uncomment them (see "Optional page elements"):
# contact_phone  = "+49 30 1234567"
# legal_notice   = true
# favicon        = "assets/favicon.svg"   # replace the built-in icon; false = no favicon
# logo           = "assets/logo.png"
# logo_alt       = "Example Ltd logo"
# credits_link   = "https://github.com/dycept/crisispage"

[intro]          # optional own introduction text per language
en = ""
de = ""
```

The file is validated on every run: required fields, e-mail format, time zone, known languages, default
language listed, `http(s)://` links, `rss = true` needs `site_url`, unknown keys (typos) are rejected.
`contact_email` must be ASCII (use punycode for internationalised domains).

## Optional page elements

Everything here is **off by default**: `init` writes the settings commented out, and removing or commenting
them out again switches the element off.

| Setting | Effect |
| --- | --- |
| `contact_phone = "+49 30 1234567"` | Adds a phone row (a `tel:` link) under the contact e-mail. |
| `legal_notice = true` | Adds a legal notice / imprint page, linked from every footer. The text lives in `legal/<code>.txt`, one plain-text file per language (blank line = paragraph, links work as in updates). The file of the default language is required; other languages without a file show it with a notice, like a missing translation. `init` creates `legal/en.txt` with a `TODO-REPLACE` line, and **the build refuses to publish the file until that line is removed.** |
| `credits_link = "https://github.com/dycept/crisispage"` | Adds a small "Powered by crisispage" link to the footer (translated per language). Leave it commented out to hide it. |
| `favicon = "assets/favicon.svg"` | Replaces the **built-in favicon** (a flat blue square with a white "i") with your own `.svg`, `.png` or `.ico`. `favicon = false` leaves the favicon out completely. |
| `logo = "assets/logo.png"` | Shows your logo above the heading on every page (`.svg`, `.png`, `.jpg`, `.gif` or `.webp`). No logo by default. |
| `logo_alt = "…"` | Alternative text for the logo. Default: the organization name. `""` marks a purely decorative logo. |

Images are copied into `public/` as `favicon.<ext>` and `logo.<ext>` and included in `SHA256SUMS.txt`. They
must lie inside the project directory, are limited to 512 KB (the page must load fast), and are checked by
file type and content. An SVG may contain **only plain graphics**: scripts, event attributes, links to other
files, `foreignObject`, animation and entity declarations are rejected. Because images are now part of the
page, the nginx config allows `img-src 'self'` (same-origin images only).

In a legal notice, state what the law of your country requires. The server also logs visitors' IP addresses, which
you may have to mention in a privacy notice; this is not legal advice.

## Updates

One update is one folder, `updates/YYYYMMDD-HHMMSS/` (the ID; a suffix `-1`, `-2` is added on collision), with
one text file per language. The file for the default language is required and carries the binding timestamp.

`updates/20261012-234119/en.txt`

```
title: Limited availability of our services
date: 2026-10-12T23:41:19+02:00

We are currently investigating an IT security incident.

More information: https://status.example.com
```

`updates/20261012-234119/de.txt` (translation: title and text only)

```
title: Eingeschränkte Erreichbarkeit unserer Dienste

Wir untersuchen derzeit einen IT-Sicherheitsvorfall.
```

Text is plain text: blank line = new paragraph, single line breaks are kept. `http://` and `https://`
addresses become links (trailing punctuation is not part of the link). There is no HTML and no Markdown;
everything is escaped, and nothing but `http(s)` is ever linked. Control characters in input are rejected.

Every update has a permanent anchor, e.g. `https://status.example.com/#u-20261012-234119`.
Timestamps are shown in the configured `timezone` with their UTC offset, e.g. `12 Oct 2026, 23:41 (UTC+02:00)`.

## Languages

- English is the default and is served from the site root: `/index.html`, `/feed.xml`.
  Every other language lives in a sub-folder: `/de/index.html`, `/de/feed.xml`. All links are relative, so
  the site works under any domain and path.
- Every page shows a plain language line, e.g. `English | Deutsch | Français`, each language in its own
  spelling (current one in bold). There is **no automatic redirect** by browser language (that would need
  JavaScript or server logic); the English start page is always reachable. Pages carry
  `<html lang>` and `hreflang` alternates including `x-default` (relative URLs, as the site must work under
  any address).
- **A missing translation never hides information:** the language page shows the default-language (English)
  text, marked `lang="en"`, with a notice in the page language, and so does that language's feed. In case of
  differences, **the English version is binding.**
- All fixed texts (headings, "Last updated", footer, month names, date format, text direction) live in
  `i18n/<code>.toml`. There is no dependence on the system locale. `dir = "rtl"` is supported.
  `build` fails if a configured language lacks its file or a required key.

**Shipped languages** (27; each is optional, only the ones listed in `languages` are built):

| Code | Language | Direction | Status |
| --- | --- | --- | --- |
| `ar` | العربية | rtl | draft, needs native review |
| `bg` | Български | ltr | draft, needs native review |
| `cs` | Čeština | ltr | draft, needs native review |
| `da` | Dansk | ltr | draft, needs native review |
| `de` | Deutsch | ltr | German text of the briefing |
| `el` | Ελληνικά | ltr | draft, needs native review |
| `en` | English | ltr | reference |
| `es` | Español | ltr | draft, needs native review |
| `fi` | Suomi | ltr | draft, needs native review |
| `fr` | Français | ltr | draft, needs native review |
| `he` | עברית | rtl | draft, needs native review |
| `hr` | Hrvatski | ltr | draft, needs native review |
| `hu` | Magyar | ltr | draft, needs native review |
| `it` | Italiano | ltr | draft, needs native review |
| `ja` | 日本語 | ltr | draft, needs native review |
| `ko` | 한국어 | ltr | draft, needs native review |
| `nb` | Norsk bokmål | ltr | draft, needs native review |
| `nl` | Nederlands | ltr | draft, needs native review |
| `pl` | Polski | ltr | draft, needs native review |
| `pt` | Português | ltr | draft, needs native review |
| `ro` | Română | ltr | draft, needs native review |
| `ru` | Русский | ltr | draft, needs native review |
| `sk` | Slovenčina | ltr | draft, needs native review |
| `sv` | Svenska | ltr | draft, needs native review |
| `tr` | Türkçe | ltr | draft, needs native review |
| `uk` | Українська | ltr | draft, needs native review |
| `zh` | 简体中文 | ltr | draft, needs native review |

Except for English and German, the translations are **machine-drafted and marked `DRAFT` in the first line of
each file. Have a native speaker check them before you rely on them in a real incident**; remove the `DRAFT`
marker once reviewed. `pt` uses European Portuguese spelling; copy it to `pt-br.toml` for Brazilian Portuguese,
and `zh` is Simplified Chinese.

**Add a language:** copy `i18n/en.toml` to `i18n/<code>.toml` (lowercase code such as `fr` or `pt-br`),
translate the values (keep every key and every `{placeholder}`), add the code to `languages` in `config.toml`,
run `build`. The unit tests check that every shipped file has the same keys as `en.toml`.

## RSS

`rss = true` writes one RSS 2.0 feed per language (`feed.xml`, `de/feed.xml`) and links it from every page.
Feeds need absolute links, so `site_url` is required. With `rss = false` no feed is written, an old feed is
removed from `public/`, and all references to it disappear.

## Search engines

The site asks to stay out of search engines: `robots.txt` (`Disallow: /`), `<meta name="robots" content="noindex">`
on every page and, in the nginx config, an `X-Robots-Tag: noindex` header. Note that `Disallow: /` stops
well-behaved crawlers from fetching the pages at all, so they never see the `noindex` tag; a search engine
may still list the bare address if other sites link to it. If you would rather have the page found by people
searching for your company name, remove `ROBOTS` and the `noindex` meta tag from `crisis.py` and the
`X-Robots-Tag` line from `nginx.conf`.

## Operation

The page must not depend on the infrastructure that is under attack: use a different hoster, your own domain
with independent DNS, and access that is not part of the company directory or SSO.

### Option 1: any static host

Upload the contents of `public/`. Done. `build` replaces files one by one atomically, removes stale files
and keeps `public/` itself (so a Docker bind mount on it keeps working).

### Option 2: your own server with Docker

`docker-compose.yml` serves `./public` read-only with an unprivileged nginx on port 8080 (bound to
`127.0.0.1`; put a TLS-terminating reverse proxy in front). New updates: build locally, then replace the
files in `public/` on the server (rsync). No restart is needed.

```sh
docker compose up -d
```

Alternatively the multi-stage `Dockerfile` builds the site from `config.toml`, `i18n/` and `updates/` and
copies only the result into the nginx image:

```sh
docker build -t crisispage . && docker run --rm -p 127.0.0.1:8080:8080 --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges crisispage
```

TLS and HSTS are the job of the reverse proxy or the hoster.

### nginx hardening (`nginx.conf`)

Only `GET` and `HEAD` (everything else `405`); `Content-Security-Policy: default-src 'none'; img-src 'self';
style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`; `X-Content-Type-Options: nosniff`;
`Referrer-Policy: no-referrer`; `X-Robots-Tag: noindex`; `server_tokens off`; no directory listing; `Cache-Control: no-cache` (readers
revalidate via ETag, so new updates show up at once); hidden files (`/.…`) are never served.
The container is read-only, drops all capabilities and sets `no-new-privileges`.

## Security when publishing

The only way to change the page is upload access to the server. There is deliberately no web login, not even
with MFA: every reachable endpoint would be attack surface.

**Second factor for every publication, with a hardware key:**

1. Each operator creates an own key: `ssh-keygen -t ed25519-sk` (add `-O verify-required` to also require a PIN).
   Every upload then needs a physical touch of the key.
2. On the server, the deploy user may only write to the target folder, via `rrsync` in `authorized_keys`:
   `command="rrsync /srv/crisispage/public",restrict sk-ssh-ed25519@openssh.com AAAA… operator-1`
   (with `rrsync` the remote path is relative to that folder, hence `deploy@host:./` in `deploy_command`;
   try it once with `rsync -n`).
3. Disable SSH password login on the server.
4. At least two people, each with their own key. Keep these accounts out of company AD/SSO.

**Genuineness for readers:**

- Name the page's address in advance on your company website (for example the imprint) and in emergency
  plans. These places are listed in the page footer (`official_links`).
- Because the build is deterministic, anyone who has the update files (for example from a public repository)
  can rebuild the site and compare the build hash with what is online (see
  [Publishing builds with hashes](#publishing-builds-with-hashes)).
- Signed updates (GPG/Sigstore) are not part of this version.

**In the code:** all HTML and XML output is escaped, including titles, configuration values and links; only
`http(s)` links are generated; nothing is run through a shell; input is never evaluated as code.
To report a vulnerability see [SECURITY.md](SECURITY.md).

## Publishing builds with hashes

Every `build` writes `public/SHA256SUMS.txt` (one `<hash>  <path>` line per file, sorted, in
`sha256sum -c` format) and prints one **build hash**, the SHA-256 of that manifest:

```
build hash (sha256 of SHA256SUMS.txt): 1cb59b6e…
```

Because the build is deterministic, that single value identifies the whole site. Publish it on a channel
that is independent of the web server (the release notes below, your company website, a social media
post, a press statement), because a manifest on the same server proves nothing against someone who controls
that server.

**Anyone can check the live site** against its manifest (replace the address):

```sh
base=https://status.example.com; mkdir verify && cd verify
curl -fsS "$base/SHA256SUMS.txt" | tee SHA256SUMS.txt | awk '{print $2}' |
  while read -r f; do mkdir -p "$(dirname "$f")"; curl -fsS "$base/$f" -o "$f"; done
sha256sum -c SHA256SUMS.txt && sha256sum SHA256SUMS.txt   # compare the last hash with the published build hash
```

**Anyone with the sources can rebuild it:** clone your repository at the release tag, run
`python3 crisis.py build`, and compare the printed build hash.

**GitHub release workflow** (`.github/workflows/release.yml`): once your `config.toml` and `updates/` are in
your repository, push a tag starting with `build-` after you have published a statement:

```sh
git tag build-20261012-2341 && git push origin build-20261012-2341
```

GitHub then runs the tests, builds the site from that exact commit, and creates a release with
`crisispage-site-<tag>.tar.gz` (a reproducible archive), its `.sha256`, and `SHA256SUMS.txt`; the release
notes show the build hash. Do not commit statements to a public repository before they are published.
(To use the workflow in a public fork you must enable GitHub Actions there.)

## Typical emergency workflow

1. Publish the prepared English statement:
   `python3 crisis.py publish "Limited availability" -f statement.txt`
2. Check the preview, answer `y`, touch the hardware key.
3. Add translations: `python3 crisis.py translate 20261012-234119 --lang de "Eingeschränkte Erreichbarkeit" -f de.txt`,
   then `python3 crisis.py build` and `python3 crisis.py deploy`.

## Preparation checklist

- [ ] Set up hoster and domain; page online, empty or with a neutral statement
- [ ] Replace **all** example values in `config.toml` (organization, contact e-mail, links) and check the built page
- [ ] Publish the page's address on your own website and in your emergency plans
- [ ] Equip at least two people with access and a hardware key
- [ ] Prepare a first statement (holding statement) in all languages
- [ ] Practise the whole workflow once a year

## Tests

```sh
python3 -m unittest
```

Standard library only. The tests cover configuration validation, languages and fallbacks, escaping and link
handling, RSS, deterministic output, checksums, `robots.txt`/`noindex`, `publish` discard behaviour,
stale-file cleanup, text contrast, the optional elements (phone, legal notice, credits, favicon, logo, including
rejection of unsafe SVGs), all shipped language files, and the absence of scripts, inline styles and external
resources. Not covered by unit tests (check them by hand when you change
the related files): HTML validation with the [W3C Nu validator](https://validator.github.io/validator/)
(`vnu.jar public/index.html style.css`), the layout at 360 px width, and the Docker/nginx behaviour.

## Project layout

```
crisis.py          all logic
i18n/              one file per interface language (27 shipped)
updates/           one folder per update (your data)
legal/             legal notice text per language (optional, your data)
assets/            your logo / favicon (optional, your data)
config.toml        created by `init` (your data)
nginx.conf  Dockerfile  docker-compose.yml
tests/
.github/workflows/  tests.yml (unit tests), release.yml (build + hashes on a build-* tag)
public/            generated, not in Git
```

## License

[MIT](LICENSE)
