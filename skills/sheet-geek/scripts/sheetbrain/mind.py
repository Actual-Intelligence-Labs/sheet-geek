"""The brain as a graph of THINGS, the way an Obsidian vault is a graph of notes.

The dots are what the sheet is about (vendors, hotels, items, accounts, classes,
the rows of a model), not its tabs and columns. Lines are how those things
relate ("Vendor A delivers to Site B", "AB123 was re-coded to
AB456"). Every note, whether the owner said it or code counted it, hangs on
the things it is about ("ABC is internal" sits on ABC). The map in a browser
and the drawing on the Excel tab are two views of this one graph; no other app
is needed. The tab's rows keep only the notes, because an AI reads those better
without the graph rows around them.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .profile import norm_key

THING_CAP = 25          # per kind of thing (vendors, locations...): the biggest, by the playbook's size
DETAIL_CAP = 12         # detail kinds the playbook marks as records (items, deals): only the biggest few
EDGES_PER_THING = 4     # the strongest links per thing; the rest would be a hairball
ROW_CAP = 60            # model rows: the most connected, plus every row a note or a problem is about


def thing_id(rid: str, key: str) -> str:
    return f"v:{rid}:{key}"


_ZW = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def _label(v) -> str:
    """A name as it reads: invisible characters (zero-width, bidi marks) gone."""
    return re.sub(r"\s+", " ", _ZW.sub("", str(v))).strip()[:60]


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


class Things:
    """Every value of every kind of thing in one file: what it is called, how big it
    is, whether the owner left it out, and which of them become dots."""

    def __init__(self, analysis, path: str):
        self.a = analysis
        self.path = path
        pb = analysis.playbook or {}
        self.pb_roles = pb.get("roles", {})
        g = pb.get("graph", {}) or {}
        self.mode = g.get("mode") or "entities"
        self.detail = set(g.get("records") or [])
        self.relations = g.get("relations") or []
        roles = analysis.detection["roles"]
        size = roles.get(g.get("size_by") or "")
        self.money = self.pb_roles.get(g.get("size_by") or "", {}).get("unit") == "currency"
        self.size_header = size["header"] if size else ""
        sides = [roles[r] for r in ("debit", "credit") if r in roles and roles[r].get("col") is not None]
        if size is None and len(sides) == 2 and sides[0]["table"] == sides[1]["table"]:
            self.money, self.size_header = True, "dollars moved"      # a ledger: debits plus credits
        self.kinds = []          # (rid, noun, table, col)
        for rid in g.get("entities") or []:
            r = roles.get(rid)
            if not r or r.get("col") is None or analysis.file_of.get(r["table"]) != path or r["col"].sensitive:
                continue
            self.kinds.append((rid, self.pb_roles.get(rid, {}).get("entity") or rid, r["table"], r["col"]))
        self.values: dict = {}   # thing id -> info
        excl = getattr(analysis, "exclusions", {}) or {}
        for rid, noun, tid, c in self.kinds:
            t = analysis.table(tid)
            js = size["col"].j if size and size.get("col") is not None and size["table"] == tid else None
            name_j = self._name_col(t, c, rid)
            left = excl.get((tid, c.j), set())
            other_excl = [(j, vals) for (xt, j), vals in excl.items() if xt == tid and j != c.j]
            for row in t.rows:
                v = row[c.j] if c.j < len(row) else None
                k = norm_key(v)
                if k is None:
                    continue
                tid_ = thing_id(rid, k)
                info = self.values.get(tid_)
                if info is None:
                    name = row[name_j] if name_j is not None and name_j < len(row) and row[name_j] else None
                    label = (_label(name) if name else "") or _label(v) or f"(unnamed {noun})"
                    info = self.values[tid_] = {
                        "id": tid_, "rid": rid, "noun": noun, "key": k, "code": _label(v) or k[:60],
                        "label": label, "amount": 0.0, "rows": 0, "left_out": k in left, "table": tid}
                info["rows"] += 1
                # other owner exclusions (ABC) come off every other thing's size
                if any(norm_key(row[j] if j < len(row) else None) in vals for j, vals in other_excl):
                    continue
                if js is not None:
                    info["amount"] += _num(row[js]) if js < len(row) else 0.0
                elif sides and sides[0]["table"] == tid:
                    info["amount"] += sum(abs(_num(row[s["col"].j])) for s in sides if s["col"].j < len(row))
                else:
                    info["amount"] += 1.0
        # the same description on two codes (a re-coded item) needs the code to tell them apart
        seen = Counter((i["rid"], i["label"]) for i in self.values.values())
        for i in self.values.values():
            if seen[(i["rid"], i["label"])] > 1 and i["code"] != i["label"]:
                i["label"] = f"{i['label']} ({i['code']})"[:80]

    def _name_col(self, t, c, rid):
        """A product shows by its name, not its code, when a column names it one to one."""
        if c.semantic != "identifier":
            return None
        for fd in self.a.fds.get(t.tid, []):
            if fd["from"] == c.header and re.search(r"desc|name|product|title", fd["to"], re.I):
                return t.headers.index(fd["to"])
        return None

    def chosen(self, mentioned: set) -> list:
        """Which values become dots: the biggest of each kind, plus any a note is about."""
        by_kind = defaultdict(list)
        for i in self.values.values():
            by_kind[i["rid"]].append(i)
        out = []
        for rid, items in by_kind.items():
            items.sort(key=lambda i: -i["amount"])
            cap = DETAIL_CAP if rid in self.detail else THING_CAP
            keep = {i["id"] for i in items[:cap]} | {i["id"] for i in items if i["id"] in mentioned or i["left_out"]}
            out += [i for i in items if i["id"] in keep]
        return out

    def find(self, text: str) -> set:
        """Things a sentence names, by code or by name, whole words only."""
        out = set()
        if not text:
            return out
        low = text.lower()
        for i in self.values.values():
            for word in {i["code"], i["label"].split(" (")[0]}:
                w = word.lower()
                if len(w) < 3 or w not in low:
                    continue
                if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", low):
                    out.add(i["id"])
                    break
        return out

    def links(self, ids: set) -> list:
        """(from id, to id, label, amount) between dots, the strongest few per dot."""
        roles = self.a.detection["roles"]
        out, per = [], Counter()
        size = None
        for rel in self.relations:
            a_r, b_r = roles.get(rel.get("from")), roles.get(rel.get("to"))
            if not a_r or not b_r or a_r["table"] != b_r["table"] or a_r.get("col") is None \
                    or b_r.get("col") is None or self.a.file_of.get(a_r["table"]) != self.path:
                continue
            t = self.a.table(a_r["table"])
            if size is None:
                g = (self.a.playbook or {}).get("graph", {}) or {}
                sr = roles.get(g.get("size_by") or "")
                size = sr["col"].j if sr and sr.get("col") is not None and sr["table"] == t.tid else -1
            w: Counter = Counter()
            excl = [(j, vals) for (xt, j), vals in (getattr(self.a, "exclusions", {}) or {}).items() if xt == t.tid]
            for row in t.rows:
                if any(norm_key(row[j] if j < len(row) else None) in vals for j, vals in excl):
                    continue      # rows the owner leaves out never make a line (ABC does not "receive" goods)
                ka = norm_key(row[a_r["col"].j] if a_r["col"].j < len(row) else None)
                kb = norm_key(row[b_r["col"].j] if b_r["col"].j < len(row) else None)
                if ka is None or kb is None:
                    continue
                w[(ka, kb)] += _num(row[size]) if 0 <= size < len(row) else 1.0
            for (ka, kb), val in w.most_common():
                x, y = thing_id(rel["from"], ka), thing_id(rel["to"], kb)
                if x not in ids or y not in ids or val <= 0 \
                        or self.values.get(x, {}).get("left_out") or self.values.get(y, {}).get("left_out"):
                    continue
                if per[x] >= EDGES_PER_THING or per[y] >= EDGES_PER_THING:
                    continue
                per[x] += 1
                per[y] += 1
                out.append((x, y, rel.get("label", "relates to"), val))
        return out


def model_rows(analysis, path: str, mentioned_cells: set) -> tuple:
    """For a financial model the things are its labeled rows: the most connected,
    and every row a problem or a note is about. Returns (rows, flow edges)."""
    fa = analysis.formulas.get(path) or {}
    flow = fa.get("row_flow", [])
    deg: Counter = Counter()
    incoming: Counter = Counter()
    for e in flow:
        deg[e["from"]] += e["refs"]
        deg[e["to"]] += e["refs"]
        incoming[e["to"]] += 1
    flagged = defaultdict(list)
    for key, items in (("pattern_breaks", "breaks its row's pattern"), ("hardcoded", "has a typed number"),
                       ("typed_in_formula_rows", "is typed in a row of formulas"),
                       ("orphan_inputs", "is an input no formula uses")):
        for x in fa.get(key, []):
            flagged[f"{x['sheet']}!{x['row_label']}"].append(f"{x['cell']} {items}")
    for c in fa.get("checks", []):
        if c["failing_count"]:
            flagged[f"{c['sheet']}!{c['row_label']}"].append("check does not tie out")
    keep = [k for k, _ in deg.most_common(ROW_CAP)]
    keep += [k for k in flagged if k not in keep]
    keep += [k for k in mentioned_cells if k not in keep]
    rows = []
    for key in keep:
        sheet, _, label = key.partition("!")
        if not label:
            continue
        rows.append({"id": f"row:{key}", "label": label[:60], "sheet": sheet,
                     "kind": "input" if incoming[key] == 0 else "calculation",
                     "refs": deg[key], "flags": flagged.get(key, [])})
    ids = {r["id"] for r in rows}
    edges = [(f"row:{e['from']}", f"row:{e['to']}", e["refs"]) for e in flow
             if f"row:{e['from']}" in ids and f"row:{e['to']}" in ids]
    return rows, edges


_CELL = re.compile(r"(?:'([^']+)'|([A-Za-z][\w &]*?))!\$?([A-Z]{1,3})\$?(\d+)")


def cells_named(analysis, text: str) -> set:
    """'Balance Sheet!R4' in a sentence -> the row it sits on, 'Balance Sheet!Cash'."""
    out = set()
    for m in _CELL.finditer(text or ""):
        sheet = (m.group(1) or m.group(2) or "").strip()
        label = analysis.cell_label(sheet, m.group(3) + m.group(4))
        row = label.split(",")[0].strip() if label else ""
        if row:
            out.add(f"{sheet}!{row}")
    return out
