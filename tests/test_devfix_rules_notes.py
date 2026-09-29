"""v0.2 practice check, round 1 fixes: typed-rule extraction (A), notes that
keep their meaning and reach the file (B), and one save protocol (E). Every
book here is written inline with synthetic names; no development workbook is
read."""
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
from sheetbrain import findings, interview, privacy, rules, say  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer, _answer_notes  # noqa: E402

SB = os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py")


# --------------------------------------------------------------------------
# inline synthetic books
# --------------------------------------------------------------------------
def _spoilage(tmp_path):
    """A loss log with an integer reason code 1 to 5 (30 rows each)."""
    p = str(tmp_path / "spoilage.xlsx")
    wb = xlsxwriter.Workbook(p)
    ws = wb.add_worksheet("Spoilage")
    ws.write_row(0, 0, ["Site", "Item", "Why Code", "Units", "Adj. Cost"])
    for i in range(150):
        ws.write_row(i + 1, 0, [["North", "South", "East"][i % 3], f"Item {i % 11}", 1 + (i % 5), 1 + i % 4,
                                5.0 + (i % 13)])
    wb.close()
    return Analysis([p])


def _buys(tmp_path, name="buys.xlsx", odd="WHX"):
    """Purchase lines: 5 sites, one (odd, written in capitals) whose documents
    alone start TR-; a Group column with a title-case Fees value."""
    p = str(tmp_path / name)
    rng = random.Random(4)
    wb = xlsxwriter.Workbook(p)
    ws = wb.add_worksheet("Lines")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_row(0, 0, ["Date", "Doc", "Site", "Group", "Amount"])
    for i in range(300):
        site = ["Harbor", "Ridge", "Mesa", "Cove", odd][i % 5]
        doc = f"TR-{5000 + i}" if site == odd else f"{rng.choice(['KD', 'PM', 'LX'])}-{5000 + i}"
        ws.write_datetime(i + 1, 0, dt.datetime(2026, 1, 1) + dt.timedelta(days=i // 3), fmt)
        grp = "Fees" if i % 7 == 0 else ["Dairy", "Paper", "Meat"][i % 3]
        ws.write_row(i + 1, 1, [doc, site, grp, round(rng.uniform(20, 400), 2)])
    wb.close()
    return p


def _charges(tmp_path):
    """A tenant ledger whose charge code DUES (in capitals) is most of the money."""
    p = str(tmp_path / "charges.xlsx")
    wb = xlsxwriter.Workbook(p)
    ws = wb.add_worksheet("Charges")
    ws.write_row(0, 0, ["Unit", "Code", "Charge"])
    for i in range(120):
        code = ["DUES", "DUES", "KEYS", "STORAGE"][i % 4]
        ws.write_row(i + 1, 0, [f"U{100 + i % 12}", code, 900.0 if code == "DUES" else 40.0])
    wb.close()
    return Analysis([p])


def _typed(text, a, col, qid="house_rules", kind="definition", codes=None, header="House rules"):
    t = a.main_table
    out = {"options": ["type"] if codes else [], "labels": [], "text": text, "not_sure": False, "kind": kind,
           "about": {"table": t.tid, "col": col, "aspect": "meaning"}, "prompt": f"What about {col}?",
           "header": header}
    if codes:
        out["codes"] = codes
    return {qid: out}


def _preds(cands):
    return sorted((tuple((c["col"], tuple(c["values"])) for c in x["rule"].predicate), tuple(x["rule"].scope))
                  for x in cands)


REASONS = ("1 = spoiled or out of date, 2 = prep or cooking mistake, 3 = dropped or broken, 4 = sent back to the "
           "supplier, 5 = staff meal. Code 4 is not a loss because the supplier credits us for it, so leave code 4 "
           "out of loss dollars. Staff meal (5) I do count as a loss.")


# --------------------------------------------------------------------------
# A. typed-rule extraction
# --------------------------------------------------------------------------
def test_a_word_inside_a_codes_meaning_is_never_a_rule(tmp_path):
    a = _spoilage(tmp_path)
    said = _typed(REASONS, a, "Why Code", qid="find_codes_why", codes=["1", "2", "3", "4", "5"])
    got = _preds(rules.candidates(a, said))
    # code 4 is the only one said to leave out; 3 is 'dropped' (what it is) and 5 is counted. Changed on
    # purpose (round 3, fix 6): 'out of loss dollars' covers the money column only, never the count of rows
    assert got == [((("Why Code", ("4",)),), ("Adj. Cost",))]
    for text in ("3 = dropped or broken.", "The dropped cases are code 3.", "The scanner drops 3 sometimes."):
        assert rules.candidates(a, _typed(text, a, "Why Code", codes=["3"])) == [], text
    for text, scope in (("3 = broken glass, drop them.", ()), ("Code 3 should be removed from loss dollars.",
                                                                ("Adj. Cost",))):
        assert _preds(rules.candidates(a, _typed(text, a, "Why Code", codes=["3"]))) == \
            [((("Why Code", ("3",)),), scope)], text


def test_a_codes_words_end_at_its_sentence(tmp_path):
    text = "4 = sent back. 5 = staff meal. Leave code 4 out."
    segs = rules.code_segments(text, ["4", "5"])
    assert [text[a:b] for _c, a, b in segs] == ["4 = sent back.", "5 = staff meal."]
    a = _spoilage(tmp_path)
    said = _typed("5 = staff meal, leave code 4 out.", a, "Why Code", codes=["4", "5"])
    assert _preds(rules.candidates(a, said)) == [((("Why Code", ("4",)),), ())]


def test_the_owners_note_marks_only_the_code_they_said_to_leave_out(tmp_path):
    a = _spoilage(tmp_path)
    said = _typed(REASONS, a, "Why Code", qid="find_codes_why", codes=["1", "2", "3", "4", "5"])
    a.apply_answers(said)
    marked = {tuple(c["rule"].predicate[0]["values"]) for c in a.unapplied_rules}
    assert marked == {("4",)}
    stmts = [r["statement"] for r in Composer(a, a.paths[0], "b1", said).compose()]
    assert not any(re.search(r"note on Why Code [35]\b", s) for s in stmts)
    assert any("note on Why Code 4" in s for s in stmts)


def test_a_value_in_capitals_binds_only_in_capitals_or_quotes(tmp_path):
    a = _charges(tmp_path)
    for text in ("Leave the dues from unit U103 out of every total.", "leave dues out of the income numbers"):
        got = rules.candidates(a, _typed(text, a, "Code"))
        assert not [c for c in got if c["rule"].cols() == ["Code"]], text       # the word 'dues' is not DUES
    assert _preds(rules.candidates(a, _typed("Leave the dues from unit U103 out of every total.", a, "Code"))) == \
        [((("Unit", ("U103",)),), ())]
    assert _preds(rules.candidates(a, _typed("Leave DUES out of every total.", a, "Code"))) == \
        [((("Code", ("DUES",)),), ())]
    assert _preds(rules.candidates(a, _typed("Leave 'dues' out of every total.", a, "Code"))) == \
        [((("Code", ("DUES",)),), ())]
    b = Analysis([_buys(tmp_path)])
    assert _preds(rules.candidates(b, _typed("leave fees out of every total", b, "Group"))) == \
        [((("Group", ("Fees",)),), ())]                      # a title-case value still binds its word


def test_the_words_after_out_of_name_the_totals_never_the_rows(tmp_path):
    a = _charges(tmp_path)
    said = _typed("Both books post charges to his unit; leave his unit out of anything about DUES income.", a,
                  "Code")
    assert rules.candidates(a, said) == []                   # 'his unit' binds to no value: nothing proposed
    b = Analysis([_buys(tmp_path)])
    said = _typed("Always exclude WHX from store spend (all WHX rows, not just Fees).", b, "Site")
    assert _preds(rules.candidates(b, said)) == [((("Site", ("WHX",)),), ())]


def test_values_listed_with_and_are_separate_rules(tmp_path):
    b = Analysis([_buys(tmp_path)])
    got = _preds(rules.candidates(b, _typed("Leave WHX and Fees out of every total.", b, "Site")))
    assert got == [((("Group", ("Fees",)),), ()), ((("Site", ("WHX",)),), ())]
    both = rules.candidates(b, _typed("Leave the WHX rows with Fees out.", b, "Site"))
    assert [sorted(p["col"] for p in c["rule"].predicate) for c in both if len(c["rule"].predicate) == 2] == \
        [["Group", "Site"]]


def test_a_rule_about_one_calculation_is_kept_for_it_alone(tmp_path):
    b = Analysis([_buys(tmp_path)])
    said = _typed("The basis is net product spend at the sites: credits netted, fees and WHX excluded.", b, "Amount",
                  qid="price_basis")
    got = _preds(rules.candidates(b, said))
    assert got == [((("Group", ("Fees",)),), ("the basis",)), ((("Site", ("WHX",)),), ("the basis",))]
    assert findings.readback(b, said) is None                # never offered for every count and total
    assert rules.unapplied(b, said) == []                    # and it marks no counted number
    rb = rules.scoped_readback(b, said)
    assert "only for the basis" in rb.prompt and "every count and total" not in rb.prompt
    # changed on purpose (practice round 4c): an option says only what the owner ticks; what the other
    # totals do is said once, in the prompt, so a ticked option never claims it as the owner's
    assert all("left out of the basis" in o["desc"] and "keeps them" not in o["desc"] for o in rb.options)
    assert "only for the basis" in rb.prompt
    said[rb.id] = interview.parse_answers([rb], "ab")[rb.id]
    before = [i["statement"] for i in b.insights]
    b.apply_answers(said)
    assert b.exclusions == {}
    assert [i["statement"] for i in b.insights] == before
    recs = Composer(b, b.paths[0], "b1", said).compose()
    kept = [r["statement"] for r in recs if r.get("ref") == "rule:scoped"]
    assert len(kept) == 2 and all(s.startswith("Kept for the basis only") for s in kept)
    assert not any(r.get("ref") == "rule:applied" for r in recs)
    lead = _preds(rules.candidates(b, _typed("For price comparisons, leave out Fees.", b, "Group")))
    assert lead == [((("Group", ("Fees",)),), ("price comparisons",))]


def test_a_readback_reply_that_types_a_scoped_rule_reads_it_back_as_scoped(tmp_path):
    b = Analysis([_buys(tmp_path)])
    said = _typed("Leave WHX and Fees out of every total.", b, "Site")
    rb = findings.readback(b, said)
    said[rb.id] = dict(interview.parse_answers([rb], "not sure")[rb.id],
                       text="Neither. Fees count in spend; leave them out of item price comparisons and rebate math "
                            "only. Always exclude WHX from store spend.")
    assert findings.readback(b, said) is None                # WHX was shown already; Fees is now scoped
    again = rules.scoped_readback(b, said)
    assert again is not None and "only for item price comparisons and rebate math" in again.prompt
    assert [d["predicate"][0]["values"] for d in again.meta["rules"].values()] == [["Fees"]]


def test_rules_typed_on_the_last_readback_are_read_back_before_the_save(tmp_path):
    book = str(tmp_path / "stands.xlsx")
    wb = xlsxwriter.Workbook(book)
    ws = wb.add_worksheet("Sales")
    fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_row(0, 0, ["Date", "Location", "Channel", "Amount"])
    for i in range(100):
        ws.write_datetime(i + 1, 0, dt.datetime(2026, 1, 1) + dt.timedelta(days=i), fmt)
        ws.write_row(i + 1, 1, [["A1", "A2", "B", "Q7", "Q8"][i % 5], ["Web", "Shop"][i % 2], 10.0 + i % 9])
    wb.close()
    home = str(tmp_path / "home")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    r = _sb(env, "start", book)
    from sheetbrain.store import Store
    st = Store(home)
    state = st.state(r["brain_id"])
    state["round"] = interview.MAX_ROUNDS - 1
    st.save_state(r["brain_id"], state)
    st.close()
    n = len(r["ask"]["questions"])
    r = _sb(env, "answer", book, "--text", "1 leave Q7 out of every total "
            + " ".join(f"{k} not sure" for k in range(2, n + 1)))
    r = _sb(env, "answer", book, "--text", "b")                  # the closing question: nothing to add
    asks = r["ask"]["questions"]
    assert [q["header"] for q in asks] == ["Your rules", "Build"]
    r = _sb(env, "answer", book, "--text", "1 Neither. Leave Q8 out of every total. 2b")
    assert r["next"] == "ask" and "One more, on the rules you just typed" in r["say"]
    labels = [o["label"] for o in r["ask"]["questions"][0]["options"]]
    assert labels[0].startswith("Location = Q8 (20 rows)") and not any("Q7" in lab for lab in labels)
    r = _sb(env, "answer", book, "--text", "a")
    assert r["next"] in ("preview", "ask")
    st = Store(home)
    answers = st.state(r["brain_id"])["answers"]
    st.close()
    assert sum(1 for k in answers if k.startswith(findings.READBACK)) == 2
    a = Analysis([book])
    assert rules.exclusions(a, answers) == {(a.main_table.tid, 1): {"q8"}}      # only the ticked rule applies


def test_a_pick_that_leaves_rows_out_is_said_back_with_its_counts(tmp_path):
    book = _buys(tmp_path)
    home = str(tmp_path / "home")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=home)
    r = _sb(env, "start", book)
    from sheetbrain.store import Store
    st = Store(home)
    state = st.state(r["brain_id"])
    state["pending"] = ["find_exclusive_site_whx"]
    st.save_state(r["brain_id"], state)
    st.close()
    r = _sb(env, "answer", book, "--text", "a")
    assert "Applied from your answer: rows of Lines where Site is WHX (60 rows" in r["say"]
    assert "left out of every count and total" in r["say"]


def test_a_rule_that_removes_most_of_the_money_says_so_and_is_never_recommended(tmp_path):
    a = _charges(tmp_path)
    t = a.main_table
    q = interview.Q("x", "Codes", "60 rows of Charges have Code DUES. What is it?",
                    [{"id": "out", "label": "Leave it out"}, {"id": "keep", "label": "Count it"}],
                    recommend="out", recommend_basis="Most rows",
                    meta={"exclude": {"table": t.tid, "col": "Code", "values": ["DUES"], "options": ["out"]}})
    rules.dress(a, q)
    assert q.recommend is None and q.options[0]["label"].startswith("Leave it out, removes ")
    assert re.search(r"removes 9\d% of Charge", q.options[0]["label"])
    small = interview.Q("y", "Codes", "30 rows have Code KEYS.", [{"id": "out", "label": "Leave it out"}],
                        recommend="out", meta={"exclude": {"table": t.tid, "col": "Code", "values": ["KEYS"],
                                                           "options": ["out"]}})
    rules.dress(a, small)
    assert small.recommend == "out" and small.options[0]["label"] == "Leave it out"


def test_two_rules_that_read_the_same_name_their_tabs(tmp_path):
    p = str(tmp_path / "counts.xlsx")
    wb = xlsxwriter.Workbook(p)
    for tab in ("Weekly", "Monthly"):
        ws = wb.add_worksheet(tab)
        ws.write_row(0, 0, ["Site", "Qty"])
        for i in range(40):
            ws.write_row(i + 1, 0, [["NR1", "NR2", "SX", "EA", "WS"][i % 5], 1 + i % 7])
    wb.close()
    a = Analysis([p])
    rb = findings.readback(a, {"house_rules": {"options": [], "labels": [], "text": "combine NR1 and NR2",
                                               "not_sure": False, "kind": "definition"}})
    rules.dress(a, rb)
    labels = [o["label"] for o in rb.options if o["id"] in rb.meta["rules"]]
    assert len(labels) == 2 and len(set(labels)) == 2
    assert any(lab.endswith("on Weekly") for lab in labels) and any(lab.endswith("on Monthly") for lab in labels)
    assert all(len(lab) <= 60 for lab in labels)


# --------------------------------------------------------------------------
# B. notes that keep their meaning and reach the file
# --------------------------------------------------------------------------
def test_the_closer_is_cut_the_way_every_note_is(tmp_path):
    a = _spoilage(tmp_path)
    text = ("Hot line items are priced per case of 12. The Units and Adj. Cost columns do not know that, so they "
            "are 12 times too high there.")
    recs = Composer(a, a.paths[0], "b1", {interview.CLOSER: {"options": ["type"], "labels": [], "text": text,
                                                              "not_sure": False, "kind": "history"}}).compose()
    notes = [r["statement"] for r in recs if r["id"].startswith(f"f:{interview.CLOSER}")]
    assert not any(n.rstrip(".").endswith("Adj") for n in notes)
    assert any("Units and Adj. Cost columns do not know that" in n for n in notes)


def test_a_sentence_that_points_back_stays_with_its_subject():
    q = interview.Q("x", "Pay basis", "How is Hours counted?", [{"id": "a", "label": "Clock hours"}],
                    fact={"kind": "definition"})
    # changed on purpose (practice round 4, fix 11): up to three typed sentences stay with the question, so
    # four are needed to see where a sentence that points back is kept
    ans = interview._answer(q, [], "FIELD-9 is paid per visit. On their rows, Hours is visits. Payroll runs weekly. "
                                   "Overtime is separate. Breaks are unpaid.")
    notes = [s for s, _ in _answer_notes(ans, q.fact, None)]
    assert len(notes) == 4
    assert '"FIELD-9 is paid per visit. On their rows, Hours is visits."' in notes[0]
    doubt = interview._answer(q, [], "FIELD-9 is paid per visit. Not sure that holds for them.")
    assert len(_answer_notes(doubt, q.fact, None)) == 2        # a doubt stays a note of its own


def test_later_notes_of_one_answer_do_not_repeat_the_question():
    q = interview.Q("x", "Pay basis", "How is Hours counted, and what is Rate on each row?",
                    [{"id": "a", "label": "Clock hours"}], fact={"kind": "definition"})
    # changed on purpose (practice round 4, fix 11): up to three typed sentences stay in the note that carries
    # the question; with more, each later one is its own note naming only the header
    ans = interview._answer(q, [], "Hours are clock hours. Rate is per hour. Overtime is its own column. "
                                   "Breaks are unpaid.")
    notes = [s for s, _ in _answer_notes(ans, q.fact, None)]
    assert notes[0].startswith('Asked "How is Hours counted') and len(notes) == 4
    assert notes[1:] == ['On "Pay basis", the owner also wrote: "Rate is per hour."',
                         'On "Pay basis", the owner also wrote: "Overtime is its own column."',
                         'On "Pay basis", the owner also wrote: "Breaks are unpaid."']
    three = interview._answer(q, [], "Hours are clock hours. Rate is per hour. Overtime is its own column.")
    notes = [s for s, _ in _answer_notes(three, q.fact, None)]
    assert notes == ['Asked "How is Hours counted, and what is Rate on each row?", the owner wrote: "Hours are clock '
                     'hours. Rate is per hour. Overtime is its own column."']


def test_money_handling_is_data_and_a_remark_about_a_person_is_private():
    for s in ("Deposits go into a separate trust account, so they are not dues income.",
              "Transfers between our two accounts are not income."):
        assert privacy.classify(s) == [(s, "data", "")], s
    got = privacy.classify("Priyanka is leaving in May.", names={"Priyanka"})
    assert got[0][1] == "private"
    assert privacy.classify("Priyanka is leaving in May. Her rows stop then.", names={"Priyanka"})[1][1] == "private"


def test_how_to_treat_rows_is_data_even_with_deal_words():
    for s in ("Fees (FEE-A, FEE-B) count in spend but never in price comparisons or rebate math.",
              "On their rows, Hours is the number of visits and Rate is what we pay per visit.",
              "Rate % is in percent points (2.0 means 2%)."):
        assert privacy.classify(s)[0][1] == "data", s
    assert privacy.classify("Vendor A is cost plus $2.10 a case with a 2% rebate.")[0][1] == "commercial"
    parts = privacy.classify("Vendor A is cost plus $2.10 a case; fees never count in rebate math.")
    assert parts == [("Vendor A is cost plus $2.10 a case;", "commercial", "business terms"),
                     ("fees never count in rebate math.", "data", "")]


def test_a_deal_keeps_its_own_tail():
    s = "Vendor B billed the item at $12.95 a pound against the $11.85 contract price; no claim filed yet."
    assert privacy.classify(s) == [(s, "commercial", "business terms")]
    kept, private, commercial = privacy.screen(s)
    assert kept == s and private == [] and commercial


def test_a_guess_goes_only_to_a_column_it_fits(tmp_path):
    def book(head, vals):
        p = str(tmp_path / f"{head.replace(' ', '_')}.xlsx")
        wb = xlsxwriter.Workbook(p)
        ws = wb.add_worksheet("Ledger")
        ws.write_row(0, 0, [head, "Amount"])
        for i in range(40):
            ws.write_row(i + 1, 0, [vals[i % len(vals)], 10.0 + i])
        wb.close()
        return p
    g = {"if": "has_role:kind", "say": "{role:kind} includes {values:kind}; transfers and journal entries move "
                                      "money between accounts without being income or spending."}
    pb = {"generic": {"id": "generic", "roles": {
        "kind": {"label": "Kind", "headers": ["type", "entry type"], "type": "text", "kind": "dimension",
                 "entity": "kind"},
        "amount": {"label": "Amount", "headers": ["amount"], "type": "number", "kind": "metric", "unit": "currency",
                   "additive": True}},
        "insights": [], "questions": [], "gotchas": [g],
        "graph": {"size_by": "amount", "entities": [], "relations": [], "mode": "entities"}}}

    def guesses(path, answers=None):
        a = Analysis([path], playbooks=pb)
        return [r["statement"] for r in Composer(a, path, "b1", answers or {}).compose()
                if r.get("source") == "inferred" and "journal" in r["statement"]]
    wrong = book("Room Type", ["Studio", "Loft", "Suite"])
    assert Analysis([wrong], playbooks=pb).detection["roles"].get("kind", {}).get("header") == "Room Type"
    assert guesses(wrong) == []                                # a room type is not a kind of entry
    right = book("Type", ["Transfer", "Journal", "Deposit"])
    assert guesses(right)
    a = Analysis([right], playbooks=pb)
    said = {"codes_type": {"options": [], "labels": [], "text": "Transfer is money between our accounts.",
                           "not_sure": False, "kind": "definition",
                           "about": {"table": a.main_table.tid, "col": "Type", "aspect": "meaning"}}}
    assert guesses(right, said) == []                          # the owner said what it is


def test_the_read_pack_is_never_cut(tmp_path):
    recs = [{"record": "meta", "id": "brain:x", "statement": "Notes.", "source": "computed", "as_of": "2026-09-28"}]
    recs += [{"record": "fact", "id": f"f:q{i}", "source": "told", "said_by": "owner", "as_of": "2026-09-28",
              "statement": f"Owner note {i}: " + "rows marked late stay in the weekly count. " * 6}
             for i in range(60)]
    recs += [{"record": "fact", "id": f"f:c{i}", "source": "computed", "as_of": "2026-09-28",
              "statement": f"Counted fact {i}."} for i in range(20)]
    pack = say.context_pack("book.xlsx", recs, {}, origin="own")
    assert len(pack) > 7000 and pack.endswith("</brain-notes>")
    assert all(f"Owner note {i}:" in pack for i in range(60))
    assert "8 more notes in this section, left out here; `sb.py read book.xlsx --all` shows every note." in pack
    full = say.context_pack("book.xlsx", recs, {}, origin="own", full=True)
    assert all(f"Counted fact {i}." in full for i in range(20)) and "more notes" not in full


# --------------------------------------------------------------------------
# E. one save protocol: the preview's question is answered with sb.py answer
# --------------------------------------------------------------------------
def _sb(env, *args):
    p = subprocess.run([sys.executable, SB, *args], capture_output=True, text=True, env=env, timeout=180)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def test_the_save_question_is_answered_like_every_other(tmp_path):
    book = _buys(tmp_path)
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / "home"))
    assert _sb(env, "start", book, "--no-questions")["ok"]
    r = _sb(env, "preview", book)
    assert r["next"] == "ask" and r["ask"]["questions"][0]["header"] == "Save brain"
    assert "sb.py answer" in r["then"]
    # changed on purpose (round 3, fix 26): the structured options are three places and Not sure; every
    # line first is typed
    r = _sb(env, "answer", book, "--text", "show every line")    # every line first, then the same question
    assert r["next"] == "ask" and " | " in r["say"] and r["ask"]["questions"][0]["header"] == "Save brain"
    copy = str(tmp_path / "out" / "copy.xlsx")
    r = _sb(env, "answer", book, "--text", "a", "--copy", copy)
    assert r["ok"] and "now has a brain" in r["say"] and os.path.exists(copy)
    assert r["written"][0]["verified"] and r["written"][0]["path"] == copy
    r = _sb(env, "answer", book, "--text", "1a")                 # settled: no save question is pending now
    assert "couldn't tell where the brain should go" not in r.get("say", "")


def test_a_direct_save_settles_the_save_question(tmp_path):
    book = _buys(tmp_path, name="local.xlsx")
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / "home"))
    _sb(env, "start", book, "--no-questions")
    _sb(env, "preview", book)
    r = _sb(env, "save", book, "--local-only")
    assert r["ok"] and r["say"].startswith("Kept on this machine only")
    r = _sb(env, "preview", book)
    r = _sb(env, "answer", book, "--json", json.dumps({"Where should the brain go?": "This machine only"}))
    assert r["ok"] and r["say"].startswith("Kept on this machine only")
