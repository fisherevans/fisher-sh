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
    .settings h3 { margin: 0 0 0.5rem; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #aaa; font-weight: 600; }
    .settings .group { margin-bottom: 1.5rem; }
    .settings .field { display: grid; grid-template-columns: 11rem 1fr 4.5rem; gap: 0.5rem; align-items: center; padding: 0.25rem 0; }
    .settings .field label { font-size: 12px; color: #c8bfae; }
    .settings .field input[type=color] { width: 100%; height: 28px; padding: 0; border: 1px solid #3a3835; border-radius: 4px; background: transparent; cursor: pointer; }
    .settings .field input[type=number],
    .settings .field input[type=text] {
      width: 100%; padding: 0.25rem 0.5rem; background: #2a2825; color: #e8dec8;
      border: 1px solid #3a3835; border-radius: 4px; font-family: ui-monospace, 'SF Mono', monospace; font-size: 12px;
    }
    .settings .field input[type=range] { width: 100%; }
    .settings .field .hex { font-family: ui-monospace, 'SF Mono', monospace; font-size: 11px; color: #8a7e6e; text-align: right; }
    .settings .field .val { font-family: ui-monospace, 'SF Mono', monospace; font-size: 11px; color: #8a7e6e; text-align: right; }
    .settings .reset { margin-top: 1rem; padding: 0.4rem 0.75rem; background: transparent; color: #888; border: 1px solid #3a3835; border-radius: 4px; cursor: pointer; font-size: 11px; }
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
        <button class="reset" id="reset-btn" type="button">reset to defaults</button>
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
  const [mdResp, themeResp, defResp] = await Promise.all([
    fetch('/content.md'),
    fetch('/theme.json'),
    fetch('/theme.defaults.json'),
  ]);
  md.value = await mdResp.text();
  theme = await themeResp.json();
  defaults = await defResp.json();
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
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(theme, null, 2)
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
  theme = JSON.parse(JSON.stringify(defaults));
  renderSettingsForm();
  saveTheme();
});

// settings form schema -- mirrors theme.json shape
const FIELDS = [
  { section: 'colors', title: 'colors', fields: [
    { key: 'bg', label: 'background', type: 'color' },
    { key: 'fg', label: 'text', type: 'color' },
    { key: 'muted', label: 'italic asides', type: 'color' },
    { key: 'accent', label: 'accent / links', type: 'color' },
    { key: 'hover', label: 'link hover', type: 'color' },
  ]},
  { section: 'sizes', title: 'sizes', fields: [
    { key: 'font-size-base-px', label: 'body size (px)', type: 'number', min: 10, max: 48, step: 1 },
    { key: 'font-size-h1-rem', label: 'h1 size (rem)', type: 'number', min: 1, max: 12, step: 0.1 },
    { key: 'font-size-h2-rem', label: 'h2 size (rem)', type: 'number', min: 0.6, max: 4, step: 0.05 },
    { key: 'max-width-px', label: 'content max-width (px)', type: 'number', min: 320, max: 1400, step: 10 },
    { key: 'line-height-body', label: 'body line-height', type: 'number', min: 1.0, max: 2.2, step: 0.05 },
  ]},
  { section: 'marauder', title: 'marauder axes', fields: [
    { key: 'opsz-body', label: 'body opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'opsz-h2', label: 'h2 opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'opsz-h1', label: 'h1 opsz (6-72)', type: 'range', min: 6, max: 72, step: 1 },
    { key: 'weight-body', label: 'body weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
    { key: 'weight-h2', label: 'h2 weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
    { key: 'weight-h1', label: 'h1 weight (100-900)', type: 'range', min: 100, max: 900, step: 50 },
  ]},
];

function renderSettingsForm() {
  settingsForm.innerHTML = '';
  for (const group of FIELDS) {
    const h = document.createElement('h3');
    h.textContent = group.title;
    settingsForm.appendChild(h);
    const div = document.createElement('div');
    div.className = 'group';
    for (const f of group.fields) {
      const row = document.createElement('div');
      row.className = 'field';
      const lbl = document.createElement('label');
      lbl.textContent = f.label;
      row.appendChild(lbl);
      const input = document.createElement('input');
      input.type = f.type;
      const current = theme[group.section][f.key];
      if (f.type === 'color') {
        input.value = current;
      } else {
        input.value = current;
        if (f.min !== undefined) input.min = f.min;
        if (f.max !== undefined) input.max = f.max;
        if (f.step !== undefined) input.step = f.step;
      }
      row.appendChild(input);
      const val = document.createElement('span');
      val.className = f.type === 'color' ? 'hex' : 'val';
      val.textContent = current;
      row.appendChild(val);
      input.addEventListener('input', () => {
        const raw = input.value;
        const v = f.type === 'number' || f.type === 'range' ? parseFloat(raw) : raw;
        theme[group.section][f.key] = v;
        val.textContent = v;
        dirty = true;
        setStatus('editing theme...');
        clearTimeout(saveThemeTimer);
        saveThemeTimer = setTimeout(saveTheme, 350);
      });
      div.appendChild(row);
    }
    settingsForm.appendChild(div);
  }
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
    },
}


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
