"""Clean rows before any detector runs (v0.2 rank 5), role binding that checks the
values and not only the header (rank 12), and the seeded generator every
detector test builds on (rank 4). Synthetic workbooks only."""
import datetime as dt
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "sheet-geek", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
import synth  # noqa: E402
from sheetbrain import analyze, detect, profile, tables  # noqa: E402
from sheetbrain.brain import _column_statement  # noqa: E402

xlsxwriter = pytest.importorskip("xlsxwriter")
PLAYBOOKS = detect.load_playbooks()


def _book(path, sheets: dict):
    """Write {sheet: rows} with xlsxwriter; datetimes get a date format."""
    wb = xlsxwriter.Workbook(str(path), {"strings_to_numbers": False, "strings_to_formulas": False})
    day = wb.add_format({"num_format": "yyyy-mm-dd"})
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if v is None:
                    continue
                if isinstance(v, dt.datetime):
                    ws.write_datetime(r, c, v, day)
                elif isinstance(v, str) and v.startswith("="):       # '=B2-B3|400': formula and its value
                    formula, value = v.split("|")
                    ws.write_formula(r, c, formula, None, float(value))
                elif isinstance(v, str):
                    ws.write_string(r, c, v)
                else:
                    ws.write_number(r, c, v)
    wb.close()
    return str(path)


def _only(a, sheet):
    ts = [t for t in a.tables if t.sheet == sheet]
    assert len(ts) == 1, [t.tid for t in ts]
    return ts[0]


def _col(a, t, header):
    return next(c for c in a.cols[t.tid] if c.header == header)


def _structure(a):
    return [i for i in a.insights if i["recipe"].startswith("structure:")]


# --------------------------------------------------------------------------
# rank 4: the generator
# --------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [0, 1, 7])
def test_same_seed_gives_the_same_workbook_and_manifest(tmp_path, seed):
    for name in synth.TRAPS:
        for twin in (False, True):
            m1 = synth.build(tmp_path / f"a_{name}_{twin}.xlsx", seed, name, twin)
            m2 = synth.build(tmp_path / f"b_{name}_{twin}.xlsx", seed, name, twin)
            assert synth.cells(m1["path"]) == synth.cells(m2["path"]), name
            assert {**m1, "path": ""} == {**m2, "path": ""}, name


def test_seeds_differ_and_writers_alternate(tmp_path):
    m0 = synth.build(tmp_path / "s0.xlsx", 0, "code_minority")
    m1 = synth.build(tmp_path / "s1.xlsx", 1, "code_minority")
    m2 = synth.build(tmp_path / "s2.xlsx", 2, "code_minority")
    assert (m0["writer"], m1["writer"], m2["writer"]) == ("xlsxwriter", "openpyxl", "xlsxwriter")
    assert synth.cells(m0["path"]) != synth.cells(m2["path"])


def test_every_trap_names_its_plant_and_every_twin_expects_silence(tmp_path):
    for name, (_, kind) in synth.TRAPS.items():
        m = synth.build(tmp_path / f"{name}.xlsx", 3, name)
        tw = synth.build(tmp_path / f"{name}_twin.xlsx", 3, name, twin=True)
        sheets = dict(synth.cells(m["path"]))
        for p in m["plants"]:
            assert p["expect"] and p["rows"] and p["sheet"] in sheets, (name, p)
            assert all(1 <= r <= len(sheets[p["sheet"]]) for r in p["rows"]), name
        assert all(p["expect"] is None and p["twin"] for p in tw["plants"]), name
        assert kind


def test_manifest_rows_point_at_the_plant(tmp_path):
    m = synth.build(tmp_path / "codes.xlsx", 5, "code_minority")
    p = m["plants"][0]
    rows = dict(synth.cells(m["path"]))[p["sheet"]]
    j = rows[0].index(p["col"])
    assert {rows[r - 1][j] for r in p["rows"]} == {p["value"]}
    m = synth.build(tmp_path / "pairs.xlsx", 5, "void_pairs")
    p = m["plants"][0]
    assert len(p["rows"]) == 2 * p["pairs"]


def test_a_combined_book_keeps_one_tab_per_trap(tmp_path):
    m = synth.build_book(tmp_path / "all.xlsx", 4, ["reimport", ("odd_group", True), "journal_three"])
    assert len({p["sheet"] for p in m["plants"]}) == 3
    assert sorted(p["expect"] for p in m["plants"] if p["trap"] == "journal_three") == ["contra", "opening", "ratio"]


def test_noise_fixtures_exist(noise_fixtures):
    # finance_model.xlsx is a copy of a development workbook, so it is not in the noise budget
    assert len(noise_fixtures) == 3
    assert all(os.path.exists(p) for paths in noise_fixtures for p in paths)
    assert not [p for paths in noise_fixtures for p in paths if os.path.basename(p) == "finance_model.xlsx"]


# --------------------------------------------------------------------------
# rank 5: clean rows
# --------------------------------------------------------------------------
def test_stacked_export_is_read_as_one_clean_table(tmp_path, synth_seed):
    m = synth.build(tmp_path / "stack.xlsx", synth_seed, "stacked_export")
    p = m["plants"][0]
    a = analyze.Analysis([m["path"]])
    t = _only(a, p["sheet"])                          # the title band is not a table
    assert p["title"] in t.title
    assert t.n_rows == p["data_rows"]
    gone = {r - 1 for r in p["header_rows"][1:] + p["total_rows"]}
    assert not gone & set(t.row_index)
    assert [r + 1 for r in t.totals_rows] == p["total_rows"]
    assert len(t.segments) == 2 and t.segments[1]["header_row"] + 1 == p["header_rows"][1]
    assert t.segments[1]["headers"] == [b for _, b in p["renamed"]]
    d = _col(a, t, p["date_col"])
    assert d.type == "date" and d.distinct == p["distinct_dates"] and d.retyped == p["text_dates"]
    price = _col(a, t, p["price_col"])
    assert price.type == "number" and price.sum == pytest.approx(p["price_sum"], abs=0.01)
    spell = next(i for i in _structure(a) if i["recipe"].startswith("structure:spellings:")
                 and i["numbers"]["col"] == p["code_col"])
    assert spell["numbers"]["cells"] == 3
    facts = " ".join(i["statement"] for i in _structure(a))
    assert "2 total rows" in facts and f"row {p['header_rows'][1]}" in facts


def test_null_twin_keeps_its_rows_and_says_nothing(tmp_path, synth_seed):
    m = synth.build(tmp_path / "clean.xlsx", synth_seed, "stacked_export", twin=True)
    p = m["plants"][0]
    a = analyze.Analysis([m["path"]])
    t = _only(a, p["sheet"])
    assert p["row_equal_to_sum"] - 1 in t.row_index        # it carries an ID and a date
    assert p["placeholder_row"] - 1 in t.row_index         # 'TBD' and 'N/A' are data, not a header
    assert not t.totals_rows and not t.segments
    facts = _structure(a)                                   # only the placeholder cells, stated as counted
    assert all(i["recipe"].startswith("structure:mixed_types:") and i["numbers"]["cells"] == 1 for i in facts)
    assert p["placeholder"] in {i["numbers"]["example"] for i in facts}


def test_a_row_that_sums_its_block_is_a_total_whatever_its_label(tmp_path):
    rng = random.Random(11)
    rows = [["Ref", "Date", "Item", "Qty", "Amount"]]
    for i in range(6):
        rows.append([f"K{100 + i}", dt.datetime(2025, 3, 1 + i), f"Thing {i}", rng.randint(1, 9),
                     round(rng.uniform(5, 50), 2)])
    rows.append([None, None, "Checked", sum(r[3] for r in rows[1:]), round(sum(r[4] for r in rows[1:]), 2)])
    rows += [["K200", dt.datetime(2025, 4, 1), "Late", 2, 10.0], ["K201", dt.datetime(2025, 4, 2), "Later", 3, 5.0],
             [None, None, "Checked", 5, 15.0],           # only 2 rows since the last break: stays
             ["K202", dt.datetime(2025, 4, 3), "Same as the sum", 10, 30.0]]   # has an ID: stays
    a = analyze.Analysis([_book(tmp_path / "t.xlsx", {"Log": rows})])
    t = _only(a, "Log")
    assert t.totals_rows == [7]
    assert t.n_rows == 10 and 9 in t.row_index and 10 in t.row_index
    assert tables.is_total_label("*** RUN TOTAL ***") and not tables.is_total_label("Checked")


def test_a_repeated_header_ends_one_segment(tmp_path):
    head = ["Ref", "Date", "Site", "Qty", "Amount"]
    rows = [head] + [[f"A{i}", dt.datetime(2025, 1, 1 + i), "North", i + 1, 10.0 + i] for i in range(8)]
    rows += [list(head)] + [[f"B{i}", dt.datetime(2025, 2, 1 + i), "South", i + 1, 20.0 + i] for i in range(8)]
    a = analyze.Analysis([_book(tmp_path / "s.xlsx", {"Export": rows})])
    t = _only(a, "Export")
    assert t.n_rows == 16 and 9 not in t.row_index
    assert [s["header_row"] for s in t.segments] == [0, 9] and t.segments[1]["start"] == 10
    assert _col(a, t, "Site").distinct == 2                 # the header text is not a value


def test_type_minorities_and_spellings_are_stated_with_counts(tmp_path):
    rows = [["Unit", "Kind", "Invoice #", "Amount"]]
    for i in range(40):
        unit = 100 + i if i % 13 == 0 else f"{i}B"
        kind = ["Gold", "Blue", "Red"][i % 3]
        rows.append([unit, kind, f"{i:06d}", 12.5 + i])
    rows[5][1], rows[8][1] = "gold ", "GOLD"
    a = analyze.Analysis([_book(tmp_path / "m.xlsx", {"Units": rows})])
    facts = {i["recipe"]: i for i in _structure(a)}
    mixed = facts["structure:mixed_types:Units:Unit:number"]
    assert mixed["numbers"]["cells"] == 4 and "4 numbers" in mixed["statement"]
    spell = facts["structure:spellings:Units:Kind"]
    assert spell["numbers"]["cells"] == 2 and "'gold '" in spell["statement"]
    assert not [i for i in a.insights if i["recipe"] == "structure:numbers_as_text"]   # an ID column


def test_currency_text_counts_in_a_number_column(tmp_path):
    rows = [["Date", "Item", "Price"]] + [[dt.datetime(2025, 5, 1 + i), f"I{i}", 10.0 + i] for i in range(20)]
    rows[3][2], rows[9][2] = "$1,179.00", "(4.50)"
    a = analyze.Analysis([_book(tmp_path / "c.xlsx", {"Prices": rows})])
    t = _only(a, "Prices")
    c = _col(a, t, "Price")
    real = sum(10.0 + i for i in range(20) if i not in (2, 8))
    assert c.type == "number" and c.sum == pytest.approx(real + 1179.0 - 4.5)
    assert c.retyped == 2 and c.negatives == 1


def test_one_column_tables_get_no_key(tmp_path):
    rows = [["Code"]] + [[f"C{i}"] for i in range(10)]
    a = analyze.Analysis([_book(tmp_path / "k.xlsx", {"List": rows})])
    t = _only(a, "List")
    assert profile.composite_key(t, a.cols[t.tid]) == []


# --------------------------------------------------------------------------
# rank 12: role binding checks the values
# --------------------------------------------------------------------------
def test_record_key_needs_values_that_are_one_per_row(tmp_path):
    rng = random.Random(3)
    codes = [f"{chr(65 + k)}{chr(70 + k)}Q" for k in range(12)]
    rows = [["Code", "Ref", "Date", "Amount"]]
    for i in range(120):
        rows.append([rng.choice(codes), f"R{5000 + i}", dt.datetime(2025, 1, 1) + dt.timedelta(days=i % 90),
                     round(rng.uniform(10, 300), 2)])
    a = analyze.Analysis([_book(tmp_path / "r.xlsx", {"Rows": rows})])
    m = detect.match_roles(PLAYBOOKS["generic"], a.cols[a.tables[0].tid])
    assert m["record_key"]["header"] == "Ref"


def test_an_entity_role_needs_the_whole_header(tmp_path):
    rng = random.Random(4)
    rows = [["Order #", "Order Date", "Customer Region", "Customer Name", "Product", "Qty", "Price"]]
    for i in range(80):
        rows.append([f"S{900 + i}", dt.datetime(2025, 2, 1) + dt.timedelta(days=i % 40),
                     rng.choice(["East", "West", "North"]), f"Buyer {i % 25}", f"Thing {i % 9}",
                     rng.randint(1, 6), round(rng.uniform(3, 40), 2)])
    a = analyze.Analysis([_book(tmp_path / "o.xlsx", {"Orders": rows})])
    m = detect.match_roles(PLAYBOOKS["sales_transactions"], a.cols[a.tables[0].tid])
    assert m["customer"]["header"] == "Customer Name"
    del rows[0][3]
    for r in rows[1:]:
        del r[3]
    a = analyze.Analysis([_book(tmp_path / "o2.xlsx", {"Orders": rows})])
    m = detect.match_roles(PLAYBOOKS["sales_transactions"], a.cols[a.tables[0].tid])
    assert "customer" not in m or m["customer"]["header"] != "Customer Region"


def test_integer_codes_are_ids_and_quantities_stay_metrics(tmp_path):
    rng = random.Random(5)
    depts = rng.sample(range(3000, 9000), 7)
    rows = [["Date", "Dept", "Qty", "Amount"]]
    for i in range(140):
        rows.append([dt.datetime(2025, 1, 1) + dt.timedelta(days=i % 60), depts[i % 7], rng.randint(1, 6),
                     round(rng.uniform(10, 90), 2)])
    a = analyze.Analysis([_book(tmp_path / "d.xlsx", {"Log": rows})])
    t = a.tables[0]
    dept, qty = _col(a, t, "Dept"), _col(a, t, "Qty")
    assert dept.codes and dept.semantic == "identifier"
    assert qty.semantic == "metric" and not qty.codes
    role_of = {(r["table"], r["header"]): rid for rid, r in a.detection["roles"].items()}
    rid = role_of.get((t.tid, "Dept"))
    stmt, _ = _column_statement(dept, t, a.playbook.get("roles", {}).get(rid, {}) if rid else {})
    assert "is a code" in stmt and "summing" not in stmt


def test_charge_and_payment_never_on_one_row_are_a_debit_credit_pair(tmp_path):
    rng = random.Random(6)
    rows = [["Date", "Account", "Memo", "Charge", "Payment"]]
    for i in range(60):
        amt = round(rng.uniform(20, 800), 2)
        charge = i % 3 != 0
        rows.append([dt.datetime(2025, 3, 1) + dt.timedelta(days=i), rng.choice(["Rent", "Water", "Fees", "Cash"]),
                     f"line {i}", amt if charge else None, None if charge else amt])
    a = analyze.Analysis([_book(tmp_path / "p.xlsx", {"Ledger": rows})])
    t = a.tables[0]
    cols = a.cols[t.tid]
    assert a.detection["pairs"][t.tid] == ["Charge", "Payment"]
    pb = PLAYBOOKS["ledger"]
    plain = detect.match_roles(pb, cols)
    assert detect.score(pb, plain) == 0                  # the headers alone miss the money group
    m = detect._infer_required(pb, plain, detect.debit_credit_pair(t, cols))
    assert m["debit"]["header"] == "Charge" and m["credit"]["header"] == "Payment" and m["debit"]["inferred"]
    assert detect.score(pb, m) > 0


def test_columns_filled_together_are_not_a_pair(tmp_path):
    rows = [["Date", "Qty", "Price", "Tip", "Refund"]]
    for i in range(40):
        rows.append([dt.datetime(2025, 3, 1) + dt.timedelta(days=i), i % 5 + 1, 3.5 + i,
                     2.0 if i % 10 == 0 else None, 5.0 if i % 10 == 5 else None])
    a = analyze.Analysis([_book(tmp_path / "n.xlsx", {"Sales": rows})])
    t = a.tables[0]
    assert detect.debit_credit_pair(t, a.cols[t.tid]) is None


def test_a_percent_header_is_never_dollars(tmp_path):
    rng = random.Random(8)
    rows = [["Item #", "Description", "Vendor", "Qty", "Unit Price", "Ext Price", "Rebate %"]]
    for i in range(60):
        q, p = rng.randint(1, 9), round(rng.uniform(5, 80), 2)
        rows.append([f"IT{i % 20}", f"Thing {i % 20}", rng.choice(["Alpha Supply", "Beta Supply"]), q, p,
                     round(q * p, 2), rng.choice([0, 1.5, 2])])
    a = analyze.Analysis([_book(tmp_path / "rb.xlsx", {"Buys": rows})])
    t = a.tables[0]
    assert "rebate" not in detect.match_roles(PLAYBOOKS["procurement"], a.cols[t.tid])
    role_of = {(r["table"], r["header"]): rid for rid, r in a.detection["roles"].items()}
    rid = role_of.get((t.tid, "Rebate %"))
    stmt, _ = _column_statement(_col(a, t, "Rebate %"), t, a.playbook.get("roles", {}).get(rid, {}) if rid else {})
    assert "$" not in stmt and "summing" not in stmt
    rows[0].append("Rebate Per Case")                    # the lexicon's own 'per' still binds as money
    for r in rows[1:]:
        r.append(round(rng.uniform(0.1, 2), 2))
    a = analyze.Analysis([_book(tmp_path / "rb2.xlsx", {"Buys": rows})])
    m = detect.match_roles(PLAYBOOKS["procurement"], a.cols[a.tables[0].tid])
    assert m["rebate"]["header"] == "Rebate Per Case"


def test_model_money_roles_bind_to_the_calculated_row(tmp_path):
    cols = "BCDEFGHIJKLM"
    months = [f"{m} 2026" for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                    "Dec"]]
    rev, cost = [1000 + 50 * k for k in range(12)], [600 + 20 * k for k in range(12)]
    opex = [[100 + k for k in range(12)], [80] * 12, [40 + k for k in range(12)]]
    gross = [rev[k] - cost[k] for k in range(12)]
    tot = [sum(o[k] for o in opex) for k in range(12)]
    net = [gross[k] - tot[k] for k in range(12)]
    pl = [["Line"] + months, ["Revenue"] + rev, ["COGS"] + cost,
          ["Gross profit"] + [f"={c}2-{c}3|{gross[k]}" for k, c in enumerate(cols)],
          ["Payroll"] + opex[0], ["Rent"] + opex[1], ["Software"] + opex[2],
          ["Total opex"] + [f"=SUM({c}5:{c}7)|{tot[k]}" for k, c in enumerate(cols)],
          ["Net income"] + [f"={c}4-{c}8|{net[k]}" for k, c in enumerate(cols)]]
    inputs = [["Input", "Value"], ["Opening cash", 5000], ["Growth", 0.05]]
    bal, run = [], 5000
    for k, c in enumerate(cols):
        run += net[k]
        prev = "Inputs!B2" if k == 0 else f"{cols[k - 1]}3"
        bal.append(f"={prev}+{c}2|{run}")
    cash = [["Line"] + months, ["Net change"] + [f"='P&L'!{c}9|{net[k]}" for k, c in enumerate(cols)],
            ["Cash balance"] + bal]
    a = analyze.Analysis([_book(tmp_path / "model.xlsx", {"P&L": pl, "Inputs": inputs, "Cash": cash})])
    # the input list is read first, so its 'Opening cash' would win without the check
    roles = {"cash": {"table": "Inputs", "header": "Opening cash", "col": None, "row_label": True}}
    detect._prefer_calculated(a, PLAYBOOKS["financial_model"], roles)
    assert roles["cash"]["header"] == "Cash balance" and roles["cash"]["table"] == "Cash"
    assert a.detection["archetype"] == "financial_model"
    assert a.detection["roles"]["cash"]["header"] == "Cash balance"


def test_a_ratio_row_never_takes_a_money_role(tmp_path):
    cols = "BCDEFGHIJKLM"
    months = [f"{m} 2026" for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                    "Dec"]]
    sales, costs = [2000 + 75 * k for k in range(12)], [1200 + 30 * k for k in range(12)]
    profit = [round((sales[k] - costs[k]) * 0.8, 2) for k in range(12)]
    pl = [["Line"] + months, ["Sales"] + sales, ["Costs"] + costs,
          ["Profit after tax"] + [f"=({c}2-{c}3)*0.8|{profit[k]}" for k, c in enumerate(cols)],
          ["Profit margin %"] + [f"={c}4/{c}2|{round(profit[k] / sales[k], 4)}" for k, c in enumerate(cols)]]
    inputs = [["Input", "Value"], ["Target profit", 900], ["Tax", 0.2]]
    a = analyze.Analysis([_book(tmp_path / "pm.xlsx", {"P&L": pl, "Inputs": inputs})])
    roles = {"profit": {"table": "Inputs", "header": "Target profit", "col": None, "row_label": True}}
    detect._prefer_calculated(a, PLAYBOOKS["financial_model"], roles)
    assert roles["profit"]["header"] == "Profit after tax"


# --------------------------------------------------------------------------
# fixes after review: data rows kept, codes, names, rates, sides, dates, totals
# --------------------------------------------------------------------------
def _orders(extra: list) -> list:
    rows = [["Order ID", "Order Date", "Customer", "Amount", "Status"]]
    names = ["Acme Supply", "Birch Goods", "Cobalt Trading"]
    for i in range(30):
        rows.append([f"SO-{1000 + i}", dt.datetime(2025, 4, 1) + dt.timedelta(days=i), names[i % 3],
                     round(40 + 3.25 * i, 2), ["Shipped", "Open", "Cancelled"][i % 3]])
    return rows[:16] + extra + rows[16:]


def test_a_cancelled_order_with_placeholders_stays_in_the_data(tmp_path):
    a = analyze.Analysis([_book(tmp_path / "o.xlsx", {"Orders": _orders(
        [["SO-999", "TBD", "Acme Supply", "N/A", "Cancelled"]])})])
    t = _only(a, "Orders")
    assert t.n_rows == 31 and 16 in t.row_index and not t.segments
    assert not [i for i in _structure(a) if i["recipe"].startswith("structure:segments:")]


@pytest.mark.parametrize("row", [
    ["SO-998", "next week", "Acme Supply", "waived", "Open"],      # an ID shaped like the others: data
    [None, "soon", "Acme Supply", "free", "Cancelled"],            # every text value is written elsewhere
])
def test_text_in_number_columns_is_a_header_only_when_it_reads_as_labels(tmp_path, row):
    a = analyze.Analysis([_book(tmp_path / "o.xlsx", {"Orders": _orders([row])})])
    t = _only(a, "Orders")
    assert t.n_rows == 31 and not t.segments


def test_whole_dollar_pay_rates_and_sizes_stay_metrics(tmp_path):
    rng = random.Random(21)
    pay = {f"E{10 + k}": v for k, v in enumerate([2500, 3000, 3500, 4200])}
    rows = [["Pay Date", "Employee ID", "Employee Name", "Gross Pay", "Room Rate", "Group Size", "Class Size",
             "Reason Code"]]
    for p in range(26):
        for k, (eid, amt) in enumerate(pay.items()):
            rows.append([dt.datetime(2025, 1, 3) + dt.timedelta(days=14 * p), eid, f"Person {k}", amt,
                         rng.choice([129, 149, 189, 229]), rng.randint(2, 6), rng.randint(2, 6), rng.randint(1, 4)])
    a = analyze.Analysis([_book(tmp_path / "pay.xlsx", {"Pay": rows})])
    t = a.tables[0]
    for h in ("Gross Pay", "Room Rate", "Group Size", "Class Size"):
        c = _col(a, t, h)
        assert c.semantic == "metric" and not c.codes, h
    assert _col(a, t, "Reason Code").codes
    m = detect.match_roles(PLAYBOOKS["payroll_hr"], a.cols[t.tid])
    assert m["gross_pay"]["header"] == "Gross Pay"


def test_a_name_part_after_the_entity_still_binds(tmp_path):
    rows = [["Employee Last Name", "Customer Company", "Vendor Legal Name", "Customer Region", "Qty"]]
    for i in range(40):
        rows.append([f"Last{i % 12}", f"Company {i % 9}", f"Vendor {i % 5} LLC", ["East", "West"][i % 2], i % 7 + 1])
    a = analyze.Analysis([_book(tmp_path / "n.xlsx", {"Names": rows})])
    cols = a.cols[a.tables[0].tid]
    assert detect.match_roles(PLAYBOOKS["payroll_hr"], cols)["employee_name"]["header"] == "Employee Last Name"
    assert detect.match_roles(PLAYBOOKS["sales_transactions"], cols)["customer"]["header"] == "Customer Company"
    assert detect.match_roles(PLAYBOOKS["procurement"], cols)["vendor"]["header"] == "Vendor Legal Name"
    region = [c for c in cols if c.header == "Customer Region"]
    assert "customer" not in detect.match_roles(PLAYBOOKS["sales_transactions"], region)


def test_rate_and_per_read_the_values_before_blocking_dollars(tmp_path):
    rng = random.Random(22)
    rows = [["Order #", "Date", "Item", "Qty", "Price per Unit", "Tax Rate", "Discount Rate"]]
    for i in range(50):
        rows.append([f"R{700 + i}", dt.datetime(2025, 6, 1) + dt.timedelta(days=i), f"Thing {i % 8}",
                     rng.randint(1, 5), round(rng.uniform(4, 60), 2), rng.choice([0.06, 0.07, 0.075]),
                     rng.choice([0, 5, 10, 12.5])])
    a = analyze.Analysis([_book(tmp_path / "r.xlsx", {"Sales": rows})])
    m = detect.match_roles(PLAYBOOKS["sales_transactions"], a.cols[a.tables[0].tid])
    assert m["unit_price"]["header"] == "Price per Unit"
    assert "tax" not in m and "discount" not in m               # both read as percents


def _ledger(tmp_path, heads, left_money=True):
    rng = random.Random(23)
    rows = [["Date", "Account", "Memo"] + heads]
    for i in range(60):
        amt = round(rng.uniform(20, 800), 2) if left_money else rng.randint(1, 40)
        left = i % 3 != 0
        rows.append([dt.datetime(2025, 3, 1) + dt.timedelta(days=i), rng.choice(["Rent", "Water", "Fees", "Cash"]),
                     f"line {i}", amt if left else None, None if left else amt])
    a = analyze.Analysis([_book(tmp_path / f"{heads[0]}.xlsx", {"Ledger": rows})])
    return a, a.tables[0]


def test_the_debit_side_comes_from_the_header_not_the_position(tmp_path):
    a, t = _ledger(tmp_path, ["Payment", "Charge"])
    cols = a.cols[t.tid]
    pair = detect.debit_credit_pair(t, cols)
    assert pair and [c.header for c in detect.pair_sides(pair)] == ["Charge", "Payment"]
    m = detect._infer_required(PLAYBOOKS["ledger"], detect.match_roles(PLAYBOOKS["ledger"], cols), pair)
    assert m["debit"]["header"] == "Charge" and m["credit"]["header"] == "Payment"


def test_a_pair_whose_headers_name_no_side_scores_but_binds_nothing(tmp_path):
    a, t = _ledger(tmp_path, ["North", "South"])
    cols = a.cols[t.tid]
    pair = detect.debit_credit_pair(t, cols)
    assert pair and detect.pair_sides(pair) is None
    pb = PLAYBOOKS["ledger"]
    m = detect._infer_required(pb, detect.match_roles(pb, cols), pair)
    assert detect.score(pb, m) > 0 and all(m[r].get("unbound") for r in ("debit", "credit"))
    assert "debit" not in a.detection["roles"] and "credit" not in a.detection["roles"]


def test_quantities_in_and_out_are_not_a_money_pair(tmp_path):
    a, t = _ledger(tmp_path, ["Qty In", "Qty Out"], left_money=False)
    assert detect.debit_credit_pair(t, a.cols[t.tid]) is None


def test_text_dates_that_read_both_ways_are_left_as_written(tmp_path):
    rows = [["Ref", "When", "Amount"]]
    for i in range(24):
        rows.append([f"W{i}", f"{1 + i % 12:02d}/{1 + i // 2 % 12:02d}/2025", 10.0 + i])
    a = analyze.Analysis([_book(tmp_path / "amb.xlsx", {"Log": rows})])
    t = _only(a, "Log")
    c = _col(a, t, "When")
    assert c.type == "text" and c.retyped == 0 and c.ambiguous_dates == 24
    assert t.retyped[1]["ambiguous"]
    facts = {i["recipe"]: i for i in _structure(a)}
    assert "both day first and month first" in facts["structure:ambiguous_dates:Log:When"]["statement"]
    assert not [r for r in facts if r.startswith("structure:mixed_types:")]


def test_a_text_date_note_names_the_order_it_read(tmp_path):
    rows = [["Ref", "When", "Amount"]] + [[f"W{i}", f"03/{13 + i:02d}/2025", 10.0 + i] for i in range(12)]
    a = analyze.Analysis([_book(tmp_path / "us.xlsx", {"Log": rows})])
    fact = next(i for i in _structure(a) if i["recipe"].startswith("structure:read_from_text:"))
    assert "month first (MM/DD/YYYY)" in fact["statement"] and fact["numbers"]["format"] == "%m/%d/%Y"


def test_a_column_of_dollar_text_is_a_column_of_numbers(tmp_path):
    prices = [10.0 + 2.5 * i for i in range(20)]
    rows = [["Date", "Item", "Price"]] + [[dt.datetime(2025, 5, 1 + i), f"I{i}", f"${p:,.2f}"]
                                          for i, p in enumerate(prices)]
    a = analyze.Analysis([_book(tmp_path / "d.xlsx", {"Prices": rows})])
    c = _col(a, _only(a, "Prices"), "Price")
    assert c.type == "number" and c.sum == pytest.approx(sum(prices)) and c.retyped == 20


def test_two_tables_on_one_tab_keep_their_own_total_notes(tmp_path):
    def block(tag):
        out = [["Ref", "Date", "Amount"]]
        out += [[f"{tag}{i}", dt.datetime(2025, 1, 1 + i), 10.0 + i] for i in range(6)]
        return out + [["Total", None, sum(10.0 + i for i in range(6))]]
    rows = block("A") + [[]] + block("B")
    path = _book(tmp_path / "two.xlsx", {"Sums": rows})
    a = analyze.Analysis([path])
    assert len([t for t in a.tables if t.sheet == "Sums"]) == 2
    notes = [i for i in a.insights if i["recipe"] == "structure:totals_rows"]
    assert sorted(i["numbers"]["table"] for i in notes) == ["Sums#1", "Sums#2"]
    from sheetbrain.brain import Composer
    kept = [r for r in Composer(a, path, "b1", {}).compose() if "total row" in str(r.get("statement", ""))]
    assert len({r["id"] for r in kept}) == 2


def test_fixture_detections_are_pinned():
    fx = os.path.join(os.path.dirname(__file__), "..", "evals", "fixtures")

    def roles(name):
        a = analyze.Analysis([os.path.join(fx, name)])
        return a, {k: (v["table"], v["header"]) for k, v in a.detection["roles"].items()}
    a, r = roles("finance_model.xlsx")
    assert a.detection["archetype"] == "financial_model"
    assert r["cash"][1] == "Cash" and r["cost"][1] == "COGS" and r["profit"][1] == "Net Income"
    a, r = roles("ledger_gl.xlsx")
    assert a.detection["archetype"] == "ledger" and r["debit"] == ("GL", "Debit") and r["credit"] == ("GL", "Credit")
    assert _col(a, _only(a, "GL"), "Account").codes
    a, r = roles("hostile_brain.xlsx")
    assert a.detection["archetype"] == "sales_transactions" and r["order_id"] == ("Orders", "Order ID")
    a, r = roles("procurement_hotel.xlsx")
    assert a.detection["archetype"] == "procurement"
    assert r["unit_price"] == ("Detail", "Unit Price") and r["vendor"] == ("Detail", "Vendor")
    a, _ = roles("messy_multitable.xlsx")
    assert a.detection["archetype"] == "generic"
