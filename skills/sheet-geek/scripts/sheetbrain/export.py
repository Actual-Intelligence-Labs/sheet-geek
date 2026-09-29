"""Deterministic outputs built from a brain plus the analysis:

guide       Guide for the next owner (Markdown): what the file is, how to read
            each tab, what the owner said, connections, gotchas, open questions.
dictionary  Data dictionary (CSV): one row per column with type, meaning,
            source and status.
blueprint   App blueprint (Markdown + SQL + Mermaid): tables, keys and
            relationships, the starting point for replacing the sheet with a
            real system.
All outputs are new files. Nothing from person, email or phone columns is copied.
"""
from __future__ import annotations

import csv
import os
import re

from .brain import col_id
from .fingerprint import parse_deps
from .recipes import pct


def _facts_for(records: list, sheet: str, header: str) -> list:
    out = []
    for r in records:
        if r.get("record") not in ("fact", "open"):
            continue
        if (sheet, header) in parse_deps(r.get("depends_on", "")):
            out.append(r)
    return out


def _tag(r: dict) -> str:
    return {"told": "the owner said", "computed": "counted", "inferred": "a guess, not confirmed",
            "web": "from the web"}.get(r.get("source", ""), r.get("source", ""))


def guide(analysis, path: str, records: list) -> str:
    path = os.path.abspath(path)
    name = os.path.basename(path)
    meta = next((r for r in records if r.get("record") == "meta"), {})
    book = next(b for b in analysis.books if b.path == path)
    det = analysis.detection
    lines = [f"# Guide to {name}", "",
             f"Written from the file's brain on {meta.get('as_of', '')}. Notes marked \"a guess\" were never "
             "confirmed by a person.", ""]
    lines += ["## What this file is", ""]
    lines.append(f"- It looks like {det.get('looks_like', 'a spreadsheet')}.")
    for r in records:
        if r.get("record") == "fact" and r.get("kind") in ("goal", "coverage", "grain"):
            lines.append(f"- {r['statement']} ({_tag(r)})")
    lines.append("")
    told = [r for r in records if r.get("record") == "fact" and r.get("source") == "told"
            and r.get("kind") not in ("goal", "coverage")]
    if told:
        lines += ["## What the owner told us", ""]
        for r in told:
            flag = " (may be out of date)" if r.get("status") == "may-be-outdated" else ""
            lines.append(f"- {r['statement']}{flag} ({r.get('said_by', 'owner')}, {r.get('as_of', '')})")
        lines.append("")
    lines += ["## The tabs", ""]
    for s in book.data_sheets():
        tabs = [t for t in analysis.tables if analysis.file_of[t.tid] == path and t.sheet == s.name]
        node = next((r for r in records if r.get("id") == f"sheet:{s.name}"), {})
        lines += [f"### {s.name}", "", node.get("statement", ""), ""]
        for t in tabs:
            if not t.rows:
                continue
            lines.append("| Column | What it is | Notes |")
            lines.append("|---|---|---|")
            for c in analysis.cols[t.tid]:
                if c.type == "empty":
                    continue
                col_node = next((r for r in records if r.get("id") == col_id(t.sheet, c.header, t.tid)), {})
                notes = "; ".join(f"{f['statement']} ({_tag(f)})" for f in _facts_for(records, t.sheet, c.header)
                                  if f.get("record") == "fact")[:400]
                what = col_node.get("statement", "").replace(f"{c.header} on {t.sheet} is ", "")
                lines.append(f"| {_md(c.header)} | {_md(what)} | {_md(notes)} |")
            lines.append("")
    conns = [r for r in records if r.get("record") in ("edge", "link")]
    if conns:
        lines += ["## How it connects", ""]
        lines += [f"- {r['statement']}" for r in conns[:30]]
        lines.append("")
    watch = [r for r in records if r.get("record") in ("insight",) or r.get("kind") == "gotcha"]
    if watch:
        lines += ["## Worth knowing", ""]
        lines += [f"- {r['statement']} ({r.get('as_of', '')})" for r in watch[:20]]
        lines.append("")
    opens = [r for r in records if r.get("record") == "open"]
    if opens:
        lines += ["## Still open", ""]
        lines += [f"- {r['statement']}" for r in opens[:15]]
        lines.append("")
    return "\n".join(lines)


def _md(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


def dictionary(analysis, path: str, records: list, out_path: str) -> str:
    path = os.path.abspath(path)
    rows = [["Sheet", "Column", "Type", "Kind", "Meaning", "Source", "Status", "Blank rate", "Distinct",
             "Example"]]
    for t in analysis.tables:
        if analysis.file_of[t.tid] != path:
            continue
        for c in analysis.cols[t.tid]:
            if c.type == "empty":
                continue
            facts = [f for f in _facts_for(records, t.sheet, c.header) if f.get("record") == "fact"]
            best = next((f for f in facts if f.get("source") == "told"), None) or \
                next((f for f in facts if f.get("source") == "inferred"), None)
            meaning = best["statement"] if best else ""
            example = "" if c.sensitive or c.semantic == "identifier" else (c.samples[0] if c.samples else "")
            rows.append([t.sheet, c.header, c.type, c.semantic, meaning, (best or {}).get("source", "computed"),
                         (best or {}).get("status", "current"), pct(c.blank_rate), c.distinct, example])
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        for r in rows:
            w.writerow([("'" + str(v)) if str(v).startswith(("=", "+", "-", "@")) else v for v in r])
    return out_path


_SQL_TYPE = {"number": "NUMERIC", "date": "DATE", "bool": "BOOLEAN", "text": "TEXT", "empty": "TEXT"}


def _ident(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "col"
    return s if not s[0].isdigit() else "c_" + s


def blueprint(analysis, path: str, records: list) -> str:
    path = os.path.abspath(path)
    tabs = [t for t in analysis.tables if analysis.file_of[t.tid] == path and t.rows]
    derived = analysis.derived
    names = {t.tid: _ident(t.sheet if len([x for x in tabs if x.sheet == t.sheet]) == 1 else t.tid) for t in tabs}
    lines = [f"# App blueprint for {os.path.basename(path)}", "",
             "A starting data model for replacing this spreadsheet with a real system, built from what the "
             "brain found. Tabs that are calculated from other tabs become views, not tables.", ""]
    ddl = []
    fks = []
    for t in tabs:
        if t.tid in derived or t.wide:
            continue
        cols = [c for c in analysis.cols[t.tid] if c.type != "empty"]
        key = analysis.keys.get(t.tid) or []
        body = []
        for c in cols:
            typ = "INTEGER" if c.type == "number" and c.integers and c.semantic == "identifier" else \
                _SQL_TYPE.get(c.type, "TEXT")
            null = " NOT NULL" if c.nulls == 0 else ""
            body.append(f"  {_ident(c.header)} {typ}{null}")
        if key:
            body.append(f"  PRIMARY KEY ({', '.join(_ident(k) for k in key)})")
        for j in analysis.joins:
            if j["from_table"] == t.tid and j["to_unique"] and j["band"] == "auto" \
                    and j["to_table"] in names and j["to_table"] not in derived:
                body.append(f"  FOREIGN KEY ({_ident(j['from_col'])}) REFERENCES "
                            f"{names[j['to_table']]}({_ident(j['to_col'])})")
                fks.append((names[t.tid], names[j["to_table"]], j["from_col"]))
        ddl.append(f"CREATE TABLE {names[t.tid]} (\n" + ",\n".join(body) + "\n);")
    lines += ["## Tables", ""]
    for t in tabs:
        role = "view (calculated from other tabs)" if t.tid in derived else (
            "time series (keep as a report or unpivot into rows)" if t.wide else "table")
        key = analysis.keys.get(t.tid) or []
        lines.append(f"- **{t.sheet}**: {role}, {t.n_rows:,} rows" + (f", unique by {', '.join(key)}" if key else ""))
    lines += ["", "## Relationships", ""]
    if fks:
        lines += [f"- {a}.{_ident(c)} points to {b}" for a, b, c in fks]
    else:
        lines.append("- No many-to-one relationships were certain enough to enforce.")
    lines += ["", "```mermaid", "erDiagram"]
    for a, b, c in fks:
        lines.append(f"  {b} ||--o{{ {a} : \"{_ident(c)}\"")
    for t in tabs:
        if t.tid in derived or t.wide:
            continue
        lines.append(f"  {names[t.tid]} {{")
        for c in [c for c in analysis.cols[t.tid] if c.type != "empty"][:25]:
            lines.append(f"    {_SQL_TYPE.get(c.type, 'TEXT').lower()} {_ident(c.header)}")
        lines.append("  }")
    lines += ["```", "", "## SQL", "", "```sql"] + ddl + ["```", ""]
    rules = [r for r in records if r.get("record") == "fact" and r.get("source") == "told"]
    if rules:
        lines += ["## Business rules the owner stated (build these in)", ""]
        lines += [f"- {r['statement']}" for r in rules]
        lines.append("")
    opens = [r for r in records if r.get("record") == "open"]
    if opens:
        lines += ["## Answer before building", ""]
        lines += [f"- {r['statement']}" for r in opens[:10]]
    return "\n".join(lines)
