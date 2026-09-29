"""The whole loop through the CLI: start, answer (with an apostrophe), save,
check, freshness after an edit, graph, export, share copy, remove."""
import json
import os
import subprocess
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SB = os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts", "sb.py")
PLAYBOOKS = os.path.join(ROOT, "dev", "test_playbooks")


def make_book(path, extra_rows=0, new_location=None):
    wb = xlsxwriter.Workbook(str(path))
    wb.set_properties({"created": __import__("datetime").datetime(2026, 1, 1)})
    v = wb.add_worksheet("Vendors")
    v.write_row(0, 0, ["VendorID", "VendorName"])
    vendors = [("V1", "Harbor Foods"), ("V2", "Gulf Paper"), ("V3", "Key Linen")]
    for i, row in enumerate(vendors, 1):
        v.write_row(i, 0, row)
    o = wb.add_worksheet("Orders")
    o.write_row(0, 0, ["OrderID", "Order Date", "Property", "SKU", "Vendor", "Qty", "Unit Cost", "Total"])
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    import datetime as dt
    locs = ["Bayfront", "Lido", "Siesta"]
    n = 30 + extra_rows
    for i in range(n):
        loc = new_location if (new_location and i >= 30) else locs[i % 3]
        qty = (i % 7) + 1
        cost = [4.5, 12.0, 7.25][i % 3]
        o.write(i + 1, 0, f"PO-{1000 + i}")
        o.write_datetime(i + 1, 1, dt.datetime(2026, 1, 1) + dt.timedelta(days=i), fmt)
        o.write_row(i + 1, 2, [loc, f"SKU-{i % 5}", vendors[i % 3][1], qty, cost])
        o.write_formula(i + 1, 7, f"=F{i + 2}*G{i + 2}", None, qty * cost)
    wb.close()


def sb(*args, stdin=None, env=None):
    p = subprocess.run([sys.executable, SB, *args], input=stdin, capture_output=True, text=True, env=env,
                       timeout=120)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


@pytest.fixture()
def env(tmp_path):
    e = dict(os.environ)
    e["SPREADSHEET_BRAIN_HOME"] = str(tmp_path / "home")
    e["SPREADSHEET_BRAIN_PLAYBOOKS"] = PLAYBOOKS
    return e


def test_full_loop(tmp_path, env):
    book = tmp_path / "orders.xlsx"
    make_book(book)
    r = sb("start", str(book), env=env)
    assert r["ok"] and r["next"] == "ask"
    assert "Purchase history" in r["say"] and "Gulf Paper is 48%" in r["say"]
    assert "3 tabs" not in r["say"] and "2 tabs, 33 rows" in r["say"]
    assert all(len(q["options"]) <= 4 for q in r["ask"]["questions"])
    r = sb("answer", str(book), "--json", "-", env=env,
           stdin=json.dumps({"Goal": "Check what I'm charged", "Coverage": "Yes, everything"}))
    assert r["ok"], r
    while r["next"] == "ask":
        r = sb("answer", str(book), "--text", "-", env=env,
               stdin="1 Harbor Foods is cost plus 5% through 2027. Between us, Rick at Harbor pads invoices.")
    assert r["next"] == "preview"
    r = sb("save", str(book), env=env)
    assert r["ok"] and "now has a brain" in r["say"]
    assert "private note" in r["say"]
    # every count the card gives is the rows read back from the file
    sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
    from sheetbrain import brainzip
    back = brainzip.read_brain(str(book))[0]
    assert r["written"][0]["verified"] and r["written"][0]["records"] == len(back)
    assert f"In the file: {len(back)} rows" in r["say"], r["say"]
    r = sb("read", str(book), env=env)
    assert "cost plus 5%" in r["say"] and "Rick" not in r["say"]
    r = sb("check", str(book), env=env)
    assert "no changes" in r["say"]
    from zipfile import ZipFile
    r = sb("graph", str(book), "--out", str(tmp_path / "map.html"), env=env)
    assert os.path.exists(r["path"]) and r["nodes"] > 5
    html = open(r["path"], encoding="utf-8").read()
    assert "default-src 'none'" in html
    for kind in ("guide", "dictionary", "blueprint"):
        r = sb("export", str(book), "--kind", kind, env=env)
        assert os.path.exists(r["path"])
    r = sb("share", str(book), "--make-copy", "nocommercial", "--out", str(tmp_path / "clean.xlsx"), env=env)
    assert r["ok"]
    with ZipFile(tmp_path / "clean.xlsx") as z:
        blob = b"".join(z.read(n) for n in z.namelist())
    assert b"cost plus 5%" not in blob
    r = sb("remove", str(book), env=env)
    assert r["ok"]


def _carry_brain(src, dst):
    sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
    from sheetbrain import brainzip
    recs, _, _ = brainzip.read_brain(str(src))
    brainzip.write_brain(str(dst), recs)
    return recs


def test_freshness_after_append(tmp_path, env):
    book = tmp_path / "orders.xlsx"
    make_book(book)
    sb("start", str(book), env=env)
    sb("answer", str(book), "--text", "-", stdin="1c 2a 3a", env=env)
    assert sb("save", str(book), env=env)["ok"]
    # same rows appended, same categories: nothing a person said should be flagged
    more = tmp_path / "more.xlsx"
    make_book(more, extra_rows=9)
    _carry_brain(book, more)
    r = sb("check", str(more), env=env)
    assert "9 new rows" in r["say"], r["say"]
    assert "may be out of date" not in r["say"], r["say"]
    # a new location appears: reported as a new value
    venice = tmp_path / "venice.xlsx"
    make_book(venice, extra_rows=6, new_location="Venice")
    _carry_brain(book, venice)
    r = sb("check", str(venice), env=env)
    assert "Venice" in r["say"], r["say"]
