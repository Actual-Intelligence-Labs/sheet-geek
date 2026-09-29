"""Grain and reference tables. Code settles what a row is and how the dates fall
as counted facts, never as questions: a grid with its periods across, a
snapshot panel whose stock columns are read on the latest date, entries whose
lines net to zero, a date a title names, the cycle the dates follow, and prices
that are the same everywhere in a period. A reference or terms table the lines
look up gets its dated windows, dated versions, list-price runs, codes that
start for one group and per-key terms as facts or findings. Every detector
fires on its plant on 19 of 20 seeds or more, stays silent on its null twin
and asks nothing on the noise books. Every book here is synthetic
(tests/synth.py) or built in the test."""
import datetime as dt
import math
import os
import random
import re
import sys

import pytest

pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import interview  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402
from sheetbrain.profile import norm_key  # noqa: E402
from sheetbrain.recipes import day_words  # noqa: E402
from test_detectors import _contract  # noqa: E402

NEEDED = math.ceil(0.95 * len(SEEDS))
GRAIN = ("snapshot_panel", "balanced_entries", "pay_cycle", "price_trend")
REFS = ("terms_window", "list_price", "fee_onset")
NEW = ("find_window_", "find_listscope_", "find_offlist_", "find_onset_")
RECIPES = ("window:", "listprice:", "listprice_all:", "offlist:", "onset:", "structure:versions:", "price_trend:")


def _built(tmp_path, seed, name, twin=False):
    m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
    return m, Analysis([m["path"]])


def _found(a, prefix):
    return [i for i in a.insights if i.get("recipe", "").startswith(prefix)]


def _grain(a, kind):
    return [g for g in a.grain_facts if g["recipe"].startswith(f"grain:{kind}:")]


def _asks(a, answers=None):
    return [q for q in interview.candidates(a, answers or {}) if q.id.startswith(NEW)]


def _ans(q, raw):
    return interview.parse_answers([q], raw)[q.id]


def _book(path, sheets):
    """Write sheets ([{name, rows}]) with xlsxwriter, formulas with their saved values."""
    synth._write_xlsxwriter(str(path), sheets)
    return str(path)


# --------------------------------------------------------------------------
# rank 11: a snapshot panel reads its stock on the latest date
# --------------------------------------------------------------------------
def _snapshot_ok(tmp_path, seed, name="snapshot_panel") -> bool:
    """The panel's date, its dates and its stock columns are named; the top
    share of the money column is counted on the latest date alone and says so;
    the column's note gives its sum there, never across dates; and no question
    asks what a row is, or whether it is a count or a log of moves."""
    m, a = _built(tmp_path, seed, name)
    p = m["plants"][0]
    t = a.tables[0]
    snap = a.snapshots.get(t.tid) or {}
    if snap.get("col") != p["col"] or snap.get("dates") != p["dates"] or set(snap.get("stock", [])) != set(p["stock"]):
        return False
    fact = _grain(a, "snapshot")
    latest = p["latest"][:10]
    if len(fact) != 1 or p["col"] in (None, "") or day_words(latest) not in fact[0]["statement"] \
            or "never summed across dates" not in fact[0]["statement"]:
        return False
    jd, js, jm = (t.headers.index(h) for h in (p["col"], p["site_col"], p["money"]))
    by: dict = {}
    for r in t.rows:
        if r[jd].date().isoformat() == latest:
            by[r[js]] = by.get(r[js], 0.0) + r[jm]
    top = [i for i in _found(a, "top_share:") if p["money"] in i["statement"]]
    if not top or not all(i["numbers"].get("as_of") == latest and day_words(latest) in i["statement"]
                          and abs(i["numbers"]["value"] - max(by.values())) < 0.01 for i in top):
        return False
    note = next(r for r in Composer(a, m["path"], "b1", {}).compose() if r.get("label") == p["money"]
                and str(r.get("id", "")).startswith("col:"))
    if "on the latest" not in note["statement"] or "over all rows" in note["statement"]:
        return False
    # what a row is is never asked: only confirmed in one tap, in room left over (round 3 fix 8)
    return not [q for q in interview.candidates(a, {}) if q.kind == "grain" and q.id != interview.CONFIRM] \
        and all(not interview.in_round(q) for q in interview.candidates(a, {}) if q.id == interview.CONFIRM)


def test_a_count_panel_reads_its_stock_on_the_latest_date(tmp_path):
    hits = sum(_snapshot_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_a_log_whose_pairs_change_every_week_is_no_panel(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "snapshot_panel", twin=True)
        assert not a.snapshots and not _grain(a, "snapshot"), seed
        assert not [i for i in _found(a, "top_share:") if "latest" in i["statement"]], seed


def test_a_bare_count_bound_to_the_on_hand_role_is_read_on_the_latest_date(tmp_path):
    """No header rule reads 'Count' as a level; the playbook role it binds to
    does. Its value column is read on the latest date too, and the playbook's
    'a count on one date, or a log of moves?' is settled by the counted fact."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "count_panel")
        roles = a.detection["roles"]
        hits += _snapshot_ok(tmp_path, seed, "count_panel") and roles.get("on_hand", {}).get("header") == "Count" \
            and "snapshot" in interview.covered_ids(a, {})
    assert hits >= NEEDED, hits


def test_a_count_log_is_no_panel_and_still_asks_count_or_log(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "count_panel", twin=True)
        assert not [s for s in a.snapshots.values() if s["stock"]], seed
        assert not [i for i in _found(a, "top_share:") if "latest" in i["statement"]], seed
        if a.detection["archetype"] == "inventory":
            assert "snapshot" in [q.id for q in interview.candidates(a, {})], seed


def test_a_panel_with_no_stock_column_still_asks_count_or_log(tmp_path):
    """A par sheet written out every week is a panel (one item at one site on one
    date), but nothing in it is a count on hand: what a row is may be stated,
    yet whether the sheet is a count or a log of moves is still the owner's."""
    rng = random.Random(16)
    sites, items = synth._names(rng, 3), synth._names(rng, 12, 3)
    days = [dt.datetime(2031, 1, 5) + dt.timedelta(days=7 * k) for k in range(10)]
    rows = [["Count Date", "Location", "Item", "Par", "Unit Cost"]] + [
        [d, s, i, rng.randint(2, 20), round(rng.uniform(2, 40), 2)] for d in days for s in sites for i in items]
    path = _book(tmp_path / "par.xlsx", [{"name": "Par", "rows": rows}])
    a = Analysis([path])
    assert a.detection["archetype"] == "inventory"
    snap = a.snapshots.get(a.tables[0].tid)
    assert snap and snap["stock"] == [] and _grain(a, "snapshot"), a.grain_facts
    assert "snapshot" not in interview.covered_ids(a, {})
    assert "snapshot" in [q.id for q in interview.candidates(a, {})]


# --------------------------------------------------------------------------
# rank 11: entries whose lines net to zero
# --------------------------------------------------------------------------
def _balanced_ok(tmp_path, seed) -> bool:
    """Credits turn negative from a random date on, so the rule is the debit
    minus the credit without its sign: every entry nets, n of n, and nothing
    asks whether the entry number repeats by mistake."""
    m, a = _built(tmp_path, seed, "balanced_entries")
    p = m["plants"][0]
    t = a.tables[0]
    b = a.balanced.get(t.tid) or {}
    n = p["entries"]
    fact = _grain(a, "balanced")
    if b.get("col") != p["col"] or b.get("entries") != n or b.get("netted") != n or len(fact) != 1:
        return False
    stmt = fact[0]["statement"]
    if f"{n:,} of {n:,}" not in stmt or "absolute Credit" not in stmt or f"{p['lo']} to {p['hi']} lines" not in stmt:
        return False
    if [i for i in _found(a, "duplicates:") if any(h == p["col"] for _s, h in i.get("depends") or [])]:
        return False
    qs = interview.candidates(a, {})
    return not [q for q in qs if (q.meta.get("about") or {}).get("col") == p["col"]] \
        and not [q for q in qs if q.kind == "grain" and q.id == "row_is"]


def test_journal_entries_net_to_zero_n_of_n(tmp_path):
    hits = sum(_balanced_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_entries_that_do_not_net_are_no_entries(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "balanced_entries", twin=True)
        assert not a.balanced and not _grain(a, "balanced"), seed


# --------------------------------------------------------------------------
# rank 11: the cycle the dates follow
# --------------------------------------------------------------------------
def _cadence_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "pay_cycle")
    p = m["plants"][0]
    fact = [g for g in _grain(a, "cadence") if g["numbers"]["col"] == p["col"]]
    if len(fact) != 1:
        return False
    n, stmt = fact[0]["numbers"], fact[0]["statement"]
    return n["step"] == 14 and n["off"] == p["off"] == 3 and n["last"] == p["last"][:10] \
        and "every 14 days on a Friday" in stmt and "3 dates are off that cycle" in stmt \
        and day_words(p["last"][:10]) in stmt


def test_pay_dates_every_14_days_on_a_friday_with_3_off_cycle(tmp_path):
    hits = sum(_cadence_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_dates_scattered_over_the_week_have_no_cycle(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "pay_cycle", twin=True)
        assert not _grain(a, "cadence"), (seed, [g["statement"] for g in _grain(a, "cadence")])


# --------------------------------------------------------------------------
# rank 11: prices the same everywhere within a period
# --------------------------------------------------------------------------
def _trend_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "price_trend")
    p = m["plants"][0]
    trend = _found(a, "price_trend:")
    return len(trend) == 1 and not _found(a, "spread:") and p["period"] in trend[0]["statement"] \
        and p["col"] in trend[0]["statement"] and trend[0]["numbers"]["moved"] >= 1


def test_prices_the_same_across_sites_each_week_are_a_trend_not_a_spread(tmp_path):
    hits = sum(_trend_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_prices_that_never_move_say_nothing(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "price_trend", twin=True)
        assert not _found(a, "price_trend:") and not _found(a, "spread:"), seed


# --------------------------------------------------------------------------
# rank 11: grids, titles, keys and the meta note, on books built here
# --------------------------------------------------------------------------
def _grid(rng, months: int = 12):
    heads = [dt.date(2031, 1, 1).replace(month=m + 1).strftime("%b %Y") for m in range(months)]
    lines = synth._names(rng, 6, 3)
    rows = [["Line"] + heads] + [[ln] + [round(rng.uniform(100, 900), 2) for _ in heads] for ln in lines]
    return rows, heads, lines


def test_a_grid_with_months_across_is_one_fact_and_one_node_per_line_item(tmp_path):
    rng = random.Random(11)
    rows, heads, lines = _grid(rng)
    path = _book(tmp_path / "grid.xlsx", [{"name": "Plan", "rows": rows}])
    a = Analysis([path])
    fact = _grain(a, "wide")
    assert len(fact) == 1, [g["statement"] for g in a.grain_facts]
    stmt = fact[0]["statement"]
    assert "each row is a line item and each column is a month" in stmt and "Jan 2031 to Dec 2031" in stmt \
        and "(12 periods, 6 line items)" in stmt, stmt
    recs = Composer(a, path, "b1", {}).compose()
    nodes = [r for r in recs if r.get("record") == "node"]
    assert not [r for r in nodes if r.get("label") in heads]
    items = {r["label"] for r in nodes if r.get("kind") == "line_item"}
    assert items == set(lines)
    assert not [q for q in interview.candidates(a, {}) if q.id == "row_is"]


def _log(rng, start, n=80, days=200):
    rows = [["Date", "Ref", "Site", "Amount"]]
    for k in range(n):
        rows.append([start + dt.timedelta(days=rng.randrange(days)), f"R-{500 + k}", rng.choice(["Kalo", "Venmi"]),
                     round(rng.uniform(10, 500), 2)])
    return rows


def test_a_title_as_of_date_is_a_snapshot_fact(tmp_path):
    rng = random.Random(12)
    body = _log(rng, dt.datetime(2031, 1, 1), days=80)
    path = _book(tmp_path / "asof.xlsx", [{"name": "Stock", "rows": [["Counts as of March 31, 2031"], []] + body}])
    a = Analysis([path])
    fact = _grain(a, "as_of")
    assert len(fact) == 1 and "snapshot as of Mar 31, 2031" in fact[0]["statement"], a.grain_facts
    assert "dated after" not in fact[0]["statement"]


def test_a_title_period_the_rows_run_past_is_counted(tmp_path):
    rng = random.Random(13)
    body = _log(rng, dt.datetime(2031, 1, 1), days=150)
    after = sum(1 for r in body[1:] if r[0] > dt.datetime(2031, 3, 31))
    path = _book(tmp_path / "period.xlsx", [{"name": "Sales", "rows": [["Sales for Q1 2031"], []] + body}])
    a = Analysis([path])
    fact = _grain(a, "title_period")
    assert len(fact) == 1 and fact[0]["numbers"]["after"] == after > 0, a.grain_facts
    assert f"{after:,} of 80 rows are dated after Mar 31, 2031" in fact[0]["statement"]
    # a title the rows keep to says nothing
    inside = _log(rng, dt.datetime(2031, 1, 1), days=80)
    path = _book(tmp_path / "inside.xlsx", [{"name": "Sales", "rows": [["Sales for Q1 2031"], []] + inside}])
    assert not _grain(Analysis([path]), "title_period")


def test_title_dates_read_as_written_and_fiscal_periods_are_left_alone():
    from sheetbrain.analyze import _title_period
    got = {t: _title_period(t, {2031}) for t in ["Counts as of March 31, 2031", "Sales for Q1 2031",
                                                  "Jan 2031 to Mar 2031", "Report run on 3/4/2031", "Sales 2031",
                                                  "Budget FY2031", "Q3 FY2031"]}
    assert [(g or {}).get("kind") for g in got.values()] == ["as_of", "quarter", "range", "run", "year", None, None]
    assert got["Sales for Q1 2031"]["end"] == got["Jan 2031 to Mar 2031"]["end"] == dt.date(2031, 3, 31)
    # a four-digit number is a year only beside a period word, or alone in the title and among the rows' years
    rows_2031 = {2031}
    assert _title_period("Store 2019 daily sales", rows_2031) is None
    assert _title_period("Store 2019 sales for 2031", rows_2031)["end"] == dt.date(2031, 12, 31)
    assert _title_period("Store 12 sales 2031", rows_2031) is None
    assert _title_period("Sales for 2030", rows_2031)["end"] == dt.date(2030, 12, 31)
    assert _title_period("Year 2030 sales", rows_2031)["end"] == dt.date(2030, 12, 31)


def test_a_store_number_in_a_title_is_no_year(tmp_path):
    rng = random.Random(17)
    body = _log(rng, dt.datetime(2031, 1, 1), n=60, days=120)
    path = _book(tmp_path / "store.xlsx", [{"name": "Sales", "rows": [["Store 2019 daily sales"], []] + body}])
    a = Analysis([path])
    assert not _grain(a, "title_period") and not _grain(a, "as_of"), a.grain_facts


def test_meta_span_comes_only_from_the_main_tables_own_date(tmp_path):
    """The lookup has an older date column and the main table has none: the meta
    names no dates. With a date on the main table, it names the column."""
    rng = random.Random(14)
    codes = [f"C{100 + k}" for k in range(8)]
    lookup = [["Code", "Name", "Added"]] + [[c, synth._word(rng, 3), dt.datetime(2019, 1, 1) + dt.timedelta(days=30 * k)]
                                            for k, c in enumerate(codes)]
    main = [["Code", "Qty", "Amount"]] + [[rng.choice(codes), rng.randint(1, 9), round(rng.uniform(5, 90), 2)]
                                          for _ in range(120)]
    path = _book(tmp_path / "nodate.xlsx", [{"name": "Lines", "rows": main}, {"name": "Codes", "rows": lookup}])
    a = Analysis([path])
    meta = next(r for r in Composer(a, path, "b1", {}).compose() if r.get("record") == "meta")
    assert "dated" not in meta["statement"] and "Lines had 120 rows" in meta["statement"], meta["statement"]
    body = _log(rng, dt.datetime(2031, 2, 1))
    path = _book(tmp_path / "dated.xlsx", [{"name": "Log", "rows": body}, {"name": "Codes", "rows": lookup}])
    a = Analysis([path])
    meta = next(r for r in Composer(a, path, "b1", {}).compose() if r.get("record") == "meta")
    first, last = min(r[0] for r in body[1:]), max(r[0] for r in body[1:])
    assert f"dated {first.date().isoformat()} to {last.date().isoformat()} by Date" in meta["statement"]


def test_a_models_meta_states_its_tab_flow_or_its_period_span(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "model_input")
    meta = next(r for r in Composer(a, m["path"], "b1", {}).compose() if r.get("record") == "meta")
    assert "when its grids ran over 12 months, Jan to Dec." in meta["statement"], meta["statement"]
    assert " rows" not in meta["statement"].split("Written on", 1)[1].split(".")[0]


def test_the_ledger_meta_still_reads_its_span_by_date():
    a = Analysis([os.path.join(synth.FIXTURES, "ledger_gl.xlsx")])
    meta = next(r for r in Composer(a, a.paths[0], "b1", {}).compose() if r.get("record") == "meta")
    assert re.search(r"dated 2025-\d\d-\d\d to 2025-\d\d-\d\d by Date", meta["statement"]), meta["statement"]


def test_a_key_of_three_columns_and_a_near_key_with_its_exceptions(tmp_path):
    rng = random.Random(15)
    sites, items = synth._names(rng, 3), synth._names(rng, 5, 3)
    days = [dt.datetime(2031, 1, 1) + dt.timedelta(days=7 * k) for k in range(8)]
    rows = [["Week", "Site", "Item", "Qty"]] + [[d, s, i, rng.randint(1, 50)] for d in days for s in sites
                                                 for i in items]
    path = _book(tmp_path / "three.xlsx", [{"name": "Counts", "rows": rows}])
    a = Analysis([path])
    assert sorted(a.keys[a.tables[0].tid]) == ["Item", "Site", "Week"] and not a.key_extra
    rows = [["Ref", "Date", "Amount"]] + [[f"N-{1000 + k}", dt.datetime(2031, 1, 1) + dt.timedelta(days=k % 300),
                                           round(rng.uniform(5, 90), 2)] for k in range(400)]
    rows += [[rows[5][0], dt.datetime(2031, 5, 5), 12.5], [rows[9][0], dt.datetime(2031, 5, 6), 13.5]]
    path = _book(tmp_path / "near.xlsx", [{"name": "Log", "rows": rows}])
    a = Analysis([path])
    tid = a.tables[0].tid
    assert a.keys[tid] == ["Ref"] and a.key_extra[tid] == 2
    key = next(r for r in Composer(a, path, "b1", {}).compose() if str(r.get("id", "")).startswith("f:key:"))
    assert "unique by Ref, except 2 rows that repeat a value already seen" in key["statement"], key["statement"]


# --------------------------------------------------------------------------
# rank 19: lines outside the dates a terms table gives their key, and dated versions
# --------------------------------------------------------------------------
def _window_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "terms_window")
    w, v = m["plants"]
    found = _found(a, "window:")
    if len(found) != 1:
        return False
    per = found[0]["numbers"]["per"]
    if [(x["key"], x["after"]) for x in per] != [(w["value"], 30)] or found[0]["numbers"]["rows"] != 30:
        return False
    qs = [q for q in _asks(a) if q.id.startswith("find_window_")]
    return len(qs) == 1 and "What applies to these 30 lines?" in qs[0].prompt and w["value"] in qs[0].prompt


def test_a_vendor_with_30_lines_after_its_end_gets_one_window_question(tmp_path):
    hits = sum(_window_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def _versions_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "terms_window")
    v = m["plants"][1]
    found = _found(a, "structure:versions:")
    return len(found) == 1 and found[0]["numbers"]["keys"] == [v["value"]] \
        and "takes the row whose" in found[0]["statement"] \
        and not [q for q in interview.candidates(a, {}) if q.id.startswith("find_unmatched_")]


def test_a_vendor_on_two_adjoining_dated_rows_is_a_versions_fact(tmp_path):
    hits = sum(_versions_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_every_line_inside_its_dates_on_one_row_each_says_nothing(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "terms_window", twin=True)
        assert not _found(a, "window:") and not _found(a, "structure:versions:") and not _asks(a), seed


def test_terms_are_per_key_facts_with_rates_and_words(tmp_path):
    """Each vendor on the terms tab is a counted fact: its dates, the rebate with
    its unit and the terms in words; the playbook's contract-terms question is
    covered once those facts exist."""
    for seed in SEEDS[:5]:
        m, a = _built(tmp_path, seed, "terms_window")
        vendors = {r["key"] for r in a.terms}
        assert m["plants"][0]["value"] in vendors and len(a.terms) == len(vendors) + 1, seed
        assert all(re.search(r"Rebate (%|Rate) \d+(\.\d+)?%", x["body"]) and "Terms \"" in x["body"]
                   for x in a.terms), [x["body"] for x in a.terms]
        # covered where the playbook asks it (a book read as generic has no contract-terms question to cover)
        asks = any(q["id"] == "contract_terms" for q in (a.playbook or {}).get("questions", []))
        assert ("contract_terms" in interview.covered_ids(a, {})) == asks, (seed, a.detection["archetype"])
        recs = Composer(a, m["path"], "b1", {}).compose()
        assert len([r for r in recs if str(r.get("id", "")).startswith("f:terms:")]) == len(a.terms)


def _buys_and_ref(rng, keys, ref_head, ref_rows, key_head="Vendor", n=400):
    """Dated purchase lines over one year keyed by one column, and a reference tab keyed the same way."""
    buys = [["Invoice Date", key_head, "Qty", "Unit Price", "Ext Price"]]
    for _ in range(n):
        q, p = rng.randint(1, 9), round(rng.uniform(4, 60), 2)
        buys.append([dt.datetime(2031, 1, 1) + dt.timedelta(days=rng.randrange(360)), rng.choice(keys), q, p,
                     round(q * p, 2)])
    return [{"name": "Buys", "rows": buys}, {"name": "Ref", "rows": [ref_head] + ref_rows}]


def test_terms_numbers_carry_only_the_unit_their_header_gives(tmp_path):
    """'Fee per Case' 2.5 is money by its header, never 2.5%; 'Min Order Qty' 1 is
    1, never 100%; 'Rebate %' 2 is 2%; a fraction under a 'Rebate Rate' header is
    a percent; 'Drop Fee' 50 keeps its currency."""
    rng = random.Random(24)
    vendors = synth._names(rng, 4)
    ref = [[v, dt.datetime(2030, 7, 1), dt.datetime(2032, 6, 30), 2.5, 1, 2, 0.015, 50, "Net 30 on all lines"]
           for v in vendors]
    head = ["Vendor", "Start Date", "End Date", "Fee per Case", "Min Order Qty", "Rebate %", "Rebate Rate",
            "Drop Fee", "Terms"]
    path = _book(tmp_path / "units.xlsx", _buys_and_ref(rng, vendors, head, ref))
    a = Analysis([path])
    assert len(a.terms) == len(vendors), [x["body"] for x in a.terms]
    for x in a.terms:
        assert "Fee per Case $2.50" in x["body"] and "Min Order Qty 1;" in x["body"], x["body"]
        assert "Rebate % 2%" in x["body"] and "Rebate Rate 1.5%" in x["body"], x["body"]
        assert "Drop Fee $50.00" in x["body"] and "2.5%" not in x["body"] and "100%" not in x["body"], x["body"]


def test_a_dated_price_list_by_item_is_versions_not_terms(tmp_path):
    """An item price list with Effective and Expires dates is no terms table: no
    per-item terms facts in any brain, and the playbook's contract-terms question
    is not covered by it. Small (20 items) or large (120 items), the same."""
    for n_items in (20, 120):
        rng = random.Random(25 + n_items)
        items = [f"SK-{1000 + k}" for k in range(n_items)]
        ref = [[it, dt.datetime(2030, 7, 1), dt.datetime(2032, 6, 30), round(rng.uniform(4, 60), 2)] for it in items]
        sheets = _buys_and_ref(rng, items, ["Item #", "Effective", "Expires", "List Price"], ref, key_head="Item #",
                               n=max(400, 12 * n_items))
        path = _book(tmp_path / f"plist{n_items}.xlsx", sheets)
        a = Analysis([path])
        assert a.terms == [], (n_items, [x["statement"] for x in a.terms][:3])
        assert "contract_terms" not in interview.covered_ids(a, {}), n_items
        recs = Composer(a, path, "b1", {}).compose()
        assert not [r for r in recs if str(r.get("id", "")).startswith("f:terms:")], n_items


def test_more_partners_than_can_be_listed_are_one_counted_fact(tmp_path):
    """40 vendors with a rebate each: one counted fact for all of them, never 30
    of them and silence on the rest."""
    rng = random.Random(26)
    vendors = synth._names(rng, 40)
    ref = [[v, dt.datetime(2030, 7, 1), dt.datetime(2032, 1, 31) if i < 3 else dt.datetime(2033, 6, 30),
            rng.choice([1, 2, 3])] for i, v in enumerate(vendors)]
    path = _book(tmp_path / "many.xlsx", _buys_and_ref(rng, vendors, ["Vendor", "Start", "End", "Rebate %"], ref,
                                                        n=1200))
    a = Analysis([path])
    assert len(a.terms) == 1 and a.terms[0]["summary"], [x["statement"] for x in a.terms]
    stmt = a.terms[0]["statement"]
    assert stmt.startswith("40 Vendors on Ref: 40 dated rows by Start and End") and "3 of them end within 90 days" \
        in stmt, stmt


def test_the_window_question_keeps_the_option_contract(tmp_path, synth_seed):
    for name in REFS:
        _m, a = _built(tmp_path, synth_seed, name)
        for q in _asks(a):
            _contract(a, q)


# --------------------------------------------------------------------------
# rank 19: a vendor that follows the list price from one month on
# --------------------------------------------------------------------------
def _conformity_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "list_price")
    p = m["plants"][0]
    lp = _found(a, "listprice:")
    if len(lp) != 1:
        return False
    runs = lp[0]["numbers"]["runs"]
    if [(x["value"], x["from"]) for x in runs] != [(p["value"], p["from"])] or lp[0]["numbers"]["group"] != p["columns"][1]:
        return False
    off = _found(a, "offlist:")
    if len(off) != 1 or off[0]["numbers"]["rows"] != 4 or off[0]["numbers"]["months"] != [p["above_month"]] \
            or off[0]["numbers"]["side"] != "above":
        return False
    rows = sorted(r + 2 for r in off[0]["numbers"]["row_ids"])
    qs = {q.id.split("_")[1]: q for q in _asks(a)}
    return rows == sorted(p["rows"]) and set(qs) == {"listscope", "offlist"} and "4 " in qs["offlist"].prompt


def test_a_vendor_on_the_list_from_month_7_with_4_lines_above_it(tmp_path):
    hits = sum(_conformity_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_the_floating_contract_price_guess_waits_on_the_list_price_check(tmp_path):
    """The procurement guess that contract prices float week to week is left out
    of the brain only once most lines with a list price are counted at it exactly
    (no_insight:listprice_all:). One vendor at the list while the others float
    keeps it, and so does every vendor floating."""
    for seed in SEEDS[:5]:
        for case in ("one", "none", "all"):
            table, _plants, (plist,) = synth.TRAPS["list_price"][0](random.Random(f"gotcha:{seed}"), case == "none")
            table[0] = ["Invoice Date", "Vendor", "Item #", "Qty", "Unit Price", "Ext Price"]
            plist[0] = ["Item #", "Contract Price"]
            if case == "all":          # every vendor charges the list price on every line
                listed = {row[0]: row[1] for row in plist[1:]}
                for row in table[1:]:
                    row[4], row[5] = listed[row[2]], round(row[3] * listed[row[2]], 2)
            path = _book(tmp_path / f"cp{seed}{case}.xlsx", [{"name": "Buys", "rows": table},
                                                             {"name": "Prices", "rows": plist}])
            a = Analysis([path])
            assert a.detection["archetype"] == "procurement" and "contract_price" in a.detection["roles"]
            assert bool(_found(a, "listprice:")) == (case == "one"), (seed, case)
            assert bool(_found(a, "listprice_all:")) == (case == "all"), (seed, case)
            guess = [r for r in Composer(a, path, "b1", {}).compose() if "float week to week" in r["statement"]]
            assert bool(guess) == (case != "all"), (seed, case)


def test_prices_that_float_around_the_list_say_nothing(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "list_price", twin=True)
        assert not _found(a, "listprice:") and not _found(a, "offlist:") and not _asks(a), seed


# --------------------------------------------------------------------------
# rank 19: a charge that starts for one vendor
# --------------------------------------------------------------------------
def _onset_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "fee_onset")
    p = m["plants"][0]
    found = _found(a, "onset:")
    if len(found) != 1:
        return False
    n = found[0]["numbers"]
    items = [(f["group"], f["code"], f["month"]) for f in n["items"]]
    if items != [(p["group"], p["value"], p["month"])] or n["col"] != p["col"]:
        return False
    if sorted(r + 2 for r in n["row_ids"]) != sorted(p["rows"]):
        return False
    qs = [q for q in _asks(a) if q.id.startswith("find_onset_")]
    return len(qs) == 1 and p["group"] in qs[0].prompt and p["value"] in qs[0].prompt


def test_a_fee_that_starts_for_one_vendor_after_4_months(tmp_path):
    hits = sum(_onset_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_a_fee_carried_from_the_first_month_is_no_onset(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "fee_onset", twin=True)
        assert not _found(a, "onset:") and not _asks(a), seed


def test_should_not_be_there_proposes_leaving_the_rows_out_through_the_readback(tmp_path):
    """'Should not be there' changes no number on its own: it proposes a rule that
    leaves out that group's rows of the code, and the readback shows its count.
    Ticked, the rule applies; until then the totals keep those rows."""
    from sheetbrain import findings, rules
    m, a = _built(tmp_path, SEEDS[0], "fee_onset")
    p = m["plants"][0]
    q = next(q for q in _asks(a) if q.id.startswith("find_onset_"))
    said = {q.id: _ans(q, "c")}
    assert said[q.id]["options"] == ["mistake"] and rules.exclusions(a, said) == {}
    rb = findings.readback(a, said)
    assert rb is not None and len(rb.options) >= 1, rb
    assert f"({len(p['rows']):,} rows)" in rb.options[0]["label"], rb.options[0]
    said[rb.id] = _ans(rb, "a")
    got = rules.confirmed(a, said)
    assert len(got) == 1 and {x["col"] for x in got[0].predicate} == {p["col"], m["plants"][0]["columns"][1]}


def test_reference_options_name_no_purchase(tmp_path):
    """The window and list-scope detectors read leases, pay rates and service
    contracts as well as purchases: their options never say a line was bought."""
    for name in ("terms_window", "list_price"):
        for seed in SEEDS[:3]:
            _m, a = _built(tmp_path, seed, name)
            for q in _asks(a):
                words = " ".join(f"{o['label']} {o.get('desc', '')}" for o in q.options)
                words += " ".join(str(s) for s in (q.fact or {}).get("statements", {}).values())
                assert not re.search(r"\bbought\b", words), (name, seed, q.id, words)


def test_the_list_price_run_holds_when_every_vendor_has_the_same_share(tmp_path, monkeypatch):
    """The vendor that follows the list is no larger than any other: its run and
    its 4 lines above the list are still found, 19 seeds of 20 or more. (The fee
    onset at equal shares holds on 18 of 20: with few rows before the start, a
    fee missing by chance is not ruled out at 1 in 100, so the chance test stays
    silent. See dev/v02/batch-notes.md.)"""
    monkeypatch.setattr(synth, "LEAD_WEIGHT", 1)
    hits = sum(_conformity_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# rank 19: two files, one answer; a summary tab that never picks a value up;
# a join that matches only once spellings are set aside
# --------------------------------------------------------------------------
def _two_files(tmp_path, seed=21):
    """A purchases file and a terms file whose vendors carry a rebate rate."""
    rng = random.Random(seed)
    vendors = synth._names(rng, 4)
    start = dt.datetime(2031, 1, 1)
    buys = [["Invoice Date", "Vendor", "Item #", "Qty", "Unit Price", "Ext Price"]]
    for _ in range(300):
        q, p = rng.randint(1, 9), round(rng.uniform(4, 60), 2)
        buys.append([start + dt.timedelta(days=rng.randrange(360)), rng.choice(vendors), synth._word(rng, 3), q, p,
                     round(q * p, 2)])
    terms = [["Vendor", "Start Date", "End Date", "Rebate Rate", "Terms"]]
    for i, v in enumerate(vendors):          # the first agreement ends a month after the last line
        terms.append([v, dt.datetime(2030, 7, 1), dt.datetime(2032, 1, 31) if i == 0 else dt.datetime(2032, 6, 30),
                      rng.choice([1, 2, 3]), "Net 30, rebate paid each quarter"])
    p1 = _book(tmp_path / "purchases.xlsx", [{"name": "Buys", "rows": buys}])
    p2 = _book(tmp_path / "terms.xlsx", [{"name": "Terms", "rows": terms}])
    return p1, p2, vendors


def test_an_answer_reaches_every_brain_whose_tables_it_touches(tmp_path, monkeypatch):
    """An answer on the purchases brain that settles the guess about the terms
    file's rebate retires that guess in the terms brain too; the terms facts
    sit in both brains, naming the file in the other one."""
    import sb
    monkeypatch.setenv("SPREADSHEET_BRAIN_HOME", str(tmp_path / "home"))
    p1, p2, vendors = _two_files(tmp_path)
    ctx = sb.Ctx([p1, p2])
    a = ctx.a
    rebate = a.detection["roles"].get("rebate")
    assert rebate and a.file_of[rebate["table"]] == p2, a.detection["roles"].keys()
    b1, b2 = ctx.f(p1)["brain_id"], ctx.f(p2)["brain_id"]

    def guesses(path, bid):
        return [r["statement"] for r in Composer(a, path, bid, sb.answers_for(ctx, bid)).compose()
                if r.get("source") == "inferred" and "Rebate Rate" in r["statement"]]
    assert guesses(p2, b2)
    q = next(q for q in interview.candidates(a, {}) if "has_role:rebate" in interview.settled(a, {q.id: {}}))
    reached = sb.save_answer(ctx, b1, q.id, _ans(q, "a"))
    assert reached == [b2] and q.id in sb.answers_for(ctx, b2)
    assert not guesses(p2, b2)
    for path, bid in ((p1, b1), (p2, b2)):
        facts = [r["statement"] for r in Composer(a, path, bid, {}).compose()
                 if str(r.get("id", "")).startswith("f:terms:")]
        assert len(facts) == len(vendors), (path, facts)
        assert all(("in terms.xlsx" in s) == (path == p1) for s in facts), facts
        soon = [s for s in facts if "days after the last line on Buys" in s]
        assert len(soon) == 1 and soon[0].startswith(vendors[0]) and "Rebate Rate " in soon[0], facts


def test_a_readback_answer_reaches_the_brain_of_the_file_its_rule_is_on(tmp_path, monkeypatch):
    """A rule typed in the purchases brain names a value on the sites file: the
    readback that confirms it is saved in the sites brain too, so that brain's
    counted numbers apply it."""
    import sb
    from sheetbrain import findings, rules
    monkeypatch.setenv("SPREADSHEET_BRAIN_HOME", str(tmp_path / "home"))
    p1, _p2, _vendors = _two_files(tmp_path)
    rng = random.Random(27)
    sites = [["Site", "Date", "Amount"]] + [[["A1", "A2", "B", "Q7", "Q70"][k % 5],
                                             dt.datetime(2031, 1, 1) + dt.timedelta(days=k % 300),
                                             round(rng.uniform(10, 90), 2)] for k in range(150)]
    p3 = _book(tmp_path / "sites.xlsx", [{"name": "Sites", "rows": sites}])
    ctx = sb.Ctx([p1, p3])
    a = ctx.a
    b1, b3 = ctx.f(p1)["brain_id"], ctx.f(p3)["brain_id"]
    tid = next(t.tid for t in a.tables if a.file_of[t.tid] == p3)
    ctx.store.save_answer(b1, "codes_site", {"options": [], "labels": [], "not_sure": False, "kind": "definition",
                                             "text": "Q7 is my sister's stand; leave Q7 out of every total",
                                             "prompt": "What do the codes in Site mean?", "header": "Codes"})
    answers = sb.answers_for(ctx, b1)
    rb = findings.readback(a, answers)
    assert rb is not None and rb.meta["tables"] == [tid], rb and rb.meta
    assert interview.build_question(a, answers) is not None
    state = dict(ctx.store.state(b1), readback=rb.id)
    with pytest.raises(SystemExit):          # it prints the next step and exits, as the command does
        sb._answer_build(ctx, a, b1, state, answers, "1a 2a")
    mine = sb.answers_for(ctx, b3)
    assert rb.id in mine and [r.table for r in rules.confirmed(a, mine)] == [tid], mine.keys()


def test_a_summary_tab_with_no_row_for_a_value_is_counted(tmp_path):
    rng = random.Random(22)
    cats = synth._names(rng, 5)
    rows = [["Date", "Category", "Amount"]]
    for k in range(200):
        rows.append([dt.datetime(2031, 1, 1) + dt.timedelta(days=k), cats[k % 5], round(rng.uniform(10, 90), 2)])
    left = cats[4]
    summary = [["Category", "Total", "Lines"]]
    for i, c in enumerate(cats[:4]):
        tot = round(sum(r[2] for r in rows[1:] if r[1] == c), 2)
        summary.append([c, synth.Formula(f"=SUMIFS(Data!C:C,Data!B:B,A{i + 2})", tot),
                        synth.Formula(f"=COUNTIFS(Data!B:B,A{i + 2})", 40)])
    path = _book(tmp_path / "summary.xlsx", [{"name": "Data", "rows": rows}, {"name": "Summary", "rows": summary}])
    a = Analysis([path])
    found = _found(a, "structure:labels:")
    assert len(found) == 1 and found[0]["numbers"]["missing"] == [left] and found[0]["numbers"]["rows"] == 40, found
    assert f"none for {left} (40 rows" in found[0]["statement"], found[0]["statement"]
    assert "never pick it up" in found[0]["statement"] and found[0]["numbers"]["never"]
    # twin: a grand total that reads the whole Amount column does pick the value up; the tab only lacks its row
    grand = round(sum(r[2] for r in rows[1:]), 2)
    summary.append(["Total", synth.Formula("=SUM(Data!C:C)", grand), synth.Formula("=COUNTA(Data!B:B)-1", 200)])
    path = _book(tmp_path / "summary_total.xlsx", [{"name": "Data", "rows": rows},
                                                   {"name": "Summary", "rows": summary}])
    found = _found(Analysis([path]), "structure:labels:")
    assert len(found) == 1 and f"none for {left} (40 rows" in found[0]["statement"], found
    assert "never" not in found[0]["statement"] and not found[0]["numbers"]["never"], found[0]["statement"]


def test_a_join_that_needs_spellings_set_aside_names_the_pairs(tmp_path):
    rng = random.Random(23)
    vendors = synth._names(rng, 5)
    lines = [["Date", "Vendor", "Amount"]] + [[dt.datetime(2031, 1, 1) + dt.timedelta(days=k), vendors[k % 5],
                                               round(rng.uniform(10, 90), 2)] for k in range(150)]
    ref = [["Vendor", "Rep"]] + [[v.upper() if i == 0 else v, synth._word(rng)] for i, v in enumerate(vendors)]
    path = _book(tmp_path / "spell.xlsx", [{"name": "Lines", "rows": lines}, {"name": "Vendors", "rows": ref}])
    a = Analysis([path])
    found = _found(a, "structure:join_variants:")
    assert len(found) == 1 and found[0]["numbers"]["rows"] == 30 and found[0]["numbers"]["table"] == a.tables[0].tid
    assert f"'{vendors[0]}' and '{vendors[0].upper()}'" in found[0]["statement"], found[0]["statement"]


def test_a_list_written_another_way_is_one_counted_fact_on_every_seed(tmp_path):
    """1 or 2 parties written with other capitals, a closing period or leading
    spaces on the list: one fact naming each pair and counting the lines that
    use them. Written the same on both (the twin): nothing."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "join_variants")
        p = m["plants"][0]
        found = _found(a, "structure:join_variants:")
        hits += len(found) == 1 and found[0]["numbers"]["rows"] == len(p["rows"]) \
            and sorted(map(tuple, found[0]["numbers"]["pairs"])) == sorted(map(tuple, p["pairs"])) \
            and found[0]["numbers"]["col"] == p["col"]
        _tw, b = _built(tmp_path, seed, "join_variants", twin=True)
        assert not _found(b, "structure:join_variants:"), seed
    assert hits >= NEEDED, hits


def test_a_summary_missing_categories_is_one_counted_fact_on_every_seed(tmp_path):
    """A summary of SUMIFS by its own labels with no row for 1 or 2 categories:
    one fact naming them, their lines, and that its totals never pick them up.
    A row for every category (the twin): nothing."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "summary_labels")
        p = m["plants"][0]
        found = _found(a, "structure:labels:")
        hits += len(found) == 1 and sorted(found[0]["numbers"]["missing"]) == sorted(p["missing"]) \
            and found[0]["numbers"]["rows"] == p["lines"] and found[0]["numbers"]["never"] \
            and "never pick" in found[0]["statement"]
        _tw, b = _built(tmp_path, seed, "summary_labels", twin=True)
        assert not _found(b, "structure:labels:"), seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# nothing else is asked or found
# --------------------------------------------------------------------------
def test_no_other_plant_and_no_twin_gets_a_reference_question(tmp_path):
    """Codes, boundaries, copies, groups, journals and panels hold no reference
    table with dates, a list price or a charge that starts for one group: none
    of them gets a new finding, and no twin does either. The sweep takes the
    first 5 seeds of every trap."""
    own = {"terms_window": ("window:", "structure:versions:"),
           "list_price": ("listprice:", "listprice_all:", "offlist:"),
           "fee_onset": ("onset:",), "price_trend": ("price_trend:",),
           # practice round 3: the list price book with text prices or terms that name a month, partners
           # outside their dates on two sides, and prices the same at every place
           "list_text": ("listprice:", "listprice_all:", "offlist:"),
           "list_terms": ("listprice:", "listprice_all:", "offlist:"), "terms_sides": ("window:",),
           "price_parity": ("price_trend:",)}
    for name in synth.TRAPS:
        for twin in (False, True):
            for seed in SEEDS[:5]:
                m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
                a = Analysis([m["path"]])
                got = [i["recipe"] for i in a.insights if i["recipe"].startswith(RECIPES)
                       and (twin or not i["recipe"].startswith(own.get(name, ("-",))))]
                assert not got, (name, twin, seed, got)
                if twin or name not in own:
                    assert not _asks(a), (name, twin, seed)


def test_no_other_plant_and_no_twin_is_read_as_a_panel_or_as_entries(tmp_path):
    """Stock read on one date and entries that net to zero change what counts, so
    they come only where one is planted. A register of one row per person per
    date may be stated as such (it is true), but with no stock column in it; a
    date cycle is a plain fact of the dates and may be stated on any trap. The
    journals of rank 21 are balanced entries, plants and twins alike, and say so."""
    journals = {"fixed_ratio", "opposite_side", "opening_entry", "journal_three", "closing_entry", "chart_side",
                "opening_ties", "contra_split", "contra_vendor", "books_moves"}      # the last three: practice round 4
    for name in synth.TRAPS:
        for twin in (False, True):
            for seed in SEEDS[:5]:
                m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
                a = Analysis([m["path"]])
                if name in journals:
                    assert a.balanced, (name, twin, seed)
                elif twin or name != "balanced_entries":
                    assert not a.balanced, (name, twin, seed, [g["statement"] for g in a.grain_facts])
                if twin or name not in ("snapshot_panel", "count_panel", "panel_keys"):
                    assert not [s for s in a.snapshots.values() if s["stock"]], (name, twin, seed)


def test_the_noise_books_get_no_reference_question(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert not [i["recipe"] for i in a.insights if i["recipe"].startswith(RECIPES)], paths
        assert not _asks(a), paths
        assert not a.snapshots and not a.balanced, paths
