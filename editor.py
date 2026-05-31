#!/usr/bin/env python3
"""editor.py - local live-preview mini-CMS for fisher.sh.

    pip install -r requirements.txt
    python editor.py [port]            # defaults to 4174

Browser opens at http://localhost:<port>/. Layout: tabbed left pane
(Content / Settings) and a live preview iframe on the right.

The content pane is a textarea over content.md. Edits debounce-render
into the preview after ~200ms and auto-save to content.md after ~800ms.
Settings is a generated form over theme.json with the same render +
save loop. Both files are written atomically (tmp + rename) so a SIGINT
mid-write can't corrupt the source.

The preview iframe loads /preview which serves the latest rendered HTML
from in-memory state, so all relative URLs (style.css, /theme.css,
@font-face hits) resolve same-origin to the editor server -- the
srcdoc-with-null-origin path was painful for cross-origin font loading.
"""

import http.server
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build import (  # noqa: E402  (import after sys.path tweak)
    ROOT,
    load_theme,
    render_body,
    render_theme,
)

DEFAULT_PORT = 4174
CONTENT_PATH = ROOT / "content.md"
THEME_PATH = ROOT / "theme.json"
TEMPLATE_PATH = ROOT / "template.html"
THEMES_DIR = ROOT / "themes"
THEME_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,40}$')


# ----- editor UI (served at /) -----

EDITOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>fisher.sh - editor</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, system-ui, "Segoe UI", sans-serif; }
    body { display: flex; flex-direction: column; background: #1a1a1a; color: #e0e0e0; }

    .topbar { display: flex; align-items: center; gap: 0.75rem; padding: 0.4rem 0.75rem; background: #2c2825; color: #f4ecd8; font-size: 13px; flex-shrink: 0; }
    .topbar .title { font-weight: 600; }
    .topbar .tabs { display: flex; gap: 0.25rem; }
    .topbar .tab { padding: 0.25rem 0.75rem; border-radius: 4px; cursor: pointer; user-select: none; opacity: 0.6; font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }
    .topbar .tab.active { background: rgba(255,255,255,0.12); opacity: 1; }
    .topbar .tab:hover { opacity: 1; }
    .topbar .status { margin-left: auto; opacity: 0.6; font-size: 12px; }

    .panes { flex: 1; display: flex; min-height: 0; }
    .pane { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .pane + .pane { border-left: 1px solid #333; }
    .pane-head { padding: 0.3rem 0.75rem; background: #232020; color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; flex-shrink: 0; }

    textarea {
      flex: 1; width: 100%;
      padding: 1rem;
      border: none; outline: none; resize: none;
      font-family: ui-monospace, 'SF Mono', 'Menlo', monospace;
      font-size: 14px; line-height: 1.55;
      background: #1e1c1a; color: #e8dec8;
      tab-size: 4;
    }

    iframe { flex: 1; width: 100%; border: none; background: #f4ecd8; }

    .settings { flex: 1; overflow: auto; padding: 1rem; background: #1e1c1a; }
    .settings h3 { margin: 1.25rem 0 0.5rem; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #aaa; font-weight: 600; }
    .settings h3:first-child { margin-top: 0; }
    .settings .group { margin-bottom: 1.25rem; }

    .scheme-bar {
      display: grid; grid-template-columns: 1fr auto auto auto; gap: 0.4rem;
      align-items: center; padding: 0.5rem; margin-bottom: 0.75rem;
      background: #2a2825; border: 1px solid #3a3835; border-radius: 6px;
    }
    .scheme-bar select {
      padding: 0.3rem 0.5rem; background: #1e1c1a; color: #e8dec8;
      border: 1px solid #3a3835; border-radius: 4px; font-size: 12px; min-width: 0;
    }
    .scheme-bar button {
      padding: 0.3rem 0.6rem; background: #3a3835; color: #e8dec8;
      border: 1px solid #4a4540; border-radius: 4px; cursor: pointer; font-size: 11px;
      white-space: nowrap;
    }
    .scheme-bar button:hover { background: #4a4540; }
    .scheme-bar button:disabled { opacity: 0.4; cursor: not-allowed; }
    .scheme-bar button.danger { background: transparent; color: #b85555; border-color: #4a3535; }
    .scheme-bar button.danger:hover { background: #4a3535; color: #f4a5a5; }

    .settings .field { display: grid; grid-template-columns: 11rem 1fr 5.5rem; gap: 0.5rem; align-items: center; padding: 0.25rem 0; }
    .settings .field.color { grid-template-columns: 11rem 3rem 1fr; }
    .settings .field label { font-size: 12px; color: #c8bfae; }
    .settings .field input[type=color] {
      width: 100%; height: 28px; padding: 0;
      border: 1px solid #3a3835; border-radius: 4px;
      background: transparent; cursor: pointer;
    }
    .settings .field input[type=text].hex {
      padding: 0.25rem 0.5rem; background: #2a2825; color: #e8dec8;
      border: 1px solid #3a3835; border-radius: 4px;
      font-family: ui-monospace, 'SF Mono', monospace; font-size: 12px;
      text-transform: lowercase;
    }
    .settings .field input[type=text].hex.invalid {
      border-color: #b85555;
    }
    .settings .field input[type=number] {
      width: 100%; padding: 0.25rem 0.5rem; background: #2a2825; color: #e8dec8;
      border: 1px solid #3a3835; border-radius: 4px;
      font-family: ui-monospace, 'SF Mono', monospace; font-size: 12px;
    }
    .settings .field input[type=range] { width: 100%; }
    .settings .field .val {
      font-family: ui-monospace, 'SF Mono', monospace; font-size: 11px;
      color: #8a7e6e; text-align: right;
    }
    .settings .reset {
      margin-top: 1.25rem; padding: 0.4rem 0.75rem;
      background: transparent; color: #888; border: 1px solid #3a3835; border-radius: 4px;
      cursor: pointer; font-size: 11px;
    }
    .settings .reset:hover { color: #e0e0e0; border-color: #555; }
  </style>
</head>
<body>
  <div class="topbar">
    <span class="title">fisher.sh</span>
    <div class="tabs">
      <span class="tab active" data-tab="content">content</span>
      <span class="tab" data-tab="settings">settings</span>
    </div>
    <span class="status" id="status">connecting...</span>
  </div>
  <div class="panes">
    <div class="pane" id="left-pane">
      <div class="pane-head" id="left-head">content.md</div>
      <textarea id="md" spellcheck="false" autofocus></textarea>
      <div class="settings" id="settings" style="display:none">
        <div id="settings-form"></div>
        <button class="reset" id="reset-btn" type="button">reset all to defaults</button>
      </div>
    </div>
    <div class="pane">
      <div class="pane-head">preview</div>
      <iframe id="preview" src="/preview"></iframe>
    </div>
  </div>

<script>
const md = document.getElementById('md');
const settingsPane = document.getElementById('settings');
const preview = document.getElementById('preview');
const status = document.getElementById('status');
const settingsForm = document.getElementById('settings-form');
const leftHead = document.getElementById('left-head');

let theme = null;
let defaults = null;
let schemeLists = { colors: [], sizes: [] };
let selectedScheme = { colors: '', sizes: '' };
let saveMdTimer = null;
let saveThemeTimer = null;
let reloadTimer = null;
let dirty = false;

function setStatus(text) { status.textContent = text; }

function scheduleReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => {
    preview.src = '/preview?v=' + Date.now();
  }, 180);
}

async function loadInitial() {
  const [mdResp, themeResp, defResp, colorsResp, sizesResp] = await Promise.all([
    fetch('/content.md'),
    fetch('/theme.json'),
    fetch('/theme.defaults.json'),
    fetch('/api/themes/colors'),
    fetch('/api/themes/sizes'),
  ]);
  md.value = await mdResp.text();
  theme = await themeResp.json();
  defaults = await defResp.json();
  schemeLists.colors = await colorsResp.json();
  schemeLists.sizes = await sizesResp.json();
  renderSettingsForm();
  setStatus('ready');
}

async function saveContent() {
  try {
    const resp = await fetch('/save/content', {
      method: 'POST', headers: {'Content-Type': 'text/markdown'}, body: md.value
    });
    if (!resp.ok) throw new Error('http ' + resp.status);
    dirty = false;
    setStatus('saved ' + new Date().toLocaleTimeString());
    scheduleReload();
  } catch (err) {
    setStatus('save failed: ' + err.message);
  }
}

async function saveTheme() {
  try {
    const resp = await fetch('/save/theme', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(theme, null, 2)
    });
    if (!resp.ok) throw new Error('http ' + resp.status);
    dirty = false;
    setStatus('theme saved ' + new Date().toLocaleTimeString());
    scheduleReload();
  } catch (err) {
    setStatus('theme save failed: ' + err.message);
  }
}

md.addEventListener('input', () => {
  dirty = true;
  setStatus('editing...');
  clearTimeout(saveMdTimer);
  saveMdTimer = setTimeout(saveContent, 700);
});

document.querySelectorAll('.topbar .tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.topbar .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    const which = t.dataset.tab;
    if (which === 'content') {
      md.style.display = '';
      settingsPane.style.display = 'none';
      leftHead.textContent = 'content.md';
    } else {
      md.style.display = 'none';
      settingsPane.style.display = '';
      leftHead.textContent = 'theme.json';
    }
  });
});

document.getElementById('reset-btn').addEventListener('click', () => {
  if (!confirm('reset ALL theme values to defaults?')) return;
  theme = JSON.parse(JSON.stringify(defaults));
  selectedScheme = { colors: '', sizes: '' };
  renderSettingsForm();
  saveTheme();
});

// ---- scheme list management ----

async function refreshSchemeList(kind) {
  const resp = await fetch('/api/themes/' + kind);
  schemeLists[kind] = await resp.json();
}

async function loadScheme(kind, name) {
  if (!name) return;
  const resp = await fetch('/api/themes/' + kind + '/' + encodeURIComponent(name));
  if (!resp.ok) { setStatus('load failed: ' + name); return; }
  const data = await resp.json();
  if (kind === 'colors') {
    theme.colors = data;
  } else {
    if (data.sizes) theme.sizes = data.sizes;
    if (data.marauder) theme.marauder = data.marauder;
    if (data.bullets) theme.bullets = data.bullets;
  }
  selectedScheme[kind] = name;
  renderSettingsForm();
  saveTheme();
  setStatus('loaded ' + kind + '/' + name);
}

async function saveSchemeAs(kind) {
  const name = prompt('save ' + kind + ' scheme as:', selectedScheme[kind] || '');
  if (!name) return;
  if (!/^[A-Za-z0-9_-]{1,40}$/.test(name)) {
    alert('name must match [A-Za-z0-9_-]{1,40}');
    return;
  }
  await writeScheme(kind, name);
  selectedScheme[kind] = name;
  await refreshSchemeList(kind);
  renderSettingsForm();
}

async function saveSchemeOverwrite(kind) {
  const name = selectedScheme[kind];
  if (!name) return;
  await writeScheme(kind, name);
}

async function writeScheme(kind, name) {
  const payload = kind === 'colors'
    ? theme.colors
    : { sizes: theme.sizes, marauder: theme.marauder, bullets: theme.bullets };
  const resp = await fetch('/api/themes/' + kind + '/' + encodeURIComponent(name), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload, null, 2)
  });
  if (resp.ok) {
    setStatus('saved ' + kind + '/' + name);
  } else {
    setStatus('save scheme failed');
  }
}

async function deleteScheme(kind) {
  const name = selectedScheme[kind];
  if (!name) return;
  if (!confirm('delete ' + kind + ' scheme "' + name + '"?')) return;
  const resp = await fetch('/api/themes/' + kind + '/' + encodeURIComponent(name), {
    method: 'DELETE'
  });
  if (resp.ok) {
    selectedScheme[kind] = '';
    await refreshSchemeList(kind);
    renderSettingsForm();
    setStatus('deleted ' + kind + '/' + name);
  }
}

// ---- form rendering ----

const BULLET_OPTIONS = [
  { value: '•', label: '•  dot' },
  { value: '◦', label: '◦  open circle' },
  { value: '▪', label: '▪  square (small)' },
  { value: '▫', label: '▫  open square' },
  { value: '◆', label: '◆  diamond' },
  { value: '◇', label: '◇  open diamond' },
  { value: '❖', label: '❖  ornate diamond' },
  { value: '✦', label: '✦  four-pointed star' },
  { value: '⁂', label: '⁂  asterism (three asterisks)' },
  { value: '·', label: '·  middle dot' },
  { value: '‣', label: '‣  triangle' },
  { value: '›', label: '›  angle' },
  { value: '⁃', label: '⁃  hyphen bullet' },
  { value: '❦', label: '❦  floral heart' },
  { value: '❧', label: '❧  rotated floral heart' },
  { value: '§', label: '§  section sign' },
];

const FIELDS = [
  { section: 'colors', kind: 'colors', title: 'colors', fields: [
    { key: 'bg', label: 'background', type: 'color' },
    { key: 'fg', label: 'text', type: 'color' },
    { key: 'muted', label: 'italic asides', type: 'color' },
    { key: 'accent', label: 'accent / links', type: 'color' },
    { key: 'hover', label: 'link hover', type: 'color' },
    { key: 'hover-bg', label: 'link hover bg (highlight)', type: 'color' },
  ]},
  { section: 'sizes', kind: 'sizes', title: 'sizes', fields: [
    { key: 'font-size-base-px', label: 'body size (px)', type: 'number', min: 10, max: 48, step: 1 },
    { key: 'font-size-h1-rem', label: 'h1 size (rem)', type: 'number', min: 1, max: 12, step: 0.1 },
    { key: 'font-size-h2-rem', label: 'h2 size (rem)', type: 'number', min: 0.6, max: 4, step: 0.05 },
    { key: 'max-width-px', label: 'content max-width (px)', type: 'number', min: 320, max: 1400, step: 10 },
    { key: 'line-height-body', label: 'body line-height', type: 'number', min: 1.0, max: 2.2, step: 0.05 },
  ]},
  { section: 'marauder', kind: 'sizes', title: 'marauder axes', fields: [
    { key: 'opsz-body', label: 'body opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'opsz-h2', label: 'h2 opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'opsz-h1', label: 'h1 opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'weight-body', label: 'body weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
    { key: 'weight-h2', label: 'h2 weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
    { key: 'weight-h1', label: 'h1 weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
    { key: 'weight-link', label: 'link weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
  ]},
  { section: 'bullets', kind: 'sizes', title: 'bullets', fields: [
    { key: 'glyph', label: 'main bullet glyph', type: 'select', options: BULLET_OPTIONS },
    { key: 'projects-glyph', label: 'sub / projects glyph', type: 'select', options: BULLET_OPTIONS },
    { key: 'size-em', label: 'size (em)', type: 'range', min: 0.5, max: 3, step: 0.05 },
    { key: 'offset-x-em', label: 'x offset (em)', type: 'range', min: -1, max: 1, step: 0.02 },
    { key: 'offset-y-em', label: 'y offset (em)', type: 'range', min: -0.6, max: 0.6, step: 0.01 },
  ]},
];

function makeSchemeBar(kind) {
  const bar = document.createElement('div');
  bar.className = 'scheme-bar';

  const sel = document.createElement('select');
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = '— pick a saved ' + kind + ' scheme —';
  sel.appendChild(placeholder);
  for (const name of schemeLists[kind]) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    if (name === selectedScheme[kind]) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener('change', () => loadScheme(kind, sel.value));
  bar.appendChild(sel);

  const saveBtn = document.createElement('button');
  saveBtn.textContent = 'save';
  saveBtn.title = 'overwrite the selected scheme with current values';
  saveBtn.disabled = !selectedScheme[kind];
  saveBtn.addEventListener('click', () => saveSchemeOverwrite(kind));
  bar.appendChild(saveBtn);

  const saveAsBtn = document.createElement('button');
  saveAsBtn.textContent = 'save as...';
  saveAsBtn.addEventListener('click', () => saveSchemeAs(kind));
  bar.appendChild(saveAsBtn);

  const delBtn = document.createElement('button');
  delBtn.textContent = 'delete';
  delBtn.className = 'danger';
  delBtn.disabled = !selectedScheme[kind];
  delBtn.addEventListener('click', () => deleteScheme(kind));
  bar.appendChild(delBtn);

  return bar;
}

function isValidHex(v) { return /^#[0-9a-fA-F]{6}$/.test(v); }

function makeField(group, f) {
  const row = document.createElement('div');
  row.className = 'field' + (f.type === 'color' ? ' color' : '');

  const lbl = document.createElement('label');
  lbl.textContent = f.label;
  row.appendChild(lbl);

  const current = theme[group.section][f.key];

  if (f.type === 'color') {
    const colorInput = document.createElement('input');
    colorInput.type = 'color';
    colorInput.value = current;
    row.appendChild(colorInput);

    const hex = document.createElement('input');
    hex.type = 'text';
    hex.className = 'hex';
    hex.value = current;
    hex.spellcheck = false;
    row.appendChild(hex);

    const commit = (val) => {
      theme[group.section][f.key] = val;
      colorInput.value = val;
      hex.value = val;
      hex.classList.remove('invalid');
      dirty = true;
      setStatus('editing theme...');
      clearTimeout(saveThemeTimer);
      saveThemeTimer = setTimeout(saveTheme, 350);
    };

    colorInput.addEventListener('input', () => commit(colorInput.value));
    hex.addEventListener('input', () => {
      const v = hex.value.trim();
      if (isValidHex(v)) {
        commit(v.toLowerCase());
      } else {
        hex.classList.add('invalid');
      }
    });
    hex.addEventListener('focus', () => hex.select());
  } else if (f.type === 'select') {
    const sel = document.createElement('select');
    sel.style.gridColumn = 'span 2';
    sel.style.padding = '0.3rem 0.5rem';
    sel.style.background = '#2a2825';
    sel.style.color = '#e8dec8';
    sel.style.border = '1px solid #3a3835';
    sel.style.borderRadius = '4px';
    sel.style.fontSize = '13px';
    for (const opt of (f.options || [])) {
      const o = document.createElement('option');
      o.value = opt.value;
      o.textContent = opt.label;
      if (opt.value === current) o.selected = true;
      sel.appendChild(o);
    }
    row.appendChild(sel);

    sel.addEventListener('change', () => {
      theme[group.section][f.key] = sel.value;
      dirty = true;
      setStatus('editing theme...');
      clearTimeout(saveThemeTimer);
      saveThemeTimer = setTimeout(saveTheme, 200);
    });
  } else {
    const input = document.createElement('input');
    input.type = f.type;
    input.value = current;
    if (f.min !== undefined) input.min = f.min;
    if (f.max !== undefined) input.max = f.max;
    if (f.step !== undefined) input.step = f.step;
    row.appendChild(input);

    const val = document.createElement('span');
    val.className = 'val';
    val.textContent = current;
    row.appendChild(val);

    input.addEventListener('input', () => {
      const v = parseFloat(input.value);
      theme[group.section][f.key] = v;
      val.textContent = v;
      dirty = true;
      setStatus('editing theme...');
      clearTimeout(saveThemeTimer);
      saveThemeTimer = setTimeout(saveTheme, 350);
    });
  }
  return row;
}

function renderSettingsForm() {
  settingsForm.innerHTML = '';

  // Color scheme picker + form
  settingsForm.appendChild(makeSection('color scheme', 'colors'));
  const colorsGroup = FIELDS.find(g => g.section === 'colors');
  for (const f of colorsGroup.fields) settingsForm.appendChild(makeField(colorsGroup, f));

  // Size scheme picker + sizes + marauder
  settingsForm.appendChild(makeSection('size scheme', 'sizes'));
  for (const g of FIELDS.filter(x => x.kind === 'sizes')) {
    const h = document.createElement('h3');
    h.textContent = g.title;
    settingsForm.appendChild(h);
    for (const f of g.fields) settingsForm.appendChild(makeField(g, f));
  }
}

function makeSection(title, kind) {
  const wrap = document.createElement('div');
  const h = document.createElement('h3');
  h.textContent = title;
  wrap.appendChild(h);
  wrap.appendChild(makeSchemeBar(kind));
  return wrap;
}

window.addEventListener('beforeunload', () => {
  if (dirty) {
    const blob = new Blob([md.value], {type: 'text/markdown'});
    navigator.sendBeacon('/save/content', blob);
  }
});

loadInitial();
</script>
</body>
</html>
"""


# ----- in-memory current state (used by /preview) -----

_state_lock = threading.Lock()
_current_html = ""
_current_theme_css = ""


def rebuild_state(content_md: str | None = None, theme_obj: dict | None = None) -> None:
    global _current_html, _current_theme_css
    if content_md is None:
        content_md = CONTENT_PATH.read_text()
    if theme_obj is None:
        theme_obj = load_theme()
    template = TEMPLATE_PATH.read_text()
    body_html = render_body(content_md)
    with _state_lock:
        _current_html = template.replace("{{ content }}", body_html)
        _current_theme_css = render_theme(theme_obj)


# ----- atomic save -----

def atomic_write_text(path: Path, data: str) -> None:
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ----- defaults (for the reset button) -----

DEFAULT_THEME = {
    "colors": {
        "bg": "#f4ecd8",
        "fg": "#2c2825",
        "muted": "#8a7e6e",
        "accent": "#a8460e",
        "hover": "#6b2a0a",
        "hover-bg": "#fff3a8",
    },
    "sizes": {
        "font-size-base-px": 22,
        "font-size-h1-rem": 4.5,
        "font-size-h2-rem": 1.4,
        "max-width-px": 720,
        "line-height-body": 1.55,
    },
    "marauder": {
        "opsz-body": 6,
        "opsz-h1": 72,
        "opsz-h2": 10,
        "weight-body": 400,
        "weight-h1": 700,
        "weight-h2": 700,
        "weight-link": 400,
    },
    "bullets": {
        "glyph": "•",
        "projects-glyph": "◦",
        "size-em": 1.5,
        "offset-x-em": 0,
        "offset-y-em": 0.04,
    },
}


# ----- named theme schemes -----

def _scheme_dir(kind: str) -> Path:
    if kind not in ("colors", "sizes"):
        raise ValueError(f"bad kind: {kind}")
    p = THEMES_DIR / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_schemes(kind: str) -> list[str]:
    """Return sorted list of saved scheme names for kind."""
    d = _scheme_dir(kind)
    return sorted(p.stem for p in d.glob("*.json"))


def read_scheme(kind: str, name: str) -> dict:
    if not THEME_NAME_RE.match(name):
        raise ValueError(f"bad name: {name}")
    path = _scheme_dir(kind) / f"{name}.json"
    return json.loads(path.read_text())


def write_scheme(kind: str, name: str, data: dict) -> None:
    if not THEME_NAME_RE.match(name):
        raise ValueError(f"bad name: {name}")
    path = _scheme_dir(kind) / f"{name}.json"
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def delete_scheme(kind: str, name: str) -> None:
    if not THEME_NAME_RE.match(name):
        raise ValueError(f"bad name: {name}")
    path = _scheme_dir(kind) / f"{name}.json"
    if path.exists():
        path.unlink()


# ----- request handler -----

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quiet by default: only show non-2xx
        if len(args) >= 2 and str(args[1])[0] not in ("2", "3"):
            super().log_message(fmt, *args)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path in ("/", "/editor", "/editor/"):
            return self._send_html(EDITOR_HTML)

        if path == "/preview":
            with _state_lock:
                html = _current_html
            return self._send_html(html)

        if path == "/theme.css":
            with _state_lock:
                css = _current_theme_css
            return self._send(css.encode("utf-8"), "text/css; charset=utf-8")

        if path == "/content.md":
            return self._send(CONTENT_PATH.read_bytes(), "text/markdown; charset=utf-8")

        if path == "/theme.json":
            return self._send(THEME_PATH.read_bytes(), "application/json; charset=utf-8")

        if path == "/theme.defaults.json":
            return self._send(
                json.dumps(DEFAULT_THEME, indent=2).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        # /api/themes/<kind>          -> list of names
        # /api/themes/<kind>/<name>   -> the scheme JSON
        m = re.match(r"^/api/themes/(colors|sizes)(?:/([^/]+))?$", path)
        if m:
            kind, name = m.group(1), m.group(2)
            if name is None:
                payload = json.dumps(list_schemes(kind)).encode("utf-8")
                return self._send(payload, "application/json; charset=utf-8")
            try:
                data = read_scheme(kind, name)
            except (FileNotFoundError, ValueError):
                return self.send_error(404)
            payload = json.dumps(data, indent=2).encode("utf-8")
            return self._send(payload, "application/json; charset=utf-8")

        # Static asset fall-through (style.css, fonts/, favicon.svg)
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""

        if self.path == "/save/content":
            atomic_write_text(CONTENT_PATH, body)
            rebuild_state(content_md=body)
            return self._send(b"ok", "text/plain")

        if self.path == "/save/theme":
            try:
                theme_obj = json.loads(body)
            except json.JSONDecodeError as e:
                return self._send(str(e).encode(), "text/plain", status=400)
            atomic_write_text(THEME_PATH, json.dumps(theme_obj, indent=2) + "\n")
            rebuild_state(theme_obj=theme_obj)
            return self._send(b"ok", "text/plain")

        self.send_error(404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        m = re.match(r"^/api/themes/(colors|sizes)/([^/]+)$", self.path)
        if not m:
            return self.send_error(404)
        kind, name = m.group(1), m.group(2)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return self._send(str(e).encode(), "text/plain", status=400)
        try:
            write_scheme(kind, name, data)
        except ValueError as e:
            return self._send(str(e).encode(), "text/plain", status=400)
        return self._send(b"ok", "text/plain")

    def do_DELETE(self):
        m = re.match(r"^/api/themes/(colors|sizes)/([^/]+)$", self.path)
        if not m:
            return self.send_error(404)
        kind, name = m.group(1), m.group(2)
        try:
            delete_scheme(kind, name)
        except ValueError as e:
            return self._send(str(e).encode(), "text/plain", status=400)
        return self._send(b"ok", "text/plain")

    def _send(self, payload: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html: str) -> None:
        self._send(html.encode("utf-8"), "text/html; charset=utf-8")


def main() -> None:
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    os.chdir(ROOT)
    rebuild_state()

    socketserver.TCPServer.allow_reuse_address = True
    print(f"editor:  http://localhost:{port}/")
    print(f"preview: http://localhost:{port}/preview")
    print(f"watching: {CONTENT_PATH.relative_to(ROOT)} + {THEME_PATH.relative_to(ROOT)}")
    print("Ctrl-C to stop")
    with socketserver.TCPServer(("", port), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nbye")


if __name__ == "__main__":
    main()
