#!/usr/bin/env python3
"""Blind judging for the pre-registered test (evals/PREREG-2026-09-26.md).

Each (business, model, repetition) pair with both answers gets two judges from
two model families: judge 1 Claude Opus 5.5 (clean-room CLI), judge 2 OpenAI
GPT-6-Astra (Codex CLI). They see the brief, the reference and the two answers
as A and B; which one had the brain is set by a seeded coin flip and kept in
order.json, never shown to them. Resumable.

    /usr/bin/python3 run_judges.py prep | run | status
"""
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_answers as ra  # noqa: E402

B = ra.B
S = os.path.dirname(R)
TIMEOUT = 1500
LIMIT = ra.LIMIT
stop = threading.Event()
lock = threading.Lock()

ANSWER = {"type": "object", "additionalProperties": False,
          "properties": {
              "plain_correct": {"type": "array", "items": {"type": "boolean"}},
              "on_target": {"type": "integer"}, "right": {"type": "integer"}, "useful": {"type": "integer"},
              "mistakes": {"type": "array", "items": {"type": "string"}},
              "cites_notes_tab": {"type": "boolean"}},
          "required": ["plain_correct", "on_target", "right", "useful", "mistakes", "cites_notes_tab"]}
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"A": ANSWER, "B": ANSWER, "better": {"type": "string", "enum": ["A", "B", "tie"]},
                         "why": {"type": "string"}},
          "required": ["A", "B", "better", "why"]}

RULES = ("A number is correct within 1% of the key or within the key's rounding. A named answer must match. "
         "An answer that computes a different thing than the key's question (for example a total that includes rows "
         "the owner leaves out) is wrong. A question with several parts is correct only if every part is. A question "
         "asking for a list is correct if it names every item in the key; extra items do not make it wrong. An answer "
         "that gives no answer to a question is wrong for that question.")


def rule_for(ans):
    if isinstance(ans, (int, float)):
        return "one number, correct within 1%"
    if isinstance(ans, list):
        return "a list: correct only if every item listed here is named"
    if isinstance(ans, dict):
        return "several parts: correct only if every part matches (numbers within 1%)"
    return "a name: must match"


PLANTED_NOTE = {"hotel": "These known issues were counted on January 2025 to June 2026, before July and August 2026 "
                         "were added; per-period counts may have grown since. The brief's addendum covers what was "
                         "added."}


def reference(b):
    v = B[b]
    ref = {"request": ra.message(b), "grading_rules": RULES,
           "plain_questions": [{"question": p["ask"], "correct_answer": p["answer"], "how_it_is_computed": p["how"],
                                "grading": rule_for(p["answer"])} for p in v["plain"]]}
    keyp = os.path.join(R, "keys", f"{b}.json")
    key = json.load(open(keyp)) if os.path.exists(keyp) else None
    if b == "hotel":
        key = json.load(open(os.path.join(S, "cold2", "judge", "hotel_key.json")))
    if key and key.get("planted"):
        ref["known_issues_in_the_data"] = key["planted"]
        if b in PLANTED_NOTE:
            ref["known_issues_note"] = PLANTED_NOTE[b]
    return ref


PROMPT = """You are a strict, blind judge in a controlled experiment. Two AI assistants were each handed the same business workbook(s) and the same message from the owner. Grade both answers against what the owner actually knows and wants.

Files in this folder:
- brief.txt: the owner's own knowledge of the business and its files (file names in it may differ from the ones in the request; they are the same workbooks).
- reference.json: the owner's full message, the three plain questions with their correct answers and how each is computed, the grading rules, and for some businesses known issues in the data.
- answer_A.md and answer_B.md: the two answers.
Read these four files and nothing else. Do not open any workbook: judge from the brief and the reference.

For each answer (A and B):
- plain_correct: exactly three booleans, one per plain question in order, graded by the rules in reference.json.
- on_target (integer 0 to 5): does it address what the owner asked, for the purpose they gave?
- right (integer 0 to 5): are its claims and numbers correct given the brief and the reference? Penalize numbers that break the owner's rules (for example counting what the owner leaves out) and confident claims the brief contradicts.
- useful (integer 0 to 5): would the owner act on it? Specific, prioritized, with the numbers they need.
- mistakes: every mistake the owner would catch, one short line each (empty list if none).
- cites_notes_tab: true if the answer mentions a notes, brain or documentation tab in the file.
Then better: "A", "B" or "tie", and why in at most two sentences.
Judge content only. Length, formatting and tone earn nothing."""


def jdir(b, m, r):
    """The judge's materials and results. The judge never runs here: it runs in
    its own root holding only brief, reference, schema and the two answers."""
    return os.path.join(R, "judges", b, m, f"r{r}")


def order_path(b, m, r):
    return os.path.join(R, "judge_orders", b, m, f"r{r}.json")


def jroot(d, which, attempt):
    import hashlib
    h = hashlib.sha256(f"{d}|{which}|{attempt}|sbj|{ra.ISOLATION}".encode()).hexdigest()[:12]
    return f"/private/tmp/sbj-{h}"


FILES = ("brief.txt", "reference.json", "schema.json", "answer_A.md", "answer_B.md")


def stage(d, which, attempt):
    root = jroot(d, which, attempt)
    shutil.rmtree(root, ignore_errors=True)
    work = os.path.join(root, "work")
    os.makedirs(work)
    os.makedirs(os.path.join(root, "tmp"))
    for f in FILES:
        shutil.copy2(os.path.join(d, f), os.path.join(work, f))
    return root, work


def a_is_brain(b, m, r):
    return random.Random(f"{b}|{m}|{r}|judge-order-2026-09-26").random() < 0.5


def prep():
    made = missing = 0
    for b in B:
        for m in ra.MODELS:
            for r in ra.REPS:
                recs = {}
                for c in ra.CONDS:
                    p = ra.rec_path(b, m, r, c)
                    recs[c] = json.load(open(p)) if os.path.exists(p) else None
                if not all(x and x.get("status") == "ok" for x in recs.values()):
                    missing += 1
                    continue
                d = jdir(b, m, r)
                os.makedirs(d, exist_ok=True)
                shutil.copy2(os.path.join(R, "briefs", f"{b}.txt"), os.path.join(d, "brief.txt"))
                json.dump(reference(b), open(os.path.join(d, "reference.json"), "w"), indent=1)
                json.dump(SCHEMA, open(os.path.join(d, "schema.json"), "w"))
                ab = a_is_brain(b, m, r)
                order = {"A": "brain" if ab else "none", "B": "none" if ab else "brain"}
                for lab, c in order.items():
                    open(os.path.join(d, f"answer_{lab}.md"), "w").write(recs[c]["answer"])
                os.makedirs(os.path.dirname(order_path(b, m, r)), exist_ok=True)
                json.dump(order, open(order_path(b, m, r), "w"))
                made += 1
    print(f"judge folders ready: {made}; pairs without both answers: {missing}")


def valid(o):
    if not isinstance(o, dict) or o.get("better") not in ("A", "B", "tie"):
        return False
    for lab in ("A", "B"):
        a = o.get(lab) or {}
        if len(a.get("plain_correct") or []) != 3:
            return False
        if not all(isinstance(a.get(k), int) and 0 <= a[k] <= 5 for k in ("on_target", "right", "useful")):
            return False
    return True


# the trials' settings, minus the rule that hides judge roots (a judge must read its own)
_js = json.loads(ra.TRIAL_SETTINGS)
_js["permissions"]["deny"] = [d for d in _js["permissions"]["deny"] if "sbj-" not in d] + [
    "Read(//private/tmp/sbt-*/**)", "Read(//tmp/sbt-*/**)"]
JUDGE_SETTINGS = json.dumps(_js)


def judge1(d, attempt):
    out = os.path.join(d, "judge1.json")
    root, work = stage(d, 1, attempt)
    cmd = [ra.CLAUDE, "-p", PROMPT, "--model", "claude-opus-5-5", "--setting-sources", "local",
           "--strict-mcp-config", "--no-session-persistence", "--settings", JUDGE_SETTINGS,
           "--json-schema", json.dumps(SCHEMA), "--output-format", "json"]
    with open(os.path.join(d, "judge1.log"), "w") as log:
        p = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=log, start_new_session=True,
                             env=ra.clean_env(root), stdin=subprocess.DEVNULL)
        try:
            so, _ = p.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
            return "timeout"
    txt = so.decode("utf-8", "replace")
    shutil.rmtree(root, ignore_errors=True)
    try:
        res = json.loads(txt)
    except json.JSONDecodeError:
        return "limit" if LIMIT.search(txt) else "error"
    so_ = res.get("structured_output")
    if isinstance(so_, str):
        try:
            so_ = json.loads(so_)
        except json.JSONDecodeError:
            so_ = None
    if isinstance(so_, dict) and re.search(r"couldn.t (grade|read)|could not (grade|read)|blocked every file|placeholder",
                                           str(so_.get("why", "")), re.I):
        return "error"
    if res.get("is_error") or not valid(so_):
        return "limit" if LIMIT.search(str(res.get("result"))) else "error"
    json.dump({"verdict": so_, "cost_usd": res.get("total_cost_usd"), "model": "claude-opus-5-5"},
              open(out, "w"), indent=1)
    return "ok"


def judge2(d, attempt):
    out = os.path.join(d, "judge2.json")
    raw = os.path.join(d, "judge2.raw.json")
    if os.path.exists(raw):
        os.remove(raw)
    root, work = stage(d, 2, attempt)
    cmd = [ra.CODEX, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6-astra", *ra.CODEX_OFF,
           "-c", 'model_reasoning_effort="high"', "-C", work, "--output-schema", os.path.join(work, "schema.json"),
           "-o", raw, PROMPT]
    with open(os.path.join(d, "judge2.log"), "w") as log:
        p = subprocess.Popen(cmd, cwd=work, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                             env=ra.clean_env(root), stdin=subprocess.DEVNULL)
        try:
            p.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return "timeout"
    shutil.rmtree(root, ignore_errors=True)
    logtext = open(os.path.join(d, "judge2.log"), encoding="utf-8", errors="replace").read()
    # a judge's commands only: the answers it reads may quote their own trial's folder
    cmds = "\n".join(re.findall(r"^exec\n(.*?)(?= in /\S+\n)", logtext, re.M | re.S))
    reach = ra.outside(cmds, root)
    if reach:
        json.dump({"outside_paths": reach}, open(os.path.join(d, f"judge2.leak{attempt}.json"), "w"))
        return "leak"
    txt = open(raw).read() if os.path.exists(raw) else ""
    try:
        v = json.loads(txt)
    except json.JSONDecodeError:
        return "limit" if LIMIT.search(open(os.path.join(d, "judge2.log")).read()) else "error"
    if not valid(v):
        return "error"
    json.dump({"verdict": v, "model": "gpt-6-astra"}, open(out, "w"), indent=1)
    return "ok"


def one(d, which):
    out = os.path.join(d, f"judge{which}.json")
    if os.path.exists(out) or stop.is_set():
        return
    st = "error"
    for attempt in (1, 2):
        st = (judge1 if which == 1 else judge2)(d, attempt)
        if st in ("ok", "limit"):
            break
    with lock:
        print(f"{time.strftime('%H:%M:%S')} judge{which} {os.path.relpath(d, R)} {st}", flush=True)
    if st == "limit":
        stop.set()


def dirs():
    out = []
    for b in B:
        for m in ra.MODELS:
            for r in ra.REPS:
                d = jdir(b, m, r)
                if os.path.exists(os.path.join(d, "answer_A.md")) and os.path.exists(order_path(b, m, r)):
                    out.append(d)
    return out


def run():
    ds = dirs()
    with ThreadPoolExecutor(4) as cp, ThreadPoolExecutor(3) as xp:
        futs = [cp.submit(one, d, 1) for d in ds] + [xp.submit(one, d, 2) for d in ds]
        for f in futs:
            f.result()
    status()
    if stop.is_set():
        print("stopped on a usage limit; launch again after it resets")
        sys.exit(3)


def status():
    ds = dirs()
    j1 = sum(os.path.exists(os.path.join(d, "judge1.json")) for d in ds)
    j2 = sum(os.path.exists(os.path.join(d, "judge2.json")) for d in ds)
    print(f"pairs {len(ds)}: judge1 done {j1}, judge2 done {j2}", flush=True)


if __name__ == "__main__":
    w = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"prep": prep, "run": run}.get(w, status)()
