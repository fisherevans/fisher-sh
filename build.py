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


def normalize_list_indent(text: str) -> str:
    """Round any indented list item up to 4-space-per-level.

    python-markdown only nests lists when child items are indented by a
    multiple of 4 spaces (or a tab). Most people type 2-space indents,
    which markdown silently treats as a paragraph continuation of the
    parent item -- so sub-bullets just don't render.

    Map input indent levels to 4-space output levels:
        1-2 input spaces  -> 4 output spaces  (level 1)
        3-4 input spaces  -> 8 output spaces  (level 2)
        5-6 input spaces  -> 12 output spaces (level 3)
    """
    def fix(m):
        spaces, marker, rest = m.groups()
        level = (len(spaces) + 1) // 2
        return ("    " * level) + marker + rest

    return re.sub(r'^( +)([-*+] )(.*)$', fix, text, flags=re.MULTILINE)


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
    pre = normalize_list_indent(md_text)
    pre = expand_keywords(pre)
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
    "hover-bg": "--hover-bg",
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
