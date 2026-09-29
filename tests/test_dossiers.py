"""Column dossiers and the closing question. A dossier asks once per code
column what its codes mean and which count toward the money, with every value's
rows and money on screen; a column that mixes a lookup's codes and names gets
the lookup's own map to confirm. The closing question comes once, after the
last round and outside the cap. Every book here is synthetic (tests/synth.py)."""
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import findings, interview, rules  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402
from test_faithful import LINTED, _bundled  # noqa: E402

SB = os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts", "sb.py")


def _dossier(a, col):
    return next((q for q in findings.code_dossiers(a, {}) if q.meta["about"]["col"] == col), None)


def _told(recs, qid):
    return [r["statement"] for r in recs if r.get("source") == "told" and r.get("ref") == f"q:{qid}"
            and r.get("record") == "fact"]


def _built(tmp_path, seed, name, twin=False):
    m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
    return m, Analysis([m["path"]])


def _plants(tmp_path, seed, twin=False):
    """A log with integer codes whose rare code carries the money, and a second
    tab whose status column holds one common code and two rare ones."""
    m = synth.build_book(tmp_path / f"codes{seed}{int(twin)}.xlsx", seed,
                         [("code_minority", twin), ("status_rare", twin)])
    return m, Analysis([m["path"]])


# --------------------------------------------------------------------------
# the detector: fires on its plants, silent on their twins and the noise books
# --------------------------------------------------------------------------
def test_one_dossier_per_code_column_listing_every_value(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _plants(tmp_path, seed)
        codes, status = m["plants"]
        qs = findings.code_dossiers(a, {})
        got = {q.meta["about"]["col"]: q for q in qs}
        ok = True
        for p, vals in ((codes, codes["values"]), (status, [status["value"]] + status["values"])):
            q = got.get(p["col"])
            if q is None or sum(1 for x in qs if x.meta["about"]["col"] == p["col"]) != 1:
                ok = False
                continue
            for v in vals:           # each value with its rows and its money (in dollars only for a currency role)
                assert re.search(rf"(?<![\w-]){re.escape(str(v))}: [\d,]+ rows?, (\$[\d,.]+[kMB]?|-?[\d,]+(\.\d\d)?)"
                                 rf"(;|\.| and|, all)", q.prompt), (seed, v, q.prompt)
        hits += ok
    assert hits >= math.ceil(0.95 * len(SEEDS)), hits


def test_the_twins_ask_nothing(tmp_path):
    """A code column with a name column that says the same thing row for row, an
    integer quantity 1 to 6, plain-word categories, and a site column of codes
    that a lookup names: no dossier, on any seed."""
    for seed in SEEDS:
        _m, a = _plants(tmp_path, seed, twin=True)
        assert findings.code_dossiers(a, {}) == [], seed
        tw = synth.build(tmp_path / f"lookup_twin{seed}.xlsx", seed, "lookup_mixed", twin=True)
        assert findings.code_dossiers(Analysis([tw["path"]]), {}) == [], seed


def test_a_few_rows_of_one_type_on_another_code_are_named_in_the_dossier(tmp_path):
    """Each type posts to its own code except a few lines of one type posted to
    another type's code: the code column's dossier says how many, with their
    money, and which code the rest of that type use. Twin: no exceptions, and
    the type names each code, so there is no dossier at all."""
    from sheetbrain.recipes import fmt_num
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "code_inside")
        p = m["plants"][0]
        q = _dossier(a, p["col"])
        if q is None or not q.meta.get("inside"):
            continue
        n = len(p["rows"])
        text = (f"{n} rows with {p['by']} {p['value']} use {p['code']} ({fmt_num(p['money'])}) where the other "
                f"{p['rest']} use {p['usual']}")
        hits += q.meta["inside"]["text"] == text and text in q.prompt and len(q.prompt) <= 500
        _tw, b = _built(tmp_path, seed, "code_inside", twin=True)
        assert findings.code_dossiers(b, {}) == [], seed
    assert hits >= math.ceil(0.95 * len(SEEDS)), hits


def test_money_is_in_dollars_only_where_a_currency_role_says_so(tmp_path):
    """The dossier and the rule readback write one table's money the same way: as
    a plain number while no role names its unit, in dollars once a currency role
    binds the column."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_rules import _pb
    rows = [["Ref", "Reason", "Amount"]]
    for i in range(120):
        rows.append([f"K-{1000 + i}", [3, 17, 29][i % 3] if i % 20 else 58, 10.0 + i % 7 * 0.25])
    path = _write(tmp_path / "unit.xlsx", {"Log": rows})
    plain = Analysis([path])
    from sheetbrain.recipes import fmt_num
    money = sum(r[2] for r in rows[1:] if r[1] == 58)
    t = plain.tables[0]
    assert rules.money_col(plain, t) == ("Amount", False)
    q = _dossier(plain, "Reason")
    assert "$" not in q.prompt and f"58: 6 rows, {fmt_num(round(money, 2))}." in q.prompt, q.prompt
    assert "$" not in next(o["desc"] for o in q.options if o["id"] == "leave_1")
    dollars = Analysis([path], playbooks=_pb([], ref=("Ref", "text", "identifier", None, None),
                                              amount=("Amount", "number", "metric", "currency", True)))
    assert rules.money_col(dollars, dollars.tables[0]) == ("Amount", True)
    q = _dossier(dollars, "Reason")
    from sheetbrain.recipes import fmt_money
    assert f"58: 6 rows, {fmt_money(money)}" in q.prompt, q.prompt


def test_evidence_beside_the_values_keeps_the_prompt_short(tmp_path):
    """A dossier that folds in another finding's evidence (renamed values, a
    value unlike the others, a few rows on another code) lists at most 5 values
    (every value when there are no more than 8, round 3 fix 26: a code hidden
    behind 'more' cannot be explained) and at most 3 renamed pairs, and stays
    within 500 characters."""
    for name in ("code_inside", "system_change", "odd_group"):
        for seed in SEEDS[:5]:
            _m, a = _built(tmp_path, seed, name)
            for q in findings.code_dossiers(a, {}):
                assert len(q.prompt) <= 500, (name, seed, len(q.prompt), q.prompt)
                listed = q.prompt.split(" mean? ", 1)[-1].split(". ", 1)[0]
                if (q.meta.get("inside") or "is unlike the others" in q.prompt or " -> " in q.prompt) \
                        and len(q.meta["codes"]) > findings.SHOWN:
                    assert len(listed.split("; ")) <= findings.BESIDE + 1, q.prompt
                assert "more" not in listed or len(q.meta["codes"]) > findings.SHOWN, q.prompt
                assert q.prompt.count(" -> ") <= findings.DOSSIER_PAIRS, q.prompt


def test_the_noise_books_get_no_dossier(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert findings.code_dossiers(a, {}) == [], paths
        assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith("codes_")], paths


def test_every_dossier_keeps_the_option_contract(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    lk = synth.build(tmp_path / "lookup.xlsx", synth_seed, "lookup_mixed")
    for an in (a, Analysis([lk["path"]])):
        env = interview.Env(an, {})
        for q in findings.code_dossiers(an, {}):
            shown = interview._options_for(q)
            assert shown[-1]["id"] == "not_sure" and len(shown) - 1 <= 3, q.id
            if q.kind in LINTED:
                assert not [o["label"] for o in q.options if _bundled(o["label"])], q.id
            ab = q.meta["about"]
            t = next(t for t in an.tables if t.tid == ab["table"])
            assert ab["col"] in t.headers and re.search(r"\d", q.prompt), q.id
            for oid, tpl in (q.fact.get("statements") or {}).items():
                o = next(o for o in q.options if o["id"] == oid)
                stmt = interview.fill(tpl, env, interview._answer(q, [oid], ""))
                assert not interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)), q.id


# --------------------------------------------------------------------------
# answers: typed meanings, picks and the lookup map
# --------------------------------------------------------------------------
def test_a_typed_code_line_proposes_exactly_that_code_and_a_note_per_code(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    p = m["plants"][0]
    q = _dossier(a, p["col"])
    other = next(v for v in p["values"] if v != p["value"])
    said = f"{p['value']} = sent back, leave out, {other} = kept"
    ans = interview.parse_answers([q], said)[q.id]
    assert ans["options"] == [] and ans["text"] == said
    answers = {q.id: ans}
    props = rules.proposals(a, answers)
    assert [c["rule"].predicate for c in props] == [[{"col": p["col"], "op": "in", "values": [str(p["value"])]}]]
    assert rules.exclusions(a, answers) == {}                     # proposed, never applied without a tick
    rb = findings.readback(a, answers)
    answers[rb.id] = interview.parse_answers([rb], "a")[rb.id]
    t = a.table(q.meta["about"]["table"])
    assert rules.exclusions(a, answers) == {(t.tid, t.headers.index(p["col"])): {str(p["value"])}}
    notes = _told(Composer(a, m["path"], "b1", {q.id: ans}).compose(), q.id)
    assert len(notes) == 2
    assert notes[0].endswith(f'"{p["value"]} = sent back, leave out."') and notes[1].endswith(f'"{other} = kept."')


def test_picking_leave_one_out_leaves_out_only_that_value(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    p = m["plants"][1]
    q = _dossier(a, p["col"])
    rare = [o for o in q.options if o["id"].startswith("leave_")]
    assert len(rare) == len(p["values"]) and q.multi
    pick = rare[0]
    value = next(v for v in p["values"] if pick["label"].startswith(f"Leave {v} out of "))
    answers = {q.id: interview.parse_answers([q], "a")[q.id]}
    ruled = rules.confirmed(a, answers)
    money = q.meta["money"]
    assert [(r.kind, r.predicate[0]["values"], r.scope) for r in ruled] == [("exclude", [value], [money])]
    a.apply_answers(answers)
    t = a.table(q.meta["about"]["table"])
    j = t.headers.index(p["col"])
    kept = a._ctx.rows(t, money)
    assert len(kept) == t.n_rows - sum(1 for r in t.rows if str(r[j]).strip() == value)
    assert {str(r[j]).strip() for r in kept} == {str(v) for v in [p["value"]] + p["values"] if v != value}
    assert len(a._ctx.rows(t)) == t.n_rows                  # counts of rows are not the money totals
    a.apply_answers({})


def test_all_of_them_count_applies_nothing(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    q = _dossier(a, m["plants"][1]["col"])
    k = next(i for i, o in enumerate(interview._options_for(q)) if o["id"] == "all_count")
    answers = {q.id: interview.parse_answers([q], chr(97 + k))[q.id]}
    assert rules.confirmed(a, answers) == [] and findings.readback(a, answers) is None
    notes = _told(Composer(a, m["path"], "b1", answers).compose(), q.id)
    assert notes == [f"Every {m['plants'][1]['col']} value counts toward {q.meta['money']} totals, per the owner."]


def test_a_column_mixing_lookup_codes_and_names_gets_the_lookups_map(tmp_path):
    hits = 0
    for seed in SEEDS:
        m = synth.build(tmp_path / f"lookup{seed}.xlsx", seed, "lookup_mixed")
        p = m["plants"][0]
        a = Analysis([m["path"]])
        q = _dossier(a, p["col"])
        if q is None:
            continue
        hits += 1
        assert f"holds names on {p['names']:,} rows and codes on {p['codes']:,}" in q.prompt, q.prompt
        answers = {q.id: interview.parse_answers([q], "a")[q.id]}
        a.apply_answers(answers)
        t = a.table(q.meta["about"]["table"])
        j = t.headers.index(p["col"])
        assert a._ctx.col(t, j).distinct == len(p["map"])       # each name counted as its code
        assert {str(r[j]) for r in a._ctx.rows(t)} == set(p["map"].values())
    assert hits >= math.ceil(0.95 * len(SEEDS)), hits


def test_a_value_set_on_two_tabs_is_asked_once_on_the_tab_with_more_money(tmp_path):
    path = str(tmp_path / "two.xlsx")
    wb = xlsxwriter.Workbook(path)
    for name, scale in (("Small", 1.0), ("Large", 10.0)):
        ws = wb.add_worksheet(name)
        ws.write_row(0, 0, ["Ref", "Reason", "Amount"])
        for i in range(120):
            ws.write_row(i + 1, 0, [f"{name[0]}-{1000 + i}", [3, 17, 29, 41][i % 4] if i % 20 else 58,
                                    round((20 + i % 7) * scale, 2)])
    wb.close()
    a = Analysis([path])
    qs = findings.code_dossiers(a, {})
    assert [q.meta["about"]["col"] for q in qs] == ["Reason"]
    assert a.table(qs[0].meta["about"]["table"]).sheet == "Large"


# --------------------------------------------------------------------------
# the closing question, through the CLI
# --------------------------------------------------------------------------
def _sb(env, *args):
    p = subprocess.run([sys.executable, SB, *args], capture_output=True, text=True, env=env, timeout=180)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def _to_the_closer(tmp_path, book, name):
    """Start, then answer every round 'not sure' until the closing question."""
    home = str(tmp_path / name)
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    r = _sb(env, "start", book)
    heads = []
    while r["next"] == "ask":
        qs = r["ask"]["questions"]
        heads.append([q["header"] for q in qs])
        if heads[-1] == ["Last one"]:
            break
        r = _sb(env, "answer", book, "--text", " ".join(f"{k} not sure" for k in range(1, len(qs) + 1)))
    return env, home, r, heads


def _answers(home, bid):
    from sheetbrain.store import Store
    st = Store(home)
    try:
        return st.state(bid)["answers"]
    finally:
        st.close()


def test_the_closer_comes_once_after_the_cap_and_carries_what_was_never_asked(tmp_path):
    m = synth.build_budget(tmp_path / "budget.xlsx", 0)
    book = m["path"]
    env, home, r, heads = _to_the_closer(tmp_path, book, "home1")
    assert heads[-1] == ["Last one"] and sum(h == ["Last one"] for h in heads) == 1
    assert all("Build" not in h for h in heads)
    bid = r["brain_id"]
    answers = _answers(home, bid)
    assert interview.substantive(answers) == interview.HARD_CAP          # the other candidates would exhaust it
    prompt = r["ask"]["questions"][0]["question"]
    a = Analysis([book])
    left = [q for q in interview.ranked(interview.candidates(a, answers)) if q.source == "finding"]
    assert left and "I noticed: " in prompt
    for q in left[:3]:
        assert interview._clause(q) in prompt, (q.id, prompt)
    ex = next(p for p in m["plants"] if p["kind"] == "exclusive")
    said = f"Leave {ex['value']} out of totals. The March rows came from the old till"
    r = _sb(env, "answer", book, "--text", said)
    asks = r["ask"]["questions"]
    assert r["next"] == "ask" and len(asks) == 2 and "What should I build" in asks[1]["question"]
    assert asks[0]["options"][0]["label"].startswith(f"{ex['col']} = {ex['value']} (")
    r = _sb(env, "answer", book, "--text", "1a 2b")
    assert r["next"] == "preview"
    after = _answers(home, bid)
    assert interview.substantive(after) == interview.HARD_CAP              # the closer is outside the cap
    rows = _sb(env, "preview", book, "--show-rows")["say"]
    told = [ln.split(" | ", 4)[4] for ln in rows.splitlines() if " | f:known_issues:" in ln]
    assert told == [f"Leave {ex['value']} out of totals.", "The March rows came from the old till."]
    assert f"Applied to counted numbers: rows of {ex['sheet']} where {ex['col']} is {ex['value']}" in rows


def test_nothing_to_add_writes_no_note(tmp_path):
    book = synth.build_budget(tmp_path / "budget.xlsx", 1)["path"]
    env, home, r, heads = _to_the_closer(tmp_path, book, "home2")
    assert heads[-1] == ["Last one"]
    before = interview.substantive(_answers(home, r["brain_id"]))
    r = _sb(env, "answer", book, "--text", "b")
    assert r["next"] == "ask" and [q["header"] for q in r["ask"]["questions"]] == ["Build"]
    r = _sb(env, "answer", book, "--text", "a")
    assert interview.substantive(_answers(home, r["brain_id"])) == before
    rows = _sb(env, "preview", book, "--show-rows")["say"]
    assert "f:known_issues" not in rows and "o:known_issues" not in rows


def test_the_closer_is_skipped_when_the_owner_stops(tmp_path):
    book = synth.build_budget(tmp_path / "budget.xlsx", 2)["path"]
    home = str(tmp_path / "home3")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    r = _sb(env, "start", book)
    n = len(r["ask"]["questions"])
    r = _sb(env, "answer", book, "--text", "stop")
    assert r["next"] == "save"
    assert interview.closer_question(Analysis([book]), {}) is not None     # it would have been asked
    heads = []                  # the round still pending is answered after the stop: no closing question
    r = _sb(env, "answer", book, "--text", " ".join(f"{k} not sure" for k in range(1, n + 1)))
    while r["next"] == "ask":
        heads += [q["header"] for q in r["ask"]["questions"]]
        if heads[-1] == "Build":
            break
        r = _sb(env, "answer", book, "--text", "b")
    assert heads and "Last one" not in heads and heads[-1] == "Build", heads
    assert interview.CLOSER not in _answers(home, r["brain_id"])


def test_a_date_span_is_named_when_a_code_bunches_up(tmp_path, synth_seed):
    import random
    rng = random.Random(f"span:{synth_seed}")
    path = str(tmp_path / "span.xlsx")
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Log")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_row(0, 0, ["Date", "Ref", "Reason", "Amount"])
    start = dt.datetime(rng.randint(2019, 2025), rng.randint(1, 12), 1)
    first = start + dt.timedelta(days=rng.randint(60, 150))          # a 3-day window inside the 200-day span
    k, rare, common = rng.randint(5, 8), rng.randint(40, 99), sorted(rng.sample(range(1, 39), 3))
    for i in range(200):
        late = i >= 200 - k
        d = first + dt.timedelta(days=(i - 200 + k) % 3) if late else start + dt.timedelta(days=i)
        ws.write_datetime(i + 1, 0, d, fmt)
        ws.write_row(i + 1, 1, [f"R-{1000 + i}", rare if late else common[i % 3], 25.0 + i % 9])
    wb.close()
    q = _dossier(Analysis([path]), "Reason")
    days = sorted({first + dt.timedelta(days=j % 3) for j in range(k)})
    assert re.search(rf"\b{rare}: {k} rows, [\d,.]+, all ", q.prompt) \
        and f", all {findings._span(days[0], days[-1])}" in q.prompt, q.prompt
    assert f"{common[0]}: " in q.prompt and ", all " not in q.prompt.split(f"{rare}: ")[0]


# --------------------------------------------------------------------------
# what an answer settles, and picks that contradict each other
# --------------------------------------------------------------------------
def test_all_of_them_count_leaves_the_meaning_open(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    col = m["plants"][1]["col"]
    q = _dossier(a, col)
    tid = q.meta["about"]["table"]
    ans = interview._answer(q, ["all_count"], "")
    assert (tid, col, "treatment") in interview.covered_keys({q.id: ans})
    assert (tid, col, "meaning") not in interview.covered_keys({q.id: ans})
    cover = next((r["statement"] for r in Composer(a, m["path"], "b1", {q.id: ans}).compose()
                  if r["id"] == "f:coverage"), "")
    assert "The owner said what" not in cover, cover
    # changed on purpose (round 3, fix 2): a word-valued status asks only which values count, so its meaning
    # was never asked and is not listed as left open; a code column's meaning is
    if q.prompt.startswith("What do the codes"):
        assert col in cover.split("Not said yet: ", 1)[1], cover
    # typed meanings do say it, where the prompt asked for them (a word-valued status asks only which count)
    typed = interview._answer(q, [], f"{m['plants'][1]['value']} = shipped")
    asked = "meaning" if q.prompt.startswith("What do the codes") else "treatment"
    assert (tid, col, asked) in interview.covered_keys({q.id: typed})


def test_a_different_answer_on_the_map_says_nothing_about_meaning(tmp_path):
    m = synth.build(tmp_path / "lookup.xlsx", 3, "lookup_mixed")
    a = Analysis([m["path"]])
    q = _dossier(a, m["plants"][0]["col"])
    tid = q.meta["about"]["table"]
    assert (tid, q.meta["about"]["col"], "meaning") not in interview.covered_keys(
        {q.id: interview._answer(q, ["different"], "")})
    assert (tid, q.meta["about"]["col"], "meaning") in interview.covered_keys({q.id: interview._answer(q, ["same"], "")})


def test_ticking_all_count_with_a_leave_out_applies_nothing(tmp_path, synth_seed):
    m, a = _plants(tmp_path, synth_seed)
    q = _dossier(a, m["plants"][1]["col"])
    shown = interview._options_for(q)
    both = "a, " + chr(97 + next(i for i, o in enumerate(shown) if o["id"] == "all_count"))
    ans = interview.parse_answers([q], both)[q.id]
    assert ans["options"] == [] and ans["not_sure"]
    answers = {q.id: ans}
    assert rules.confirmed(a, answers) == [] and rules.exclusions(a, answers) == {}
    assert _told(Composer(a, m["path"], "b1", answers).compose(), q.id) == []


# --------------------------------------------------------------------------
# one dossier per table and column
# --------------------------------------------------------------------------
def _write(path, sheets: dict) -> str:
    wb = xlsxwriter.Workbook(str(path))
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if v is not None:
                    ws.write(r, c, v)
    wb.close()
    return str(path)


def _coded(rng, pre, headers, codes, n):
    """A block: a unique ref, code columns that share one set of values, an amount."""
    rows = [["Ref"] + headers + ["Amount"]]
    for i in range(n):
        v = codes[0] if i % 12 else rng.choice(codes[1:])
        rows.append([f"{pre}-{1000 + i}"] + [v] * len(headers) + [round(rng.uniform(10, 90), 2)])
    return rows


def test_two_tables_on_one_sheet_each_get_their_own_dossier(tmp_path, synth_seed):
    import random
    rng = random.Random(f"two:{synth_seed}")
    codes = synth._codes(rng, 6, 2)
    rows = _coded(rng, "A", ["Status"], codes[:3], rng.randint(60, 90)) + [[], [], []] \
        + _coded(rng, "B", ["Status"], codes[3:], rng.randint(60, 90))
    a = Analysis([_write(tmp_path / "two.xlsx", {"Log": rows})])
    assert len(a.tables) == 2
    qs = findings.code_dossiers(a, {})
    assert sorted(q.meta["about"]["table"] for q in qs) == sorted(t.tid for t in a.tables)
    assert len({q.id for q in qs}) == 2
    ids = [q.id for q in interview.candidates(a, {}) if q.id.startswith("codes_")]
    assert sorted(ids) == sorted(q.id for q in qs)                 # neither is dropped as a duplicate id


def test_two_columns_of_one_table_with_the_same_values_are_two_dossiers(tmp_path, synth_seed):
    import random
    rng = random.Random(f"same:{synth_seed}")
    a = Analysis([_write(tmp_path / "same.xlsx", {"Log": _coded(rng, "A", ["Status", "Flag"],
                                                                 synth._codes(rng, 3, 2), rng.randint(60, 90))})])
    assert sorted(q.meta["about"]["col"] for q in findings.code_dossiers(a, {})) == ["Flag", "Status"]


# --------------------------------------------------------------------------
# standard codes explain themselves; a currency column is a question about the unit
# --------------------------------------------------------------------------
def test_standard_codes_are_never_asked_and_a_currency_asks_the_unit(tmp_path):
    hits = 0
    for seed in SEEDS:
        tw = synth.build(tmp_path / f"std_twin{seed}.xlsx", seed, "standard_sets", twin=True)
        assert findings.code_dossiers(Analysis([tw["path"]]), {}) == [], seed
        m = synth.build(tmp_path / f"std{seed}.xlsx", seed, "standard_sets")
        p = m["plants"][0]
        qs = findings.code_dossiers(Analysis([m["path"]]), {})
        if len(qs) != 1:
            continue
        q = qs[0]
        hits += 1
        assert q.kind == "unit" and q.meta["about"]["aspect"] == "unit" and q.meta["about"]["col"] == p["columns"][1]
        assert not [o for o in q.options if o["id"].startswith("leave_")] and not q.meta.get("rules")
        for v in [p["value"]] + p["values"]:
            assert re.search(rf"\b{v}: [\d,]+ rows?\b", q.prompt), (v, q.prompt)
    assert hits >= math.ceil(0.95 * len(SEEDS)), hits


def test_a_status_header_keeps_codes_that_also_spell_states(tmp_path):
    rows = [["Order", "Ship State", "Status", "Amount"]]
    for i in range(300):
        rows.append([f"SO-{5000 + i}", ["FL", "GA", "NY", "TX", "WY"][0 if i % 9 else 1 + i % 4],
                     "PA" if i % 15 else ["CA", "OK"][i % 2], 10.0 + i % 50])
    a = Analysis([_write(tmp_path / "sales.xlsx", {"Sales": rows})])
    assert [q.meta["about"]["col"] for q in findings.code_dossiers(a, {})] == ["Status"]


# --------------------------------------------------------------------------
# bar 5: no synthetic null twin gets a dossier
# --------------------------------------------------------------------------
def test_no_null_twin_of_any_trap_gets_a_dossier(tmp_path):
    for name in synth.TRAPS:
        for seed in SEEDS:
            tw = synth.build(tmp_path / f"{name}{seed}.xlsx", seed, name, twin=True)
            assert findings.code_dossiers(Analysis([tw["path"]]), {}) == [], (name, seed)


# --------------------------------------------------------------------------
# option labels: a long money header, or one that reads as two things
# --------------------------------------------------------------------------
@pytest.mark.parametrize("money", ["Fees and Charges", "Extended Net Merchandise Value After Adjustments"])
def test_a_long_money_header_is_named_under_the_option(tmp_path, money):
    import random
    rng = random.Random(7)
    rows = [["Ref", "Reason", money]]
    for i in range(120):
        rows.append([f"K-{1000 + i}", [3, 17, 29][i % 3] if i % 20 else 58, round(rng.uniform(10, 90), 2)])
    a = Analysis([_write(tmp_path / "long.xlsx", {"Log": rows})])
    q = _dossier(a, "Reason")
    leave = [o for o in q.options if o["id"].startswith("leave_")]
    assert leave and q.meta["money"] == money
    for o in leave:
        assert len(o["label"]) <= findings.LABEL_MAX and o["label"].endswith(" out of the totals")
        assert not _bundled(o["label"]) and o["desc"].endswith(f"of {money}")


# --------------------------------------------------------------------------
# joins: a short unique list inside a longer column is not a lookup it is missing from
# --------------------------------------------------------------------------
def test_a_short_list_inside_a_longer_column_asks_no_join(tmp_path, synth_seed):
    import random
    rng = random.Random(f"subset:{synth_seed}")
    cats = synth._names(rng, rng.randint(16, 24), 3)
    some = rng.sample(cats, rng.randint(6, 9))
    spend = [["Date", "Ref", "Category", "Amount"]]
    for i in range(rng.randint(240, 360)):
        spend.append([f"2024-{1 + i % 12:02d}-{1 + i % 28:02d}", f"SP-{3000 + i}", rng.choice(cats),
                      round(rng.uniform(10, 400), 2)])
    budget = [["Category", "Budget"]] + [[c, float(rng.randint(10, 90) * 100)] for c in some]
    a = Analysis([_write(tmp_path / "subset.xlsx", {"Spend": spend, "Budget": budget})])
    assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith("join_")]
    assert all(j["band"] == "auto" for j in a.joins if j["from_col"] == j["to_col"] == "Category")
