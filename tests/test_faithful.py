"""The brain never says what the owner did not say: a typed reply stays the
owner's words, a pick comes only from an explicit letter, a note carries
exactly what was picked and typed, and every option makes one claim the owner
could see."""
import datetime as dt
import glob
import json
import os
import re
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "skills", "spreadsheet-brain", "scripts"))
from sheetbrain import findings, interview  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer, _answer_notes, _said_sentences  # noqa: E402

FX = os.path.join(ROOT, "evals", "fixtures")
PLAYBOOKS = os.path.join(ROOT, "skills", "spreadsheet-brain", "playbooks")
LINTED = {"exclusion", "definition", "coverage", "rule", "unit"}
ONE_CONCEPT = {"Total and subtotal rows"}          # reads as one thing, though it has an 'and'

GOAL = interview.Q("goal", "Goal", "What do you want from this sheet? Pick all that apply.",
                   [{"id": "monthly", "label": "See a clean monthly report"},
                    {"id": "tidy", "label": "Clean up the records"},
                    {"id": "costs", "label": "Cut costs"},
                    {"id": "plan", "label": "Compare to plan"}], multi=True, kind="goal")
TYPED_GOAL = "Close the year for the accountant with holds cleaned up, a report by team, and draws kept out"


def _book(path, total_row=False):
    """A small log: an ID, a date, a site, a unit column and a channel column
    whose values happen to be unit codes, a quantity and an amount with a few
    negatives."""
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Log")
    ws.write_row(0, 0, ["Ref", "Entry Date", "Site", "UOM", "Channel", "Qty", "Amount"])
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    n = 60
    for i in range(n):
        ws.write(i + 1, 0, f"R-{1000 + i}")
        ws.write_datetime(i + 1, 1, dt.datetime(2026, 1, 1) + dt.timedelta(days=i), fmt)
        ws.write_row(i + 1, 2, [["North", "South", "East"][i % 3], ["CS", "EA"][i % 2], ["CS", "CT"][i % 2],
                                (i % 5) + 1, -12.5 if i % 11 == 0 else 20.0 + i])
    if total_row:
        ws.write_row(n + 1, 0, ["Total", None, None, None, None, sum((i % 5) + 1 for i in range(n)),
                                sum(-12.5 if i % 11 == 0 else 20.0 + i for i in range(n))])
    wb.close()
    return str(path)


def _ans(q, raw):
    return interview.parse_answers([q], raw)[q.id]


def _q(analysis, prefix, answers=None):
    return next(q for q in interview.candidates(analysis, answers or {}) if q.id.startswith(prefix))


def _told(recs, qid):
    return [r["statement"] for r in recs if r.get("source") == "told" and r.get("ref") == f"q:{qid}"
            and r.get("record") == "fact"]


def _bundled(label: str) -> bool:
    """An option label that makes more than one claim: two nouns joined by
    'and', a comma list, a list in its aside, or a claim tacked on after a comma."""
    if label in ONE_CONCEPT:
        return False
    return bool(re.search(r"\band\b", label, re.I) or re.search(r",[^,]*\b(or|and)\b", label)
                or label.count(",") >= 2 or re.search(r"\([^)]*,[^)]*\)", label)
                or re.search(r",\s*not\b", label, re.I))


# --------------------------------------------------------------------------
# replies stay the owner's words
# --------------------------------------------------------------------------
def test_a_typed_goal_is_never_filed_as_a_pick():
    for raw in (TYPED_GOAL, {"Goal": TYPED_GOAL}):
        a = interview.parse_answers([GOAL], raw)["goal"]
        assert a["options"] == [] and a["labels"] == []
        assert a["inferred_options"] == []                     # 'clean' is in two labels: no guess
        assert a["text"] == TYPED_GOAL


def test_a_goal_is_inferred_only_when_its_words_point_at_one_option():
    a = _ans(GOAL, "mostly to find where costs went up")
    assert a["options"] == [] and a["inferred_options"] == ["costs"]
    assert interview.goal_ids({"goal": a}) == ["costs"]                   # ranking may use it
    assert interview.goal_ids({"goal": dict(a, options=["plan"])}) == ["plan"]


def test_a_typed_goal_opens_the_gates_of_the_goals_it_clearly_names(tmp_path):
    """Practice round 4, fix 3: a typed goal that holds every topic word of a goal's
    label opens that goal's gated questions, as a pick does; one that shares only
    some of a label's topic words ranks by it and opens nothing. Neither is a pick."""
    an = Analysis([_book(tmp_path / "log.xlsx")])
    gated = [{"id": "months_final", "header": "Closed", "kind": "history", "priority": 1,
              "ask_if": ["goal:monthly"], "prompt": "Which months are final?",
              "options": [{"id": "all", "label": "All of them"}, {"id": "none", "label": "None yet"}]},
             {"id": "plan_source", "header": "Plan from", "kind": "history", "priority": 1,
              "ask_if": ["goal:plan"], "prompt": "Where does the plan come from?",
              "options": [{"id": "budget", "label": "The budget"}, {"id": "other", "label": "Somewhere else"}]}]
    an.playbook = dict(an.playbook, goals=[{"id": o["id"], "label": o["label"]} for o in GOAL.options],
                       questions=list(an.playbook.get("questions", [])) + gated)
    goal_q = next(q for q in interview.candidates(an, {}) if q.id == "goal")
    said = interview.parse_answers([goal_q], "I want a monthly view")["goal"]
    assert said["options"] == [] and said["inferred_options"] == ["monthly"]
    ids = {q.id for q in interview.candidates(an, {"goal": said})}
    assert "months_final" in ids and "plan_source" not in ids
    partly = interview.parse_answers([goal_q], "keep to the plan")["goal"]
    assert partly["options"] == [] and partly["inferred_options"] == ["plan"]     # ranks by it
    assert "plan_source" not in {q.id for q in interview.candidates(an, {"goal": partly})}
    picked = {"goal": dict(partly, options=["plan"], labels=["Compare to plan"])}
    assert "plan_source" in {q.id for q in interview.candidates(an, picked)}


def test_a_letter_is_a_pick_only_when_it_is_marked_as_one():
    build = interview.Q("_build", "Build", "What should I build?", [
        {"id": "report", "label": "A monthly report"}, {"id": "guide", "label": "A guide"},
        {"id": "check", "label": "A check"}, {"id": "app", "label": "An app"}], kind="build")
    a = _ans(build, "a report by team")
    assert a["options"] == [] and a["text"] == "a report by team"      # the article, not option a
    a = _ans(build, "a) report by team")
    assert a["options"] == ["report"] and a["text"] == "report by team"
    for said in ("a.m. deliveries only", "a-la-carte orders"):     # a mark with a letter after it is a word
        a = _ans(build, said)
        assert a["options"] == [] and a["text"] == said, said
    assert _ans(build, "a - report by team")["options"] == ["report"]
    codes = interview.Q("codes_x", "Codes", "What do the codes mean?", [
        {"id": "type", "label": "I'll type them"}, {"id": "skip", "label": "Skip for now"}])
    a = _ans(codes, "a S1 = new lead")
    assert a["options"] == ["type"] and a["text"] == "S1 = new lead"
    multi = interview.Q("m", "M", "Which apply?", [{"id": x, "label": x.title()} for x in ("one", "two", "six")],
                        multi=True)
    assert _ans(multi, "a bed and breakfast")["options"] == []
    a = _ans(multi, "a bad day")                              # every letter in range, still words
    assert a["options"] == [] and a["text"] == "a bad day"
    assert _ans(multi, "a, b")["options"] == ["one", "two"]
    assert _ans(multi, "ab and the rest")["options"] == ["one", "two"]
    assert interview.parse_answers([multi, codes], "1 a bed 2a S1 = new lead")["m"]["text"] == "a bed"


def test_not_sure_is_always_its_own_letter():
    q = interview.Q("four", "Four", "Which one?", [{"id": f"o{i}", "label": f"Option {i}"} for i in range(4)])
    text = interview.render_text([q])
    assert "e) Not sure" in text
    assert _ans(q, "1e")["not_sure"] is True and _ans(q, "e")["not_sure"] is True
    ask = interview.render_ask([q])["questions"][0]
    # changed on purpose (round 3, fix 26): Not sure keeps its place in the tool's four, and the option past
    # three is named to type
    assert [o["label"] for o in ask["options"]] == ["Option 0", "Option 1", "Option 2", "Not sure"]
    assert 'Or choose Other and type "Option 3".' in ask["question"]
    three = interview.Q("three", "Three", "Which one?", [{"id": f"o{i}", "label": f"Option {i}"} for i in range(3)])
    assert [o["label"] for o in interview.render_ask([three])["questions"][0]["options"]][-1] == "Not sure"
    assert "d) Not sure" in interview.render_text([three])
    assert "Not sure" not in interview.render_text([GOAL])            # the goal and the build have none


def test_commas_split_a_reply_only_between_option_labels():
    q = interview.Q("basis", "Price basis", "What's in the price?",
                    [{"id": "deals", "label": "Contract deals"}, {"id": "rebates", "label": "Rebates later"}],
                    multi=True)
    assert interview.parse_answers([q], {"Price basis": "Contract deals, Rebates later"})["basis"]["options"] == \
        ["deals", "rebates"]
    typed = "cleaned up, a report by team, and draws kept out"
    a = interview.parse_answers([q], {"Price basis": typed})["basis"]
    assert a["options"] == [] and a["text"] == typed


def test_the_text_menu_shows_what_each_option_means():
    q = interview.Q("x", "X", "What is it?", [{"id": "a", "label": "Internal entries",
                                               "desc": "Transfers or bookkeeping"},
                                              {"id": "b", "label": "A real site"}])
    text = interview.render_text([q])
    assert "a) Internal entries: Transfers or bookkeeping" in text and "b) A real site" in text


def test_unit_codes_are_skipped_only_in_a_unit_column(tmp_path):
    an = Analysis([_book(tmp_path / "log.xlsx")])
    about = [q.meta.get("about") or {} for q in interview.candidates(an, {})]
    assert any(ab.get("col") == "Channel" and ab.get("aspect") == "meaning" for ab in about)   # 'CS' is a code here
    assert not any(ab.get("col") == "UOM" and ab.get("aspect") == "meaning" for ab in about)   # and a unit here
    assert interview.is_unit_col("UOM") and interview.is_unit_col("Pack Size") and interview.is_unit_col("x", "uom")
    assert not interview.is_unit_col("Channel") and not interview.is_unit_col("Unit Price")


# --------------------------------------------------------------------------
# every option makes one claim the owner could see
# --------------------------------------------------------------------------
def test_the_label_lint_catches_bundles():
    for bad in ("Owner draws and contributions", "Discounts and comps", "Voids and cancelled orders",
                "Delivery, fuel and drop fees", "Right size, industry or area", "Not real items (fees, holds)",
                "Internal, not a real location"):
        assert _bundled(bad), bad
    for ok in ("Total and subtotal rows", "Credits or returns", "Yes, one base unit", "Cash, adjusted at year end"):
        assert not _bundled(ok), ok


def test_every_playbook_question_keeps_the_contract():
    for path in sorted(glob.glob(os.path.join(PLAYBOOKS, "*.json"))):
        pb = json.load(open(path, encoding="utf-8"))
        for d in pb.get("questions", []):
            where = f"{os.path.basename(path)}:{d['id']}"
            q = interview.Q(d["id"], d["header"], d["prompt"], d.get("options", []), kind=d.get("kind", "definition"))
            shown = interview._options_for(q)
            assert shown[-1]["id"] == "not_sure", where
            assert len([o for o in shown if o["id"] != "not_sure"]) <= 3, where
            if q.kind in LINTED:
                assert not [o["label"] for o in q.options if _bundled(o["label"])], where
            if d.get("recommend"):
                assert d.get("recommend_if"), where                  # a prior alone never recommends
            for oid, stmt in ((d.get("fact") or {}).get("statements") or {}).items():
                o = next(o for o in q.options if o["id"] == oid)
                assert not interview.unseen_words(stmt, d["prompt"], o["label"], o.get("desc", "")), where


@pytest.mark.parametrize("path", [os.path.join(FX, "procurement_hotel.xlsx"), os.path.join(FX, "crm_contacts.csv"),
                                  os.path.join(FX, "messy_multitable.xlsx")], ids=os.path.basename)
def test_the_goal_and_build_prompts_show_at_most_four_and_every_other_prompt_three(path):
    """PLAN.md acceptance bar 3, as recorded on 2026-09-28: the goal and build
    prompts are exempt from the 3-option count (and from Not sure), with at most
    4 options each. Every other prompt the engine can put first shows at most 3
    real options plus Not sure."""
    a = Analysis([path])
    qs = interview.candidates(a, {})
    goal = next((q for q in qs if q.id == "goal"), None)
    build = interview.build_question(a, {})
    for q in [x for x in (goal, build) if x is not None]:
        shown = interview._options_for(q)
        assert len(shown) <= 4 and "not_sure" not in [o["id"] for o in shown], q.id
    for q in qs:
        if q.kind in ("goal", "build"):
            continue
        shown = interview._options_for(q)
        assert shown[-1]["id"] == "not_sure" and len(shown) - 1 <= 3, q.id


def _file_questions(a):
    """Every question code asks about this file: findings, the follow-ups each
    pick opens, alias pairs, and what a grown file would ask."""
    first = list(findings.finding_questions(a, {}))
    out = list(first)
    for q in first:
        for o in q.options:
            out += findings.follow_ups(a, {q.id: interview._answer(q, [o["id"]], "")})
    out += interview._alias_questions(a, {})
    answers = {}
    for q in first:
        if q.id.startswith("find_unmatched_"):
            answers[q.id] = dict(interview._answer(q, [o["id"] for o in q.options[:2]], ""), keys=q.meta["keys"][:1])
    report = {"new_values": {}}
    t = a.main_table
    c = next((c for c in a.cols[t.tid] if c.semantic == "dimension" and c.type == "text" and not c.sensitive),
             None) if t is not None else None
    if c is not None:
        report["new_values"][c.header] = [str(k) for k, _ in c.top[:2]]
    return out + findings.grow_questions(a, answers, report, since="2026-07-01")


FIXTURES = sorted(glob.glob(os.path.join(FX, "*.xlsx"))) + [os.path.join(FX, "crm_contacts.csv"),
                                                            os.path.join(FX, "crm_deals.csv")]


@pytest.mark.parametrize("path", FIXTURES, ids=os.path.basename)
def test_every_question_about_a_file_keeps_the_contract(path):
    a = Analysis([path])
    env = interview.Env(a, {})
    for q in _file_questions(a):
        shown = interview._options_for(q)
        assert shown[-1]["id"] == "not_sure", q.id
        assert len([o for o in shown if o["id"] != "not_sure"]) <= 3, q.id
        if q.kind in LINTED:
            assert not [o["label"] for o in q.options if _bundled(o["label"])], q.id
        if q.source == "finding":
            ab = q.meta.get("about") or {}
            t = next((t for t in a.tables if t.tid == ab.get("table")), None)
            assert t is not None, q.id
            assert ab["col"] in t.headers or ab["col"] in a.row_labels(t) or ab["aspect"] == "scope", q.id
            assert re.search(r"\d", q.prompt), q.id                  # a counted number from this file
        for oid, tpl in ((q.fact or {}).get("statements") or {}).items():
            o = next((o for o in q.options if o["id"] == oid), None)
            if o is None:
                continue
            stmt = interview.fill(tpl, env, interview._answer(q, [oid], ""))
            assert not interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)), (q.id, oid)


# --------------------------------------------------------------------------
# notes say exactly what was picked and what was typed
# --------------------------------------------------------------------------
def test_typed_words_never_sit_under_a_template_verb(tmp_path):
    path = _book(tmp_path / "log.xlsx")
    a = Analysis([path])
    q = _q(a, "find_negatives_")
    said = "They are refunds we gave back when an order went wrong"
    ans = _ans(q, said)
    assert ans["options"] == [] and ans["text"] == said
    assert ans["about"]["col"] == "Amount"                          # what the question was about travels with it
    stmt = _told(Composer(a, path, "b1", {q.id: ans}).compose(), q.id)
    assert len(stmt) == 1 and stmt[0].startswith('Asked "') and f'"{said}."' in stmt[0]
    assert "are, per the owner" not in stmt[0]


def test_one_pick_with_typed_words_quotes_both(tmp_path):
    path = _book(tmp_path / "log.xlsx")
    a = Analysis([path])
    q = _q(a, "find_negatives_")
    ans = _ans(q, "a: mostly refunds from the patio")
    assert ans["options"] == ["credits"]
    stmt = _told(Composer(a, path, "b1", {q.id: ans}).compose(), q.id)[0]
    # changed on purpose (practice round 4, fix 4): a pick with typed words is one note, the pick's curated
    # sentence (every word of it on screen) and then the owner's words
    label = next(o["label"] for o in q.options if o["id"] == "credits")
    cur = (q.fact.get("statements") or {}).get("credits")
    if cur:
        assert stmt.startswith(interview.fill(cur, interview.Env(a, {}), ans).rstrip(".")[:40])
    else:
        assert f'"{label}"' in stmt
    assert stmt.endswith('; the owner wrote: "mostly refunds from the patio."')


def test_every_pick_is_kept():
    hotel = os.path.join(FX, "procurement_hotel.xlsx")
    a = Analysis([hotel, os.path.join(FX, "procurement_contracts.xlsx")])
    q = _q(a, "find_exclusive_")
    stmt = _told(Composer(a, hotel, "b1", {q.id: _ans(q, "ab")}).compose(), q.id)
    # changed on purpose (round 3, fix 3): one note per pick, each its own curated sentence (or its label
    # with the description shown), so neither pick is lost and neither note carries the other's claim
    # changed on purpose (practice round 3b): a first note names every pick, so no single pick's note
    # reads as the whole answer; then one note per pick
    assert len(stmt) == 3
    labels = [o["label"] for o in q.options if o["id"] in _ans(q, "ab")["options"]]
    assert all(lab in stmt[0] for lab in labels), stmt[0]
    for lab, note in zip(labels, stmt[1:]):
        assert lab.lower() in note.lower(), (lab, note)


@pytest.mark.parametrize("ic", ["Doc No", "Ref", "Voucher"])
def test_an_id_prefix_note_names_the_id_header_as_written(tmp_path, ic):
    """'Doc No' made plural is 'Doc Nos', a word the owner never saw, which sent
    the note to the Q&A frame. Each curated statement uses the header as shown."""
    import random
    rng = random.Random(5)
    rows = [["Date", ic, "Site", "Amount"]]
    for k in range(240):
        site = rng.choice(["Kalo", "Venmi", "Torsil", "Hub"])
        doc = f"TR-{5000 + k}" if site == "Hub" else rng.choice(["IN", "CR", "PO"]) + f"-{1000 + k}"
        rows.append([dt.datetime(2031, 1, 1) + dt.timedelta(days=k), doc, site, round(rng.uniform(10, 500), 2)])
    wb = xlsxwriter.Workbook(str(tmp_path / "ids.xlsx"))
    ws = wb.add_worksheet("Log")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            ws.write_datetime(r, c, v, fmt) if isinstance(v, dt.datetime) else ws.write(r, c, v)
    wb.close()
    a = Analysis([str(tmp_path / "ids.xlsx")])
    q = _q(a, "find_exclusive_")
    env = interview.Env(a, {})
    ids = [o["id"] for o in q.options]          # the third option says what else it is (round 3, fix 4)
    for oid in ids:
        o = next(o for o in q.options if o["id"] == oid)
        stmt = interview.fill(q.fact["statements"][oid], env, interview._answer(q, [oid], ""))
        assert not interview.unseen_words(stmt, q.prompt, o["label"], interview._shown_desc(q, o)), (oid, stmt)
        told = _told(Composer(a, str(tmp_path / "ids.xlsx"), "b1", {q.id: _ans(q, "abc"[ids.index(oid)])})
                     .compose(), q.id)
        assert told == [stmt], (oid, told)
    assert f"{ic}s" not in q.fact["statement"]


def test_ok_takes_no_recommendation_the_data_rules_out(tmp_path):
    path = _book(tmp_path / "log.xlsx")
    a = Analysis([path])
    assert not any(t.totals_rows for t in a.tables)
    q = _q(a, "leave_out")
    assert q.recommend is None
    ans = _ans(q, "ok")
    assert ans["not_sure"] is True and ans["options"] == []
    assert not _told(Composer(a, path, "b1", {q.id: ans}).compose(), q.id)


def test_a_total_row_backs_the_total_row_recommendation(tmp_path):
    a = Analysis([_book(tmp_path / "log.xlsx", total_row=True)])
    if not any(t.totals_rows for t in a.tables):
        pytest.skip("this table reader does not mark the total row")
    assert _q(a, "leave_out").recommend == "totals"


def test_a_doubt_stays_with_its_own_sentence(tmp_path):
    path = _book(tmp_path / "log.xlsx")
    a = Analysis([path])
    q = _q(a, "find_negatives_")
    said = "Not sure which method. On group G the price is per case of 12."
    notes = _told(Composer(a, path, "b1", {q.id: _ans(q, said)}).compose(), q.id)
    assert len(notes) == 2
    assert "not sure" in notes[0].lower() and "not sure" not in notes[1].lower()
    assert '"On group G the price is per case of 12."' in notes[1]


def test_a_curated_sentence_needs_every_word_on_screen():
    q = interview.Q("x", "X", "12 rows have a blank Site. What does it mean?",
                    [{"id": "shared", "label": "Shared by all of them", "desc": "It belongs to no single site"}],
                    fact={"kind": "definition", "statements": {
                        "shared": "A blank Site (12 rows) is shared by all of them, per the owner."}})
    assert _answer_notes(interview._answer(q, ["shared"], ""), q.fact, None) == \
        [("A blank Site (12 rows) is shared by all of them, per the owner.", "")]
    q.fact["statements"]["shared"] = "A blank Site (12 rows) is overhead, per the owner."   # 'overhead' was never shown
    stmt = _answer_notes(interview._answer(q, ["shared"], ""), q.fact, None)[0][0]
    assert stmt.startswith('Asked "12 rows have a blank Site') and '"Shared by all of them"' in stmt
    assert "overhead" not in stmt


def test_an_accepted_recommendation_says_so():
    q = interview.Q("fill", "Group label", "Site is written only on each group's first row (8 rows). Apply below?",
                    [{"id": "yes", "label": "Yes, it applies below"}, {"id": "no", "label": "No, blanks mean blank"}],
                    recommend="yes", recommend_basis="Every blank sits under a filled row",
                    fact={"kind": "rule", "statements": {"yes": "Site applies to the rows below, per the owner."}})
    ans = _ans(q, "ok")
    assert ans["accepted"] and ans["options"] == ["yes"]
    stmt = _answer_notes(ans, q.fact, None)[0][0]
    assert 'the owner accepted the recommended "Yes, it applies below"' in stmt
    assert _answer_notes(_ans(q, "a"), q.fact, None)[0][0] == "Site applies to the rows below, per the owner."


def test_typed_sentences_split_where_a_sentence_ends():
    said = _said_sentences("Est. Loss is net of credits. Approx. 5 cases a week, e.g. on Fridays. Done", {"Est. Loss"})
    assert said == ["Est. Loss is net of credits.", "Approx. 5 cases a week, e.g. on Fridays.", "Done"]
    assert _said_sentences("cost is $2.10 a case. then fees") == ["cost is $2.10 a case. then fees"]


def test_a_lone_capital_ends_a_sentence_but_an_initialism_does_not():
    assert _said_sentences("Not sure about A. B is a fee.") == ["Not sure about A.", "B is a fee."]
    assert _said_sentences("The code is A. The other is B.") == ["The code is A.", "The other is B."]
    assert _said_sentences("Sold in the U.S. Mostly online.") == ["Sold in the U.S. Mostly online."]
    assert _said_sentences("Only the U. S. Stores count.") == ["Only the U. S. Stores count."]


# --------------------------------------------------------------------------
# review fixes: the article, marks, skips, lone letters, what the owner saw
# --------------------------------------------------------------------------
CODES = interview.Q("codes_x", "Codes", "What do the codes in Site mean (N1, S2)?", [
    {"id": "type", "label": "I'll type them", "desc": "Type the meanings as your answer"},
    {"id": "tab", "label": "A tab or file explains them", "desc": "I'll link it, not copy it"},
    {"id": "skip", "label": "Skip for now", "desc": "They stay unexplained, marked open"}],
    fact={"kind": "definition", "statement": "The codes in Site mean: {answer_text}."})


def test_the_article_a_never_opens_a_type_it_option():
    for said in ("a few are fees, the rest are sites", "a lot of these are old store codes"):
        a = _ans(CODES, said)
        assert a["options"] == [] and a["text"] == said, said           # byte for byte, 'a' kept
    a = _ans(CODES, "a S1 = new lead")
    assert a["options"] == ["type"] and a["text"] == "S1 = new lead"
    assert _ans(CODES, "a) few are fees")["options"] == ["type"]          # marked: a pick


def test_a_typed_code_answer_is_the_owners_words_alone():
    stmt = _answer_notes(_ans(CODES, "a S1 = new lead. S2 = lost"), CODES.fact, None)
    # changed on purpose (v02a fix brief B3): the question once, the notes after it name its header
    assert [s for s, _ in stmt] == ['Asked "What do the codes in Site mean (N1, S2)?", the owner wrote: '
                                    '"S1 = new lead."',
                                    'On "Codes", the owner also wrote: "S2 = lost."']
    assert not any("type them" in s for s, _ in stmt)


def test_skip_for_now_is_open_not_a_note():
    a = _ans(CODES, "c")
    assert a["not_sure"] is True and a["options"] == [] and a["labels"] == []
    assert _answer_notes(a, CODES.fact, None) == []


def test_a_letter_past_the_last_option_is_not_sure():
    three = interview.Q("three", "Three", "Which one?", [{"id": f"o{i}", "label": f"Option {i}"} for i in range(3)])
    other = interview.Q("other", "Other", "And this?", [{"id": f"p{i}", "label": f"Pick {i}"} for i in range(2)])
    for raw in ("e", "e."):
        a = _ans(three, raw)
        assert a["not_sure"] is True and a["options"] == [] and a["text"] == "", raw
    a = interview.parse_answers([three, other], "1e 2a")
    assert a["three"]["not_sure"] is True and a["three"]["text"] == "" and a["other"]["options"] == ["p0"]


def test_a_note_uses_only_the_description_the_ask_tool_showed():
    long = "Money that comes back later. " + " ".join(["It is shown on the next statement as a credit line."] * 5)
    q = interview.Q("x", "X", "What are they?", [{"id": "back", "label": "Money back", "desc": long},
                                                 {"id": "fix", "label": "Fixes"}])
    shown = interview.render_ask([q])["questions"][0]["options"][0]["description"]
    assert len(shown) <= interview.DESC_MAX < len(long)
    assert interview._answer(q, ["back"], "")["descs"]["back"] == shown


def test_the_reply_tip_shows_a_letter_with_words():
    q = interview.Q("x", "X", "What is it?", [{"id": "a", "label": "One"}, {"id": "b", "label": "Two"}])
    assert 'a letter and your words like "a) ..."' in interview.render_text([q])


def test_a_private_sentence_leaves_the_rest_byte_for_byte(tmp_path):
    path = _book(tmp_path / "log.xlsx")
    a = Analysis([path])
    q = _q(a, "find_negatives_")
    ans = _ans(q, "Refunds mostly\nsome are voids. John is lazy about keying them in.")
    comp = Composer(a, path, "b1", {q.id: ans})
    notes = _told(comp.compose(), q.id)
    assert len(notes) == 2 and notes[0].endswith('"Refunds mostly."') and notes[1].endswith('"some are voids."')
    assert not any("John" in n for n in notes) and any("John" in s for s, _ in comp.private)


def test_rows_slot_counts_the_filled_rows_of_a_role(tmp_path):
    a = Analysis([_book(tmp_path / "log.xlsx")])
    env = interview.Env(a, {})
    rid = next(r for r, info in a.detection["roles"].items() if info.get("col") is not None
               and info["col"].type == "number")
    assert interview.fill(f"{{rows:{rid}}}", env) == f"{env.col(rid).count:,}"


def test_a_recommendation_needs_the_data_to_back_it():
    a = Analysis([os.path.join(FX, "crm_contacts.csv")])
    env = interview.Env(a, {})
    c = env.col("email")
    assert env.holds("repeats:email") == (c.distinct < c.count)
    q = next(q for q in interview.candidates(a, {}) if q.id == "merge_dups")
    assert (q.recommend == "review") == env.holds("repeats:email")
    if q.recommend:
        assert f"{c.distinct:,}" in q.recommend_basis and "{" not in q.recommend_basis
    pb = json.load(open(os.path.join(PLAYBOOKS, "procurement.json"), encoding="utf-8"))
    assert not next(d for d in pb["questions"] if d["id"] == "same_item").get("recommend")


def test_a_new_name_with_no_near_spelling_gets_no_recommendation(tmp_path):
    a = Analysis([_book(tmp_path / "log.xlsx")])
    q = next(q for q in findings.grow_questions(a, {}, {"new_values": {"Site": ["East"]}}, since="2026-07-01")
             if q.id.startswith("grow_new_"))
    assert q.recommend is None
    ans = _ans(q, "ok")
    assert ans["not_sure"] is True and ans["options"] == []


@pytest.mark.parametrize("path", FIXTURES, ids=os.path.basename)
def test_every_playbook_question_about_a_column_carries_a_count(path):
    a = Analysis([path])
    env = interview.Env(a, {})
    for q in interview.candidates(a, {}):
        if q.source != "playbook":
            continue
        d = next(d for d in a.playbook["questions"] if d["id"] == q.id)
        named = [p.partition(":")[2] for p in d.get("ask_if", [])
                 if p.split(":")[0] in ("has_role", "mixed_values", "has_negatives", "has_blanks", "coded")]
        if any(env.col(r) is not None for r in named):
            assert re.search(r"\d", q.prompt), (q.id, q.prompt)


# --------------------------------------------------------------------------
# follow-ups: the right table, and a partial answer links nothing
# --------------------------------------------------------------------------
def _two_logs(tmp_path):
    """Two logs in one file, each with codes a list in another file does not
    have; each old code shares its description with a listed one."""
    lists, logs = xlsxwriter.Workbook(str(tmp_path / "lists.xlsx")), xlsxwriter.Workbook(str(tmp_path / "logs.xlsx"))
    for ref, log, code, desc, pre, names in (
            ("Items", "Orders", "Item Code", "Description", "IT", ["Bolt", "Nut", "Washer", "Screw", "Rivet"]),
            ("Parts", "Repairs", "Part No", "Part Description", "PX", ["Valve", "Pump", "Seal", "Hose", "Gauge"])):
        r = lists.add_worksheet(ref)
        r.write_row(0, 0, [code, desc, "List Price"])
        for i, n in enumerate(names):
            r.write_row(i + 1, 0, [f"{pre}-{100 + i}", n, 5.0 + i])
        t = logs.add_worksheet(log)
        t.write_row(0, 0, ["Ref", code, desc, "Qty"])
        for i in range(120):
            k = i % 5
            old = i % 8 == 0
            t.write_row(i + 1, 0, [f"{log[0]}{5000 + i}", f"{pre}-{900 + k % 3}" if old else f"{pre}-{100 + k}",
                                   names[k % 3] if old else names[k], (i % 4) + 1])
    lists.close()
    logs.close()
    return [str(tmp_path / "logs.xlsx"), str(tmp_path / "lists.xlsx")]


def test_a_follow_up_names_the_column_of_the_answer_that_opened_it(tmp_path):
    a = Analysis(_two_logs(tmp_path))
    found = [q for q in findings.finding_questions(a, {}) if q.id.startswith("find_unmatched_")]
    assert len(found) == 2
    for q in found:
        fu = findings.follow_ups(a, {q.id: interview._answer(q, ["old_codes"], "")})
        codes = next(f for f in fu if f.id.startswith("follow_codes_"))
        assert codes.meta["about"]["table"] == q.meta["about"]["table"]
        assert codes.meta["about"]["col"] == q.meta["about"]["col"]
        pre = q.meta["about"]["values"][0][:2]
        assert all(v.startswith(pre) for v in codes.meta["about"]["values"])     # its own codes, not the other log's
        assert "pairs of codes" in codes.prompt


def test_a_partial_recode_answer_draws_no_same_as_line():
    hotel = os.path.join(FX, "procurement_hotel.xlsx")
    a = Analysis([hotel, os.path.join(FX, "procurement_contracts.xlsx")])
    answers = {}
    q = _q(a, "find_unmatched_item", answers)
    answers[q.id] = _ans(q, "ab")
    fu = _q(a, "follow_codes_", answers)
    answers[fu.id] = _ans(fu, "b) The first two are all the same. The rest are different.")
    assert answers[fu.id]["options"] == ["some"]
    recs = Composer(a, hotel, "b1", answers).compose()
    assert not [r for r in recs if r["record"] == "edge" and r["kind"] == "same_as" and r.get("source") == "told"]
