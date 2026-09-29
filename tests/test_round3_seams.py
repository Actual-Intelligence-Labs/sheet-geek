"""Practice round 3, the seams between the two halves: what the questions offer
(findings, interview) and what the answers do (rules, brain, say), driven
through the CLI on synthetic trap books (tests/synth.py), never a development
workbook.

- A map that pairs each value with its own match (names with codes, old values
  with new ones) is said pair by pair, never as 'counted as one'.
- Two leave-outs picked together on a pick-all-that-apply question both apply;
  a pick of 'count them' beside 'leave them out' settles nothing.
- Every label the structured ask tool shows (a long recommended one cut to fit,
  a first-round label that says what a heavy rule removes) finds its option
  again, and the note quotes the label the owner saw.
- An option that names a column by its role shows the column, never the slot."""
import json
import os
import re
import subprocess
import sys

import pytest

pytest.importorskip("xlsxwriter")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts"))
import synth  # noqa: E402
from sheetbrain import brainzip  # noqa: E402

SB = os.path.abspath(os.path.join(HERE, "..", "skills", "spreadsheet-brain", "scripts", "sb.py"))
SEEDS = (1, 2, 3)


def sb(env, *args, stdin=None):
    p = subprocess.run([sys.executable, SB, *args], input=stdin, capture_output=True, text=True, env=env,
                       timeout=300)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def _start(tmp_path, name, seed):
    book = tmp_path / f"{name}{seed}.xlsx"
    synth.build(book, seed, name)
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / f"home{seed}"))
    return book, env, sb(env, "start", str(book))


def _state(env, bid):
    with open(os.path.join(env["SPREADSHEET_BRAIN_HOME"], "work", bid, "state.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _labels(q):
    return [o["label"].replace(" (Recommended)", "") for o in q["options"]]


def _drive(book, env, r, pick):
    """Answer every round with pick(question) -> letters (or '' for not sure), then save a copy and read
    its brain back: (every 'say' the answers gave, the brain's records)."""
    said = []
    for _ in range(20):
        if r.get("next") != "ask":
            break
        qs = (r.get("ask") or {}).get("questions") or []
        reply = [f"{k}{pick(q) or ' not sure'}" for k, q in enumerate(qs, 1)]
        r = sb(env, "answer", str(book), "--text", "-", stdin="\n".join(reply))
        assert r.get("ok") is not False, r
        said.append(r.get("say") or "")
    out = str(book).replace(".xlsx", "_out.xlsx")
    r = sb(env, "save", str(book), "--copy", out)
    assert r["ok"], r
    return said, brainzip.read_brain(out)[0]


# --------------------------------------------------------------------------
# a map of pairs is said pair by pair
# --------------------------------------------------------------------------
_MAP_PICKS = ("Each name is its code", "Yes, all the same")


def _map_pick(q):
    labels = _labels(q)
    got = next((i for i, lab in enumerate(labels) if lab in _MAP_PICKS), None)
    if got is not None:
        return "abcd"[got]
    # the rest: the first option, or every option of a pick-all-that-apply question (a switch that opens the
    # question about new names)
    return "a" if not q.get("multiSelect") else "".join("abcd"[i] for i, lab in enumerate(labels) if lab != "Not sure")


@pytest.mark.parametrize("name", ["branch_scope", "status_switch"])
def test_a_map_of_pairs_is_never_said_as_one(tmp_path, name):
    seen = 0
    for seed in SEEDS:
        book, env, r = _start(tmp_path, name, seed)
        said, recs = _drive(book, env, r, _map_pick)
        applied = [x["statement"] for x in recs if x.get("ref") == "rule:applied" and " counts as " in x["statement"]]
        if not applied:
            continue
        seen += 1
        # each pair named: 'A counts as X, B as Y'; never all of them 'counted as one'
        assert all(re.search(r"\w counts as \S+(, \S.* as \S+| and \S.* as \S+)", s) for s in applied), applied
        text = " ".join(said + [x["statement"] for x in recs])
        assert not re.search(r"\bcount(ed)? as one\b", text), text
        assert any("Applied from your answer: in " in s and " counts as " in s for s in said), said
    assert seen >= 2, seen


# --------------------------------------------------------------------------
# pick all that apply: two leave-outs go together, a count and a leave-out do not
# --------------------------------------------------------------------------
def _nonstock(q):
    return q.get("multiSelect") and any(lab.startswith("Count them as") for lab in _labels(q))


def test_two_leave_outs_picked_together_both_apply(tmp_path):
    seen = 0
    for seed in SEEDS:
        book, env, r = _start(tmp_path, "gift_cards", seed)

        def pick(q):
            if _nonstock(q):
                return "".join("abcd"[i] for i, lab in enumerate(_labels(q)) if lab.startswith("Leave out of"))
            return ""
        q = next((q for q in r["ask"]["questions"] if _nonstock(q)), None)
        if q is None:
            continue
        leave = [lab for lab in _labels(q) if lab.startswith("Leave out of")]
        seen += len(leave) >= 2
        said, recs = _drive(book, env, r, pick)
        applied = [x["statement"] for x in recs if x.get("ref") == "rule:applied"]
        # one rule on those rows, out of every total the picks named, and only those
        # changed on purpose (practice round 4, fix 2): the note says where the rows are left out, in one sentence
        measures = [lab[len("Leave out of "):-len(" totals")] for lab in leave]
        assert len(applied) == 1 and all(m in applied[0] for m in measures) \
            and "totals; the other counts and totals keep them." in applied[0], applied
        told = [x["statement"] for x in recs if x.get("source") == "told" and "How do the" in x["statement"]]
        assert told and not any(x.get("record") == "open" and "How do the" in x["statement"] for x in recs)
    assert seen >= 2, seen


def test_count_beside_leave_out_settles_nothing(tmp_path):
    book, env, r = _start(tmp_path, "gift_cards", SEEDS[0])
    q = next(q for q in r["ask"]["questions"] if _nonstock(q))
    both = "".join("abcd"[i] for i, lab in enumerate(_labels(q)) if lab.startswith(("Count them", "Leave out of")))
    said, recs = _drive(book, env, r, lambda x: both if _nonstock(x) else "")
    assert not [x for x in recs if x.get("ref") == "rule:applied"]
    assert any(x.get("record") == "open" and "How do the" in x["statement"] for x in recs)


# --------------------------------------------------------------------------
# every label the structured ask shows finds its option again
# --------------------------------------------------------------------------
def _norm(s):
    s = re.sub(r"\(recommended\)", "", str(s), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


@pytest.mark.parametrize("name", ["model_flows", "per_case", "piece_rate", "sentinel_roster", "list_terms"])
def test_every_label_the_ask_tool_shows_finds_its_option(tmp_path, name):
    for seed in SEEDS[:2]:
        book, env, r = _start(tmp_path, name, seed)
        qs = r["ask"]["questions"]
        pending = _state(env, r["brain_id"])["pending"]
        assert len(pending) == len(qs)
        shown = {}
        for q in qs:
            # a pick that needs typed words is still open without them: not one of these
            real = [o["label"] for o in q["options"] if o["label"] != "Not sure" and "(type" not in o["label"]]
            # the recommended label (the one cut to fit when long), else the last real one (a heavy rule's)
            shown[q["question"]] = next((x for x in real if x.endswith("(Recommended)")), real[-1])
        r2 = sb(env, "answer", str(book), "--json", "-", stdin=json.dumps(shown))
        assert r2.get("ok") is not False, r2
        answers = _state(env, r["brain_id"])["answers"]
        for qid, q in zip(pending, qs):
            a = answers[qid]
            label = shown[q["question"]]
            assert a["options"] and not a["text"], (qid, label, a)
            assert _norm(a["labels"][0]).startswith(_norm(label)), (qid, label, a["labels"])
            if "(Recommended)" not in label:
                assert a["labels"][0] == label, (qid, label, a["labels"])      # the note quotes what was shown


# --------------------------------------------------------------------------
# an option that names a column by its role shows the column
# --------------------------------------------------------------------------
def test_an_option_that_names_a_role_shows_the_column(tmp_path):
    slot = re.compile(r"\{(?:role|values|count|rows|sum):[a-z_]+\}")
    seen = 0
    for seed in SEEDS[:2]:
        book, env, r = _start(tmp_path, "two_exports", seed)

        def pick(q):
            labels = _labels(q)
            return "abcd"[labels.index("Connect it to another sheet")] if "Connect it to another sheet" in labels \
                else ""
        for _ in range(6):
            if r.get("next") != "ask":
                break
            qs = r["ask"]["questions"]
            assert not slot.search(json.dumps(r["ask"])) and not slot.search(r.get("ask_text") or ""), r["ask"]
            seen += any(q["header"] == "Matches on" for q in qs)
            reply = [f"{k}{pick(q) or ' not sure'}" for k, q in enumerate(qs, 1)]
            r = sb(env, "answer", str(book), "--text", "-", stdin="\n".join(reply))
    assert seen, seen
