"""Deterministic evals against the fixture answer keys (no model involved).

E1 archetype: did detection pick the right data type?
E2 structure: joins, row key and derived tabs found vs the key.
E8 integrity: writing, updating and removing a brain leaves every other part identical.
Freshness (E7): each variant produces the expected freshness signals.

Usage: python evals/structure.py [--fixtures DIR] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts"))

from sheetbrain import brainzip, fresh  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402


def _norm(s: str) -> str:
    return " ".join(str(s).lower().replace("_", " ").split())


def _col_match(found_table: str, found_col: str, want: str) -> bool:
    """want is 'Sheet.Column'; tables may be 'file:Sheet' or 'Sheet#2'."""
    sheet, _, col = want.partition(".")
    t = found_table.split(":")[-1].split("#")[0]
    return _norm(t) == _norm(sheet) and _norm(found_col) == _norm(col)


def score_fixture(key_path: str) -> dict:
    key = json.load(open(key_path))
    d = os.path.dirname(key_path)
    files = [os.path.join(d, f) for f in key["files"]]
    a = Analysis(files)
    det = a.detection
    res = {"fixture": key["fixture"], "archetype_expected": key.get("archetype"),
           "archetype_found": det["archetype"], "confidence": det["confidence"]}
    res["E1_archetype_ok"] = det["archetype"] == key.get("archetype")
    # joins
    want = key.get("joins", [])
    found = [j for j in a.joins if j["band"] in ("auto", "ask")]
    hit = 0
    for w in want:
        ok = any((_col_match(j["from_table"], j["from_col"], w["from"]) and
                  _col_match(j["to_table"], j["to_col"], w["to"])) or
                 (_col_match(j["from_table"], j["from_col"], w["to"]) and
                  _col_match(j["to_table"], j["to_col"], w["from"])) for j in found)
        hit += ok
    res["E2_joins_recall"] = round(hit / len(want), 3) if want else None
    res["joins_found"] = [f"{j['from_table']}.{j['from_col']} -> {j['to_table']}.{j['to_col']} "
                          f"({j['band']}, {j['rows_matched']})" for j in found][:12]
    # derived tabs
    dwant = key.get("derived_tabs", [])
    dfound = {(d2["sheet"], d2["from"]) for fa in a.formulas.values() for d2 in (fa or {}).get("derived", [])}
    res["E2_derived_recall"] = (round(sum(1 for x in dwant if (x["sheet"], x["from"]) in dfound) / len(dwant), 3)
                                if dwant else None)
    res["derived_found"] = sorted(dfound)
    # grain: the key's sheet has a detected row key
    g = key.get("grain") or {}
    if g.get("sheet"):
        t = next((t for t in a.tables if t.sheet == g["sheet"]), None)
        res["grain_key_found"] = (a.keys.get(t.tid) if t else None)
    res["insights"] = [i["statement"] for i in a.insights][:14]
    res["roles"] = {r: v["header"] for r, v in det["roles"].items()}
    return res


def integrity(key_path: str) -> dict:
    key = json.load(open(key_path))
    d = os.path.dirname(key_path)
    src = os.path.join(d, key["files"][0])
    if not src.lower().endswith((".xlsx", ".xlsm")):
        return {"fixture": key["fixture"], "E8": "skipped (csv)"}
    tmp = tempfile.mkdtemp()
    try:
        a = Analysis([src])
        recs = Composer(a, src, "evalbrain0001", {}).compose()
        recs = [r for r in recs if r.get("_travel", "file") == "file"]
        out = os.path.join(tmp, "b.xlsx")
        res = brainzip.write_brain(src, recs, dst=out)
        with zipfile.ZipFile(src) as z1, zipfile.ZipFile(out) as z2:
            before = {i.filename: z1.read(i) for i in z1.infolist()}
            after = {i.filename: z2.read(i) for i in z2.infolist()}
        changed = sorted(k for k in before if before[k] != after.get(k))
        allowed = {"[Content_Types].xml", "docProps/app.xml", res.brain_part}
        ok = all(c in allowed or c.endswith("workbook.xml") or c.endswith("workbook.xml.rels")
                 for c in changed)
        back, warnings, _ = brainzip.read_brain(out)
        rt = [r["statement"] for r in back] == [r["statement"] for r in recs]
        removed = os.path.join(tmp, "r.xlsx")
        brainzip.remove_brain(out, dst=removed)
        with zipfile.ZipFile(removed) as z3:
            restored = {i.filename: z3.read(i) for i in z3.infolist()}
        had_brain = bool(brainzip.read_brain(src)[0])
        # a file that arrived with someone else's brain: removing ours removes the tab entirely,
        # so the check is "everything except the brain is back", not "identical to the original"
        if had_brain:
            removes_ok = not brainzip.read_brain(removed)[0]
        else:
            removes_ok = restored == before
        return {"fixture": key["fixture"], "E8_only_bookkeeping_changed": ok, "changed": changed,
                "E8_roundtrip": rt, "E8_remove_restores": removes_ok, "had_brain": had_brain,
                "records": len(recs)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def freshness(fixtures_dir: str) -> list:
    base = os.path.join(fixtures_dir, "procurement_hotel.xlsx")
    vdir = os.path.join(fixtures_dir, "variants")
    if not (os.path.exists(base) and os.path.isdir(vdir)):
        return [{"E7": "skipped (no variants)"}]
    tmp = tempfile.mkdtemp()
    out = []
    try:
        a0 = Analysis([base])
        answers = {"unit_basis": {"options": ["per_lb"], "labels": ["Per lb"], "text": "", "not_sure": False,
                                  "header": "Units", "kind": "unit", "at": "2026-01-01T00:00:00",
                                  "fact": {"kind": "unit", "class": "data", "depends": ["unit_price"],
                                           "statement": "Prices are compared {answer_labels}."}}}
        recs = Composer(a0, base, "evalfresh0001", answers).compose()
        for fn in sorted(os.listdir(vdir)):
            if not fn.endswith(".xlsx"):
                continue
            p = os.path.join(vdir, fn)
            shutil.copy2(p, os.path.join(tmp, "procurement_hotel.xlsx"))
            tp = os.path.join(tmp, "procurement_hotel.xlsx")
            a = Analysis([tp])
            rep = fresh.check(a, tp, [dict(r) for r in recs])
            out.append({"variant": fn, "line": rep["line"], "changed": rep["changed"],
                        "rows_delta": rep["rows_delta"], "new_values": rep["new_values"],
                        "renamed": rep["renamed"], "removed_cols": rep["removed_cols"],
                        "outdated": len(rep["outdated"]), "refreshed": rep["refreshed"]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default=os.path.join(HERE, "fixtures"))
    ap.add_argument("--json")
    args = ap.parse_args()
    keys = sorted(os.path.join(args.fixtures, f) for f in os.listdir(args.fixtures) if f.endswith(".key.json"))
    report = {"structure": [], "integrity": [], "freshness": []}
    for k in keys:
        report["structure"].append(score_fixture(k))
        report["integrity"].append(integrity(k))
    report["freshness"] = freshness(args.fixtures)
    txt = json.dumps(report, indent=1, default=str)
    if args.json:
        open(args.json, "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
