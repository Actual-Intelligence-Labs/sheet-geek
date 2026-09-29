"""Questions about THIS file: the oddities code finds, the answers that keep
their specifics, the guesses an answer retires, and owner rules that stay
scoped to what they were said about."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
from sheetbrain import findings, interview, rules  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402

FX = os.path.join(ROOT, "evals", "fixtures")


@pytest.fixture(scope="module")
def hotel():
    return Analysis([os.path.join(FX, "procurement_hotel.xlsx"), os.path.join(FX, "procurement_contracts.xlsx")])


@pytest.fixture(scope="module")
def ledger():
    return Analysis([os.path.join(FX, "ledger_gl.xlsx")])


@pytest.fixture(scope="module")
def model():
    return Analysis([os.path.join(FX, "finance_model.xlsx")])


def _q(analysis, prefix, answers=None):
    return next(q for q in interview.candidates(analysis, answers or {}) if q.id.startswith(prefix))


def _ans(q, raw):
    return interview.parse_answers([q], raw)[q.id]


def test_new_checks_fire_only_on_the_planted_problem(hotel, ledger, model):
    kinds = lambda a: sorted(i["recipe"].split(":")[0] for i in a.insights  # noqa: E731
                             if i["recipe"].startswith(("blanks", "signflip", "exclusive")))
    assert kinds(hotel) == ["exclusive"]                       # CMSY and its TR- transfers, not vendor prefixes
    assert kinds(ledger) == ["blanks", "signflip"]             # blank Class, Credit flips sign on Apr 1
    assert kinds(model) == []


def test_sign_flip_found_at_the_system_change(ledger):
    i = next(i for i in ledger.insights if i["recipe"].startswith("signflip"))
    assert i["numbers"]["date"] == "2025-04-01" and i["numbers"]["col"] == "Credit"


def test_commissary_answer_leaves_its_rows_out_of_totals(hotel):
    q = _q(hotel, "find_exclusive_")
    assert "CMSY" in q.prompt and "TR-" in q.prompt
    excl = rules.exclusions(hotel, {q.id: _ans(q, "a")})
    assert any("cmsy" in vals for vals in excl.values())
    assert rules.exclusions(hotel, {q.id: _ans(q, "c")}) == {}  # "a real location": nothing left out


def test_a_rebate_rule_does_not_drop_fees_from_spend(hotel):
    said = {"rebate_programs": {"options": ["distributor"], "text": "Coastline pays 2% back; fees and CMSY don't "
                                "count", "kind": "definition", "not_sure": False}}
    assert rules.exclusions(hotel, said) == {}


def test_cells_are_named_in_words(model):
    q = _q(model, "find_typed_plug")
    assert "Cash, May 2027 (Balance Sheet!R4)" in q.prompt
    assert "Balance Check row fails" in q.prompt              # the failing check is folded in, not asked twice
    ids = {c.id for c in interview.candidates(model, {})}
    assert "find_check" not in ids and "overrides" not in ids
    breaks = _q(model, "find_pattern_breaks")
    assert "P&L!N4" not in breaks.prompt                      # asked on its own as a typed factor


def test_finding_answers_keep_their_specifics(model, tmp_path):
    q = _q(model, "find_typed_plug")
    answers = {q.id: _ans(q, "b"), "goal": {"options": ["audit_it"], "labels": ["Check it for errors"],
                                             "text": "", "not_sure": False}}
    comp = Composer(model, os.path.join(FX, "finance_model.xlsx"), "b1", answers)
    stmts = [r["statement"] for r in comp.compose() if r.get("source") == "told"]
    assert any("Balance Sheet!R4" in s and "mistake" in s.lower() for s in stmts)


def test_an_answer_retires_the_matching_guess(hotel):
    q = _q(hotel, "find_negatives_")
    path = os.path.join(FX, "procurement_hotel.xlsx")
    # the guess itself: an open item ('Not answered yet: ...') is also inferred and may say 'negative'
    before = [r["statement"] for r in Composer(hotel, path, "b1", {}).compose()
              if r.get("record") == "fact" and r.get("source") == "inferred"
              and "negative" in r["statement"].lower()]
    after = [r["statement"] for r in Composer(hotel, path, "b1", {q.id: _ans(q, "a")}).compose()
             if r.get("record") == "fact" and r.get("source") == "inferred"
             and "negative" in r["statement"].lower()]
    assert before and not after


def test_suspense_follow_up_lists_the_parked_lines(ledger):
    q = _q(ledger, "find_unmatched_")
    assert q.options[0]["id"] == "suspense"
    fu = findings.follow_ups(ledger, {q.id: _ans(q, "a")})
    assert fu and "WIRE OUT" in fu[0].prompt and "$8,750.00" in fu[0].prompt


def test_typed_request_at_the_build_step_is_kept_word_for_word(ledger):
    bq = interview.build_question(ledger, {"goal": {"options": ["monthly_pnl"], "labels": [], "not_sure": False}})
    assert bq.options[0]["id"] == "monthly_pnl"               # the owner's goal leads the menu
    a = _ans(bq, "A 2025 P&L by class for the CPA")
    assert a["options"] == [] and a["text"] == "A 2025 P&L by class for the CPA"
    assert _ans(bq, "b")["options"] == [bq.options[1]["id"]]


def test_ill_type_them_with_nothing_typed_stays_open():
    q = interview.Q("codes_x", "Codes", "?", [{"id": "type", "label": "I'll type them"},
                                              {"id": "skip", "label": "Skip for now"}])
    assert _ans(q, "a")["not_sure"] is True
    assert _ans(q, "a S1 = new lead")["text"] == "S1 = new lead"
