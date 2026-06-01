# fisher-sh

Static landing page for [fisher.sh](https://fisher.sh).

Markdown source, theme tokens in JSON, rendered to static HTML by a small Python build. Cloudflare Pages runs the build on push and serves `dist/`.

| File | What |
|---|---|
| `content.md` | The page content. Edit this for what the page says. |
| `theme.json` | Colors, font sizes, optical-size axes, weights. Edit for how it looks. |
| `style.css` | Layout, typography, link behavior. Edit for structural style changes. |
| `template.html` | The page wrapper (`<head>`, font preloads, body shell). |
| `build.py` | Renders `content.md` + `theme.json` -> `dist/`. |
| `editor.py` | Local split-pane editor + live preview + auto-save. |
| `fonts/` | Bundled Marauder variable woff2 (OFL 1.1). |

## Local editor

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python editor.py
# open http://localhost:4174/
```

Two tabs in the left pane: **content** (`content.md` raw markdown) and **settings** (form over `theme.json`). The right pane is a live preview that reloads as you type. Edits auto-save with a small debounce. No `git` involvement; commit and push when you're happy with what's on disk.

The same `build.py` runs locally for a one-shot render:

```sh
.venv/bin/python build.py
# output in dist/
```

## Content markup

`content.md` is regular Markdown with two small extras:

| Syntax | Renders as |
|---|---|
| `[[burlington, vermont]]` | `<span class="kw">burlington, vermont</span>` (highlighted, not a link) |
| `[[DataDog\|https://log.fisher.sh/tags/datadog/]]` | `<a class="kw" href="...">DataDog</a>` (highlighted + link to a blog tag page) |
| `<!-- list-class: projects -->` before a list | adds `class="projects"` to the next `<ul>` (open-circle bullets) |
| `<!-- divider -->` on its own line | inlines `dividers/flourish.svg` as a centered SVG that picks up the theme `--muted` color via `currentColor` |
| `<!-- divider:name -->` | same, but inlines `dividers/<name>.svg` -- drop more SVGs in that folder to get more options |
| `text\n{: .center }` on the next line | text-align: center on the preceding block. Also `.left` and `.right`. Works on paragraphs, headings, lists. |

Smartypants is on, so `"foo"` becomes curly quotes, `...` becomes `…`, `---` becomes an em dash.

## Cloudflare Pages

- Project name: `fisher-sh`
- Build command: `pip install -r requirements.txt && python build.py`
- Output directory: `dist`
- Custom domains: `fisher.sh`, `www.fisher.sh`
- Source: GitHub `fisherevans/fisher-sh` on `main`

Full recreate-from-scratch runbook lives in [nottingham-cloud / systems/log.md](https://github.com/fisherevans/nottingham-cloud/blob/main/systems/log.md#bootstrap--recovery-procedures).
