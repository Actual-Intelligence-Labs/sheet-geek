#!/usr/bin/env python3
"""Fact capture, confirmatory test secondary measure 4 (evals/PREREG-confirmatory.md); same scorer as the v0.2 acceptance run.

For each development business, two checkers from different model families
(Claude Opus 5.5 and OpenAI GPT-6-Astra, both headless, both given only the
text below) decide for every frozen fact (evals/v02-dev-facts.json) whether a
told note in the brain states it in full, and whether that note came from a
question about the fact's own subject. A fact counts only when both agree.

    /usr/bin/python3 score_capture.py <label> <spec.json>
    spec.json: {"<business>": {"brain_files": [...], "home": "<SPREADSHEET_BRAIN_HOME>"}}
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.expanduser("~/lab/spreadsheet-brain")
sys.path.insert(0, os.path.join(REPO, "skills", "spreadsheet-brain", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sheetbrain import brainzip  # noqa: E402
import run_answers as ra  # noqa: E402

class _Facts(dict):
    """Study 2: each business's owner facts come from its builder's key (keys/<b>.json, owner_facts),
    numbered in order; read only when scored, never printed."""
    def __missing__(self, b):
        key = json.load(open(os.path.join(R, "keys", f"{b}.json")))
        facts = [{"id": f"{b}-{i + 1:02d}", "fact": f if isinstance(f, str) else json.dumps(f, ensure_ascii=False)}
                 for i, f in enumerate(key["owner_facts"])]
        self[b] = facts
        return facts


FACTS = {"facts": _Facts()}
VERDICT = {"type": "object", "additionalProperties": False,
           "properties": {"facts": {"type": "array", "items": {
               "type": "object", "additionalProperties": False,
               "properties": {"id": {"type": "string"},
                              "captured": {"type": "string", "enum": ["full", "partial", "no"]},
                              "note_quote": {"type": "string"},
                              "asked": {"type": "boolean"},
                              "question_id": {"type": "string"},
                              "why": {"type": "string"}},
               "required": ["id", "captured", "note_quote", "asked", "question_id", "why"]}}},
           "required": ["facts"]}

RUBRIC = """You score how well a tool captured a business owner's knowledge. The tool asked the owner questions about a spreadsheet and wrote notes into the file. Below: the owner's brief (everything the owner knows), the list of facts to score, every note the owner is credited with (source told), and every question the tool asked with the owner's reply.

For EACH fact, decide:
- captured: "full" if one or more owner notes state the fact correctly with every rule-bearing clause (what it is, and what to do with it: leave out, combine, divide, which rows) and nothing that contradicts it; "partial" if a note states part of it or states it vaguely; "no" otherwise. Quote the note(s) in note_quote ("" if none).
- asked: true only if the captured note came from a question whose subject is this fact's subject (the question is about that column, code, rows, unit or rule). False if the note came from the goal question, the closing "anything else" question, the build question, or from words the owner volunteered while answering a question about something else. question_id is the id of the question the note came from ("" if none).
- why: one short sentence.
Judge only from the text below. Score every fact id exactly once."""


def told_notes(files):
    out = []
    for p in files:
        recs, _w, _i = brainzip.read_brain(p)
        for r in recs:
            if r.get("source") == "told":
                out.append({"file": os.path.basename(p), "id": r.get("id", ""), "ref": r.get("ref", ""),
                            "label": r.get("label", ""), "statement": r.get("statement", "")})
    return out


def questions(home):
    out = {}
    for st in sorted(glob.glob(os.path.join(home, "work", "*", "state.json"))):
        s = json.load(open(st))
        for qid, a in (s.get("answers") or {}).items():
            if not isinstance(a, dict):
                continue
            out[qid] = {k: a.get(k) for k in ("kind", "header", "prompt", "labels", "text", "not_sure", "about",
                                               "meta_header", "source") if a.get(k) not in (None, "", [])}
    return out


def payload(b, spec):
    brief = open(os.path.join(R, "briefs", f"{b}.txt")).read()
    data = {"facts": FACTS["facts"][b], "owner_notes": told_notes(spec["brain_files"]),
            "questions_and_replies": questions(spec["home"])}
    return f"{RUBRIC}\n\nOWNER BRIEF:\n{brief}\n\nDATA (JSON):\n{json.dumps(data, indent=1, ensure_ascii=False)}"


def run_claude(prompt, work):
    cmd = [ra.CLAUDE, "-p", prompt, "--model", "claude-opus-5-5", "--setting-sources", "local", "--strict-mcp-config",
           "--no-session-persistence", "--settings", ra.TRIAL_SETTINGS, "--json-schema", json.dumps(VERDICT),
           "--output-format", "json"]
    p = subprocess.run(cmd, cwd=work, env=ra.clean_env(os.path.dirname(work)), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=1800)
    res = json.loads(p.stdout.strip().splitlines()[-1])
    so = res.get("structured_output")
    return json.loads(so) if isinstance(so, str) else so


def run_codex(prompt, work):
    schema = os.path.join(work, "schema.json")
    json.dump(VERDICT, open(schema, "w"))
    out = os.path.join(work, "codex.json")
    cmd = [ra.CODEX, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6-astra", *ra.CODEX_OFF,
           "-c", 'model_reasoning_effort="high"', "-C", work, "--output-schema", schema, "-o", out, prompt]
    subprocess.run(cmd, cwd=work, env=ra.clean_env(os.path.dirname(work)), capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, timeout=1800)
    return json.load(open(out))


def score(b, spec, label):
    prompt = payload(b, spec)
    root = tempfile.mkdtemp(prefix=f"sbscore-{b}-", dir="/private/tmp")
    work = os.path.join(root, "work")
    os.makedirs(work)
    os.makedirs(os.path.join(root, "tmp"))
    with ThreadPoolExecutor(2) as ex:
        fc, fx = ex.submit(run_claude, prompt, work), ex.submit(run_codex, prompt, work)
        vc, vx = fc.result(), fx.result()
    by_c = {f["id"]: f for f in vc["facts"]}
    by_x = {f["id"]: f for f in vx["facts"]}
    rows = []
    for f in FACTS["facts"][b]:
        c, x = by_c.get(f["id"], {}), by_x.get(f["id"], {})
        rows.append({"id": f["id"], "fact": f["fact"], "claude": c, "codex": x,
                     "captured_full_both": c.get("captured") == "full" and x.get("captured") == "full",
                     "counted": (c.get("captured") == "full" and x.get("captured") == "full"
                                 and c.get("asked") is True and x.get("asked") is True)})
    n = len(rows)
    res = {"business": b, "label": label, "facts": n, "counted": sum(r["counted"] for r in rows),
           "captured_full_both": sum(r["captured_full_both"] for r in rows),
           "agreement_captured": sum(r["claude"].get("captured") == r["codex"].get("captured") for r in rows) / n,
           "rows": rows}
    res["rate"] = res["counted"] / n
    return res


if __name__ == "__main__":
    label, spec_path = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    out_dir = os.path.join(R, "capture", label)
    os.makedirs(out_dir, exist_ok=True)
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda b: score(b, spec[b], label), list(spec)))
    for r in results:
        json.dump(r, open(os.path.join(out_dir, f"{r['business']}.json"), "w"), indent=1, ensure_ascii=False)
    rates = [r["rate"] for r in results]
    pooled = sum(r["counted"] for r in results) / sum(r["facts"] for r in results)
    summary = {"label": label, "per_business": {r["business"]: {"counted": r["counted"], "facts": r["facts"],
                                                               "rate": round(r["rate"], 3),
                                                               "captured_full_both": r["captured_full_both"],
                                                               "checker_agreement": round(r["agreement_captured"], 3)}
                                                for r in results},
               "mean_rate": round(sum(rates) / len(rates), 3), "pooled_rate": round(pooled, 3), "min_rate": round(min(rates), 3)}
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))
