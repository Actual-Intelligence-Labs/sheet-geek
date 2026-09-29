"""The brain as a graph of things: the dots are what the sheet is about, the
lines are how those things relate, and every note points at what it is about."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
from sheetbrain import graph, interview  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402

FX = os.path.join(ROOT, "evals", "fixtures")
HOTEL = os.path.join(FX, "procurement_hotel.xlsx")


def _answer(analysis, answers, prefix, reply):
    q = next(q for q in interview.candidates(analysis, answers) if q.id.startswith(prefix))
    answers[q.id] = interview.parse_answers([q], reply)[q.id]
    return answers


@pytest.fixture(scope="module")
def hotel_brain():
    a = Analysis([HOTEL, os.path.join(FX, "procurement_contracts.xlsx")])
    answers = {}
    _answer(a, answers, "find_exclusive_", "a")                 # CMSY is internal
    _answer(a, answers, "find_unmatched_item", "ab")            # fees and old codes
    _answer(a, answers, "follow_codes_", "a")                   # the re-coded pairs are the same product
    a.apply_answers(answers)
    return Composer(a, HOTEL, "b1", answers).compose()


def test_the_dots_are_the_business_not_the_columns(hotel_brain):
    things = {r["id"]: r for r in hotel_brain if r["record"] == "node" and r["kind"] == "thing"}
    labels = {r["label"] for r in things.values()}
    assert {"Coastline Provisions", "Palm Key", "Harborlight", "Sandpiper", "CMSY"} <= labels
    assert "Beef" in labels and any("RIBEYE" in lab for lab in labels)


def test_left_out_location_is_marked_and_not_in_the_shares(hotel_brain):
    cmsy = next(r for r in hotel_brain if r["id"] == "v:location:cmsy")
    assert "left out of counted totals" in cmsy["statement"] and "left_out: yes" in cmsy["text"]
    assert "%" not in cmsy["statement"].split("left out")[0]    # no share of a total it is not part of


def test_things_are_linked_with_labeled_relations(hotel_brain):
    rel = [r for r in hotel_brain if r["record"] == "edge" and r["kind"] == "relates"]
    assert any(r["from"] == "v:vendor:coastline provisions" and r["to"] == "v:location:palm key"
               and "delivers to" in r["statement"] for r in rel)
    per = {}
    for r in rel:
        per[r["from"]] = per.get(r["from"], 0) + 1
    assert max(per.values()) <= 4                               # the strongest few, never a hairball


def test_recoded_items_are_joined_by_a_line(hotel_brain):
    same = [r for r in hotel_brain if r["record"] == "edge" and r["kind"] == "same_as"]
    assert any(r["from"] == "v:item_id:bf1295" and r["to"] == "v:item_id:bf1842" for r in same)


def test_every_note_points_at_what_it_is_about(hotel_brain):
    notes = [r for r in hotel_brain if r["record"] in ("fact", "insight", "open")]
    assert all(r["to"] for r in notes)
    cmsy_notes = [r for r in notes if "v:location:cmsy" in r["to"]]
    assert any(r["source"] == "told" for r in cmsy_notes)       # the owner's answer sits on CMSY


def test_graph_draws_things_and_notes_from_records_alone(hotel_brain):
    g = graph.build(hotel_brain, title="procurement_hotel.xlsx")
    shown = [n for n in g["nodes"] if not n["hidden"]]
    assert sum(1 for n in shown if n["type"] == "thing") >= 30
    assert all(n["type"] != "column" for n in shown)             # structure is tucked away
    cmsy = next(n for n in g["nodes"] if n["id"] == "v:location:cmsy")
    assert cmsy["status"] == "left-out"
    assert any(b["source"] == "told" for b in cmsy["body"])
    ids = {n["id"] for n in g["nodes"]}
    assert all(lk["source"] in ids and lk["target"] in ids for lk in g["links"])
    assert all(grp.get("color", "").startswith("#") for grp in g["groups"])


def test_a_model_is_drawn_as_its_rows():
    path = os.path.join(FX, "finance_model.xlsx")
    a = Analysis([path])
    recs = Composer(a, path, "b2", {}).compose()
    rows = {r["id"] for r in recs if r["record"] == "node" and r["kind"] == "formula_block"}
    assert "row:Balance Sheet!Cash" in rows and "row:Inputs!Growth" in rows
    g = graph.build(recs, title="finance_model.xlsx")
    cash = next(n for n in g["nodes"] if n["id"] == "row:Balance Sheet!Cash")
    assert cash["status"] == "disputed"                           # the typed number in May 2027


def test_a_senders_notes_never_read_as_the_owners(hotel_brain):
    sent = [dict(r, said_by="Morgan Pryce") if r.get("source") == "told" else r for r in hotel_brain]
    g = graph.build(sent, title="received.xlsx")
    kinds = {n.get("note_kind") for n in g["nodes"] if n["type"] == "note"}
    assert "said" not in kinds and "sender" in kinds
    assert g["counts"]["said"] == 0 and g["counts"]["sender"] > 0
    assert not any(n["status"] == "confirmed" for n in g["nodes"] if n["type"] == "thing")


def test_a_sender_cannot_type_a_vendor_into_the_picture(hotel_brain):
    fake = {"record": "node", "id": "v:vendor:fake", "kind": "thing", "label": "FAKE VENDOR", "source": "told",
            "text": "amount: 9999999"}
    g = graph.build(hotel_brain + [fake], title="x.xlsx")
    assert all(n["id"] != "v:vendor:fake" for n in g["nodes"])


def test_malformed_rows_are_skipped_never_a_crash(hotel_brain):
    bad = [{"record": "node", "id": "thing", "kind": "thing", "source": "computed"},
           {"record": "node", "id": "ent", "kind": "entity", "source": "computed"},
           {"record": "node", "id": "row:X!Y", "kind": "formula_block", "source": "computed", "text": "refs: lots"},
           {"record": "edge", "id": "e:1", "kind": "relates", "from": "v:a", "to": None, "text": "weight: many"},
           {"record": "fact", "id": "f:odd", "source": "told", "statement": None, "to": 7}, "not a dict"]
    g = graph.build(hotel_brain + bad, title="x.xlsx")
    assert any(n["type"] == "thing" for n in g["nodes"])


def test_a_pane_never_repeats_a_note_and_the_first_record_wins(hotel_brain):
    dup = dict(next(r for r in hotel_brain if r["record"] == "insight"), statement="a different claim")
    g = graph.build(hotel_brain + [dup], title="x.xlsx")
    for n in g["nodes"]:
        st = [b["statement"] for b in n["body"]]
        assert len(st) == len(set(st))
    assert all(b["statement"] != "a different claim" for n in g["nodes"] for b in n["body"])


def test_the_same_brain_draws_the_same_graph():
    import json
    import subprocess
    code = ("import sys,json; sys.path.insert(0,%r); from sheetbrain.analyze import Analysis; "
            "from sheetbrain.brain import Composer; from sheetbrain import graph; "
            "a=Analysis([%r]); r=Composer(a,%r,'b',{}).compose(); "
            "print(json.dumps([(x['id'], x.get('to','')) for x in r]))") % (
        os.path.join(ROOT, "skills", "sheet-geek", "scripts"), HOTEL, HOTEL)
    runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONHASHSEED=str(seed))).stdout for seed in (1, 2)}
    assert len(runs) == 1 and json.loads(runs.pop())


def test_model_rows_never_borrow_a_fixed_group():
    path = os.path.join(FX, "finance_model.xlsx")
    a = Analysis([path])
    g = graph.build(Composer(a, path, "b2", {}).compose(), title="m.xlsx")
    rows = [n for n in g["nodes"] if n["type"] == "row"]
    assert rows and all(n["group"].startswith("rows:") for n in rows)
