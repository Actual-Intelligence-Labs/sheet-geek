"""v0.2 practice check, round 3: typed-rule extraction, rule readbacks and
scopes, applying rules, notes, the privacy screen, the preview and the save
protocol. Every book here is written inline with synthetic names and values; no
development workbook is read."""
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
from sheetbrain import brain, findings, interview, privacy, rules, say  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer, _answer_notes  # noqa: E402
from sheetbrain.rules import Rule  # noqa: E402

SB = os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py")


# --------------------------------------------------------------------------
# inline synthetic books
# --------------------------------------------------------------------------
ITEMS = [("1001", "Food", 4.0), ("1002", "Food", 6.5), ("1003", "Food", 3.2), ("1004", "Paper", 9.0),
         ("1005", "Paper", 11.0), ("7001", "Cellar", 180.0), ("7002", "Cellar", 150.0), ("7003", "Cellar", 210.0),
         ("1006", "Food", 5.5), ("1007", "Paper", 7.25)]
SITES = ["NT", "ST", "WR", "WRX", "K4"]


def _stock(tmp_path, join=True, name="stock.xlsx"):
    """Counts (Week, Item No, Group, Site, On Hand, Cost Each, Stock Value = On Hand
    x Cost Each), a Loss Log (Date, Item No, Site, Why, Qty, Cost Each, Loss Value =
    Qty x Cost Each; no Group) and a Catalog lookup. join=False writes the Loss Log
    with a Lot number instead of Item No: nothing ties its rows to a group."""
    rng = random.Random(3)
    path = str(tmp_path / name)
    wb = xlsxwriter.Workbook(path)
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws = wb.add_worksheet("Counts")
    ws.write_row(0, 0, ["Week", "Item No", "Group", "Site", "On Hand", "Cost Each", "Stock Value"])
    r = 1
    for w in range(12):
        for code, cat, cost in ITEMS:
            n = rng.randint(1, 9)
            ws.write_datetime(r, 0, dt.datetime(2026, 1, 4) + dt.timedelta(days=7 * w), fmt)
            ws.write_row(r, 1, [code, cat, SITES[(w + int(code)) % 5], n, cost, round(n * cost, 2)])
            r += 1
    ws = wb.add_worksheet("Loss Log")
    ws.write_row(0, 0, ["Date", "Item No" if join else "Lot", "Site", "Why", "Qty", "Cost Each", "Loss Value"])
    for i in range(90):
        code, _cat, cost = ITEMS[i % len(ITEMS)]
        q = rng.randint(1, 4)
        ws.write_datetime(i + 1, 0, dt.datetime(2026, 1, 2) + dt.timedelta(days=i), fmt)
        ws.write_row(i + 1, 1, [code if join else f"L{700 + i}", SITES[i % 5], 1 + (i % 5), q, cost,
                                round(q * cost, 2)])
    ws = wb.add_worksheet("Catalog")
    ws.write_row(0, 0, ["Item No", "Group", "Supplier"])
    for k, (code, cat, _c) in enumerate(ITEMS):
        ws.write_row(k + 1, 0, [code, cat, ["Harbor Supply", "Gull Foods", "Pier Paper"][k % 3]])
    wb.close()
    return Analysis([path])


def _orders(tmp_path):
    """Order lines: SKU with three GV- vouchers, Status, Channel, Qty, Unit Price, Amount."""
    path = str(tmp_path / "orders.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Line Items")
    ws.write_row(0, 0, ["Order ID", "SKU", "Status", "Channel", "Qty", "Unit Price", "Amount"])
    skus = [("GV-020", 20.0), ("GV-040", 40.0), ("GV-080", 80.0), ("TK-201", 149.0), ("PD-310", 69.0),
            ("SV-120", 34.5), ("CR-440", 55.0), ("LN-900", 18.0)]
    for i in range(160):
        sku, price = skus[i % len(skus)]
        q = 1 + i % 3
        ws.write_row(i + 1, 0, [f"#{50000 + i}", sku, ["Closed", "Open", "Returned", "Sent"][i % 4],
                                ["Online", "Kiosk", "Mail", "RX"][i % 4 if i % 9 else 3], q, price, round(q * price, 2)])
    wb.close()
    return Analysis([path])


def _ledger(tmp_path):
    """A unit ledger: charges on CHG and PEN rows, payments on RCV rows (GL 1200, or
    3300 for a few escrow holds), and a Status A or X."""
    path = str(tmp_path / "ledger.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Ledger")
    ws.write_row(0, 0, ["Unit", "Code", "GL", "Charge", "Payment", "Status"])
    for i in range(150):
        unit = f"U{100 + i % 15}"
        if i % 3 == 0:
            row = [unit, "CHG", 5000, 910.0, 0, "A"]
        elif i % 3 == 1:
            row = [unit, "PEN", 5000, 40.0, 0, "A"]
        else:
            gl = 3300 if i % 30 == 2 else 1200
            row = [unit, "RCV", gl, 0, 460.0 if gl == 3300 else 910.0, "X" if i % 45 == 5 else "A"]
        ws.write_row(i + 1, 0, row)
    wb.close()
    return Analysis([path])


def _register(tmp_path):
    """A pay register whose Location holds names on its first 60 rows and codes after."""
    path = str(tmp_path / "register.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Register")
    ws.write_row(0, 0, ["Worker ID", "Location", "Position", "Hours", "Gross Wages"])
    pairs = [("Ashford Mill", "AFM"), ("Brook Hollow", "BKH"), ("Cedar Gap", "CDG")]
    for i in range(120):
        name, code = pairs[i % 3]
        ws.write_row(i + 1, 0, [f"E{100 + i % 30}", name if i < 60 else code, ["CREW", "HAUL", "PIECE"][i % 3],
                                3 + i % 5 if i % 3 == 2 else 30 + i % 10, 1000.0 + i])
    wb.close()
    return Analysis([path])


def _buys(tmp_path):
    """Purchase lines: Item # fee codes SVC-A and SVC-B that are exactly the Category
    Fees rows, a WHX site, and Amount with cents."""
    rng = random.Random(9)
    path = str(tmp_path / "buys.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Detail")
    ws.write_row(0, 0, ["Site", "Item #", "Category", "Amount"])
    for i in range(200):
        fee = i % 8 == 0
        item = ["SVC-A", "SVC-B"][(i // 8) % 2] if fee else f"IT{300 + i % 17}"
        ws.write_row(i + 1, 0, [["Harbor", "Ridge", "Mesa", "WHX"][i % 4], item, "Fees" if fee else
                                ["Dairy", "Paper", "Meat"][i % 3], round(rng.uniform(20, 300), 2)])
    ws.write_row(201, 0, ["Mesa", "IT301", "Meat", 14.30])
    ws.write_row(202, 0, ["Ridge", "IT302", "Meat", 13.10])
    wb.close()
    return Analysis([path])


def _pb(**roles):
    spec = {}
    for rid, (header, typ, kind, unit, additive) in roles.items():
        spec[rid] = {"label": header, "headers": [header.lower()], "type": typ, "kind": kind, "entity": header.lower()}
        if unit:
            spec[rid]["unit"] = unit
        if additive is not None:
            spec[rid]["additive"] = additive
    return {"generic": {"id": "generic", "roles": spec, "insights": ["top_share:amount:place"], "questions": [],
                        "graph": {"size_by": "amount", "entities": [], "relations": [], "mode": "entities"}}}


def _typed(text, a, tab=None, col=None, qid="house_rules", kind="definition", codes=None):
    t = a.table(tab) if tab else a.main_table
    out = {"options": ["type"] if codes else [], "labels": [], "text": text, "not_sure": False, "kind": kind,
           "about": {"table": t.tid, "col": col or t.headers[0], "aspect": "meaning"}, "prompt": "What about it?",
           "header": "House rules"}
    if codes:
        out["codes"] = codes
    return {qid: out}


def _tid(a, sheet):
    return next(t.tid for t in a.tables if t.sheet == sheet)


def _conds(c):
    return [(p["col"], p.get("op", "in"), tuple(p["values"])) for p in c["rule"].predicate]


def _tick(cands, qid="confirm_rules_t"):
    return {qid: {"options": [f"r{k}" for k in range(1, len(cands) + 1)], "labels": [], "text": "", "not_sure": False,
                  "rules": [dict(c["rule"].to_dict(), option=f"r{k}", confirmed=True) for k, c in enumerate(cands, 1)]}}


def _sum(a, sheet, col, ruled=True):
    t = a.table(_tid(a, sheet))
    j = t.headers.index(col)
    rows = a._ctx.rows(t, col) if ruled else t.rows
    return round(sum(r[j] for r in rows if isinstance(r[j], (int, float))), 2)


CELLAR = ("Cost Each on cellar lines is the case price, and every cellar item comes 12 to a case, so the single cost "
          "is Cost Each divided by 12.")


# --------------------------------------------------------------------------
# fix 6: never a rule broader than the owner's sentence
# --------------------------------------------------------------------------
def test_a_unit_rule_keeps_its_condition_and_binds_it_through_a_key(tmp_path):
    a = _stock(tmp_path)
    cands = rules.candidates(a, _typed(CELLAR, a, "Counts", "Cost Each"))
    got = {a.table(c["rule"].table).sheet: c for c in cands}
    assert set(got) == {"Counts", "Loss Log"}                    # never the lookup, never every row
    assert _conds(got["Counts"]) == [("Group", "in", ("Cellar",))]
    assert _conds(got["Loss Log"]) == [("Item No", "in", ("7001", "7002", "7003"))]
    assert got["Loss Log"]["rule"].predicate[0]["via"]["col"] == "Group"
    assert "is one whose Group on" in rules.where(got["Loss Log"]["rule"])
    assert all(c["rule"].predicate for c in cands)               # no unconditional 'divided by 12' anywhere
    assert rules.unbound(a, _typed(CELLAR, a, "Counts", "Cost Each")) == []


def test_a_condition_that_binds_nowhere_offers_nothing_and_says_so(tmp_path):
    a = _stock(tmp_path)
    said = _typed("Cost Each on imported lines is per case of 12.", a, "Counts", "Cost Each")
    assert rules.candidates(a, said) == []
    assert {a.table(u["table"]).sheet for u in rules.unbound(a, said)} == {"Counts", "Loss Log"}
    recs = Composer(a, a.paths[0], "b1", said).compose()
    notes = [r["statement"] for r in recs if r["id"].startswith("f:xu:")]
    assert notes and all(n.startswith("Not applied to counted numbers: the owner wrote") for n in notes)
    # twin: the same rule said with no condition is for every row
    plain = rules.candidates(a, _typed("Cost Each is per case of 12.", a, "Counts", "Cost Each"))
    assert plain and all(not c["rule"].predicate for c in plain)


def test_a_leave_out_beside_a_keep_clause_is_only_ever_scoped(tmp_path):
    b = _buys(tmp_path)
    for text, words in (("SVC-A, SVC-B are fees: count in spend, leave out of price comparisons and rebate math.",
                         "price comparisons and rebate math"),
                        ("Count fees in spend but leave them out of totals by item.", "totals by item")):
        cands = rules.candidates(b, _typed(text, b))
        assert cands and all(c.get("scope_words") == words for c in cands), text
        assert rules.proposals(b, _typed(text, b)) == [], text          # never for every count and total
    # twin: without the keep clause the same leave-out is every count and total
    cands = rules.candidates(b, _typed("Leave fees out of totals.", b))
    assert cands and all(not c["rule"].scope for c in cands)


def test_rules_naming_the_same_rows_are_one_option(tmp_path):
    b = _buys(tmp_path)
    said = _typed("Leave SVC-A and SVC-B out of item price comparisons. Leave Fees out of item price comparisons.", b)
    cands = rules.candidates(b, said)
    assert len(cands) == 2 and len({c["rows"] for c in cands}) == 1
    shown = rules.proposals(b, said, scoped=True)
    assert len(shown) == 1 and shown[0]["rule"].predicate[0]["col"] == "Item #"     # the values as typed win
    rb = rules.scoped_readback(b, said)
    said[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    assert rules.proposals(b, said, scoped=True) == []                # once shown, neither is asked again


def test_scope_words_set_the_same_scope_on_every_tab_holding_the_value(tmp_path):
    a = _stock(tmp_path)
    cands = rules.candidates(a, _typed("K4 is not ours: leave K4 out of every group figure.", a, "Loss Log", "Site"))
    assert {a.table(c["rule"].table).sheet: tuple(c["rule"].scope) for c in cands} == {"Counts": (), "Loss Log": ()}
    only = rules.candidates(a, _typed("Leave K4 out, Loss Value only.", a, "Loss Log", "Site"))
    by = {a.table(c["rule"].table).sheet: c for c in only}
    assert by["Loss Log"]["rule"].scope == ["Loss Value"] and not by["Loss Log"].get("scope_words")
    assert by["Counts"]["scope_words"] == "Loss Value"                # Counts has no Loss Value: nothing changes there
    # a narrower pick with typed words that say every total: what the owner typed wins
    t = a.table(_tid(a, "Loss Log"))
    pick = {"find_codes_site": {"options": ["k4"], "labels": ["Leave K4 out of Loss Value totals"], "not_sure": False,
                                "text": "K4 = a kiosk, not part of us: leave K4 out of every group figure.",
                                "exclude": {"table": t.tid, "col": "Site", "values": ["K4"], "options": ["k4"],
                                            "scope": ["Loss Value"]}}}
    assert [r.scope for r in rules.confirmed(a, pick)] == [[]]
    pick["find_codes_site"]["text"] = "K4 = a kiosk."
    assert [r.scope for r in rules.confirmed(a, pick)] == [["Loss Value"]]


def test_money_words_scope_the_rule_to_the_money_the_rows_carry(tmp_path):
    a = _stock(tmp_path)
    said = _typed("4 = returned to the supplier. Code 4 is not spoilage, so leave code 4 out of the loss dollars.", a,
                  "Loss Log", "Why", codes=["1", "2", "3", "4", "5"])
    cands = rules.candidates(a, said)
    assert [(c["rule"].predicate[0]["values"], c["rule"].scope) for c in cands] == [(["4"], ["Loss Value"])]
    rb = rules.dress(a, findings.readback(a, said))
    assert "left out of Loss Value totals only" in rb.options[0]["desc"]
    assert "every count and total" not in rb.options[0]["desc"]


def test_a_calculation_named_as_the_subject_keeps_the_rule_for_it(tmp_path):
    b = _buys(tmp_path)
    said = _typed("Rebates get paid each quarter on product spend (fees and WHX excluded).", b)
    cands = rules.candidates(b, said)
    assert cands and all(c["scope_words"] == "rebates" for c in cands)
    assert rules.proposals(b, said) == [] and rules.unapplied(b, said) == []


# --------------------------------------------------------------------------
# fix 23: typed rules the parser used to miss
# --------------------------------------------------------------------------
def test_an_id_prefix_is_a_prefix_rule(tmp_path):
    o = _orders(tmp_path)
    for text in ("Leave GV- lines out of sales.", "Voucher lines (SKUs starting with GV-) stay out of sales."):
        cands = [c for c in rules.candidates(o, _typed(text, o)) if c["rule"].predicate[0]["op"] == "prefix"]
        assert [_conds(c) for c in cands] == [[("SKU", "prefix", ("GV-",))]], text
        assert cands[0]["rows"] == sum(1 for r in o.main_table.rows if r[1].startswith("GV-"))
    # a code written as a number has a prefix too
    a = _stock(tmp_path)
    cands = rules.candidates(a, _typed("Leave item numbers starting with 7 out of every total.", a, "Loss Log",
                                       "Item No"))
    log = [c for c in cands if a.table(c["rule"].table).sheet == "Loss Log"]
    assert [_conds(c) for c in log] == [[("Item No", "prefix", ("7",))]] and log[0]["rows"] == 27
    # twin: a prefix no two values share names no rows
    assert not [c for c in rules.candidates(o, _typed("Leave ZZ- lines out of sales.", o))
                if c["rule"].predicate[0]["op"] == "prefix"]


def test_not_a_measure_proposes_leaving_the_rows_out_of_it(tmp_path):
    led = _ledger(tmp_path)
    cands = rules.candidates(led, _typed("3300 is escrow money, not income.", led, col="GL"))
    assert [(_conds(c), c["rule"].scope) for c in cands] == [([("GL", "in", ("3300",))], ["Payment"])]
    # a sentence that points back to a code two sentences up ('They are ...')
    text = ("3300 = on a payment row, an escrow hold. Holds go into a separate escrow account. They are not rent or "
            "fee income and they are not applied to the unit balance. 1200 = regular payments.")
    cands = rules.candidates(led, _typed(text, led, col="GL", codes=["1200", "3300", "5000"]))
    assert {c["rule"].predicate[0]["values"][0] for c in cands} == {"3300"}
    # twin: a meaning with no measure says nothing to leave out
    assert rules.candidates(led, _typed("3300 is the escrow account.", led, col="GL")) == []


def test_combine_and_same_as_propose_a_map(tmp_path):
    a = _stock(tmp_path)
    said = _typed("WR = Westridge, WRX = Westridge (old code before May; same site as WR, combine them).", a,
                  "Loss Log", "Site", codes=SITES)
    maps = [c for c in rules.candidates(a, said) if c["rule"].kind == "map"]
    assert maps and all(sorted(c["rule"].predicate[0]["values"]) == ["WR", "WRX"] for c in maps)
    two = rules.candidates(a, _typed("WRX and WR are the same site, combine them.", a, "Loss Log", "Site"))
    assert two and all(c["rule"].kind == "map" for c in two)
    assert rules.candidates(a, _typed("WR is our oldest site.", a, "Loss Log", "Site")) == []


# --------------------------------------------------------------------------
# fix 10: a leave-out covers every spelling the owner merged
# --------------------------------------------------------------------------
def _branch_map(t):
    return Rule("map", t.tid, [{"col": "Location", "op": "in", "values": ["Ashford Mill", "AFM", "Brook Hollow", "BKH",
                                                                        "Cedar Gap", "CDG"]}],
                {"col": "Location", "to": {"ashford mill": "AFM", "afm": "AFM", "brook hollow": "BKH", "bkh": "BKH",
                                         "cedar gap": "CDG", "cdg": "CDG"}})


def test_an_exclusion_takes_every_spelling_a_confirmed_map_merged(tmp_path):
    a = _register(tmp_path)
    t = a.main_table
    out = Rule("exclude", t.tid, [{"col": "Location", "op": "in", "values": ["CDG"]}])
    said = {"find_map": {"options": [], "labels": [], "not_sure": False, "kind": "mapping",
                         "text": "Each name is its code: Ashford Mill is AFM, Brook Hollow is BKH. Cedar Gap is CDG.",
                         "rules": [dict(_branch_map(t).to_dict(), option="same", confirmed=False)]},
            "confirm_rules_k": {"options": ["r1"], "labels": [], "text": "", "not_sure": False,
                                "rules": [dict(out.to_dict(), option="r1", confirmed=True)]}}
    got = {r.kind: r for r in rules.confirmed(a, said)}
    assert sorted(got["exclude"].predicate[0]["values"]) == ["CDG", "Cedar Gap"]     # typed pairs confirm the map
    a.apply_answers(said)
    j = t.headers.index("Gross Wages")
    assert _sum(a, "Register", "Gross Wages") == round(sum(r[j] for r in t.rows if r[1] not in ("CDG", "Cedar Gap")), 2)
    # twin: a map the owner did not confirm never widens the leave-out
    said["find_map"]["text"] = "Ashford Mill is AFM, but Cedar Gap is a different place."
    assert [r.predicate[0]["values"] for r in rules.confirmed(a, said)] == [["CDG"]]


def test_a_proposed_leave_out_is_read_back_with_both_spellings(tmp_path):
    a = _register(tmp_path)
    t = a.main_table
    said = {"find_map": {"options": ["same"], "labels": ["Each name is its code"], "text": "", "not_sure": False,
                         "rules": [dict(_branch_map(t).to_dict(), option="same", confirmed=True)]}}
    said.update(_typed("Leave CDG out of every total.", a, col="Location"))
    props = rules.proposals(a, said)
    assert [sorted(c["rule"].predicate[0]["values"]) for c in props] == [["CDG", "Cedar Gap"]]
    assert props[0]["rows"] == 40 and "Cedar Gap" in rules.where(props[0]["rule"])


# --------------------------------------------------------------------------
# fix 22: rules reach derived columns and joined tabs
# --------------------------------------------------------------------------
def test_a_unit_rule_divides_the_columns_worked_out_from_it_on_both_tabs(tmp_path):
    a = _stock(tmp_path)
    said = _typed(CELLAR, a, "Counts", "Cost Each")
    said.update(_tick(rules.candidates(a, said)))
    got = rules.confirmed(a, said)
    assert sorted((a.table(r.table).sheet, r.values["col"], r.values.get("follows", "")) for r in got) == [
        ("Counts", "Cost Each", ""), ("Counts", "Stock Value", "Cost Each"),
        ("Loss Log", "Cost Each", ""), ("Loss Log", "Loss Value", "Cost Each")]
    raw = {k: _sum(a, *k, ruled=False) for k in (("Counts", "Stock Value"), ("Loss Log", "Loss Value"))}
    a.apply_answers(said)
    for (sheet, col), before in raw.items():
        t = a.table(_tid(a, sheet))
        j, cj = t.headers.index(col), t.headers.index("Item No")
        cellar = sum(r[j] for r in t.rows if str(r[cj]).startswith("7"))
        assert _sum(a, sheet, col) == round(before - cellar + cellar / 12, 2), sheet
    recs = Composer(a, a.paths[0], "b1", said).compose()
    assert any("so it follows the owner's rule on Cost Each" in r["statement"] for r in recs)


def test_a_leave_out_on_a_lookup_value_reaches_the_lines_through_its_key(tmp_path):
    a = _stock(tmp_path)
    cands = rules.candidates(a, _typed("Leave the Paper items out of every total.", a, "Counts", "Group"))
    got = {a.table(c["rule"].table).sheet: _conds(c) for c in cands}
    assert got["Loss Log"] == [("Item No", "in", ("1004", "1005", "1007"))]      # through the Catalog key
    assert got["Counts"] == [("Group", "in", ("Paper",))]
    # twin: without the key, the lines are never guessed at
    b = _stock(tmp_path, join=False, name="nojoin2.xlsx")
    cands = rules.candidates(b, _typed("Leave the Paper items out of every total.", b, "Counts", "Group"))
    assert "Loss Log" not in {b.table(c["rule"].table).sheet for c in cands}


def test_without_a_key_the_unit_rule_stays_on_its_own_tab(tmp_path):
    a = _stock(tmp_path, join=False, name="nojoin.xlsx")
    said = _typed(CELLAR, a, "Counts", "Cost Each")
    cands = rules.candidates(a, said)
    assert [a.table(c["rule"].table).sheet for c in cands] == ["Counts"]
    assert [a.table(u["table"]).sheet for u in rules.unbound(a, said)] == ["Loss Log"]
    said.update(_tick(cands))
    before = _sum(a, "Loss Log", "Loss Value", ruled=False)
    a.apply_answers(said)
    assert _sum(a, "Loss Log", "Loss Value") == before


# --------------------------------------------------------------------------
# fix 27: the counted total after every rule, each effect where its rows carry money
# --------------------------------------------------------------------------
def test_two_rules_give_one_counted_total_and_each_its_own_money(tmp_path):
    led = _ledger(tmp_path)
    t = led.main_table
    r1 = Rule("exclude", t.tid, [{"col": "Status", "op": "in", "values": ["X"]}])
    r2 = Rule("exclude", t.tid, [{"col": "GL", "op": "in", "values": ["3300"]}])
    e = rules.effect(led, r1)
    assert e["col"] == "Payment" and e["sum"] > 0                   # payment rows: never '0 of Charge'
    said = _tick([{"rule": r1}, {"rule": r2}])
    led.apply_answers(said)
    recs = Composer(led, led.paths[0], "b1", said).compose()
    total = [r["statement"] for r in recs if r.get("ref") == "rule:total"]
    j = t.headers.index("Payment")
    after = sum(r[j] for r in t.rows if r[5] != "X" and r[2] != 3300)
    assert len(total) == 1 and f"Payment {after:,.0f}" in total[0]
    per = [r["statement"] for r in recs if r.get("ref") == "rule:applied"]
    assert len(per) == 2 and not any("counted total is" in s for s in per)
    assert all("of Payment" in s for s in per)
    text = say.save_preview(recs, [], name="ledger.xlsx", kind="xlsx", tab_state="visible")
    assert total[0] in text


# --------------------------------------------------------------------------
# fix 3: notes keep what the owner was shown
# --------------------------------------------------------------------------
def _q(opts, statements=None, prompt="12 rows have Site XR. What is XR?"):
    return interview.Q("find_x", "Site XR", prompt, opts, multi=True,
                       fact={"kind": "definition", "statements": statements or {}})


def test_two_picks_write_two_notes_each_with_its_description():
    q = _q([{"id": "internal", "label": "Internal entries", "desc": "Counting them again doubles the total"},
            {"id": "double", "label": "Already counted elsewhere", "desc": "Leave these rows out of every tab"}])
    notes = [s for s, _ in _answer_notes(interview._answer(q, ["internal", "double"], ""), q.fact, None)]
    # changed on purpose (practice round 3b): the first note names both picks, then one note per pick
    assert len(notes) == 3
    assert notes[0] == 'Asked "12 rows have Site XR. What is XR?", the owner picked "Internal entries" and "Already counted elsewhere".'
    assert '"Internal entries" ("Counting them again doubles the total")' in notes[1]
    assert notes[2].startswith('On "Site XR", one of the owner\'s picks was "Already counted elsewhere" ("Leave these rows')
    one = _answer_notes(interview._answer(q, ["double"], ""), q.fact, None)
    assert one[0][0].endswith('picked "Already counted elsewhere" ("Leave these rows out of every tab").')
    goal = interview.Q("goal", "Goal", "What do you want from this sheet?",
                       [{"id": "leaks", "label": "Find leaks", "desc": "Leave these rows out of every tab"}],
                       multi=True, kind="goal")
    said = _answer_notes(interview._answer(goal, ["leaks"], ""), {}, None)[0][0]
    assert '"Find leaks"' in said and "Leave these rows" not in said        # a goal's pitch is never quoted


def test_a_description_that_adds_a_claim_is_flagged_by_the_lint():
    # changed on purpose (practice round 4, fix 1): desc_adds_claim is the lint that sends such a description
    # back to be rewritten; a note quotes the description the owner saw under the pick
    assert brain.desc_adds_claim("Loans, grants, gifts", "Where does the cash come from?", "Outside money")
    assert not brain.desc_adds_claim("Count each pair as one", "Same things under new names?", "Yes, all the same")
    # the rule's own values are counted evidence, not a claim
    assert not brain.desc_adds_claim("Rows where Item # is SVC-A or SVC-B: left out only for price comparisons",
                                     "From what you typed, 1 rule would leave 25 rows out only for price comparisons.",
                                     "Item #: 2 values")
    assert brain.desc_adds_claim("Transfers or bookkeeping entries", "What is WHX?", "Internal entries")
    q = _q([{"id": "outside", "label": "Outside money", "desc": "Loans, grants, gifts"}],
           prompt="Where does the cash come from?")
    note = _answer_notes(interview._answer(q, ["outside"], ""), q.fact, None)[0][0]
    assert '"Outside money" ("Loans, grants, gifts")' in note


def test_a_pick_that_applied_a_rule_says_what_it_did_and_only_then(tmp_path):
    b = _buys(tmp_path)
    t = b.main_table
    q = interview.Q("find_site_whx", "Site WHX", "50 rows have Site WHX. What is WHX?",
                    [{"id": "internal", "label": "Internal entries", "desc": "Counting them again doubles the total"},
                     {"id": "real", "label": "Real, keep it", "desc": ""}],
                    fact={"kind": "definition"},
                    meta={"about": {"table": t.tid, "col": "Site", "aspect": "meaning", "values": ["WHX"]},
                          "exclude": {"table": t.tid, "col": "Site", "values": ["WHX"], "options": ["internal"]}})
    said = {q.id: interview._answer(q, ["internal"], "")}
    b.apply_answers(said)
    note = next(r["statement"] for r in Composer(b, b.paths[0], "b1", said).compose() if r["id"] == f"f:{q.id}")
    # changed on purpose (practice round 4, fix 2): the tail names its rows by count
    assert note.endswith("so these 50 rows are left out of every count and total.")
    said = {q.id: interview._answer(q, ["real"], "")}
    b.apply_answers(said)
    note = next(r["statement"] for r in Composer(b, b.paths[0], "b1", said).compose() if r["id"] == f"f:{q.id}")
    assert "left out" not in note


def test_a_sentence_opening_with_both_stays_with_the_one_before():
    q = _q([{"id": "type", "label": "I'll type it", "desc": ""}], prompt="Refund changes sign in May. What happened?")
    notes = _answer_notes(interview._answer(q, [], "Refunds switched sign in May. Both are still refunds."), q.fact,
                          None)
    assert len(notes) == 1 and "Both are still refunds." in notes[0][0]
    apart = _answer_notes(interview._answer(q, [], "Refunds switched sign in May. Both might still be refunds."),
                          q.fact, None)
    assert len(apart) == 2                                          # a doubt stays a note of its own
    # changed on purpose (practice round 4, fix 11): up to three sentences share the question's note, so the
    # grouping is checked where notes are grouped
    assert brain.note_groups([("Refunds switched sign.", 0), ("Both A1 and A2 are ours.", 1)]) == \
        ["Refunds switched sign.", "Both A1 and A2 are ours."]      # 'Both A1 and A2' names its own subject


# --------------------------------------------------------------------------
# fix 9: a batch of small findings, and amounts the file already holds
# --------------------------------------------------------------------------
def test_each_pick_of_a_batch_is_a_note_about_its_own_finding(tmp_path):
    b = _buys(tmp_path)
    t = b.main_table
    about = {"known_1": {"table": t.tid, "col": "Amount", "aspect": "history", "values": ["IT301"]},
             "known_2": {"table": t.tid, "col": "Site", "aspect": "history", "values": ["Mesa"]},
             "known_3": {"table": t.tid, "col": "Item #", "aspect": "history", "values": ["IT302"]}}
    q = interview.Q("find_known_ab12", "Known issues?", "I found 3 smaller things: 4 IT301 lines above the list; Mesa "
                    "lines outside their dates; 2 IT302 rows where the line total is off. Which of these do you already "
                    "know about?",
                    [{"id": "known_1", "label": "4 IT301 lines above the list", "desc": "4 IT301 lines above the list"},
                     {"id": "known_2", "label": "Mesa lines outside their dates", "desc": "Mesa lines outside dates"},
                     {"id": "known_3", "label": "2 IT302 rows off", "desc": "2 IT302 rows where the line total is off"}],
                    multi=True, kind="history", fact={"kind": "history"},
                    meta={"about": about["known_1"], "members": ["m1", "m2", "m3"], "member_about": about})
    ans = interview._answer(q, ["known_1", "known_3"], "The Mesa dates are a known gap in the export.")
    ans["member_about"] = about
    recs = [r for r in Composer(b, b.paths[0], "b1", {q.id: ans}).compose() if r.get("ref") == f"q:{q.id}"
            and r["record"] == "fact"]
    assert len(recs) == 3
    assert [r["_about"]["col"] for r in recs] == ["Amount", "Item #", "Site"]
    assert '"The Mesa dates are a known gap in the export."' in recs[2]["statement"]


def test_a_sentence_whose_amounts_are_in_the_file_stays_in_the_file(tmp_path):
    b = _buys(tmp_path)
    held = privacy.workbook_amounts(b)
    both = "The vendor billed IT301 at $14.30 against the $13.10 contract price; no claim filed."
    assert privacy.is_commercial(both) and not privacy.is_commercial(both, held)
    one = "The vendor billed IT301 at $14.30 against the $10.15 contract price; no claim filed."
    assert privacy.is_commercial(one, held)                          # one amount the file does not show
    assert privacy.is_commercial("We pay cost plus 5% on every line.", held)      # a percent is a term
    kept, private, commercial = privacy.screen(both, set(), set(), held)
    assert kept == both and not commercial


# --------------------------------------------------------------------------
# fix 12: a group counted in another unit is never summed with the others
# --------------------------------------------------------------------------
def test_a_group_in_another_unit_is_never_summed_with_the_others(tmp_path):
    a = _register(tmp_path)
    t = a.main_table
    ug = {"table": t.tid, "col": "Hours", "group_col": "Position", "values": ["PIECE"], "option": "other"}
    said = {"find_unit_job": {"options": ["other"], "labels": ["Another unit (type which)"], "text": "pieces",
                              "not_sure": False, "unit_group": ug}}
    assert [r.kind for r in rules.unit_groups(a, said)] == ["unit"]
    a.apply_answers(said)
    recs = Composer(a, a.paths[0], "b1", said).compose()
    hours = next(r["statement"] for r in recs if r["id"].endswith(".{Hours}"))
    assert "not summed: the rows where Position is PIECE are in another unit, per the owner" in hours
    assert "summing to" not in hours
    assert rules.proposals(a, said) == []                            # a unit note is never offered as a tick
    # twin: 'Same unit as the rest' leaves the column summed
    said["find_unit_job"]["options"] = ["same"]
    a.apply_answers(said)
    hours = next(r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose()
                 if r["id"].endswith(".{Hours}"))
    assert "summing to" in hours


# --------------------------------------------------------------------------
# fix 24: a column that changed unit is never summed
# --------------------------------------------------------------------------
def test_a_column_that_changed_unit_is_never_summed_in_rules_or_the_readout(tmp_path):
    path = str(tmp_path / "shop.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Sales")
    ws.write_row(0, 0, ["Place", "Channel", "Markdown"])
    for i in range(80):
        ws.write_row(i + 1, 0, [["North", "South", "East", "West"][i % 4], ["Web", "RX"][i % 2],
                                [10, 15, 20][i % 3] if i < 40 else round(3.5 + i % 7, 2)])
    wb.close()
    a = Analysis([path], playbooks=_pb(place=("Place", "text", "entity", None, None),
                                       amount=("Markdown", "number", "metric", "currency", True)))
    t = a.main_table
    a.insights.append({"recipe": f"boundary:{t.tid}:2026-06-09", "statement": "Around Jun 9, Markdown changes form.",
                       "numbers": {"table": t.tid, "when": "Jun 9, 2026", "date": "2026-06-09",
                                   "changes": [{"col": "Markdown", "kind": "number"}]}})
    text = say.readout(a, 0.1, asked=[])
    assert "Markdown is not summed: its numbers change form at Jun 9, 2026" in text
    assert not re.search(r"\$[\d,.]+k? in Markdown", text)
    said = _tick([{"rule": Rule("exclude", t.tid, [{"col": "Channel", "op": "in", "values": ["RX"]}])}])
    a.apply_answers(said)
    a.mixed_units[(t.tid, "Markdown")] = "Jun 9, 2026"
    recs = Composer(a, path, "b1", said).compose()
    applied = [r["statement"] for r in recs if r.get("ref") == "rule:applied"]
    assert applied and not any("of Markdown" in s or "counted total" in s for s in applied)
    line = say.applied_line(a, rules.confirmed(a, said)[0])
    assert "of Markdown" not in line


# --------------------------------------------------------------------------
# fix 25: open items and later answers stay in step
# --------------------------------------------------------------------------
def test_a_closing_sentence_naming_an_open_items_values_closes_it(tmp_path, monkeypatch):
    b = _buys(tmp_path)
    t = b.main_table
    opens = [interview.Q("find_odd_site_mesa", "Site Mesa", "What is Mesa?",
                         [{"id": "ours", "label": "Ours, count it"}], kind="definition",
                         meta={"about": {"table": t.tid, "col": "Site", "aspect": "meaning", "values": ["Mesa"]}}),
             interview.Q("find_known_x", "Off list", "4 IT301 lines above the list. Known?",
                         [{"id": "known", "label": "A known issue"}], kind="history",
                         meta={"about": {"table": t.tid, "col": "Item #", "aspect": "history", "values": ["IT301"]}}),
             interview.Q("find_unmatched_coral", "Supplier", "Coral Growers is not on the list. What is it?",
                         [{"id": "ours", "label": "On purpose"}], kind="definition",
                         meta={"about": {"table": t.tid, "col": "Site", "aspect": "meaning",
                                         "values": ["Coral Growers"]}}),
             interview.Q("find_unmatched_tern", "Supplier", "Tern Bay Foods is not on the list. What is it?",
                         [{"id": "ours", "label": "On purpose"}], kind="definition",
                         meta={"about": {"table": t.tid, "col": "Site", "aspect": "meaning",
                                         "values": ["Tern Bay Foods"]}}),
             interview.Q("row_grain", "Row", "Is each row one line or one order?",
                         [{"id": "line", "label": "One line"}], kind="grain",
                         meta={"about": {"table": t.tid, "col": "", "aspect": "grain"}}),
             interview.Q("leave_out", "Leave out", "Which of these should stay out of totals?",
                         [{"id": "voids", "label": "Voided lines"}, {"id": "test", "label": "Test rows"}],
                         kind="exclusion", meta={"about": {"table": t.tid, "col": "Site", "aspect": "treatment"}})]
    monkeypatch.setattr(interview, "open_items", lambda analysis, state: list(opens))
    b.keys[t.tid] = ["Site", "Item #"]
    said = {interview.CLOSER: {"options": ["type"], "labels": ["I'll type it"], "not_sure": False,
                               "text": "Mesa is our newest site and counts as a store. The vendor billed IT301 at "
                                       "$19.40 against the $17.00 contract price; no claim filed. Coral stopped "
                                       "delivering in May. Tern is a bird we see from the dock."}}
    recs = Composer(b, b.paths[0], "b1", said).compose()
    ids = {r["id"] for r in recs}
    assert "o:find_odd_site_mesa" not in ids                        # answered in the closing note, which stays
    assert "o:find_unmatched_coral" not in ids                      # a name said by its first word
    assert "o:find_unmatched_tern" in ids                           # a first word under five letters is not enough
    assert "o:row_grain" not in ids                                  # code counted a key
    answered = next(r for r in recs if r["id"] == "f:answered:find_known_x")
    assert "kept on this machine" in answered["statement"] and answered.get("_travel", "file") == "file"
    leave = next(r["statement"] for r in recs if r["id"] == "o:leave_out")
    assert "(Voided lines; Test rows)" in leave                      # 'these' named, so it stands alone


def test_an_unticked_readback_rule_is_never_the_owners_note(tmp_path):
    b = _buys(tmp_path)
    said = _typed("Leave WHX out of every total.", b, col="Site")
    rb = findings.readback(b, said)
    said[rb.id] = interview.parse_answers([rb], "No. WHX is a real site.")[rb.id]
    b.apply_answers(said)
    assert rules.unapplied(b, said) == [] and len(rules.declined(b, said)) == 1
    recs = Composer(b, b.paths[0], "b1", said).compose()
    stmts = [r["statement"] for r in recs if r.get("ref") in ("rule:not_applied", "rule:declined")]
    assert len(stmts) == 1 and "a proposed rule on Site WHX that the owner did not tick" in stmts[0]
    assert not any("the owner's note on Site WHX" in r["statement"] for r in recs)
    text = say.save_preview(recs, [], name="buys.xlsx", kind="xlsx", tab_state="visible")
    assert "Rules I proposed that you did not tick" in text and "Rules you wrote that are not applied" not in text
    # twin: Not sure on the readback keeps it the owner's unapplied note
    said[rb.id] = interview.parse_answers([rb], "not sure")[rb.id]
    b.apply_answers(said)
    assert len(rules.unapplied(b, said)) == 1 and rules.declined(b, said) == []


def test_the_coverage_line_is_counted_and_never_speaks_for_the_owner(tmp_path):
    # changed on purpose (practice round 4c): the coverage line is the interview's bookkeeping; as a told
    # note it claimed the owner explained every column a question named, which the gate caught
    b = _buys(tmp_path)
    t = b.main_table
    said = {"what_site": {"options": [], "labels": [], "text": "Site is the store.", "not_sure": False,
                          "about": {"table": t.tid, "col": "Site", "aspect": "meaning"}, "prompt": "What is Site?"},
            "what_cat": {"options": ["x"], "labels": ["Treatment"], "text": "", "not_sure": False,
                         "about": {"table": t.tid, "col": "Category", "aspect": "treatment", "asked": "meaning"},
                         "prompt": "What is Category?"}}
    recs = {r["id"]: r for r in Composer(b, b.paths[0], "b1", said).compose()}
    assert recs["f:coverage"]["source"] == "computed" and "Not said yet" not in recs["f:coverage"]["statement"]
    assert "The owner said what" not in recs["f:coverage"]["statement"]
    assert recs["f:coverage:open"]["source"] == "computed" and "Category" in recs["f:coverage:open"]["statement"]


# --------------------------------------------------------------------------
# fix 26: the save question keeps the option contract
# --------------------------------------------------------------------------
def test_the_save_question_offers_not_sure_in_its_structured_options():
    q = say.save_question()
    opts = interview.render_ask([q])["questions"][0]["options"]
    assert len(opts) == 3 and opts[-1]["label"] == "Not sure"        # a tab, this machine only, Not sure
    assert "Not sure is fine" not in interview.render_ask([q])["questions"][0]["question"]


def _sb(env, *args):
    p = subprocess.run([sys.executable, SB, *args], capture_output=True, text=True, env=env, timeout=180)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def test_not_sure_on_the_save_question_keeps_the_brain_here(tmp_path):
    b = _buys(tmp_path)
    book = b.paths[0]
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / "home"))
    assert _sb(env, "start", book, "--no-questions")["ok"]
    r = _sb(env, "preview", book)
    assert [o["label"] for o in r["ask"]["questions"][0]["options"]][-1] == "Not sure"
    r = _sb(env, "answer", book, "--text", "d")
    assert r["ok"] and r["say"].startswith("Kept on this machine only for now")
    r = _sb(env, "preview", book)
    r = _sb(env, "answer", book, "--text", "show every line")
    assert r["next"] == "ask" and " | " in r["say"]


# --------------------------------------------------------------------------
# fix 30: wording and provenance
# --------------------------------------------------------------------------
def test_a_rules_rows_read_plainly():
    r = Rule("exclude", "t", [{"col": "Week", "op": "between", "values": ["2026-08-16", "2026-08-16"]},
                              {"col": "Order ID", "op": "in", "values": ["#101", "#101", "#101", "#116"]}])
    assert rules.where(r) == "Week is on 2026-08-16 and Order ID is #101 (3 times) or #116"
    assert rules.where(Rule("exclude", "t", [{"col": "Week", "op": "between", "values": ["2026-01-01", "2026-02-01"]}])) \
        == "Week is from 2026-01-01 to 2026-02-01"


def test_the_readout_cuts_at_a_word_and_says_each_finding_once():
    s = "Refund: positive before May, negative after, on the same accounts " * 4
    cut = say._short(s, 60)
    body = cut[:-3]
    assert cut.endswith("...") and len(cut) <= 60 and s.startswith(body)
    assert s[len(body)] in " ,"                                      # the cut falls between two words
    got = say._one_each([{"recipe": "signflip:gl:refund", "statement": "Refund: positive before, negative after."},
                         {"recipe": "boundary:gl:2026", "statement": "Refund: positive before, negative after (May)."},
                         {"recipe": "top_share:x", "statement": "North is 40% of Amount."}])
    assert [g["recipe"] for g in got] == ["signflip:gl:refund", "top_share:x"]


# --------------------------------------------------------------------------
# fix 11: a 'not real items' answer is never wider than its scope follow-up
# --------------------------------------------------------------------------
def test_not_real_items_stay_in_price_comparisons_unless_the_owner_ticked_them(tmp_path):
    b = _buys(tmp_path)
    t = b.main_table
    first = {"options": ["not_items"], "labels": ["Not real items"], "text": "", "not_sure": False,
             "keys": ["SVC-A", "SVC-B"], "about": {"table": t.tid, "col": "Item #", "aspect": "meaning"}}
    assert rules.not_items(b, {"find_unmatched_item": first}) == {"SVC-A", "SVC-B"}
    only_totals = {"options": ["totals"], "labels": ["Amount totals"], "text": "", "not_sure": False}
    assert rules.not_items(b, {"find_unmatched_item": first, "follow_items_item": only_totals}) == set()
    prices = dict(only_totals, options=["totals", "prices"])
    assert rules.not_items(b, {"find_unmatched_item": first, "follow_items_item": prices}) == {"SVC-A", "SVC-B"}
    unsure = {"options": [], "labels": [], "text": "", "not_sure": True}
    assert rules.not_items(b, {"find_unmatched_item": first, "follow_items_item": unsure}) == {"SVC-A", "SVC-B"}


def test_every_total_is_never_a_total_column_on_another_tab(tmp_path):
    path = str(tmp_path / "two.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Lines")
    ws.write_row(0, 0, ["Site", "Amount"])
    for i in range(60):
        ws.write_row(i + 1, 0, [["North", "South", "WHX"][i % 3], 10.5 + i])
    ws = wb.add_worksheet("Roll Up")
    ws.write_row(0, 0, ["Region", "Total"])
    for i in range(12):
        ws.write_row(i + 1, 0, [f"R{i}", 100.25 + i])
    wb.close()
    a = Analysis([path])
    cands = rules.candidates(a, _typed("Leave WHX out of every total.", a, col="Site"))
    assert [(c["rule"].scope, c.get("scope_words")) for c in cands] == [([], "")]
    named = rules.candidates(a, _typed("Leave WHX out of Total.", a, col="Site"))
    assert [c.get("scope_words") for c in named] == ["Total"]          # the column, written as it is, on its tab


def test_each_pick_names_the_total_it_named_even_when_two_picks_make_one_rule(tmp_path):
    """Practice round 3b: two picks on one value, one leaving its rows out of a worked-out
    measure's totals ('Net') and one out of a column's ('Discount'), were merged into one rule;
    the second pick's note then said 'left out of Net only'. Each pick says its own total, and a
    measure's name reads as a total, never as 'only'."""
    assert brain._names_measure("Net") and brain._names_measure("Gross Margin")
    assert not brain._names_measure("rebate math") and not brain._names_measure("price comparisons")


def test_a_pick_among_several_never_reads_as_the_whole_answer():
    """Practice round 3b: 'Not real items' picked with 'Replaced codes' for 574 unmatched rows was
    written as its own note, which read as all 574 rows being not real items."""
    q = _q([{"id": "notitems", "label": "Not real items", "desc": ""},
            {"id": "old", "label": "Replaced codes", "desc": ""}])
    notes = [s for s, _ in _answer_notes(interview._answer(q, ["notitems", "old"], ""), q.fact, None)]
    assert '"Not real items" and "Replaced codes"' in notes[0]
    assert all("one of the owner's picks was" in n for n in notes[1:])
