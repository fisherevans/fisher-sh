#!/usr/bin/env python3
"""build.py - render content.md + theme.json into dist/.

Reads content.md, expands the [[wiki-style]] keyword markup, runs the
markdown converter (with attr_list for {: .class} hooks and smarty for
curly quotes / dashes / ellipses), injects the body into template.html,
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

import markdown

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

STATIC_FILES = ["style.css", "favicon.svg"]
STATIC_DIRS = ["fonts"]
OPTIONAL_FILES = ["resume.pdf"]


# ----- markdown render -----

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


def attach_list_classes(html: str) -> str:
    """Convert `<!-- list-class: NAME -->` markers into class= on the
    next <ul>. Lets content.md mark a list as `class="projects"` etc.
    without the attr_list-on-list awkwardness (attr_list attaches to
    the LAST <li>, not the <ul>, which isn't what we want)."""
    return re.sub(
        r'<!--\s*list-class:\s*([A-Za-z0-9_-]+)\s*-->\s*<ul>',
        r'<ul class="\1">',
        html,
    )


def render_body(md_text: str) -> str:
    pre = expand_keywords(md_text)
    converter = markdown.Markdown(
        extensions=['attr_list', 'smarty'],
        output_format='html5',
    )
    html = converter.convert(pre)
    return attach_list_classes(html)


def render_page(md_text: str, template: str) -> str:
    return template.replace("{{ content }}", render_body(md_text))


# ----- theme -----

THEME_KEY_TO_CSS_VAR = {
    "bg": "--bg",
    "fg": "--fg",
    "muted": "--muted",
    "accent": "--accent",
    "hover": "--hover",
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
}


def render_theme(theme: dict) -> str:
    """theme.json -> small CSS string that overrides :root tokens."""
    pairs = []
    flat = {}
    for section in ("colors", "sizes", "marauder"):
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
    content = (ROOT / "content.md").read_text()
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
