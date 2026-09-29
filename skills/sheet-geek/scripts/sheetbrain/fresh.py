"""Freshness (decision 10). On every reopen: re-fingerprint what each fact
depends on (seconds, no AI). Computed facts refresh silently; facts a person
gave whose data moved become "may be out of date"; notes past their
stale_after date get re-checked even if nothing changed.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from .fingerprint import file_fp, fp_like, parse_deps
from .store import today


def _parse_text(text: str) -> dict:
    out = {}
    for line in (text or "").split("\n"):
        k, sep, v = line.partition(":")
        if sep:
            out[k.strip()] = v.strip()
    return out


def aged(rec: dict, on: str | None = None) -> bool:
    sa = (rec.get("stale_after") or "").strip()
    if not sa or sa == "on-change":
        return False
    on_d = dt.date.fromisoformat(on or today())
    try:
        if sa.startswith("+") and sa.endswith("d"):
            base = dt.date.fromisoformat((rec.get("as_of") or today())[:10])
            return on_d > base + dt.timedelta(days=int(sa[1:-1]))
        return on_d > dt.date.fromisoformat(sa[:10])
    except ValueError:
        return False


def check(analysis, path: str, records: list) -> dict:
    path = os.path.abspath(path)
    meta = next((r for r in records if r.get("record") == "meta"), None)
    since = (meta or {}).get("as_of", "")
    cur_fp = file_fp(analysis, path)
    report = {"since": since, "changed": False, "rows_delta": {}, "new_values": {}, "gone_values": {},
              "added_cols": [], "removed_cols": [], "renamed": [], "outdated": [], "aged": [],
              "refreshed": 0, "missing": [], "received_checked": {}, "line": ""}
    # sheet-level: rows
    for r in records:
        if r.get("record") == "node" and r.get("kind") == "sheet":
            info = _parse_text(r.get("text", ""))
            sheet = r.get("label", "")
            try:
                old_rows = int(info.get("rows", "0"))
            except ValueError:
                old_rows = 0
            new_rows = sum(t.n_rows for t in analysis.tables
                           if analysis.file_of[t.tid] == path and t.sheet == sheet)
            if sheet and new_rows != old_rows and (sheet in cur_fp):
                report["rows_delta"][sheet] = new_rows - old_rows
    # columns: added, removed, renamed; value lists for categories
    from .brain import col_id, col_sheet
    sheet_names = {s.name for b in analysis.books if b.path == path for s in b.sheets}
    old_cols = {}
    for r in records:
        if r.get("record") == "node" and str(r.get("id", "")).startswith("col:"):
            old_cols[r["id"]] = r
    cur_cols = {}
    for t in analysis.tables:
        if analysis.file_of[t.tid] != path:
            continue
        periods = set(analysis.period_headers(t))      # a grid's periods are its line items' cells, not columns
        for c in analysis.cols[t.tid]:
            if c.type != "empty" and c.header not in periods:
                cur_cols[col_id(t.sheet, c.header, t.tid)] = (t, c)
    _rekey_legacy(old_cols, cur_cols, sheet_names)
    removed = [k for k in old_cols if k not in cur_cols]
    added = [k for k in cur_cols if k not in old_cols]
    for k in list(removed):
        info = _parse_text(old_cols[k].get("text", ""))
        old_vals = set((info.get("values") or "").split(" | ")) - {""}
        for a in list(added):
            t, c = cur_cols[a]
            if info.get("type") == c.type and _same_shape(info, c, old_vals):
                if not (old_cols[k]["label"] == c.header and col_sheet(k, sheet_names) == t.sheet):
                    report["renamed"].append({"from": old_cols[k]["label"], "to": c.header, "sheet": t.sheet})
                removed.remove(k)
                added.remove(a)
                break
    # a tab whose formulas lost their saved results (re-saved by a tool that does not calculate) only
    # looks empty; its columns are not gone
    uncalculated = {s.name for b in analysis.books if b.path == path for s in b.sheets
                    if getattr(s, "missing_cached", 0)}
    removed = [k for k in removed if col_sheet(k, sheet_names) not in uncalculated]
    # a brain written before grids had line items kept each period as a column: those are not gone
    from .analyze import _period_kind
    grids = {t.sheet for t in analysis.tables if t.wide and analysis.file_of[t.tid] == path}
    removed = [k for k in removed if not (col_sheet(k, sheet_names) in grids and _period_kind(k[k.find(".{") + 2:-1]))]
    report["removed_cols"] = [old_cols[k]["label"] for k in removed]
    report["added_cols"] = [cur_cols[k][1].header for k in added]
    for k, r in old_cols.items():
        if k not in cur_cols:
            continue
        info = _parse_text(r.get("text", ""))
        if "values" not in info:
            continue
        old_vals = set(info["values"].split(" | ")) - {""}
        t, c = cur_cols[k]
        new_vals = {str(v)[:40] for v, _ in c.top[:60]} if c.distinct <= 60 else set()
        if not new_vals:
            continue
        plus = sorted(new_vals - old_vals)
        minus = sorted(old_vals - new_vals)
        if plus:
            report["new_values"][c.header] = plus[:5]
        if minus:
            report["gone_values"][c.header] = minus[:5]
    rename = {(x["sheet"], x["from"]): x["to"] for x in report["renamed"]}
    # which columns' values changed (same header, same position or not)
    report["changed_cols"] = []
    for k, r in old_cols.items():
        if k not in cur_cols or not r.get("data_fp") or r["data_fp"].startswith("d:"):
            continue
        if cur_cols[k][0].tid in analysis.derived:
            continue          # a calculated tab changes because its source did
        deps = parse_deps(r.get("depends_on", ""))
        if deps and fp_like(analysis, path, deps, r["data_fp"]) != r["data_fp"]:
            report["changed_cols"].append(r.get("label", ""))
    # facts
    for r in records:
        deps = [(s, rename.get((s, h), h)) for s, h in parse_deps(r.get("depends_on", ""))]
        src = r.get("source", "")
        if deps and r.get("data_fp"):
            now_fp = fp_like(analysis, path, deps, r["data_fp"])
            if not now_fp:
                report["missing"].append(r)
                if src in ("told", "web"):
                    report["outdated"].append(dict(r, _why="a column it depends on is gone"))
                continue
            if now_fp != r["data_fp"]:
                if src == "computed":
                    report["refreshed"] += 1
                elif src in ("told", "web", "inferred"):
                    report["outdated"].append(dict(r, _why=_why(deps, report)))
        if src in ("told", "web") and aged(r) and r.get("status") != "may-be-outdated":
            report["aged"].append(r)
        if r.get("status") == "may-be-outdated" and r not in report["outdated"]:
            report["outdated"].append(dict(r, _why="flagged at an earlier check"))
    old_meta_fp = (meta or {}).get("data_fp", "")
    from .brain import _combine
    if old_meta_fp:
        # the raw-cell fingerprint is the judge: structural differences with identical cells come
        # from a newer version of this tool reading the file differently, not from the data
        report["changed"] = old_meta_fp != _combine(cur_fp)
        if report["changed"] and not (report["rows_delta"] or report["added_cols"] or report["removed_cols"]
                                      or report["renamed"] or report["changed_cols"] or report["new_values"]):
            report["moved_only"] = True      # same values under the same headers, in new places
            report["changed"] = False
        if not report["changed"]:
            for k in ("rows_delta", "new_values", "gone_values"):
                report[k] = {}
            report["added_cols"], report["removed_cols"], report["renamed"] = [], [], []
            report["outdated"] = [r for r in report["outdated"] if r.get("status") == "may-be-outdated"]
    else:
        report["changed"] = bool(report["rows_delta"] or report["added_cols"] or report["removed_cols"]
                                 or report["renamed"])
    report["line"] = one_liner(os.path.basename(path), report)
    return report


def _rekey_legacy(old_cols: dict, cur_cols: dict, sheet_names: set) -> None:
    """A brain saved before a tab's tables had their own place in the id named the
    columns 'col:Sheet.{H}', then 'col:Sheet.{H}~2' for the next table with that
    header. Those are the n-th table on the tab that has the header, so they are
    filed under that table's id now instead of reading as gone and new."""
    from .brain import col_sheet
    for k in [k for k in old_cols if k not in cur_cols]:
        m = re.search(r"~(\d+)$", k)
        base = k[:m.start()] if m else k
        if not base.endswith("}") or ".{" not in base:
            continue
        sheet, header = col_sheet(base, sheet_names), base[base.find(".{") + 2:-1]
        same = [cid for cid, (t, c) in cur_cols.items() if t.sheet == sheet and c.header == header]
        n = int(m.group(1)) if m else 1
        if len(same) >= n and same[n - 1] not in old_cols:
            old_cols[same[n - 1]] = old_cols.pop(k)


def _same_shape(info: dict, c, old_vals: set) -> bool:
    if old_vals and c.distinct <= 60:
        new_vals = {str(v)[:40] for v, _ in c.top[:60]}
        return len(old_vals & new_vals) >= 0.9 * max(1, len(old_vals))
    try:
        return abs(int(info.get("distinct", "-1")) - c.distinct) <= max(2, 0.1 * c.distinct)
    except ValueError:
        return False


def _why(deps: list, report: dict) -> str:
    cols = ", ".join(h for _, h in deps[:3])
    return f"{cols} changed"


def _date_words(iso: str) -> str:
    try:
        d = dt.date.fromisoformat(iso[:10])
        return d.strftime("%b %-d") if os.name != "nt" else d.strftime("%b %d")
    except (ValueError, TypeError):
        return "the last check"


def one_liner(name: str, rep: dict) -> str:
    since = _date_words(rep.get("since", ""))
    clauses = []
    rows = sum(v for v in rep["rows_delta"].values())
    if rows > 0:
        clauses.append(f"{rows:,} new rows")
    elif rows < 0:
        clauses.append(f"{-rows:,} fewer rows")
    for col, vals in list(rep["new_values"].items())[:1]:
        v = vals[0]
        clauses.append(f"a new {col} value ({v})" if len(vals) == 1 else f"{len(vals)} new {col} values")
    if rep["renamed"]:
        r = rep["renamed"][0]
        clauses.append(f"{r['from']} is now called {r['to']}")
    if rep["removed_cols"]:
        clauses.append(f"{len(rep['removed_cols'])} column{'s' if len(rep['removed_cols']) > 1 else ''} gone")
    changed_cols = [c for c in rep.get("changed_cols", []) if c not in rep["new_values"]]
    if changed_cols and not rows:
        shown = changed_cols[:3]
        more = f" and {len(changed_cols) - 3} more" if len(changed_cols) > 3 else ""
        clauses.append(f"values changed in {_join_and(shown)}{more} (same rows)")
    n_out = len(rep["outdated"])
    n_aged = len(rep["aged"])
    if rep.get("moved_only") and not n_out and not n_aged:
        return f"{name}: columns were rearranged since {since}, but no values changed. The brain is current."
    if not rep["changed"] and not n_out and not n_aged:
        return f"{name}: no changes since {since}. The brain is current."
    head = f"{name} changed since {since}: " if rep["changed"] else f"{name}: "
    tail = []
    if n_out:
        tail.append(f"{n_out} note{'s' if n_out != 1 else ''} may be out of date")
    if n_aged:
        tail.append(f"{n_aged} note{'s' if n_aged != 1 else ''} {'are' if n_aged != 1 else 'is'} "
                    "old enough to re-check")
    parts = clauses[:3] + tail
    if not parts:
        parts = ["the data changed; every counted fact was refreshed"]
    ask = " Review them now or keep going?" if (n_out or n_aged) else ""
    return head + _join(parts) + "." + ask


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + ", " + items[-1]


def _join_and(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def merge_previous(new: list, prev: list) -> list:
    """Carry status forward: a note a person gave keeps its date, and becomes
    may-be-outdated when the data it depends on moved since they said it."""
    prev_by_id = {r.get("id"): r for r in prev}
    for r in new:
        p = prev_by_id.get(r.get("id"))
        if not p or r.get("source") not in ("told", "web"):
            continue
        reconfirmed = (r.get("as_of", "") > p.get("as_of", ""))
        if reconfirmed:
            continue
        r["as_of"] = p.get("as_of", r.get("as_of"))
        if p.get("status") == "may-be-outdated":
            r["status"] = "may-be-outdated"
            r["data_fp"] = p.get("data_fp", r.get("data_fp"))
        elif p.get("data_fp") and r.get("data_fp") and p["data_fp"] != r["data_fp"] and \
                p["data_fp"].startswith("d:") == r["data_fp"].startswith("d:"):
            r["status"] = "may-be-outdated"
            r["data_fp"] = p["data_fp"]
    return new


def review_questions(report: dict, limit: int = 3) -> list:
    """Structured questions for outdated or aged notes, most important first."""
    from .interview import Q
    items = report["outdated"] + [r for r in report["aged"] if r not in report["outdated"]]
    qs = []
    for r in items[:limit]:
        when = _date_words(r.get("as_of", ""))
        why = r.get("_why") or "it is old enough to re-check"
        who = "You said" if r.get("said_by") in ("owner", "") else f"{r.get('said_by')} said"
        stmt = re.sub(r"\s+", " ", r.get("statement", ""))[:220]
        qs.append(Q(f"review:{r['id']}", "Outdated", f'{who} "{stmt}" ({when}). Since then, {why}. '
                                                      "Still true?",
                    [{"id": "still", "label": "Still true", "desc": "Keep it, dated today"},
                     {"id": "changed", "label": "Changed", "desc": "Pick Other and type what is true now"},
                     {"id": "remove", "label": "Remove this note", "desc": "Take it out of the brain"}],
                    why="", kind="review", priority=1, source="builtin", meta={"record": r}))
    return qs
