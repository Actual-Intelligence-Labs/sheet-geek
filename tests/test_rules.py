"""One scoped rule model applied to every counted number, and rules from any
typed answer: proposed, read back with counts, applied only when ticked, and
marked on every number they would change until then."""
import datetime as dt
import json
import os
import re
import subprocess
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
import synth  # noqa: E402
from sheetbrain import findings, interview, rules  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402
from sheetbrain.rules import Rule  # noqa: E402

FX = os.path.join(ROOT, "evals", "fixtures")
SB = os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts", "sb.py")


def _pb(insights, **roles):
    """A one-table playbook: each role a header, a type, a kind and a unit."""
    spec = {}
    for rid, (header, typ, kind, unit, additive) in roles.items():
        spec[rid] = {"label": header, "headers": [header.lower()], "type": typ, "kind": kind,
                     "entity": header.lower()}
        if unit:
            spec[rid]["unit"] = unit
        if additive is not None:
            spec[rid]["additive"] = additive
    return {"generic": {"id": "generic", "roles": spec, "insights": insights, "questions": [],
                        "graph": {"size_by": "amount", "entities": [], "relations": [], "mode": "entities"}}}


@pytest.fixture()
def log(tmp_path):
    """Site, Unit, Group, Qty, Price, Amount: 120 rows, Amount = Qty x Price."""
    path = str(tmp_path / "log.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Log")
    ws.write_row(0, 0, ["Site", "Unit", "Group", "Qty", "Price", "Amount"])
    for i in range(120):
        q, p = 1 + i % 6, 12.0 + (i % 3)
        ws.write_row(i + 1, 0, [["A", "B", "X1", "X", "C"][i % 5], 7 if i % 3 == 0 else 8, "G" if i % 4 == 0 else "H",
                                q, p, q * p])
    wb.close()
    return Analysis([path], playbooks=_pb(
        ["top_share:amount:place", "count_distinct:place", "mixed:place", "product_check:qty:price:amount",
         "pareto:amount:place", "negatives:amount"],
        place=("Site", "text", "entity", None, None), unit=("Unit", "number", "dimension", None, None),
        group=("Group", "text", "dimension", None, None), qty=("Qty", "number", "metric", "count", True),
        price=("Price", "number", "metric", "currency", False),
        amount=("Amount", "number", "metric", "currency", True)))


def _sales(tmp_path, n=100):
    """Location {A1, A2, B, Q7, Q70}, Channel, Tier and Amount."""
    path = str(tmp_path / "sales.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Sales")
    ws.write_row(0, 0, ["Location", "Channel", "Tier", "Amount"])
    for i in range(n):
        ws.write_row(i + 1, 0, [["A1", "A2", "B", "Q7", "Q70"][i % 5], ["Web", "Shop"][i % 2],
                                ["Gold", "Base", "Mid"][i % 3], 10.0 + i])
    wb.close()
    return Analysis([path], playbooks=_pb(["top_share:amount:location", "count_distinct:location"],
                                          location=("Location", "text", "entity", None, None),
                                          amount=("Amount", "number", "metric", "currency", True)))


def _ticked(*rs):
    """A readback answer that ticked these rules."""
    return {"confirm_rules_t": {"options": [], "labels": [], "text": "", "not_sure": False,
                                "rules": [dict(r.to_dict(), confirmed=True) for r in rs]}}


def _typed(text, qid="codes_location", kind="definition"):
    return {qid: {"options": [], "labels": [], "text": text, "not_sure": False, "kind": kind,
                  "prompt": "What do the codes in Location mean?", "header": "Codes"}}


def _stmt(a, prefix):
    return next(i["statement"] for i in a.insights if i["recipe"].startswith(prefix))


def _told(recs):
    return [r["statement"] for r in recs if r.get("record") == "fact" and r.get("source") == "told"]


# --------------------------------------------------------------------------
# one rule model, applied to every counted number
# --------------------------------------------------------------------------
def test_a_conjunction_removes_only_its_intersection(log):
    t = log.main_table
    both = [r for r in t.rows if r[0] == "A" and r[1] == 7]
    rule = Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["A"]},
                                   {"col": "Unit", "op": "in", "values": [7]}])
    log.apply_answers(_ticked(rule))
    kept = log._ctx.rows(t)
    assert len(kept) == t.n_rows - len(both) and both
    assert any(r[0] == "A" for r in kept) and any(r[1] == 7 for r in kept)   # each side alone stays
    assert "leaving out rows where Site is A and Unit is 7, per the owner" in _stmt(log, "top_share")


def test_a_map_makes_two_values_one_group(log):
    t = log.main_table
    before = log.insights
    rule = Rule("map", t.tid, [{"col": "Site", "op": "in", "values": ["X1", "X"]}], {"col": "Site", "to": {"x1": "X"}})
    log.apply_answers(_ticked(rule))
    assert "the largest of 4 sites" in _stmt(log, "top_share") and "X is 40%" in _stmt(log, "top_share")
    assert "There are 4 distinct sites" in _stmt(log, "count_distinct")
    assert "There are 5 distinct sites" in next(i["statement"] for i in before if i["recipe"].startswith("count_d"))
    ent = next(r for r in Composer(log, log.paths[0], "b1", {}).compose() if r["id"] == "ent:place")
    assert "4 sites in Site on Log after the owner's rules; the column has 5 values" in ent["statement"]


def test_a_scale_changes_the_sum_by_exactly_the_divided_part(log):
    t = log.main_table
    g = sum(r[5] for r in t.rows if r[2] == "G")
    total = sum(r[5] for r in t.rows)
    rule = Rule("scale", t.tid, [{"col": "Group", "op": "in", "values": ["G"]}], {"col": "Amount", "by": 12})
    log.apply_answers(_ticked(rule))
    assert log._ctx.col(t, 5, "Amount").sum == pytest.approx(total - g + g / 12)
    col = next(r for r in Composer(log, log.paths[0], "b1", {}).compose() if r["id"] == "col:Log.{Amount}")
    assert "over all rows" in col["statement"] and "after the owner's rules" in col["statement"]
    fact = next(r for r in Composer(log, log.paths[0], "b1", {}).compose() if r["id"].startswith("f:x:"))
    assert fact["statement"].startswith("Applied to counted numbers: Amount is divided by 12 on the rows of Log "
                                        "where Group is G (30 rows;")


def test_removing_each_rule_restores_the_numbers(log):
    t = log.main_table
    base = [i["statement"] for i in log.insights]
    for rule in (Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["A"]}]),
                 Rule("filter", t.tid, [{"col": "Group", "op": "in", "values": ["H"]}]),
                 Rule("map", t.tid, [{"col": "Site", "op": "in", "values": ["X1", "X"]}], {"col": "Site", "to": {"x1": "X"}}),
                 Rule("scale", t.tid, [], {"col": "Price", "by": 12})):
        log.apply_answers(_ticked(rule))
        assert [i["statement"] for i in log.insights] != base, rule.kind
        log.apply_answers({})
        assert [i["statement"] for i in log.insights] == base, rule.kind
        assert log._ctx.rows(t) is t.rows


def test_a_rule_scoped_to_one_metric_leaves_other_totals_alone(log):
    t = log.main_table
    rule = Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["A"]}], scope=["Price"])
    log.apply_answers(_ticked(rule))
    assert len(log._ctx.rows(t, "Price")) == 96 and len(log._ctx.rows(t, "Amount")) == 120
    assert "leaving out" not in _stmt(log, "top_share")          # a total of Amount, not of Price


def test_a_short_code_matches_only_itself(tmp_path):
    a = _sales(tmp_path)
    cands = rules.candidates(a, _typed("Q7 is my sister's stand; leave Q7 out of every total"))
    assert [(c["rule"].predicate, c["rows"]) for c in cands] == [
        ([{"col": "Location", "op": "in", "values": ["Q7"]}], 20)]
    assert rules.candidates(a, _typed("leave q7 out of every total")) == []      # a capital code, only in capitals
    a.apply_answers(_ticked(cands[0]["rule"]))
    kept = {r[0] for r in a._ctx.rows(a.main_table)}
    assert "Q70" in kept and "Q7" not in kept


def test_a_pair_rule_drops_both_rows_of_each_pair(tmp_path, synth_seed):
    m = synth.build(tmp_path / "pairs.xlsx", synth_seed, "void_pairs")
    p = m["plants"][0]
    a = Analysis([m["path"]])
    t = next(t for t in a.tables if t.sheet == p["sheet"])
    void = p["values"][1]
    rule = Rule("pair", t.tid, [{"col": p["col"], "op": "in", "values": [void]}], {"match": [p["columns"][0]]})
    a.apply_answers(_ticked(rule))
    kept = a._ctx.rows(t)
    assert len(kept) == t.n_rows - 2 * p["pairs"]
    doc = t.headers.index(p["columns"][0])
    gone = {str(n) for n in p["numbers"]}
    assert not any(str(r[doc]) in gone for r in kept)
    tw = synth.build(tmp_path / "twin.xlsx", synth_seed, "void_pairs", twin=True)
    b = Analysis([tw["path"]])
    tt = b.tables[0]
    kind = tt.rows[0][tt.headers.index(tw["plants"][0]["columns"][1])]
    b.apply_answers(_ticked(Rule("pair", tt.tid, [{"col": tw["plants"][0]["columns"][1], "op": "in",
                                                    "values": [kind]}])))
    assert len(b._ctx.rows(tt)) == tt.n_rows                  # every number once: nothing has a twin


def test_a_predicate_can_name_an_id_prefix_and_a_date_window():
    idx = {"Ref": 0, "Date": 1}
    rule = Rule("exclude", "t", [{"col": "Ref", "op": "prefix", "values": ["tr-"]},
                                 {"col": "Date", "op": "between", "values": ["2026-02-01", "2026-02-28"]}])
    assert rule.matches(["TR-104", dt.datetime(2026, 2, 3)], idx)
    assert not rule.matches(["TR-104", dt.datetime(2026, 3, 3)], idx)         # outside the window
    assert not rule.matches(["PO-104", dt.datetime(2026, 2, 3)], idx)         # another prefix
    assert not rule.matches([104, None], idx)
    assert Rule.from_dict(rule.to_dict()).key() == rule.key()


def test_every_detail_number_on_the_hotel_says_how_it_was_counted():
    a = Analysis([os.path.join(FX, "procurement_hotel.xlsx"), os.path.join(FX, "procurement_contracts.xlsx")])
    q = next(q for q in interview.candidates(a, {}) if q.id.startswith("find_exclusive_"))
    a.apply_answers({q.id: interview.parse_answers([q], "a")[q.id]})
    detail = [i for i in a.insights if any(s == "Detail" for s, _h in i.get("depends") or [])]
    assert len(detail) >= 10
    for i in detail:
        assert "leaving out CMSY" in i["statement"] or "before the owner's rules" in i["statement"], i["statement"]


def test_ids_and_codes_are_never_summed(tmp_path):
    path = str(tmp_path / "ids.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Log")
    ws.write_row(0, 0, ["Ref No", "Rate %", "Amount"])
    for i in range(40):
        ws.write_row(i + 1, 0, [5000 + i, 0.05, 10.0 + i])
    wb.close()
    a = Analysis([path], playbooks=_pb([], ref=("Ref No", "number", "identifier", None, True),
                                       rate=("Rate %", "number", "metric", "currency", True),
                                       amount=("Amount", "number", "metric", "currency", True)))
    cols = {r["label"]: r["statement"] for r in Composer(a, path, "b1", {}).compose() if r["id"].startswith("col:")}
    assert "summing" not in cols["Ref No"] and "summing" not in cols["Rate %"] and "summing" in cols["Amount"]


# --------------------------------------------------------------------------
# rules from any answer: proposed, read back with counts, marked until applied
# --------------------------------------------------------------------------
def test_a_typed_leave_out_is_read_back_with_counts(tmp_path):
    a = _sales(tmp_path)
    said = _typed("Q7 is my sister's stand; leave Q7 out of every total")
    assert rules.exclusions(a, said) == {}                     # a codes answer only proposes
    rb = findings.readback(a, said)
    assert rb is not None and rb.id.startswith(findings.READBACK)
    assert rb.options[0]["label"] == "Location = Q7 (20 rows)"
    assert "$1,210" in rb.options[0]["desc"]
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    assert rules.exclusions(a, said) == {(a.main_table.tid, 0): {"q7"}}
    assert findings.readback(a, said) is None                  # read back once
    a.apply_answers(said)
    assert "leaving out Q7, per the owner" in _stmt(a, "top_share") and "4 distinct" in _stmt(a, "count_distinct")
    recs = Composer(a, a.paths[0], "b1", said).compose()
    applied = [r["statement"] for r in recs if r["id"].startswith("f:x:")]
    assert applied == ["Applied to counted numbers: rows of Sales where Location is Q7 (20 rows, $1,210 of Amount) "
                       "are left out. When this was written, all 100 rows total $5,950 and the counted total is "
                       "$4,740."]


def test_a_rule_not_ticked_changes_nothing_and_says_so(tmp_path):
    a = _sales(tmp_path)
    said = _typed("Q7 is my sister's stand; leave Q7 out of every total")
    rb = findings.readback(a, said)
    said[rb.id] = interview.parse_answers([rb], "not sure")[rb.id]
    assert rules.exclusions(a, said) == {} and findings.readback(a, said) is None
    a.apply_answers(said)
    assert "the largest of 5 locations" in _stmt(a, "top_share")
    mark = "Counted over every row; the owner's note on Location Q7 changes this number and is not applied here."
    assert _stmt(a, "top_share").endswith(mark) and _stmt(a, "count_distinct").endswith(mark)
    recs = Composer(a, a.paths[0], "b1", said).compose()
    notes = [r["statement"] for r in recs if r["id"].startswith("f:xn:")]
    assert notes == ["Not applied to counted numbers: the owner's note on Location Q7 (20 rows on Sales, $1,210 of "
                     "Amount); those rows are counted as they are."]
    assert not any(r["id"].startswith("f:x:") for r in recs)
    assert not any(re.search(r"\bleft out\b|\bleaving out\b", s) for s in _told(recs))


def test_combine_is_marked_until_a_map_is_confirmed(tmp_path):
    a = _sales(tmp_path)
    said = _typed("combine A1 and A2 for anything about the north store", qid="goal", kind="goal")
    a.apply_answers(said)
    for recipe in ("top_share", "count_distinct"):
        assert "the owner's note on Location A1 and A2 changes this number" in _stmt(a, recipe)
    rb = findings.readback(a, said)
    assert rb.options[0]["label"] == "Location A1 with A2 as one (40 rows)"
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    a.apply_answers(said)
    assert "not applied" not in _stmt(a, "top_share") and "counting A1 and A2 as one" in _stmt(a, "top_share")
    assert "There are 4 distinct" in _stmt(a, "count_distinct")


def test_a_sentence_with_no_rule_verb_marks_nothing(tmp_path):
    a = _sales(tmp_path)
    said = _typed("A1 is our oldest store")
    base = [i["statement"] for i in a.insights]
    assert rules.candidates(a, said) == [] and findings.readback(a, said) is None
    a.apply_answers(said)
    assert [i["statement"] for i in a.insights] == base
    assert not any(r["id"].startswith(("f:x", "f:xn")) for r in Composer(a, a.paths[0], "b1", said).compose())


def test_three_columns_give_three_rules_and_their_conjunction(tmp_path):
    a = _sales(tmp_path)
    said = _typed("Leave out Q7 on the Web channel for Gold tier")
    cands = rules.candidates(a, said)
    singles = sorted(c["rule"].predicate[0]["col"] for c in cands if len(c["rule"].predicate) == 1)
    assert singles == ["Channel", "Location", "Tier"]
    both = [c for c in cands if len(c["rule"].predicate) == 3]
    assert len(both) == 1 and both[0]["rows"] == sum(
        1 for r in a.main_table.rows if r[0] == "Q7" and r[1] == "Web" and r[2] == "Gold")
    rb = findings.readback(a, said)
    assert rb.options[0]["label"].startswith("Location = Q7 with Channel = Web with Tier = Gold")
    tier = next(o for o in rb.options if o["label"].startswith("Tier = Gold"))
    said[rb.id] = interview.parse_answers([rb], chr(97 + rb.options.index(tier)))[rb.id]
    a.apply_answers(said)
    kept = a._ctx.rows(a.main_table)
    assert all(r[2] != "Gold" for r in kept)
    assert any(r[0] == "Q7" for r in kept) and any(r[1] == "Web" for r in kept)   # the others stay counted


def test_the_readback_keeps_the_option_contract(tmp_path):
    a = _sales(tmp_path)
    for text in ("Leave out Q7 on the Web channel for Gold tier", "combine A1 and A2", "leave Q7 out of Amount"):
        rb = findings.readback(a, _typed(text))
        shown = interview._options_for(rb)
        assert shown[-1]["id"] == "not_sure" and len(shown) <= 4, text
        assert re.search(r"\d", rb.prompt) and rb.kind == "rule" and rb.multi
        assert not [o["label"] for o in rb.options if re.search(r"\band\b|,", o["label"])], text
        ab = rb.meta["about"]
        assert ab["col"] in a.table(ab["table"]).headers
        assert len(interview.render_ask([rb])["questions"][0]["options"]) <= 4


def test_an_exclusion_answer_naming_one_column_applies_without_a_readback(tmp_path):
    a = _sales(tmp_path)
    one = _typed("Q7 never counts", qid="exclude_rows", kind="exclusion")
    assert rules.exclusions(a, one) == {(a.main_table.tid, 0): {"q7"}}
    assert findings.readback(a, one) is None
    two = _typed("Q7 and Web never count", qid="exclude_rows", kind="exclusion")
    assert rules.exclusions(a, two) == {}                      # two columns: read back first
    assert findings.readback(a, two) is not None


def test_a_scope_pick_confirms_the_rules_for_that_metric_only(tmp_path):
    a = _sales(tmp_path)
    said = _typed("leave Q7 out of Amount")
    rb = findings.readback(a, said)
    assert [o["label"] for o in rb.options][-1] == "Only for Amount"
    said[rb.id] = interview.parse_answers([rb], "a, b")[rb.id]
    got = rules.confirmed(a, said)
    assert len(got) == 1 and got[0].scope == ["Amount"]
    assert rules.exclusions(a, said) == {}                     # not every total, so not a plain exclusion


def test_a_scope_ticked_alone_confirms_no_rule(tmp_path):
    a = _sales(tmp_path)
    said = _typed("Leave out Q7 on the Web channel from Amount")
    rb = findings.readback(a, said)
    labels = [o["label"] for o in rb.options]
    assert labels[-1] == "Only for Amount" and len(rb.meta["rules"]) == 2
    said[rb.id] = interview.parse_answers([rb], chr(97 + len(labels) - 1))[rb.id]
    assert rules.confirmed(a, said) == []
    again = findings.readback(a, said)                         # the rules shown count as read back
    assert again is None or not {str(Rule.from_dict(d).key()) for d in again.meta["rules"].values()} & {
        str(Rule.from_dict(d).key()) for d in rb.meta["rules"].values()}
    assert said[rb.id]["labels"] == [] and said[rb.id]["not_sure"]
    a.apply_answers(said)
    recs = Composer(a, a.paths[0], "b1", said).compose()
    assert not any("Only for Amount" in s for s in _told(recs))


def test_a_scope_is_offered_only_where_every_rule_has_that_column(tmp_path):
    path = str(tmp_path / "two.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Sales")
    ws.write_row(0, 0, ["Location", "Amount"])
    for i in range(60):
        ws.write_row(i + 1, 0, [["A1", "A2", "Q7"][i % 3], 10.0 + i])
    ws = wb.add_worksheet("Log")
    ws.write_row(0, 0, ["Site", "Cost"])
    for i in range(60):
        ws.write_row(i + 1, 0, [["North", "South", "West"][i % 3], 5.0 + i])
    wb.close()
    a = Analysis([path])
    said = _typed("Leave Q7 and West out of Amount")
    cands = rules.candidates(a, said)
    assert {c["rule"].table for c in cands} == {t.tid for t in a.tables}
    # changed on purpose (round 3, fix 6): the rule is never broader than the sentence. Q7 is left out of
    # Amount, the column Sales has; Log has no Amount, so West is kept for 'Amount' alone and changes no
    # count or total there, and is never offered for every count and total
    sales = next(t.tid for t in a.tables if t.sheet == "Sales")
    assert {(c["rule"].table == sales, tuple(c["rule"].scope)) for c in cands} == {(True, ("Amount",)),
                                                                                   (False, ("Amount",))}
    rb = findings.readback(a, said)
    assert {Rule.from_dict(d).table for d in rb.meta["rules"].values()} == {sales}
    assert all(c["rule"].table == sales or c.get("scope_words") for c in cands)


def test_readbacks_stay_outside_the_question_cap(tmp_path):
    a = _sales(tmp_path)
    said = _typed("leave Q7 out of every total")
    rb = findings.readback(a, said)
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    assert interview.substantive(said) == 1
    q = next(q for q in findings.follow_ups(a, _typed("leave Q7 out")) if q.id.startswith(findings.READBACK))
    # a follow-up at 1.5x the worth of the rows it would change, never ahead of the queue by rule
    assert q.gated and q.value == pytest.approx(interview.FOLLOW_UP * interview.worth(q.meta["stake"], "treatment"))


def test_a_readback_fires_on_a_typed_rule_about_a_real_value(tmp_path, synth_seed):
    m = synth.build(tmp_path / "plant.xlsx", synth_seed, "odd_group")
    p = m["plants"][0]
    a = Analysis([m["path"]])
    said = _typed(f"The {p['value']} site closed; leave {p['value']} out of every total.")
    rb = findings.readback(a, said)
    assert rb is not None and any(o["label"].startswith(f"{p['col']} = {p['value']} (") for o in rb.options)
    # the same sentence argued against, on the plant: nothing proposed, nothing marked
    against = _typed(f"The {p['value']} site closed; don't leave {p['value']} out of any total.")
    assert findings.readback(a, against) is None and rules.unapplied(a, against) == []


def test_the_verb_gate_holds_on_the_null_twins(tmp_path, synth_seed):
    """Verb-gate checks: a sentence that names a real value with no rule verb
    proposes nothing, on the twins of the rule plants."""
    tw = synth.build(tmp_path / "twin.xlsx", synth_seed, "odd_group", twin=True)
    b = Analysis([tw["path"]])
    quiet = _typed(f"{tw['plants'][0]['value']} is our newest site.")
    assert findings.readback(b, quiet) is None and rules.unapplied(b, quiet) == []
    flat = synth.build(tmp_path / "flat.xlsx", synth_seed, "per_case", twin=True)
    c = Analysis([flat["path"]])
    price = flat["plants"][0]["columns"][1]
    assert findings.readback(c, _typed(f"{price} is what we pay for one.")) is None


def test_a_typed_unit_rule_divides_the_named_price(tmp_path, synth_seed):
    m = synth.build(tmp_path / "case.xlsx", synth_seed, "per_case")
    p = m["plants"][0]
    a = Analysis([m["path"]])
    price = p["columns"][1]
    said = _typed(f"{price} for {p['value']} is per case of {p['pack']}.")
    rb = findings.readback(a, said)
    assert rb is not None and rb.options[0]["label"].startswith(f"{price} divided by {p['pack']} where ")
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    a.apply_answers(said)
    t = a.main_table
    j = t.headers.index(price)
    assert a._ctx.col(t, j, price).max < a.cols[t.tid][j].max


def test_a_unit_rule_that_names_no_column_divides_the_price(tmp_path, synth_seed):
    m = synth.build(tmp_path / "case.xlsx", synth_seed, "per_case")
    p = m["plants"][0]
    a = Analysis([m["path"]])
    said = _typed(f"{p['value']} is priced per case of {p['pack']}.")
    rb = findings.readback(a, said)
    assert rb is not None and rb.options[0]["label"].startswith(f"{p['columns'][1]} divided by {p['pack']} where ")


def test_noise_books_get_no_readback_and_no_marks(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        base = [i["statement"] for i in a.insights]
        for said in ({}, _typed("This is the file we use every month for the board.")):
            assert findings.readback(a, said) is None, paths
            a.apply_answers(said)
            assert [i["statement"] for i in a.insights] == base, paths


# --------------------------------------------------------------------------
# only what the owner said: negation, one rule per clause, every value shown
# --------------------------------------------------------------------------
def _rules_of(cands):
    return sorted((c["rule"].kind, tuple(c["rule"].predicate[0]["values"])) for c in cands)


def test_an_exclusion_answer_that_argues_against_leaving_out_applies_nothing(tmp_path):
    a = _sales(tmp_path)
    for text in ("Include Q7 even without receipts.", "Q7 is internal but it counts.",
                 "Keep Q7 in every total, never remove it.", "Q7 should never be excluded.", "Never ignore Q7.",
                 "Q7 aren't a problem", "Don't leave Q7 out.", "Q7 should not be left out."):
        said = _typed(text, qid="exclude_rows", kind="exclusion")
        assert rules.exclusions(a, said) == {}, text
    assert rules.exclusions(a, _typed("Q7 never counts", qid="exclude_rows", kind="exclusion")) \
        == {(a.main_table.tid, 0): {"q7"}}
    assert rules.exclusions(a, _typed("Don't count Q7.", qid="exclude_rows", kind="exclusion")) \
        == {(a.main_table.tid, 0): {"q7"}}


def test_a_negated_sentence_proposes_and_marks_nothing(tmp_path):
    a = _sales(tmp_path)
    base = [i["statement"] for i in a.insights]
    for text in ("Don't leave Q7 out.", "Keep Q7, do not drop it.", "Never ignore Q7."):
        said = _typed(text)
        assert rules.candidates(a, said) == [] and findings.readback(a, said) is None, text
        a.apply_answers(said)
        assert [i["statement"] for i in a.insights] == base, text
        assert not any(r["id"].startswith("f:xn:") for r in Composer(a, a.paths[0], "b1", said).compose())


def test_each_rule_verb_proposes_from_its_own_clause(tmp_path):
    a = _sales(tmp_path)
    want = [("exclude", ("Q7",)), ("map", ("A1", "A2"))]
    for text in ("Leave Q7 out and combine A1 and A2.", "Leave Q7 out, but A1 and A2 are the same store.",
                 "leave Q7 out of every total. combine A1 and A2.", "Q7 never counts and A1 is the same as A2."):
        assert _rules_of(rules.candidates(a, _typed(text))) == want, text
    assert _rules_of(rules.candidates(a, _typed("Keep Q7 but leave Q70 out."))) == [("exclude", ("Q70",))]
    assert _rules_of(rules.candidates(a, _typed("Leave Q7 out and don't combine A1 and A2."))) == \
        [("exclude", ("Q7",))]
    # an exclusion answer applies only the clause that leaves rows out
    said = _typed("Q7 never counts; combine A1 and A2", qid="exclude_rows", kind="exclusion")
    assert rules.exclusions(a, said) == {(a.main_table.tid, 0): {"q7"}}


def test_a_value_that_is_an_ordinary_word_applies_only_as_written(tmp_path):
    path = str(tmp_path / "other.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Sales")
    ws.write_row(0, 0, ["Location", "Amount"])
    for i in range(60):
        ws.write_row(i + 1, 0, [["North", "South", "Other"][i % 3], 10.0 + i])
    wb.close()
    a = Analysis([path])
    assert rules.exclusions(a, _typed("Leave out the other store.", qid="exclude_rows", kind="exclusion")) == {}
    assert rules.exclusions(a, _typed("Leave out the Other rows.", qid="exclude_rows", kind="exclusion")) \
        == {(a.main_table.tid, 0): {"other"}}
    assert rules.candidates(a, _typed("Leave out the other store."))           # the readback still asks


def test_every_value_of_a_shown_rule_is_in_its_label_or_description(tmp_path):
    a = _sales(tmp_path)
    for text in ("Leave out A1, A2 and Q7.", "Leave out Q7 on the Web channel for Gold tier", "combine A1, A2 and B"):
        rb = findings.readback(a, _typed(text))
        shown = interview.render_ask([rb])["questions"][0]["options"]      # what the owner sees
        for k, o in enumerate(rb.options):
            d = rb.meta["rules"].get(o["id"])
            if d is None:
                continue
            assert len(o["label"]) <= findings.LABEL_MAX and shown[k]["label"] == o["label"], o["label"]
            for p in d["predicate"]:
                for v in p["values"]:
                    assert v in o["label"] or v in shown[k]["description"], (text, v)
            assert o["desc"].startswith(f"Rows where {rules.where(Rule.from_dict(d))}: "), o["desc"]


def test_a_long_label_is_never_cut_inside_a_condition():
    rule = Rule("exclude", "t", [{"col": "Supplier", "op": "in", "values": ["Northwind Traders Wholesale"]},
                                 {"col": "Channel", "op": "in", "values": ["Web"]},
                                 {"col": "Tier", "op": "in", "values": ["Gold"]}])
    label = findings._rule_label({"rule": rule, "rows": 3})
    assert len(label) <= findings.LABEL_MAX
    assert label == "Supplier: 1 value +2 more conditions (3 rows)"
    short = Rule("exclude", "t", [{"col": "Site", "op": "in", "values": ["North"]},
                                  {"col": "Channel", "op": "in", "values": ["Web"]}])
    assert findings._rule_label({"rule": short, "rows": 3}) == "Site = North with Channel = Web (3 rows)"


def test_column_and_entity_notes_carry_the_not_applied_mark(tmp_path):
    a = _sales(tmp_path)
    said = _typed("leave Q7 out of every total")
    a.apply_answers(said)
    recs = {r["id"]: r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose()}
    mark = "the owner's note on Location Q7 changes this number and is not applied here."
    assert recs["col:Sales.{Amount}"].endswith(mark) and recs["ent:location"].endswith(mark)
    base = {r["id"]: r["statement"] for r in Composer(_sales(tmp_path), a.paths[0], "b1", {}).compose()}
    assert "not applied" not in base["col:Sales.{Amount}"] and "not applied" not in base["ent:location"]


def test_the_mark_under_an_applied_rule_does_not_say_every_row(tmp_path):
    a = _sales(tmp_path)
    t = a.main_table
    said = dict(_typed("combine A1 and A2"), **_ticked(Rule("exclude", t.tid, [{"col": "Location", "op": "in",
                                                                               "values": ["Q7"]}])))
    a.apply_answers(said)
    stmt = _stmt(a, "top_share")
    assert "(leaving out Q7, per the owner)" in stmt and "Counted over every row" not in stmt
    assert stmt.endswith("The owner's note on Location A1 and A2 would change this number and is not applied here.")


def test_a_unit_rule_never_makes_the_product_check_fail(log):
    t = log.main_table
    before = next(i for i in log.insights if i["recipe"].startswith("product_check"))
    rule = Rule("scale", t.tid, [{"col": "Group", "op": "in", "values": ["G"]}], {"col": "Price", "by": 12})
    log.apply_answers(_ticked(rule))
    after = next(i for i in log.insights if i["recipe"].startswith("product_check"))
    # changed on purpose (round 3, fix 22): Amount is Qty x Price on every row, so the unit rule on Price
    # divides Amount on the same rows too; the check still reads both as recorded
    assert after["statement"] == before["statement"][:-1] + (" (Price and Amount as recorded, before the owner's "
                                                             "unit rule).")
    assert after.get("oddity") == before.get("oddity") and after["weight"] == before["weight"]
    assert log._ctx.col(t, 4, "Price").sum < log.cols[t.tid][4].sum           # other numbers do divide


def test_a_readback_reply_that_types_a_rule_gets_it_read_back(tmp_path):
    """Changed on purpose (v02a fix brief A7): words typed on a readback are read
    too. What the readback showed is not proposed again; a new rule is read back
    once more, within the two extra prompts."""
    a = _sales(tmp_path)
    said = {"find_codes_location": dict(_typed("leave Q7 out of every total")["codes_location"],
                                        rules=[{"kind": "exclude", "table": a.main_table.tid, "predicate": []}])}
    assert _rules_of(rules.candidates(a, said)) == [("exclude", ("Q7",))]
    rb = findings.readback(a, said)
    said[rb.id] = dict(interview.parse_answers([rb], "not sure")[rb.id], text="Neither. Leave A1 out")
    assert _rules_of(rules.candidates(a, said)) == [("exclude", ("A1",)), ("exclude", ("Q7",))]
    again = findings.readback(a, said)
    assert again is not None and [o["label"] for o in again.options if o["id"] in again.meta["rules"]] == \
        ["Location = A1 (20 rows)"]
    said[again.id] = dict(interview.parse_answers([again], "not sure")[again.id], text="leave B out")
    assert findings.readback(a, said) is None                 # the two extra prompts are spent


def test_readbacks_and_just_in_time_questions_share_one_budget(tmp_path):
    import sb
    a = _sales(tmp_path)
    said = _typed("leave Q7 out of every total")
    assert sb._jit_room(said) == rules.MAX_READBACKS
    rb = findings.readback(a, said)
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    assert sb._jit_room(said) == rules.MAX_READBACKS - 1
    said["confirm_rules_x"] = {"options": [], "labels": [], "text": "", "not_sure": True, "rules": []}
    assert sb._jit_room(said) == 0
    assert findings.readback(a, dict(said, **_typed("combine A1 and A2", qid="goal", kind="goal"))) is None


# --------------------------------------------------------------------------
# through the CLI: the readback rides with the build question when no round is left
# --------------------------------------------------------------------------
def _sb(env, *args, stdin=None):
    p = subprocess.run([sys.executable, SB, *args], input=stdin, capture_output=True, text=True, env=env, timeout=120)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def test_the_readback_rides_with_the_build_question(tmp_path):
    book = str(tmp_path / "stands.xlsx")
    wb = xlsxwriter.Workbook(book)
    ws = wb.add_worksheet("Sales")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_row(0, 0, ["Date", "Location", "Channel", "Amount"])
    for i in range(80):
        ws.write_datetime(i + 1, 0, dt.datetime(2026, 1, 1) + dt.timedelta(days=i), fmt)
        ws.write_row(i + 1, 1, [["A1", "A2", "B", "Q7"][i % 4], ["Web", "Shop"][i % 2], 10.0 + i])
    wb.close()
    home = str(tmp_path / "home")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    r = _sb(env, "start", book)
    assert r["next"] == "ask"
    from sheetbrain.store import Store
    st = Store(home)
    state = st.state(r["brain_id"])
    state["round"] = interview.MAX_ROUNDS - 1            # the last round: nothing is asked after it
    st.save_state(r["brain_id"], state)
    st.close()
    n = len(r["ask"]["questions"])
    r = _sb(env, "answer", book, "--text", "1 leave Q7 out of every total "
            + " ".join(f"{k} not sure" for k in range(2, n + 1)))
    # after the last round, the closing question alone, outside the cap
    assert r["next"] == "ask" and [q["header"] for q in r["ask"]["questions"]] == ["Last one"]
    r = _sb(env, "answer", book, "--text", "b")
    asks = r["ask"]["questions"]
    assert r["next"] == "ask" and len(asks) == 2
    assert asks[0]["question"].startswith("From what you typed, 1 rule would change 20 rows")
    # Q7 is a quarter of this book's money: the label says what ticking it removes (v02a fix brief A9)
    assert asks[0]["options"][0]["label"] == "Location = Q7 (20 rows), removes 26% of Amount"
    assert "What should I build" in asks[1]["question"]
    r = _sb(env, "answer", book, "--text", "1a 2b")
    assert r["next"] == "preview"
    st = Store(home)
    answers = st.state(r["brain_id"])["answers"]
    st.close()
    assert interview.substantive(answers) == n - 1          # the goal and the closing question are not counted
    r = _sb(env, "preview", book, "--show-rows")
    assert "Applied to counted numbers: rows of Sales where Location is Q7 (20 rows" in r["say"]
