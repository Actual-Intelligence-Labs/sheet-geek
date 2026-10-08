"""Write the map page: one self-contained HTML file per brain.

render(graph, out_path) reads assets/viewer.html, inlines the vendored
force-graph library and the third-party notices, embeds the graph JSON
(version 2, from sheetbrain.graph.build) in a
<script type="application/json" id="graph-data"> block, writes the file and
returns its path.

The page draws the brain the way Obsidian's graph view draws a vault: dots for
the things in the data and the notes about them, lines for how they connect.
Deep links: page.html#focus=<url-encoded node id> opens that dot's note, and
&local=1 (plus &depth=2) shows only that dot and its neighbors.

Everything in the graph is untrusted text. It only ever lands inside the JSON
block, escaped so it cannot close the script element, and the page reads it
with JSON.parse and renders it with textContent or on the canvas. The page
carries a CSP with no network access, so it works from file:// and never
phones home.

Usage without the package:  python render.py <graph.json> <out.html>
Stdlib only. Python 3.10+.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"
TEMPLATE_PATH = ASSETS_DIR / "viewer.html"
FORCE_GRAPH_PATH = ASSETS_DIR / "force-graph.min.js"
NOTICES_PATH = ASSETS_DIR / "THIRD_PARTY_LICENSES.txt"

JS_MARKER = "/*__FORCE_GRAPH_JS__*/"
JSON_MARKER = "__GRAPH_JSON__"
NOTICES_MARKER = "<!--__THIRD_PARTY_NOTICES__-->"

CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:"
)

_SCRIPT_OPEN = re.compile(r"<(?=script)", re.IGNORECASE)


def _clean(value: Any) -> Any:
    """Make the graph strictly JSON-safe: no NaN or Infinity, only JSON types."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def embed_json(graph: dict) -> str:
    """Serialize the graph for a <script type="application/json"> block.

    `</` becomes `<\\/` (the contract rule), and `<!--` and `<script` are
    escaped too, so no string in the data can end the element early or push
    the HTML tokenizer into its escaped-script states. All of these are valid
    JSON escapes, so JSON.parse returns the original text unchanged.
    """
    text = json.dumps(_clean(graph), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    text = text.replace("</", "<\\/")
    text = text.replace("<!--", "\\u003c!--")
    text = _SCRIPT_OPEN.sub(r"\\u003c", text)
    # Line and paragraph separators are legal in JSON; escape them anyway so
    # older parsers and editors never trip on them.
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return text


def _library_js() -> str:
    js = FORCE_GRAPH_PATH.read_text(encoding="utf-8")
    lowered = js.lower()
    if "</script" in lowered or "<!--" in js:
        raise RuntimeError("force-graph.min.js contains a sequence that would break inline embedding")
    return js


def _notices_comment() -> str:
    text = NOTICES_PATH.read_text(encoding="utf-8").strip()
    # Nothing in the notices may end the HTML comment early.
    text = text.replace("--!>", "- -!>").replace("-->", "- ->")
    return "<!--\n" + text + "\n-->"


def build_html(graph: dict) -> str:
    """Return the full viewer page for `graph` as a string."""
    if not isinstance(graph, dict):
        raise TypeError("graph must be a dict (contract 2)")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for marker in (JS_MARKER, JSON_MARKER, NOTICES_MARKER):
        if template.count(marker) != 1:
            raise RuntimeError("viewer.html must contain exactly one %r marker" % marker)
    if CSP not in template:
        raise RuntimeError("viewer.html lost its Content-Security-Policy meta")
    parts = {
        JS_MARKER: _library_js(),
        JSON_MARKER: embed_json(graph),
        NOTICES_MARKER: _notices_comment(),
    }
    pattern = re.compile("|".join(re.escape(m) for m in parts))
    # One pass, so text inserted for one marker is never scanned for another.
    return pattern.sub(lambda m: parts[m.group(0)], template)


def render(graph: dict, out_path: str) -> str:
    """Write the viewer page for `graph` to `out_path` and return the path."""
    html = build_html(graph)
    out = Path(out_path).expanduser()
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sb-render-", suffix=".html", dir=str(out.parent or "."))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(html)
        # mkstemp creates 0600; give the page normal file permissions.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o644 & ~umask)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return str(out)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] in ("-h", "--help"):
        sys.stderr.write("usage: python render.py <graph.json> <out.html>\n")
        return 2
    with open(args[0], encoding="utf-8") as fh:
        graph = json.load(fh)
    print(render(graph, args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
