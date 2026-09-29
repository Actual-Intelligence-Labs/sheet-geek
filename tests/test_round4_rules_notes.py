"""v0.2 practice check, round 4: typed-rule extraction, rule readbacks and
scopes, applying rules, notes, the privacy screen and the CLI protocol. Every
book here is written inline with synthetic names and values; no development
workbook is read."""
import datetime as dt
import os
import random
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import sb  # noqa: E402
from sheetbrain import brain, findings, interview, privacy, rules  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer, _answer_notes  # noqa: E402
from sheetbrain.rules import Rule  # noqa: E402


# --------------------------------------------------------------------------
# inline synthetic books
# --------------------------------------------------------------------------
def _lines(tmp_path, name="lines.xlsx", derive=True):
    """Order lines with no amount column (Qty, Unit Price, Discount in dollars), so
    the measure is worked out as Net; three GV- vouchers carry no Discount; a
    Lineup lookup keyed by Part. derive=False adds a Line Total column, so the
    table has an amount of its own."""
    rng = random.Random(5)
    path = str(tmp_path / name)
    wb = xlsxwriter.Workbook(path)
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Lines")
    head = ["Ref", "Day", "Part", "Outlet", "Qty", "Unit Price", "Discount"] + ([] if derive else ["Line Total"])
    ws.write_row(0, 0, head)
    skus = [("GV-010", 10.0), ("GV-025", 25.0), ("GV-050", 50.0), ("TP-110", 39.5), ("BK-220", 18.25),
            ("LN-330", 64.0), ("CR-440", 12.75), ("SH-550", 88.0)]
    for i in range(200):
        sku, price = skus[i % len(skus)]
        q = 1 + i % 4
        disc = 0.0 if sku.startswith("GV-") else round(rng.choice([0, 0, 1.5, 3.25, 6.0]), 2)
        ws.write(i + 1, 0, f"Q{7000 + i}")
        ws.write_datetime(i + 1, 1, dt.datetime(2026, 1, 1) + dt.timedelta(days=i % 150), fmt)
        row = [sku, ["Web", "Shop", "Phone", "Kiosk"][i % 4], q, price, disc]
        if not derive:
            row.append(round(q * price - disc, 2))
        ws.write_row(i + 1, 2, row)
    ws = wb.add_worksheet("Lineup")
    ws.write_row(0, 0, ["Part", "Group", "List Price"])
    for k, (sku, price) in enumerate(skus):
        ws.write_row(k + 1, 0, [sku, "Vouchers" if sku.startswith("GV-") else ["Tools", "Books"][k % 2], price])
    wb.close()
    return Analysis([path])


def _stock(tmp_path, name="stock.xlsx"):
    """Stock takes (Week, Item No, Group, Site, On Hand, Cost Each, Stock Value = On
    Hand x Cost Each), a Loss Log keyed by Item No (Date, Item No, Site, Qty, Cost
    Each, Loss Value = Qty x Cost Each; no Group) and a Catalog lookup. Items
    starting with 7 are the Cellar group, priced per case."""
    rng = random.Random(3)
    items = [("1001", "Food", 4.0), ("1002", "Food", 6.5), ("1003", "Paper", 9.0), ("1004", "Paper", 11.0),
             ("7001", "Cellar", 180.0), ("7002", "Cellar", 150.0), ("1005", "Food", 5.5), ("1006", "Paper", 7.25)]
    path = str(tmp_path / name)
    wb = xlsxwriter.Workbook(path)
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Stock")
    ws.write_row(0, 0, ["Week", "Item No", "Group", "Site", "On Hand", "Cost Each", "Stock Value"])
    r = 1
    for w in range(10):
        for code, cat, cost in items:
            n = rng.randint(1, 9)
            ws.write_datetime(r, 0, dt.datetime(2026, 1, 4) + dt.timedelta(days=7 * w), fmt)
            ws.write_row(r, 1, [code, cat, ["NT", "ST", "WR"][(w + int(code)) % 3], n, cost, round(n * cost, 2)])
            r += 1
    ws = wb.add_worksheet("Loss Log")
    ws.write_row(0, 0, ["Date", "Item No", "Site", "Qty", "Cost Each", "Loss Value"])
    for i in range(80):
        code, _cat, cost = items[i % len(items)]
        q = rng.randint(1, 4)
        ws.write_datetime(i + 1, 0, dt.datetime(2026, 1, 2) + dt.timedelta(days=i), fmt)
        ws.write_row(i + 1, 1, [code, ["NT", "ST", "WR"][i % 3], q, cost, round(q * cost, 2)])
    ws = wb.add_worksheet("Catalog")
    ws.write_row(0, 0, ["Item No", "Group", "Supplier"])
    for k, (code, cat, _c) in enumerate(items):
        ws.write_row(k + 1, 0, [code, cat, ["Harbor Supply", "Gull Foods"][k % 2]])
    wb.close()
    return Analysis([path])


def _pay(tmp_path, name="pay.xlsx"):
    """A pay register (Worker No, Site, Paid On, Hours, Extra Time, Gross) whose rows
    before Mar 1 carry overtime inside Hours, and a Staff roster keyed by Worker No with
    a Home Site. Site XR is a site run for someone else; E900 is a test record."""
    path = str(tmp_path / name)
    wb = xlsxwriter.Workbook(path)
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Register")
    ws.write_row(0, 0, ["Worker No", "Site", "Paid On", "Hours", "Extra Time", "Gross"])
    emps = [(f"E{100 + k}", ["NA", "SB", "XR"][k % 3]) for k in range(24)] + [("E900", "NA")]
    r = 1
    for c in range(8):
        day = dt.datetime(2026, 1, 9) + dt.timedelta(days=14 * c)
        for emp, site in emps:
            if emp == "E900" and c < 3:
                continue
            ot = (c + int(emp[1:])) % 5
            ws.write(r, 0, emp)
            ws.write(r, 1, site)
            ws.write_datetime(r, 2, day, fmt)
            ws.write_row(r, 3, [70 + ot if day < dt.datetime(2026, 3, 1) else 70, ot, 1500.0 + 10 * (r % 13)])
            r += 1
    ws = wb.add_worksheet("Staff")
    ws.write_row(0, 0, ["Worker No", "Home Site", "Role"])
    for k, (emp, site) in enumerate(emps):
        ws.write_row(k + 1, 0, [emp, site, ["Crew", "Lead"][k % 2]])
    wb.close()
    return Analysis([path])


def _typed(text, a, tab=None, col=None, qid="house_rules", kind="definition", prompt="What about it?"):
    t = a.table(tab) if tab else a.main_table
    return {qid: {"options": [], "labels": [], "text": text, "not_sure": False, "kind": kind,
                  "about": {"table": t.tid, "col": col or t.headers[0], "aspect": "meaning"}, "prompt": prompt,
                  "header": "House rules"}}


def _tid(a, sheet):
    return next(t.tid for t in a.tables if t.sheet == sheet)


def _conds(c):
    return [(p["col"], p.get("op", "in"), tuple(p["values"])) for p in c["rule"].predicate]


def _tick(cands, qid="confirm_rules_t"):
    return {qid: {"options": [f"r{k}" for k in range(1, len(cands) + 1)], "labels": [], "text": "", "not_sure": False,
                  "rules": [dict(c["rule"].to_dict(), option=f"r{k}", confirmed=True) for k, c in enumerate(cands, 1)]}}


def _q(opts, statements=None, prompt="30 rows on Lines have a code that is not in the list. What are they?",
       header="Not listed", multi=True):
    return interview.Q("find_x", header, prompt, opts, multi=multi,
                       fact={"kind": "definition", "statements": statements or {}})


def _notes(recs, qid):
    return [r["statement"] for r in recs if r.get("source") == "told" and r.get("ref") == f"q:{qid}"
            and r.get("record") == "fact"]


def _gv_rows(a):
    t = a.main_table
    return sum(1 for row in t.rows if str(row[2]).startswith("GV-"))


def _nonstock(a, picks, typed=""):
    """A pick-all question on the voucher lines, like the non-stock finding asks it:
    'Leave out of Net totals' and 'Leave out of Discount totals', each a rule of its own."""
    t = a.main_table
    pred = [{"col": "Part", "op": "in", "values": ["GV-010", "GV-025", "GV-050"]}]
    n = _gv_rows(a)
    q = interview.Q("find_nonstock_x", "Vouchers", f"Vouchers in Group on Lineup has no cost; its {n} lines on Lines "
                    "carry no Discount. How do the Vouchers lines count?",
                    [{"id": "leave", "label": "Leave out of Net totals", "desc": f"Their {n} lines stay out of Net totals"},
                     {"id": "adj", "label": "Leave out of Discount totals",
                      "desc": f"Their {n} lines stay out of Discount totals"}],
                    multi=True, kind="exclusion", source="finding", fact={"kind": "exclusion"},
                    meta={"about": {"table": t.tid, "col": "Part", "aspect": "treatment"},
                          "rules": {"leave": Rule("exclude", t.tid, pred, scope=["Net"]).to_dict(),
                                    "adj": Rule("exclude", t.tid, pred, scope=["Discount"]).to_dict()}})
    return q, {q.id: interview._answer(q, picks, typed)}


# --------------------------------------------------------------------------
# fix 1: a pick keeps what the owner saw under it
# --------------------------------------------------------------------------
def test_picks_naming_their_own_values_are_said_pick_by_pick():
    q = _q([{"id": "fees", "label": "Not real items", "desc": "Charges, not things you buy: SVC-A and SVC-B"},
            {"id": "old", "label": "Replaced codes", "desc": "The same thing under a new code: IT301 -> IT410"}],
           statements={"old": "30 rows on Lines have a code that is not in the list: replaced codes, per the owner."})
    notes = [s for s, _ in _answer_notes(interview._answer(q, ["fees", "old"], ""), q.fact, None)]
    assert notes[0] == 'On "Not listed", the owner picked "Not real items" and "Replaced codes".'
    assert '"Not real items" ("Charges, not things you buy: SVC-A and SVC-B")' in notes[1]
    assert '"Replaced codes" ("The same thing under a new code: IT301 -> IT410")' in notes[2]
    assert not any("30 rows" in n for n in notes)                  # no note speaks for the whole count
    # twin: picks whose descriptions name no values of their own keep the question in the first note
    q2 = _q([{"id": "a", "label": "Internal entries", "desc": "Transfers between our own sites"},
             {"id": "b", "label": "Already counted elsewhere", "desc": "Counting them again doubles them"}])
    notes = [s for s, _ in _answer_notes(interview._answer(q2, ["a", "b"], ""), q2.fact, None)]
    assert notes[0].startswith('Asked "30 rows on Lines have a code') and len(notes) == 3


def test_a_pick_quotes_the_description_it_was_shown_less_only_the_typing_instruction():
    q = _q([{"id": "pack", "label": "For a pack or case", "desc": "Type how many are in one; Cost Each on its 40 rows "
             "is divided by that"}, {"id": "one", "label": "For one"}], multi=False)
    note = _answer_notes(interview._answer(q, ["pack"], ""), q.fact, None)[0][0]
    assert note.endswith('picked "For a pack or case" ("Cost Each on its 40 rows is divided by that").')
    # a description that adds a claim is still what the owner saw: quoted, and flagged by the lint
    q = _q([{"id": "out", "label": "Outside money", "desc": "Loans, grants, gifts"}], prompt="Where is it from?")
    assert '"Outside money" ("Loans, grants, gifts")' in _answer_notes(interview._answer(q, ["out"], ""), q.fact, None)[0][0]
    assert brain.desc_adds_claim("Loans, grants, gifts", "Where is it from?", "Outside money")
    assert not brain.desc_adds_claim("Leave these 40 rows out of every count and total", "What is it?", "Not ours")
    assert brain._without_instruction("Type what it is, if you like") == ""
    assert brain._without_instruction("Leave it out of every total (type why)") == "Leave it out of every total"
    assert brain._without_instruction("Say why: it is kept as written") == "It is kept as written"


def test_a_curated_sentence_carries_a_description_that_names_more():
    q = _q([{"id": "internal", "label": "Internal entries", "desc": "Transfers or bookkeeping entries"},
            {"id": "plain", "label": "Real, keep it", "desc": "Its 12 rows stay in every count and total"}],
           statements={"internal": "The XR rows in Site are internal entries, per the owner.",
                       "plain": "The XR rows in Site are real, per the owner."}, prompt="12 rows have Site XR. What is XR?",
           multi=False)
    note = _answer_notes(interview._answer(q, ["internal"], ""), q.fact, None)[0][0]
    assert note == 'The XR rows in Site are internal entries, per the owner ("Transfers or bookkeeping entries").'
    # twin: a description that only says how rows are treated adds nothing to the sentence
    note = _answer_notes(interview._answer(q, ["plain"], ""), q.fact, None)[0][0]
    assert note == "The XR rows in Site are real, per the owner."


def test_the_readbacks_rules_describe_only_what_their_labels_and_counts_say(tmp_path):
    a = _stock(tmp_path)
    said = _typed("Leave the 7001 items out of item price comparisons.", a, "Stock", "Item No")
    rb = rules.scoped_readback(a, said)
    assert rb is not None and rb.options
    for o in rb.options:
        assert not brain.desc_adds_claim(o["desc"], rb.prompt, o["label"]), o


# --------------------------------------------------------------------------
# fix 2: each pick's treatment is said from its own rule
# --------------------------------------------------------------------------
def test_two_picks_on_the_same_rows_say_their_own_totals_and_one_rule_says_both(tmp_path):
    a = _lines(tmp_path)
    assert rules.measure_names(a, a.main_table.tid) == {"Net"}
    q, said = _nonstock(a, ["leave", "adj"])
    live = rules.confirmed(a, said)
    assert [(r.kind, r.scope) for r in live] == [("exclude", ["Net", "Discount"])]       # one rule, out of both
    a.apply_answers(said)
    phr = brain.applied_phrases(a, q.id, said[q.id])
    n = _gv_rows(a)
    assert phr == {"leave": f"so these {n} rows are left out of Net totals",
                   "adj": f"so these {n} rows are left out of Discount totals"}
    recs = Composer(a, a.paths[0], "b1", said).compose()
    applied = [r["statement"] for r in recs if r.get("ref") == "rule:applied"]
    assert len(applied) == 1 and "are left out of Net and Discount totals; the other counts and totals keep them." \
        in applied[0]
    assert not any("every count and total in this brain keeps them" in r["statement"] for r in recs)
    # twin: one pick, one scope, one tail
    q, said = _nonstock(a, ["adj"])
    a.apply_answers(said)
    assert brain.applied_phrases(a, q.id, said[q.id]) == {"adj": f"so these {n} rows are left out of Discount totals"}


def test_the_tail_comes_right_after_the_pick_before_the_evidence(tmp_path):
    a = _lines(tmp_path, derive=False)
    t = a.main_table
    q = interview.Q("find_copies_x", "Copies", "12 rows appear twice, each Q7 row with a twin. Which rows are the copies?",
                    [{"id": "keep_b", "label": "Re-imported: keep the Q7 rows", "desc": ""}], kind="history",
                    source="finding", fact={"kind": "history", "statements": {
                        "keep_b": "The 12 rows that appear twice were re-imported, per the owner: keep the Q7 rows. Every "
                                  "Q7 row has a twin."}},
                    meta={"about": {"table": t.tid, "col": "Ref", "aspect": "history"},
                          "exclude": {"table": t.tid, "col": "Outlet", "values": ["Kiosk"], "options": ["keep_b"]}})
    said = {q.id: interview._answer(q, ["keep_b"], "")}
    a.apply_answers(said)
    note = next(r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose() if r["id"] == f"f:{q.id}")
    assert note == ("The 12 rows that appear twice were re-imported, per the owner: keep the Q7 rows, so these 50 rows "
                    "are left out of every count and total. Every Q7 row has a twin.")


def test_a_unit_pick_says_what_it_divided(tmp_path):
    a = _stock(tmp_path)
    t = a.table(_tid(a, "Stock"))
    q = interview.Q("find_odd_x", "Unit price", "Cellar in Group on Stock (20 rows) is unlike the others. Is Cost Each "
                    "there for one, or for a pack or case?",
                    [{"id": "pack", "label": "For a pack or case", "desc": "Type how many are in one; Cost Each on its "
                      "20 rows is divided by that"}, {"id": "one", "label": "For one"}], kind="unit",
                    source="finding", fact={"kind": "unit"},
                    meta={"about": {"table": t.tid, "col": "Cost Each", "aspect": "unit"},
                          "scale": {"table": t.tid, "col": "Cost Each", "option": "pack",
                                    "predicate": [{"col": "Group", "op": "in", "values": ["Cellar"]}]}})
    said = {q.id: interview._answer(q, ["pack"], "12")}
    a.apply_answers(said)
    phr = brain.applied_phrases(a, q.id, said[q.id])
    assert phr == {"pack": "so Cost Each is divided by 12 on these 20 rows, and so is Stock Value, worked out from it"}
    # twin: no number typed, no rule and nothing said
    said = {q.id: interview._answer(q, ["pack"], "")}
    a.apply_answers(said)
    assert brain.applied_phrases(a, q.id, said[q.id]) == {}


def test_a_typed_rule_a_pick_already_carries_out_is_not_listed_as_not_applied(tmp_path):
    a = _lines(tmp_path)
    q, said = _nonstock(a, ["leave", "adj"])
    said.update(_typed("Voucher lines (Parts starting with GV-) are not sales; leave them out of sales and discount "
                       "numbers.", a, col="Part", qid="leave_out", kind="exclusion"))
    got = [c for c in rules.candidates(a, said) if c["rule"].table == a.main_table.tid]
    assert [(_conds(c), c["rule"].scope) for c in got] == [([("Part", "prefix", ("GV-",))], ["Net", "Discount"])]
    a.apply_answers(said)
    assert rules.unapplied(a, said) == [] and rules.proposals(a, said) == []
    # twin: typed for every count and total while the picks left the rows out of two totals only: still open
    said["leave_out"]["text"] = "Leave GV- lines out of every total."
    a.apply_answers(said)
    assert [c["rule"].scope for c in rules.unapplied(a, said)] == [[]]


# --------------------------------------------------------------------------
# fix 4: a pick with typed words is one note
# --------------------------------------------------------------------------
def test_a_pick_with_typed_words_writes_the_curated_sentence_then_the_words():
    q = _q([{"id": "test", "label": "A test record (type why)", "desc": "Leave it out of every tab"},
            {"id": "real", "label": "Real, keep it"}],
           statements={"test": "E900 on Register is a test record, never real, per the owner."},
           prompt="E900 on Register has 5 rows, all from the switch on. Real, or never real?", header="Test ID",
           multi=False)
    notes = _answer_notes(interview._answer(q, ["test"], "The new vendor set it up to try the export."), q.fact, None)
    # the description only says how the rows are treated, so the curated sentence stands for it
    assert [s for s, _ in notes] == ['E900 on Register is a test record, never real, per the owner; the owner wrote: '
                                     '"The new vendor set it up to try the export."']


# --------------------------------------------------------------------------
# fix 5: typed sentences bind the right rows, table and measure
# --------------------------------------------------------------------------
def test_a_drop_beside_a_keep_never_takes_what_is_kept(tmp_path):
    a = _lines(tmp_path, derive=False)
    said = _typed("Drop the Kiosk lines and keep the GV- vouchers.", a, col="Outlet")
    got = [(_conds(c)) for c in rules.candidates(a, said) if c["rule"].table == a.main_table.tid]
    assert got == [[("Outlet", "in", ("Kiosk",))]]
    said = _typed("Drop only the older copies and keep the GV- originals.", a, col="Part")
    assert rules.candidates(a, said) == []                           # the kept rows are never the rows dropped
    # twin: 'keep' alone proposes nothing
    assert rules.candidates(a, _typed("Keep the GV- rows.", a, col="Part")) == []
    # the words stay the owner's rule, listed, not dropped
    assert [u["said"] for u in rules.unread(a, said)] == ["Drop only the older copies and keep the GV- originals."]


def test_a_pronoun_object_binds_what_its_sentence_or_the_one_before_named(tmp_path):
    a = _lines(tmp_path, derive=False)
    said = _typed("The Kiosk lines are copies from the old till. Drop them.", a, col="Outlet")
    assert [_conds(c) for c in rules.candidates(a, said) if c["rule"].table == a.main_table.tid] == \
        [[("Outlet", "in", ("Kiosk",))]]
    # named in its own sentence first: never a value of the sentence before
    said = _typed("Kiosk lines are fine. Lines with Parts starting with GV- are vouchers; leave them out of every total.",
                  a, col="Part")
    assert [_conds(c) for c in rules.candidates(a, said) if c["rule"].table == a.main_table.tid] == \
        [[("Part", "prefix", ("GV-",))]]
    # the order stays in the note of the sentence it points back to
    assert brain.note_groups([("The Kiosk lines are copies from the old till.", 0), ("Drop them.", 1)]) == \
        ["The Kiosk lines are copies from the old till. Drop them."]


def test_a_category_word_used_as_a_plain_noun_binds_nothing(tmp_path):
    a = _stock(tmp_path)
    said = _typed("Harbor Supply is a new local paper supplier. Leave them out of every total.", a, "Stock", "Group")
    assert not [c for c in rules.candidates(a, said) if any(p["col"] == "Group" for p in c["rule"].predicate)]
    # twin: the category named as written is the subject the pronoun stands for
    said = _typed("Paper is resold at cost. Leave them out of every total.", a, "Stock", "Group")
    assert any(_conds(c) == [("Group", "in", ("Paper",))] for c in rules.candidates(a, said))


def test_scope_words_resolve_to_measures_and_a_named_count_is_every_total(tmp_path):
    a = _lines(tmp_path)
    said = _typed("Kiosk lines are not sales: leave them out of sales and discount numbers, not just Unit Price.", a,
                  col="Outlet")
    got = [c for c in rules.candidates(a, said) if c["rule"].table == a.main_table.tid]
    assert [(_conds(c), c["rule"].scope) for c in got] == [([("Outlet", "in", ("Kiosk",))], ["Net", "Discount"])]
    p = _pay(tmp_path)
    said = _typed("Leave XR out of cost, pay, hours and headcount.", p, "Register", "Site")
    got = {p.table(c["rule"].table).sheet: c["rule"].scope for c in rules.candidates(p, said)}
    assert got["Register"] == [] and got.get("Staff", []) == []      # a named count is every count and total
    # twin: a list of columns only is those totals
    said = _typed("Leave XR out of Gross and Extra Time.", p, "Register", "Site")
    got = {p.table(c["rule"].table).sheet: c["rule"].scope for c in rules.candidates(p, said)}
    assert sorted(got["Register"]) == ["Extra Time", "Gross"]


def test_a_value_on_two_tabs_binds_to_the_tab_the_sentence_names(tmp_path):
    p = _pay(tmp_path)
    tabs = {p.table(c["rule"].table).sheet for c in rules.candidates(
        p, _typed("Leave XR out of every total on the Register tab.", p, "Register", "Site"))}
    assert tabs == {"Register"}
    # twin: naming no tab, every tab that holds the value
    tabs = {p.table(c["rule"].table).sheet for c in rules.candidates(
        p, _typed("Leave XR out of every total.", p, "Register", "Site"))}
    assert tabs == {"Register", "Staff"}


def test_a_rules_size_is_never_the_sum_of_a_price(tmp_path):
    a = _lines(tmp_path)
    e = rules.effect(a, Rule("exclude", a.main_table.tid, [{"col": "Outlet", "op": "in", "values": ["Kiosk"]}]))
    assert e["rows"] == 50 and e["col"] == ""
    prod = a.table(_tid(a, "Lineup"))
    assert rules.lookup_key(a, prod.tid) == "Part"
    e = rules.effect(a, Rule("exclude", prod.tid, [{"col": "Group", "op": "in", "values": ["Tools"]}]))
    assert e["col"] == ""                                            # a lookup tab's money is no total
    # twin: a table with an amount of its own is sized by it
    b = _lines(tmp_path, name="amount.xlsx", derive=False)
    e = rules.effect(b, Rule("exclude", b.main_table.tid, [{"col": "Outlet", "op": "in", "values": ["Kiosk"]}]))
    assert e["col"] == "Line Total"


def test_a_unit_rule_takes_the_condition_the_sentence_before_gave_its_column(tmp_path):
    a = _stock(tmp_path)
    said = _typed("Cost Each on cellar lines is the case price, and every cellar item comes 12 to a case. The real "
                  "bottle cost is Cost Each divided by 12.", a, "Stock", "Cost Each")
    got = {a.table(c["rule"].table).sheet: c for c in rules.candidates(a, said) if c["rule"].kind == "scale"}
    assert _conds(got["Stock"]) == [("Group", "in", ("Cellar",))]
    assert got["Loss Log"]["rule"].predicate and got["Loss Log"]["rows"] == 20
    # twin: with no sentence before naming the column, the rule is for every row as said
    said = _typed("The real bottle cost is Cost Each divided by 12.", a, "Stock", "Cost Each")
    assert all(not c["rule"].predicate for c in rules.candidates(a, said) if c["rule"].kind == "scale")


# --------------------------------------------------------------------------
# fix 6: one row set, one scope; a readback never re-offers what a pick settled
# --------------------------------------------------------------------------
def _site_pick(p, label, scope=("Gross",)):
    t = p.table(_tid(p, "Register"))
    q = interview.Q("find_odd_site_xr", "Site XR", "XR in Site on Register (64 rows) is unlike the others. Is XR ours, "
                    "counted in Gross totals?",
                    [{"id": "leave_out", "label": label, "desc": "Its 64 rows come out of Gross totals"},
                     {"id": "ours", "label": "Ours, count it"}], kind="exclusion", source="finding",
                    fact={"kind": "exclusion"},
                    meta={"about": {"table": t.tid, "col": "Site", "aspect": "meaning", "values": ["XR"]},
                          "exclude": {"table": t.tid, "col": "Site", "values": ["XR"], "options": ["leave_out"],
                                      "scope": list(scope)}})
    return q


def test_a_pick_that_says_what_the_record_is_leaves_it_out_of_every_total_on_every_joined_tab(tmp_path):
    p = _pay(tmp_path)
    q = _site_pick(p, "Not ours: leave it out (type why)")
    said = {q.id: interview._answer(q, ["leave_out"], "")}
    got = rules.confirmed(p, said)
    # the value reaches the roster tab its column joins (Home Site), out of every total there too
    assert sorted((p.table(r.table).sheet, r.predicate[0]["col"], r.scope) for r in got) == \
        [("Register", "Site", []), ("Staff", "Home Site", [])]
    # a record ID reaches every tab its ID joins
    t = p.table(_tid(p, "Register"))
    q2 = interview.Q("find_outliers_x", "Test ID", "E900 has 15 rows. What is it?",
                     [{"id": "test", "label": "A test record, never real (type why)"}], kind="exclusion",
                     source="finding", fact={"kind": "exclusion"},
                     meta={"about": {"table": t.tid, "col": "Worker No", "aspect": "meaning", "values": ["E900"]},
                           "exclude": {"table": t.tid, "col": "Worker No", "values": ["E900"], "options": ["test"],
                                       "scope": ["Gross"]}})
    got = rules.confirmed(p, {q2.id: interview._answer(q2, ["test"], "")})
    assert sorted((p.table(r.table).sheet, r.scope) for r in got) == [("Register", []), ("Staff", [])]
    # twin: a pick that only names a total stays on that total, on its own tab
    q3 = _site_pick(p, "Leave out of Gross totals")
    got = rules.confirmed(p, {q3.id: interview._answer(q3, ["leave_out"], "")})
    assert [(p.table(r.table).sheet, r.scope) for r in got] == [("Register", ["Gross"])]


def test_leave_outs_of_the_same_rows_merge_and_every_total_wins(tmp_path):
    p = _pay(tmp_path)
    q = _site_pick(p, "Leave out of Gross totals")
    said = {q.id: interview._answer(q, ["leave_out"], "")}
    t = p.table(_tid(p, "Register"))
    typed = Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["XR"]}])
    said.update(_tick([{"rule": typed}]))
    got = [r for r in rules.confirmed(p, said) if r.table == t.tid]
    assert [(r.kind, r.scope) for r in got] == [("exclude", [])]


def test_words_typed_with_a_scoped_pick_that_say_every_total_win(tmp_path):
    p = _pay(tmp_path)
    t = p.table(_tid(p, "Register"))
    rule = dict(Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["XR"]}], scope=["Gross"]).to_dict(),
                option="leave_1", confirmed=True)
    said = {"codes_site": {"options": ["leave_1"], "labels": ["Leave XR out of Gross totals"], "not_sure": False,
                           "codes": ["NA", "SB", "XR"], "rules": [rule],
                           "text": "XR = a site we run for another firm, not part of us; leave XR out of every total.",
                           "about": {"table": t.tid, "col": "Site", "aspect": "meaning"}}}
    assert [r.scope for r in rules.confirmed(p, said) if r.table == t.tid] == [[]]
    # twin: words that name no wider total leave the pick as it was
    said["codes_site"]["text"] = "XR = a site we run for another firm."
    assert [r.scope for r in rules.confirmed(p, said) if r.table == t.tid] == [["Gross"]]


def test_the_readback_offers_a_wider_rule_as_wider_and_never_a_lookup_row(tmp_path):
    p = _pay(tmp_path)
    t = p.table(_tid(p, "Register"))
    q = interview.Q("find_x_e900", "E900", "E900 on Register has 15 rows. Counted in Gross totals?",
                    [{"id": "leave_out", "label": "Leave out of Gross totals"}, {"id": "keep", "label": "Keep it"}],
                    kind="exclusion", source="finding", fact={"kind": "exclusion"},
                    meta={"about": {"table": t.tid, "col": "Worker No", "aspect": "treatment", "values": ["E900"]},
                          "exclude": {"table": t.tid, "col": "Worker No", "values": ["E900"], "options": ["leave_out"],
                                      "scope": ["Gross"]}})
    said = {q.id: interview._answer(q, ["leave_out"], "")}
    said.update(_typed("Leave E900 out of every total.", p, "Register", "Worker No"))
    p.apply_answers(said)
    props = rules.proposals(p, said)
    reg = [c for c in props if c["rule"].table == t.tid]
    assert [c["rule"].scope for c in reg] == [[]] and reg[0]["narrower"] == ["Gross"]
    rb = findings.readback(p, said)
    rules.dress(p, rb)
    lab = next(o["label"] for o in rb.options if (rb.meta["rules"].get(o["id"]) or {}).get("table") == t.tid)
    assert "every count, not just Gross" in lab and len(lab) <= 60
    # the same rule, as narrow as the pick: never asked again
    said["house_rules"]["text"] = "Leave E900 out of Gross."
    assert not [c for c in rules.proposals(p, said) if c["rule"].table == t.tid]
    # a lookup tab's own row for a key changes no total: never offered
    a = _lines(tmp_path)
    said = _typed("Leave GV-010 out of every total.", a, "Lineup", "Part")
    assert not [c for c in rules.proposals(a, said) if c["rule"].table == _tid(a, "Lineup")]


# --------------------------------------------------------------------------
# fix 7: an option the owner's words restated, ticked on the readback
# --------------------------------------------------------------------------
def test_a_ticked_readback_line_is_a_pick_on_its_own_question(tmp_path):
    p = _pay(tmp_path)
    q = _site_pick(p, "Not ours: leave it out (type why)")
    q.fact["statements"] = {"leave_out": "XR in Site on Register is not ours, per the owner."}
    orig = interview._answer(q, [], "XR is the site we run for Pell Co, they pay us back.")
    line = {"kind": "infer", "table": "", "infer": {"qid": q.id, "option": "leave_out",
                                                  "label": "Not ours: leave it out (type why)",
                                                  "desc": "Its 64 rows come out of Gross totals"}}
    said = {q.id: orig, "confirm_rules_i": {"options": ["r1"], "labels": ["So: XR is not ours"], "text": "",
                                            "not_sure": False, "rules": [dict(line, option="r1", confirmed=True)]}}
    got = rules.confirmed(p, said)
    assert [(r.predicate[0]["values"], r.scope) for r in got if r.kind == "exclude"
            and r.table == _tid(p, "Register")] == [(["XR"], [])]
    p.apply_answers(said)
    note = _notes(Composer(p, p.paths[0], "b1", said).compose(), q.id)[0]
    assert note.startswith("XR in Site on Register is not ours, per the owner") and \
        note.endswith('the owner wrote: "XR is the site we run for Pell Co, they pay us back."')
    # twin: the same line not ticked applies nothing and writes the owner's words only
    said["confirm_rules_i"]["rules"][0]["confirmed"] = False
    said["confirm_rules_i"]["options"] = []
    assert not [r for r in rules.confirmed(p, said) if r.kind == "exclude"]
    p.apply_answers(said)
    note = _notes(Composer(p, p.paths[0], "b1", said).compose(), q.id)[0]
    assert note.startswith('Asked "XR in Site') and "not ours, per the owner" not in note


# --------------------------------------------------------------------------
# fix 8: model inputs, and the privacy screen's vocabulary
# --------------------------------------------------------------------------
def test_a_line_item_word_is_data_and_a_client_about_to_churn_is_private():
    heads = {"Churn rate", "Pay per person"}
    assert privacy.classify("Churn rate is 1.5% of opening seats.", set(), heads)[0][1] == "data"
    assert privacy.classify("Our churn runs about 2% a month.", set(), set())[0][1] == "data"
    assert privacy.classify("Their client is about to churn.", set(), set())[0][1] == "private"
    assert privacy.classify("Medical supplies are billed monthly.", set(), set())[0][1] == "data"
    assert privacy.classify("Jonah is lazy about keying them in.", set(), set())[0][1] == "private"


def test_a_typed_reply_to_an_inputs_readback_is_split_per_input():
    prompt = ("These inputs drive the model: Uplift (Levers!B4) = 4% (\"monthly\"), in New seats = x; Loss "
              "(Levers!B5) = 2%, with no note, in Lost seats = y; Price (Levers!B6) = 49, with no note, in Seat revenue = z. "
              "Right as read?")
    ans = {"options": [], "labels": [], "text": "Uplift and Loss are right. The price moves in March.",
           "not_sure": False, "prompt": prompt, "header": "Inputs"}
    notes = [s for s, _ in brain._input_notes(ans)]
    assert len(notes) == 3
    assert notes[0].startswith("Uplift (Levers!B4) = 4%") and "right as read, per the owner" in notes[0]
    assert notes[1].startswith("Loss (Levers!B5) = 2%")
    assert notes[2] == 'On "Inputs", the owner also wrote: "The price moves in March."'
    assert "Right as read?" not in " ".join(notes)
    # a label with an aside is named by its words before it ('UP' for 'UP (monthly uplift)')
    aside = dict(ans, prompt=prompt.replace("Uplift (Levers!B4)", "UP (monthly uplift) (Levers!B4)"),
                 text="UP is right.")
    assert [s.split(" = ")[0] for s, _ in brain._input_notes(aside)] == ["UP (monthly uplift) (Levers!B4)"]
    # twin: a reply that names no input is the plain answer
    assert brain._input_notes(dict(ans, text="All good.")) == []


# --------------------------------------------------------------------------
# fix 11: up to three typed sentences stay with the question
# --------------------------------------------------------------------------
def test_up_to_three_sentences_stay_in_the_note_with_the_question():
    q = _q([{"id": "a", "label": "One"}], prompt="How is Hours counted?", header="Hours", multi=False)
    said = "Hours are clock hours. Breaks are unpaid. Overtime is its own column."
    notes = [s for s, _ in _answer_notes(interview._answer(q, [], said), q.fact, None)]
    assert notes == [f'Asked "How is Hours counted?", the owner wrote: "{said}"']
    doubt = "Hours are clock hours. Maybe breaks are paid."
    assert len(_answer_notes(interview._answer(q, [], doubt), q.fact, None)) == 2
    lines = "Hours are clock hours.\nBreaks are unpaid."
    assert len(_answer_notes(interview._answer(q, [], lines), q.fact, None)) == 2
    defs = "A1 = the north yard. B2 = the south yard."
    assert len(_answer_notes(interview._answer(q, [], defs), q.fact, None)) == 2


# --------------------------------------------------------------------------
# fix 15: the owner's switch date is the date every note of that switch gives
# --------------------------------------------------------------------------
def test_the_owners_switch_date_replaces_the_one_found(tmp_path):
    a = _lines(tmp_path, derive=False)
    t = a.main_table
    q = interview.Q("find_boundary_lines_20260226", "Switch date", "Around Feb 26, 2026, Lines changes form. What "
                    "happened then?", [{"id": "system", "label": "A new system or export"}], multi=True,
                    kind="history", source="finding",
                    fact={"kind": "history", "statements": {
                        "system": "Lines changes form around Feb 26, 2026: a new system or export, per the owner."}},
                    meta={"about": {"table": t.tid, "col": "Ref", "aspect": "history"}})
    said = {q.id: interview._answer(q, ["system"], "We moved tills on March 2, 2026.")}
    note = _notes(Composer(a, a.paths[0], "b1", said).compose(), q.id)[0]
    assert note.startswith("Lines changes form around Mar 2, 2026: a new system or export, per the owner")
    # twin: no date typed, the date found stays
    said = {q.id: interview._answer(q, ["system"], "We moved tills.")}
    assert _notes(Composer(a, a.paths[0], "b1", said).compose(), q.id)[0].startswith(
        "Lines changes form around Feb 26, 2026")


# --------------------------------------------------------------------------
# fix 16: owner rules that change a column's arithmetic become rules
# --------------------------------------------------------------------------
def test_before_a_date_subtract_a_column_is_an_adjust_rule(tmp_path):
    p = _pay(tmp_path)
    said = _typed("In the old rows (checks dated before March 1, 2026), Hours includes overtime; to get true hours, "
                  "subtract Extra Time.", p, "Register", "Hours")
    got = [c for c in rules.candidates(p, said) if c["rule"].kind == "adjust"]
    assert len(got) == 1
    r = got[0]["rule"]
    assert r.values == {"col": "Hours", "minus": "Extra Time"} and r.predicate[0]["values"] == ["", "2026-02-28"]
    t = p.table(r.table)
    jh, jo, jd = t.headers.index("Hours"), t.headers.index("Extra Time"), t.headers.index("Paid On")
    ot_before = sum(row[jo] for row in t.rows if row[jd] < dt.datetime(2026, 3, 1))
    before = sum(row[jh] for row in t.rows)
    said.update(_tick(got))
    p.apply_answers(said)
    after = sum(row[jh] for row in p._ctx.rows(t, "Hours"))
    assert round(before - after, 2) == round(ot_before, 2)
    recs = Composer(p, p.paths[0], "b1", said).compose()
    assert any("Hours is taken as Hours minus Extra Time" in r["statement"] for r in recs if r.get("ref") == "rule:applied")
    # twin: without a date there is nothing to read
    assert not [c for c in rules.candidates(p, _typed("Subtract Extra Time from Hours.", p, "Register", "Hours"))
                if c["rule"].kind == "adjust"]


def test_a_treatment_no_rule_could_be_read_from_is_listed(tmp_path):
    a = _lines(tmp_path, derive=False)
    said = _typed("Never add the voucher quantities into units sold.", a, col="Part")
    assert [u["said"] for u in rules.unread(a, said)] == ["Never add the voucher quantities into units sold."]
    recs = Composer(a, a.paths[0], "b1", said).compose()
    assert any(r["statement"] == 'The owner\'s rule, not applied from these words alone: "Never add the voucher '
               'quantities into units sold."' for r in recs)
    # twin: a sentence that states no treatment is not a rule
    assert rules.unread(a, _typed("Vouchers are store credit.", a, col="Part")) == []


# --------------------------------------------------------------------------
# fix 22: counted totals respect the table's own reading
# --------------------------------------------------------------------------
def test_the_counted_total_counts_every_rule_and_the_rows_two_share(tmp_path):
    a = _lines(tmp_path, derive=False)
    t = a.main_table
    r1 = Rule("exclude", t.tid, [{"col": "Outlet", "op": "in", "values": ["Kiosk"]}])
    r2 = Rule("exclude", t.tid, [{"col": "Part", "op": "in", "values": ["GV-010", "TP-110"]}])
    m = Rule("map", t.tid, [{"col": "Outlet", "op": "in", "values": ["Web", "Shop"]}],
             {"col": "Outlet", "to": {"web": "Web", "shop": "Web"}})
    said = _tick([{"rule": r1}, {"rule": r2}, {"rule": m}])
    a.apply_answers(said)
    total = next(r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose() if r.get("ref") == "rule:total")
    assert total.startswith("Counted after all 3 of the owner's rules on Lines:")
    both = sum(1 for row in t.rows if row[3] == "Kiosk" and row[2] in ("GV-010", "TP-110"))
    assert both and f"share {both} rows" in total


def test_a_snapshots_stock_is_read_on_its_latest_date_in_the_counted_total(tmp_path):
    a = _stock(tmp_path)
    t = a.table(_tid(a, "Stock"))
    assert "Stock Value" in (a.snapshots.get(t.tid) or {}).get("stock", [])
    said = _tick([{"rule": Rule("exclude", t.tid, [{"col": "Site", "op": "in", "values": ["WR"]}])},
                  {"rule": Rule("exclude", t.tid, [{"col": "Item No", "op": "in", "values": ["1001"]}])}])
    a.apply_answers(said)
    total = next(r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose() if r.get("ref") == "rule:total")
    assert "on the latest Week, " in total and "rows then:" in total


def _gl(tmp_path):
    """A journal whose Credit column is written positive before May 2025 and negative after."""
    path = str(tmp_path / "gl.xlsx")
    wb = xlsxwriter.Workbook(path)
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Journal")
    ws.write_row(0, 0, ["Entry", "Date", "Account", "Debit", "Credit"])
    r = 1
    for e in range(120):
        day = dt.datetime(2025, 1, 2) + dt.timedelta(days=e * 2)
        amt = 100.0 + e
        for acct, dr, cr in (("Cash", amt, 0), ("Sales", 0, -amt if day >= dt.datetime(2025, 5, 1) else amt)):
            ws.write(r, 0, f"T{e}")
            ws.write_datetime(r, 1, day, fmt)
            ws.write_row(r, 2, [acct, dr, cr])
            r += 1
    wb.close()
    return Analysis([path])


def test_a_same_side_sign_pick_sums_the_column_without_its_sign(tmp_path):
    g = _gl(tmp_path)
    b = next(i for i in g.insights if i["recipe"].startswith("boundary:"))
    qid = "find_boundary_journal_" + b["numbers"]["date"].replace("-", "")
    said = {qid: {"options": ["sign"], "labels": ["Same meaning, only the sign flipped"], "text": "", "not_sure": False,
                  "about": {"table": g.main_table.tid, "col": "Credit", "aspect": "history"}, "prompt": "?"}}
    g.apply_answers(said)
    credit = next(r["statement"] for r in Composer(g, g.paths[0], "b1", said).compose() if r["id"].endswith(".{Credit}"))
    t = g.main_table
    want = sum(abs(row[4]) for row in t.rows)
    assert "as absolute values, both signs meaning the same per the owner" in credit
    from sheetbrain.recipes import fmt_money
    assert f"summing to {fmt_money(want)} as absolute values" in credit
    # twin: without the pick, the column is summed as written
    g.apply_answers({})
    credit = next(r["statement"] for r in Composer(g, g.paths[0], "b1", {}).compose() if r["id"].endswith(".{Credit}"))
    assert "absolute" not in credit


def test_a_pair_rule_names_both_sides(tmp_path):
    path = str(tmp_path / "voids.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Checks")
    ws.write_row(0, 0, ["Check No", "Type", "Worker No", "Amount"])
    r = 1
    for i in range(60):
        ws.write_row(r, 0, [f"C{1000 + i}", "R", f"E{i % 12}", 400.0 + i])
        r += 1
    for i in range(5):
        ws.write_row(r, 0, [f"C{1000 + i}", "V", f"E{i % 12}", -(400.0 + i)])
        r += 1
    wb.close()
    a = Analysis([path])
    t = a.main_table
    pair = Rule("pair", t.tid, [{"col": "Type", "op": "in", "values": ["V"]}], {"match": ["Check No"]})
    said = _tick([{"rule": pair}])
    a.apply_answers(said)
    note = next(r["statement"] for r in Composer(a, path, "b1", said).compose() if r.get("ref") == "rule:applied")
    assert "(5 rows and the 5 rows they cancel, 10 in all)" in note


# --------------------------------------------------------------------------
# fix 23: the readback says each rule's scope in its label; 'last' counts what is to come
# --------------------------------------------------------------------------
def test_the_readback_puts_each_rules_scope_in_its_label(tmp_path):
    p = _pay(tmp_path)
    said = _typed("Leave XR out of Gross.", p, "Register", "Site")
    rb = findings.readback(p, said)
    rules.dress(p, rb)
    assert not [o for o in rb.options if o["id"] == "scope"] and not rb.meta.get("scopes")
    lab = next(o["label"] for o in rb.options if o["id"] in rb.meta["rules"])
    assert lab.endswith("out of Gross only") and len(lab) <= 60


def test_an_adjust_rule_reads_back_as_what_it_takes_off(tmp_path):
    p = _pay(tmp_path)
    said = _typed("Before March 1, 2026, Hours includes overtime: subtract Extra Time.", p, "Register", "Hours")
    rb = findings.readback(p, said)
    rules.dress(p, rb)
    lab = next(o["label"] for o in rb.options if (rb.meta["rules"].get(o["id"]) or {}).get("kind") == "adjust")
    assert lab.startswith("Hours minus Extra Time to 2026-02-28") and len(lab) <= 60


def test_the_last_wording_counts_what_is_still_to_come():
    one = sb._steps_left({}, readback=True)
    assert "last" not in one.lower() and one.startswith("Got it. Two more: the rules you typed, and what to build")
    assert "may need one more answer" in one and "save it" in one
    spent = {"confirm_rules_a": {}, "confirm_rules_b": {}}
    assert "may need" not in sb._steps_left(spent, readback=False)
