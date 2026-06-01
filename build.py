#!/usr/bin/env python3
"""build.py - render content.html + theme.json into dist/.

Reads content.html (raw HTML body), expands the [[wiki-style]] keyword
markup and <!-- divider --> SVG shortcuts, injects the body into
template.html,
and renders theme.json into a tiny dist/theme.css that overrides the
:root token defaults baked into style.css. Static assets (style.css,
fonts/, favicon.svg) are copied verbatim.

Run from this directory:

    pip install -r requirements.txt
    python build.py

Cloudflare Pages build command:

    pip install -r requirements.txt && python build.py

Cloudflare Pages output directory: dist
"""

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

STATIC_FILES = ["style.css", "favicon.svg"]
STATIC_DIRS = ["fonts", "dividers"]
OPTIONAL_FILES = ["resume.pdf"]
DIVIDERS_DIR = "dividers"


# ----- body render -----

def expand_keywords(text: str) -> str:
    """Wiki-style keyword markup.

    [[text]]       -> <span class="kw">text</span>
    [[text|url]]   -> <a class="kw" href="url">text</a>

    Linked form is matched first to avoid the plain pattern eating the
    pipe + URL.
    """
    text = re.sub(
        r'\[\[([^\]|]+)\|([^\]]+)\]\]',
        r'<a class="kw" href="\2">\1</a>',
        text,
    )
    text = re.sub(
        r'\[\[([^\]]+)\]\]',
        r'<span class="kw">\1</span>',
        text,
    )
    return text


def expand_dividers(text: str) -> str:
    """Decorative SVG dividers, referenced as a CSS mask (not inlined).

        <!-- divider -->        -> dividers/flourish.svg (default)
        <!-- divider:name -->   -> dividers/<name>.svg

    The SVG is wrapped in <div class="divider"> so markdown sees a
    block-level element (no surrounding <p>) and CSS can size/center it.
    Inline form means the SVG inherits color from `currentColor` and
    follows the theme without needing a separate request.
    """
    def inline(m: "re.Match[str]") -> str:
        name = m.group(1) or "flourish"
        svg_path = ROOT / DIVIDERS_DIR / f"{name}.svg"
        if not svg_path.exists():
            return f"<!-- divider:{name} (file not found) -->"
        # Reference the file as a CSS mask (see .divider in style.css) instead
        # of inlining the SVG markup - keeps content.html small.
        return f'<div class="divider" style="--divider-img: url(\'/{DIVIDERS_DIR}/{name}.svg\')"></div>'

    return re.sub(
        r'<!--\s*divider(?::([A-Za-z0-9_-]+))?\s*-->',
        inline,
        text,
    )


def render_body_html(html_text: str) -> str:
    """The body source is raw HTML (content.html). We keep the two authoring
    shortcuts that work fine in HTML - [[keyword]] / [[keyword|url]] markup and
    <!-- divider --> SVG inlining - but run no markdown conversion."""
    out = expand_keywords(html_text)
    out = expand_dividers(out)
    return out


def render_page(html_text: str, template: str) -> str:
    return template.replace("{{ content }}", render_body_html(html_text))


# ----- theme -----

THEME_KEY_TO_CSS_VAR = {
    "bg": "--bg",
    "fg": "--fg",
    "muted": "--muted",
    "accent": "--accent",
    "hover": "--hover",
    "hover-bg": "--hover-bg",
    "divider": "--divider-color",
    "font-size-base-px": ("--font-size-base", lambda v: f"{v}px"),
    "font-size-h1-rem": ("--font-size-h1", lambda v: f"{v}rem"),
    "font-size-h2-rem": ("--font-size-h2", lambda v: f"{v}rem"),
    "max-width-px": ("--max-width", lambda v: f"{v}px"),
    "line-height-body": ("--line-height-body", lambda v: f"{v}"),
    "opsz-body": ("--opsz-body", lambda v: f"{v}"),
    "opsz-h1": ("--opsz-h1", lambda v: f"{v}"),
    "opsz-h2": ("--opsz-h2", lambda v: f"{v}"),
    "weight-body": ("--weight-body", lambda v: f"{v}"),
    "weight-h1": ("--weight-h1", lambda v: f"{v}"),
    "weight-h2": ("--weight-h2", lambda v: f"{v}"),
    "weight-link": ("--weight-link", lambda v: f"{v}"),
    # Bullet glyphs are stored as raw unicode chars in JSON; CSS needs them
    # wrapped in single quotes (string literal) as a `content:` value.
    "glyph": ("--bullet-glyph", lambda v: f"'{v}'"),
    "projects-glyph": ("--bullet-projects-glyph", lambda v: f"'{v}'"),
    "size-em": ("--bullet-size", lambda v: f"{v}em"),
    "offset-x-em": ("--bullet-offset-x", lambda v: f"{v}em"),
    "offset-y-em": ("--bullet-offset-y", lambda v: f"{v}em"),
}


def render_theme(theme: dict) -> str:
    """theme.json -> small CSS string that overrides :root tokens."""
    pairs = []
    flat = {}
    for section in ("colors", "sizes", "marauder", "bullets"):
        if section in theme:
            flat.update(theme[section])

    for key, value in flat.items():
        mapping = THEME_KEY_TO_CSS_VAR.get(key)
        if mapping is None:
            continue
        if isinstance(mapping, tuple):
            css_var, formatter = mapping
            pairs.append(f"    {css_var}: {formatter(value)};")
        else:
            pairs.append(f"    {mapping}: {value};")

    body = "\n".join(pairs)
    return f"/* generated from theme.json by build.py */\n:root {{\n{body}\n}}\n"


def load_theme() -> dict:
    return json.loads((ROOT / "theme.json").read_text())


# ----- pipeline -----

def build() -> None:
    content = (ROOT / "content.html").read_text()
    template = (ROOT / "template.html").read_text()
    theme = load_theme()

    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(render_page(content, template))
    (DIST / "theme.css").write_text(render_theme(theme))

    for fname in STATIC_FILES:
        shutil.copy(ROOT / fname, DIST / fname)
    for fname in OPTIONAL_FILES:
        src = ROOT / fname
        if src.exists():
            shutil.copy(src, DIST / fname)
    for dname in STATIC_DIRS:
        src = ROOT / dname
        dst = DIST / dname
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    print(f"built {DIST.relative_to(ROOT)}/")


if __name__ == "__main__":
    build()
