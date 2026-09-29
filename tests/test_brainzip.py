"""File-layer safety: the brain tab is added, updated and removed without
touching any other part of the workbook."""
import os
import re
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "sheet-geek", "scripts"))
from sheetbrain import brainzip  # noqa: E402

xlsxwriter = pytest.importorskip("xlsxwriter")


def _parts(path):
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


@pytest.fixture()
def book(tmp_path):
    p = tmp_path / "book.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws = wb.add_worksheet("Data")
    ws.write_row(0, 0, ["Item", "Qty", "Price", "Total"])
    for i in range(1, 21):
        ws.write_row(i, 0, [f"I{i:03}", i, 2.5])
        ws.write_formula(i, 3, f"=B{i + 1}*C{i + 1}", None, i * 2.5)
    ch = wb.add_chart({"type": "column"})
    ch.add_series({"values": "=Data!$D$2:$D$21"})
    ws.insert_chart("F2", ch)
    ws.data_validation("A2:A21", {"validate": "list", "source": ["I001", "I002"]})
    ws2 = wb.add_worksheet("Summary")
    ws2.write_formula(0, 0, "=SUM(Data!D2:D21)", None, 525.0)
    wb.define_name("Totals", "=Data!$D$2:$D$21")
    wb.close()
    return p


RECS = [
    {"record": "meta", "id": "brain:abc123abc123", "kind": "generic", "label": "book.xlsx",
     "statement": "Notes about this workbook's data.", "source": "computed", "status": "current"},
    {"record": "fact", "id": "f:1", "kind": "unit", "label": "Qty unit",
     "statement": "Qty is counted in cases of 12.", "source": "told", "status": "confirmed"},
]


def test_add_keeps_every_other_part_identical(book, tmp_path):
    before = _parts(book)
    out = tmp_path / "out.xlsx"
    res = brainzip.write_brain(str(book), RECS, dst=str(out))
    after = _parts(out)
    assert res.mode == "added"
    changed = {k for k in before if before[k] != after.get(k)}
    assert changed <= {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml",
                       "docProps/app.xml", "xl/worksheets/sheet1.xml"}
    for p in ("xl/charts/chart1.xml", "xl/worksheets/sheet2.xml"):
        assert before[p] == after[p]
    # the tab that was selected only lost its "selected" flag, so the workbook opens on the brain alone
    assert res.view_parts == ["xl/worksheets/sheet1.xml"]
    assert after["xl/worksheets/sheet1.xml"] == before["xl/worksheets/sheet1.xml"].replace(b' tabSelected="1"', b"", 1)
    recs, warnings, info = brainzip.read_brain(str(out))
    assert info["state"] == "visible"
    assert [r["statement"] for r in recs] == [r["statement"] for r in RECS]


def test_update_touches_only_the_brain_part(book, tmp_path):
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a))
    res = brainzip.write_brain(str(a), RECS + [dict(RECS[1], id="f:2")], dst=str(b))
    assert res.mode == "replaced"
    pa, pb = _parts(a), _parts(b)
    assert {k for k in pa if pa[k] != pb[k]} == {res.brain_part}


def test_noop_write_is_unchanged(book, tmp_path):
    a = tmp_path / "a.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a))
    res = brainzip.write_brain(str(a), RECS, dst=str(tmp_path / "c.xlsx"))
    assert res.mode == "unchanged"


def test_hidden_then_visible(book, tmp_path):
    a = tmp_path / "a.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a), state="hidden")
    assert brainzip.read_brain(str(a))[2]["state"] == "hidden"
    b = tmp_path / "b.xlsx"
    brainzip.write_brain(str(a), RECS, dst=str(b), state="visible")
    assert brainzip.read_brain(str(b))[2]["state"] == "visible"
    with pytest.raises(brainzip.BrainError):
        brainzip.write_brain(str(book), RECS, dst=str(tmp_path / "x.xlsx"), state="veryHidden")


def test_remove_restores_the_original_bytes(book, tmp_path):
    a = tmp_path / "a.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a))
    r = tmp_path / "r.xlsx"
    brainzip.remove_brain(str(a), dst=str(r))
    assert _parts(r) == _parts(book)


def test_in_place_write_makes_a_backup(book, tmp_path):
    bdir = tmp_path / "backups"
    res = brainzip.write_brain(str(book), RECS, backup_dir=str(bdir))
    assert res.backup and os.path.exists(res.backup)
    assert not os.path.dirname(res.backup) == os.path.dirname(str(book))


def test_refuses_when_open_in_excel(book, tmp_path):
    lock = book.parent / ("~$" + book.name)
    lock.write_text("x")
    with pytest.raises(brainzip.BrainError):
        brainzip.write_brain(str(book), RECS, dst=str(tmp_path / "o.xlsx"))


def test_never_overwrites_a_users_own_brain_named_sheet(tmp_path):
    p = tmp_path / "u.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    wb.add_worksheet("Data").write(0, 0, "x")
    wb.add_worksheet("_Brain").write(0, 0, "my own notes")
    wb.close()
    with pytest.raises(brainzip.BrainError):
        brainzip.write_brain(str(p), RECS, dst=str(tmp_path / "o.xlsx"))


def test_long_text_chunks_and_reassembles(book, tmp_path):
    long = "x" * 70000 + "\U0001F600" * 10
    recs = RECS + [{"record": "fact", "id": "f:long", "kind": "history", "label": "long",
                    "statement": "Long note.", "source": "told", "status": "confirmed", "text": long}]
    out = tmp_path / "long.xlsx"
    brainzip.write_brain(str(book), recs, dst=str(out))
    back, _, _ = brainzip.read_brain(str(out))
    assert next(r for r in back if r["id"] == "f:long")["text"] == long


def test_hygiene_and_formula_neutralizing(book, tmp_path):
    recs = RECS + [{"record": "fact", "id": "f:h", "kind": "rule", "label": "h",
                    "statement": "=HYPERLINK(\"http://x\")", "source": "told", "status": "confirmed",
                    "text": "hi\U000E0049\U000E0047there‮"}]
    out = tmp_path / "h.xlsx"
    brainzip.write_brain(str(book), recs, dst=str(out))
    back, warnings, _ = brainzip.read_brain(str(out))
    h = next(r for r in back if r["id"] == "f:h")
    assert h["statement"] == "=HYPERLINK(\"http://x\")"   # text, stored with a leading apostrophe
    assert h["text"] == "hithere"
    with zipfile.ZipFile(out) as z:
        brain_xml = next(z.read(n) for n in z.namelist() if n.endswith("sheet3.xml"))
    assert b"<f>" not in brain_xml and b"'=HYPERLINK" in brain_xml


def test_csv_sidecar_roundtrip(tmp_path):
    c = tmp_path / "d.csv"
    c.write_text("a,b\n1,2\n")
    brainzip.write_sidecar(str(c), RECS)
    back, _, info = brainzip.read_brain(str(c))
    assert info["present"] and len(back) == 2
    assert c.read_text() == "a,b\n1,2\n"


# --------------------------------------------------------------------------
# the brain drawn on its tab (sheetbrain.xldraw), next to the table
# --------------------------------------------------------------------------
import json  # noqa: E402

DEV = os.path.join(os.path.dirname(__file__), "..", "dev")


def _graph(name="ledger"):
    with open(os.path.join(DEV, f"sample_graph_v2_{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


BRAIN_OWN = {"xl/worksheets/sheet3.xml", "xl/worksheets/_rels/sheet3.xml.rels", "xl/drawings/brainmap1.xml"}
WIRING = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels", "[Content_Types].xml", "docProps/app.xml"}
VIEW = {"xl/worksheets/sheet1.xml"}     # the tab that was selected: only its "selected" flag goes


def _rezip(src, dst, edit):
    """Copy a package entry by entry, letting edit(name, data) rename or change parts."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name, data = edit(info.filename, zin.read(info))
            if name:
                zout.writestr(name, data)


def test_graph_add_keeps_the_users_own_drawings_and_charts(book, tmp_path):
    before = _parts(book)
    assert "xl/drawings/drawing1.xml" in before and "xl/charts/chart1.xml" in before
    out = tmp_path / "g.xlsx"
    res = brainzip.write_brain(str(book), RECS, dst=str(out), graph=_graph())
    after = _parts(out)
    assert res.mode == "added" and res.drawing_part == "xl/drawings/brainmap1.xml"
    assert {k for k in before if before[k] != after.get(k)} <= WIRING | VIEW
    assert set(after) - set(before) == BRAIN_OWN
    for p in before:
        if p.startswith(("xl/drawings/", "xl/charts/", "xl/worksheets/", "xl/theme/", "xl/styles")) and p not in VIEW:
            assert before[p] == after[p], p
    ct = after["[Content_Types].xml"].decode()
    assert ('<Override PartName="/xl/drawings/brainmap1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>') in ct
    rels = after["xl/worksheets/_rels/sheet3.xml.rels"].decode()
    assert 'Id="rId1"' in rels and 'Target="../drawings/brainmap1.xml"' in rels and "/relationships/drawing" in rels
    sheet = after["xl/worksheets/sheet3.xml"].decode()
    assert sheet.index("<pageMargins") < sheet.index('<drawing r:id="rId1"/>') < sheet.index("</worksheet>")
    assert ('<sheetView tabSelected="1" showGridLines="0" zoomScale="80" zoomScaleNormal="80" '
            'workbookViewId="0" topLeftCell="T1">') in sheet    # the tab opens on the drawing, zoomed to fit
    assert '<col min="20" max=' in sheet and 'customHeight="1"' in sheet  # the grid the shapes sit on
    recs, _, info = brainzip.read_brain(str(out))
    assert [r["statement"] for r in recs] == [r["statement"] for r in RECS]
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(str(out))
    assert wb.sheetnames[-1] == "_brain"


def test_graph_replace_redraws_in_place(book, tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a), graph=_graph("ledger"))
    res = brainzip.write_brain(str(a), RECS + [dict(RECS[1], id="f:2")], dst=str(b), graph=_graph("hotel"))
    pa, pb = _parts(a), _parts(b)
    assert res.mode == "replaced" and set(pa) == set(pb)
    assert {k for k in pa if pa[k] != pb[k]} == {"xl/worksheets/sheet3.xml", "xl/drawings/brainmap1.xml"}
    again = brainzip.write_brain(str(b), RECS + [dict(RECS[1], id="f:2")], dst=str(tmp_path / "c.xlsx"),
                                 graph=_graph("hotel"))
    assert again.mode == "unchanged"                      # same brain, same picture, same bytes


def test_graph_remove_restores_the_original_bytes(book, tmp_path):
    a, b, r = tmp_path / "a.xlsx", tmp_path / "b.xlsx", tmp_path / "r.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a), graph=_graph("ledger"))
    brainzip.write_brain(str(a), RECS, dst=str(b), graph=_graph("finance"))
    res = brainzip.remove_brain(str(b), dst=str(r))
    assert set(BRAIN_OWN) <= set(res.changed_parts)
    assert _parts(r) == _parts(book)


def test_writing_without_a_graph_takes_the_drawing_away(book, tmp_path):
    a, b, plain = tmp_path / "a.xlsx", tmp_path / "b.xlsx", tmp_path / "plain.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a), graph=_graph())
    res = brainzip.write_brain(str(a), RECS, dst=str(b))
    brainzip.write_brain(str(book), RECS, dst=str(plain))
    assert res.mode == "replaced" and res.drawing_part is None
    assert _parts(b) == _parts(plain)                     # exactly a brain that never had a drawing
    assert b"brainmap" not in _parts(b)["[Content_Types].xml"]


def test_graph_added_to_a_plain_brain_then_removed(book, tmp_path):
    a, b, r = tmp_path / "a.xlsx", tmp_path / "b.xlsx", tmp_path / "r.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(a))
    res = brainzip.write_brain(str(a), RECS, dst=str(b), graph=_graph())
    pa, pb = _parts(a), _parts(b)
    assert res.mode == "replaced"
    assert set(pb) - set(pa) == {"xl/worksheets/_rels/sheet3.xml.rels", "xl/drawings/brainmap1.xml"}
    assert {k for k in pa if pa[k] != pb[k]} == {"xl/worksheets/sheet3.xml", "[Content_Types].xml"}
    ct_a, ct_b = pa["[Content_Types].xml"], pb["[Content_Types].xml"]
    assert len(ct_b) > len(ct_a) and ct_b.replace(ct_b[ct_b.index(b"<Override PartName=\"/xl/drawings/brainmap1"):
                                                        ct_b.index(b"</Types>")], b"") == ct_a
    brainzip.remove_brain(str(b), dst=str(r))
    assert _parts(r) == _parts(book)


def test_drawing_part_name_never_collides(book, tmp_path):
    crowded = tmp_path / "crowded.xlsx"
    theirs = b'<?xml version="1.0"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"/>'

    def edit(name, data):
        if name == "[Content_Types].xml":
            data = data.replace(b"</Types>", b'<Override PartName="/xl/drawings/BrainMap1.xml" ContentType='
                                b'"application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>')
        return name, data
    _rezip(book, crowded, edit)
    with zipfile.ZipFile(crowded, "a") as z:           # someone else's parts with our stem, any case
        z.writestr("xl/drawings/BrainMap1.xml", theirs)
        z.writestr("xl/drawings/_rels/brainmap2.xml.rels", b'<?xml version="1.0"?><Relationships '
                   b'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    before = _parts(crowded)
    out, r = tmp_path / "out.xlsx", tmp_path / "r.xlsx"
    res = brainzip.write_brain(str(crowded), RECS, dst=str(out), graph=_graph())
    after = _parts(out)
    assert res.drawing_part == "xl/drawings/brainmap3.xml"
    assert after["xl/drawings/BrainMap1.xml"] == theirs
    assert all(before[p] == after[p] for p in before if p not in WIRING | VIEW)
    brainzip.remove_brain(str(out), dst=str(r))
    assert _parts(r) == before


def test_in_place_write_with_graph_keeps_a_backup(book, tmp_path):
    original = book.read_bytes()
    res = brainzip.write_brain(str(book), RECS, backup_dir=str(tmp_path / "bk"), graph=_graph())
    assert res.mode == "added" and res.backup and open(res.backup, "rb").read() == original
    assert "xl/drawings/brainmap1.xml" in _parts(book)


def test_redraws_a_drawing_excel_renamed_on_save(book, tmp_path):
    """Excel renames parts when it saves. The brain finds its drawing through
    the tab's own relationship, redraws that part and removes it cleanly."""
    a, renamed, b, r = (tmp_path / n for n in ("a.xlsx", "renamed.xlsx", "b.xlsx", "r.xlsx"))
    brainzip.write_brain(str(book), RECS, dst=str(a), graph=_graph())

    def edit(name, data):
        if name == "xl/drawings/brainmap1.xml":
            return "xl/drawings/drawing2.xml", data
        if name in ("xl/worksheets/_rels/sheet3.xml.rels", "[Content_Types].xml"):
            data = data.replace(b"brainmap1.xml", b"drawing2.xml")
        return name, data
    _rezip(a, renamed, edit)
    res = brainzip.write_brain(str(renamed), RECS, dst=str(b), graph=_graph("hotel"))
    pr, pb = _parts(renamed), _parts(b)
    assert res.drawing_part == "xl/drawings/drawing2.xml" and set(pr) == set(pb)
    assert {k for k in pr if pr[k] != pb[k]} == {"xl/drawings/drawing2.xml"}   # same notes, new picture
    brainzip.remove_brain(str(b), dst=str(r))
    pr2 = _parts(r)
    assert not any(k.startswith("xl/drawings/drawing2") or k == "xl/worksheets/_rels/sheet3.xml.rels" for k in pr2)
    assert b"drawing2.xml" not in pr2["[Content_Types].xml"]
    assert pr2["xl/drawings/drawing1.xml"] == _parts(book)["xl/drawings/drawing1.xml"]


def test_graph_text_from_the_sheet_stays_text(book, tmp_path):
    g = _graph()
    g["title"] = "<b>x</b> & =cmd|' /C calc'!A0 \u202e"
    for n in g["nodes"]:
        n["label"] = "</a:t><a:hlinkClick r:id=\"rId9\"/>" + n["label"]
    out = tmp_path / "h.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(out), graph=g)
    xml = _parts(out)["xl/drawings/brainmap1.xml"]
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)
    assert b"<a:hlinkClick" not in xml and b"&lt;/a:t&gt;&lt;a:hlinkClick" in xml
    assert "\u202e".encode() not in xml and b"&lt;b&gt;x&lt;/b&gt; &amp; =cmd" in xml


# --------------------------------------------------------------------------
# the tab's columns: what a note is first, read by header name
# --------------------------------------------------------------------------
def test_columns_put_the_note_first_and_old_brains_still_read():
    assert brainzip.COLUMNS[:6] == [brainzip.FORMAT_LABEL, "label", "statement", "source", "as_of", "about"]
    assert brainzip.COLUMNS[6:] == ["id", "kind", "status", "said_by", "depends_on", "data_fp", "class",
                                    "stale_after", "ref", "part", "text", "from"]
    rec = {"record": "fact", "id": "f:1", "kind": "unit", "label": "Qty unit", "statement": "Cases of 12.",
           "source": "told", "status": "confirmed", "as_of": "2026-09-01", "said_by": "owner",
           "from": "", "to": "v:item:a | v:item:b", "text": "k: v"}
    rows = brainzip.records_to_rows([rec])
    assert rows[1][5] == "v:item:a | v:item:b" and rows[1][2] == "Cases of 12."
    back, _ = brainzip.rows_to_records(rows)
    assert back[0]["to"] == rec["to"] and "about" not in back[0]
    # a brain written with the old order and a "to" column reads the same
    old_head = [brainzip.FORMAT_LABEL, "id", "kind", "label", "statement", "source", "status", "as_of",
                "said_by", "from", "to", "depends_on", "data_fp", "class", "stale_after", "ref", "part", "text"]
    old_row = [rec.get("record" if h == brainzip.FORMAT_LABEL else h, "") for h in old_head]
    back2, _ = brainzip.rows_to_records([old_head, old_row])
    assert back2 == back
    # both names present: the filled one wins, whichever comes first
    both, _ = brainzip.rows_to_records([[brainzip.FORMAT_LABEL, "to", "about", "id"], ["fact", "", "x:1", "f:9"]])
    assert both[0]["to"] == "x:1"
    assert brainzip.records_to_rows([{"record": "fact", "id": "f:2", "about": "x:2"}])[1][5] == "x:2"


def test_old_column_order_in_a_real_file_still_reads(tmp_path):
    p = tmp_path / "old.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    wb.add_worksheet("Data").write(0, 0, "x")
    head = [brainzip.FORMAT_LABEL, "id", "kind", "label", "statement", "source", "to"]
    br = wb.add_worksheet("_brain")
    br.write_row(0, 0, head)
    br.write_row(1, 0, ["fact", "f:1", "unit", "Qty unit", "Cases of 12.", "told", "v:item:a"])
    wb.close()
    recs, _, info = brainzip.read_brain(str(p))
    assert info["present"] and recs[0]["to"] == "v:item:a" and recs[0]["statement"] == "Cases of 12."
    out = tmp_path / "new.xlsx"
    brainzip.write_brain(str(p), recs, dst=str(out))          # rewritten in the new order, same notes
    again, _, _ = brainzip.read_brain(str(out))
    assert again[0]["to"] == "v:item:a" and again[0]["statement"] == "Cases of 12."


def test_the_tab_reads_well_without_touching_the_workbooks_styles(book, tmp_path):
    long_text = "\n".join(f"key {i}: " + "words " * 12 for i in range(6))
    recs = RECS + [{"record": "fact", "id": "f:t", "kind": "rule", "label": "t", "statement": "Has detail.",
                    "source": "computed", "status": "current", "text": long_text},
                   {"record": "edge", "id": "e:1", "kind": "relates", "label": "e", "statement": "A to B.",
                    "source": "computed", "status": "current", "from": "v:a:1", "to": "v:b:2", "text": "w: 1"}]
    out = tmp_path / "w.xlsx"
    before = _parts(book)
    brainzip.write_brain(str(book), recs, dst=str(out))
    after = _parts(out)
    assert after["xl/styles.xml"] == before["xl/styles.xml"]
    sheet = after["xl/worksheets/sheet3.xml"].decode()
    assert '<col min="3" max="3" width="110" customWidth="1"/>' in sheet            # the statement column
    assert '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>' in sheet
    assert ' s="' not in sheet                                                        # no style of ours
    # long text stops at its own column: an empty cell next to it (only where "from" is empty)
    row_t = re.search(r'<row r="4">(.*?)</row>', sheet, re.S).group(1)
    assert '<c r="R4" t="inlineStr"><is><t></t></is></c>' in row_t
    row_e = re.search(r'<row r="5">(.*?)</row>', sheet, re.S).group(1)
    assert '<c r="R5" t="inlineStr"><is><t>v:a:1</t></is></c>' in row_e and row_e.count('r="R5"') == 1
    assert not re.search(r'<c r="R2"', sheet)                                        # no text, nothing added
    back, _, _ = brainzip.read_brain(str(out))
    assert [r.get("from", "") for r in back] == ["", "", "", "v:a:1"]
    assert next(r for r in back if r["id"] == "f:t")["text"] == long_text


# --------------------------------------------------------------------------
# the workbook opens on the brain tab, and removing the brain puts that back
# --------------------------------------------------------------------------
openpyxl = pytest.importorskip("openpyxl")


def _view(parts):
    wb = parts["xl/workbook.xml"]
    m = re.search(rb'<workbookView\b[^>]*>', wb)
    active = re.search(rb'activeTab="(\d+)"', m.group(0)) if m else None
    selected = sorted(n for n, v in parts.items() if n.startswith("xl/worksheets/sheet")
                      and n.endswith(".xml") and re.search(rb'<sheetView\b[^>]*tabSelected="1"', v))
    return int(active.group(1)) if active else 0, selected


def _book_active_second(tmp_path):
    p = tmp_path / "second.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws = wb.add_worksheet("Orders")
    ws.write_row(0, 0, ["Vendor", "Amount"])
    ws.write_row(1, 0, ["Acme", 12.5])
    ws2 = wb.add_worksheet("Summary")
    ws2.write(0, 0, "Total")
    ws2.activate()
    wb.close()
    return p


def test_the_workbook_opens_on_the_brain_tab(book, tmp_path):
    out = tmp_path / "o.xlsx"
    brainzip.write_brain(str(book), RECS, dst=str(out), graph=_graph())
    after = _parts(out)
    assert _view(after) == (2, ["xl/worksheets/sheet3.xml"])                  # the brain, and only the brain
    wb = openpyxl.load_workbook(str(out))
    assert wb.active.title == "_brain" and wb.sheetnames == ["Data", "Summary", "_brain"]
    assert [bool(ws.sheet_view.tabSelected) for ws in wb.worksheets] == [False, False, True]
    pd = pytest.importorskip("pandas")
    first = pd.read_excel(str(out))                                             # no sheet name: the first tab
    assert list(first.columns)[:3] == ["Item", "Qty", "Price"]


def test_active_sheet_not_first_add_replace_remove_restore_every_byte(tmp_path):
    src = _book_active_second(tmp_path)
    original = _parts(src)
    assert _view(original) == (1, ["xl/worksheets/sheet2.xml"])
    a, b, c, r = (tmp_path / n for n in ("a.xlsx", "b.xlsx", "c.xlsx", "r.xlsx"))
    res = brainzip.write_brain(str(src), RECS, dst=str(a), graph=_graph())
    pa = _parts(a)
    assert _view(pa) == (2, ["xl/worksheets/sheet3.xml"]) and res.view_parts == ["xl/worksheets/sheet2.xml"]
    assert pa["xl/worksheets/sheet2.xml"] == original["xl/worksheets/sheet2.xml"].replace(b' tabSelected="1"', b"", 1)
    assert pa["xl/worksheets/sheet1.xml"] == original["xl/worksheets/sheet1.xml"]
    assert openpyxl.load_workbook(str(a)).active.title == "_brain"
    res = brainzip.write_brain(str(a), RECS + [dict(RECS[1], id="f:2")], dst=str(b), graph=_graph("hotel"))
    pb = _parts(b)
    assert res.mode == "replaced" and res.view_parts == []
    assert {k for k in pa if pa[k] != pb[k]} == {"xl/worksheets/sheet3.xml", "xl/drawings/brainmap1.xml"}
    again = brainzip.write_brain(str(b), RECS + [dict(RECS[1], id="f:2")], dst=str(c), graph=_graph("hotel"))
    assert again.mode == "unchanged"
    brainzip.remove_brain(str(b), dst=str(r))
    assert _parts(r) == original                                                # every byte, the view included
    assert openpyxl.load_workbook(str(r)).active.title == "Summary"


def test_in_place_add_then_remove_restores_the_file(tmp_path):
    src = _book_active_second(tmp_path)
    before = src.read_bytes()
    brainzip.write_brain(str(src), RECS, backup_dir=str(tmp_path / "bk"), graph=_graph())
    brainzip.write_brain(str(src), RECS, backup_dir=str(tmp_path / "bk"), graph=_graph("finance"))
    brainzip.remove_brain(str(src), backup_dir=str(tmp_path / "bk"))
    assert _parts(src) == _parts_bytes(before)


def _parts_bytes(blob):
    import io
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {i.filename: z.read(i) for i in z.infolist()}


def test_grouped_tabs_are_ungrouped_and_put_back(tmp_path):
    p = tmp_path / "grouped.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    sheets = [wb.add_worksheet(n) for n in ("A", "B", "C")]
    sheets[0].write(0, 0, "x")
    sheets[1].select()
    sheets[2].select()
    wb.close()
    original = _parts(p)
    assert len(_view(original)[1]) == 3
    out, r = tmp_path / "o.xlsx", tmp_path / "r.xlsx"
    res = brainzip.write_brain(str(p), RECS, dst=str(out))
    assert _view(_parts(out)) == (3, ["xl/worksheets/sheet4.xml"])            # Excel will not open it grouped
    assert sorted(res.view_parts) == ["xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml"]
    brainzip.remove_brain(str(out), dst=str(r))
    assert _parts(r) == original


def _minimal_book(path, with_views=False):
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
          'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override '
          'PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.'
          'sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    views = "<bookViews/>" if with_views else ""
    wbx = ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
           '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' + views +
           '<sheets><sheet name="Only" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wbr = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
           '2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
           'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    sh = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
          '2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in (("[Content_Types].xml", ct), ("_rels/.rels", rels), ("xl/workbook.xml", wbx),
                     ("xl/_rels/workbook.xml.rels", wbr), ("xl/worksheets/sheet1.xml", sh)):
            z.writestr(n, d)


def test_a_workbook_with_no_view_list_gets_one_and_loses_it_again(tmp_path):
    p, out, r = tmp_path / "min.xlsx", tmp_path / "o.xlsx", tmp_path / "r.xlsx"
    _minimal_book(p)
    original = _parts(p)
    brainzip.write_brain(str(p), RECS, dst=str(out), graph=_graph())
    wbx = _parts(out)["xl/workbook.xml"]
    assert b'<bookViews><workbookView activeTab="1"/></bookViews><sheets>' in wbx
    assert openpyxl.load_workbook(str(out)).active.title == "_brain"
    brainzip.remove_brain(str(out), dst=str(r))
    assert _parts(r) == original


def test_an_empty_view_list_is_left_alone(tmp_path):
    p, out, r = tmp_path / "min.xlsx", tmp_path / "o.xlsx", tmp_path / "r.xlsx"
    _minimal_book(p, with_views=True)
    original = _parts(p)
    brainzip.write_brain(str(p), RECS, dst=str(out))
    wbx = _parts(out)["xl/workbook.xml"]
    assert wbx.count(b"bookViews") == 1                                          # never a second list
    brainzip.remove_brain(str(out), dst=str(r))
    assert _parts(r) == original


def test_a_hidden_brain_leaves_the_view_alone_and_hiding_puts_it_back(tmp_path):
    src = _book_active_second(tmp_path)
    original = _parts(src)
    h1, v, h2, r = (tmp_path / n for n in ("h1.xlsx", "v.xlsx", "h2.xlsx", "r.xlsx"))
    brainzip.write_brain(str(src), RECS, dst=str(h1), state="hidden")
    p1 = _parts(h1)
    assert _view(p1) == (1, ["xl/worksheets/sheet2.xml"])                      # opens where it always did
    assert p1["xl/worksheets/sheet2.xml"] == original["xl/worksheets/sheet2.xml"]
    brainzip.write_brain(str(h1), RECS, dst=str(v), state="visible")
    assert _view(_parts(v)) == (2, ["xl/worksheets/sheet3.xml"])
    brainzip.write_brain(str(v), RECS, dst=str(h2), state="hidden")
    assert _parts(h2) == p1                                                      # hiding again: the same bytes
    brainzip.remove_brain(str(h2), dst=str(r))
    assert _parts(r) == original


def test_a_put_back_record_from_a_sender_can_only_undo_our_own_edits(tmp_path):
    src = _book_active_second(tmp_path)
    a, forged, r = tmp_path / "a.xlsx", tmp_path / "forged.xlsx", tmp_path / "r.xlsx"
    brainzip.write_brain(str(src), RECS, dst=str(a))
    import base64
    b64 = lambda x: base64.b64encode(x).decode()  # noqa: E731
    evil = [("xl/worksheets/sheet1.xml", b'<sheetData>', b'<sheetData><row r="99"/>'),
            ("xl/workbook.xml", b'<sheets>', b'<definedNames/><sheets>'),
            ("[Content_Types].xml", b'<Types', b'<Types evil="1"'),
            ("xl/worksheets/sheet1.xml", b'<sheetView workbookViewId="0"/>', b'<sheetView workbookViewId="0" x="1"/>')]
    extra = " ".join(f"{b64(p.encode())}.{b64(w)}.{b64(n)}" for p, w, n in evil)

    def edit(name, data):
        if name == "xl/worksheets/sheet3.xml":
            m = re.search(rb"<!--spreadsheet-brain put-back 1: ([^>]*) -->", data)
            data = data[:m.end(1)] + b" " + extra.encode() + data[m.end(1):]
        return name, data
    _rezip(a, forged, edit)
    fp = _parts(forged)
    assert extra.encode() in fp["xl/worksheets/sheet3.xml"]
    brainzip.remove_brain(str(forged), dst=str(r))
    pr = _parts(r)
    for part in ("xl/worksheets/sheet1.xml", "[Content_Types].xml"):
        assert pr[part] == _parts(src)[part]                                    # nothing of theirs applied
    assert b"definedNames" not in pr["xl/workbook.xml"] and b"row r=\"99\"" not in pr["xl/worksheets/sheet1.xml"]
    assert openpyxl.load_workbook(str(r)).active.title == "Summary"


def test_after_an_app_saved_the_file_removal_still_opens_on_a_real_tab(tmp_path):
    """If another app rewrote the view (so the record no longer matches), removing
    the brain cannot restore the old bytes, but the workbook must still open on a tab
    that exists and is visible."""
    src = _book_active_second(tmp_path)
    a, resaved, r = tmp_path / "a.xlsx", tmp_path / "resaved.xlsx", tmp_path / "r.xlsx"
    brainzip.write_brain(str(src), RECS, dst=str(a))

    def edit(name, data):
        if name == "xl/workbook.xml":
            data = re.sub(rb'<workbookView\b[^>]*/>', b'<workbookView xWindow="0" yWindow="0" windowWidth="100" '
                          b'windowHeight="100" activeTab="2"/>', data)
        return name, data
    _rezip(a, resaved, edit)
    brainzip.remove_brain(str(resaved), dst=str(r))
    wb = openpyxl.load_workbook(str(r))
    assert wb.sheetnames == ["Orders", "Summary"] and wb.active.title in ("Orders", "Summary")
    assert _view(_parts(r))[0] in (0, 1)


def test_writes_into_a_workbook_that_declares_r_on_each_sheet(book, tmp_path):
    # openpyxl without lxml writes xmlns:r on every <sheet>, not on the root
    local = tmp_path / "local_r.xlsx"
    parts = _parts(book)
    wb = parts["xl/workbook.xml"].decode()
    decl = re.search(r'\s+xmlns:r="[^"]+"', wb).group(0)
    wb = wb.replace(decl, "", 1)
    wb = re.sub(r"<sheet ", "<sheet" + decl + " ", wb)
    parts["xl/workbook.xml"] = wb.encode()
    with zipfile.ZipFile(local, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    out = tmp_path / "out.xlsx"
    brainzip.write_brain(str(local), RECS, dst=str(out))
    recs, _w, info = brainzip.read_brain(str(out))
    assert info["where"] == "_brain" and any(r["id"] == "f:1" for r in recs)
    back = tmp_path / "back.xlsx"
    brainzip.remove_brain(str(out), dst=str(back))
    assert _parts(back) == _parts(local)
