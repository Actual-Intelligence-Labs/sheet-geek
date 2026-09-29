"""The brain drawn as native Excel shapes: what gets drawn, where, and that
nothing from a spreadsheet can turn into markup, a link or a formula."""
import collections
import json
import math
import os
import random
import sys
import time
import xml.etree.ElementTree as ET

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts"))
from sheetbrain import xldraw  # noqa: E402

NS = {"xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
SAMPLES = ["hotel", "finance", "ledger"]
FIRST_COL = 19
PX = xldraw.EMU


def _sample(name):
    with open(os.path.join(HERE, "..", "dev", f"sample_graph_v2_{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _as_sender(g, every=1):
    """The same brain as it reads in a file someone sent: their told notes are the sender's."""
    g = json.loads(json.dumps(g))
    said = [n for n in g["nodes"] if n.get("note_kind") == "said"]
    for i, n in enumerate(said):
        if i % every == 0:
            n["note_kind"], n["group"] = "sender", "sender"
    g["groups"].append({"id": "sender", "label": "What the sender said (not checked)", "color": "#D9925B",
                        "type": "note"})
    return g


def _draw(graph):
    return xldraw.drawing_parts(graph, origin_emu_x=26879550, first_col=FIRST_COL)


def _shapes(root):
    """Every shape, connector and group, with its own xfrm, keyed by id."""
    out = {}
    for el in root.iter():
        tag = el.tag.split("}")[1]
        if tag not in ("sp", "cxnSp", "grpSp"):
            continue
        pr = el.find(".//xdr:cNvPr", NS)
        xf = el.find("xdr:grpSpPr/a:xfrm" if tag == "grpSp" else "xdr:spPr/a:xfrm", NS)
        off, ext = xf.find("a:off", NS), xf.find("a:ext", NS)
        geom = el.find("xdr:spPr/a:prstGeom", NS)
        out[pr.get("id")] = {"tag": tag, "el": el, "name": pr.get("name"), "xfrm": xf,
                             "geom": geom.get("prst") if geom is not None else None,
                             "x": int(off.get("x")), "y": int(off.get("y")),
                             "w": int(ext.get("cx")), "h": int(ext.get("cy"))}
    return out


def _text(el):
    return [t.text or "" for t in el.iter(f"{{{NS['a']}}}t")]


def _links(shapes):
    """(start shape, end shape, connector) for every line."""
    out = []
    for s in shapes.values():
        if s["tag"] == "cxnSp":
            st, en = s["el"].find(".//a:stCxn", NS), s["el"].find(".//a:endCxn", NS)
            out.append((shapes[st.get("id")], shapes[en.get("id")], s))
    return out


def _gap(a, b):
    dx = max(0, a["x"] - (b["x"] + b["w"]), b["x"] - (a["x"] + a["w"]))
    dy = max(0, a["y"] - (b["y"] + b["h"]), b["y"] - (a["y"] + a["h"]))
    return max(dx, dy) / PX


@pytest.fixture(scope="module", params=SAMPLES)
def drawn(request):
    g = _sample(request.param)
    res = _draw(g)
    return g, res, ET.fromstring(res["xml"])


def test_well_formed_deterministic_and_fast():
    for name in SAMPLES:
        g = _sample(name)
        t = time.time()
        a = _draw(g)
        assert time.time() - t < 5
        b = _draw(json.loads(json.dumps(g)))
        assert a["xml"] == b["xml"]            # same brain, same bytes
        ET.fromstring(a["xml"])
        assert a["content_type"] == "application/vnd.openxmlformats-officedocument.drawing+xml"


def test_the_order_records_arrive_in_does_not_move_anything():
    """A brain's records can come back in another order (a set in the writer, a
    file saved by another app). The picture must not change with it."""
    for name in SAMPLES:
        g = _sample(name)
        base = _draw(g)["xml"]
        rng = random.Random(7)
        for _ in range(3):
            h = json.loads(json.dumps(g))
            rng.shuffle(h["links"])
            things = [n for n in h["nodes"] if n["type"] in ("thing", "row")]
            rest = [n for n in h["nodes"] if n["type"] not in ("thing", "row")]
            rng.shuffle(things)
            h["nodes"] = rest[:1] + things + rest[1:]
            assert _draw(h)["xml"] == base, name


def test_draws_every_thing_and_every_owner_note(drawn):
    g, res, root = drawn
    shapes = _shapes(root)
    names = [s["name"] for s in shapes.values()]
    texts = " ".join(t for s in shapes.values() if s["tag"] == "sp" for t in _text(s["el"]))
    assert res["nodes"] <= xldraw.MAX_NODES
    p = xldraw.plan(g)
    picked = xldraw.pick(g)
    assert all(not n.get("hidden") for n in picked)                      # no columns, no minor notes
    assert sum(1 for n in picked if n["type"] == "file") == 1
    things = [n for n in g["nodes"] if n["type"] in ("thing", "row") and not n.get("hidden")]
    assert len(things) <= 90 and all(n in picked for n in things)       # every vendor, item and row
    assert p["dropped"] == 0
    assert all(k in picked for k in g["nodes"] if k["type"] == "kind")
    said = [n for n in g["nodes"] if n.get("note_kind") == "said" and not n.get("hidden")]
    on_map = [n for n in said if n in p["notes"]]
    in_panel = [n for sec in p["panel"] if sec[0] == "said" for n in sec[2]]
    assert said and sorted(n["id"] for n in on_map + in_panel) == sorted(n["id"] for n in said)
    assert sum(1 for x in names if x.startswith("Note ")) == len(on_map)   # as note shapes on the map
    for n in in_panel:                                                  # and the rest, in words, in the panel
        assert xldraw.wrap_lines(xldraw.clean(xldraw.note_text(n), 400), 8, 300, 3)[0][:20] in texts


def test_owner_notes_about_the_whole_file_do_not_circle_the_file_dot(drawn):
    g, _, root = drawn
    shapes = _shapes(root)
    file_dot = next(s for s in shapes.values() if s["tag"] == "sp" and (s["name"] or "").startswith("Dot ")
                    and s["geom"] == "ellipse" and s["w"] == round(36 * PX))
    for a, b, _ in _links(shapes):
        if a["name"].startswith("Note "):
            assert b is not file_dot and b["name"].startswith("Dot ")      # notes only sit on things


def test_owner_notes_sit_next_to_their_dot_with_a_line_in_the_note_color(drawn):
    g, _, root = drawn
    shapes = _shapes(root)
    said = next(x["color"] for x in g["groups"] if x["id"] == "said").lstrip("#").upper()
    by_note = collections.defaultdict(list)
    for a, b, c in _links(shapes):
        if a["name"].startswith("Note "):
            by_note[a["name"]].append((a, b))
            ln = c["el"].find("xdr:spPr/a:ln", NS)
            assert int(ln.get("w")) >= 15875 and ln.find("a:solidFill/a:srgbClr", NS).get("val") == said
    assert by_note
    for pairs in by_note.values():
        note = pairs[0][0]
        assert note["geom"] == "foldedCorner"
        assert note["el"].find("xdr:spPr/a:solidFill/a:srgbClr", NS).get("val") == said
        if len(pairs) == 1:                                              # right beside its one dot
            assert _gap(note, pairs[0][1]) <= 60, note["name"]


def test_sender_notes_never_look_like_the_owners():
    for name in SAMPLES:
        g = _as_sender(_sample(name), every=2)                          # half the owner's, half the sender's
        root = ET.fromstring(_draw(g)["xml"])
        shapes = _shapes(root)
        said = next(x["color"] for x in g["groups"] if x["id"] == "said").lstrip("#").upper()
        owner = [s for s in shapes.values() if (s["name"] or "").startswith("Note ")]
        sender = [s for s in shapes.values() if (s["name"] or "").startswith("Sender note ")]
        assert owner and sender
        for s in sender:
            assert s["geom"] != "foldedCorner"
            ln = s["el"].find("xdr:spPr/a:ln", NS)
            assert ln.find("a:prstDash", NS) is not None                   # dashed: not checked
            fill = s["el"].find("xdr:spPr/a:solidFill/a:srgbClr", NS).get("val")
            assert fill != said and ln.find("a:solidFill/a:srgbClr", NS).get("val") == "D9925B"
        for a, b, c in _links(shapes):
            if a["name"].startswith("Sender note "):
                ln = c["el"].find("xdr:spPr/a:ln", NS)
                assert ln.find("a:prstDash", NS) is not None
                assert ln.find("a:solidFill/a:srgbClr", NS).get("val") != said
        key = next(s for s in shapes.values() if s["name"] == "Brain key")
        assert "What the sender said (not checked)" in "".join(_text(key["el"]))


def test_found_notes_are_numbered_on_their_dots_and_listed_in_full(drawn):
    g, _, root = drawn
    shapes = _shapes(root)
    marks = [s for s in shapes.values() if (s["name"] or "").startswith("Marker ")]
    listed = {s["name"].split()[-1] for s in shapes.values() if (s["name"] or "").startswith("Panel marker ")}
    assert marks
    for m in marks:
        num = m["name"].split(":")[0].split()[-1]
        assert _text(m["el"]) == [num] and num in listed
        ends = [b for a, b, _ in _links(shapes) if a is m]
        assert ends and all(b["name"].startswith("Dot ") for b in ends)
        assert min(_gap(m, b) for b in ends) <= 140
    p = xldraw.plan(g)
    found = [e for sec in p["panel"] if sec[0] == "found" for e in sec[2]]
    assert 0 < len(found) <= xldraw.MAX_FOUND
    # a finding about a thing is marked before one about a tab or the whole file
    reach = [bool(m) for _, m in found]
    assert reach == sorted(reach, reverse=True)


def test_names_are_unique_readable_and_keep_their_code():
    g = _sample("hotel")
    names = [s["name"].split(": ", 1)[1] for s in _shapes(ET.fromstring(_draw(g)["xml"])).values()
             if (s["name"] or "").startswith("Dot ")]
    assert len(names) == len(set(names))
    assert not any(n.isupper() and len(n) > 8 for n in names)            # no SHOUTING item names
    assert xldraw._title_case("CHICKEN BREAST BNLS SKLS 6 OZ (BF1842)") == "Chicken Breast Bnls Skls 6 OZ (BF1842)"
    assert xldraw._title_case("CMSY") == "CMSY"
    assert xldraw.dot_label({"type": "thing", "label": "CHICKEN BREAST BNLS SKLS 6 OZ (BF1842)"}) == \
        "Chicken Breast Bnls... (BF1842)"
    rows = [{"id": "row:P&L!D&A", "type": "row", "label": "D&A", "sheet": "P&L", "group": "rows:P&L"},
            {"id": "row:Cash Flow!D&A", "type": "row", "label": "D&A", "sheet": "Cash Flow", "group": "rows:Cash Flow"}]
    assert sorted(xldraw._unique_labels(rows).values()) == ["D&A (Cash Flow)", "D&A (P&L)"]


def test_note_text_is_short_and_in_the_owners_words():
    def t(statement, label=""):
        return xldraw.note_text({"summary": statement, "label": label})
    assert t("The owner's goal for this data is: check what I'm charged.") == "Goal: check what I'm charged"
    assert t("The owner asked to build first: Contract audit.") == "Build first: Contract audit"
    assert t("Negative Ext Price rows are, per the owner: credits or returns.",
             "Negatives: credits or returns") == "Negatives: credits or returns"
    assert t("Re-coded items, per the owner (BF1295 -> BF1842 (CHICKEN)): yes, all the same.") == \
        "Re-coded items: yes, all the same"
    assert t("Credits are netted into totals, per the owner, so totals are after credits.") == \
        "Credits are netted into totals, so totals are after credits"
    got = t("CMSY in Location is internal, not a real location (its Invoice #s start TR-), per the owner, so "
            "counted totals leave its rows out. The owner's words: \"It's the commissary, a central prep "
            "kitchen, not a hotel.\".")
    assert got == "It's the commissary, a central prep kitchen, not a hotel"
    assert t("The typed number inside Revenue is, per the owner: A test to take out. In the owner's words: "
             "Priya's test of a 7% price increase, never approved.", "Typed factor: A test to take out") == \
        "Typed factor: A test to take out. Priya's test of a 7% price increase, never approved"


def test_lines_are_bound_to_shapes_and_run_centre_to_centre(drawn):
    _, res, root = drawn
    shapes = _shapes(root)
    cxns = [s for s in shapes.values() if s["tag"] == "cxnSp"]
    assert len(cxns) == res["lines"] > 0
    for c in cxns:
        st = c["el"].find(".//a:stCxn", NS)
        en = c["el"].find(".//a:endCxn", NS)
        assert st is not None and en is not None
        a, b = shapes[st.get("id")], shapes[en.get("id")]
        assert a["tag"] == b["tag"] == "sp"
        for end, idx in ((a, st.get("idx")), (b, en.get("idx"))):
            assert 0 <= int(idx) < (8 if end["geom"] == "ellipse" else 4)
        fh, fv = c["xfrm"].get("flipH") == "1", c["xfrm"].get("flipV") == "1"
        x1, x2 = (c["x"] + c["w"], c["x"]) if fh else (c["x"], c["x"] + c["w"])
        y1, y2 = (c["y"] + c["h"], c["y"]) if fv else (c["y"], c["y"] + c["h"])
        for (px, py), s in (((x1, y1), a), ((x2, y2), b)):
            assert abs(px - (s["x"] + s["w"] / 2)) <= 2 and abs(py - (s["y"] + s["h"] / 2)) <= 2


def test_the_skeleton_lines_are_faint(drawn):
    _, _, root = drawn
    for a, b, c in _links(_shapes(root)):
        alpha = c["el"].find("xdr:spPr/a:ln/a:solidFill/a:srgbClr/a:alpha", NS)
        if b["name"].startswith("Dot ") and a["name"].startswith("Dot ") and alpha is not None \
                and int(alpha.get("val")) <= 10000:
            break
    else:
        pytest.fail("no faint thing-to-kind line")


def test_dot_and_name_move_together(drawn):
    _, _, root = drawn
    groups = [s for s in _shapes(root).values() if s["tag"] == "grpSp"]
    assert groups
    for g in groups:
        xf = g["xfrm"]
        cho, che = xf.find("a:chOff", NS), xf.find("a:chExt", NS)
        assert (int(cho.get("x")), int(cho.get("y"))) == (g["x"], g["y"])      # children map 1 to 1
        assert (int(che.get("cx")), int(che.get("cy"))) == (g["w"], g["h"])
        kids = [k for k in g["el"] if k.tag.endswith("}sp")]
        assert [k.find("xdr:spPr/a:prstGeom", NS).get("prst") for k in kids] == ["ellipse", "rect"]
        assert kids[1].find("xdr:nvSpPr/xdr:cNvSpPr", NS).get("txBox") == "1"
        dot = kids[0].find("xdr:spPr/a:xfrm/a:off", NS)
        assert (int(dot.get("x")), int(dot.get("y"))) == (g["x"], g["y"])        # group starts at the dot


def test_every_shape_is_cell_anchored_on_the_grid(drawn):
    _, res, root = drawn
    first, last, width = res["cols"]
    assert first == FIRST_COL and width == xldraw.COL_CHARS
    anchors = list(root)
    assert anchors
    for an in anchors:
        tag = an.tag.split("}")[1]
        assert tag in ("oneCellAnchor", "twoCellAnchor")
        for side in ("from", "to"):
            el = an.find(f"xdr:{side}", NS)
            if el is None:
                continue
            col, row = int(el.find("xdr:col", NS).text), int(el.find("xdr:row", NS).text)
            assert FIRST_COL <= col <= last and 1 <= row <= res["rows"]
            assert 0 <= int(el.find("xdr:colOff", NS).text) < xldraw.COL_EMU    # never clamped by Excel
            assert 0 <= int(el.find("xdr:rowOff", NS).text) < xldraw.ROW_EMU


def test_layout_stays_on_the_canvas_and_nothing_overlaps():
    for g in [_sample(n) for n in SAMPLES] + [_as_sender(_sample("hotel"))]:
        shapes = _shapes(ET.fromstring(_draw(g)["xml"]))
        W, H = xldraw.CANVAS_W * PX, xldraw.CANVAS_H * PX
        right = (xldraw.CANVAS_W - xldraw.MARGIN - xldraw.PANEL_W) * PX      # the map stays left of the panel
        boxes = []
        for s in shapes.values():
            if s["tag"] == "sp" and (s["name"] or "").startswith(("Dot ", "Note ", "Sender note ", "Marker ")):
                assert 0 <= s["x"] and s["x"] + s["w"] <= right and xldraw.HEADER_H * PX <= s["y"] + s["h"] <= H
                boxes.append(s)
            if s["tag"] == "sp":
                assert s["x"] + s["w"] <= W + PX
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                assert ox <= 0 or oy <= 0, (a["name"], b["name"])


def test_the_panel_fits_and_says_what_is_left_out():
    for name in SAMPLES:
        res = _draw(_sample(name))
        shapes = _shapes(ET.fromstring(res["xml"]))
        panel = next(s for s in shapes.values() if s["name"] == "Brain side panel")
        for s in shapes.values():
            if (s["name"] or "").startswith(("Panel ",)):
                assert panel["x"] <= s["x"] and s["x"] + s["w"] <= panel["x"] + panel["w"] + 2 * PX, s["name"]
                assert panel["y"] <= s["y"] and s["y"] + s["h"] <= panel["y"] + panel["h"], s["name"]
        tip = "".join(_text(next(s for s in shapes.values() if s["name"] == "Brain tip")["el"]))
        p = xldraw.plan(_sample(name))
        if p["table_only"] + res["panel"]["hidden"]:
            assert "only in the table" in tip
        assert "table to the left" in tip and "\u2014" not in tip


def test_group_colors_are_the_graph_colors():
    g = _sample("hotel")
    root = ET.fromstring(_draw(g)["xml"])
    color = {x["id"]: x["color"].lstrip("#").upper() for x in g["groups"]}
    p = xldraw.plan(g)
    labels = xldraw._unique_labels(p["hubs"] + p["things"])
    by_label = {labels[n["id"]]: color[n["group"]] for n in p["hubs"] + p["things"] if n["type"] in ("thing", "kind")}
    seen = 0
    for s in _shapes(root).values():
        if s["tag"] == "sp" and (s["name"] or "").startswith("Dot "):
            label = s["name"].split(": ", 1)[1]
            if label in by_label:
                fill = s["el"].find("xdr:spPr/a:solidFill/a:srgbClr", NS)
                assert fill.get("val") == by_label[label]
                seen += 1
    assert seen >= 40


HOSTILE = "<script>alert(1)</script> & \"q\" 'a' =HYPERLINK(\"http://evil\",\"x\")\x01\x08‮\U000E0041﻿"


def _hostile_graph():
    g = _sample("hotel")
    g = json.loads(json.dumps(g))
    g["title"] = HOSTILE
    g["subtitle"] = HOSTILE
    for n in g["nodes"]:
        n["label"] = HOSTILE + n["label"]
        n["summary"] = HOSTILE
    for grp in g["groups"]:
        grp["label"] = HOSTILE
        grp["color"] = "url(javascript:alert(1))" if grp["id"] in ("vendor", "said") else grp["color"]
    return g


def test_spreadsheet_text_never_becomes_markup_links_or_formulas():
    for g in (_hostile_graph(), _as_sender(_hostile_graph())):
        res = _draw(g)
        xml = res["xml"].decode("utf-8")
        root = ET.fromstring(res["xml"])                        # still well formed
        assert "<script" not in xml and "&lt;script&gt;" in xml
        assert "hlink" not in xml and "fld" not in xml and "http://evil" in xml.replace("&quot;", '"')
        for ch in ("\x01", "\x08", "‮", "\U000E0041", "﻿"):
            assert ch not in xml
        for el in root.iter():
            tag = el.tag.split("}")[1]
            if tag == "sp":
                assert el.get("macro") == "" and el.get("textlink") == ""
            if tag == "cxnSp":
                assert el.get("macro") == ""
            if tag == "srgbClr":
                assert len(el.get("val")) == 6 and all(c in "0123456789ABCDEF" for c in el.get("val"))
        texts = [t.text or "" for t in root.iter(f"{{{NS['a']}}}t")]
        assert any("=HYPERLINK" in t for t in texts)          # shown as words, inside shape text only
        assert all(len(t) <= 400 for t in texts)


def test_empty_odd_and_huge_graphs():
    for g in ({}, [], {"nodes": None, "links": None, "groups": None},
              {"nodes": [{"id": 5}, "x", {"id": "a", "type": "thing", "size": "NaN", "group": "g"},
                         {"id": "n", "type": "note", "note_kind": "said", "summary": None, "label": None}],
               "links": [7, {"source": "a", "target": "nowhere", "weight": "heavy"},
                         {"source": "n", "target": "a", "type": "about"}, {"source": 3, "target": None}],
               "groups": ["x", {"id": None}]}):
        res = _draw(g)
        ET.fromstring(res["xml"])
    big = {"title": "big.xlsx", "groups": [{"id": "vendor", "color": "#5B8DEF"}],
           "nodes": [{"id": "brain:1", "label": "big.xlsx", "type": "file", "group": "file", "size": 34}]
           + [{"id": f"v:{i}", "label": f"Vendor {i}", "type": "thing", "group": "vendor", "size": 6 + i % 20}
              for i in range(1500)]
           + [{"id": f"f:{i}", "label": f"Note {i}", "type": "note", "group": "said", "note_kind": "said",
               "size": 6.5} for i in range(300)]
           + [{"id": f"i:{i}", "label": f"Found {i}", "type": "note", "group": "found", "note_kind": "found",
               "size": 5} for i in range(300)],
           "links": [{"source": f"v:{i}", "target": f"v:{i + 1}", "type": "relates", "weight": 1}
                     for i in range(1499)]
           + [{"source": f"f:{i}", "target": f"v:{i}", "type": "about"} for i in range(300)]
           + [{"source": f"i:{i}", "target": f"v:{i * 3}", "type": "about"} for i in range(300)]}
    t = time.time()
    res = _draw(big)
    assert time.time() - t < 10
    assert res["nodes"] <= xldraw.MAX_NODES and res["lines"] <= xldraw.MAX_LINES
    root = ET.fromstring(res["xml"])
    tip = "".join(t.text or "" for t in root.iter(f"{{{NS['a']}}}t") if "table to the left" in (t.text or ""))
    assert "smaller things are not drawn" in tip and "only in the table" in tip


def test_clean_and_wrap():
    assert xldraw.clean("a‮b\U000E0041c\x01d￾  e\n f") == "abcd e f"
    assert xldraw.clean("one two three four", 12) == "one two..."
    assert xldraw.note_lines("Books basis: Cash", 8, 168) == ["Books basis: Cash"]
    two = xldraw.note_lines("Typed factor: A test to take out of the model before it goes out", 8, 168)
    assert len(two) == 2 and all(len(x.split()) > 2 for x in two)            # balanced, no lone word
    long = xldraw.note_lines("x" * 90 + " " + "y" * 90, 8, 168)
    assert all(xldraw.text_px(x, 8) <= 168 and x.endswith("...") for x in long)
    three = xldraw.wrap_lines("word " * 80, 8, 200, 3)
    assert len(three) == 3 and three[-1].endswith("...") and all(xldraw.text_px(x, 8) <= 200 for x in three)
    assert xldraw.wrap_lines("short", 8, 200, 3) == ["short"]
    assert xldraw.wrap_lines("x" * 300, 8, 100, 2)[0].endswith("...")
    assert xldraw.text_px("iii", 10) < xldraw.text_px("MMM", 10)
    assert math.isclose(xldraw.text_px("0", 12), 0.507 * 16, rel_tol=0.01)
