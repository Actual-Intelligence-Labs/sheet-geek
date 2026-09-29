"""Practice round 4, the seams between the two halves: what the questions offer
(findings, interview) and what the answers do (rules, brain), driven through
the CLI on synthetic books (tests/synth.py plants and inline books), never a
development workbook.

- A pack price asked on one tab and carried to another tab with the same items
  (the question's scale_also) is said once, from the tab the question asked
  about: never with the other tab's rows, never 'the same on' the asked tab, and
  never a tail that repeats what the pack statement already says.
- An inputs readback that shows inputs sharing a note as one clause ('A, B and
  C (Sheet!B3, B4 and B5) = 1, 2 and 3 ("note")'): a typed reply that names one
  of them confirms that one input, and the others stay open.
- A readback's 'So:' line the owner ticks is written on the question it
  restates, with that option's curated sentence and the owner's words, and not
  again as a pick on the readback."""
import json
import os
import subprocess
import sys
from collections import Counter

import pytest

pytest.importorskip("xlsxwriter")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts"))
import synth  # noqa: E402
from synth import Formula  # noqa: E402
from sheetbrain import brainzip  # noqa: E402

SB = os.path.abspath(os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts", "sb.py"))
SEEDS = (1, 2, 3)


def sb(env, *args, stdin=None):
    p = subprocess.run([sys.executable, SB, *args], input=stdin, capture_output=True, text=True, env=env,
                       timeout=300)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def _labels(q):
    return [o["label"].replace(" (Recommended)", "") for o in q["options"]]


def _drive(book, env, reply_to):
    """Answer every round with reply_to(round, number, question) -> the reply line
    after its number ('a', ' some words', ' not sure'), save a copy and read its
    brain back: (every question asked, the brain's records)."""
    r = sb(env, "start", str(book))
    asked = []
    for rnd in range(20):
        if r.get("next") != "ask":
            break
        qs = (r.get("ask") or {}).get("questions") or []
        asked += qs
        reply = [f"{k}{reply_to(rnd, k, q) or ' not sure'}" for k, q in enumerate(qs, 1)]
        r = sb(env, "answer", str(book), "--text", "-", stdin="\n".join(reply))
        assert r.get("ok") is not False, r
    out = str(book).replace(".xlsx", "_out.xlsx")
    r = sb(env, "save", str(book), "--copy", out)
    assert r["ok"], r
    return asked, brainzip.read_brain(out)[0]


def _env(tmp_path, tag):
    return dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / f"home_{tag}"))


# --------------------------------------------------------------------------
# a pack price on two tabs: said once, from the tab asked about
# --------------------------------------------------------------------------
def _pack_reply(pack):
    def reply(_rnd, _k, q):
        labels = _labels(q)
        if "For a pack or case" in labels:
            return f"{'abcd'[labels.index('For a pack or case')]}, {pack} in one"
        return ""
    return reply


@pytest.mark.parametrize("name", ["case_price_tabs", "per_case"])
def test_a_pack_price_is_said_once_from_the_tab_the_question_asked_about(tmp_path, name):
    seen = 0
    for seed in SEEDS:
        book = tmp_path / f"{name}{seed}.xlsx"
        m = synth.build(book, seed, name)
        pack = m["plants"][0].get("pack") or 12
        asked, recs = _drive(book, _env(tmp_path, f"{name}{seed}"), _pack_reply(pack))
        q = next((q for q in asked if "For a pack or case" in _labels(q)), None)
        if q is None:
            continue
        seen += 1
        told = [x["statement"] for x in recs if x.get("source") == "told" and "pack or case of" in x["statement"]]
        assert len(told) == 1, told
        note = told[0]
        # the asked tab's own rows, once; the other tab only as 'the same on <tab>' the question named
        asked_tab = note.split(" on ", 2)[1].split(" is for a pack")[0]
        assert f"the same on {asked_tab}" not in note, note
        assert note.count("divided by") == 1, note
        applied = [x["statement"] for x in recs if x.get("ref") == "rule:applied" and "divided by" in x["statement"]]
        tabs = {s.split("on the rows of ", 1)[1].split(" where ", 1)[0] for s in applied}
        if name == "case_price_tabs":
            assert len(tabs) == 2 and "the same on " in note, (tabs, note)
        else:
            assert len(tabs) == 1 and "the same on " not in note, (tabs, note)
    assert seen >= 2, seen


# --------------------------------------------------------------------------
# inputs that share a note: a typed reply naming one confirms only that one
# --------------------------------------------------------------------------
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _team_model(path, shared: bool):
    """A 24-month plan on one tab reading a levers tab laid out Lever | Value |
    Note: an uplift rate, three teams' pay per person (one note for all three, or,
    in the twin, a note each), a tax rate and the opening cash."""
    tab = "Levers"
    note = "pay per person each month"
    teams = [("Design", 9000, 3), ("Support", 7000, 2), ("Field", 8000, 4)]
    ins = [["Lever", "Value", "Note"], ["Uplift", 0.03, "per month"]]
    ins += [[n, cost, note if shared else f"{n.lower()} team, loaded"] for n, cost, _h in teams]
    ins += [["Tax share", 0.21, "flat, on profit"], ["Cash at start", 400000, "bank at the start"]]
    ref = {n: f"{tab}!$B${i + 3}" for i, (n, _c, _h) in enumerate(teams)}
    lines = ["Revenue"] + [f"{n} pay" for n, _c, _h in teams] + ["Profit", "Tax", "Cash"]
    row = {k: i + 2 for i, k in enumerate(lines)}
    grid = [["Line"] + [f"{m} {2027 + k // 12}" for k, m in enumerate(_MONTHS * 2)]]
    v = {k: [0.0] * 24 for k in lines}
    f = {k: [None] * 24 for k in lines}
    for m in range(24):
        c, p = synth._col(m + 1), synth._col(m) if m else None
        v["Revenue"][m] = 90000.0 if m == 0 else round(v["Revenue"][m - 1] * 1.03, 2)
        f["Revenue"][m] = f"={p}{row['Revenue']}*(1+{tab}!$B$2)" if m else None
        for n, cost, heads in teams:
            v[f"{n} pay"][m], f[f"{n} pay"][m] = heads * cost, f"={heads}*{ref[n]}"
        v["Profit"][m] = round(v["Revenue"][m] - sum(v[f"{n} pay"][m] for n, _c, _h in teams), 2)
        f["Profit"][m] = f"={c}{row['Revenue']}" + "".join(f"-{c}{row[f'{n} pay']}" for n, _c, _h in teams)
        v["Tax"][m] = round(max(0, v["Profit"][m]) * 0.21, 2)
        f["Tax"][m] = f"=MAX(0,{c}{row['Profit']})*{tab}!$B$6"
        v["Cash"][m] = round((v["Cash"][m - 1] if m else 400000) + v["Profit"][m] - v["Tax"][m], 2)
        f["Cash"][m] = (f"={p}{row['Cash']}" if m else f"={tab}!$B$7") + f"+{c}{row['Profit']}-{c}{row['Tax']}"
    for k in lines:
        grid.append([k] + [Formula(f[k][m], v[k][m]) if f[k][m] else v[k][m] for m in range(24)])
    synth._write_xlsxwriter(str(path), [{"name": "Plan", "rows": grid}, {"name": tab, "rows": ins}])


@pytest.mark.parametrize("shared", [True, False])
def test_a_typed_reply_naming_one_input_of_a_shared_note_confirms_that_input_only(tmp_path, shared):
    book = tmp_path / f"team{int(shared)}.xlsx"
    _team_model(book, shared)

    def reply(_rnd, _k, q):
        if q["question"].startswith(("These inputs drive", "These other inputs drive")):
            return " Support and Uplift are right. Field goes up in the spring."
        return ""
    asked, recs = _drive(book, _env(tmp_path, f"team{int(shared)}"), reply)
    q = next(q for q in asked if q["question"].startswith("These inputs drive"))
    # the question shows the three teams as one clause when they share a note, else one clause each
    assert ("Design, Support and Field (Levers!B3, B4 and B5)" in q["question"]) is shared, q["question"]
    told = [x["statement"] for x in recs if x.get("source") == "told"]
    right = [s for s in told if "right as read, per the owner" in s]
    note = "pay per person each month" if shared else "support team, loaded"
    assert right == [f'Support (Levers!B4) = 7,000 ("{note}"), right as read, per the owner, who wrote: '
                     '"Support and Uplift are right."',
                     'Uplift (Levers!B2) = 0.03 ("per month"), right as read, per the owner, who wrote: '
                     '"Support and Uplift are right."'], told
    assert 'On "Inputs", the owner also wrote: "Field goes up in the spring."' in told
    assert not any(s.startswith(("Design", "Field (")) and "right as read" in s for s in told), told


# --------------------------------------------------------------------------
# a ticked 'So:' line is written on the question it restates
# --------------------------------------------------------------------------
def _rarest_account(book):
    import openpyxl
    wb = openpyxl.load_workbook(str(book), read_only=True, data_only=True)
    try:
        rows = list(wb.worksheets[0].iter_rows(min_row=2, values_only=True))
    finally:
        wb.close()
    return Counter(r[2] for r in rows if r[2]).most_common()[-1][0]


@pytest.mark.parametrize("tick", [True, False])
def test_a_ticked_so_line_is_written_on_the_question_it_restates(tmp_path, tick):
    seen = 0
    for seed in SEEDS:
        book = tmp_path / f"be{seed}{int(tick)}.xlsx"
        synth.build(book, seed, "balanced_entries")
        acct = _rarest_account(book)
        state = {"rule": False}

        def reply(_rnd, _k, q, state=state):
            labels = _labels(q)
            if "Same meaning, only the sign flipped" in labels:
                return " Both mean a credit"
            if any(lab.startswith("So: ") for lab in labels):
                # tick the 'So:' line (or, in the twin, only the typed rule beside it)
                return "".join("abcd"[i] for i, lab in enumerate(labels)
                               if lab.startswith("So: ") is tick and lab != "Not sure")
            if not state["rule"] and not q.get("multiSelect") and not q["question"].startswith(
                    ("I read this tab", "Around ", "Last one", "I understand this data")):
                state["rule"] = True
                return f" The {acct} lines are not ours, leave them out of every total."
            return ""
        asked, recs = _drive(book, _env(tmp_path, f"be{seed}{int(tick)}"), reply)
        rb = next((q for q in asked if any(lab.startswith("So: ") for lab in _labels(q))), None)
        if rb is None:
            continue
        seen += 1
        told = [x for x in recs if x.get("source") == "told"]
        boundary = [x["statement"] for x in told if x.get("ref", "").startswith("q:find_boundary_")]
        assert len(boundary) == 1, boundary
        if tick:
            # the option's own sentence, then the owner's words, on the boundary question
            assert "both signs of Credit mean the same thing" in boundary[0] \
                and boundary[0].endswith('the owner wrote: "Both mean a credit."'), boundary
        else:
            assert boundary[0].startswith("Asked ") and "both signs" not in boundary[0], boundary
        # never again as a pick on the readback
        assert not any("So: " in x["statement"] for x in told), [x["statement"] for x in told]
    assert seen >= 2, seen
