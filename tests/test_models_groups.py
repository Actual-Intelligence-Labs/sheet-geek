"""Formula models and group arithmetic. A model is read in words: each input with
its note (or none), each row that reads an input as a formula of labels, what
an odd formula does unlike its row, one ledger of the problem cells, typed
blocks that feed the model, rows whose typed periods do not tie to the row they
link to, the balance rows' lowest point, and the same row read again without a
plan the owner named. Rows gathered under a repeated ID get a companion line at
a fixed share, lines on the unusual side and the opening entry. Every detector
fires on its plant on 19 of 20 seeds or more, stays silent on its null twin and
asks nothing on the noise books. Every book here is synthetic (tests/synth.py)
or an evals fixture."""
import math
import os
import re
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import analyze, findings, formulas, interview, rules, say, workbook  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from test_faithful import LINTED, _bundled  # noqa: E402

NEEDED = math.ceil(0.95 * len(SEEDS))
MODEL_ASKS = ("find_plan_block_", "find_tieout_", "find_pattern_breaks", "find_hardcoded", "find_orphans",
              "find_typed_plug", "find_typed_row_", "find_check")
MODEL_RECIPES = ("formula:plan_block:", "formula:tieout:", "model:without:")
GROUP_ASKS = ("find_ratio_", "find_contra_", "find_opening_", "find_closing_")
GROUP_RECIPES = ("ratio:", "contra:", "opening:", "closing:")
JOURNALS = ("fixed_ratio", "opposite_side", "opening_entry", "journal_three", "closing_entry", "chart_side")
PLANS = ("model_plan", "model_plan_inputs")      # the typed headcount block on the model's tab, or on the inputs tab


def _built(tmp_path, seed, name, twin=False):
    m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
    return m, Analysis([m["path"]])


def _found(a, prefix):
    return [i for i in a.insights if i.get("recipe", "").startswith(prefix)]


def _asks(a, prefixes, answers=None):
    return [q for q in interview.candidates(a, answers or {}) if q.id.startswith(prefixes)]


def _plant(m, what):
    return next(p for p in m["plants"] if p.get("what") == what)


def _contract(a, q):
    """The option contract: at most 3 real options plus Not sure, one claim per
    label, a counted number in the prompt, meta.about naming a table and a column
    or model row that exist, and curated notes that use only words the owner saw."""
    shown = interview._options_for(q)
    assert shown[-1]["id"] == "not_sure" and len(shown) - 1 <= 3, q.id
    if q.kind in LINTED:
        assert not [o["label"] for o in q.options if _bundled(o["label"])], q.id
    ab = q.meta["about"]
    t = next(t for t in a.tables if t.tid == ab["table"])
    assert (ab["col"] in t.headers or ab["col"] in a.row_labels(t)) and re.search(r"\d", q.prompt), q.id
    env = interview.Env(a, {})
    for oid, tpl in ((q.fact or {}).get("statements") or {}).items():
        o = next(o for o in q.options if o["id"] == oid)
        stmt = interview.fill(tpl, env, interview._answer(q, [oid], ""))
        assert not interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)), (q.id, oid)


# --------------------------------------------------------------------------
# rank 20: a model in words
# --------------------------------------------------------------------------
def _words_ok(tmp_path, seed, name="model_plan") -> bool:
    """A driver fact for revenue in labels over its periods; one input fact per
    input row, the tax rate saying it has no note; the unused start input names
    its typed twin; the rate question shows the formula and outranks the scale
    question, whatever the goal."""
    m, a = _built(tmp_path, seed, name)
    drv, orphan, bare = _plant(m, "driver"), _plant(m, "orphan"), _plant(m, "no_note")
    tab, sheet = drv["inputs_tab"], m["plants"][0]["sheet"]
    growth = f"{drv['input']} ({tab}!{drv['input_cell']})"
    fact = _found(a, f"model:driver:{sheet}:{drv['row']}")
    if len(fact) != 1 or f"{drv['row']} = prior {drv['row']} x (1 + {growth})" not in fact[0]["statement"]:
        return False
    inputs = _found(a, f"model:input:{tab}:")
    if len(inputs) != 7 or not all(i["statement"].startswith("In the file, ") for i in inputs):
        return False
    if [i["numbers"]["label"] for i in inputs if i["statement"].endswith("with no note.")] != [bare["col"]]:
        return False
    orph = _found(a, "formula:orphan_inputs")
    if len(orph) != 1 or orph[0]["numbers"]["named"][0] != f"{orphan['col']} ({orphan['cell']})" \
            or f"({sheet}!{orphan['twin_cell']})" not in orph[0]["statement"] \
            or f"{orphan['value']:,}" not in orph[0]["statement"]:
        return False
    for goal in ([], ["audit_it"], ["roll_forward"]):
        answers = {}
        if goal:
            gq = next(q for q in interview.candidates(a, {}) if q.id == "goal")
            answers["goal"] = interview._answer(gq, goal, "")
        by = {q.id: q for q in interview.candidates(a, answers)}
        rate, scale = by.get("growth_period"), by.get("money_scale")
        if rate is None or scale is None or growth not in rate.prompt or rate.value <= scale.value:
            return False
    return True


@pytest.mark.parametrize("name", PLANS)
def test_a_model_is_read_in_words(tmp_path, name):
    hits = sum(_words_ok(tmp_path, seed, name) for seed in SEEDS)
    assert hits >= NEEDED, hits


def _ledger_ok(tmp_path, seed, name="model_plan") -> bool:
    """The break names both inputs in its question and its note; the typed 1.05
    style multiplier is one ledger cell with the amount it adds and no break; the
    failing check names the typed cell in its month; the ledger counts distinct
    cells and is counted again after an answer."""
    m, a = _built(tmp_path, seed, name)
    drv, brk, mul, plug = (_plant(m, w) for w in ("driver", "break", "multiplier", "plug"))
    sheet, tab = brk["sheet"], drv["inputs_tab"]
    said = f"uses {brk['uses']} ({tab}!B3) where the row uses {brk['instead']} ({tab}!B2)"
    q = next((q for q in interview.candidates(a, {}) if q.id == "find_pattern_breaks"), None)
    if q is None or said not in q.prompt or said not in q.fact["statement"]:
        return False
    _contract(a, q)
    led = _found(a, "model:ledger")
    if len(led) != 1:
        return False
    n = led[0]["numbers"]
    cells = n["cells"]
    if len(cells) != len(set(cells)) or n["count"] != len(cells) or cells.count(f"{sheet}!{mul['cell']}") != 1:
        return False
    hc = _found(a, "formula:hardcoded")[0]["numbers"]
    added = hc["added"][hc["cells"].index(f"{sheet}!{mul['cell']}")]
    # the amount is read from the saved value, or from the formula when a script saved none (cents may differ)
    if added is None or abs(added - mul["added"]) > 0.05 \
            or f"multiplies a link by a typed {mul['k']}, adding {added:,.2f}" not in led[0]["statement"]:
        return False
    if f"{sheet}!{mul['cell']}" in _found(a, "formula:pattern_breaks")[0]["numbers"]["cells"]:
        return False
    chk = [i for i in _found(a, "formula:check") if i.get("oddity")]
    if len(chk) != 1 or f"({sheet}!{plug['cell']})" not in chk[0]["statement"] \
            or chk[0]["numbers"]["explained_by"] != [f"{sheet}!{plug['cell']}"]:
        return False
    hq = next(q for q in interview.candidates(a, {}) if q.id == "find_hardcoded")
    if f"adding {added:,.2f}" not in hq.prompt:
        return False
    a.apply_answers({hq.id: interview._answer(hq, ["approved"], "")})
    after = _found(a, "model:ledger")
    return len(after) == 1 and len(after[0]["numbers"]["answered"]) == 1 and "answered about 1 of them" in \
        after[0]["statement"]


@pytest.mark.parametrize("name", PLANS)
def test_each_problem_cell_is_counted_once_with_all_its_reasons(tmp_path, name):
    hits = sum(_ledger_ok(tmp_path, seed, name) for seed in SEEDS)
    assert hits >= NEEDED, hits


def _blocks_ok(tmp_path, seed, name="model_plan", pick="plan") -> bool:
    """The typed headcount block gets a provenance question, on whichever tab it
    is typed; the typed first months of collections, off from the revenue they
    link to, get a tie-out question; the cash row's lowest point and its period
    are stated; with the funding row called a plan (or a test number), cash read
    again without it first goes below zero in the planted month."""
    m, a = _built(tmp_path, seed, name)
    block, tie, series, plan = (_plant(m, w) for w in ("plan_block", "tieout", "series", "plan"))
    sheet = block["sheet"]
    cands = interview.candidates(a, {})
    pb = [q for q in cands if q.id.startswith("find_plan_block_")]
    if len(pb) != 1 or pb[0].meta["about"]["col"] != block["col"] or [o["id"] for o in pb[0].options] \
            != ["approved", "estimate", "placeholder"] or "when and by whom" not in pb[0].prompt:
        return False
    if f" on {block['block_tab'] or sheet} " not in pb[0].prompt:
        return False
    tq = [q for q in cands if q.id.startswith("find_tieout_")]
    if len(tq) != 1 or tq[0].meta["about"]["col"] != tie["col"] or f"links to {tie['link']}" not in tq[0].prompt \
            or "3 periods" not in tq[0].prompt:
        return False
    for q in pb + tq:
        _contract(a, q)
    low = _found(a, f"model:series:{sheet}:{series['col']}")
    if len(low) != 1 or abs(low[0]["numbers"]["min"] - series["min"]) > 1.0 \
            or low[0]["numbers"]["min_at"] != series["min_at"] or low[0]["numbers"]["below"] is not None:
        return False
    fund = next((q for q in cands if q.id.startswith("find_typed_row_") and q.meta["about"]["col"] == plan["col"]),
                None)
    if fund is None:
        return False
    a.apply_answers({fund.id: interview._answer(fund, [pick], "")})
    wo = _found(a, f"model:without:{sheet}:{plan['col']}")
    label = next(o["label"] for o in fund.options if o["id"] == pick)
    return len(wo) == 1 and wo[0]["numbers"]["below"] == plan["below"] and wo[0]["numbers"]["without"] == plan["col"] \
        and f"Without the {plan['col']} row" in wo[0]["statement"] and label in wo[0]["statement"] \
        and wo[0]["numbers"]["kept"] == [] and "kept as typed" not in wo[0]["statement"]


@pytest.mark.parametrize("name", PLANS)
def test_typed_blocks_tie_outs_and_a_plan_taken_out(tmp_path, name):
    hits = sum(_blocks_ok(tmp_path, seed, name) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_a_typed_row_called_a_test_number_is_taken_out_too(tmp_path):
    """'A test number, should come out' is the clearest case for reading cash
    without it; the note quotes the pick."""
    hits = sum(_blocks_ok(tmp_path, seed, pick="test") for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_a_balance_row_with_its_own_typed_cell_says_it_was_kept(tmp_path):
    """Without the plan, a balance row that holds a typed number after its
    formulas start keeps that number, and the reading names it."""
    openpyxl = pytest.importorskip("openpyxl")
    m = synth.build(tmp_path / "kept.xlsx", 0, "model_plan")
    series, plan = _plant(m, "series"), _plant(m, "plan")
    wb = openpyxl.load_workbook(m["path"])
    ws = wb[series["sheet"]]
    r = series["rows"][0]
    ws.cell(row=r, column=20).value = 1234.5            # a later cash month typed over its formula
    wb.save(m["path"])
    a = Analysis([m["path"]])
    fund = next(q for q in interview.candidates(a, {}) if q.id.startswith("find_typed_row_")
                and q.meta["about"]["col"] == plan["col"])
    a.apply_answers({fund.id: interview._answer(fund, ["plan"], "")})
    wo = _found(a, f"model:without:{series['sheet']}:{plan['col']}")
    assert len(wo) == 1 and wo[0]["numbers"]["kept"] == [f"T{r}"], wo
    assert f"{series['col']} on {series['sheet']}, with its typed T{r} kept as typed, is lowest" in wo[0]["statement"]


@pytest.mark.parametrize("name", PLANS)
def test_a_model_with_none_of_them_asks_nothing_new(tmp_path, name):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, name, twin=True)
        assert not _asks(a, MODEL_ASKS), (seed, [q.id for q in _asks(a, MODEL_ASKS)])
        assert not _found(a, MODEL_RECIPES) and not _found(a, "model:ledger"), seed
        assert _found(a, "model:driver:"), seed          # its drivers are still read in words


# --------------------------------------------------------------------------
# rank 20h: typed actuals that do not tie to the row they link to, at the tabs' switch
# --------------------------------------------------------------------------
def _at_switch_ok(tmp_path, seed) -> bool:
    """Receipts typed through the actuals switch shared by 5 rows on 2 tabs, off
    from the revenue they link to after it: asked, with the stated numbers as
    evidence and the same 3 options."""
    m, a = _built(tmp_path, seed, "model_actuals")
    p = m["plants"][0]
    bd = _found(a, "formula:actuals_boundary")
    if len(bd) != 1 or bd[0]["numbers"]["rows"] < 3 or bd[0]["numbers"]["last_actual"] != p["last_actual"]:
        return False
    tie = _found(a, "formula:tieout:")
    if len(tie) != 1 or not tie[0]["numbers"]["at_switch"] or _found(a, "model:tieout"):
        return False
    qs = _asks(a, ("find_tieout_",))
    if len(qs) != 1 or [o["id"] for o in qs[0].options] != ["other", "timing", "mistake"] \
            or qs[0].meta["about"]["col"] != p["col"] or f"{p['typed']} periods" not in qs[0].prompt \
            or "switches from typed numbers to formulas" not in qs[0].prompt \
            or f"{tie[0]['numbers']['typed_sum']:,.2f}" not in qs[0].prompt:
        return False
    _contract(a, qs[0])
    return True


def test_typed_actuals_at_the_switch_that_do_not_tie_are_asked(tmp_path):
    hits = sum(_at_switch_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


# --------------------------------------------------------------------------
# rank 20j: sign and scale are counted facts, asked only when the file disagrees
# --------------------------------------------------------------------------
def _named_scale_ok(tmp_path, seed) -> bool:
    """A synthetic model whose titles name one currency and whose profit row
    subtracts two cost rows of positive numbers: neither sign nor scale is
    asked; where actuals end is a one-tap confirm whose note keeps the month and
    the tabs the prompt showed."""
    _m, a = _built(tmp_path, seed, "model_actuals")
    cands = interview.candidates(a, {})
    ids = {q.id for q in cands}
    if "sign_rule" in ids or "money_scale" in ids:
        return False
    sign, scale = _found(a, "model:sign"), _found(a, "model:scale")
    if not sign or not scale or sign[0]["settles"] != ["model_sign"] or scale[0]["settles"] != ["model_scale"]:
        return False
    # where actuals end: the detected switch on every sheet that switches there, with where the typed months come
    # from folded into the same pick (practice round 4, fix 3); nothing recommends a source code cannot see
    bd = _found(a, "formula:actuals_boundary")[0]
    q = next(q for q in cands if q.id == "actuals_end")
    if q.recommend or not q.prompt.startswith(bd["statement"]) or len(bd["numbers"]["sheets"]) < 2 \
            or [o["id"] for o in q.options] != ["typed_books", "typed_hand", "other"]:
        return False
    for oid in ("typed_books", "typed_hand"):
        stmt = q.fact["statements"][oid]
        o = next(o for o in q.options if o["id"] == oid)
        if f"after {analyze._month(bd['numbers']['last_actual'])} on {' and '.join(bd['numbers']['sheets'])}." \
                not in stmt or interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)):
            return False
    text = say.model_readout(a, 0.5, 3)
    return "Actuals end at" in text and " and ".join(bd["numbers"]["sheets"][-2:]) in text \
        and sign[0]["statement"] in text and scale[0]["statement"] in text


def test_a_model_that_names_its_scale_and_subtracts_its_costs_is_not_asked_either(tmp_path):
    hits = sum(_named_scale_ok(tmp_path, seed) for seed in SEEDS)
    assert hits >= NEEDED, hits


def test_its_twin_that_names_two_scales_is_asked_and_ties_out(tmp_path):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, "model_actuals", twin=True)
        scale = _found(a, "model:scale")
        assert scale and scale[0]["numbers"]["conflict"] and scale[0]["settles"] == [], seed
        ids = {q.id for q in interview.candidates(a, {})}
        assert "money_scale" in ids and not [i for i in ids if i.startswith("find_tieout_")], (seed, ids)
        assert not _found(a, "formula:tieout:"), seed


def test_a_model_that_names_two_scales_is_asked(tmp_path):
    tabs = {"Plan": [SimpleNamespace(sheet="Plan", title="Plan (in thousands)")],
            "Actuals": [SimpleNamespace(sheet="Actuals", title="Actuals (USD)")]}
    sc = formulas._scale(tabs, [])
    assert sc["conflict"] and sc["kinds"] == ["as shown", "thousands"]
    assert not formulas._scale({"Plan": [SimpleNamespace(sheet="Plan", title="Plan (in thousands)")]}, [])["conflict"]
    _m, a = _built(tmp_path, 0, "model_plan")
    out = []
    a._sign_fact({"positive_subtracted": ["P!Costs"], "negative_added": ["P!Refunds"], "example": "",
                  "example_sheet": "P"}, out)
    assert out[0]["settles"] == [] and out[0]["statement"].startswith("Subtotals disagree on signs")
    # a model whose file names no scale still asks it
    assert "money_scale" in {q.id for q in interview.candidates(a, {})}


# --------------------------------------------------------------------------
# rank 21: group arithmetic
# --------------------------------------------------------------------------
def _ratio_ok(a, p) -> bool:
    qs = _asks(a, ("find_ratio_",))
    pct = f"{p['rate'] * 100:.4g}%"
    if len(qs) != 1 or pct not in qs[0].prompt or f"{p['values'][0]} line in {p['col']}" not in qs[0].prompt:
        return False
    _contract(a, qs[0])
    ans = {qs[0].id: interview._answer(qs[0], ["collected"], "")}
    ruled = rules.confirmed(a, ans)
    return [r.predicate for r in ruled] == [[{"col": p["col"], "op": "in", "values": [p["values"][0]]}]]


def _contra_ok(a, p) -> bool:
    qs = _asks(a, ("find_contra_",))
    if len(qs) != 1 or f"\"{p['prefix']}\"" not in qs[0].prompt or not qs[0].prompt.startswith("4 lines") \
            or qs[0].meta["about"]["col"] != p["col"]:
        return False
    _contract(a, qs[0])
    # practice round 4, fix 14: netting opens only on a typed answer that picked no group, never on a pick
    if [q for q in findings.follow_ups(a, {qs[0].id: interview._answer(qs[0], ["credits"], "")})
            if q.id.startswith("follow_net_")]:
        return False
    fu = [q for q in findings.follow_ups(a, {qs[0].id: interview._answer(qs[0], [], "Money we gave back")})
          if q.id.startswith("follow_net_")]
    # the follow-up names its lines itself, so it stands alone as an open item (round 3 fix 25)
    if len(fu) != 1 or "refunds or returns among the 4 lines on the side opposite" not in fu[0].prompt \
            or fu[0].meta["about"]["col"] != p["col"]:
        return False
    _contract(a, fu[0])
    return True


def _opening_ok(a, p) -> bool:
    """The pick scopes the entry to balances: it leaves the totals of Debit and
    Credit (what happened in the period) and stays in the row counts."""
    qs = _asks(a, ("find_opening_",))
    if len(qs) != 1 or f"Is {p['value']} the opening balances carried in, or activity in the period?" not in qs[0].prompt:
        return False
    _contract(a, qs[0])
    ans = {qs[0].id: interview._answer(qs[0], ["opening"], "")}
    ruled = rules.confirmed(a, ans)
    if [r.predicate for r in ruled] != [[{"col": p["col"], "op": "in", "values": [p["value"]]}]] \
            or ruled[0].scope != ["Debit", "Credit"]:
        return False
    t = next(t for t in a.tables if t.tid == ruled[0].table)
    before = {m: len(a._ctx.rows(t, m)) for m in (None, "Debit", "Credit")}
    a.apply_answers(ans)
    after = {m: len(a._ctx.rows(t, m)) for m in (None, "Debit", "Credit")}
    return after[None] == before[None] and all(after[m] == before[m] - len(p["rows"]) for m in ("Debit", "Credit"))


def _closing_ok(a, p) -> bool:
    """The last entry closes the period out: the pick leaves it out of the totals
    of Debit and Credit (what happened in the period) and keeps it in row counts."""
    qs = _asks(a, ("find_closing_",))
    if len(qs) != 1 or f"Is {p['value']} the closing entry that carries the balances out, or activity in the period?" not in qs[0].prompt:
        return False
    _contract(a, qs[0])
    ans = {qs[0].id: interview._answer(qs[0], ["closing"], "")}
    ruled = rules.confirmed(a, ans)
    if [r.predicate for r in ruled] != [[{"col": p["col"], "op": "in", "values": [p["value"]]}]] \
            or ruled[0].scope != ["Debit", "Credit"]:
        return False
    t = next(t for t in a.tables if t.tid == ruled[0].table)
    before = {m: len(a._ctx.rows(t, m)) for m in (None, "Debit", "Credit")}
    a.apply_answers(ans)
    after = {m: len(a._ctx.rows(t, m)) for m in (None, "Debit", "Credit")}
    return after[None] == before[None] and all(after[m] == before[m] - len(p["rows"]) for m in ("Debit", "Credit"))


def _chart_contra_ok(a, p) -> bool:
    """With no prefix, the chart's normal side names exactly the planted lines:
    never the cash lines, which sit on both sides by nature."""
    qs = _asks(a, ("find_contra_",))
    found = _found(a, "contra:")
    if len(qs) != 1 or len(found) != 1 or not qs[0].prompt.startswith("4 lines") \
            or f"{p['side']} on " not in qs[0].prompt or "gives each" not in qs[0].prompt:
        return False
    _contract(a, qs[0])
    t = next(t for t in a.tables if t.tid == found[0]["numbers"]["table"])
    rows = sorted(t.row_index[i] + 1 for i in found[0]["numbers"]["row_ids"])
    fu = [q for q in findings.follow_ups(a, {qs[0].id: interview._answer(qs[0], [], "Money we gave back")})
          if q.id.startswith("follow_net_")]
    return rows == sorted(p["rows"]) and len(fu) == 1 and found[0]["numbers"]["prefix"] is None


CHECK = {"ratio": _ratio_ok, "contra": _contra_ok, "opening": _opening_ok, "closing": _closing_ok}


def test_lines_off_the_charts_side_with_no_prefix_are_asked(tmp_path):
    """A chart gives each account its normal side; 4 lines on the income accounts'
    debit side carry ordinary memos. Only those 4 are asked about."""
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "chart_side")
        hits += _chart_contra_ok(a, m["plants"][0]) and len(_asks(a, GROUP_ASKS)) == 1
    assert hits >= NEEDED, hits


def test_a_last_entry_named_closing_in_its_ordinary_sense_is_not_asked(tmp_path):
    """'Store closing supplies' on the last entry is a word, not the period closed
    out: nothing is asked, on any seed."""
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "closing_entry", twin=True)
        rows = dict(synth.cells(m["path"]))[m["plants"][0]["sheet"]]
        assert re.search(r"clos", " ".join(str(v) for v in rows[-1]), re.I), seed
        assert not _found(a, "closing:") and not _asks(a, ("find_closing_",)), seed


def test_a_big_last_order_with_no_debit_and_credit_is_not_a_closing_entry(tmp_path):
    """The amounts alone point at a closing entry only in a table with a debit and
    a credit side: an order file's biggest order being its last is just that."""
    import datetime as dt
    import random
    rng = random.Random(9)
    start = dt.datetime(2031, 1, 1)
    rows = [["Order ID", "Date", "Item", "Amount"]]
    for k in range(60):
        for _ in range(rng.randint(2, 3)):
            rows.append([f"SO-{100 + k}", start + dt.timedelta(days=k), synth._word(rng), round(rng.uniform(10, 90), 2)])
    last = start + dt.timedelta(days=70)
    rows += [["SO-900", last, "Bulk", 9000.0], ["SO-900", last, "Bulk", 8000.0]]
    path = str(tmp_path / "orders.xlsx")
    synth._write_xlsxwriter(path, [{"name": "Orders", "rows": rows}])
    a = Analysis([path])
    assert a._group_col(a.tables[0]) is not None and not _found(a, "closing:")


def test_closing_words_are_read_as_closing_only_in_that_sense():
    said = ["Closing entry", "Year-end close", "Close the books", "Closing balances", "Balance carried forward",
            "Period end closing", "c/f to next year"]
    plain = ["Store closing supplies", "Close out sale", "Closing shift float", "Closed old account",
             "Closing time cleanup", "Grand opening flyers"]
    assert all(analyze._CLOSING_WORDS.search(x) for x in said), said
    assert not [x for x in plain if analyze._CLOSING_WORDS.search(x)]


@pytest.mark.parametrize("name", ["fixed_ratio", "fee_line", "opposite_side", "opening_entry", "closing_entry"])
def test_each_group_plant_gets_its_question(tmp_path, name):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, name)
        p = m["plants"][0]
        hits += CHECK[p["expect"]](a, p) and len(_asks(a, GROUP_ASKS)) == 1
    assert hits >= NEEDED, (name, hits)


def test_a_journal_with_all_three_gets_exactly_three_questions(tmp_path):
    hits = 0
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "journal_three")
        found = [q.id for q in interview.candidates(a, {}) if q.id.startswith("find_")]
        hits += sorted(i.split("_")[1] for i in found) == ["contra", "opening", "ratio"] \
            and all(CHECK[p["expect"]](a, p) for p in m["plants"])
    assert hits >= NEEDED, hits


@pytest.mark.parametrize("name", JOURNALS + ("fee_line",))
def test_the_same_groups_without_them_ask_nothing(tmp_path, name):
    for seed in SEEDS:
        _m, a = _built(tmp_path, seed, name, twin=True)
        assert not _found(a, GROUP_RECIPES), (name, seed)
        assert not [q.id for q in interview.candidates(a, {}) if q.id.startswith("find_")], (name, seed)


def test_a_prefix_family_marks_only_its_own_rows(tmp_path):
    """Cash sits on both sides by nature: its other-side lines carry no prefix and
    are never counted with the refunds the prefix marks."""
    for seed in SEEDS[:5]:
        m, a = _built(tmp_path, seed, "opposite_side")
        got = _found(a, "contra:")
        assert got and got[0]["numbers"]["rows"] == len(m["plants"][0]["rows"]), seed
        assert got[0]["numbers"]["prefix"] == m["plants"][0]["prefix"], seed


def test_a_first_entry_named_opening_in_its_ordinary_sense_is_not_asked(tmp_path):
    """'Grand opening flyers' on the first entry is a word, not balances carried
    in: the twin's first memo says it, and nothing is asked."""
    for seed in SEEDS[:5]:
        m, a = _built(tmp_path, seed, "opening_entry", twin=True)
        rows = dict(synth.cells(m["path"]))[m["plants"][0]["sheet"]]
        assert re.search(r"open|begin", " ".join(str(v) for v in rows[1]), re.I), seed
        assert not _found(a, "opening:") and not _asks(a, ("find_opening_",)), seed


def test_a_first_entry_whose_memo_says_nothing_is_found_by_its_amounts(tmp_path):
    """Balances carried in under a plain memo: found because the entry holds the
    largest Debit and Credit, far above the rest."""
    import random
    for seed in SEEDS[:5]:
        rng = random.Random(f"maxima:{seed}")
        table, plants = synth._journal(rng, opening=True)
        memo = table[0].index(next(h for h in table[0] if h in synth.POOLS["note"]))
        for r in plants[0]["rows"]:
            table[r - 1][memo] = "January entry"
        path = str(tmp_path / f"maxima{seed}.xlsx")
        synth._write_xlsxwriter(path, [{"name": "Journal", "rows": table}])
        a = Analysis([path])
        got = _found(a, "opening:")
        assert len(got) == 1 and got[0]["numbers"]["maxima"] and not got[0]["numbers"]["said"], seed
        assert "it holds the largest Debit" in got[0]["statement"] and "its text says" not in got[0]["statement"]
        assert _opening_ok(a, dict(plants[0], col=plants[0]["col"])), seed


def test_a_negative_line_at_a_fixed_share_is_asked_once_as_a_negative(tmp_path):
    """A discount at -10% of its order is a negative, not also a fixed share: one
    question for those rows, never two."""
    for seed in SEEDS:
        m, a = _built(tmp_path, seed, "discount_line")
        asked = [q.id for q in interview.candidates(a, {}) if q.id.startswith("find_")]
        assert not _found(a, "ratio:") and len(asked) == 1 and asked[0].startswith("find_negatives_"), (seed, asked)


# --------------------------------------------------------------------------
# counts are of every cell, and a formula that cannot be read the same way is not read
# --------------------------------------------------------------------------
def _grid_book(path, rows_n: int, formula) -> str:
    """One period grid of rows_n typed-then-formula rows: formula(r, col, prev) per cell."""
    heads = [f"{m} 2031" for m in synth._MONTHS]
    rows = [["Line"] + heads]
    for k in range(rows_n):
        r = k + 2
        rows.append([f"Line {k + 1}", 100.0 + k] + [synth.Formula(formula(r, synth._col(m + 1), synth._col(m)), 0)
                                                    for m in range(1, 12)])
    synth._write_xlsxwriter(str(path), [{"name": "Grid", "rows": rows}])
    return str(path)


def test_the_ledger_counts_every_problem_cell_past_the_shown_few(tmp_path):
    """70 rows of 11 formulas each with a typed 1.37: the typed-number note and
    the ledger both count all 770 cells, not the 60 kept for display."""
    a = Analysis([_grid_book(tmp_path / "many.xlsx", 70, lambda r, c, p: f"={p}{r}*1.37")])
    hc = _found(a, "formula:hardcoded")[0]
    led = _found(a, "model:ledger")[0]
    assert hc["numbers"]["count"] == 770 and led["numbers"]["count"] == 770
    assert led["statement"].startswith("770 cells hold the formula problems found")


def test_a_power_after_a_minus_or_a_chain_of_powers_is_not_read_again(tmp_path):
    """Excel reads -2^2 as 4 and 2^3^2 as 64; Python reads them as -4 and 512, so
    such a formula saved without its value has no number rather than a wrong one."""
    rows = [["Line", "Jan 2031"], ["A", 2], ["B", synth.Formula("=-B2^2", None)], ["C", synth.Formula("=B2^3^2", None)],
            ["D", synth.Formula("=B2^2", None)], ["E", synth.Formula("=0-B2^2", None)]]
    path = str(tmp_path / "pow.xlsx")
    synth._write_openpyxl(path, [{"name": "P", "rows": rows}])
    model = formulas.Model(workbook.load(path))
    assert model.number(("P", 2, 1)) is None and model.number(("P", 3, 1)) is None
    assert model.number(("P", 4, 1)) == 4.0 and model.number(("P", 5, 1)) == -4.0


def test_a_price_list_a_lookup_reads_is_not_a_tab_of_inputs(tmp_path):
    """Label | price rows read only through a lookup range are a list: no input
    facts for them, while the rates each read by their own cell are inputs."""
    heads = [f"{m} 2031" for m in synth._MONTHS]
    items = [synth._word(__import__("random").Random(k), 3) for k in range(40)]
    prices = [["Item", "Price"]] + [[it, 5 + k] for k, it in enumerate(items)]
    inputs = [["Input", "Value", "Note"], ["Growth", 0.03, "per month"], ["Tax rate", 0.2, "flat"]]
    grid = [["Line"] + heads]
    for k, it in enumerate(items[:4]):
        r = k + 2
        grid.append([it] + [synth.Formula(f"=VLOOKUP($A{r},Prices!$A$2:$B$41,2,FALSE)*(1+Inputs!$B$2)", 0)
                            for _ in heads])
    grid.append(["Tax"] + [synth.Formula(f"={synth._col(m + 1)}2*Inputs!$B$3", 0) for m in range(12)])
    path = str(tmp_path / "lookup.xlsx")
    synth._write_xlsxwriter(path, [{"name": "Model", "rows": grid}, {"name": "Prices", "rows": prices},
                                   {"name": "Inputs", "rows": inputs}])
    a = Analysis([path])
    tabs = {i["numbers"]["sheet"] for i in _found(a, "model:input:")}
    assert tabs == {"Inputs"}, tabs


def test_a_break_no_words_describe_shows_the_two_formulas(tmp_path, monkeypatch):
    """When the difference cannot be put in words, the question shows the odd
    formula beside the row's usual one, moved to its cell."""
    assert findings._two_formulas("S!E4", "=D4*1.1+C2", "=B4*$B$9", (3, 2)) == \
        "is =D4*1.1+C2 where the row has =D4*$B$9"
    monkeypatch.setattr(formulas, "diff_words", lambda *a, **k: [])
    m, a = _built(tmp_path, 0, "model_plan")
    brk = _plant(m, "break")
    q = next(q for q in interview.candidates(a, {}) if q.id == "find_pattern_breaks")
    assert re.search(re.escape(brk["cell"]) + r"[^;]* is =\S+ where the row has =\S+", q.prompt), q.prompt


@pytest.mark.parametrize("in_step", [True, False])
def test_typed_history_summed_by_range_is_data_not_a_plan(tmp_path, in_step):
    """Typed monthly rows on a tab of their own: read month by month by a model,
    they are a plan block; read only through a SUM of the year, they are data."""
    heads = [f"{m} 2031" for m in synth._MONTHS]
    hist = [["Line"] + heads] + [[f"Line {k}"] + [10 * k + (m * 7) % 5 + m for m in range(12)] for k in range(1, 4)]
    if in_step:
        calc = [["Line"] + heads] + [[f"Out {k}"] + [synth.Formula(f"=History!{synth._col(m + 1)}{k + 1}*2", 0)
                                                     for m in range(12)] for k in range(1, 4)]
    else:
        calc = [["Line", "Year"]] + [[f"Out {k}", synth.Formula(f"=SUM(History!B{k + 1}:M{k + 1})", 0)]
                                     for k in range(1, 4)]
    path = str(tmp_path / f"hist{int(in_step)}.xlsx")
    synth._write_xlsxwriter(path, [{"name": "Calc", "rows": calc}, {"name": "History", "rows": hist}])
    a = Analysis([path])
    assert bool(_found(a, "formula:plan_block:History:")) == in_step


def test_an_unused_input_typed_in_several_cells_counts_the_others(tmp_path):
    m, a = _built(tmp_path, 0, "model_plan")
    fa = a.formulas[m["path"]]
    o = fa["_all"]["orphan_inputs"][0]
    o["twins"], o["twin_count"] = o["twins"][:1] * 3, 3
    out = []
    a._book_insights(a.books[0], out)
    stmt = next(i for i in out if i["recipe"] == "formula:orphan_inputs")["statement"]
    assert "and 2 other cells, and nothing links them" in stmt, stmt
    o["twin_count"] = 2
    out = []
    a._book_insights(a.books[0], out)
    assert "and 1 other cell, and nothing links them" in next(i for i in out if i["recipe"] ==
                                                              "formula:orphan_inputs")["statement"]


def test_level_rows_are_balances_and_check_rows_are_not():
    level = ["Cash", "Bank balance", "Cash in bank", "Loan balance", "Closing cash", "AR outstanding", "Cash on Hand"]
    flows = ["Cash in", "Cash out", "Net cash flow", "Opening cash", "Cash from operations", "Interest on loan balance",
             "Balance paid"]
    assert all(analyze._LEVEL_ROW.search(x) and not analyze._NOT_LEVEL.search(x) for x in level), level
    assert all(analyze._NOT_LEVEL.search(x) for x in flows), flows
    assert not formulas._CHECK_LABEL.search("Bank balance") and not formulas._CHECK_LABEL.search("Loan balance")
    assert all(formulas._CHECK_LABEL.search(x) for x in ("Balance check", "Check", "Difference", "Balancing",
                                                          "Out of balance"))


# --------------------------------------------------------------------------
# the noise budget
# --------------------------------------------------------------------------
OWN = {"model_plan": ("formula:plan_block:", "formula:tieout:"),
       "model_plan_inputs": ("formula:plan_block:", "formula:tieout:"),
       "model_actuals": ("formula:tieout:",), "fixed_ratio": ("ratio:",), "fee_line": ("ratio:",),
       "opposite_side": ("contra:",), "opening_entry": ("opening:",), "journal_three": ("ratio:", "contra:", "opening:"),
       "closing_entry": ("closing:",), "chart_side": ("contra:",),
       "link_multiplier": ("formula:plan_block:", "formula:tieout:"),          # practice round 1
       "opening_ties": ("opening:",), "carried_in": ("opening:",),             # practice round 3
       "contra_split": ("contra:",), "contra_vendor": ("contra:",)}            # practice round 4


def test_no_other_plant_and_no_twin_gets_a_model_or_group_finding(tmp_path):
    """Codes, boundaries, copies, panels, references and the rank 8 model: none of
    them is a typed plan block, a tie-out, a fixed share, an other-side line or an
    opening entry, and no twin of any trap is. The sweep takes the first 5 seeds."""
    for name in synth.TRAPS:
        for twin in (False, True):
            for seed in SEEDS[:5]:
                m = synth.build(tmp_path / f"{name}{seed}{int(twin)}.xlsx", seed, name, twin=twin)
                a = Analysis([m["path"]])
                got = [i["recipe"] for i in a.insights if i["recipe"].startswith(MODEL_RECIPES + GROUP_RECIPES)
                       and (twin or not i["recipe"].startswith(OWN.get(name, ("-",))))]
                assert not got, (name, twin, seed, got)


def test_the_noise_books_get_no_model_or_group_question(noise_fixtures):
    for paths in noise_fixtures:
        a = Analysis(paths)
        assert not _found(a, MODEL_RECIPES + GROUP_RECIPES), paths
        assert not _asks(a, ("find_plan_block_", "find_tieout_") + GROUP_ASKS), paths
