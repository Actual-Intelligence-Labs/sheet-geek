"""Practice round 3: what is found, which questions are asked, how they rank,
and what they say and offer.

Each detector fires on its synthetic plant on 19 of 20 seeds or more and stays
silent on its null twin on every seed; the noise books get none of the new
questions. Every book here is synthetic (tests/synth.py) or built in the test;
none is a development workbook."""
import datetime as dt
import math
import os
import re
import sys
import types

import pytest

openpyxl = pytest.importorskip("openpyxl")
xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import findings, interview, recipes  # noqa: E402
from sheetbrain.analyze import Analysis, _range_words  # noqa: E402
from sheetbrain.profile import norm_key  # noqa: E402
from sheetbrain.rules import Rule  # noqa: E402
from test_detectors import _contract  # noqa: E402

NEEDED = math.ceil(0.95 * len(SEEDS))
# what round 3 added: its recipes and its questions (a confirm of what code counted is not a detector)
NEW_RECIPES = ("unitgroup:", "unpaid:", "period:", "nonstock:")
NEW_ASKS = ("find_unit_", "find_unpaid_", "find_period_", "find_nonstock_", "find_lookup_", "find_known_",
            "find_drivers_", "follow_items_", findings.REBATE, interview.UNIFORM)
# the driver readback reads back the inputs of any formula model (a model twin has them too): a readback of
# what the file says, never a finding, so only the noise books must be without it
FOUND_ASKS = tuple(x for x in NEW_ASKS if x != "find_drivers_")
_BOOKS: dict = {}


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    """(manifest, analysis) of a trap on a seed, built once per module."""
    folder = tmp_path_factory.mktemp("r3")

    def get(name, seed, twin=False):
        key = (name, seed, twin)
        if key not in _BOOKS:
            m = synth.build(folder / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
            _BOOKS[key] = (m, Analysis([m["path"]]))
        return _BOOKS[key]
    return get


def _cands(a, answers=None):
    return interview.candidates(a, answers or {})


def _asked(a, prefix, answers=None):
    return [q for q in _cands(a, answers) if q.id.startswith(prefix)]


def _found(a, prefix):
    return [i for i in a.insights if i.get("recipe", "").startswith(prefix)]


def _sheet_of(a, tid):
    return next(t.sheet for t in a.tables if t.tid == tid)


def _write(path, sheets: dict) -> str:
    wb = xlsxwriter.Workbook(str(path))
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if isinstance(v, dt.datetime):
                    ws.write_datetime(r, c, v, wb.add_format({"num_format": "yyyy-mm-dd"}))
                elif v is not None:
                    ws.write(r, c, v)
    wb.close()
    return str(path)


def _silent(a, asks=FOUND_ASKS):
    """No round-3 recipe and no round-3 question on this book."""
    return not _found(a, NEW_RECIPES) and not _asked(a, asks)


# --------------------------------------------------------------------------
# fix 12: a group paid in another unit on quantity x rate = money
# --------------------------------------------------------------------------
def test_a_group_paid_per_piece_gets_one_unit_question(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("piece_rate", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_unit_")
        if len(qs) != 1:
            continue
        q = qs[0]
        ab = q.meta["about"]
        ok = (_sheet_of(a, ab["table"]), ab["col"], ab["aspect"], ab["values"]) == (p["sheet"], p["col"], "unit",
                                                                                    [p["value"]])
        ok = ok and p["value"] in q.prompt and "whole numbers" in q.prompt and re.search(r"\d+ rows", q.prompt)
        ok = ok and [o["id"] for o in q.options] == ["same", "other"]
        ok = ok and q.meta["unit_group"]["predicate"] == [{"col": p["group"], "op": "in", "values": [p["value"]]}]
        _contract(a, q)
        hits += bool(ok)
    assert hits >= NEEDED, hits


def test_a_part_time_or_senior_group_gets_no_unit_question(book):
    kinds = set()
    for seed in SEEDS:
        m, a = book("piece_rate", seed, True)
        kinds.add(m["plants"][0]["variant"])
        assert not _found(a, "unitgroup:") and not _asked(a, "find_unit_"), (seed, m["plants"][0]["variant"])
    if len(SEEDS) >= 6:
        assert kinds == {"part", "senior"}


# --------------------------------------------------------------------------
# fix 13: an odd group renamed mid-period, with more evidence, asked as scope
# --------------------------------------------------------------------------
def test_an_odd_branch_renamed_mid_period_is_one_scope_question_under_both_names(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("branch_scope", seed)
        p = m["plants"][0]
        qs = [q for q in _asked(a, "find_odd_") if q.meta["about"]["col"] == p["col"]]
        if len(qs) != 1:
            continue
        q = qs[0]
        both = {norm_key(p["value"]), norm_key(p["also"])}
        rule = q.meta["rules"]["leave_out"]
        # round 4 fix 6: a joined tab gets its own leave-out rule, listed after the one on this tab
        rule = rule[0] if isinstance(rule, list) else rule
        assert rule["table"] == q.meta["about"]["table"]
        ok = {norm_key(v) for v in q.meta["about"]["values"]} == both
        ok = ok and p["value"] in q.prompt and p["also"] in q.prompt and "no other tab" in q.prompt
        ok = ok and {norm_key(v) for v in rule["predicate"][0]["values"]} == both
        ok = ok and [o["id"] for o in q.options] == ["count", "leave_out"]
        _contract(a, q)
        hits += bool(ok)
    assert hits >= NEEDED, hits


def test_a_busy_branch_with_every_job_is_not_an_odd_group(book):
    for seed in SEEDS:
        _m, a = book("branch_scope", seed, True)
        assert not _found(a, "oddgroup:") and not _asked(a, "find_odd_"), seed


# --------------------------------------------------------------------------
# fix 14: a cycle read by rows, and whether the title's period is complete
# --------------------------------------------------------------------------
def test_a_pay_cycle_read_by_rows_asks_whether_the_period_is_complete(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("pay_cycle_title", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_period_")
        if len(qs) != 1:
            continue
        q = qs[0]
        last = dt.datetime.fromisoformat(p["last"])
        words = f"{last.strftime('%b')} {last.day}, {last.year}"
        ok = q.recommend == "complete" and "every 14 days on a Friday" in q.prompt and "off that cycle" in q.prompt
        ok = ok and words in next(o["label"] for o in q.options if o["id"] == "complete")
        ok = ok and q.meta["about"]["col"] == p["col"]
        _contract(a, q)
        hits += bool(ok)
    assert hits >= NEEDED, hits


def test_scattered_runs_or_a_last_run_on_the_titles_end_ask_nothing(book):
    kinds = set()
    for seed in SEEDS:
        m, a = book("pay_cycle_title", seed, True)
        kinds.add(m["plants"][0]["variant"])
        assert not _asked(a, "find_period_"), (seed, m["plants"][0]["variant"])
    if len(SEEDS) >= 6:
        assert kinds == {"scattered", "ends"}


# --------------------------------------------------------------------------
# fix 15: opening and carried-in balances among ties on the first date
# --------------------------------------------------------------------------
def test_an_opening_entry_is_found_among_other_entries_on_the_first_date(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("opening_ties", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_opening_")
        hits += len(qs) == 1 and qs[0].meta["about"]["values"] == [p["value"]]
        _m, b = book("opening_ties", seed, True)
        assert not _found(b, "opening:") and not _asked(b, "find_opening_"), seed
    assert hits >= NEEDED, hits


def test_rows_that_carry_balances_in_are_asked_as_the_opening(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("carried_in", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_opening_")
        if len(qs) == 1:
            q = qs[0]
            hits += (q.meta["about"]["col"], q.meta["about"]["values"]) == (p["col"], [p["value"]]) \
                and p["said"] in q.prompt and "first date" in q.prompt
            _contract(a, q)
        _m, b = book("carried_in", seed, True)
        assert not _found(b, "opening:") and not _asked(b, "find_opening_"), seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 16: renamed values at a switch, and the seam between two exports
# --------------------------------------------------------------------------
def test_statuses_renamed_at_a_switch_are_named_in_the_status_question(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("status_switch", seed)
        p = m["plants"][0]
        qs = [q for q in _asked(a, "codes_") if q.meta["about"]["col"] == p["col"]]
        hits += len(qs) == 1 and all(f"{old} -> {new}" in qs[0].prompt for old, new in p["pairs"])
        _m, b = book("status_switch", seed, True)
        assert not _found(b, ("boundary:", "handoff:")) and not a.handoffs.get((None, None)), seed
        assert not [q for q in _asked(b, "codes_") if "->" in q.prompt], seed
    assert hits >= NEEDED, hits


def test_two_stacked_exports_name_the_second_header_row_and_the_total_rows(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("two_exports", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_boundary_")
        if len(qs) == 1:
            q = qs[0]
            hits += f"A second header row sits at row {p['header_row']}" in q.prompt \
                and "ends with a total row" in q.prompt and all(str(r) in q.prompt for r in p["total_rows"])
        _m, b = book("two_exports", seed, True)
        assert not _found(b, "boundary:") and not _asked(b, "find_boundary_"), seed
    assert hits >= NEEDED, hits
    # a switch with no seam says nothing of one
    _m, a = book("system_change", SEEDS[0])
    assert all("second header row" not in q.prompt for q in _asked(a, "find_boundary_"))


# --------------------------------------------------------------------------
# fix 17: an ID charged every period that never pays
# --------------------------------------------------------------------------
def test_a_holder_charged_every_month_who_never_pays_is_asked(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("unpaid_ledger", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_unpaid_")
        if len(qs) != 1:
            continue
        q = qs[0]
        hits += q.meta["about"]["values"] == [p["value"]] and "never pays" in q.prompt \
            and [o["id"] for o in q.options] == ["not_owed", "owed", "type"]
        _contract(a, q)
    assert hits >= NEEDED, hits


def test_a_holder_who_pays_in_one_lump_is_not_asked(book):
    for seed in SEEDS:
        _m, a = book("unpaid_ledger", seed, True)
        assert not _found(a, "unpaid:") and not _asked(a, "find_unpaid_"), seed


# --------------------------------------------------------------------------
# fix 18: a status that lines up with another column's blanks
# --------------------------------------------------------------------------
def test_a_status_that_is_exactly_the_rows_with_no_rent_is_framed_on_rent(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("status_roster", seed)
        p = m["plants"][0]
        qs = [q for q in _asked(a, "codes_") if q.meta["about"]["col"] == p["col"]]
        hits += len(qs) == 1 and qs[0].prompt.startswith(f"Only {p['value']} in {p['col']}") \
            and "no Actual Rent" in qs[0].prompt and [o["id"] for o in qs[0].options] == ["all_but", "type"]
        _m, b = book("status_roster", seed, True)
        assert findings.code_dossiers(b, {}) == [], seed
    assert hits >= NEEDED, hits


def test_blanks_spread_over_the_statuses_keep_the_plain_status_question(tmp_path):
    rows = [["Unit", "Holder ID", "Status", "Market Rent", "Actual Rent"]]
    for k in range(100):
        st = "Notice" if k % 20 == 7 else "Vacant" if k % 20 == 11 else "Current"
        blank = k % 10 == 3
        rows.append([f"{100 + k}", None if blank else f"T{1000 + k}", st, 1000.0 + k, None if blank else 950.0 + k])
    a = Analysis([_write(tmp_path / "roster.xlsx", {"Roster": rows})])
    q = next(q for q in findings.code_dossiers(a, {}) if q.meta["about"]["col"] == "Status")
    assert q.prompt.startswith("Which Status values") and not q.prompt.startswith("Only")


# --------------------------------------------------------------------------
# fix 19: a blank input column, asked about as the input
# --------------------------------------------------------------------------
def test_a_blank_quantity_is_asked_about_and_names_the_amount_it_makes(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("measure_coblank", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_blankm_")
        if len(qs) != 1:
            continue
        q = qs[0]
        hits += q.meta["about"]["col"] == p["qty"] and f"What does a blank {p['qty']} mean?" in q.prompt \
            and p["col"] in q.prompt and "Not counted that period: count it as zero" in [o["label"] for o in q.options]
    assert hits >= NEEDED, hits


def test_a_blank_amount_beside_a_filled_quantity_is_asked_about_the_amount(tmp_path):
    rows = [["Date", "Ref", "Site", "Qty", "Price", "Amount"]]
    for k in range(400):
        q, pr = 1 + k % 6, 5.0 + k % 4
        blank = 200 <= k < 209
        rows.append([dt.datetime(2024, 1, 1) + dt.timedelta(days=k // 3), f"R-{1000 + k}",
                     ["North", "South", "East"][k % 3] if not blank else "North", q, pr, None if blank else q * pr])
    a = Analysis([_write(tmp_path / "coblank.xlsx", {"Log": rows})])
    qs = _asked(a, "find_blankm_")
    assert qs and qs[0].meta["about"]["col"] == "Amount", [q.prompt for q in qs]


# --------------------------------------------------------------------------
# fix 20: a lookup whose columns classify the rows
# --------------------------------------------------------------------------
def test_a_chart_that_gives_each_account_its_side_gets_one_meaning_question(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("chart_side", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_lookup_")
        if len(qs) == 1:
            q = qs[0]
            # practice round 4, fix 20: asked by the list's role, with a yes an owner who knows its name can pick
            hits += p["side"] in q.prompt and [o["id"] for o in q.options] == ["yes", "partly", "reference"] \
                and q.options[0]["label"].startswith("Yes, it is the ")
            _contract(a, q)
        _m, b = book("chart_side", seed, True)
        assert not _asked(b, "find_lookup_"), seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 21: a category a list gives no cost, carried to the lines
# --------------------------------------------------------------------------
def test_a_category_the_list_gives_no_cost_is_asked_on_the_lines(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("gift_cards", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_nonstock_")
        if len(qs) != 1:
            continue
        q = qs[0]
        rule = q.meta["rules"]["leave"]
        ok = _sheet_of(a, q.meta["about"]["table"]) == p["sheet"] and q.meta["about"]["col"] == p["col"]
        ok = ok and q.multi and p["category"] in q.prompt and "never a Discount" in q.prompt
        # the rule names the codes as the list writes them
        ok = ok and all(v == v.upper() for v in rule["predicate"][0]["values"]) and len(rule["predicate"][0]["values"]) == 3
        _contract(a, q)
        hits += bool(ok)
    assert hits >= NEEDED, hits


def test_blank_costs_spread_over_categories_are_not_asked(book):
    for seed in SEEDS:
        _m, a = book("gift_cards", seed, True)
        assert not _found(a, "nonstock:") and not _asked(a, "find_nonstock_"), seed


# --------------------------------------------------------------------------
# fix 5: what an answer moves, measured where the rows carry money
# --------------------------------------------------------------------------
def test_a_status_on_a_product_list_is_worth_the_money_of_the_lines_it_touches(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("lookup_status", seed)
        p = m["plants"][0]
        qs = [q for q in _asked(a, "codes_") if q.meta["about"]["col"] == p["col"]]
        if len(qs) == 1:
            q = qs[0]
            hits += _sheet_of(a, q.meta.get("through") or "") == p["sheet"] \
                and abs(q.meta["stake"] - p["share"]) < 0.002 and q.meta["stake"] < 0.1
        _m, b = book("lookup_status", seed, True)
        assert findings.code_dossiers(b, {}) == [], seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 7: a growth rate beside a sibling flow off the same stock
# --------------------------------------------------------------------------
def test_a_growth_rate_beside_a_churn_rate_is_asked_with_the_churn(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("model_flows", seed)
        p = m["plants"][0]
        q = next((q for q in _cands(a) if q.id == "growth_period"), None)
        if q is None:
            continue
        hits += p["other"] in q.prompt and "subtracts" in q.prompt and q.recommend == "before" \
            and [o["id"] for o in q.options] == ["before", "net", "yearly"]
        _m, b = book("model_flows", seed, True)
        tq = next((q for q in _cands(b) if q.id == "growth_period"), None)
        assert tq is None or not [o for o in tq.options if o["id"] == "net"], seed
    assert hits >= NEEDED, hits


def test_a_model_with_many_inputs_reads_them_back_in_one_question(book):
    hits = 0
    for seed in SEEDS:
        _m, a = book("model_plan", seed)
        qs = _asked(a, "find_drivers_")
        if len(qs) == 1:
            q = qs[0]
            hits += q.prompt.startswith("These inputs drive the model:") and len(q.options) == 2 \
                and len(interview._ident(q.meta["about"]) or ()) >= 2
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 8: one-tap confirms of what code counted
# --------------------------------------------------------------------------
def test_a_counted_key_that_disagrees_with_the_panel_puts_the_row_confirm_in_a_round(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("panel_keys", seed)
        q = next((q for q in _cands(a) if q.id == interview.CONFIRM), None)
        hits += q is not None and interview.in_round(q) and m["plants"][0]["key"] in q.prompt
        _m, b = book("panel_keys", seed, True)
        tq = next((q for q in _cands(b) if q.id == interview.CONFIRM), None)
        assert tq is None or not interview.in_round(tq), seed
    assert hits >= NEEDED, hits


def test_a_price_the_same_at_every_place_gets_a_one_tap_confirm(book):
    hits = 0
    for seed in SEEDS:
        _m, a = book("price_parity", seed)
        q = next((q for q in _cands(a) if q.id == interview.UNIFORM), None)
        hits += q is not None and q.recommend == "right" and not interview.in_round(q)
        _m, b = book("price_parity", seed, True)
        assert not [q for q in _cands(b) if q.id == interview.UNIFORM], seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 9: small 'known issue or news?' findings asked as one
# --------------------------------------------------------------------------
def _small(qid, value, about, clause):
    q = interview.Q(qid, "Small", f"{clause}. A known issue, or news to you?",
                    [{"id": "known", "label": "Known", "desc": "Known"}, {"id": "news", "label": "News", "desc": "News"}],
                    kind="history", source="finding", meta={"about": about, "clause": clause})
    q.value = value
    return q


def test_small_known_issue_findings_are_asked_as_one_question(book):
    _m, a = book("list_price", SEEDS[0])
    t = a.tables[0]
    col = t.headers[1]
    qs = [_small("find_offlist_a", 1.2, {"table": t.tid, "col": col, "aspect": "meaning", "values": ["A"]},
                 "4 A lines above the list"),
          _small("find_onset_b", 1.1, {"table": t.tid, "col": col, "aspect": "meaning", "values": ["B"]},
                 "A fee starts for B"),
          _small("find_window_c", 1.0, {"table": t.tid, "col": col, "aspect": "meaning", "values": ["C"]},
                 "3 lines outside their dates")]
    out = findings.batch_known(a, qs, {})
    assert len(out) == 1 and out[0].id.startswith(findings.KNOWN) and out[0].multi
    q = out[0]
    assert q.meta["members"] == ["find_offlist_a", "find_onset_b", "find_window_c"]
    assert set(q.meta["member_about"]) == {"known_1", "known_2", "known_3"}
    assert len(interview._options_for(q)) == 4 and interview._options_for(q)[-1]["id"] == "not_sure"
    # one small finding alone stays itself
    assert findings.batch_known(a, qs[:1], {}) == qs[:1]
    # practice round 4, fix 10: which total is right decides a treatment, so it is asked alone, never batched
    prod = _small("find_product_d", 1.0, {"table": t.tid, "col": col, "aspect": "meaning", "values": ["D"]},
                  "3 rows where the line total is off")
    assert prod in findings.batch_known(a, qs[:2] + [prod], {})


# --------------------------------------------------------------------------
# fix 26: questions the owner can answer
# --------------------------------------------------------------------------
def test_partners_outside_their_dates_on_two_sides_are_asked_one_at_a_time(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("terms_sides", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_window_")
        if len(qs) != 1:
            continue
        q = qs[0]
        named = [v for v in p["values"] if v in q.prompt]
        if len(named) != 1:
            continue
        ans = interview.parse_answers([q], "1a")
        nxt = _asked(a, "find_window_", ans)
        other = [v for v in p["values"] if v not in named][0]
        hits += len(nxt) == 1 and other in nxt[0].prompt and named[0] not in nxt[0].prompt
        _contract(a, q)
    assert hits >= NEEDED, hits
    for seed in SEEDS:
        _m, b = book("terms_sides", seed, True)
        assert not _found(b, "window:") and not _asked(b, "find_window_"), seed


def test_a_person_on_the_same_rows_as_a_site_is_folded_into_the_sites_question(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("person_site", seed)
        p = m["plants"][0]
        cands = _cands(a)
        alone = [q for q in cands if q.id.startswith("find_odd_") and q.meta["about"]["col"] == p["col"]]
        site = [q for q in cands if (q.meta.get("about") or {}).get("col") == p["columns"][0]]
        hits += not alone and len(site) == 1 and p["value"] in site[0].prompt and "same rows" in site[0].prompt
        _m, b = book("person_site", seed, True)
        assert not [q for q in _cands(b) if p["value"] in q.prompt or q.id.startswith("find_odd_")], seed
    assert hits >= NEEDED, hits


def test_a_code_column_of_eight_values_or_fewer_names_every_one(book):
    for seed in SEEDS[:5]:
        m, a = book("code_minority", seed)
        p = m["plants"][0]
        q = next(q for q in _asked(a, "codes_") if q.meta["about"]["col"] == p["col"])
        assert len(p["values"]) > 8 or all(re.search(rf"\b{v}: ", q.prompt) for v in p["values"]), (seed, q.prompt)
        assert len(p["values"]) > 8 or " more" not in q.prompt


def test_not_sure_keeps_its_place_and_a_fourth_option_is_named_to_type():
    q = interview.Q("x", "Pick", "Which one?", [{"id": k, "label": f"Option {k}", "desc": ""} for k in "abcd"])
    ask = interview.render_ask([q])["questions"][0]
    assert [o["label"] for o in ask["options"]] == ["Option a", "Option b", "Option c", "Not sure"]
    assert 'Or choose Other and type "Option d".' in ask["question"]
    q3 = interview.Q("y", "Pick", "Which one?", [{"id": k, "label": f"Option {k}", "desc": ""} for k in "abc"])
    ask3 = interview.render_ask([q3])["questions"][0]
    assert ask3["options"][-1]["label"] == "Not sure" and "Or choose Other" not in ask3["question"]


def test_a_taxonomy_question_offers_something_else_to_type():
    import json
    with open(os.path.join(ROOT, "skills", "sheet-geek", "playbooks", "ledger.json")) as f:
        pb = json.load(f)
    q = next(q for q in pb["questions"] if q["id"] == "class_meaning")
    assert len(q["options"]) <= 3 and q["options"][-1]["label"] == "Something else (type it)"


# --------------------------------------------------------------------------
# fix 1 and fix 4: prompts carry the context an answer confirms; options say what a thing is
# --------------------------------------------------------------------------
def test_prices_typed_as_text_are_said_in_the_off_list_question(book):
    hits = 0
    for seed in SEEDS:
        _m, a = book("list_text", seed)
        qs = _asked(a, "find_offlist_")
        hits += len(qs) == 1 and "typed as text" in qs[0].prompt
        _m, b = book("list_text", seed, True)
        assert not _found(b, ("listprice:", "offlist:")) and not _asked(b, ("find_offlist_", "find_listscope_"))
    assert hits >= NEEDED, hits
    _m, a = book("list_price", SEEDS[0])
    assert all("typed as text" not in q.prompt for q in _asked(a, "find_offlist_"))


def test_terms_that_name_the_month_recommend_that_the_partner_reprices(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("list_terms", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_listscope_")
        hits += len(qs) == 1 and qs[0].recommend == "reprices" and p["terms"] in qs[0].prompt
        _m, b = book("list_terms", seed, True)
        assert not _asked(b, ("find_offlist_", "find_listscope_")), seed
    assert hits >= NEEDED, hits
    _m, a = book("list_price", SEEDS[0])
    assert all(q.recommend != "reprices" for q in _asked(a, "find_listscope_"))


def test_a_list_scope_question_names_the_column_it_matched(book):
    """The question and its clause start with the matched column's name, never with
    '{}' or a title-period dict (a bug from 0.2.0 to 0.2.1)."""
    hits = 0
    for seed in SEEDS:
        m, a = book("list_terms", seed)
        p = m["plants"][0]
        q = next(iter(_asked(a, "find_listscope_")), None)
        hits += (q is not None and q.prompt.startswith(f"{p['col']} on ")
                 and q.meta["clause"].startswith(f"{p['col']} on "))
    assert hits >= NEEDED, hits


def test_a_title_period_and_a_short_range_are_one_question(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("title_short", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_short_")
        if len(qs) != 1:
            continue
        q = qs[0]
        hits += "title says" in q.prompt and f"only to row {p['range_end']}" in q.prompt \
            and f"{len(p['late'])} rows" in q.prompt
        _m, b = book("title_short", seed, True)
        assert not _asked(b, "find_short_"), seed
    assert hits >= NEEDED, hits


def test_a_test_id_on_a_roster_offers_to_leave_it_out_of_every_tab(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("sentinel_roster", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_outliers_")
        if len(qs) != 1:
            continue
        q = qs[0]
        test = next((o for o in q.options if o["id"] == "test"), None)
        # practice round 4, fix 4: its rows carry money, so whether any went out is its own pick
        hits += p["value"] in q.prompt and test is not None and "every tab" in test["desc"] \
            and [o["id"] for o in q.options] == ["test", "paid", "keep"]
        _contract(a, q)
        _m, b = book("sentinel_roster", seed, True)
        assert not _asked(b, "find_outliers_"), seed
    assert hits >= NEEDED, hits


def test_replaced_codes_name_their_pairs_and_fee_codes_stay_apart(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("recoded_items", seed)
        p = m["plants"][0]
        qs = _asked(a, "find_unmatched_")
        if len(qs) != 1:
            continue
        q = qs[0]
        old = next(o for o in q.options if o["id"] == "old_codes")
        items = next(o for o in q.options if o["id"] == "not_items")
        hits += all(f"{x} -> {y}" in old["desc"] for x, y in p["pairs"]) \
            and all(f in items["desc"] for f in p["fees"]) and not any(x in items["desc"] for x, _y in p["pairs"])
        _m, b = book("recoded_items", seed, True)
        tq = _asked(b, "find_unmatched_")
        assert all("->" not in o.get("desc", "") for q2 in tq for o in q2.options), seed
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 28: blank slices leave out the category where the column never applies
# --------------------------------------------------------------------------
def test_a_blank_slice_leaves_out_the_category_that_is_always_blank(book):
    hits = 0
    for seed in SEEDS:
        m, a = book("blank_slice", seed)
        p = m["plants"][0]
        qs = [q for q in _asked(a, "find_blank_") if q.meta["about"]["col"] == p["col"]]
        hits += len(qs) == 1 and f"is {p['filled']}" in qs[0].prompt and f"of {p['slice_rows']} rows" in qs[0].prompt
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# fix 29: what a rebate is paid on, from the exclusions answered
# --------------------------------------------------------------------------
def test_the_rebate_base_lists_the_exclusions_the_owner_answered(tmp_path):
    for seed in SEEDS[:5]:
        m = synth.build_book(tmp_path / f"rebate{seed}.xlsx", seed, ["recoded_items", "terms_window"])
        a = Analysis([m["path"]])
        un = _asked(a, "find_unmatched_")[0]
        answers = interview.parse_answers([un], "1a")
        answers["find_negatives_x"] = {"options": ["credits"], "labels": ["Credits"], "text": ""}
        q = next(q for q in _cands(a, answers) if q.id == findings.REBATE)
        assert q.multi and [o["id"] for o in q.options] == ["net", "fees"], [o["id"] for o in q.options]
        fees = next(p for p in m["plants"] if p["trap"] == "recoded_items")["fees"]
        assert any(f in q.options[1]["desc"] for f in fees)
        b = Analysis([synth.build_book(tmp_path / f"norebate{seed}.xlsx", seed, ["recoded_items"])["path"]])
        un = _asked(b, "find_unmatched_")[0]
        answers = interview.parse_answers([un], "1a")
        answers["find_negatives_x"] = {"options": ["credits"], "labels": ["Credits"], "text": ""}
        assert not [q for q in _cands(b, answers) if q.id == findings.REBATE]


# --------------------------------------------------------------------------
# fix 2 and fix 25: question identity by values and aspect; closer answers route to open items
# --------------------------------------------------------------------------
def test_a_mapping_answer_settles_only_the_values_it_mapped():
    said = {("T", "Code", "meaning"): [(frozenset({"a", "b"}), True)]}
    inside = interview.Q("q1", "h", "p", [], meta={"about": {"table": "T", "col": "Code", "aspect": "meaning",
                                                              "values": ["A"]}})
    outside = interview.Q("q2", "h", "p", [], meta={"about": {"table": "T", "col": "Code", "aspect": "meaning",
                                                               "values": ["A", "C"]}})
    other_aspect = interview.Q("q3", "h", "p", [], meta={"about": {"table": "T", "col": "Code", "aspect": "unit",
                                                                    "values": ["A"]}})
    assert interview._is_settled(inside, said)
    assert not interview._is_settled(outside, said) and not interview._is_settled(other_aspect, said)
    plain = {("T", "Code", "meaning"): [(frozenset({"a"}), False)]}
    assert interview._is_settled(outside, plain)          # a plain answer about an overlapping value settles it


def test_a_closer_sentence_naming_an_open_items_value_routes_to_it(book):
    m, a = book("unpaid_ledger", SEEDS[0])
    who = m["plants"][0]["value"]
    q = _asked(a, "find_unpaid_")[0]
    answers = {interview.CLOSER: {"text": f"Our office manager lives in {who}. Nothing else to add.",
                                  "options": [], "not_sure": False}}
    routes = interview.closer_routes(a, answers)
    assert routes.get(q.id) == [f"Our office manager lives in {who}."]
    assert q.id not in [x.id for x in interview.open_items(a, {"answers": answers})]


def test_typed_words_that_name_every_value_confirm_the_map_offered():
    rule = Rule("map", "T", [{"col": "Site", "op": "in", "values": ["QXR", "Quarry Row"]}],
                values={"col": "Site", "to": {"qxr": "Quarry Row", "quarry row": "Quarry Row"}}).to_dict()
    q = interview.Q("follow_handoff_t_site", "Same?", "QXR stops and Quarry Row starts. Same thing?",
                    [{"id": "same", "label": "The same thing", "desc": ""}, {"id": "apart", "label": "Apart",
                                                                              "desc": ""}],
                    source="finding", meta={"rules": {"same": rule}, "map_text": {"option": "same",
                                                                                  "values": ["QXR", "Quarry Row"]}})
    got = interview._answer(q, [], "QXR became Quarry Row when we moved")
    assert [r["confirmed"] for r in got["rules"]] == [True]
    assert [r["confirmed"] for r in interview._answer(q, [], "QXR moved")["rules"]] == [False]


# --------------------------------------------------------------------------
# fix 10: an exclusion takes every spelling a confirmed map merged
# --------------------------------------------------------------------------
def test_a_confirmed_map_widens_a_confirmed_exclusion_to_every_spelling():
    mp = Rule("map", "T", [{"col": "Site", "op": "in", "values": ["QXR", "Quarry Row"]}],
              values={"col": "Site", "to": {"qxr": "Quarry Row", "quarry row": "Quarry Row"}}, confirmed=True)
    ex = Rule("exclude", "T", [{"col": "Site", "op": "in", "values": ["QXR"]}], confirmed=True)
    out = recipes._widen([mp, ex])
    assert sorted(out[1].predicate[0]["values"]) == ["QXR", "Quarry Row"]
    loose = Rule("map", "T", mp.predicate, values=mp.values, confirmed=False)
    assert recipes._widen([loose, ex])[1].predicate[0]["values"] == ["QXR"]


# --------------------------------------------------------------------------
# fix 24: the owner's date wins, and a sign-only change is not a unit change
# --------------------------------------------------------------------------
def test_the_date_the_owner_typed_replaces_the_detected_one():
    assert findings._owner_date("we switched on July 3, 2025 I think", "Jun 30, 2025") == "Jul 3, 2025"
    assert findings._owner_date("switched 7/3/2025", "Jun 30, 2025") == "Jul 3, 2025"
    assert findings._owner_date("new system then", "Jun 30, 2025") == "Jun 30, 2025"


def test_a_column_whose_only_change_is_its_sign_is_not_a_unit_change(book):
    for seed in SEEDS[:5]:
        _m, a = book("balanced_entries", seed)
        q = next((q for q in _asked(a, "find_boundary_")), None)
        if q is None:
            continue
        signed = [c["col"] for i in _found(a, "boundary:") for c in i["numbers"]["changes"] if c["kind"] == "sign"]
        if not signed:
            continue
        col = signed[0]
        first = next(o["id"] for o in q.options if o["id"] != "changed")
        plain = {q.id: dict(interview._answer(q, [first], f"{col} is the same amount"), about=q.meta["about"])}
        assert (q.meta["about"]["table"], col) not in findings.unit_changed(a, plain)
        unit = {q.id: dict(interview._answer(q, [first], f"{col} went to cents"), about=q.meta["about"])}
        assert (q.meta["about"]["table"], col) in findings.unit_changed(a, unit)


# --------------------------------------------------------------------------
# fix 30: wording
# --------------------------------------------------------------------------
def test_a_range_with_equal_ends_reads_on_that_day():
    d = dt.date(2025, 3, 4).toordinal()
    assert _range_words(d, d).startswith("on ") and " to " not in _range_words(d, d)
    assert " to " in _range_words(d, d + 3)


def test_a_date_column_with_one_day_says_it_is_that_day_on_every_row(tmp_path):
    rows = [["Count Date", "Item", "Qty", "Unit Cost", "Value"]]
    for k in range(60):
        rows.append([dt.datetime(2025, 3, 31), f"Item {k}", 1 + k % 9, 2.5, (1 + k % 9) * 2.5])
    a = Analysis([_write(tmp_path / "count.xlsx", {"Count": rows})])
    said = [i["statement"] for i in a.insights if i["recipe"].startswith("date_range:")]
    assert said and all("on every row" in s and " to " not in s for s in said), said


def test_a_pay_rate_is_paid_at_rates_and_a_selling_price_sold_at_prices():
    def ctx(pb_id, header, label=""):
        pb = {"id": pb_id, "roles": {header: {"label": label}}}
        return types.SimpleNamespace(pb=pb, label=lambda h: h)
    assert recipes._spread_verb(ctx("payroll_hr", "Rate", "pay rate"), "Rate") == "were paid at rates"
    assert recipes._spread_verb(ctx("sales_transactions", "Price"), "Price") == "were sold at prices"
    assert recipes._spread_verb(ctx("procurement", "Unit Cost"), "Unit Cost") == "were bought at prices"


# --------------------------------------------------------------------------
# the contract and the noise budget
# --------------------------------------------------------------------------
R3 = ("piece_rate", "branch_scope", "pay_cycle_title", "opening_ties", "carried_in", "status_switch", "two_exports",
      "unpaid_ledger", "status_roster", "gift_cards", "lookup_status", "model_flows", "panel_keys", "price_parity",
      "terms_sides", "person_site", "list_text", "list_terms", "title_short", "sentinel_roster", "recoded_items")


def test_every_round3_question_keeps_the_option_contract(book, synth_seed):
    for name in R3:
        _m, a = book(name, synth_seed)
        for q in _cands(a):
            shown = interview._options_for(q)
            if q.kind in ("goal", "build"):
                continue
            assert shown[-1]["id"] == "not_sure" and len(shown) - 1 <= 3, (name, q.id)
            if q.id.startswith(NEW_ASKS) and not q.meta.get("confirm"):
                _contract(a, q)


def test_no_null_twin_of_any_trap_gets_a_round3_question(tmp_path):
    """New detectors stay silent on every twin; the sweep over every trap takes
    the first 3 seeds (each round-3 trap's own twin is checked on every seed above)."""
    for name in synth.TRAPS:
        for seed in SEEDS[:3]:
            m = synth.build(tmp_path / f"{name}{seed}.xlsx", seed, name, twin=True)
            a = Analysis([m["path"]])
            assert _silent(a), (name, seed, [i["recipe"] for i in _found(a, NEW_RECIPES)],
                                [q.id for q in _asked(a, FOUND_ASKS)])
            # what a row is enters a round only where two counted readings disagree
            assert not [q for q in _cands(a) if q.id == interview.CONFIRM and interview.in_round(q)], (name, seed)


def test_the_noise_books_get_no_round3_question(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert _silent(a, NEW_ASKS), (paths, [q.id for q in _asked(a, NEW_ASKS)])
        # a confirm of what code counted only fills room left over there
        assert not [q for q in _cands(a) if q.meta.get("confirm") and interview.in_round(q)], paths
