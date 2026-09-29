"""Formula analysis: which sheet feeds which, lookups, derived tabs, pattern
breaks, typed numbers inside formulas, volatile functions, balance checks and
the actuals boundary in time-series models. No formula engine needed.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from . import brainzip
from .profile import _is_num

_STR = re.compile(r'"(?:[^"]|"")*"')
_SHEET_REF = re.compile(
    r"(?:'((?:[^']|'')+)'|([A-Za-z_À-￿][\w.À-￿]*))!"
    r"(\$?[A-Z]{1,3}\$?\d{1,7}(?::\$?[A-Z]{1,3}\$?\d{1,7})?|\$?[A-Z]{1,3}:\$?[A-Z]{1,3}|\$?\d+:\$?\d+)")
_LOCAL_REF = re.compile(r"(?<![A-Za-z_!$\d.])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![\d(A-Za-z_])")
_COLS_REF = re.compile(r"(?<![A-Za-z_!$\d.])(\$?)([A-Z]{1,3}):(\$?)([A-Z]{1,3})(?![\w(])")
_NUMBER = re.compile(r"(?<![A-Za-z_$\d.\[])(\d+\.\d+|\d+)(?![\d.\]A-Za-z_(])")
_FUNC = re.compile(r"\b([A-Z][A-Z0-9.]*)\(")
_LOOKUPS = {"VLOOKUP", "HLOOKUP", "XLOOKUP", "INDEX", "MATCH", "XMATCH", "LOOKUP", "SUMIFS",
            "SUMIF", "COUNTIFS", "COUNTIF", "AVERAGEIFS", "FILTER", "GETPIVOTDATA"}
_VOLATILE = {"TODAY", "NOW", "RAND", "RANDBETWEEN", "OFFSET", "INDIRECT", "CELL", "INFO"}
_OK_CONSTANTS = {"0", "1", "2", "3", "4", "5", "6", "7", "10", "12", "24", "30", "31", "52",
                 "60", "100", "365", "1000", "0.5"}
# a row that should be zero: 'Check', 'Difference', 'Balance test', 'Balancing'; a bare 'Bank balance' is a level
_CHECK_LABEL = re.compile(r"\bcheck\b|\bdiff(erence)?\b|\bbalanc(e|es)\s*(check|test)\b|\bbalancing\b|"
                          r"\bout\s+of\s+balance\b|\berror\b|\bvariance\b", re.I)


def strip_strings(f: str) -> str:
    return _STR.sub('""', f)


def sheet_refs(f: str) -> list:
    """(sheet name, ref text) for every sheet-qualified reference."""
    out = []
    for m in _SHEET_REF.finditer(strip_strings(f)):
        name = (m.group(1) or m.group(2) or "").replace("''", "'")
        out.append((name, m.group(3)))
    return out


def functions(f: str) -> set:
    return set(_FUNC.findall(strip_strings(f).upper()))


def r1c1(f: str, r: int, c: int) -> str:
    """Relative signature: A1 refs become offsets from the formula's own cell,
    so a formula copied across a row or down a column has one signature."""
    s = strip_strings(f)

    def cell(m):
        cabs, col, rabs, row = m.groups()
        ci = brainzip.col_index(col)
        ri = int(row) - 1
        cc = f"C{ci}" if cabs else f"C[{ci - c}]"
        rr = f"R{ri}" if rabs else f"R[{ri - r}]"
        return rr + cc

    def cols(m):
        a_abs, a, b_abs, b = m.groups()
        ai, bi = brainzip.col_index(a), brainzip.col_index(b)
        return (f"C{ai}" if a_abs else f"C[{ai - c}]") + ":" + (f"C{bi}" if b_abs else f"C[{bi - c}]")

    def sheet_ref(m):
        name = m.group(1) or m.group(2) or ""
        ref = m.group(3)
        rel = _COLS_REF.sub(cols, " " + ref)
        rel = _LOCAL_REF.sub(cell, rel)
        return f"{name}!{rel.strip()}"

    s = _SHEET_REF.sub(sheet_ref, s)     # other-tab refs are relative too: Inputs!AA32 in col AA
    s = _COLS_REF.sub(cols, s)
    s = _LOCAL_REF.sub(cell, s)
    return s


def constants(f: str) -> list:
    s = strip_strings(f)
    s = _SHEET_REF.sub(" ", s)
    s = _COLS_REF.sub(" ", s)
    s = _LOCAL_REF.sub(" ", s)
    return [n for n in _NUMBER.findall(s) if n not in _OK_CONSTANTS]


def _row_label(sheet, r: int) -> str:
    row = sheet.values[r] if r < len(sheet.values) else []
    for v in row[:3]:
        if isinstance(v, str) and v.strip() and not v.strip().startswith("="):
            return re.sub(r"\s+", " ", v.strip())[:60]
    return ""


def _ref_rows(ref: str) -> tuple:
    """(first row, last row) 0-based for a ref like A1, A1:B5, A:A (None, None)."""
    rows = re.findall(r"\$?[A-Z]{1,3}\$?(\d+)", ref)
    if not rows:
        return None, None
    rs = [int(x) - 1 for x in rows]
    return min(rs), max(rs)


def analyze(book, tables_by_sheet: dict) -> dict:
    sheets = {s.name: s for s in book.data_sheets()}
    total = 0
    by_sheet: dict = {}
    sheet_edges: Counter = Counter()
    lookups: Counter = Counter()
    row_flow: Counter = Counter()
    volatile: Counter = Counter()
    breaks = []
    hardcoded = []
    missing = 0
    header_at: dict = {}
    every_row: dict = {}
    for sname, tabs in tables_by_sheet.items():
        for t in tabs:
            for j, c in enumerate(t.cols):
                header_at[(sname, c)] = (t, t.headers[j])
    for s in sheets.values():
        n = len(s.formulas)
        total += n
        missing += s.missing_cached
        if n:
            by_sheet[s.name] = n
        by_row: dict = defaultdict(list)
        by_col: dict = defaultdict(list)
        for (r, c), f in s.formulas.items():
            fu = functions(f)
            for v in fu & _VOLATILE:
                volatile[v] += 1
            refs = sheet_refs(f)
            src_label = _row_label(s, r)
            for (other, ref) in refs:
                if other == s.name or other not in sheets:
                    continue
                sheet_edges[(other, s.name)] += 1
                if fu & _LOOKUPS:
                    hdr = header_at.get((s.name, c), (None, ""))[1]
                    lookups[(s.name, hdr, other, sorted(fu & _LOOKUPS)[0])] += 1
                r1, r2 = _ref_rows(ref)
                if r1 is not None and r1 == r2 and src_label:
                    lab = _row_label(sheets[other], r1)
                    if lab:
                        row_flow[(f"{other}!{lab}", f"{s.name}!{src_label}")] += 1
            # same-sheet row-to-row flow (models chain rows on one sheet)
            if src_label:
                for m in _LOCAL_REF.finditer(_SHEET_REF.sub(" ", strip_strings(f))):
                    rr = int(m.group(4)) - 1
                    if rr != r:
                        lab = _row_label(s, rr)
                        if lab and lab != src_label:
                            row_flow[(f"{s.name}!{lab}", f"{s.name}!{src_label}")] += 1
            consts = constants(f)
            if consts and (refs or _LOCAL_REF.search(strip_strings(f))):
                hardcoded.append({"sheet": s.name, "cell": f"{brainzip.col_letter(c)}{r + 1}",
                                  "formula": f[:120], "constant": consts[0],
                                  "row_label": src_label})
            sig = r1c1(f, r, c)
            by_row[r].append((c, sig, f))
            by_col[c].append((r, sig, f))
        breaks += _pattern_breaks(s, by_row, by_col)
        every_row.update({(s.name, r): cells for r, cells in by_row.items()})
    derived = []
    for s in sheets.values():
        cells = sum(1 for row in s.values for v in row
                    if v is not None and not (isinstance(v, str) and not v.strip()))
        if not s.formulas or cells == 0:
            continue
        incoming = {a: n for (a, b), n in sheet_edges.items() if b == s.name}
        share = len(s.formulas) / cells
        if incoming and share >= 0.5:
            src = max(incoming, key=incoming.get)
            derived.append({"sheet": s.name, "from": src, "formula_share": round(min(1.0, share), 3),
                            "refs": incoming[src]})
    model = Model(book)                  # numbers of formulas a script saved without their values
    checks = _balance_checks(sheets, tables_by_sheet, model)
    boundary = _actuals_boundary(sheets, tables_by_sheet)
    plugs = _typed_in_formula_rows(sheets)
    tall = {sname: [(t.first_data, t.last_row) for t in tabs if not t.wide and t.n_rows >= 50]
            for sname, tabs in tables_by_sheet.items()}
    orphans = _orphan_inputs(sheets, {a for (a, b) in sheet_edges}, tall)
    short = _short_ranges(sheets, tables_by_sheet)
    calculated = {b for (a, b) in sheet_edges}
    typed_rows = _typed_rows_on_calculated_tabs(sheets, calculated)
    # a model read in words: its inputs, the drivers that use them, and what each odd formula does differently
    graph = dependents(book) if total else {"single": {}, "boxes": [], "total": 0}
    inputs = _inputs(sheets, graph, tall) if total else []
    inputs_at = {(x["sheet"], x["r"], x["c"]): x for x in inputs}
    name = namer(sheets, inputs_at)
    heads = {k: h for k, (t, h) in header_at.items() if t.wide}
    for br in breaks:
        rr, cc, _, _ = _cell_rc(br["cell"])
        br["diff"] = diff_words(br["formula"], br["expected_like"], br["sheet"], rr, cc, name, br.get("expected_at"))
    for x in hardcoded:
        cell = x["cell"]
        rr, cc, _, _ = _cell_rc(cell)
        br = next((b for b in breaks if b["sheet"] == x["sheet"] and b["cell"] == cell), None)
        k = next((d.split("typed ", 1)[1] for d in (br or {}).get("diff") or [] if d.startswith("multiplies by")), "")
        v = model.number((x["sheet"], rr, cc)) if br is not None and k else None
        if br is not None and br["diff"] == [f"multiplies by a typed {k}"] and _is_num(v) and float(k):
            x["multiplier"] = k                      # a link times a typed k: the amount it adds, not a break
            x["added"] = round(v - v / float(k), 2)
    for o in orphans:
        rr, cc, _, _ = _cell_rc(o["cell"])
        twins = _twins(sheets, o["value"], {(o["sheet"], rr, cc)})
        o["twins"] = [f"{s2}!{brainzip.col_letter(c2)}{r2 + 1}" for s2, r2, c2 in twins[:3]]
        o["twin_count"] = len(twins)
    switch = dict(boundary.get("cols") or {})
    return {
        "count": total,
        "by_sheet": by_sheet,
        "missing_cached": missing,
        "sheet_edges": [{"from": a, "to": b, "refs": n} for (a, b), n in sheet_edges.most_common(60)],
        "lookups": [{"sheet": a, "column": h, "to_sheet": b, "function": fn, "cells": n}
                    for (a, h, b, fn), n in lookups.most_common(30)],
        "row_flow": [{"from": a, "to": b, "refs": n} for (a, b), n in row_flow.most_common(400)],
        "derived": derived,
        "pattern_breaks": breaks[:60],
        "pattern_breaks_total": len(breaks),
        "hardcoded": hardcoded[:60],
        "hardcoded_total": len(hardcoded),
        "volatile": dict(volatile),
        "checks": checks,
        "actuals_boundary": boundary,
        "typed_in_formula_rows": plugs[:30],
        "typed_rows_on_calculated_tabs": typed_rows[:20],
        "orphan_inputs": orphans[:30],
        "short_ranges": short[:10],
        "inputs": inputs[:60],
        "drivers": _drivers(sheets, every_row, inputs_at, heads)[:40],
        "plan_blocks": _plan_blocks(sheets, tables_by_sheet, switch, graph)[:6],
        "tieouts": _tieouts(sheets, tables_by_sheet, switch, model)[:10],
        "sign": _sign(sheets, every_row, model),
        "scale": _scale(tables_by_sheet, inputs),
        "calc_manual": bool(getattr(book.pkg, "calc_manual", False)),
        "external_links": int(getattr(book.pkg, "external_links", 0) or 0),
        "connections": bool(getattr(book.pkg, "connections", False)),
        # every problem cell, uncapped, for the one ledger that counts them (never written out)
        "_all": {"pattern_breaks": breaks, "hardcoded": hardcoded, "typed_in_formula_rows": plugs,
                 "orphan_inputs": orphans},
    }


def _consistent(groups: dict, min_cells: int, share: float) -> dict:
    """index -> the signature most cells in that row (or column) share."""
    out = {}
    for k, cells in groups.items():
        if len(cells) < min_cells:
            continue
        sigs = Counter(sig for _, sig, _ in cells)
        main, n = sigs.most_common(1)[0]
        if n >= share * len(cells):
            out[k] = main
    return out


def _pattern_breaks(sheet, by_row: dict, by_col: dict) -> list:
    """A cell breaks the pattern only if it differs from its row AND does not
    follow its own column (a Total column is its own consistent pattern)."""
    out = []
    col_main = _consistent(by_col, 3, 0.8)
    row_main = _consistent(by_row, 3, 0.8)
    for r, cells in by_row.items():
        if len(cells) < 4:
            continue
        cells.sort()
        sigs = Counter(sig for _, sig, _ in cells)
        main, n_main = sigs.most_common(1)[0]
        if n_main < 0.6 * len(cells):
            continue
        for idx, (c, sig, f) in enumerate(cells):
            if sig != main and idx != 0 and col_main.get(c) != sig:   # first cell of a series often differs
                like = next((cc, ff) for cc, s2, ff in cells if s2 == main)
                out.append({"sheet": sheet.name, "cell": f"{brainzip.col_letter(c)}{r + 1}",
                            "row_label": _row_label(sheet, r), "formula": f[:120],
                            "expected_like": like[1][:120], "expected_at": (r, like[0]),
                            "axis": "row"})
    for c, cells in by_col.items():
        if len(cells) < 10:
            continue
        sigs = Counter(sig for _, sig, _ in cells)
        main, n_main = sigs.most_common(1)[0]
        if n_main < 0.9 * len(cells):
            continue
        for (r, sig, f) in cells:
            if sig != main and row_main.get(r) != sig:
                if any(x["cell"] == f"{brainzip.col_letter(c)}{r + 1}" for x in out):
                    continue
                like = next((rr, ff) for rr, s2, ff in cells if s2 == main)
                out.append({"sheet": sheet.name, "cell": f"{brainzip.col_letter(c)}{r + 1}",
                            "row_label": _row_label(sheet, r), "formula": f[:120], "axis": "column",
                            "expected_like": like[1][:120], "expected_at": (like[0], c)})
    return out


def _balance_checks(sheets: dict, tables_by_sheet: dict, model: "Model") -> list:
    """Check rows (a label like Check or Difference) and the periods they are not
    zero in. model: a check formula saved without its value is read again."""
    out = []
    for sname, tabs in tables_by_sheet.items():
        s = sheets.get(sname)
        if s is None:
            continue
        for t in tabs:
            if not t.wide:
                continue
            for ri, row in zip(t.row_index, t.rows):
                label = _row_label(s, ri)
                if not label or not _CHECK_LABEL.search(label):
                    continue
                failing = []
                worst = 0.0
                row = [v if _is_num(v) or j >= len(t.cols) or (ri, t.cols[j]) not in s.formulas
                       else model.number((sname, ri, t.cols[j])) for j, v in enumerate(row)]
                for j, v in enumerate(row):
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 0.5:
                        failing.append(t.headers[j])
                        worst = max(worst, abs(float(v)))
                numeric = sum(1 for v in row if isinstance(v, (int, float)) and not isinstance(v, bool))
                if numeric >= 3:
                    out.append({"sheet": sname, "row_label": label, "periods": numeric,
                                "failing": failing[:12], "failing_count": len(failing),
                                "max_abs": round(worst, 2)})
    return out


def _typed_in_formula_rows(sheets: dict) -> list:
    """A number typed into a row that is otherwise formulas, after the row's
    formulas start (typed actuals BEFORE the first formula are normal)."""
    out = []
    for s in sheets.values():
        rows: dict = defaultdict(set)
        for (r, c) in s.formulas:
            rows[r].add(c)
        for r, fcols in rows.items():
            if len(fcols) < 4 or r >= len(s.values):
                continue
            first, last = min(fcols), max(fcols)
            row = s.values[r]
            for c in range(first + 1, min(last, len(row) - 1) + 1):
                if c not in fcols and c < len(row) and _is_num(row[c]):
                    out.append({"sheet": s.name, "cell": f"{brainzip.col_letter(c)}{r + 1}",
                                "row_label": _row_label(s, r), "value": row[c]})
    return out


def _typed_rows_on_calculated_tabs(sheets: dict, calculated: set) -> list:
    """A whole row of typed numbers on a tab that is otherwise calculated (a
    planned raise typed into a cash flow). Not an error by itself, but it moves
    the answer and nothing upstream changes it."""
    out = []
    for name in calculated:
        s = sheets.get(name)
        if s is None or not s.formulas:
            continue
        formula_rows = {r for (r, c) in s.formulas}
        for r, row in enumerate(s.values):
            if r in formula_rows:
                continue
            label = _row_label(s, r)
            nums = [(c, v) for c, v in enumerate(row) if _is_num(v) and v != 0]
            if not label or not nums or len(formula_rows) < 3:
                continue
            biggest = max(nums, key=lambda cv: abs(cv[1]))
            out.append({"sheet": name, "row_label": label, "cells": len(nums),
                        "biggest": biggest[1], "cell": f"{brainzip.col_letter(biggest[0])}{r + 1}",
                        "header_row": r})
    return out


def _short_ranges(sheets: dict, tables_by_sheet: dict) -> list:
    """Formulas that read a data table through a fixed range that stops before the
    table's last row: rows added later are silently left out of their totals."""
    ends: dict = {}
    funcs: dict = {}
    for s in sheets.values():
        for (r, c), f in s.formulas.items():
            for other, ref in sheet_refs(f):
                if other == s.name or other not in tables_by_sheet:
                    continue
                box = _ref_box(ref)
                if not box or box[0] == box[2]:
                    continue
                key = (s.name, other)
                ends.setdefault(key, []).append(box)
                # the functions that read the range ('SUMIFS'), so the question can name them
                for name in re.findall(r"\b([A-Z][A-Z0-9.]*)\s*\(", strip_strings(str(f))):
                    funcs.setdefault(key, Counter())[name] += 1
    out = []
    for (frm, other), boxes in ends.items():
        for t in tables_by_sheet.get(other, []):
            if t.wide or t.n_rows < 20:
                continue
            spans = [b for b in boxes if b[0] <= t.first_data + 1 and t.first_data <= b[2] < t.last_row]
            if not spans:
                continue
            end = max(b[2] for b in spans)
            if end >= t.last_row or end - t.first_data < 0.5 * (t.last_row - t.first_data):
                continue
            out.append({"sheet": frm, "reads": other, "range_end": end + 1, "last_row": t.last_row + 1,
                        "rows_left_out": t.last_row - end, "formulas": len(spans),
                        "functions": [k for k, _n in (funcs.get((frm, other)) or Counter()).most_common(3)]})
    return out


def _orphan_inputs(sheets: dict, source_sheets: set, tall: dict | None = None) -> list:
    """Typed, labeled numbers on a tab that feeds other tabs, which no formula
    anywhere reads: usually a leftover from an earlier version. Rows of a tall data
    table are data, not inputs, so they are never called leftovers."""
    boxes: dict = defaultdict(list)
    for s in sheets.values():
        for (r, c), f in s.formulas.items():
            for other, ref in sheet_refs(f):
                boxes[other].append(_ref_box(ref))
            local = _SHEET_REF.sub(" ", strip_strings(f))
            for m in _LOCAL_REF.finditer(local):
                rr, cc = int(m.group(4)) - 1, brainzip.col_index(m.group(2))
                boxes[s.name].append((rr, cc, rr, cc))
            for m in re.finditer(r"(\$?[A-Z]{1,3}\$?\d{1,7}):(\$?[A-Z]{1,3}\$?\d{1,7})", local):
                boxes[s.name].append(_ref_box(m.group(0)))
    out = []
    for name in source_sheets:
        s = sheets.get(name)
        if s is None:
            continue
        bx = [b for b in boxes.get(name, []) if b]
        for r, row in enumerate(s.values):
            label = _row_label(s, r)
            if not label:
                continue
            if any(r1 <= r <= r2 for (r1, c1, r2, c2) in bx):
                continue          # some cell of this row is read by a formula: the row is in use
            if any(a <= r <= b for a, b in (tall or {}).get(name, [])):
                continue          # a row of data (an invoice line), not an input
            for c, v in enumerate(row[:4]):
                if not _is_num(v) or (r, c) in s.formulas:
                    continue
                out.append({"sheet": name, "cell": f"{brainzip.col_letter(c)}{r + 1}",
                            "row_label": label, "value": v})
                break
    return out


def _ref_box(ref: str):
    parts = ref.replace("$", "").split(":")
    try:
        def rc(p):
            m = re.fullmatch(r"([A-Z]{1,3})?(\d+)?", p)
            col = brainzip.col_index(m.group(1)) if m and m.group(1) else None
            row = int(m.group(2)) - 1 if m and m.group(2) else None
            return row, col
        (r1, c1) = rc(parts[0])
        (r2, c2) = rc(parts[-1])
        r1 = 0 if r1 is None else r1
        r2 = 10 ** 7 if r2 is None else r2
        c1 = 0 if c1 is None else c1
        c2 = 16384 if c2 is None else c2
        return (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))
    except (AttributeError, ValueError):
        return None


_LOCAL_RANGE = re.compile(r"(?<![A-Za-z_!$\d.])\$?[A-Z]{1,3}\$?\d{1,7}\s*:\s*\$?[A-Z]{1,3}\$?\d{1,7}(?![\d(A-Za-z_])")
MAX_GRAPH_FORMULAS = 50_000          # past this, reach is not worked out (0 for every cell)


def dependents(book) -> dict:
    """Which formula cells read which cells: {"single": {(sheet, r, c): [formula
    cell]}, "boxes": [(sheet, r1, c1, r2, c2, formula cell)], "total": formula
    count}. A range is kept as a box, never expanded cell by cell."""
    single: dict = defaultdict(list)
    boxes = []
    total = 0
    sheets = {s.name: s for s in book.data_sheets()}
    if sum(len(s.formulas) for s in sheets.values()) > MAX_GRAPH_FORMULAS:
        return {"single": {}, "boxes": [], "total": 0}
    for s in sheets.values():
        for (r, c), f in s.formulas.items():
            total += 1
            me = (s.name, r, c)
            text = strip_strings(f)
            for m in _SHEET_REF.finditer(text):
                name = (m.group(1) or m.group(2) or "").replace("''", "'")
                box = _ref_box(m.group(3))
                if box and name in sheets:
                    if box[0] == box[2] and box[1] == box[3]:
                        single[(name, box[0], box[1])].append(me)
                    else:
                        boxes.append((name,) + box + (me,))
            local = _SHEET_REF.sub(" ", text)
            for m in _LOCAL_RANGE.finditer(local):
                box = _ref_box(re.sub(r"\s+", "", m.group(0)))
                if box:
                    boxes.append((s.name,) + box + (me,))
            local = _LOCAL_RANGE.sub(" ", local)
            for m in _COLS_REF.finditer(local):
                box = _ref_box(f"{m.group(2)}:{m.group(4)}")
                if box:
                    boxes.append((s.name,) + box + (me,))
            local = _COLS_REF.sub(" ", local)
            for m in _LOCAL_REF.finditer(local):
                single[(s.name, int(m.group(4)) - 1, brainzip.col_index(m.group(2)))].append(me)
    return {"single": dict(single), "boxes": boxes, "total": total}


def reach(graph: dict, sheet: str, cell: str) -> int:
    """How many formula cells change, directly or down the chain, when this cell changes."""
    m = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", str(cell or ""))
    if not m or not graph.get("total"):
        return 0
    start = (sheet, int(m.group(2)) - 1, brainzip.col_index(m.group(1)))
    seen, todo = set(), [start]
    while todo:
        at = todo.pop()
        nxt = list(graph["single"].get(at, []))
        nxt += [f for name, r1, c1, r2, c2, f in graph["boxes"]
                if name == at[0] and r1 <= at[1] <= r2 and c1 <= at[2] <= c2]
        for f in nxt:
            if f not in seen:
                seen.add(f)
                todo.append(f)
    seen.discard(start)
    return len(seen)


def downstream(graph: dict, sheet: str, cell: str) -> set:
    """The formula cells (sheet, r, c) that change, directly or down the chain, when this cell changes."""
    m = re.fullmatch(r"\$?([A-Z]{1,3})\$?(\d+)", str(cell or ""))
    if not m or not graph.get("total"):
        return set()
    start = (sheet, int(m.group(2)) - 1, brainzip.col_index(m.group(1)))
    seen, todo = set(), [start]
    while todo:
        at = todo.pop()
        nxt = list(graph["single"].get(at, []))
        nxt += [f for name, r1, c1, r2, c2, f in graph["boxes"]
                if name == at[0] and r1 <= at[1] <= r2 and c1 <= at[2] <= c2]
        for f in nxt:
            if f not in seen:
                seen.add(f)
                todo.append(f)
    seen.discard(start)
    return seen


def reach_many(graph: dict, cells: list) -> int:
    """How many formula cells change when any of these (sheet, r, c) cells change."""
    if not graph.get("total"):
        return 0
    seen, todo = set(), list(cells)
    start = set(cells)
    while todo:
        at = todo.pop()
        nxt = list(graph["single"].get(at, []))
        nxt += [f for name, r1, c1, r2, c2, f in graph["boxes"]
                if name == at[0] and r1 <= at[1] <= r2 and c1 <= at[2] <= c2]
        for f in nxt:
            if f not in seen:
                seen.add(f)
                todo.append(f)
    return len(seen - start)


# --------------------------------------------------------------------------
# formulas in words: tokens, labels, the difference between two formulas
# --------------------------------------------------------------------------
_TOKEN = re.compile(
    r'(?P<str>"(?:[^"]|"")*")'
    r"|(?P<sref>(?:'(?:[^']|'')+'|[A-Za-z_À-￿][\w.À-￿]*)!\$?[A-Z]{1,3}\$?\d{1,7}"
    r"(?::\$?[A-Z]{1,3}\$?\d{1,7})?)"
    r"|(?P<range>\$?[A-Z]{1,3}\$?\d{1,7}:\$?[A-Z]{1,3}\$?\d{1,7})"
    r"|(?P<func>[A-Z][A-Z0-9.]*)\("
    r"|(?P<ref>\$?[A-Z]{1,3}\$?\d{1,7})(?![\w(])"
    r"|(?P<num>\d+\.\d*|\.\d+|\d+)"
    r"|(?P<name>[A-Za-z_][\w.]*)"
    r"|(?P<op><=|>=|<>|[-+*/^&=<>(),%;])"
    r"|(?P<sp>\s+)"
    r"|(?P<other>.)")
_CELL = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d{1,7})")


def tokens(f: str) -> list:
    """(kind, text) for each piece of a formula, spaces dropped: str, sref (a
    reference on another tab, maybe a range), range, func (its name; the '(' is
    its own op token), ref, num, name, op, other."""
    out = []
    for m in _TOKEN.finditer(str(f).lstrip("=")):
        kind = m.lastgroup
        if kind == "sp":
            continue
        if kind == "func":
            out += [("func", m.group("func")), ("op", "(")]
        else:
            out.append((kind, m.group(kind)))
    return out


def _cell_rc(text: str) -> tuple:
    """'$B$4' -> (row, col, row absolute, col absolute), 0-based."""
    m = _CELL.fullmatch(text)
    cabs, col, rabs, row = m.groups()
    return int(row) - 1, brainzip.col_index(col), bool(rabs), bool(cabs)


def ref_of(tok: tuple, sheet: str) -> tuple | None:
    """(sheet, r1, c1, r2, c2) of a ref, range or sref token; None for anything else."""
    kind, text = tok
    if kind not in ("ref", "range", "sref"):
        return None
    name = sheet
    if kind == "sref":
        name, _, text = text.rpartition("!")
        name = name[1:-1].replace("''", "'") if name.startswith("'") else name
    parts = text.split(":")
    r1, c1, _, _ = _cell_rc(parts[0])
    r2, c2, _, _ = _cell_rc(parts[-1])
    return name, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def _rel(tok: tuple, sheet: str, r: int, c: int):
    """A token as its copy-invariant form: references relative to the formula's own cell."""
    ref = ref_of(tok, sheet)
    if ref is None:
        return tok
    kind, text = tok
    name = ref[0]
    pieces = []
    for p in text.rpartition("!")[2].split(":"):
        rr, cc, rabs, cabs = _cell_rc(p)
        pieces.append((f"R{rr}" if rabs else f"R[{rr - r}]") + (f"C{cc}" if cabs else f"C[{cc - c}]"))
    return ("ref", f"{name}!" + ":".join(pieces))


def _period_words(k: int) -> str:
    return f"{abs(k)} period{'s' if abs(k) != 1 else ''} {'earlier' if k < 0 else 'later'}"


def namer(sheets: dict, inputs_at: dict):
    """A function naming the cell a formula reads: an input by its label and cell,
    a line of a grid by its row label (the prior period's when it reads one column
    to the left), else the cell itself."""
    def name(s2: str, r2: int, c2: int, s: str, r: int, c: int) -> str:
        a1 = f"{brainzip.col_letter(c2)}{r2 + 1}"
        inp = inputs_at.get((s2, r2, c2))
        if inp:
            return f"{inp['label']} ({s2}!{a1})"
        lab = _row_label(sheets[s2], r2) if s2 in sheets else ""
        if not lab:
            return f"{s2}!{a1}" if s2 != s else a1
        on = f" on {s2}" if s2 != s else ""
        if c2 == c:
            return lab + on
        if c2 == c - 1:
            return f"prior {lab}{on}"
        return f"{lab}{on} ({_period_words(c2 - c)})"
    return name


_OP_WORDS = {"*": " x ", "/": " / ", "+": " + ", "-": " - ", "^": "^", ",": ", ", "(": "(", ")": ")",
             "&": " & ", "=": " = ", "<": " < ", ">": " > ", "<=": " <= ", ">=": " >= ", "<>": " <> ",
             "%": "%", ";": "; "}


def words(f: str, sheet: str, r: int, c: int, name) -> str:
    """A formula read aloud with labels: '=B4*(1+Inputs!$B$2)' in column C reads
    'prior Revenue x (1 + Growth (Inputs!B2))'."""
    out = []
    for tok in tokens(f):
        kind, text = tok
        ref = ref_of(tok, sheet)
        if ref is not None:
            s2, r1, c1, r2, c2 = ref
            first = name(s2, r1, c1, sheet, r, c)
            out.append(first if (r1, c1) == (r2, c2) else f"{first} to {name(s2, r2, c2, sheet, r, c)}")
        elif kind == "op":
            out.append(_OP_WORDS.get(text, text))
        else:
            out.append(text)
    s = re.sub(r"\s+", " ", "".join(out)).strip()
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"^- ", "-", s)
    return re.sub(r"([(,x/+]) - ", r"\1 -", s)


def moved(f: str, dr: int, dc: int) -> str:
    """The formula as if copied dr rows down and dc columns across: relative
    references move, absolute ones stay."""
    def one(m):
        cabs, col, rabs, row = m.groups()
        cc = brainzip.col_index(col) + (0 if cabs else dc)
        rr = int(row) + (0 if rabs else dr)
        if cc < 0 or rr < 1:
            raise ValueError(m.group(0))
        return f"{cabs}{brainzip.col_letter(cc)}{rabs}{rr}"
    out = []
    for kind, text in tokens(f):
        if kind in ("ref", "range", "sref"):
            sheet, bang, cells = text.rpartition("!")
            text = sheet + bang + _CELL.sub(one, cells)
        out.append(text)
    return "=" + "".join(out)


def diff_words(f: str, expected: str, sheet: str, r: int, c: int, name, at: tuple | None = None) -> list:
    """What a formula does that its row's usual formula does not, in labels: 'uses
    A where the row uses B', 'drops + C', 'adds + D', 'reads prior X', 'multiplies
    by a typed 1.05'. at: the cell the usual formula was read from, so it is
    compared as if copied here. [] when the difference is not one of these."""
    import difflib
    try:
        if at:
            expected = moved(expected, r - at[0], c - at[1])
    except ValueError:
        return []
    mine, theirs = tokens(f), tokens(expected)
    a = [_rel(t, sheet, r, c) for t in mine]
    b = [_rel(t, sheet, r, c) for t in theirs]
    out = []

    def named(tok):
        ref = ref_of(tok, sheet)
        if ref is None:
            return tok[1]
        s2, r1, c1, r2, c2 = ref
        return name(s2, r1, c1, sheet, r, c)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        got, want = mine[i1:i2], theirs[j1:j2]
        if op == "replace" and len(got) == len(want) == 1 and ref_of(got[0], sheet) and ref_of(want[0], sheet):
            g, w = ref_of(got[0], sheet), ref_of(want[0], sheet)
            if g[:2] == w[:2] and g[3] == w[3]:           # the same line, another period
                out.append(f"reads {named(got[0])} where the row reads {named(want[0])}")
            else:
                out.append(f"uses {named(got[0])} where the row uses {named(want[0])}")
        elif op == "delete" and len(got) == 2 and got[0] == ("op", "*") and got[1][0] == "num":
            out.append(f"multiplies by a typed {got[1][1]}")
        elif op == "delete" and len(got) == 2 and got[0][0] == "op" and got[0][1] in "+-" and ref_of(got[1], sheet):
            out.append(f"adds {got[0][1]} {named(got[1])}")
        elif op == "insert" and len(want) == 2 and want[0][0] == "op" and want[0][1] in "+-" \
                and ref_of(want[1], sheet):
            out.append(f"drops {want[0][1]} {named(want[1])}")
        elif op == "insert" and len(want) == 2 and want[1][0] == "op" and want[1][1] in "+-" \
                and ref_of(want[0], sheet):
            out.append(f"drops {named(want[0])} {want[1][1]}")
        else:
            return []
    return out


# --------------------------------------------------------------------------
# a model's inputs, its drivers in words, plan blocks, tie-outs, sign and scale
# --------------------------------------------------------------------------
_PERIODISH = re.compile(r"\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}[\s\-']*\d{0,4}|(Q[1-4]|FY|H[12])\s?\d{0,4}|\d{4}|M\d{1,2}")
_TRIVIAL = {0, 1, -1}
INPUT_ROWS = 2            # label | value rows on one tab, one of them read by a formula, before they are inputs
PLAN_SHARE = 0.75         # a typed row covering this share of a grid's periods (and every forecast one) is a block
PLAN_VALUES = 3           # distinct nonzero numbers it holds, at least: a constant is an input, not a plan
PLAN_REACH = 0.10         # the share of the model's formula cells it feeds, at least
TIE_CENT = 0.005          # a typed month and the row its later months link to differ by more than a cent


_MONTH_START = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I)


def _is_period(h) -> bool:
    """A grid column header that names a period: a date, a month, a quarter, a year."""
    h = str(h).strip()
    return bool(_PERIODISH.fullmatch(h)) and not re.search(r"total", h, re.I) \
        and bool(re.search(r"\d", h) or _MONTH_START.match(h))


def _referenced(graph: dict) -> tuple:
    """(cells a formula reads one by one, boxes a formula reads): for 'is this cell read?'."""
    boxes: dict = defaultdict(list)
    for name, r1, c1, r2, c2, _f in graph.get("boxes", []):
        boxes[name].append((r1, c1, r2, c2))
    return set(graph.get("single", {})), boxes


def _is_read(read: tuple, sheet: str, r: int, c: int) -> bool:
    single, boxes = read
    return (sheet, r, c) in single or any(r1 <= r <= r2 and c1 <= c <= c2 for r1, c1, r2, c2 in boxes.get(sheet, []))


def _inputs(sheets: dict, graph: dict, tall: dict) -> list:
    """Label | value | note rows: a text label, a typed number just right of it,
    maybe a text note right of that, and nothing else on the row. A tab's rows
    count as inputs only when there are INPUT_ROWS or more and a formula reads one
    by its own cell. A row read only through a range (a price list a lookup reads)
    is a row of a list, not an input."""
    single, boxes = _referenced(graph)
    out = []
    for s in sheets.values():
        rows = []
        for r, row in enumerate(s.values):
            if any(a <= r <= b for a, b in (tall or {}).get(s.name, [])):
                continue
            filled = [(c, v) for c, v in enumerate(row) if v is not None and not (isinstance(v, str) and not v.strip())]
            if not 2 <= len(filled) <= 3 or filled[0][0] > 2:
                continue
            (lc, lab), (vc, val) = filled[0], filled[1]
            if not isinstance(lab, str) or vc != lc + 1 or not _is_num(val) or (r, vc) in s.formulas:
                continue
            note = ""
            if len(filled) == 3:
                nc, nv = filled[2]
                if nc != vc + 1 or not isinstance(nv, str) or (r, nc) in s.formulas:
                    continue
                note = re.sub(r"\s+", " ", nv.strip())[:120]
            own = (s.name, r, vc) in single
            if not own and _is_read((set(), boxes), s.name, r, vc):
                continue
            rows.append({"sheet": s.name, "r": r, "c": vc, "cell": f"{brainzip.col_letter(vc)}{r + 1}",
                         "label": re.sub(r"\s+", " ", lab.strip())[:60], "value": val, "note": note,
                         "read": own})
        if len(rows) >= INPUT_ROWS and any(x["read"] for x in rows):
            out += rows
    return out


def _drivers(sheets: dict, by_row: dict, inputs_at: dict, heads: dict) -> list:
    """Each formula row whose usual formula reads an input, in words, with the
    periods that formula covers: 'Revenue = prior Revenue x (1 + Growth (Inputs!B2))'."""
    name = namer(sheets, inputs_at)
    out = []
    for (sname, r), cells in by_row.items():
        if len(cells) < 3:
            continue
        s = sheets[sname]
        label = _row_label(s, r)
        sigs = Counter(sig for _, sig, _ in cells)
        main, n = sigs.most_common(1)[0]
        if not label or n < 0.5 * len(cells):
            continue
        cols = sorted(c for c, sig, _ in cells if sig == main)
        c0 = cols[0]
        f0 = next(f for c, sig, f in cells if c == c0)
        used = []
        for tok in tokens(f0):
            ref = ref_of(tok, sname)
            if ref is not None and (ref[0], ref[1], ref[2]) in inputs_at and ref[1:3] == ref[3:5]:
                used.append(f"{ref[0]}!{brainzip.col_letter(ref[2])}{ref[1] + 1}")
        if not used:
            continue
        out.append({"sheet": sname, "r": r, "row_label": label, "words": words(f0, sname, r, c0, name),
                    "formula": f0[:160], "first": heads.get((sname, cols[0]), ""),
                    "last": heads.get((sname, cols[-1]), ""), "cells": n, "formulas": len(cells),
                    "inputs": used})
    out.sort(key=lambda d: (d["sheet"], d["r"]))
    return out


def _twins(sheets: dict, value, skip: set) -> list:
    """Typed cells (not formulas) holding exactly this value: [(sheet, r, c)]."""
    if not _is_num(value) or value in _TRIVIAL or (abs(value) < 10 and float(value).is_integer()):
        return []
    out = []
    for s in sheets.values():
        for r, row in enumerate(s.values):
            for c, v in enumerate(row):
                if _is_num(v) and v == value and (r, c) not in s.formulas and (s.name, r, c) not in skip:
                    out.append((s.name, r, c))
    return out


def _plan_blocks(sheets: dict, tables_by_sheet: dict, switch: dict, graph: dict) -> list:
    """Typed rows of a period grid, on any tab (an inputs tab or a calculated one),
    that cover the grid's periods (every forecast period after the actuals switch),
    hold PLAN_VALUES or more different numbers and feed PLAN_REACH or more of the
    model's formula cells: a plan, an estimate or a placeholder, which only the
    owner can say. A plan drives the model period by period: PLAN_SHARE of its
    cells are each read on their own by a formula in the same period, while typed
    history read only through ranges (a SUM of the year) is data. Adjacent rows
    are one block."""
    out = []
    total = graph.get("total") or 0
    period_at = {(sname, c): str(t.headers[j]) for sname, tabs in tables_by_sheet.items() for t in tabs if t.wide
                 for j, c in enumerate(t.cols) if _is_period(t.headers[j])}

    def in_step(sname, ri, c) -> bool:
        return any(period_at.get((f[0], f[2])) == period_at.get((sname, c)) or f[2] == c
                   for f in graph.get("single", {}).get((sname, ri, c), []))
    for sname in sorted(tables_by_sheet):
        s = sheets.get(sname)
        if s is None or not total:
            continue
        for t in tables_by_sheet.get(sname, []):
            if not t.wide:
                continue
            per = [(j, c) for j, c in enumerate(t.cols) if _is_period(t.headers[j])]
            if len(per) < 6:
                continue
            cut = switch.get(sname)
            ahead = [c for j, c in per if cut is None or c > cut]
            rows = []
            for ri, row in zip(t.row_index, t.rows):
                if any((ri, c) in s.formulas for _, c in per):
                    continue
                typed = [c for j, c in per if j < len(row) and _is_num(row[j])]
                vals = {row[j] for j, c in per if j < len(row) and _is_num(row[j]) and row[j] != 0}
                if len(typed) < PLAN_SHARE * len(per) or not set(ahead) <= set(typed) or len(vals) < PLAN_VALUES \
                        or sum(in_step(sname, ri, c) for c in typed) < PLAN_SHARE * len(typed):
                    continue
                rows.append((ri, [(sname, ri, c) for c in typed], sorted(vals)))
            blocks = []
            for ri, cells, vals in rows:
                if blocks and blocks[-1][-1][0] == ri - 1:
                    blocks[-1].append((ri, cells, vals))
                else:
                    blocks.append([(ri, cells, vals)])
            for blk in blocks:
                cells = [x for _, cs, _ in blk for x in cs]
                feeds = reach_many(graph, cells)
                if feeds < PLAN_REACH * total:
                    continue
                heads = [t.headers[j] for j, c in per if any(c == x[2] for x in blk[0][1])]
                vals = [v for _, _, vs in blk for v in vs]
                out.append({"sheet": sname, "rows": [_row_label(s, ri) for ri, _, _ in blk],
                            "row_index": [ri for ri, _, _ in blk], "cells": len(cells), "periods": len(heads),
                            "first": heads[0], "last": heads[-1], "lo": min(vals), "hi": max(vals), "feeds": feeds,
                            "share": round(feeds / total, 4),
                            "cell": f"{brainzip.col_letter(blk[0][1][0][2])}{blk[0][0] + 1}"})
    return out


def _pure_link(f: str, sheet: str, r: int, c: int):
    """(sheet, row, column offset, sign) when the formula only reads one cell of
    another row (optionally negated); else None."""
    toks = tokens(f)
    sign = 1
    if toks[:1] == [("op", "-")]:
        sign, toks = -1, toks[1:]
    if toks[:1] == [("op", "+")]:
        toks = toks[1:]
    if len(toks) != 1:
        return None
    ref = ref_of(toks[0], sheet)
    if ref is None or ref[1:3] != ref[3:5] or (ref[0] == sheet and ref[1] == r):
        return None
    return ref[0], ref[1], ref[2] - c, sign


def _tieouts(sheets: dict, tables_by_sheet: dict, switch: dict, model: "Model") -> list:
    """A row typed in its first periods whose later periods link to another row: its
    typed periods compared with that row. A gap in them is either stated (when the
    whole tab switches from typed actuals to formulas at that column) or asked.
    model: the linked row's numbers, read again where the file saved none."""
    out = []
    for sname, tabs in tables_by_sheet.items():
        s = sheets.get(sname)
        if s is None:
            continue
        for t in tabs:
            if not t.wide:
                continue
            per = [(j, c) for j, c in enumerate(t.cols) if _is_period(t.headers[j])]
            for ri, row in zip(t.row_index, t.rows):
                fcols = [c for _, c in per if (ri, c) in s.formulas]
                if len(fcols) < 3:
                    continue
                lead = [(j, c) for j, c in per if c < fcols[0] and j < len(row) and _is_num(row[j])]
                if len(lead) < 2:
                    continue
                links = Counter(_pure_link(s.formulas[(ri, c)], sname, ri, c) for c in fcols)
                link, n = links.most_common(1)[0]
                if link is None or n < 0.8 * len(fcols) or link[0] not in sheets:
                    continue
                s2, r2, dc, sign = link
                typed = linked = 0.0
                off = []
                for j, c in lead:
                    w = model.number((s2, r2, c + dc)) if c + dc >= 0 else None
                    if not _is_num(w):
                        continue
                    v, w = row[j], sign * w
                    typed += v
                    linked += w
                    if abs(v - w) > TIE_CENT + 1e-9:
                        off.append(t.headers[j])
                if not off:
                    continue
                out.append({"sheet": sname, "row_label": _row_label(s, ri), "r": ri,
                            "cell": f"{brainzip.col_letter(lead[0][1])}{ri + 1}",
                            "link_sheet": s2, "link_label": _row_label(sheets[s2], r2), "offset": dc, "sign": sign,
                            "first": t.headers[lead[0][0]], "last": t.headers[lead[-1][0]], "typed": len(lead),
                            "off": len(off), "off_first": off[0], "typed_sum": round(typed, 2),
                            "linked_sum": round(linked, 2), "gap": round(typed - linked, 2),
                            "at_switch": switch.get(sname) == lead[-1][1]})
    return out


def _sign(sheets: dict, by_row: dict, model: "Model") -> dict:
    """How the model's subtotals treat costs: rows a subtotal subtracts that hold
    positive numbers, and rows it adds that hold negative ones."""
    minus, plus = {}, {}
    subtotals, plain = set(), {}
    for (sname, r), cells in by_row.items():
        if len(cells) < 3:
            continue
        main = Counter(sig for _, sig, _ in cells).most_common(1)[0][0]
        c0, f0 = next((c, f) for c, sig, f in cells if sig == main)
        toks = tokens(f0)
        if toks and all(k in ("ref", "sref", "range") or (k == "op" and x in "+-()") or (k == "func" and x == "SUM")
                        for k, x in toks) and (("func", "SUM") in toks or sum(k != "op" for k, _ in toks) >= 2):
            subtotals.add((sname, r))       # a subtotal's sign is an outcome, never evidence of a convention
        if toks and not any(k not in ("ref", "sref", "op") or (k == "op" and x not in "+-") for k, x in toks):
            plain[(sname, r)] = (cells, main, c0, f0, toks)
    for (sname, r), (cells, main, c0, f0, toks) in plain.items():
        s = sheets[sname]
        sign = 1
        refs = []
        for k, x in toks:
            if k == "op":
                sign = -1 if x == "-" else 1
                continue
            ref = ref_of((k, x), sname)
            if ref is None or ref[1:3] != ref[3:5] or ref[2] != c0 or ref[0] not in sheets:
                refs = []
                break
            refs.append((sign, ref[0], ref[1]))
            sign = 1
        if len(refs) < 2 or not any(sg < 0 for sg, _, _ in refs):
            continue
        cols = [c for c, sig, _ in cells if sig == main]
        for k, (sg, s2, r2) in enumerate(refs):
            if (s2, r2) in subtotals or (k == 0 and sg > 0):
                continue          # a subtotal, or the line a subtotal starts from, says nothing about costs
            vals = [v for v in (model.number((s2, r2, c)) for c in cols) if _is_num(v)]
            if len(vals) < 3:
                continue
            key = (s2, _row_label(sheets[s2], r2))
            example = (sname, _row_label(s, r), refs)
            if sg < 0 and sum(v >= 0 for v in vals) >= 0.9 * len(vals) and any(v > 0 for v in vals):
                minus.setdefault(key, example)
            elif sg > 0 and sum(v <= 0 for v in vals) >= 0.9 * len(vals) and any(v < 0 for v in vals):
                plus.setdefault(key, example)
    if not minus and not plus:
        return {}
    sname, total, refs = next(iter((minus or plus).values()))
    shown = f"{total} = " + " ".join(("- " if sg < 0 else "+ ") + _row_label(sheets[s2], r2) for sg, s2, r2 in refs)
    shown = shown.replace("= + ", "= ")
    return {"positive_subtracted": sorted(f"{s}!{lab}" for s, lab in minus),
            "negative_added": sorted(f"{s}!{lab}" for s, lab in plus), "example": shown, "example_sheet": sname}


_SCALE = [("thousands", re.compile(r"\bin\s+thousands\b|\bthousands\b|\$\s?0{3}s?\b|\(0{3}s?\)|\b0{3}s\b|\$\s?k\b", re.I)),
          ("millions", re.compile(r"\bin\s+millions\b|\bmillions\b|\$\s?mm?\b|\(\$?mm?\)", re.I)),
          ("as shown", re.compile(r"\b(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|dollars|euros|pounds)\b|\(\$\)"))]


def _scale(tables_by_sheet: dict, inputs: list) -> dict:
    """The money scale the file states in its titles and input notes: 'as shown'
    (a currency named with no scale), thousands or millions, with where it is said."""
    said: dict = defaultdict(list)
    texts = [(t.sheet, "title", t.title) for tabs in tables_by_sheet.values() for t in tabs if t.title]
    texts += [(x["sheet"], "note", x["note"]) for x in inputs if x["note"]]
    for sheet, where, text in texts:
        for kind, pat in _SCALE:
            m = pat.search(text)
            if m:
                said[kind].append((sheet, where, m.group(0)))
                break
    if not said:
        return {}
    kinds = sorted(said)
    return {"kinds": kinds, "said": {k: said[k][:12] for k in kinds}, "conflict": len(kinds) > 1}


# --------------------------------------------------------------------------
# a model evaluated again with some typed cells set to zero (a plan taken out)
# --------------------------------------------------------------------------
class Unsupported(Exception):
    pass


_EVAL_FUNCS = {"SUM": "_sum", "MAX": "_max", "MIN": "_min", "ROUND": "_round", "ABS": "_abs", "AVERAGE": "_avg"}
MAX_EVAL_RANGE = 5000     # cells one range may hold in an evaluated formula


class Model:
    """A formula model read again with arithmetic only (+ - * / ^, SUM, MAX, MIN,
    ROUND, ABS, AVERAGE): the value of any cell with some typed cells set to zero.
    Raises Unsupported for anything else, so a consequence is never guessed."""
    def __init__(self, book, zero: set = frozenset()):
        self.sheets = {s.name: s for s in book.data_sheets()}
        self.zero = set(zero)
        self.memo: dict = {}
        self.code: dict = {}

    def typed(self, key) -> float:
        name, r, c = key
        s = self.sheets.get(name)
        if s is None:
            raise Unsupported(name)
        v = s.values[r][c] if r < len(s.values) and c < len(s.values[r]) else None
        return float(v) if _is_num(v) else 0.0

    def number(self, key):
        """A cell's number as the file saved it; a formula saved without its value
        (a file a script wrote) is read again. None when neither gives a number."""
        name, r, c = key
        s = self.sheets.get(name)
        v = s.values[r][c] if s is not None and r < len(s.values) and c < len(s.values[r]) else None
        if _is_num(v) or s is None or (r, c) not in s.formulas:
            return v if _is_num(v) else None
        try:
            return self.value(key)
        except Unsupported:
            return None

    def _compile(self, key):
        if key in self.code:
            return self.code[key]
        name, r, c = key
        expr, deps = [], []
        toks = tokens(self.sheets[name].formulas[(r, c)])
        # Excel binds a leading minus tighter than ^ (-2^2 is 4) and reads a chain of ^ left to right;
        # Python does neither, so such a formula is never read again
        unary = any(k == "op" and x == "-" and (i == 0 or (toks[i - 1][0] == "op" and toks[i - 1][1] != ")"))
                    for i, (k, x) in enumerate(toks))
        carets = sum(1 for k, x in toks if k == "op" and x == "^")
        if carets > 1 or (carets and unary):
            raise Unsupported("^")
        for tok in toks:
            kind, text = tok
            ref = ref_of(tok, name)
            if ref is not None:
                s2, r1, c1, r2, c2 = ref
                if (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_EVAL_RANGE:
                    raise Unsupported(text)
                cells = [(s2, rr, cc) for rr in range(r1, r2 + 1) for cc in range(c1, c2 + 1)]
                deps += cells
                expr.append(f"_v({cells!r})" if kind == "range" or (kind == "sref" and ":" in text)
                            else f"_one({cells[0]!r})")
            elif kind == "func" and text in _EVAL_FUNCS:
                expr.append(_EVAL_FUNCS[text])
            elif kind == "num":
                expr.append(text)
            elif kind == "op" and text in "+-*/(),^":
                expr.append("**" if text == "^" else text)
            else:
                raise Unsupported(text)
        self.code[key] = ("".join(expr), deps)
        return self.code[key]

    def value(self, key) -> float:
        name, r, c = key
        stack, busy = [key], set()
        while stack:
            k = stack[-1]
            if k in self.memo:
                stack.pop()
                continue
            if k[0] not in self.sheets:
                raise Unsupported(k[0])
            if k in self.zero or (k[1], k[2]) not in self.sheets[k[0]].formulas:
                self.memo[k] = 0.0 if k in self.zero else self.typed(k)
                stack.pop()
                continue
            expr, deps = self._compile(k)
            todo = [d for d in deps if d not in self.memo]
            if todo:
                if k in busy:
                    raise Unsupported("a formula that reads itself")
                busy.add(k)
                stack += todo
                continue
            self.memo[k] = self._run(expr)
            stack.pop()
        return self.memo[key]

    def _run(self, expr: str) -> float:
        memo = self.memo

        def flat(xs):
            return [x for x in xs if x is not None]
        env = {"__builtins__": {}, "_one": lambda k: memo[k], "_v": lambda ks: [memo[k] for k in ks],
               "_sum": lambda *a: sum(sum(x) if isinstance(x, list) else x for x in a),
               "_max": lambda *a: max(flat([y for x in a for y in (x if isinstance(x, list) else [x])])),
               "_min": lambda *a: min(flat([y for x in a for y in (x if isinstance(x, list) else [x])])),
               "_round": lambda x, n=0: round(x, int(n)), "_abs": abs,
               "_avg": lambda *a: (lambda ys: sum(ys) / len(ys))([y for x in a for y in
                                                                   (x if isinstance(x, list) else [x])])}
        try:
            v = eval(expr, env)          # noqa: S307: built only from this module's own tokens
        except (ZeroDivisionError, ValueError, TypeError, SyntaxError, OverflowError):
            raise Unsupported(expr)
        if isinstance(v, list):
            raise Unsupported(expr)
        return float(v)


def _actuals_boundary(sheets: dict, tables_by_sheet: dict) -> dict:
    """Rows that switch from typed numbers to formulas at the same column: the sheet
    and column with the most rows, and every sheet switching at that same column."""
    votes: Counter = Counter()
    where: dict = {}
    for sname, tabs in tables_by_sheet.items():
        s = sheets.get(sname)
        if s is None:
            continue
        for t in tabs:
            if not t.wide:
                continue
            for ri, row in zip(t.row_index, t.rows):
                seq = []
                for j, c in enumerate(t.cols):
                    v = row[j] if j < len(row) else None
                    # a formula counts as one even when the file saved no value for it (a script wrote it)
                    if (ri, c) not in s.formulas and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                        continue
                    seq.append((j, (ri, c) in s.formulas))
                if len(seq) < 6:
                    continue
                flags = [f for _, f in seq]
                if flags[0] is False and True in flags:
                    k = flags.index(True)
                    # a forecast has several formula periods after the switch; a lone trailing
                    # formula column is a Total, not the end of actuals
                    first_hdr = t.headers[seq[k][0]] if seq[k][0] < len(t.headers) else ""
                    periodish = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}[\s\-']*\d{0,4}|"
                                                  r"(Q[1-4]|FY|H[12])\s?\d{0,4}|\d{4}|M\d{1,2}", first_hdr))
                    if all(flags[k:]) and k >= 2 and len(flags) - k >= 3 and periodish \
                            and not re.search(r"total", first_hdr, re.I):
                        j = seq[k - 1][0]
                        votes[(sname, t.headers[j])] += 1
                        where[(sname, t.headers[j])] = t.cols[j]
    if not votes:
        return {}
    (sname, hdr), n = votes.most_common(1)[0]
    if n < 3:
        return {}
    # every tab whose rows switch at the same period, with the sheet column of its last typed period
    same = sorted(((s, m) for (s, h), m in votes.items() if h == hdr and m >= 2), key=lambda x: (-x[1], x[0]))
    return {"sheet": sname, "last_actual": hdr, "rows": sum(m for _, m in same),
            "sheets": [s for s, _ in same], "cols": {s: where[(s, hdr)] for s, _ in same}}
