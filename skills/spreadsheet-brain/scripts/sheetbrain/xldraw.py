"""The brain drawn as native Excel shapes, for the `_brain` tab.

drawing_parts(graph, origin_emu_x=..., first_col=...) turns the graph JSON that
sheetbrain.graph.build returns into the DrawingML of one drawing part:

  * a dark canvas with a title, a one-line key and a side panel
  * a dot for every thing in the sheet (vendors, hotels, items, accounts, model
    rows), sized by how much it matters and colored by its group, with its name
    under it; dot and name are one group, so they move together
  * what the owner said about a thing sits right next to its dot as a note
    shape, joined to it by a line in the note color. What the owner said about
    the whole file is listed in the side panel instead of circling the file dot
  * what the sender of a file said (not checked) looks different on purpose:
    a dashed outline in its own color, never the owner's filled note
  * what code found, open questions and guesses are small numbered markers on
    the dots they are about; the side panel lists each one in full, by number
  * connector lines bound to the shapes they join (stCxn and endCxn), so
    dragging a dot in Excel drags its lines with it
  * a line at the bottom pointing to the notes table on the left

The layout is a force-directed simulation in pure Python with a fixed start.
Everything is put in a fixed order (by id) before the layout runs, so the same
brain draws the same picture whatever order its records arrive in, and a brain
saved twice without changes leaves the file byte-identical.

Placement: every shape is anchored to a cell of a grid of narrow columns to
the right of the table (the brain tab sets their width and pins its row
height). Cell anchors keep the drawing next to the table on Windows and on a
Mac, whatever the workbook's default font. Lines run centre to centre behind
the dots, so where a platform renders columns a little wider or narrower each
line still ends under its dot.

All text comes from spreadsheets and is untrusted: it is cleaned (invisible and
XML-illegal characters removed), clipped and XML-escaped. No hyperlink, macro,
cell link or field is ever written, so no text can become a link or a formula.

Stdlib only.
"""
from __future__ import annotations

import json
import math
import re
from xml.sax.saxutils import escape as _esc

NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.drawing+xml"
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"

EMU = 9525                 # EMU per design pixel (96 dpi); 1 px = 0.75 pt
COL_CHARS = 2.25           # drawing grid column width, in Excel's character units
COL_EMU = 165100           # how wide such a column renders: 13 pt (measured in Excel for Mac, Calibri 11)
ROW_EMU = 190500           # 15 pt rows; the brain tab pins its row height when it has a drawing
CANVAS_W = 1800            # design pixels
CANVAS_H = 960
HEADER_H = 74              # title and key
FOOTER_H = 36              # the pointer to the notes table
MARGIN = 30
PANEL_W = 340              # the side panel: what was said about the whole file, and the numbered notes
PANEL_GAP = 20
MAX_NODES = 100            # dots, note shapes and markers, not counting lines
MAX_LINES = 240
MAX_SAID = 16              # owner notes drawn next to their dots
MAX_SENDER = 16            # the sender's notes drawn next to their dots
MAX_FOUND = 6              # what code found, listed (and marked on its dots)
MAX_QUESTIONS = 4
MAX_GUESSES = 2
MAX_PANEL_SAID = 10        # the owner's notes about the whole file, listed in the panel
MAX_PANEL_SENDER = 6       # a sender's: fewer, so what code found still has room
MAX_SHEETS = 8             # more tabs than this and only the ones things hang off are drawn
LAYOUT_STEPS = 360

BG = "121211"              # canvas, close to the map's own background
PANEL_BG = "1A1A18"
INK = "E6E4DE"             # names of the big things
INK_DIM = "9C9A93"         # names of small things
LINE = "FFFFFF"
_FALLBACK = {"file": "F2F1EC", "sheet": "8C8F96", "said": "4F7CFF", "sender": "D9925B", "found": "E0B354",
             "guess": "8A8F98", "question": "C678DD", "web": "4FB3C8", "other file": "B8B2A7"}
_ALERT = {"disputed": "E5534B", "left-out": "7A7F87", "may-be-outdated": "E0B354"}
_FLAGGED = ("disputed", "left-out", "may-be-outdated")

# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------
_ILLEGAL_XML = re.compile("[\x00-\x08\x0B\x0C\x0E-\x1F￾￿]|[\ud800-\udfff]")
_INVISIBLE = re.compile("[\U000E0000-\U000E007F\u202a-\u202e\u2066-\u2069\u200b-\u200d\u2060\ufeff]")


def clean(s, limit: int | None = None) -> str:
    """Untrusted text to one plain line: invisible and XML-illegal characters
    removed, whitespace collapsed, clipped at a word with '...'."""
    s = "" if s is None else str(s)
    s = _INVISIBLE.sub("", s)
    s = _ILLEGAL_XML.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit - 1].rsplit(" ", 1)[0] if " " in s[:limit - 1] else s[:limit - 1]
        s = cut.rstrip(",;:(-") + "..."
    return s


def xml_text(s: str) -> str:
    return _esc(s)


def xml_attr(s: str) -> str:
    return _esc(s, {'"': "&quot;", "'": "&apos;"})


def _hex(c, fallback: str = "8C8F96") -> str:
    c = str(c or "").strip().lstrip("#")
    return c.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", c) else fallback


def _mix(a: str, b: str, t: float) -> str:
    """Color a moved t of the way to color b (both 6 hex digits)."""
    pa = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(pa, pb))


def _ink_on(fill: str) -> str:
    """Dark text on a light fill, white text on a dark one."""
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return "1D1C1A" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else "FFFFFF"


# Calibri advance widths for ' ' to '~', in thousandths of an em (read from the font).
# Shape text is pinned to Calibri, so these are the widths Excel lays out with.
_W_REG = [226, 326, 401, 498, 507, 715, 682, 221, 303, 303, 498, 498, 250, 306, 252, 386, 507, 507, 507,
          507, 507, 507, 507, 507, 507, 507, 268, 268, 498, 498, 498, 463, 894, 579, 544, 533, 615, 488,
          459, 631, 623, 252, 319, 520, 420, 855, 646, 662, 517, 673, 543, 459, 487, 642, 567, 890, 519,
          487, 468, 307, 386, 307, 498, 498, 291, 479, 525, 423, 525, 498, 305, 471, 525, 229, 239, 455,
          229, 799, 525, 527, 525, 525, 349, 391, 335, 525, 452, 715, 433, 453, 395, 314, 460, 314, 498]
_W_BOLD = [226, 326, 438, 498, 507, 729, 705, 233, 312, 312, 498, 498, 258, 306, 267, 430, 507, 507, 507,
           507, 507, 507, 507, 507, 507, 507, 276, 276, 498, 498, 498, 463, 898, 606, 561, 529, 630, 488,
           459, 637, 631, 267, 331, 547, 423, 874, 659, 676, 532, 686, 563, 473, 495, 653, 591, 906, 551,
           520, 478, 325, 430, 325, 498, 498, 300, 494, 537, 418, 537, 503, 316, 474, 537, 246, 255, 480,
           246, 813, 537, 538, 537, 537, 355, 399, 347, 537, 473, 745, 459, 474, 397, 344, 475, 344, 498]
FONT = "Calibri"


def text_px(s: str, pt: float, bold: bool = False) -> float:
    """Rendered width of s in design pixels, from Calibri's own advance widths
    (anything outside plain ASCII counts as a wide letter)."""
    table = _W_BOLD if bold else _W_REG
    units = 0
    for ch in s:
        o = ord(ch)
        units += table[o - 32] if 32 <= o <= 126 else 600
    return units / 1000 * pt * 96 / 72


def line_px(pt: float) -> int:
    """Height of one line of text at pt, in design pixels."""
    return int(round(pt * 96 / 72 * 1.22))


def _clip_px(s: str, pt: float, max_w: float, bold: bool = False) -> str:
    if text_px(s, pt, bold) <= max_w:
        return s
    while len(s) > 1 and text_px(s + "...", pt, bold) > max_w:
        s = s[:-1]
    cut = s.rsplit(" ", 1)[0] if " " in s and len(s.rsplit(" ", 1)[0]) > len(s) * 0.6 else s
    return cut.rstrip(",;:( -") + "..."


def note_lines(s: str, pt: float, max_w: float) -> list[str]:
    """One line when it fits, otherwise two lines of about the same width
    (no lone last word), each clipped with '...' if it still runs over."""
    if not s or text_px(s, pt) <= max_w:
        return [s]
    words = s.split(" ")
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        m = max(text_px(a, pt), text_px(b, pt))
        if best is None or m < best[0]:
            best = (m, [a, b])
    lines = best[1] if best else [s]
    out = []
    for ln in lines:
        while len(ln) > 4 and text_px(ln, pt) > max_w:
            ln = ln[:-4].rstrip(",;: ") + "..."
        out.append(ln)
    return out


def wrap_lines(s: str, pt: float, max_w: float, max_lines: int = 3, bold: bool = False,
               first_w: float | None = None) -> list[str]:
    """Words wrapped into at most max_lines lines no wider than max_w (the first
    line no wider than first_w); what does not fit ends the last line with '...'."""
    words = [w for w in (s or "").split(" ") if w]
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        w = words[i]
        room = first_w if (first_w is not None and not lines) else max_w
        trial = (cur + " " + w) if cur else w
        if text_px(trial, pt, bold) <= room:
            cur = trial
            i += 1
            continue
        if not cur:                       # one word wider than the line: cut it
            cur = _clip_px(w, pt, room, bold)
            i += 1
        lines.append(cur)
        cur = ""
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if i < len(words) and lines:
        room = first_w if (first_w is not None and len(lines) == 1) else max_w
        lines[-1] = _clip_px(lines[-1] + " " + " ".join(words[i:]), pt, room, bold)
    return lines or [""]


# --------------------------------------------------------------------------
# what each dot and note says
# --------------------------------------------------------------------------
_KEEP_UPPER = {"LB", "LBS", "OZ", "CT", "EA", "CS", "GAL", "PK", "BIB", "IQF", "USDA", "BBQ", "HOA", "LLC",
               "LLP", "INC", "USA", "AR", "AP", "MRR", "ARPA", "CAC", "COGS", "EBIT", "EBITDA", "PP&E"}


def _title_case(s: str) -> str:
    """An all-capitals name of several words, in Title Case (codes and units stay as they are)."""
    letters = [c for c in s if c.isalpha()]
    words = [w for w in s.split(" ") if any(c.isalpha() for c in w)]
    if len(words) < 2 or len(letters) < 7 or any(c.islower() for c in letters):
        return s
    out = []
    for w in s.split(" "):
        lead = w[:len(w) - len(w.lstrip("("))]
        trail = w[len(w.rstrip(")")):] if w.rstrip(")") != "" else ""
        core = w[len(lead):len(w) - len(trail)]
        if not core or any(c.isdigit() for c in core) or core in _KEEP_UPPER or not core[0].isalpha():
            out.append(w)
        else:
            out.append(lead + core[:1] + core[1:].lower() + trail)
    return " ".join(out)


def _clip_keep_code(s: str, n: int) -> str:
    """Clip a name to n characters, keeping a code in brackets at its end:
    'Chicken Breast Bnls... (BF1842)'."""
    if len(s) <= n:
        return s
    m = re.search(r"\s*(\([^()]{1,14}\))$", s)
    if not m or len(m.group(1)) > n - 8:
        return clean(s, n)
    head = clean(s[:m.start()], n - len(m.group(1)) - 1)
    return f"{head} {m.group(1)}"


def dot_label(n: dict, limit: int = 30) -> str:
    """The name under a dot: readable (Title Case for all-capitals names) and clipped."""
    lab = clean(n.get("label") or n.get("id"))
    if n.get("type") in ("thing", "row"):
        lab = _title_case(lab)
    lim = 40 if n.get("type") in ("file", "kind") else limit
    return _clip_keep_code(lab, lim)


def _unique_labels(nodes: list[dict]) -> dict:
    """id -> label, with the tab (a model row) or the code (a thing) added where two drawn names match."""
    labels = {n["id"]: dot_label(n) for n in nodes}
    seen: dict = {}
    for n in nodes:
        seen.setdefault(labels[n["id"]].lower(), []).append(n)
    for same in seen.values():
        if len(same) < 2:
            continue
        for n in same:
            extra = ""
            if n.get("type") == "row":
                extra = clean(n.get("sheet") or str(n.get("group") or "").replace("rows:", ""))
            else:
                m = re.match(r"^v:[^:]+:(.+)$", n["id"])
                extra = clean(m.group(1)).upper() if m else ""
            if extra and extra.lower() not in labels[n["id"]].lower():
                base = clean(n.get("label") or n["id"])
                base = _title_case(base) if n.get("type") in ("thing", "row") else base
                labels[n["id"]] = _clip_keep_code(f"{base} ({extra[:14]})", 34)
    return labels


_WORDS = re.compile(r"(?:In the owner['’]s words|The owner['’]s words|In their words)\s*:\s*(.+)$",
                    re.I)
_STOCK = ((re.compile(r"^The owner's goal for this data is:\s*", re.I), "Goal"),
          (re.compile(r"^The owner asked to build first:\s*", re.I), "Build first"),
          (re.compile(r"^This model is relied on for, per the owner:\s*", re.I), "Used for"))
_QUOTES = "\"'“”‘’ "


def note_text(n: dict) -> str:
    """What a note says, short and in the speaker's own words when there are
    some: 'Goal: check what I'm charged...' rather than the form's opening."""
    st = clean(n.get("summary") or n.get("label"))
    label = clean(n.get("label"))
    words = ""
    m = _WORDS.search(st)
    if m:
        words = m.group(1).strip(_QUOTES).rstrip(".").strip(_QUOTES).rstrip(".")
        st = st[:m.start()].strip()
    head = ""
    for rx, h in _STOCK:
        if rx.match(st):
            head, st = h, rx.sub("", st)
            break
    if not head:
        hm = re.match(r"^([^:]{2,28}):\s", label)
        if hm and not hm.group(1).lower().startswith("the owner"):
            head = hm.group(1).strip()
    answer = st
    pm = re.search(r",?\s*per the owner", st)
    if pm:
        tail = st[pm.end():]
        cut = tail.rfind(": ")
        if cut >= 0:
            answer = tail[cut + 2:]
            lead = st[:pm.start()].strip()
            if not head and 2 <= len(lead) <= 32:
                head = lead
        else:
            answer = (st[:pm.start()] + tail).strip()
    answer = answer.strip().rstrip(".").strip()
    if answer.startswith('"') and (answer.endswith('"') or answer.count('"') == 1):
        answer = answer[1:-1] if answer.endswith('"') and len(answer) > 1 else answer[1:]   # a quoted answer
    elif answer.count('"') % 2 == 1:         # a quote the clipping left open
        answer = answer.rstrip('"')
    body = answer
    if words:
        if len(answer) <= 48 or words.lower().startswith(("also", "and ", "plus ")):
            body = f"{answer}. {words}" if answer else words
        else:
            body = words
    if head and not body.lower().startswith(head.lower()):
        return f"{head}: {body}"
    return body


def _plain_note(n: dict, kind: str) -> str:
    st = clean(n.get("summary") or n.get("label"))
    if kind == "question":
        st = re.sub(r"^(Not answered yet|The owner was not sure):\s*", "", st)
    return st.rstrip(".")


# --------------------------------------------------------------------------
# what to draw: the plan
# --------------------------------------------------------------------------
def _num(v, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _thing(n: dict) -> bool:
    return n.get("type") in ("thing", "row")


def _kind_of(n: dict) -> str:
    k = str(n.get("note_kind") or "")
    if k in ("said", "sender", "found", "question", "guess", "web"):
        return "found" if k == "web" else k
    g = str(n.get("group") or "")
    return {"said": "said", "sender": "sender", "question": "question", "guess": "guess"}.get(g, "found")


def _ckey(n: dict) -> str:
    return json.dumps(n, sort_keys=True, default=str)


def _canon(graph) -> tuple:
    """(nodes by id, links in a fixed order, each node's place in the graph).
    A duplicate id keeps the same node whatever the order it came in."""
    if not isinstance(graph, dict):
        graph = {}
    nodes: dict = {}
    order: dict = {}
    for i, n in enumerate(graph.get("nodes") or []):
        if not (isinstance(n, dict) and isinstance(n.get("id"), str) and n.get("id")):
            continue
        cur = nodes.get(n["id"])
        if cur is None or _ckey(n) < _ckey(cur):
            nodes[n["id"]] = n
        order.setdefault(n["id"], i)
    links = []
    for lk in graph.get("links") or []:
        if isinstance(lk, dict) and isinstance(lk.get("source"), str) and isinstance(lk.get("target"), str):
            links.append(lk)
    links.sort(key=lambda lk: (lk["source"], lk["target"], str(lk.get("type")), str(lk.get("weight")),
                               str(lk.get("status")), str(lk.get("label"))))
    return nodes, links, order


def plan(graph, max_nodes: int = MAX_NODES) -> dict:
    """Decide what is drawn where. Returns
      hubs, things: the dots (file, kinds, tabs, other files; things and model rows)
      notes: owner and sender notes drawn next to the dots they are about
      markers: numbered found notes, questions and guesses on their dots
      panel: sections of the side panel, each (kind, title, [items])
      dropped: things not drawn; about: note id -> target ids."""
    nodes, links, order = _canon(graph)
    shown = [n for n in nodes.values() if not n.get("hidden")]
    rank = lambda n: (order.get(n["id"], 0), n["id"])  # noqa: E731  graph order, then id
    about: dict = {}
    for lk in links:
        if lk.get("type") == "about" and lk["target"] in nodes and lk["source"] != lk["target"]:
            about.setdefault(lk["source"], [])
            if lk["target"] not in about[lk["source"]]:
                about[lk["source"]].append(lk["target"])
    for k in about:
        about[k].sort()
    files = sorted((n for n in shown if n.get("type") == "file"), key=rank)[:1]
    kinds = sorted((n for n in shown if n.get("type") == "kind"), key=rank)[:16]
    sheets = sorted((n for n in shown if n.get("type") == "sheet"), key=rank)
    others = sorted((n for n in shown if n.get("type") == "other_file"), key=rank)[:6]
    things = [n for n in shown if _thing(n)]
    notes = sorted((n for n in shown if n.get("type") == "note"), key=rank)
    by_kind: dict = {}
    for n in notes:
        by_kind.setdefault(_kind_of(n), []).append(n)
    thing_ids = {n["id"] for n in things}
    kind_ids = {n["id"] for n in kinds} | {n["id"] for n in others}

    def on_map_targets(n, drawn_ids):
        return [t for t in about.get(n["id"], []) if t in drawn_ids]

    hubs = files + kinds + (sheets if len(sheets) <= MAX_SHEETS else []) + others
    room = max(0, max_nodes - len(hubs))
    # notes that are about a thing, a kind or another file sit on the map; the rest go to the panel
    said_map = [n for n in by_kind.get("said", []) if on_map_targets(n, thing_ids | kind_ids)][:MAX_SAID]
    sender_map = [n for n in by_kind.get("sender", []) if on_map_targets(n, thing_ids | kind_ids)][:MAX_SENDER]
    said_map = said_map[:room]
    sender_map = sender_map[:max(0, room - len(said_map))]
    room -= len(said_map) + len(sender_map)
    def reach(n):          # about a thing first, then about a tab or column, then the whole file
        ts = about.get(n["id"], [])
        if any(t in thing_ids and nodes[t].get("status") != "left-out" for t in ts):
            return 0
        return 1 if any((nodes.get(t) or {}).get("type") in ("sheet", "column", "kind", "other_file")
                        for t in ts) else 2
    listed = {k: sorted(by_kind.get(k, []), key=lambda n: (reach(n), rank(n)))[:cap] for k, cap in
              (("found", MAX_FOUND), ("question", MAX_QUESTIONS), ("guess", MAX_GUESSES))}
    want_marks = sum(1 for k in listed for n in listed[k] if on_map_targets(n, thing_ids))
    thing_room = max(0, room - min(want_marks, room // 3))
    # the things: the ones notes are about and the flagged ones first, then a fair share of each
    # kind (every member of a small kind before the biggest of a big one), biggest first
    must = []
    for n in said_map + sender_map:
        must += [t for t in about.get(n["id"], []) if t in thing_ids]
    must += sorted(n["id"] for n in things if n.get("status") in _FLAGGED)
    chosen: list = []
    for t in must:
        if t not in chosen and len(chosen) < thing_room:
            chosen.append(t)
    groups: dict = {}
    for n in sorted(things, key=lambda n: (-_num(n.get("size"), 0.0), n["id"])):
        if n["id"] not in chosen:
            groups.setdefault(str(n.get("group") or ""), []).append(n["id"])
    left = thing_room - len(chosen)
    order_g = sorted(groups, key=lambda g: (len(groups[g]), g))
    for gi, g in enumerate(order_g):
        share = left // max(1, len(order_g) - gi)
        take = groups[g][:max(0, share)]
        chosen += take
        left -= len(take)
    drawn_things = [nodes[i] for i in chosen]
    dropped = len(things) - len(drawn_things)
    if len(sheets) > MAX_SHEETS:           # only the tabs the drawn rows and kinds hang off
        parents = {lk["target"] for lk in links if lk.get("type") == "part_of"
                   and lk["source"] in set(chosen) | {n["id"] for n in kinds}}
        hubs += [n for n in sheets if n["id"] in parents][:MAX_SHEETS]
    dot_ids = {n["id"] for n in hubs} | set(chosen)
    map_ids = {n["id"] for n in drawn_things} | kind_ids
    said_map = [n for n in said_map if on_map_targets(n, map_ids)]
    sender_map = [n for n in sender_map if on_map_targets(n, map_ids)]
    room = max(0, max_nodes - len(dot_ids) - len(said_map) - len(sender_map))
    # markers: a found note, question or guess about a drawn thing that is not left out of totals
    live = {n["id"] for n in drawn_things if n.get("status") != "left-out"}
    markers: list = []
    sections = []
    num = 0
    said_panel = [n for n in by_kind.get("said", []) if n not in said_map]
    sender_panel = [n for n in by_kind.get("sender", []) if n not in sender_map]
    said_panel = sorted(said_panel, key=lambda n: (0 if "goal" in n["id"].lower() else 1, rank(n)))[:MAX_PANEL_SAID]
    sender_panel = sorted(sender_panel, key=lambda n: (0 if "goal" in n["id"].lower() else 1,
                                                       rank(n)))[:MAX_PANEL_SENDER]
    if said_panel:
        sections.append(("said", "What the owner said about the whole file", said_panel))
    if sender_panel:
        sections.append(("sender", "What the sender said about the whole file (not checked)", sender_panel))
    titles = {"found": "What code found", "question": "Open questions", "guess": "Guesses to confirm"}
    for kind in ("found", "question", "guess"):
        items = sorted(listed[kind], key=lambda n: (0 if on_map_targets(n, live) else 1, rank(n)))
        if not items:
            continue
        entries = []
        for n in items:
            tg = on_map_targets(n, live)
            if tg and len(markers) < room:
                num += 1
                markers.append({"node": n, "num": num, "targets": tg, "kind": kind})
                entries.append((n, num))
            else:
                entries.append((n, None))
        sections.append((kind, titles[kind], entries))
    counts = {k: len(v) for k, v in by_kind.items()}          # everything the brain holds, by kind
    in_view = len(said_map) + len(said_panel) + len(sender_map) + len(sender_panel) + \
        sum(len(v) for v in listed.values())
    counts.update({k: len(v) for k, v in listed.items()})
    return {"hubs": hubs, "things": drawn_things, "notes": said_map + sender_map, "markers": markers,
            "panel": sections, "dropped": dropped, "about": about, "nodes": nodes, "links": links,
            "counts": counts, "things_total": len(things), "table_only": max(0, len(notes) - in_view)}


def pick(graph: dict, max_nodes: int = MAX_NODES) -> list[dict]:
    """The dots, note shapes and markers drawn on the map, in drawing order."""
    p = plan(graph, max_nodes)
    return p["hubs"] + p["things"] + p["notes"] + [m["node"] for m in p["markers"]]


# --------------------------------------------------------------------------
# which lines to draw
# --------------------------------------------------------------------------
_LINE_RANK = {"about": 0, "mark": 1, "relates": 1, "feeds": 1, "same_as": 1, "joins_on": 2,
              "derived_from": 2, "links_file": 2, "looks_up": 2, "part_of": 4}


def lines_for(graph: dict, drawn: list[dict], p: dict | None = None) -> list[dict]:
    """Lines between drawn dots and from each drawn note or marker to what it is
    about. A line to a dot that is not drawn goes to the nearest drawn dot it
    belongs to (an item to its kind, a tab to the file), so nothing floats loose.
    One line per pair, in a fixed order."""
    p = p or plan(graph)
    ids = {n["id"] for n in drawn}
    notes = {n["id"]: n for n in p["notes"]}
    marks = {m["node"]["id"]: m for m in p["markers"]}
    parent: dict = {}
    for lk in p["links"]:
        if lk.get("type") == "part_of":
            parent.setdefault(lk["source"], lk["target"])
    file_id = next((n["id"] for n in drawn if n.get("type") == "file"), None)

    def resolve(i):
        hops = 0
        while i is not None and i not in ids and hops < 12:
            i = parent.get(i)
            hops += 1
        return i if i in ids else file_id

    best: dict = {}
    for lk in p["links"]:
        typ = str(lk.get("type") or "relates")
        src = lk["source"]
        if typ == "about" or src in p["about"]:
            continue                       # notes get their own lines below
        a, b = resolve(src), resolve(lk["target"])
        if not a or not b or a == b or a in notes or b in notes or a in marks or b in marks:
            continue
        key = (a, b) if a < b else (b, a)
        rank = _LINE_RANK.get(typ, 2)
        w = max(0.0, _num(lk.get("weight"), 1.0))
        cur = best.get(key)
        rec = {"a": a, "b": b, "type": typ, "weight": w, "status": str(lk.get("status") or "current"),
               "rank": rank}
        if cur is None or rank < cur["rank"] or (rank == cur["rank"] and w > cur["weight"]):
            best[key] = dict(rec, weight=max(w, cur["weight"]) if cur and cur["rank"] == rank else w)
    for nid, n in sorted(notes.items()):
        for t in p["about"].get(nid, []):
            if t in ids:
                best[(nid, t)] = {"a": nid, "b": t, "type": "about", "weight": 1.0, "rank": 0,
                                  "status": str(n.get("status") or "current"), "kind": _kind_of(n)}
    for nid, m in sorted(marks.items()):
        for t in m["targets"]:
            if t in ids:
                best[(nid, t)] = {"a": nid, "b": t, "type": "mark", "weight": 1.0, "rank": 1,
                                  "status": "current", "kind": m["kind"]}
    lines = [best[k] for k in sorted(best)]
    linked = {x for ln in lines for x in (ln["a"], ln["b"])}
    for n in sorted(drawn, key=lambda n: n["id"]):
        if n["id"] not in linked and file_id and n["id"] != file_id and n["id"] not in marks:
            lines.append({"a": n["id"], "b": file_id, "type": "part_of", "weight": 0.5,
                          "status": "current", "rank": 4})
    if len(lines) > MAX_LINES:
        keep = sorted(range(len(lines)), key=lambda i: (lines[i]["rank"], -lines[i]["weight"], i))
        lines = [lines[i] for i in sorted(keep[:MAX_LINES])]
    return lines


# --------------------------------------------------------------------------
# how each dot looks
# --------------------------------------------------------------------------
def _style(n: dict, colors: dict) -> dict:
    typ = n.get("type")
    kind = _kind_of(n) if typ == "note" else None
    group = str(n.get("group") or "")
    color = colors.get(kind or group) or colors.get(group) or _FALLBACK.get(kind or group) or "8C8F96"
    st = {"color": color, "shape": "dot", "label_pt": 8.0, "bold": False, "ink": INK}
    size = max(0.0, _num(n.get("size"), 6.0))
    if typ == "file":
        st.update(d=36, label_pt=11.0, bold=True, color=_hex(colors.get("file"), "F2F1EC"))
    elif typ == "kind":
        st.update(d=26, label_pt=10.0, bold=True)
    elif typ == "sheet":
        st.update(d=17, label_pt=8.0, ink=INK_DIM)
    elif typ == "other_file":
        st.update(d=18, label_pt=8.5)
    elif typ == "note":
        st.update(shape="note" if kind == "said" else "sender", label_pt=8.0)
    else:
        d = 9 + 0.7 * min(size, 34)
        st.update(d=round(d), label_pt=8.5 if d >= 20 else 7.5, ink=INK if d >= 17 else INK_DIM)
    return st


def _label(n: dict) -> str:
    if n.get("type") == "note":
        return clean(note_text(n), 160)
    return dot_label(n)


# --------------------------------------------------------------------------
# layout: a small deterministic force simulation (d3-force style)
# --------------------------------------------------------------------------
_REST = {"part_of": 64, "about": 40, "relates": 150, "feeds": 82, "joins_on": 150,
         "derived_from": 150, "same_as": 56, "links_file": 120, "looks_up": 120}
_TYPE_ORDER = {"file": 0, "kind": 1, "sheet": 2, "other_file": 3, "thing": 4, "row": 4, "note": 5}


def layout(items: list[dict], lines: list[dict], *, steps: int = LAYOUT_STEPS) -> None:
    """Positions (x, y) in design pixels, written into each item. Deterministic:
    fixed start, fixed order, no randomness."""
    n = len(items)
    if not n:
        return
    idx = {it["id"]: i for i, it in enumerate(items)}
    deg = [0] * n
    edges = []
    for ln in lines:
        a, b = idx.get(ln["a"]), idx.get(ln["b"])
        if a is None or b is None:
            continue
        deg[a] += 1
        deg[b] += 1
        edges.append((a, b, ln))
    # start: hubs on a ring around the file, members near their hub
    hub_types = ("file", "kind", "sheet", "other_file")
    hubs = [i for i, it in enumerate(items) if it["type"] in hub_types and it["type"] != "file"]
    x = [0.0] * n
    y = [0.0] * n
    placed = [False] * n
    for i, it in enumerate(items):
        if it["type"] == "file":
            placed[i] = True
    for k, i in enumerate(hubs):
        ang = 2 * math.pi * k / max(1, len(hubs)) + 0.35
        x[i], y[i] = 260 * math.cos(ang), 190 * math.sin(ang)
        placed[i] = True
    nbrs: list = [[] for _ in range(n)]
    for a, b, ln in edges:
        nbrs[a].append(b)
        nbrs[b].append(a)
    golden = math.pi * (3 - math.sqrt(5))
    count = [0] * n
    for _round in range(3):
        for i in range(n):
            if placed[i]:
                continue
            home = next((j for j in nbrs[i] if placed[j]), None)
            if home is None and _round < 2:
                continue
            count_h = count[home] if home is not None else i
            if home is not None:
                count[home] += 1
            hx, hy = (x[home], y[home]) if home is not None else (0.0, 0.0)
            r = 26 + 14 * math.sqrt(count_h + 1)
            ang = (count_h + 1) * golden + i * 0.001
            x[i], y[i] = hx + r * math.cos(ang), hy + r * math.sin(ang)
            placed[i] = True
    # a note shape is wide: it springs from its edge and keeps a wider berth
    wide = [it["shape"] != "dot" for it in items]
    rad = [it["w"] * 0.3 if wide[i] else it["r"] for i, it in enumerate(items)]
    halo = [(it["w"] * 0.42 + 8) if wide[i] else
            it["r"] + 6 + min(it.get("label_w", 0) * 0.28, 34) for i, it in enumerate(items)]
    charge = []
    for i, it in enumerate(items):
        c = -260.0
        if it["type"] in ("kind", "file"):
            c = -620.0
        elif it["type"] in ("sheet", "other_file"):
            c = -420.0
        elif wide[i]:
            c = -360.0
        charge.append(c)
    vx = [0.0] * n
    vy = [0.0] * n
    alpha, alpha_min = 1.0, 0.001
    decay = 1 - alpha_min ** (1 / steps)
    for _step in range(steps):
        alpha += (0.0 - alpha) * decay
        # springs
        for a, b, ln in edges:
            typ = ln["type"]
            rest = _REST.get(typ, 100) + rad[a] + rad[b]
            if typ == "part_of" and items[b]["type"] in ("sheet", "file") and items[a]["type"] in ("kind", "sheet"):
                rest += 70
            strength = 1.0 / max(1, min(deg[a], deg[b]))
            if typ == "relates":
                strength *= 0.25
            elif typ == "feeds":
                strength *= 0.55
            elif typ == "about":
                strength = max(strength, 0.6)
            dx = x[b] + vx[b] - x[a] - vx[a]
            dy = y[b] + vy[b] - y[a] - vy[a]
            dist = math.sqrt(dx * dx + dy * dy) or 1e-6
            f = (dist - rest) / dist * alpha * strength
            bias = deg[a] / (deg[a] + deg[b])
            vx[b] -= dx * f * bias
            vy[b] -= dy * f * bias
            vx[a] += dx * f * (1 - bias)
            vy[a] += dy * f * (1 - bias)
        # repulsion (every pair; n is small)
        for i in range(n):
            xi, yi = x[i], y[i]
            for j in range(i + 1, n):
                dx = x[j] - xi
                dy = y[j] - yi
                l2 = dx * dx + dy * dy
                if l2 < 1:
                    dx, dy, l2 = (0.5 + (i % 3)) * 0.37, (0.5 + (j % 3)) * 0.29, 0.5
                if l2 > 640000:
                    continue
                wi = charge[j] * alpha / l2
                wj = charge[i] * alpha / l2
                vx[i] += dx * wi
                vy[i] += dy * wi
                vx[j] -= dx * wj
                vy[j] -= dy * wj
        # gentle pull to the middle, wider than tall
        for i in range(n):
            vx[i] -= x[i] * 0.022 * alpha
            vy[i] -= y[i] * 0.034 * alpha
        # no two dots on top of each other
        for i in range(n):
            for j in range(i + 1, n):
                dx = x[j] + vx[j] - x[i] - vx[i]
                dy = y[j] + vy[j] - y[i] - vy[i]
                rr = halo[i] + halo[j]
                l2 = dx * dx + dy * dy
                if l2 < rr * rr:
                    dist = math.sqrt(l2) or 1e-6
                    push = (rr - dist) / dist * 0.5
                    vx[i] -= dx * push * 0.5
                    vy[i] -= dy * push * 0.5
                    vx[j] += dx * push * 0.5
                    vy[j] += dy * push * 0.5
        for i in range(n):
            vx[i] *= 0.6
            vy[i] *= 0.6
            x[i] += vx[i]
            y[i] += vy[i]
    # turn the picture so its long side runs across the canvas
    mx, my = sum(x) / n, sum(y) / n
    cxx = sum((a - mx) ** 2 for a in x)
    cyy = sum((b - my) ** 2 for b in y)
    cxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    theta = 0.5 * math.atan2(2 * cxy, cxx - cyy)
    ct, st = math.cos(-theta), math.sin(-theta)
    for i in range(n):
        a, b = x[i] - mx, y[i] - my
        items[i]["x"], items[i]["y"] = a * ct - b * st, a * st + b * ct


def _ext(it) -> tuple:
    """Half width, reach up and reach down of an item (a dot with its name under it, or a box)."""
    if it["shape"] != "dot":
        return it["w"] / 2, it["h"] / 2, it["h"] / 2
    return max(it["r"], it.get("label_w", 0) / 2), it["r"], it["r"] + 3 + it.get("label_h", 0)


def fit(items: list[dict], left: float, top: float, right: float, bottom: float,
        movers: list[dict] | None = None) -> None:
    """Scale and centre the dots into the box, then push apart any dots or names
    that still overlap, keeping everything inside the box. movers (the notes)
    are moved by the same scaling, so they keep their side of their dot."""
    if not items:
        return
    xs = [it["x"] for it in items]
    ys = [it["y"] for it in items]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad_x = max(_ext(it)[0] for it in items)
    pad_y = max(max(_ext(it)[1], _ext(it)[2]) for it in items)
    aw = max(1.0, right - left - 2 * pad_x)
    ah = max(1.0, bottom - top - 2 * pad_y)
    sx = aw / max(1e-6, maxx - minx)
    sy = ah / max(1e-6, maxy - miny)
    s = min(sx, sy, 1.7)                # a small brain stays together instead of filling the corners
    sx_, sy_ = min(sx, s * 1.45), min(sy, s * 1.45)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    mid_x, mid_y = (left + right) / 2, (top + bottom) / 2
    for it in items + list(movers or []):
        it["x"] = mid_x + (it["x"] - cx) * sx_
        it["y"] = mid_y + (it["y"] - cy) * sy_
    # overlap removal on boxes (dot plus its name); names side by side need a wide gap
    # or they read as one phrase
    gap = 16
    for _pass in range(90):
        moved = False
        for i in range(len(items)):
            a = items[i]
            ahw, aup, adn = _ext(a)
            for j in range(i + 1, len(items)):
                b = items[j]
                bhw, bup, bdn = _ext(b)
                ox = min(a["x"] + ahw, b["x"] + bhw) - max(a["x"] - ahw, b["x"] - bhw) + gap
                oy = min(a["y"] + adn, b["y"] + bdn) - max(a["y"] - aup, b["y"] - bup) + gap * 0.5
                if ox <= 0 or oy <= 0:
                    continue
                moved = True
                if ox * 0.8 < oy:
                    d = ox / 2 + 0.5
                    if a["x"] < b["x"] or (a["x"] == b["x"] and i < j):
                        a["x"] -= d
                        b["x"] += d
                    else:
                        a["x"] += d
                        b["x"] -= d
                else:
                    d = oy / 2 + 0.5
                    if a["y"] < b["y"] or (a["y"] == b["y"] and i < j):
                        a["y"] -= d
                        b["y"] += d
                    else:
                        a["y"] += d
                        b["y"] -= d
        for it in items:
            hw, up, dn = _ext(it)
            it["x"] = min(max(it["x"], left + hw), right - hw)
            it["y"] = min(max(it["y"], top + up), bottom - dn)
        if not moved:
            break


def _box(it, pad: float = 0.0) -> tuple:
    hw, up, dn = _ext(it)
    return (it["x"] - hw - pad, it["y"] - up - pad, it["x"] + hw + pad, it["y"] + dn + pad)


def _hits(b, boxes) -> float:
    """How much box b overlaps the boxes (area)."""
    tot = 0.0
    for o in boxes:
        ox = min(b[2], o[2]) - max(b[0], o[0])
        oy = min(b[3], o[3]) - max(b[1], o[1])
        if ox > 0 and oy > 0:
            tot += ox * oy
    return tot


def snap(sat: dict, targets: list[dict], boxes: list, area: tuple) -> None:
    """Put a note or marker right next to what it is about: beside its one dot
    (the side the layout gave it first, then the others), or in the middle of
    its dots, then further out, at the first spot where it covers nothing."""
    hw, up, dn = _ext(sat)
    left, top, right, bottom = area
    if len(targets) == 1:
        t = targets[0]
        thw, tup, tdn = _ext(t)
        tx, ty, r = t["x"], t["y"], t["r"]
        gap = 7 if sat["shape"] == "mark" else 9
        sides = [(tx + r + gap + hw, ty), (tx - r - gap - hw, ty),
                 (tx, ty - tup - gap - dn), (tx, ty + tdn + gap + up),
                 (tx + thw + gap + hw, ty - tup - gap - dn + 4), (tx - thw - gap - hw, ty - tup - gap - dn + 4),
                 (tx + thw + gap + hw, ty + tdn + gap + up - 4), (tx - thw - gap - hw, ty + tdn + gap + up - 4)]
        if sat["shape"] == "mark":
            k = r + gap + hw
            sides = [(tx + k * 0.8, ty - k * 0.8), (tx - k * 0.8, ty - k * 0.8), (tx + k, ty), (tx - k, ty),
                     (tx + thw + hw + 3, ty + tdn - up), (tx - thw - hw - 3, ty + tdn - up)] + sides
        want = math.atan2(sat["y"] - ty, sat["x"] - tx)
        near = sides[:8] if sat["shape"] != "mark" else sides[:6]
        near.sort(key=lambda p: abs(math.remainder(math.atan2(p[1] - ty, p[0] - tx) - want, 2 * math.pi)))
        cands = near + sides[len(near):]
        cx, cy = tx, ty
    else:
        cx = sum(t["x"] for t in targets) / len(targets)
        cy = sum(t["y"] for t in targets) / len(targets)
        cands = [(cx, cy)]
    for ring in range(1, 16):          # then further out, in 16 directions
        d = ring * 14
        for k in range(16):
            ang = 2 * math.pi * k / 16
            cands.append((cx + (d + (0 if len(targets) > 1 else 40)) * math.cos(ang),
                          cy + (d + (0 if len(targets) > 1 else 40)) * math.sin(ang)))
    best = None
    for px, py in cands:
        px = min(max(px, left + hw), right - hw)
        py = min(max(py, top + up), bottom - dn)
        b = (px - hw - 3, py - up - 3, px + hw + 3, py + dn + 3)
        h = _hits(b, boxes)
        if h == 0:
            best = (0, px, py)
            break
        if best is None or h < best[0]:
            best = (h, px, py)
    sat["x"], sat["y"] = best[1], best[2]


# --------------------------------------------------------------------------
# DrawingML
# --------------------------------------------------------------------------
class _Ids:
    def __init__(self) -> None:
        self.n = 1

    def next(self) -> int:
        self.n += 1
        return self.n


def _cell(px: float, py: float, first_col: int, first_row: int) -> tuple:
    c, co = divmod(int(round(max(0.0, px) * EMU)), COL_EMU)
    r, ro = divmod(int(round(max(0.0, py) * EMU)), ROW_EMU)
    return first_col + c, co, first_row + r, ro


def _from(px, py, fc, fr, tag="from") -> str:
    c, co, r, ro = _cell(px, py, fc, fr)
    return (f"<xdr:{tag}><xdr:col>{c}</xdr:col><xdr:colOff>{co}</xdr:colOff>"
            f"<xdr:row>{r}</xdr:row><xdr:rowOff>{ro}</xdr:rowOff></xdr:{tag}>")


def _xfrm(x, y, w, h, extra="", flip="") -> str:
    return (f"<a:xfrm{flip}><a:off x=\"{int(round(x * EMU))}\" y=\"{int(round(y * EMU))}\"/>"
            f"<a:ext cx=\"{max(0, int(round(w * EMU)))}\" cy=\"{max(0, int(round(h * EMU)))}\"/>{extra}</a:xfrm>")


def _fill(color: str, alpha: int | None = None) -> str:
    a = f"<a:alpha val=\"{alpha}\"/>" if alpha is not None else ""
    return f"<a:solidFill><a:srgbClr val=\"{color}\">{a}</a:srgbClr></a:solidFill>"


def _ln(w_pt: float, color: str | None, alpha: int | None = None, dash: str | None = None) -> str:
    if color is None:
        return "<a:ln><a:noFill/></a:ln>"
    d = f"<a:prstDash val=\"{dash}\"/>" if dash else ""
    return f"<a:ln w=\"{int(round(w_pt * 12700))}\">{_fill(color, alpha)}{d}</a:ln>"


def _run(text: str, pt: float, color: str, bold: bool = False, glow: bool = True) -> str:
    b = " b=\"1\"" if bold else ""
    g = (f"<a:effectLst><a:glow rad=\"38100\"><a:srgbClr val=\"{BG}\"><a:alpha val=\"85000\"/>"
         f"</a:srgbClr></a:glow></a:effectLst>") if glow else ""
    return (f"<a:r><a:rPr lang=\"en-US\" sz=\"{int(round(pt * 100))}\"{b} dirty=\"0\">{_fill(color)}{g}"
            f"<a:latin typeface=\"{FONT}\"/><a:cs typeface=\"{FONT}\"/></a:rPr><a:t>{xml_text(text)}</a:t></a:r>")


def _body(paras: list, *, wrap: bool, anchor: str = "t", ins: tuple = (0, 0, 0, 0),
          algn: str = "ctr") -> str:
    l, t, r, b = (int(round(v * EMU)) for v in ins)
    bp = (f"<a:bodyPr wrap=\"{'square' if wrap else 'none'}\" lIns=\"{l}\" tIns=\"{t}\" rIns=\"{r}\" "
          f"bIns=\"{b}\" anchor=\"{anchor}\" rtlCol=\"0\" vertOverflow=\"overflow\" horzOverflow=\"overflow\">"
          f"<a:noAutofit/></a:bodyPr>")
    ps = "".join(f"<a:p><a:pPr algn=\"{algn}\"/>{p}</a:p>" for p in paras)
    return f"<xdr:txBody>{bp}<a:lstStyle/>{ps}</xdr:txBody>"


def _sp(sid: int, name: str, descr: str, xfrm: str, geom: str, fill: str, ln: str,
        body: str = "", txbox: bool = False, locks: str = "") -> str:
    d = f" descr=\"{xml_attr(descr)}\"" if descr else ""
    tb = " txBox=\"1\"" if txbox else ""
    return (f"<xdr:sp macro=\"\" textlink=\"\"><xdr:nvSpPr><xdr:cNvPr id=\"{sid}\" name=\"{xml_attr(name)}\"{d}/>"
            f"<xdr:cNvSpPr{tb}>{locks}</xdr:cNvSpPr></xdr:nvSpPr><xdr:spPr>{xfrm}"
            f"<a:prstGeom prst=\"{geom}\"><a:avLst/></a:prstGeom>{fill}{ln}</xdr:spPr>{body}</xdr:sp>")


def _one_anchor(x, y, w, h, fc, fr, inner: str) -> str:
    return (f"<xdr:oneCellAnchor>{_from(x, y, fc, fr)}"
            f"<xdr:ext cx=\"{int(round(w * EMU))}\" cy=\"{int(round(h * EMU))}\"/>{inner}"
            f"<xdr:clientData/></xdr:oneCellAnchor>")


def _two_anchor(x, y, w, h, fc, fr, inner: str) -> str:
    return (f"<xdr:twoCellAnchor>{_from(x, y, fc, fr)}{_from(x + w, y + h, fc, fr, 'to')}{inner}"
            f"<xdr:clientData/></xdr:twoCellAnchor>")


def _text_box(ids, name, x, y, w, h, paras, fc, fr, *, algn="l", anchor="t") -> str:
    return _one_anchor(x, y, w, h, fc, fr, _sp(
        ids.next(), name, "", _xfrm(x, y, w, h), "rect", "<a:noFill/>", _ln(0, None),
        _body(paras, wrap=False, algn=algn, anchor=anchor), txbox=True))


# ellipse connection sites, counter-clockwise from the top (ECMA-376 presetShapeDefinitions)
def _ellipse_site(dx: float, dy: float) -> int:
    ang = math.degrees(math.atan2(-dy, dx))          # y up
    return int(round((ang - 90) / 45)) % 8


def _rect_site(dx: float, dy: float, w: float, h: float) -> int:
    # rect-like presets (rect, roundRect, foldedCorner): 0 top, 1 left, 2 bottom, 3 right
    if abs(dx) * h >= abs(dy) * w:
        return 3 if dx > 0 else 1
    return 2 if dy > 0 else 0


def _legend(items: list[dict], groups: list, alerts: set, extra: list) -> list[tuple]:
    """(symbol, symbol color, words) for each group drawn, in the graph's order,
    then the notes and markers, then what the rings mean."""
    drawn_groups = {}
    for it in items:
        if it["type"] in ("file", "sheet", "note"):
            continue
        drawn_groups.setdefault(str(it["node"].get("group") or ""), it)
    out = []
    for g in groups:
        gid = str(g.get("id") or "")
        if gid in drawn_groups:
            out.append(("●", _hex(g.get("color")), clean(g.get("label") or gid, 28)))
    if any(it["type"] == "sheet" for it in items):
        g = next((g for g in groups if g.get("id") == "sheet"), {})
        out.append(("●", _hex(g.get("color"), "8C8F96"), clean(g.get("label") or "Tabs", 28)))
    out += extra
    if "disputed" in alerts:
        out.append(("○", _ALERT["disputed"], "Red ring: worth a look"))
    if "left-out" in alerts:
        out.append(("●", "5C5F66", "Faded: left out of totals"))
    return out


def _group_label(groups: list, gid: str, fallback: str) -> str:
    for g in groups:
        if str(g.get("id") or "") == gid and g.get("label"):
            return clean(g.get("label"), 44)
    return fallback


def drawing_parts(graph: dict, *, origin_emu_x: int, first_col: int | None = None,
                  first_row: int = 1) -> dict:
    """DrawingML for the brain drawing.

    first_col is the 0-based column whose left edge sits at origin_emu_x (the
    brain tab passes both). Every shape is anchored to cells from there, one
    row down so the drawing clears the frozen header row. When first_col is
    None it is worked out from origin_emu_x with the standard 64 px column.

    Returns {"xml": bytes, "cols": (first, last, width_chars), "rows": n,
    "nodes": n, "lines": n, "shapes": n, "width_px": w, "height_px": h,
    "content_type": ..., "rel_type": ...}.
    """
    if not isinstance(graph, dict):
        graph = {}
    if first_col is None:
        first_col = max(0, int(origin_emu_x) // (64 * EMU))
    groups = [g for g in (graph.get("groups") or []) if isinstance(g, dict)]
    colors = {str(g.get("id")): _hex(g.get("color"), "") for g in groups}
    colors = {k: v for k, v in colors.items() if v}
    p = plan(graph)
    # the fixed order everything is laid out in: by kind of dot, then by id
    dots = sorted(p["hubs"] + p["things"], key=lambda n: (_TYPE_ORDER.get(n.get("type"), 9), n["id"]))
    notes = sorted(p["notes"], key=lambda n: n["id"])
    marks = sorted(p["markers"], key=lambda m: m["num"])
    labels = _unique_labels(dots)
    items = []
    for n in dots:
        st = _style(n, colors)
        d = st["d"]
        items.append({"id": n["id"], "node": n, "type": n.get("type"), "shape": "dot", "style": st,
                      "label": labels[n["id"]], "r": d / 2,
                      "label_w": text_px(labels[n["id"]], st["label_pt"], st["bold"]),
                      "label_h": round(st["label_pt"] * 96 / 72 * 1.25)})
    for n in notes:
        st = _style(n, colors)
        ls = wrap_lines(clean(note_text(n), 400), st["label_pt"], 232, 3)
        # Excel sets small text a little wider than the font's advance widths: leave room
        w = max(text_px(x, st["label_pt"]) for x in ls) * 1.07 + 22
        items.append({"id": n["id"], "node": n, "type": "note", "shape": st["shape"], "style": st,
                      "label": " ".join(ls), "lines": ls, "w": min(262, max(70, w)),
                      "h": 6 + line_px(st["label_pt"]) * len(ls) + 6, "r": 10})
    p_nodes = [it["node"] for it in items]
    lines = lines_for(graph, p_nodes + [m["node"] for m in marks], p)
    layout(items, [ln for ln in lines if ln["type"] != "mark"])
    area = (MARGIN, HEADER_H + 10, CANVAS_W - MARGIN - PANEL_W - PANEL_GAP, CANVAS_H - FOOTER_H)
    dot_items = [it for it in items if it["shape"] == "dot"]
    note_items = [it for it in items if it["shape"] != "dot"]
    fit(dot_items + note_items, *area)   # the notes keep their room next to their dots
    by_id = {it["id"]: it for it in items}
    # the notes and markers go right next to their dots, owner notes first
    boxes = [_box(it, 2) for it in dot_items]
    for it in sorted(note_items, key=lambda it: (0 if it["shape"] == "note" else 1, it["id"])):
        tg = [by_id[t] for t in p["about"].get(it["id"], []) if t in by_id and by_id[t]["shape"] == "dot"]
        if tg:
            snap(it, tg, boxes, area)
        boxes.append(_box(it, 2))
    mark_items = []
    for m in marks:
        n = m["node"]
        color = colors.get(m["kind"]) or _FALLBACK.get(m["kind"], "E0B354")
        it = {"id": n["id"], "node": n, "type": "mark", "shape": "mark", "r": 8.5, "w": 17, "h": 17,
              "num": m["num"], "kind": m["kind"], "color": color}
        tg = [by_id[t] for t in m["targets"] if t in by_id]
        it["x"] = sum(t["x"] for t in tg) / len(tg) + 30
        it["y"] = sum(t["y"] for t in tg) / len(tg) - 30
        snap(it, tg, boxes, area)
        boxes.append(_box(it, 2))
        mark_items.append(it)
        by_id[it["id"]] = it
    ids = _Ids()
    out = []
    fc, fr = first_col, first_row
    # the canvas: a twoCellAnchor, so it stretches with the grid on every platform
    out.append(_two_anchor(0, 0, CANVAS_W, CANVAS_H, fc, fr, _sp(
        ids.next(), "Brain canvas", "", _xfrm(0, 0, CANVAS_W, CANVAS_H), "rect", _fill(BG),
        _ln(0.75, "2A2A27"), locks="<a:spLocks noGrp=\"1\"/>")))
    px0 = CANVAS_W - MARGIN - PANEL_W
    pan_top, pan_bottom = HEADER_H + 10, CANVAS_H - FOOTER_H
    out.append(_two_anchor(px0, pan_top, PANEL_W, pan_bottom - pan_top, fc, fr, _sp(
        ids.next(), "Brain side panel", "", _xfrm(px0, pan_top, PANEL_W, pan_bottom - pan_top), "rect",
        _fill(PANEL_BG), _ln(0.75, "2A2A27"), locks="<a:spLocks noGrp=\"1\"/>")))
    # shape ids first, so lines can point at them
    for it in items + mark_items:
        if it["shape"] == "dot":
            it["gid"] = ids.next()
        it["sid"] = ids.next()
        it["tid"] = ids.next() if it["shape"] == "dot" else None
    said_color = colors.get("said") or _FALLBACK["said"]
    sender_color = colors.get("sender") or _FALLBACK["sender"]
    # lines, behind everything else: the faint skeleton first
    n_lines = 0
    for ln in sorted(lines, key=lambda ln: (0 if ln["type"] == "part_of" else 1)):
        a, b = by_id.get(ln["a"]), by_id.get(ln["b"])
        if not a or not b:
            continue
        x1, y1, x2, y2 = a["x"], a["y"], b["x"], b["y"]
        if abs(x2 - x1) < 0.5 and abs(y2 - y1) < 0.5:
            continue
        typ = ln["type"]
        color, width, alpha, dash = LINE, 0.75, 26000, None
        if typ == "relates":             # heavier (more money, more rows) reads stronger
            wt = min(4.0, ln["weight"])
            width, alpha = 0.5 + 0.45 * wt, int(10000 + 7500 * wt)
        elif typ in ("feeds", "derived_from", "joins_on", "links_file", "same_as", "looks_up"):
            width, alpha = 1.0, 36000
        elif typ == "part_of":          # a thing to its kind or tab: the color already says it
            width, alpha = 0.5, 9000
        elif typ == "about":
            if ln.get("kind") == "sender":
                color, width, alpha, dash = sender_color, 1.25, 85000, "dash"
            else:
                color, width, alpha = said_color, 1.25, 85000
        elif typ == "mark":
            color, width, alpha = a.get("color") or LINE, 0.75, 60000
        if ln["status"] == "disputed" and typ not in ("about", "mark"):
            color, alpha = _ALERT["disputed"], 80000
        elif ln["status"] in ("unconfirmed", "may-be-outdated") and typ not in ("about", "mark"):
            dash = "dash"
        if typ == "same_as":
            dash, alpha = "sysDot", 60000
        sa = _ellipse_site(x2 - x1, y2 - y1) if a["shape"] in ("dot", "mark") else \
            _rect_site(x2 - x1, y2 - y1, a["w"], a["h"])
        sb = _ellipse_site(x1 - x2, y1 - y2) if b["shape"] in ("dot", "mark") else \
            _rect_site(x1 - x2, y1 - y2, b["w"], b["h"])
        flip = (" flipH=\"1\"" if x2 < x1 else "") + (" flipV=\"1\"" if y2 < y1 else "")
        bx, by_ = min(x1, x2), min(y1, y2)
        bw, bh = abs(x2 - x1), abs(y2 - y1)
        cid = ids.next()
        n_lines += 1
        inner = (f"<xdr:cxnSp macro=\"\"><xdr:nvCxnSpPr><xdr:cNvPr id=\"{cid}\" name=\"Line {n_lines}\"/>"
                 f"<xdr:cNvCxnSpPr><a:stCxn id=\"{a['sid']}\" idx=\"{sa}\"/><a:endCxn id=\"{b['sid']}\" idx=\"{sb}\"/>"
                 f"</xdr:cNvCxnSpPr></xdr:nvCxnSpPr><xdr:spPr>{_xfrm(bx, by_, bw, bh, flip=flip)}"
                 f"<a:prstGeom prst=\"straightConnector1\"><a:avLst/></a:prstGeom>{_ln(width, color, alpha, dash)}"
                 f"</xdr:spPr></xdr:cxnSp>")
        out.append(_two_anchor(bx, by_, bw, bh, fc, fr, inner))
    # dots (with their names), then the notes on top of them
    alerts = set()
    for k, it in enumerate(dot_items, 1):
        n, st = it["node"], it["style"]
        status = str(n.get("status") or "current")
        if status in _ALERT:
            alerts.add(status)
        d, r = st["d"], it["r"]
        x0, y0 = it["x"] - r, it["y"] - r
        lh = it["label_h"]
        fill, line = _fill(st["color"]), _ln(1.25, BG)
        if status == "left-out":
            fill, line = _fill(st["color"], 32000), _ln(1.25, st["color"], 70000, "sysDash")
        elif status == "disputed":
            line = _ln(2.25, _ALERT["disputed"])
        elif status == "may-be-outdated":
            line = _ln(2.0, _ALERT["may-be-outdated"])
        dot = _sp(it["sid"], f"Dot {k}: {it['label']}", clean(n.get("summary"), 300), _xfrm(x0, y0, d, d),
                  "ellipse", fill, line)
        ink = st["ink"] if status != "left-out" else INK_DIM
        name = _sp(it["tid"], f"Name {k}", "", _xfrm(x0, y0 + d + 2, d, lh),
                   "rect", "<a:noFill/>", _ln(0, None),
                   _body([_run(it["label"], st["label_pt"], ink, st["bold"])], wrap=False), txbox=True)
        gw, gh = d, d + 2 + lh
        grp = (f"<xdr:grpSp><xdr:nvGrpSpPr><xdr:cNvPr id=\"{it['gid']}\" name=\"{xml_attr('Node ' + str(k) + ': ' + it['label'])}\"/>"
               f"<xdr:cNvGrpSpPr/></xdr:nvGrpSpPr><xdr:grpSpPr>"
               f"{_xfrm(x0, y0, gw, gh, extra=_chx(x0, y0, gw, gh))}</xdr:grpSpPr>{dot}{name}</xdr:grpSp>")
        out.append(_one_anchor(x0, y0, gw, gh, fc, fr, grp))
    for k, it in enumerate(note_items, 1):
        n, st = it["node"], it["style"]
        w, h = it["w"], it["h"]
        x0, y0 = it["x"] - w / 2, it["y"] - h / 2
        if it["shape"] == "note":            # the owner's own note: a filled sticky note
            fill_c = st["color"]
            ink = _ink_on(fill_c)
            body = _body([_run(t, st["label_pt"], ink, glow=False) for t in it["lines"]], wrap=False,
                         anchor="ctr", ins=(9, 4, 9, 4), algn="l")
            line = _ln(1.0, _ALERT["disputed"]) if n.get("status") == "disputed" else \
                _ln(0.75, _mix(fill_c, "000000", 0.35))
            shape = _sp(it["sid"], f"Note {k}: {clean(n.get('label'), 40)}", clean(n.get("summary"), 300),
                        _xfrm(x0, y0, w, h), "foldedCorner", _fill(fill_c), line, body)
        else:                                 # someone else's note, not checked: dashed, never filled
            c = st["color"]
            body = _body([_run(t, st["label_pt"], _mix(c, "FFFFFF", 0.35), glow=False) for t in it["lines"]],
                         wrap=False, anchor="ctr", ins=(9, 4, 9, 4), algn="l")
            shape = _sp(it["sid"], f"Sender note {k}: {clean(n.get('label'), 40)}", clean(n.get("summary"), 300),
                        _xfrm(x0, y0, w, h), "roundRect", _fill(_mix(BG, c, 0.14)), _ln(1.25, c, None, "dash"),
                        body)
        out.append(_one_anchor(x0, y0, w, h, fc, fr, shape))
    for it in mark_items:
        out.append(_marker(it, it["sid"], it["x"], it["y"], fc, fr))
    # the side panel: what was said about the whole file, then the numbered notes
    shown_counts = _panel(out, ids, p, groups, colors, px0, pan_top, pan_bottom, fc, fr)
    # title, key and the pointer to the table
    kind = clean(re.split(r"\s+[.·]\s+", str(graph.get("subtitle") or ""))[0], 40)
    parts = [kind] if kind else []
    total = p["things_total"]
    drawn_things = len(p["things"])
    if total:
        parts.append(f"{drawn_things} things" if drawn_things == total else f"{drawn_things} of {total} things")
    cnt = p["counts"]
    for key_, one, many in (("said", "note from the owner", "notes from the owner"),
                            ("sender", "note from the sender, not checked", "notes from the sender, not checked"),
                            ("found", "finding", "findings"), ("question", "open question", "open questions")):
        if cnt.get(key_):
            parts.append(f"{cnt[key_]} {one if cnt[key_] == 1 else many}")
    title = clean(graph.get("title") or "Spreadsheet", 60)
    sub = clean(" · ".join(parts), 150)
    head = _run(title, 15, "F2F1EC", bold=True, glow=False)
    if sub:
        head += _run("    " + sub, 9, INK_DIM, glow=False)
    out.append(_text_box(ids, "Brain title", MARGIN, 14, CANVAS_W - 2 * MARGIN, 26, [head], fc, fr))
    extra = []
    if any(it["shape"] == "note" for it in note_items) or cnt.get("said"):
        extra.append(("■", said_color, _group_label(groups, "said", "What the owner said")))
    if any(it["shape"] == "sender" for it in note_items) or cnt.get("sender"):
        extra.append(("□", sender_color, _group_label(groups, "sender", "What the sender said (not checked)")))
    for kind_, fb in (("found", "What code found"), ("question", "Open questions"), ("guess", "Guesses to confirm")):
        if any(m["kind"] == kind_ for m in marks):
            extra.append(("●", colors.get(kind_) or _FALLBACK[kind_],
                          _group_label(groups, kind_, fb) + " (numbered)"))
    key_runs, used = [], 0.0
    room = CANVAS_W - 2 * MARGIN - 10
    for sym, col, words in _legend(items, groups, alerts, extra):
        wpx = text_px(sym + " " + words + "     ", 9)
        if used + wpx > room:
            break
        used += wpx
        key_runs.append(_run(sym + " ", 9, col, glow=False) + _run(words + "     ", 9, "C9C7C0", glow=False))
    if key_runs:
        out.append(_text_box(ids, "Brain key", MARGIN, 44, CANVAS_W - 2 * MARGIN, 18, ["".join(key_runs)], fc, fr))
    tip = ("Every note is a row in the table to the left: what it is in B, the note in C, where it came from "
           "in D, its date in E, what it is about in F. Drag a dot and its lines follow.")
    more = []
    if p["dropped"]:
        more.append(f"{p['dropped']} smaller things are not drawn.")
    only = p["table_only"] + shown_counts["hidden"]
    if only:
        more.append(f"{only} more {'note is' if only == 1 else 'notes are'} only in the table.")
    if more:
        tip += " " + " ".join(more)
    out.append(_text_box(ids, "Brain tip", MARGIN, CANVAS_H - 27, CANVAS_W - 2 * MARGIN, 18,
                         [_run(tip, 8.5, "8F8D86", glow=False)], fc, fr))
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
           f"<xdr:wsDr xmlns:xdr=\"{NS_XDR}\" xmlns:a=\"{NS_A}\">" + "".join(out) + "</xdr:wsDr>")
    last_col = first_col + int(math.ceil(CANVAS_W * EMU / COL_EMU)) + 1
    rows = int(math.ceil(CANVAS_H * EMU / ROW_EMU)) + 2
    return {"xml": xml.encode("utf-8"), "cols": (first_col, last_col, COL_CHARS), "rows": rows,
            "nodes": len(items) + len(mark_items), "lines": n_lines, "shapes": len(out), "width_px": CANVAS_W,
            "height_px": CANVAS_H, "content_type": CONTENT_TYPE, "rel_type": REL_TYPE,
            "panel": shown_counts}


def _marker(it: dict, sid: int, cx: float, cy: float, fc: int, fr: int, name: str | None = None) -> str:
    """A small numbered circle: a found note (filled), a question (filled) or a guess (a ring)."""
    d = it.get("w", 17)
    x0, y0 = cx - d / 2, cy - d / 2
    color = it["color"]
    if it["kind"] == "guess":
        fill, line, ink = _fill(BG), _ln(1.5, color), color
    else:
        fill, line, ink = _fill(color), _ln(1.0, BG), _ink_on(color)
    body = _body([_run(str(it["num"]), 7.5, ink, bold=True, glow=False)], wrap=False, anchor="ctr",
                 ins=(0, 0, 0, 0), algn="ctr")
    nm = name or f"Marker {it['num']}: {clean(it['node'].get('label'), 40)}"
    shape = _sp(sid, nm, clean(it["node"].get("summary"), 300), _xfrm(x0, y0, d, d), "ellipse", fill, line, body)
    return _one_anchor(x0, y0, d, d, fc, fr, shape)


def _where(p: dict, n: dict) -> str:
    """For a note about tabs or columns: which ones, in a few words."""
    nodes = p["nodes"]
    cols, tabs = [], []
    for t in p["about"].get(n["id"], []):
        tn = nodes.get(t) or {}
        lab = clean(tn.get("label"), 24)
        if tn.get("type") == "column" and lab and lab not in cols:
            cols.append(lab)
        elif tn.get("type") == "sheet" and lab and lab not in tabs:
            tabs.append(lab)
    if not cols and not tabs:
        return ""
    what = ", ".join(cols[:2]) + (" on " if cols and tabs else "") + ", ".join(tabs[:2])
    return f" ({what})"


def _panel(out: list, ids: _Ids, p: dict, groups: list, colors: dict, px0: float, top: float,
           bottom: float, fc: int, fr: int) -> dict:
    """The side panel, top down: section titles, and each note in full (three
    lines at most) with the same bullet it has on the map. Stops when full."""
    pt, tpt = 8.0, 8.5
    lh = line_px(pt)
    x_bul = px0 + 14
    x_txt = px0 + 34
    w_txt = PANEL_W - 34 - 14
    y = top + 12
    shown, hidden = 0, 0
    sections = p["panel"]
    said_c = colors.get("said") or _FALLBACK["said"]
    sender_c = colors.get("sender") or _FALLBACK["sender"]
    if not sections:
        out.append(_text_box(ids, "Panel empty", x_bul, y, PANEL_W - 28, lh + 4,
                             [_run("Nothing said about the whole file yet.", pt, INK_DIM, glow=False)], fc, fr))
        return {"shown": 0, "hidden": 0}
    for si, (kind, title, entries) in enumerate(sections):
        if y + line_px(tpt) + lh + 10 > bottom - 8:
            hidden += len(entries)
            continue
        col = {"said": said_c, "sender": sender_c}.get(kind) or colors.get(kind) or _FALLBACK.get(kind, INK)
        out.append(_text_box(ids, f"Panel title {si + 1}", x_bul, y, PANEL_W - 28, line_px(tpt) + 2,
                             [_run(title, tpt, _mix(col, "FFFFFF", 0.25), bold=True, glow=False)], fc, fr))
        y += line_px(tpt) + 6
        for e in entries:
            n, num = (e, None) if isinstance(e, dict) else e
            if kind in ("said", "sender"):
                text = note_text(n) + _where(p, n)
                max_l = 3
            else:
                text = _plain_note(n, kind)
                max_l = 2
            ls = wrap_lines(clean(text, 400), pt, w_txt, max_l)
            h = lh * len(ls) + 2
            if y + h > bottom - 8 - (lh + 4):
                hidden += 1
                continue
            cy = y + lh / 2 + 1
            if kind == "said":
                out.append(_one_anchor(x_bul + 3, cy - 5, 10, 10, fc, fr, _sp(
                    ids.next(), f"Panel bullet {shown + 1}", "", _xfrm(x_bul + 3, cy - 5, 10, 10), "foldedCorner",
                    _fill(said_c), _ln(0.5, _mix(said_c, "000000", 0.35)))))
            elif kind == "sender":
                out.append(_one_anchor(x_bul + 3, cy - 5, 10, 10, fc, fr, _sp(
                    ids.next(), f"Panel bullet {shown + 1}", "", _xfrm(x_bul + 3, cy - 5, 10, 10), "roundRect",
                    _fill(_mix(BG, sender_c, 0.14)), _ln(1.0, sender_c, None, "dash"))))
            elif num is not None:
                mk = {"num": num, "kind": kind, "node": n, "w": 15,
                      "color": colors.get(kind) or _FALLBACK.get(kind, "E0B354")}
                out.append(_marker(mk, ids.next(), x_bul + 8, cy, fc, fr, name=f"Panel marker {num}"))
            else:
                c = colors.get(kind) or _FALLBACK.get(kind, "E0B354")
                out.append(_one_anchor(x_bul + 5, cy - 3, 6, 6, fc, fr, _sp(
                    ids.next(), f"Panel bullet {shown + 1}", "", _xfrm(x_bul + 5, cy - 3, 6, 6), "ellipse",
                    _fill(c) if kind != "guess" else "<a:noFill/>", _ln(1.0, c) if kind == "guess" else _ln(0, None))))
            ink = INK if kind in ("said", "sender") else "C9C7C0"
            out.append(_text_box(ids, f"Panel note {shown + 1}", x_txt, y, w_txt + 8, h,
                                 [_run(t, pt, ink, glow=False) for t in ls], fc, fr))
            shown += 1
            y += h + 5
        y += 8
    return {"shown": shown, "hidden": hidden}


def _chx(x, y, w, h) -> str:
    return (f"<a:chOff x=\"{int(round(x * EMU))}\" y=\"{int(round(y * EMU))}\"/>"
            f"<a:chExt cx=\"{int(round(w * EMU))}\" cy=\"{int(round(h * EMU))}\"/>")
