"""The question budget: one list ranked by value (stake x certainty x kind
weight), merged before it is ranked, with coverage kept per table, column and
aspect, and an honest cap. Every book here is synthetic (tests/synth.py): 15
planted issues of known stake, and a model whose one typed input feeds most of
its formulas."""
import math
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from conftest import SEEDS  # noqa: E402
from sheetbrain import findings, interview  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402


_BOOKS: dict = {}


def _book(tmp_path, seed, fresh=False):
    """The budget book for a seed, built and read once per run (tests that change
    the analysis ask for a fresh one)."""
    if seed not in _BOOKS:
        m = synth.build_budget(tmp_path / "budget.xlsx", seed)
        _BOOKS[seed] = (m, Analysis([m["path"]]))
    m, a = _BOOKS[seed]
    return (m, Analysis([m["path"]])) if fresh else (m, a)


def _qid(a, plant, kind="codes"):
    """The question a plant should raise: the one about its table and column."""
    tid = next(t.tid for t in a.tables if t.sheet == plant["sheet"])
    prefix = {"codes": "codes_", "blanks": "find_blank_", "exclusive": "find_exclusive_",
              "negatives": "find_negatives_"}[kind]
    return next(q.id for q in interview.candidates(a, {}) if q.id.startswith(prefix)
                and (q.meta.get("about") or {}).get("table") == tid
                and (q.meta.get("about") or {}).get("col") == plant["col"])


def _interview(a, pick=lambda q: ["not_sure"]):
    """Every round until the engine stops, each question answered by pick(q).
    Returns the answers and, per round, the questions asked and the ranked list
    they were drawn from."""
    state = {"answers": {}, "round": 0}
    rounds = []
    while True:
        pool = interview.ranked(interview.candidates(a, state["answers"]), first_round=state["round"] == 0)
        qs = interview.next_round(a, state)
        if not qs:
            break
        rounds.append((qs, pool))
        for q in qs:
            state["answers"][q.id] = interview._answer(q, pick(q), "")
        state["round"] += 1
    return state, rounds


def test_round_one_holds_the_two_highest_stake_findings(tmp_path, synth_seed):
    m, a = _book(tmp_path, synth_seed)
    top = sorted(m["plants"], key=lambda p: -p["stake"])[:2]
    first = {q.id for q in interview.next_round(a, {"answers": {}, "round": 0})}
    for p in top:
        assert _qid(a, p, p["kind"]) in first, (p, first)
    assert "goal" in first and len(first) <= interview.ROUND1


def _with_playbook_questions(a, plant):
    """Two playbook questions, each bound by a role: one on how the plant's
    column is treated (a detector found something in it), and one on the
    reference numbers of the same tab, which no detector touches."""
    t = next(t for t in a.tables if t.sheet == plant["sheet"])
    ref = t.headers[1]
    a.detection["roles"]["pb_group"] = {"table": t.tid, "header": plant["col"], "col": a.col(t.tid, plant["col"])}
    a.detection["roles"]["pb_ref"] = {"table": t.tid, "header": ref, "col": a.col(t.tid, ref)}
    opts = [{"id": "a", "label": "One thing", "desc": ""}, {"id": "b", "label": "Another thing", "desc": ""}]
    backed = {"id": "pb_group_counts", "header": "Groups", "kind": "rule", "priority": 2,
              "ask_if": ["has_role:pb_group"], "prompt": "Does every {role:pb_group} count toward the totals?",
              "options": opts}
    unbacked = {"id": "pb_ref_source", "header": "Refs", "kind": "definition", "priority": 2,
                "ask_if": ["has_role:pb_ref"], "prompt": "Where do the {role:pb_ref} numbers come from?",
                "options": opts}
    a.playbook = dict(a.playbook, questions=list(a.playbook.get("questions", [])) + [backed, unbacked])
    return backed["id"], unbacked["id"]


def test_no_playbook_question_goes_ahead_of_a_finding_worth_more(tmp_path):
    for seed in SEEDS:
        m, a = _book(tmp_path, seed, fresh=True)
        top = max((p for p in m["plants"] if p["kind"] == "codes"), key=lambda p: p["stake"])
        backed, unbacked = _with_playbook_questions(a, top)
        # the check below is not vacuous: before the finding is asked, the prior on its column is backed by it,
        # and ranks below it (round 3 fix 5)
        by = {q.id: q for q in interview.candidates(a, {})}
        cid = _qid(a, top)
        assert by[backed].meta.get("backed") and by[backed].value < by[cid].value, seed
        _state, rounds = _interview(a)
        seen = [q for qs, _pool in rounds for q in qs if q.source == "playbook"]
        assert unbacked not in {q.id for q in seen} and all(q.meta.get("backed") for q in seen), seed
        for qs, pool in rounds:
            asked = {q.id for q in qs}
            waiting = [q for q in pool if q.source == "finding" and q.id not in asked]
            for q in qs:
                if q.source == "playbook":
                    assert all(w.value <= q.value for w in waiting), (q.id, [(w.id, w.value) for w in waiting])


def test_a_prior_on_a_column_whose_finding_was_answered_backs_on_nothing(tmp_path, synth_seed):
    """Round 3 fix 5: a playbook question is backed only by findings still to ask
    on its column; once the finding there is answered it waits for room left over."""
    m, a = _book(tmp_path, synth_seed, fresh=True)
    top = max((p for p in m["plants"] if p["kind"] == "codes"), key=lambda p: p["stake"])
    backed, _unbacked = _with_playbook_questions(a, top)
    cid = _qid(a, top)
    q = next(q for q in interview.candidates(a, {}) if q.id == cid)
    after = {x.id: x for x in interview.candidates(a, {cid: interview._answer(q, ["not_sure"], "")})}
    assert not after[backed].meta.get("backed") and not interview.in_round(after[backed])


def test_the_cap_holds_and_the_closer_sits_outside_it(tmp_path, synth_seed):
    _m, a = _book(tmp_path, synth_seed)
    state, rounds = _interview(a)
    answers = state["answers"]
    assert interview.substantive(answers) == interview.HARD_CAP          # 14 findings, 10 asked
    assert "goal" in answers and len(rounds) <= interview.MAX_ROUNDS
    for k, (qs, _pool) in enumerate(rounds):
        subs = [q for q in qs if q.kind != "goal"]
        assert len(subs) <= (interview.ROUND1 - 1 if k == 0 else interview.ROUND_N)
    closer = interview.closer_question(a, answers)
    assert closer is not None and len(interview.render_ask([closer])["questions"][0]["options"]) == 3
    answers[closer.id] = interview._answer(closer, ["nothing"], "")
    assert interview.substantive(answers) == interview.HARD_CAP           # the closer is not counted
    assert interview.closer_question(a, answers) is None                  # and asked once


def test_a_model_input_feeding_most_formulas_outranks_the_model_priors(tmp_path, synth_seed):
    m = synth.build(tmp_path / "model.xlsx", synth_seed, "model_input", writer="xlsxwriter")
    a = Analysis([m["path"]])
    assert a.detection["archetype"] == "financial_model"
    by = {q.id: q for q in interview.candidates(a, {})}
    plug = by["find_typed_plug"]
    assert m["plants"][0]["cell"] in plug.prompt and plug.meta["stake"] > 0.5
    assert plug.value > by["money_scale"].value
    # costs are positive rows every subtotal subtracts: code states the sign rule, so it is never asked (rank 20j)
    assert "sign_rule" not in by and any(i["recipe"] == "model:sign" and i["settles"] for i in a.insights)
    # the typed factor in the last cash cell feeds nothing: the same kind of finding, worth less
    assert by["find_hardcoded"].meta["stake"] == 0 and by["find_hardcoded"].value < plug.value
    first = [q.id for q in interview.next_round(a, {"answers": {}, "round": 0})]
    assert first[:2] == ["goal", "find_typed_plug"]
    assert "money_scale" not in first and "sign_rule" not in first


def test_a_follow_up_at_one_and_a_half_does_not_jump_a_treatment_question_worth_more(tmp_path, synth_seed):
    m, a = _book(tmp_path, synth_seed)
    neg = next(p for p in m["plants"] if p["kind"] == "negatives")
    ex = next(p for p in m["plants"] if p["kind"] == "exclusive")
    by = {q.id: q for q in interview.candidates(a, {})}
    nq, xq = by[_qid(a, neg, "negatives")], by[_qid(a, ex, "exclusive")]
    # the negatives question says how they count with its pick; words typed with no pick open the net question
    answers = {nq.id: interview._answer(nq, [], "Refunds from the stores.")}
    cands = {q.id: q for q in interview.candidates(a, answers)}
    fu = next(q for q in cands.values() if q.id.startswith("follow_net_"))
    assert fu.gated and fu.value == pytest.approx(interview.FOLLOW_UP * interview.worth(fu.meta["stake"], "treatment"),
                                                  abs=0.01)
    assert xq.meta["about"]["aspect"] == "treatment" and cands[xq.id].value > fu.value
    order = [q.id for q in interview.ranked(list(cands.values()))]
    assert order.index(xq.id) < order.index(fu.id)            # a follow-up no longer goes first by rule


def test_answering_the_blanks_leaves_the_meaning_question_open(tmp_path, synth_seed):
    m, a = _book(tmp_path, synth_seed)
    blanks = next(p for p in m["plants"] if p["kind"] == "blanks")
    codes = next(p for p in m["plants"] if p["kind"] == "codes" and p["sheet"] == blanks["sheet"])
    bid, cid = _qid(a, blanks, "blanks"), _qid(a, codes, "codes")
    before = {q.id for q in interview.ranked(interview.candidates(a, {}))}
    assert len({bid, cid} & before) == 1                                  # one question per column per round
    bq = next(q for q in interview.candidates(a, {}) if q.id == bid)
    answers = {bid: interview._answer(bq, ["missing"], "")}
    tid = bq.meta["about"]["table"]
    assert (tid, blanks["col"], "blanks") in interview.covered_keys(answers)
    assert (tid, blanks["col"], "meaning") not in interview.covered_keys(answers)
    assert cid in {q.id for q in interview.ranked(interview.candidates(a, answers))}


def test_a_code_column_named_inside_another_question_id_is_still_asked(tmp_path, synth_seed):
    m, a = _book(tmp_path, synth_seed, fresh=True)
    p = max((p for p in m["plants"] if p["kind"] == "codes"), key=lambda p: p["stake"])
    cid = _qid(a, p)
    other = {"id": f"{p['col'].lower()}_history", "header": "History", "kind": "history", "priority": 2,
             "ask_if": [], "prompt": "Where does this tab come from?",
             "options": [{"id": "export", "label": "An export"}, {"id": "typed", "label": "Typed by hand"}]}
    a.playbook = dict(a.playbook, questions=list(a.playbook.get("questions", [])) + [other])
    ids = [q.id for q in interview.candidates(a, {})]
    assert other["id"] in ids and p["col"].lower() in other["id"]
    assert cid in ids and cid in [q.id for q in interview.next_round(a, {"answers": {}, "round": 0})]


def test_a_just_in_time_answer_gets_its_follow_up_before_the_preview(tmp_path):
    """A build pick that needs an answer asks it just in time; a rule typed in
    that answer is read back once, before the preview, not left unread."""
    import json
    import subprocess
    sb_py = os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py")

    def sb(*args):
        p = subprocess.run([sys.executable, sb_py, *args], capture_output=True, text=True, env=env, timeout=180)
        assert p.stdout, p.stderr
        return json.loads(p.stdout)
    m = synth.build_budget(tmp_path / "budget.xlsx", 3)
    book = m["path"]
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / "home"))
    r = sb("start", book)
    while r["next"] == "ask" and r["ask"]["questions"][0]["header"] != "Build":
        qs = r["ask"]["questions"]
        r = sb("answer", book, "--text", "b" if qs[0]["header"] == "Last one"
               else " ".join(f"{k} not sure" for k in range(1, len(qs) + 1)))
    labels = [o["label"] for o in r["ask"]["questions"][0]["options"]]
    k = next(i for i, o in enumerate(r["ask"]["questions"][0]["options"])
             if "needs one more answer" in o["description"])
    r = sb("answer", book, "--text", chr(97 + k))
    assert r["next"] == "ask" and len(r["ask"]["questions"]) == 1, (labels, r["say"])
    ex = next(p for p in m["plants"] if p["kind"] == "exclusive")
    r = sb("answer", book, "--text", f"Mostly one line per sale. Leave {ex['value']} out of every total.")
    asks = r["ask"]["questions"]
    assert r["next"] == "ask" and asks[0]["header"] == "Your rules"
    assert asks[0]["options"][0]["label"].startswith(f"{ex['col']} = {ex['value']} (")
    r = sb("answer", book, "--text", "a")
    assert r["next"] == "preview"
    rows = sb("preview", book, "--show-rows")["say"]
    assert f"Applied to counted numbers: rows of {ex['sheet']} where {ex['col']} is {ex['value']}" in rows


@pytest.mark.parametrize("seed", [6, 7])
def test_every_prompt_fits_fifteen_with_at_most_two_extras(tmp_path, seed):
    """A rule typed in round 1 is read back (one extra), the build pick needs one
    answer just in time (a second), and a rule typed in that answer would open a
    third readback: it waits, and the whole interview stays within 15 prompts."""
    import json
    import subprocess
    sb_py = os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py")

    def sb(*args):
        p = subprocess.run([sys.executable, sb_py, *args], capture_output=True, text=True, env=env, timeout=180)
        assert p.stdout, p.stderr
        return json.loads(p.stdout)
    m = synth.build_budget(tmp_path / "budget.xlsx", seed)
    book = m["path"]
    ex = next(p for p in m["plants"] if p["kind"] == "exclusive")
    bl = next(p for p in m["plants"] if p["kind"] == "blanks")
    code = next(p for p in m["plants"] if p["kind"] == "codes" and p["sheet"] == bl["sheet"])
    home = str(tmp_path / "home")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    shown = []
    r = sb("start", book)
    while r["next"] == "ask":
        qs = r["ask"]["questions"]
        shown += [q["header"] for q in qs]
        if qs[-1]["header"] == "Build":
            break
        if qs[0]["header"] == "Last one":
            reply = "b"
        elif len(shown) == len(qs):                    # round 1: the last one gets a typed rule
            reply = " ".join(f"{k} not sure" for k in range(1, len(qs))) + f" {len(qs)} Leave {ex['value']} out of totals."
        else:
            reply = " ".join(f"{k} a" if q["header"] == "Your rules" else f"{k} not sure" for k, q in enumerate(qs, 1))
        r = sb("answer", book, "--text", reply)
    assert shown.count("Your rules") == 1 and shown[-1] == "Build"
    opts = r["ask"]["questions"][-1]["options"]
    k = next(i for i, o in enumerate(opts) if "needs one more answer" in o["description"])
    r = sb("answer", book, "--text", chr(97 + k))
    assert r["next"] == "ask"
    shown += [q["header"] for q in r["ask"]["questions"]]
    r = sb("answer", book, "--text", f"Mostly one line per sale. Leave {code['value']} out of every total.")
    assert r["next"] == "preview"
    extras = len(shown) - 1 - interview.HARD_CAP - 2                   # past the goal, 10 answers, closer, build
    assert len(shown) <= 15 and extras <= 2, shown
    from sheetbrain.store import Store
    st = Store(home)
    try:
        answers = st.state(r["brain_id"])["answers"]
    finally:
        st.close()
    assert interview.substantive(answers) == interview.HARD_CAP + 1      # the just-in-time answer is an extra
    # the third readback was there to ask: the budget held it back
    assert findings.readback(Analysis([book]), answers) is not None


def test_every_finding_left_after_the_cap_is_an_open_item_with_its_evidence(tmp_path, synth_seed):
    _m, a = _book(tmp_path, synth_seed)
    state, _rounds = _interview(a)
    answers = state["answers"]
    left = [q for q in interview.candidates(a, answers) if q.source == "finding" and q.id not in answers]
    assert left                                                          # the cap left some unasked
    opens = {q.id: q for q in interview.open_items(a, state)}
    for q in left:
        assert q.id in opens and re.search(r"\d", opens[q.id].prompt), q.id
    recs = Composer(a, a.paths[0], "b1", answers).compose()
    open_ids = {r["id"] for r in recs if r["record"] == "open"}
    assert all(f"o:{q.id}" in open_ids for q in left)
    cover = next(r["statement"] for r in recs if r["id"] == "f:coverage")
    assert "Not said yet" in cover
