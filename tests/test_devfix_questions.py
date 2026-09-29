"""Practice round 1 fixes (dev/v02 brief, sections C and D): questions that name
their evidence, one per value, the measure a code counts toward, mixed units,
room left filled with the playbook's questions about columns in the file, and
detection gaps (total rows found by their sums, a typed factor on a tied-out
row, copies across numbering families, case-only renames, the data's own date
span, a blank measure beside a blank count, a price for a case in a unit no
other group uses, dated reference rows). Every book here is synthetic
(tests/synth.py) or built in the test; each detector fires on its plant on 19
of 20 seeds or more and stays silent on its twin."""
import datetime as dt
import math
import os
import re
import sys

import pytest

pytest.importorskip("openpyxl")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import findings, interview, recipes  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from test_detectors import _contract  # noqa: E402

NEEDED = math.ceil(0.95 * len(SEEDS))


def _built(tmp_path, seed, name, twin=False):
    m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
    return m, Analysis([m["path"]])


def _qs(a, prefix, answers=None):
    return [q for q in interview.candidates(a, answers or {}) if q.id.startswith(prefix)]


def _write(path, sheets: dict) -> str:
    synth._write_xlsxwriter(str(path), [{"name": k, "rows": v} for k, v in sheets.items()])
    return str(path)


def _interview(a, answers=None, pick=lambda q: ["not_sure"]):
    state = {"answers": dict(answers or {}), "round": 0}
    asked = []
    while True:
        qs = interview.next_round(a, state)
        if not qs:
            break
        for q in qs:
            if q.kind == "goal":
                continue
            asked.append(q)
            state["answers"][q.id] = interview._answer(q, pick(q), "")
        state["round"] += 1
    return state, asked


# --------------------------------------------------------------------------
# D1: a total row after each part of a stacked export, found by its sums
# --------------------------------------------------------------------------
def _part_totals_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "part_totals")
    p = m["plants"][0]
    t = a.tables[0]
    facts = " ".join(i["statement"] for i in a.insights if i["recipe"] == "structure:totals_rows")
    return [r + 1 for r in t.totals_rows] == p["total_rows"] and t.n_rows == p["data_rows"] \
        and "2 total rows" in facts


def test_unlabeled_part_totals_are_found_by_one_money_column(tmp_path):
    """No label and a rate that is an average, not a sum: the pay equals the part's
    rows, and the last row the rows of both parts."""
    hits = sum(_part_totals_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_a_row_equal_to_the_running_sum_with_an_id_stays_data(tmp_path, synth_seed):
    m, a = _built(tmp_path, synth_seed, "part_totals", twin=True)
    t = a.tables[0]
    assert not t.totals_rows and m["plants"][0]["row_equal_to_sum"] - 1 in t.row_index


# --------------------------------------------------------------------------
# D2: a typed factor inside a formula on a row whose typed months were asked about
# --------------------------------------------------------------------------
def _factor_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "link_multiplier")
    p = next(x for x in m["plants"] if x["what"] == "multiplier")
    tie = next(iter(_qs(a, "find_tieout_")), None)
    hard = next(iter(_qs(a, "find_hardcoded")), None)
    if tie is None or hard is None or hard.meta["about"]["col"] != tie.meta["about"]["col"] \
            or p["cell"] not in hard.prompt:
        return False
    # the row's typed months answered: the factor inside one of its formulas is still asked
    answers = {tie.id: interview._answer(tie, [], "Some money the link leaves out.")}
    still = [q.id for q in interview.candidates(a, answers)]
    _state, asked = _interview(a, answers)
    return "find_hardcoded" in still and "find_hardcoded" in [q.id for q in asked]


def test_a_typed_factor_on_a_tied_out_row_is_still_asked(tmp_path):
    hits = sum(_factor_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_the_factor_answered_settles_it(tmp_path):
    m, a = _built(tmp_path, 0, "link_multiplier")
    hard = _qs(a, "find_hardcoded")[0]
    answers = {hard.id: interview._answer(hard, ["test"], "")}
    assert not _qs(a, "find_hardcoded", answers)


def test_a_model_with_nothing_planted_asks_about_no_factor(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "link_multiplier", twin=True)
    assert not _qs(a, "find_hardcoded") and not _qs(a, "find_tieout_")


# --------------------------------------------------------------------------
# D3: orders re-imported under a new numbering by a system that renamed its codes
# --------------------------------------------------------------------------
def _reimport_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "reimport_lines")
    p = m["plants"][0]
    found = [i for i in a.insights if i["recipe"].startswith("copies:")]
    qs = _qs(a, "find_copies_")
    if len(found) != 1 or len(qs) != 1:
        return False
    n = found[0]["numbers"]
    return n["rows"] == p["copies"] and n["col"] == p["col"] and p["families"][1] in qs[0].prompt \
        and n["window"][0] >= p["window"][0][:10] and n["window"][1] <= p["window"][1][:10]


def test_orders_loaded_twice_under_two_numberings_are_one_copies_question(tmp_path):
    """Several lines per order, a memo worded another way and a source code
    renamed by the new system: the copies are still found."""
    hits = sum(_reimport_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_invoices_and_their_payments_are_never_copies(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "reimport_lines", twin=True)
    assert not [i for i in a.insights if i["recipe"].startswith("copies:")] and not _qs(a, "find_copies_")


def test_the_copies_question_keeps_the_contract(tmp_path):
    for seed in SEEDS[:5]:
        _m, a = _built(tmp_path, seed, "reimport_lines")
        for q in _qs(a, "find_copies_"):
            _contract(a, q)


# --------------------------------------------------------------------------
# D4: a value only written in capitals from the switch date is in the list of renames
# --------------------------------------------------------------------------
def _case_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "case_rename")
    p = m["plants"][0]
    ho = next((i for i in a.insights if i["recipe"].startswith("handoff:") and i["numbers"]["col"] == p["col"]), None)
    if ho is None:
        return False
    pairs = [[x["old"], x["new"]] for x in ho["numbers"]["pairs"]]
    q = next(iter(_qs(a, "find_handoff_") + _qs(a, "codes_")), None)
    return all(x in pairs for x in p["pairs"]) and q is not None and p["pairs"][2][1] in q.prompt


def test_a_rename_in_capitals_only_is_in_the_same_list(tmp_path):
    hits = sum(_case_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_capitals_scattered_through_the_dates_are_no_rename(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "case_rename", twin=True)
    assert not [i for i in a.insights if i["recipe"].startswith(("handoff:", "boundary:"))]
    assert not _qs(a, "find_handoff_")


def _variants_ok(tmp_path, seed) -> bool:
    m = synth.build_files(tmp_path, seed, "file_variants")
    a = Analysis(m["paths"])
    qs = _qs(a, "alias_")
    if len(qs) != 1:
        return False
    q = qs[0]
    _contract(a, q)
    return all(f"'{x}' and '{y}'" in q.prompt or f"'{y}' and '{x}'" in q.prompt for x, y in m["plants"][0]["pairs"]) \
        and len(q.meta["tables"]) == 2


def test_a_name_two_files_write_in_other_capitals_is_one_alias_question(tmp_path):
    hits = sum(_variants_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_the_same_spelling_in_both_files_asks_nothing_and_one_book_keeps_a_fact(tmp_path, synth_seed):
    m = synth.build_files(tmp_path, synth_seed, "file_variants", twin=True)
    assert not _qs(Analysis(m["paths"]), "alias_")
    _m, a = _built(tmp_path, synth_seed, "file_variants")          # both tabs in one book: a counted fact only
    assert not _qs(a, "alias_") and [i for i in a.insights if i["recipe"].startswith("structure:join_variants:")]


# --------------------------------------------------------------------------
# D5: the data's span is the main table's own date, never a hire date beside it
# --------------------------------------------------------------------------
def _span_ok(tmp_path, seed, twin=False) -> bool:
    m, a = _built(tmp_path, seed, "hire_dates", twin=twin)
    p = m["plants"][0]
    first = next((i for i in a.insights if i["recipe"].startswith("date_range:")), None)
    return first is not None and first["numbers"]["min"] == p["first"][:10] and \
        first["numbers"]["max"] == p["last"][:10] and first["numbers"]["col"] == p["col"]


def test_the_span_comes_from_the_pay_dates_whatever_the_playbook_order(tmp_path):
    hits = sum(_span_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits
    assert all(_span_ok(tmp_path, seed, twin=True) for seed in SEEDS[:5])


# --------------------------------------------------------------------------
# D6: the measure blank on the same rows as the count it is worked out from
# --------------------------------------------------------------------------
def _coblank_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "measure_coblank")
    p = m["plants"][0]
    qs = _qs(a, "find_blankm_")
    if len(qs) != 1:
        return False
    q = qs[0]
    _contract(a, q)
    # round 3 fix 19: the amount is the quantity times the price, so the question is about the quantity the
    # blanks come from, and names the amount as following from it
    return q.meta["about"]["col"] == p["qty"] and f"{p['col']} = {p['qty']} x " in q.prompt \
        and f"{p['qty']} on " in q.prompt and f"blank on {len(p['rows'])} of" in q.prompt


def test_a_blank_measure_beside_a_blank_count_is_asked(tmp_path):
    hits = sum(_coblank_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_blanks_a_type_column_explains_stay_a_counted_fact(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "measure_coblank", twin=True)
    assert not _qs(a, "find_blankm_")
    assert [i for i in a.insights if i["recipe"].startswith("structure:blank_match:")]


# --------------------------------------------------------------------------
# D7: one group priced per case, in a unit no other group counts in
# --------------------------------------------------------------------------
def _case_price_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "case_price")
    p = m["plants"][0]
    qs = [q for q in _qs(a, "find_odd_") if q.meta["finding"]["numbers"]["value"] == p["value"]]
    if len(qs) != 1:
        return False
    q = qs[0]
    _contract(a, q)
    return q.kind == "unit" and f"Is {p['price']} there for one {p['unit']} as counted, or for a pack or case?" \
        in q.prompt and q.meta["about"]["col"] == p["price"]


def test_a_case_price_in_a_unit_of_its_own_asks_what_the_price_is_for(tmp_path):
    """The other groups' prices differ among themselves (one level a tenth of
    another) and none counts in its unit: its price far beyond every one of
    theirs, and its share of the money, are still the evidence."""
    hits = sum(_case_price_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_the_same_group_priced_per_unit_asks_nothing(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "case_price", twin=True)
    assert not _qs(a, "find_odd_") and not [i for i in a.insights if i["recipe"].startswith("oddgroup:")]


# --------------------------------------------------------------------------
# D8: a reference key on two dated rows: which row applies to which lines
# --------------------------------------------------------------------------
def _versions_q_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "terms_window")
    v = m["plants"][1]
    qs = _qs(a, "find_versions_")
    if len(qs) != 1:
        return False
    q = qs[0]
    _contract(a, q)
    t = a.tables[0]
    j, dj = t.headers.index(v["col"]), a._axis_j(t)
    split = dt.date.fromisoformat(v["split"][:10]).toordinal()
    mine = [r[dj].toordinal() for r in t.rows if r[j] == v["value"] and hasattr(r[dj], "year")]
    before, after = sum(1 for d in mine if d <= split), sum(1 for d in mine if d > split)
    return v["value"] in q.prompt and f"({before:,} line" in q.prompt and f"({after:,} line" in q.prompt \
        and q.recommend == "by_date"


def test_a_key_on_two_dated_rows_asks_which_applies_with_the_counts(tmp_path):
    hits = sum(_versions_q_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_one_dated_row_per_key_asks_nothing(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "terms_window", twin=True)
    assert not _qs(a, "find_versions_")


# --------------------------------------------------------------------------
# C1: room left after every detector question is filled with the playbook's
# questions about a column in the file; a goal that names a topic backs one
# --------------------------------------------------------------------------
def _with_questions(a):
    """Two unbacked playbook questions on a book no detector finds anything in:
    one about a column the file has (its role binds), one about no column."""
    t = a.tables[0]
    col = t.headers[2]
    a.detection["roles"]["pb_col"] = {"table": t.tid, "header": col, "col": a.col(t.tid, col)}
    opts = [{"id": "a", "label": "One thing", "desc": ""}, {"id": "b", "label": "Another thing", "desc": ""}]
    bound = {"id": "pb_bound", "header": "Bound", "kind": "definition", "priority": 2, "ask_if": ["has_role:pb_col"],
             "prompt": "What is {role:pb_col}?", "options": opts}
    loose = {"id": "pb_loose", "header": "Source", "kind": "history", "priority": 2, "ask_if": [],
             "prompt": "Where does this tab come from?", "options": opts,
             "fact": {"statement": "The source of this tab, per the owner: {answer_labels}."}}
    a.playbook = dict(a.playbook, questions=[bound, loose])
    return bound["id"], loose["id"]


def test_room_left_is_filled_with_playbook_questions_about_columns_in_the_file(tmp_path, synth_seed):
    _m, a = _built(tmp_path, synth_seed, "part_totals")          # no detector finds anything here
    # (a one-tap confirm of what code counted only fills room left over, round 3 fix 8)
    assert not [q for q in interview.candidates(a, {}) if q.source != "playbook" and q.kind != "goal"
                and not q.meta.get("leftover")]
    bound, loose = _with_questions(a)
    _state, asked = _interview(a)
    ids = [q.id for q in asked]
    # the question about a column the file has is asked first; one about the whole table (round 3 fix 8) only
    # after it, in the room left over
    assert bound in ids and len(ids) <= interview.HARD_CAP
    assert loose not in ids or ids.index(bound) < ids.index(loose)
    # a goal that names its topic backs the other, and it is asked too
    goal = {"goal": {"options": [], "text": "Know the source of every tab before the audit."}}
    _state, asked = _interview(a, goal)
    assert loose in [q.id for q in asked]


def test_no_fill_while_a_detector_question_is_waiting(tmp_path):
    """The budget book has more findings than the cap: every playbook question
    asked is backed, and none goes ahead of a finding worth more."""
    m = synth.build_budget(tmp_path / "budget.xlsx", 2)
    a = Analysis([m["path"]])
    _state, asked = _interview(a)
    assert len(asked) == interview.HARD_CAP
    assert all(q.meta.get("backed") for q in asked if q.source == "playbook")


def test_a_goal_that_names_a_topic_backs_that_question():
    q = {"id": "leave_out", "header": "Leave out", "kind": "exclusion",
         "fact": {"statement": "The owner said to leave these out of income and cost totals: {answer_labels}."}}
    assert interview._goal_names("Close the year with transfers kept out of costs.", q)
    assert not interview._goal_names("Close the year for the accountant.", q)
    assert not interview._goal_names("", q)
    # words every goal and question use name no topic
    assert not interview._goal_names("I want the totals right each month.", q)


def test_the_noise_books_still_get_no_new_question(noise_fixtures):
    new = ("find_boundary_", "find_handoff_", "follow_handoff_", "find_copies_", "find_upload_", "find_pairs_",
           "find_odd_", "find_blankm_", "find_outliers_", "find_derive_", "find_versions_", "find_window_")
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith(new)], paths
        assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith("alias_") and
                    (q.meta.get("finding") or {}).get("recipe", "").startswith("structure:join_variants:")], paths


def test_every_null_twin_of_the_new_traps_asks_no_new_question(tmp_path):
    new = ("find_copies_", "find_handoff_", "find_odd_", "find_blankm_", "find_versions_", "find_hardcoded")
    for name in ("part_totals", "link_multiplier", "reimport_lines", "case_rename", "file_variants", "hire_dates",
                 "measure_coblank", "case_price", "odd_twice"):
        for seed in SEEDS[:5]:
            _m, a = _built(tmp_path, seed, name, twin=True)
            assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith(new)], (name, seed)


# --------------------------------------------------------------------------
# C2: questions name their evidence
# --------------------------------------------------------------------------
def test_a_row_with_no_id_is_named_by_its_key_columns(tmp_path):
    import random
    rng = random.Random(3)
    rows = [["Site", "Unit", "Kind", "List Rent", "Billed Rent"]]
    for s in ("North Yard", "South Yard", "East Yard"):
        for u in range(101, 131):
            lst = rng.randint(900, 1600)
            rows.append([s, f"{u}B", rng.choice(["One", "Two"]), lst, round(lst * rng.uniform(0.93, 1.0))])
    rows[40][4] = round(rows[40][3] * 0.3)
    a = Analysis([_write(tmp_path / "rents.xlsx", {"Roll": rows})])
    q = _qs(a, "find_outliers_")[0]
    assert re.search(r"row 41 \((North|South|East) Yard, \d+B|row 41 \(\d+B, (North|South|East) Yard", q.prompt), q.prompt
    assert "Leave that row out (type why)" in [o["label"] for o in q.options]


# --------------------------------------------------------------------------
# C3: the same odd value on two tabs is one question listing both
# --------------------------------------------------------------------------
def _twice_ok(tmp_path, seed) -> bool:
    m, a = _built(tmp_path, seed, "odd_twice")
    p = m["plants"][0]
    qs = [q for q in _qs(a, "find_odd_") if q.meta["finding"]["numbers"]["value"] == p["value"]]
    if len(qs) != 1:
        return False
    q = qs[0]
    _contract(a, q)
    sheets = {t.sheet for t in a.tables}
    if len(q.meta["tables"]) != 2 or not all(s in q.prompt for s in sheets):
        return False
    # leaving it out takes its rows out of both tabs
    a.apply_answers({q.id: interview._answer(q, ["leave_out"], "")})
    return all(len(a._ctx.rows(t)) < len(t.rows) for t in a.tables)


def test_the_same_odd_value_on_two_tabs_is_one_question(tmp_path):
    hits = sum(_twice_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_once_answered_neither_tab_asks_it_again(tmp_path):
    m, a = _built(tmp_path, 1, "odd_twice")
    q = _qs(a, "find_odd_")[0]
    answers = {q.id: interview._answer(q, ["count"], "")}
    assert not _qs(a, "find_odd_", answers)


# --------------------------------------------------------------------------
# C4 and C5: the measure a code counts toward, and a column the owner said changed unit
# --------------------------------------------------------------------------
def _orders(tmp_path, split=False):
    import random
    rng = random.Random(11)
    rows = [["Order No", "Order Date", "Status", "Qty", "Unit Price", "Discount"]]
    start = dt.datetime(2031, 1, 1)
    for k in range(400):
        d = start + dt.timedelta(days=k // 2)
        st = rng.choice(["Fulfilled"] * 12 + ["Complete"] * 6 + ["Refunded", "Shipped"])
        disc = rng.choice([0, 10, 15, 20]) if (split and k < 200) else round(rng.uniform(0, 12), 2)
        no = f"SO-{5000 + k}" if not split or k < 200 else f"WB{90000 + k}"      # a new system from the split on
        rows.append([no, d, st, rng.randint(1, 5), rng.choice([19.5, 24.0, 42.75, 88.0]), disc])
    return _write(tmp_path / f"orders{int(split)}.xlsx", {"Lines": rows})


def test_a_discount_is_never_the_measure_a_code_counts_toward(tmp_path):
    a = Analysis([_orders(tmp_path)])
    t = a.tables[0]
    assert findings.money_column(a, t).header == "Discount"
    q = _qs(a, "codes_")[0]
    assert "count toward the totals" in q.prompt and "Discount totals" not in q.prompt
    assert "$" not in q.prompt.split("?", 1)[1]          # no discount money shown as what each value is worth
    d = _qs(a, "find_derive_")[0]
    answers = {d.id: interview._answer(d, ["yes"], "")}
    q = _qs(a, "codes_", answers)[0]
    assert f"count toward {d.meta['derive']['name']} totals" in q.prompt


def test_a_column_the_owner_said_changed_unit_is_never_summed_across_the_date(tmp_path):
    a = Analysis([_orders(tmp_path, split=True)])
    b = next((q for q in _qs(a, "find_boundary_")), None)
    if b is None:
        pytest.skip("no boundary found in this book")
    answers = {b.id: interview._answer(b, ["changed"], "Discount was a percent before and dollars after.")}
    changed = findings.unit_changed(a, answers)
    assert (a.tables[0].tid, "Discount") in changed
    when = changed[(a.tables[0].tid, "Discount")]
    d = _qs(a, "find_derive_", answers)[0]          # asked once the switch is answered (practice round 4, fix 11)
    answers[d.id] = interview._answer(d, ["yes"], "")
    a.apply_answers(answers)
    net = next(i for i in a.insights if i["recipe"].startswith("by_month:"))
    assert f"mixed units before and after {when}" in net["statement"] and "totals $" not in net["statement"]
    for q in _qs(a, "codes_", answers):
        assert f"mixed units before and after {when}" in q.prompt and "$" not in q.prompt


def test_a_sum_recipe_on_a_mixed_unit_column_says_so_instead():
    class Col:
        header, j = "Adj", 0

    class T:
        tid, sheet = "t", "Lines"

    class A:
        mixed_units = {("t", "Adj"): "Mar 2, 2031"}

        def table(self, tid):
            return T()

    class C:
        a = A()
        det = {"roles": {"adj": {"table": "t", "header": "Adj", "col": Col()}}}

        def role(self, rid):
            return self.det["roles"].get(rid)
    note = recipes._mixed_note(C(), "top_share", ["adj", "site"])
    assert note and "mixed units before and after Mar 2, 2031" in note["statement"]
    assert recipes._mixed_note(C(), "date_range", ["adj"]) is None


def test_a_money_column_that_changed_unit_is_not_summed_anywhere(tmp_path):
    """A sales book whose Net Sales is written in cents until a new system takes
    over and in dollars after, with a few negative lines in both halves. Once the
    owner says the unit changed: no readout, column note or recipe sums it, the
    negative lines are still counted (never totaled) and still asked about, and
    no question says '0 rows'."""
    import random

    from sheetbrain import say
    from sheetbrain.brain import Composer
    rng = random.Random(5)
    rows = [["Order Date", "Order ID", "Product", "Location", "Net Sales"]]
    start = dt.datetime(2031, 1, 1)
    for k in range(400):
        new = k >= 200
        v = round(rng.uniform(5, 90), 2) * (-1 if k % 23 == 0 else 1)
        rows.append([start + dt.timedelta(days=k // 2), f"WB{90000 + k}" if new else f"SO-{7000 + k}",
                     rng.choice(["Mug", "Tote", "Cap", "Pin", "Scarf"]), rng.choice(["Elm", "Oak", "Pine"]),
                     v if new else round(v * 100)])
    p = _write(tmp_path / "sales_cents.xlsx", {"Orders": rows})
    a = Analysis([p])
    b = _qs(a, "find_boundary_")[0]
    answers = {b.id: interview._answer(b, ["changed"], "Net Sales was in cents before and dollars after.")}
    when = findings.unit_changed(a, answers)[(a.tables[0].tid, "Net Sales")]
    a.apply_answers(answers)
    neg = next(i for i in a.insights if i["recipe"].startswith("negatives:"))
    assert neg["numbers"]["rows"] > 0 and "totaling" not in neg["statement"]
    assert f"not totaled: mixed units before and after {when}" in neg["statement"]
    qs = interview.candidates(a, answers)
    assert not [q.id for q in qs if re.match(r"0 rows?\b", q.prompt)]
    assert any(q.id.startswith("find_negatives_") and q.prompt.startswith(f"{neg['numbers']['rows']:,} rows")
               for q in qs)
    note = next(r["statement"] for r in Composer(a, p, "b1", answers).compose()
                if r["id"].startswith("col:") and "Net Sales on" in r["statement"])
    assert f"not summed: mixed units before and after {when}" in note and "summing to" not in note
    money = next(line for line in say.readout(a, 0.1).splitlines() if line.startswith(("Money", "Numbers")))
    assert "mixed units" in money and " in Net Sales" not in money and money.count("mixed units") == 1


# --------------------------------------------------------------------------
# C6: a list of values leaves out the ones the owner said are internal
# --------------------------------------------------------------------------
def test_a_value_list_leaves_out_what_the_owner_left_out(tmp_path):
    m, a = _built(tmp_path, 0, "odd_group")
    p = m["plants"][0]
    t = a.tables[0]
    a.detection["roles"]["pb_site"] = {"table": t.tid, "header": p["col"], "col": a.col(t.tid, p["col"])}
    env = interview.Env(a, {})
    tpl = "Your {count:pb_site} sites include {values:pb_site}."
    before = interview.fill(tpl, env)
    assert p["value"] in before
    answers = {"find_x": {"options": ["internal"], "exclude": {"table": t.tid, "col": p["col"], "values": [p["value"]],
                                                              "options": ["internal", "double"]}}}
    after = interview.fill(tpl, interview.Env(a, answers))
    n = a.col(t.tid, p["col"]).distinct
    assert p["value"] not in after and after.startswith(f"Your {n - 1} sites include"), after
    # a Not sure, or a pick that is not a leave-out, keeps it
    answers["find_x"]["options"] = ["real"]
    assert p["value"] in interview.fill(tpl, interview.Env(a, answers))


# --------------------------------------------------------------------------
# C7: 'the same ID?' only of ID columns
# --------------------------------------------------------------------------
def test_two_columns_of_names_are_never_asked_as_the_same_id(tmp_path):
    import random
    rng = random.Random(5)
    people = [f"{f} {l}" for f, l in synth._people(rng, 30)]
    staff = [["Staff No", "Staff Name", "Hours"]] + [[f"S{100 + i}", p, rng.randint(10, 40)] for i, p in enumerate(people)]
    sites = [["Site", "Site Lead", "Region"]] + [[f"Site {k}", people[k] if k < 24 else f"{synth._word(rng)} "
                                                  f"{synth._word(rng, 3)}", rng.choice(["N", "S"])] for k in range(30)]
    a = Analysis([_write(tmp_path / "names.xlsx", {"Staff": staff, "Sites": sites})])
    assert not _qs(a, "join_")


def test_an_id_join_is_still_asked(noise_fixtures):
    crm = next(p for p in noise_fixtures if any(x.endswith("crm_contacts.csv") for x in p))
    a = Analysis(crm)
    if any(j["band"] == "ask" for j in a.joins):
        assert _qs(a, "join_")


# --------------------------------------------------------------------------
# C8: whole words in headers, singular when the count is 1
# --------------------------------------------------------------------------
def test_headers_are_whole_words_and_counts_of_one_are_singular():
    assert findings.header_words("Blank", "Adjustment Amount") == "Adjustment"
    assert findings.header_words("Blank", "Discount") == "Discount"
    assert findings.header_words("Blank", "Site") == "Blank Site"
    assert findings.header_words("What is", "North Yard East") == "North Yard"
    assert all(len(findings.header_words("New", w)) <= 12 for w in ("Supercalifragilistic", "A B C D E F G"))
    assert recipes.plural("Entered By", 2) == "Entered By values"
    assert recipes.plural("Entered By", 1) == "Entered By value"
    assert recipes.plural("Ship To", 3) == "Ship To values"
    assert recipes.plural("Category", 2) == "Categories" and recipes.plural("Site", 1) == "Site"
    assert findings.one_is_one("1 rows have a blank and 1 values appear") == "1 row has a blank and 1 value appears"
    assert findings.one_is_one("21 rows have 11 values and 1,001 rows") == "21 rows have 11 values and 1,001 rows"


def test_no_prompt_on_the_new_traps_says_one_rows(tmp_path):
    for name in ("reimport_lines", "case_rename", "measure_coblank", "case_price", "odd_twice", "terms_window"):
        _m, a = _built(tmp_path, 1, name)
        for q in interview.candidates(a, {}):
            text = " ".join([q.prompt] + [o["label"] + " " + o.get("desc", "") for o in q.options])
            assert not re.search(r"(?<![\d,.])\b1 (rows|lines|values)\b", text), (name, q.id, text)
            assert len(interview._header(q.header)) <= 12
