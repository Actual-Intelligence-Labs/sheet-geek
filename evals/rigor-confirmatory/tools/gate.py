#!/usr/bin/env python3
"""The gate's checker part (as in the first test): two checkers (Claude Opus 5.5,
headless, clean room) read every owner note (source told) against the owner's
brief. Checker A looks for notes that contradict the brief; checker B for owner
claims the brief never makes. A business passes when neither flags any note.
The operator reads only pass or fail; the flagged notes are written to a file.

    /usr/bin/python3 gate.py <label> <spec.json> <briefs_dir>
    spec.json: {"<business>": {"brain_files": [...], "home": "..."}}
"""
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_answers as ra  # noqa: E402
from score_capture import told_notes  # noqa: E402

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERDICT = {"type": "object", "additionalProperties": False,
           "properties": {"notes": {"type": "array", "items": {
               "type": "object", "additionalProperties": False,
               "properties": {"id": {"type": "string"}, "flag": {"type": "boolean"}, "why": {"type": "string"}},
               "required": ["id", "flag", "why"]}}},
           "required": ["notes"]}

COMMON = """A tool asked a business owner questions about a spreadsheet and wrote each answer into the file as an owner note. Someone played the owner, answering ONLY from the owner's brief below; where the brief was silent they were to answer "not sure". Each note records the question and what the owner picked or typed. Read every note below and decide for each one (by its id) whether to flag it. Judge only from the text given. Score every note id exactly once."""

LENS = {
    "contradicts": "Flag a note (flag true) when what it says the owner picked or wrote contradicts the brief: a different number, rule, meaning, treatment or name than the brief gives. Do not flag a note because it is incomplete, vague, or says not sure.",
    "unsupported": "Flag a note (flag true) when it credits the owner with a claim the brief never makes: a fact, number, rule, reason, name or preference that is not in the brief and does not follow plainly from it. A pick of an option counts as the owner's claim. Do not flag not sure, a restatement of the question, or a claim the brief makes in other words.",
}


def run_claude(prompt, work):
    cmd = [ra.CLAUDE, "-p", prompt, "--model", "claude-opus-5-5", "--setting-sources", "local", "--strict-mcp-config",
           "--no-session-persistence", "--settings", ra.TRIAL_SETTINGS, "--json-schema", json.dumps(VERDICT),
           "--output-format", "json"]
    p = subprocess.run(cmd, cwd=work, env=ra.clean_env(os.path.dirname(work)), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=1800)
    res = json.loads(p.stdout.strip().splitlines()[-1])
    so = res.get("structured_output")
    return json.loads(so) if isinstance(so, str) else so


def check(b, spec, briefs, lens):
    brief = open(os.path.join(briefs, f"{b}.txt")).read()
    notes = told_notes(spec["brain_files"])
    ids = [f"{n['file']}#{n['id']}" for n in notes]
    data = [{"id": i, "note": n["statement"]} for i, n in zip(ids, notes)]
    prompt = f"{COMMON}\n\n{LENS[lens]}\n\nOWNER BRIEF:\n{brief}\n\nNOTES (JSON):\n{json.dumps(data, indent=1, ensure_ascii=False)}"
    root = tempfile.mkdtemp(prefix=f"sbgate-{b}-{lens}-", dir="/private/tmp")
    work = os.path.join(root, "work")
    os.makedirs(work)
    os.makedirs(os.path.join(root, "tmp"))
    v = run_claude(prompt, work)
    got = {x["id"]: x for x in v["notes"]}
    missing = [i for i in ids if i not in got]
    flagged = [dict(got[i], note=d["note"]) for i, d in zip(ids, data) if i in got and got[i]["flag"]]
    return {"lens": lens, "notes": len(ids), "missing": missing, "flagged": flagged}


if __name__ == "__main__":
    label, spec_path, briefs = sys.argv[1], sys.argv[2], sys.argv[3]
    spec = json.load(open(spec_path))
    out_dir = os.path.join(R, "gate", label)
    os.makedirs(out_dir, exist_ok=True)
    jobs = [(b, lens) for b in spec for lens in LENS]
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda j: check(j[0], spec[j[0]], briefs, j[1]), jobs))
    summary = {}
    for (b, lens), r in zip(jobs, res):
        s = summary.setdefault(b, {"notes": r["notes"], "pass": True})
        s[lens] = len(r["flagged"])
        s["missing_" + lens] = len(r["missing"])
        s["pass"] = s["pass"] and not r["flagged"] and not r["missing"] and r["notes"] > 0
        json.dump(r, open(os.path.join(out_dir, f"{b}.{lens}.json"), "w"), indent=1, ensure_ascii=False)
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))
