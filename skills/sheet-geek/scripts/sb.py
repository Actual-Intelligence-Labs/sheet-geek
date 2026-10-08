#!/usr/bin/env python3
"""sb: the Sheet Geek conductor.

Every command is non-interactive and prints one JSON object on stdout:
  {"ok": true, "say": "<show this to the user word for word>", "next": "<what to do next>", ...}
Diagnostics go to stderr. The model relays `say`, asks `ask` (or `ask_text`),
and runs the command named in `next`. Run `sb.py <command> --help` for details.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sheetbrain import brain as brain_mod, brainzip, findings, fresh, interview, privacy, rules, say  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402
from sheetbrain.fingerprint import deps_fp, file_fp, fp_like, parse_deps  # noqa: E402
from sheetbrain.store import BRAIN_ID, Store, now, today, under_sync_root  # noqa: E402

VERSION = brainzip.TOOL_VERSION
SHEET_EXT = (".xlsx", ".xlsm", ".csv", ".tsv")
NEXT_HELP = {
    "ask": "Ask the questions in `ask` with your structured question tool (or show `ask_text`), then "
           "pass the answers on standard input: sb.py answer <file> --json - (or --text -), with the "
           "answers in a quoted heredoc so apostrophes survive.",
    "save": "Run: sb.py preview <file> to show what will be saved, ask its one question, and pass the reply to "
            "sb.py answer <file> (add --copy <new path> for an uploaded file).",
    "preview": "Run: sb.py preview <file>.",
    "graph": "Run: sb.py graph <file> to draw the map, then give the user the file it names.",
    "build": "Build what the owner picked (see `build.how`), then offer the map: sb.py graph <file>.",
    "review": "Run: sb.py review <file> to ask about the notes that may be out of date.",
    "answer_user": "Answer the user's question using the brain notes; use code for any number.",
    "start": "Run: sb.py start <file>.",
    "done": "Nothing more to run.",
    "stop": "Stop and tell the user what happened.",
}


def emit(obj: dict, code: int = 0):
    obj.setdefault("ok", True)
    if "next" in obj:
        obj.setdefault("next_help", NEXT_HELP.get(obj["next"], ""))
    s = json.dumps(obj, ensure_ascii=False, default=str)
    sys.stdout.write(s + "\n")
    sys.stdout.flush()
    sys.exit(code)


def fail(msg: str, code: int = 1):
    emit({"ok": False, "say": msg, "next": "stop"}, code)


def arg_text(v):
    """'-' reads standard input, so answers containing apostrophes or quotes
    never have to survive shell quoting."""
    if v is None:
        return None
    if v == "-":
        return sys.stdin.read()
    return v


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def read_existing(path: str):
    try:
        return brainzip.read_brain(path)
    except brainzip.BrainError as e:
        return [], [str(e)], {"present": False}


def _sha(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def meta_id(records: list):
    """The brain id a file carries. It names folders in the local index, so an id
    that is not the 12 hex characters this tool writes is mapped to 12 hex made from
    it: the same file keeps the same id, and no id can name a folder outside the index."""
    m = next((r for r in records if r.get("record") == "meta"), None)
    bid = str(m.get("id", ""))[6:] if m and str(m.get("id", "")).startswith("brain:") else ""
    if not bid or BRAIN_ID.fullmatch(bid):
        return bid or None
    return hashlib.sha256(bid.encode("utf-8", "replace")).hexdigest()[:12]


def passable(f: dict, recs: list) -> list:
    """Notes fit to pass on in a copy or an export: a note someone else wrote (a received
    file, or rows added to your tab elsewhere) that reads like a command stays out, and
    a received file's 'owner' is the sender, not you."""
    foreign = {(r.get("id"), r.get("statement")) for r in f.get("foreign") or []}
    heads = {r.get("label") for r in recs if str(r.get("id") or "").startswith("col:")}

    def theirs(r):
        return f["origin"] != "own" or (r.get("id"), r.get("statement")) in foreign
    out = []
    for r in recs:
        if theirs(r) and brain_mod.kept_when_received(r) and brain_mod.reads_as_command(r.get("statement", ""), heads):
            continue
        if f["origin"] != "own" and r.get("source") == "told" and r.get("said_by", "") in ("", "owner"):
            r = dict(r, said_by="sender")
        out.append(r)
    return out


def new_file(p: str) -> str:
    """A path that names no existing file: ' (2)', ' (3)' ... before the extension."""
    base, ext = os.path.splitext(p)
    n = 2
    while os.path.exists(p):
        p, n = f"{base} ({n}){ext}", n + 1
    return p


def session_files(paths: list) -> list:
    """The files a session started with: `answer f.csv` still sees f.csv's partner
    files, so links between them survive every round."""
    paths = [os.path.abspath(p) for p in paths]
    try:
        st = Store()
        for p in paths:
            row = st.db.execute("SELECT brain_id FROM files WHERE path=? ORDER BY updated_at DESC LIMIT 1",
                                (p,)).fetchone()
            if not row:
                continue
            named = {os.path.basename(x) for x in paths}
            for extra in st.state(row[0]).get("files", []) or []:
                # a partner file only: never a second copy of a file named here (the one named wins)
                if extra not in paths and os.path.basename(extra) not in named and os.path.exists(extra):
                    paths.append(extra)
        st.close()
    except Exception:  # noqa: BLE001  (a missing index never blocks the command)
        pass
    return paths


class Ctx:
    def __init__(self, paths: list, *, analyze: bool = True, expand: bool = True):
        self.paths = session_files(paths) if expand else [os.path.abspath(p) for p in paths]
        for p in self.paths:
            if not os.path.exists(p):
                fail(f"I can't find {p}.")
        self.store = Store()
        self.files = []
        for p in self.paths:
            recs, warns, info = read_existing(p)
            bid, origin = self.store.brain_id_for(p, meta_id(recs), has_brain=bool(recs))
            entry = {"path": p, "name": os.path.basename(p), "records": recs, "warnings": warns,
                     "info": info, "brain_id": bid, "origin": origin, "has_brain": bool(recs),
                     "tab_missing": False, "sha": _sha(p)}
            entry["foreign"] = []
            if recs and origin == "own":
                # a file you sent that came back edited: notes you never wrote here are not yours
                mine = {(r.get("id"), r.get("statement")) for r in self.store.records(bid)}
                if mine:
                    entry["foreign"] = [r for r in recs if r.get("source") in ("told", "web", "inferred")
                                        and (r.get("id"), r.get("statement")) not in mine]
            if not recs:
                idx = self.store.records(bid)
                if idx:
                    entry["records"] = [{k: v for k, v in r.items() if not k.startswith("_")}
                                        for r in idx]
                    entry["has_brain"] = True
                    known = self.store.file(bid) or {}
                    entry["tab_missing"] = known.get("tab_state") in ("visible", "hidden")
            self.files.append(entry)
        self.a = None
        self.secs = 0.0
        if analyze:
            t0 = time.time()
            try:
                self.a = Analysis(self.paths)
            except brainzip.BrainError as e:
                fail(str(e))
            self.secs = time.time() - t0

    def f(self, path: str | None = None) -> dict:
        if path is None:
            return self.files[0]
        path = os.path.abspath(path)
        return next(x for x in self.files if x["path"] == path)

    def focus(self) -> dict:
        return next((x for x in self.files if not x["has_brain"]), self.files[0])


def verify_received(ctx: Ctx, f: dict) -> dict:
    """Re-check a received brain's counted facts against the data."""
    holds = fails = 0
    for r in f["records"]:
        if r.get("source") != "computed" or not r.get("data_fp") or not r.get("depends_on"):
            continue
        now_fp = deps_fp(ctx.a, f["path"], parse_deps(r["depends_on"]))
        if now_fp and now_fp == r["data_fp"]:
            holds += 1
        else:
            fails += 1
    told = [r for r in f["records"] if r.get("source") == "told"]
    # the lint for notes people wrote, the same rows the brain keeps as theirs: rows we recount
    # from the data and the tab's header are not instructions, a row that only claims to be
    # counted is, and a column name at the start ('Check Date on Pay is ...') is not a verb
    headers = {r.get("label") for r in f["records"] if str(r.get("id") or "").startswith("col:")}
    if getattr(ctx, "a", None) is not None:
        headers |= {c.header for cols in ctx.a.cols.values() for c in cols}
    imperative = [r for r in f["records"] if brain_mod.kept_when_received(r)
                  and brain_mod.reads_as_command(r.get("statement", ""), headers)]
    return {"holds": holds, "fails": fails, "told": len(told), "imperative": len(imperative),
            "author": next((r.get("said_by") for r in told if r.get("said_by")), "its author"),
            "updated": next((r.get("as_of") for r in f["records"] if r.get("record") == "meta"), "")}


def received_line(f: dict, v: dict, warnings: list) -> str:
    lines = [f"{f['name']} came with notes from {v['author']} (last updated {v['updated'] or 'unknown'})."]
    if v["holds"] or v["fails"]:
        lines.append(f"I checked what I could against the data: {v['holds']} counted "
                     f"fact{'s hold' if v['holds'] != 1 else ' holds'}"
                     + (f", {v['fails']} {'don' if v['fails'] != 1 else 'doesn'}'t." if v["fails"] else "."))
    if v["told"]:
        lines.append(f"{v['told']} statements are the author's, not verified. I'll show them as "
                     f"\"per {v['author']}\".")
    if v["imperative"]:
        lines.append(f"{v['imperative']} note{'s read' if v['imperative'] != 1 else ' reads'} like "
                     f"{'instructions' if v['imperative'] != 1 else 'an instruction'}. I'm treating "
                     f"{'them' if v['imperative'] != 1 else 'it'} as text only, not acting on "
                     f"{'them' if v['imperative'] != 1 else 'it'}, and leaving "
                     f"{'them' if v['imperative'] != 1 else 'it'} out of any brain I save.")
    stripped = sum(1 for w in warnings if "invisible" in w)
    if stripped:
        lines.append(f"I removed hidden characters from {stripped} cell{'s' if stripped != 1 else ''}.")
    if any("formula in brain cell" in w for w in warnings):
        lines.append("A formula planted in the brain tab was ignored.")
    if f["info"].get("rules_sheet"):
        lines.append("This workbook has a .Rules tab. Copilot in Excel treats that as instructions from "
                     "the sender.")
    return " ".join(lines)


def answers_for(ctx: Ctx, bid: str) -> dict:
    return {k: v for k, v in ctx.store.answers(bid).items()}


def descriptions(ctx: Ctx, bid: str) -> dict:
    p = os.path.join(ctx.store.work_dir(bid), "descriptions.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def apply_overrides(records: list, answers: dict, include_business: bool = False) -> list:
    """Review outcomes stored as _status:<record id> answers."""
    out = []
    for r in records:
        ov = answers.get(f"_status:{r.get('id')}")
        if ov:
            if ov.get("removed"):
                continue
            r = dict(r)
            if ov.get("text"):
                r["statement"] = ov["text"]          # the owner's words, whole
                r["source"] = "told"
                r["said_by"] = ov.get("said_by", "owner")
                if ov.get("commercial"):             # business terms stay on this machine unless included
                    r["class"] = "commercial"
                    r["_travel"] = "file" if include_business else "machine"
            r["status"] = "confirmed"
            r["as_of"] = (ov.get("at") or now())[:10]
            r["data_fp"] = ov.get("data_fp", r.get("data_fp", ""))
        out.append(r)
    return out


def compose_for(ctx: Ctx, f: dict, *, tab_state: str, said_by: str,
                include_business: bool = False) -> tuple:
    bid = f["brain_id"]
    answers = answers_for(ctx, bid)
    received = f["records"] if f["origin"] == "received" else list(f.get("foreign") or [])
    prev = f["records"] if f["origin"] == "own" else ctx.store.records(bid)
    ctx.a.apply_answers(answers)      # the owner's confirmed rules re-scope every counted number
    comp = Composer(ctx.a, f["path"], bid, answers, descriptions=descriptions(ctx, bid),
                    received=received, said_by=said_by, tab_state=tab_state, store=ctx.store)
    comp.include_business = include_business
    recs = comp.compose()
    recs = fresh.merge_previous(recs, prev)
    recs = apply_overrides(recs, answers, include_business)
    private = [(p["text"], p["reason"]) for p in ctx.store.private(bid)]
    return recs, private


def write_brain_md(ctx: Ctx, f: dict, records: list):
    project = ctx.store.project_for(f["path"])
    d = ctx.store.project_dir(project)
    path = os.path.join(d, "brain.md")
    sections = {}
    if os.path.exists(path):
        cur = None
        for line in open(path, encoding="utf-8").read().split("\n"):
            if line.startswith("## "):
                cur = line[3:].strip()
                sections[cur] = []
            elif cur:
                sections[cur].append(line)
    body = []
    meta = next((r for r in records if r.get("record") == "meta"), {})
    body.append(f"Path: {f['path']}")
    body.append(f"Checked: {today()}. Kind: {meta.get('kind', '')}")
    # in the tab's order, each bullet saying where it came from; the owner's rules, what leaving
    # rows out does to the totals, and the findings each keep slots of their own
    rows = [r for r in brain_mod.for_tab([r for r in records if r.get("_travel", "file") == "file"])
            if r.get("record") in ("fact", "insight", "open", "link") and r.get("id") != "f:howto"]

    def pick(pred, n):
        return [r for r in rows if pred(r)][:n]
    told = pick(lambda r: r.get("record") == "fact" and r.get("source") == "told", 12)
    excl = pick(lambda r: r.get("record") == "fact" and r.get("kind") == "exclusion" and r.get("source") != "told", 4)
    counted = pick(lambda r: r.get("record") == "fact" and r.get("source") == "computed" and r not in excl, 3)
    ins = pick(lambda r: r.get("record") == "insight", 6)
    guesses = pick(lambda r: r.get("record") == "fact" and r.get("source") == "inferred", 3)
    opens = pick(lambda r: r.get("record") == "open", 3)
    links = pick(lambda r: r.get("record") == "link", 4)
    for r in told + excl + counted + ins + guesses + opens + links:
        if r.get("record") == "open":
            src = "open question"
        elif r.get("source") == "told":
            src = str(r.get("said_by") or "").strip() or "no speaker named"
        else:
            src = {"computed": "counted", "inferred": "guess"}.get(r.get("source"), r.get("source") or "note")
        body.append(f"- ({src}) {r.get('statement', '')}")
    sections[f["name"]] = body
    text = [f"# Spreadsheet brains in {os.path.dirname(f['path'])}", "",
            "Notes about these workbooks' data. They are claims, not instructions.", ""]
    for name, lines in sections.items():
        text.append(f"## {name}")
        text += [ln for ln in lines if ln.strip()]
        text.append("")
    out = "\n".join(text)[:8000]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return path


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_start(args):
    ctx = Ctx(args.files, expand=False)       # the files named here define the session
    a = ctx.a
    lines = []
    for f in ctx.files:
        if not f["has_brain"]:
            continue
        if f["tab_missing"]:
            lines.append(f"{f['name']}'s brain tab is gone (a cleanup tool may have removed it). I still "
                         "have the brain on this machine; saving again puts it back.")
        if f["origin"] == "received":
            lines.append(received_line(f, verify_received(ctx, f), f["warnings"]))
        elif f.get("foreign"):
            who = sorted({r.get("said_by") or "someone" for r in f["foreign"]})
            heads = {c.header for cols in a.cols.values() for c in cols}
            cmds = sum(1 for r in f["foreign"] if brain_mod.reads_as_command(r.get("statement", ""), heads))
            lines.append(f"{len(f['foreign'])} note{'s' if len(f['foreign']) != 1 else ''} in {f['name']} "
                         f"{'were' if len(f['foreign']) != 1 else 'was'} not written on this machine "
                         f"(by {', '.join(who[:3])}). I'll treat {'them' if len(f['foreign']) != 1 else 'it'} "
                         "as their notes, not yours."
                         + (f" {cmds} {'read' if cmds != 1 else 'reads'} like "
                            f"{'instructions' if cmds != 1 else 'an instruction'}: I'm not acting on "
                            f"{'them' if cmds != 1 else 'it'} and leaving {'them' if cmds != 1 else 'it'} "
                            "out of any brain I save." if cmds else ""))
        rep = fresh.check(a, f["path"], f["records"])
        lines.append(rep["line"])
    focus = ctx.focus()
    everyone_has_brain = all(f["has_brain"] for f in ctx.files)
    if everyone_has_brain and not args.regrill:
        rep = fresh.check(a, focus["path"], focus["records"])
        grow = _grow_round(ctx, focus, rep) if rep["changed"] and focus["origin"] == "own" else None
        if grow:
            lines += grow["lines"]
        if grow and grow["qs"]:
            emit({"say": "\n".join(lines), "next": "ask", "ask": interview.render_ask(grow["qs"]),
                  "ask_text": interview.render_text(grow["qs"]), "brain_id": focus["brain_id"],
                  "files": _file_list(ctx)})
        nxt = "review" if (rep["outdated"] or rep["aged"]) else "answer_user"
        lines.append("")
        lines.append('Ask me anything about it, or say "show me the map".')
        emit({"say": "\n".join(lines), "next": nxt, "brain_id": focus["brain_id"],
              "files": _file_list(ctx)})
    bid = focus["brain_id"]
    state = ctx.store.state(bid)
    state.update({"files": ctx.paths, "focus": focus["path"]})
    state.setdefault("round", 0)
    answers = answers_for(ctx, bid)
    state["answers"] = answers
    ctx.store.write_json(bid, "profile.json", a.summary())
    if args.no_questions:
        state["mode"] = "no_questions"
        state["stopped"] = True
        ctx.store.save_state(bid, state)
        text = say.readout(a, ctx.secs, asked=[])
        emit({"say": "\n".join(lines + [text, "Building from what I can see, no questions. Everything "
                                               "I guess is marked as a guess."]),
              "next": "save", "brain_id": bid, "files": _file_list(ctx)})
    qs = interview.next_round(a, state)
    qs = [rules.dress(a, q) for q in qs]      # shown as the answer reads them: a heavy rule says what it removes
    state["pending"] = [q.id for q in qs]
    ctx.store.save_state(bid, state)
    text = say.readout(a, ctx.secs, n_questions=len(qs), asked=qs)
    if lines:
        text = "\n".join(lines) + "\n\n" + text
    if not qs:
        sug, _ = say.suggestions(a, answers)
        emit({"say": text + "\nI have what I need.\n\n" + sug, "next": "save", "brain_id": bid,
              "files": _file_list(ctx)})
    emit({"say": text, "next": "ask", "ask": interview.render_ask(qs),
          "ask_text": interview.render_text(qs), "brain_id": bid, "files": _file_list(ctx),
          "profile": os.path.join(ctx.store.work_dir(bid), "profile.json")})


def _grow_round(ctx: Ctx, f: dict, rep: dict) -> dict | None:
    """The data grew: apply the owner's rules to the new rows without asking, and ask
    only about what is new (a new code, a new hotel), suggesting the answer the brain
    already points to."""
    from dataclasses import asdict
    from sheetbrain import findings, rules
    bid = f["brain_id"]
    answers = answers_for(ctx, bid)
    excluded = ctx.a.apply_answers(answers)
    asked_before = {str(r.get("id", ""))[2:] for r in f["records"] if r.get("record") == "open"}
    new_findings = [q for q in findings.finding_questions(ctx.a, answers)
                    if q.id not in answers and q.id not in asked_before and not q.gated]
    qs = (findings.grow_questions(ctx.a, answers, rep, since=rep.get("since", ""), prev=f["records"])
          + new_findings)[:4]
    applied = [f"{', '.join(v[:3])} {'stays' if len(v) == 1 else 'stay'} out of totals" for _s, _h, v in excluded]
    fees = sorted(rules.not_items(ctx.a, answers))
    if fees:
        applied.append(f"{' and '.join(x.upper() for x in fees[:3])} {'stays' if len(fees) == 1 else 'stay'} out of "
                       "price comparisons")
    if any(k.startswith("follow_net_") and "net" in (v.get("options") or []) for k, v in answers.items()
           if isinstance(v, dict)):
        applied.append("credits are netted in totals, while price comparisons still use the lines before credits "
                       "(not applied)")
    elif any(k.startswith("follow_net_") and "totals_only" in (v.get("options") or []) for k, v in answers.items()
             if isinstance(v, dict)):
        applied.append("credits are netted in totals only")
    lines = []
    if applied:
        lines.append("I applied what you told me before to the new rows: " + "; ".join(applied) + ".")
    if not qs:
        return {"qs": [], "lines": lines}
    qs = [rules.dress(ctx.a, q) for q in qs]  # shown as the answer reads them
    state = ctx.store.state(bid)
    state.update({"mode": "grow", "grow_qs": [asdict(q) for q in qs], "pending": [q.id for q in qs],
                  "files": ctx.paths, "focus": f["path"]})
    ctx.store.save_state(bid, state)
    lines.append(f"{len(qs)} new thing{'s' if len(qs) != 1 else ''} to check, with my best guess marked:")
    return {"qs": qs, "lines": lines}


def _file_list(ctx: Ctx) -> list:
    return [{"path": f["path"], "brain_id": f["brain_id"], "has_brain": f["has_brain"],
             "origin": f["origin"]} for f in ctx.files]


def cmd_answer(args):
    ctx = Ctx(args.files)
    a = ctx.a
    focus = ctx.focus() if not args.brain_file else ctx.f(args.brain_file)
    bid = focus["brain_id"]
    state = ctx.store.state(bid)
    if state.get("pending") == ["_save_failed"]:     # a choice about a failed save, not about the data
        return _answer_save_failed(ctx, args, bid, state)
    if state.get("pending") == ["_save"]:            # where the brain goes, asked by preview
        return _answer_save(ctx, args, bid, state)
    answers = answers_for(ctx, bid)
    confirmed_before = {rules.ident(a, r) for r in rules.confirmed(a, answers)}
    raw = None
    if args.json:
        try:
            raw = json.loads(args.json)
        except json.JSONDecodeError as e:
            fail(f"--json is not valid JSON: {e}")
    elif args.text is not None:
        raw = args.text
        if re.fullmatch(r"\s*(stop|enough|skip|just build it|no more( questions)?|done)\s*[.!]?\s*",
                        raw, re.I):
            state["stopped"] = True
            ctx.store.save_state(bid, state)
            sug, _ = say.suggestions(a, answers)
            emit({"say": "Stopping the questions. " + sug, "next": "save", "brain_id": bid})
    else:
        fail("Pass the answers with --json or --text.")
    pending = list(state.get("pending", []))
    if pending == ["_build"]:
        return _answer_build(ctx, a, bid, state, answers, raw)
    closing = pending == [interview.CLOSER]
    if state.get("mode") == "grow":        # questions about what is new since the brain was written
        qs = [interview.Q(**d) for d in state.get("grow_qs", [])]
    elif closing:
        qs = [q for q in [interview.closer_question(a, answers)] if q is not None]
    else:
        by_id = {q.id: q for q in interview.candidates(a, answers)}
        rb = _readback(a, answers)            # a readback asked on its own, after the rounds
        if rb is not None:
            by_id.setdefault(rb.id, rb)
        qs = [by_id[i] for i in pending if i in by_id] or \
            interview.next_round(a, dict(state, answers=answers))
    qs = [rules.dress(a, q) for q in qs]      # the labels the owner was shown
    parsed = interview.parse_answers(qs, raw)
    if not parsed:
        emit({"ok": False, "say": "I couldn't match that reply to the questions. Try again, for "
                                  "example \"1a 2b\".", "next": "ask",
              "ask": interview.render_ask(qs), "ask_text": interview.render_text(qs)})
    names = privacy.entity_names(a)
    headers = privacy.vocabulary(a)              # column names and line-item labels are the data's own words
    amounts = privacy.workbook_amounts(a)        # an amount already in the file is no business term to withhold
    kept_private = 0
    for qid, ans in parsed.items():
        if ans.get("text"):
            kept, private, commercial = privacy.screen(ans["text"], names, headers, amounts)
            for sent, reason in private:
                ctx.store.add_private(bid, sent, reason)
                kept_private += 1
            ans["text"] = kept
            if commercial:
                ans["commercial"] = True
        ans["at"] = now()
        save_answer(ctx, bid, qid, ans)          # and on every other brain whose tables it touches
    # a pick or an answer that applied a rule just now is said back with its rows and money
    answers = answers_for(ctx, bid)
    shown = rules.offered(answers, a)
    applied_note = " ".join(say.applied_line(a, r) for r in rules.confirmed(a, answers)
                            if rules.ident(a, r) not in confirmed_before and rules.ident(a, r) not in shown)
    if state.get("after_build"):   # a second rule readback, asked after the build pick: then save and build
        state["after_build"] = False
        state["answers"] = answers
        return _build_next(ctx, a, bid, state, answers, state.get("build") or {}, lead=applied_note or "Thanks.")
    if state.get("mode") == "grow":
        state["mode"] = ""
        state["pending"] = []
        state["answers"] = answers_for(ctx, bid)
        ctx.store.save_state(bid, state)
        emit({"say": "Got it, noted with today's date. It goes into the brain when it is saved: next I'll show you "
                     "what changes in the file, then save it.", "next": "preview", "brain_id": bid})
    if state.get("jit"):          # the just-in-time answers for the build pick: now save, then build
        answers = answers_for(ctx, bid)
        # threads those answers opened (not threads older answers left open) are asked once, before the
        # preview, from what is left of the extra prompts the just-in-time questions drew on
        jit_ids = state.get("jit_ids") or []
        fu = []
        if not state.get("jit_follow"):
            before = {q.id for q in findings.follow_ups(a, {k: v for k, v in answers.items() if k not in jit_ids})}
            fu = [q for q in findings.follow_ups(a, answers) if q.id not in answers and q.id not in before]
            fu = fu[:max(0, _jit_room(answers) - len(jit_ids))]
        state["answers"] = answers
        if fu:
            state["jit_follow"] = True
            state["pending"] = [q.id for q in fu]
            ctx.store.save_state(bid, state)
            emit({"say": f"Thanks. {'One more' if len(fu) == 1 else 'Two more'} on what you just said:",
                  "next": "ask", "ask": interview.render_ask(fu), "ask_text": interview.render_text(fu),
                  "brain_id": bid, "build": _build_plan(state.get("build") or {})})
        state["jit"] = False
        state["jit_follow"] = False
        state["jit_ids"] = []
        state["pending"] = []
        ctx.store.save_state(bid, state)
        emit({"say": "Thanks. First I'll show you exactly what goes into the file and save the brain, then I'll "
                     "build it.", "next": "preview", "brain_id": bid, "build": _build_plan(state.get("build") or {})})
    state["round"] = state.get("round", 0) + 1
    answers = answers_for(ctx, bid)
    state["answers"] = answers
    nxt = [] if closing else interview.next_round(a, state)
    state["pending"] = [q.id for q in nxt]
    ctx.store.save_state(bid, state)
    note = (applied_note + " ") if applied_note else ""
    if kept_private:
        note += (f"I'll keep {kept_private} thing{'s' if kept_private != 1 else ''} you said on this machine "
                 "only, since it sounds private. ")
    nxt = [rules.dress(a, q) for q in nxt]
    if nxt:
        goal = (answers.get("goal") or {}).get("labels") or []
        lead = (f"{len(nxt)} more, because you picked \"{goal[0]}\"." if len(goal) == 1 and state["round"] == 1
                else f"{len(nxt)} more, based on your answers.")
        emit({"say": note + lead, "next": "ask", "ask": interview.render_ask(nxt),
              "ask_text": interview.render_text(nxt), "brain_id": bid})
    # after the last round, once: what someone new would get wrong (outside the question cap)
    cq = interview.closer_question(a, answers) if not state.get("stopped") else None
    if cq is not None:
        state["pending"] = [cq.id]
        ctx.store.save_state(bid, state)
        emit({"say": note + "Got it.", "next": "ask", "ask": interview.render_ask([cq]),
              "ask_text": interview.render_text([cq]), "brain_id": bid})
    bq = interview.build_question(a, answers)
    # rules the owner typed and has not seen read back: asked now, in the same message as the build pick
    rb = _readback(a, answers)
    if bq is None and rb is None:
        emit({"say": note + "That's all I need. First I'll show you exactly what goes into the file, then "
                            "save it.", "next": "preview", "brain_id": bid})
    if bq is None:
        state["pending"] = [rb.id]
        ctx.store.save_state(bid, state)
        emit({"say": note + "Got it. One more, on the rules you typed; then I'll show you what goes into the file:",
              "next": "ask", "ask": interview.render_ask([rb]), "ask_text": interview.render_text([rb]),
              "brain_id": bid})
    state["pending"] = ["_build"]
    state["readback"] = rb.id if rb is not None else ""
    ctx.store.save_state(bid, state)
    qs = [q for q in (rb, bq) if q is not None]
    emit({"say": note + _steps_left(answers, rb is not None), "next": "ask",
          "ask": interview.render_ask(qs), "ask_text": interview.render_text(qs), "brain_id": bid})


def _steps_left(answers: dict, readback: bool) -> str:
    """What is still to come, counted, never 'last' when more may follow: the rules
    readback, the build pick, the answers a build pick may still need (from what
    is left of the extra prompts) and the save."""
    room = _jit_room(answers) - (1 if readback else 0)
    asks = "Two more: the rules you typed, and what to build first" if readback else \
        "One more: what to build first"
    extra = (f" (what you pick may need {'one more answer' if room == 1 else 'one or two more answers'})"
             if room > 0 else "")
    return f"Got it. {asks}{extra}; then I'll show you what goes into the file and save it."


def save_answer(ctx: Ctx, bid: str, qid: str, ans: dict) -> list:
    """Save an answer on the brain it was asked in and on every other brain of the
    session whose tables it touches, so a guess it settles retires in each of
    them. Returns the other brains it reached."""
    ctx.store.save_answer(bid, qid, ans)
    others = touched_brains(ctx, bid, qid, ans)
    for ob in others:
        ctx.store.save_answer(ob, qid, ans)
    return others


def touched_brains(ctx: Ctx, bid: str, qid: str, ans: dict) -> list:
    """The session's other brains whose tables an answer is about: the table its
    question names, every table the question read, and the tables of the columns
    whose guesses it settles."""
    a = ctx.a
    if a is None:
        return []
    tids = set(ans.get("tables") or []) | {(ans.get("about") or {}).get("table")}
    roles = a.detection["roles"]
    for p in interview.settled(a, {qid: ans}):
        r = roles.get(p.partition(":")[2].split("=")[0])
        if r:
            tids.add(r.get("table"))
    paths = {a.file_of.get(t) for t in tids if t}
    return [f["brain_id"] for f in ctx.files if f["brain_id"] != bid and f["path"] in paths]


def _answer_build(ctx, a, bid, state, answers, raw):
    """The owner picked what to build (or typed something else entirely), and
    answered the rule readback when it rode along. Rules typed on that readback
    are read back once more, before the build's own questions."""
    bq = interview.build_question(a, answers)
    rb = _readback(a, answers) if state.get("readback") else None
    typed_rules = False
    if rb is not None and rb.id == state["readback"] and bq is not None:
        parsed = interview.parse_answers([rb, bq], raw) or interview.parse_answers([bq], raw)
        ans = parsed.get(rb.id)
        if ans:
            if ans.get("text"):
                kept, private, _ = privacy.screen(ans["text"], privacy.entity_names(a), privacy.vocabulary(a),
                                                  privacy.workbook_amounts(a))
                for sent, reason in private:
                    ctx.store.add_private(bid, sent, reason)
                ans["text"] = kept
                typed_rules = bool(kept.strip())
            ans["at"] = now()
            save_answer(ctx, bid, rb.id, ans)          # and on every brain whose tables a rule is on
            answers = answers_for(ctx, bid)
            state["answers"] = answers
    else:
        parsed = interview.parse_answers([bq], raw) if bq else {}
    state["readback"] = ""
    # a reply that answered only the readback picked nothing to build
    choice = parsed.get("_build") or {"options": [], "labels": [],
                                      "text": raw if isinstance(raw, str) and not parsed else ""}
    if choice.get("text"):
        names = privacy.entity_names(a)
        headers = privacy.vocabulary(a)
        kept, private, _ = privacy.screen(choice["text"], names, headers, privacy.workbook_amounts(a))
        for sent, reason in private:
            ctx.store.add_private(bid, sent, reason)
        choice["text"] = kept
    choice["at"] = now()
    ctx.store.save_answer(bid, "_build", choice)
    state["pending"] = []
    state["build"] = choice
    ctx.store.save_state(bid, state)
    what = ((choice.get("labels") or [None])[0] or (choice.get("text") or "").strip() or "nothing yet").rstrip(". ")
    # rules typed on the readback, read back once more (within the extra prompts), before anything else
    again = _readback(a, answers) if typed_rules else None
    if again is not None:
        state["pending"] = [again.id]
        state["after_build"] = True
        ctx.store.save_state(bid, state)
        emit({"say": brain_mod._sentence(f"Got it: {what}") + " One more, on the rules you just typed:",
              "next": "ask", "ask": interview.render_ask([again]), "ask_text": interview.render_text([again]),
              "brain_id": bid, "build": _build_plan(choice)})
    return _build_next(ctx, a, bid, state, answers, choice, lead=brain_mod._sentence(f"Got it: {what}"))


def _build_next(ctx, a, bid, state, answers, choice, lead: str = ""):
    """After the build pick: the answer it still needs, asked just in time, then the
    preview and the save."""
    lead = (lead.strip() + " ") if lead.strip() else ""
    # the pick needs an answer nobody gave yet: ask it now, just in time, then save
    _, items = say.suggestions(a, answers)
    item = next((it for it in items if it["id"] in (choice.get("options") or [])), None)
    by_id = {q.id: q for q in interview.candidates(a, answers)}
    jit = [by_id[m] for m in (item or {}).get("missing", []) if m in by_id][:_jit_room(answers)]
    if jit:
        state["pending"] = [q.id for q in jit]
        state["jit"] = True
        state["jit_ids"] = [q.id for q in jit]
        ctx.store.save_state(bid, state)
        more = "one more answer" if len(jit) == 1 else "two more answers"
        emit({"say": lead + f"It needs {more} first.", "next": "ask",
              "ask": interview.render_ask(jit), "ask_text": interview.render_text(jit), "brain_id": bid,
              "build": _build_plan(choice)})
    state["pending"] = []
    ctx.store.save_state(bid, state)
    emit({"say": lead + "First I'll show you exactly what goes into the file and save the brain, then I'll "
                        "build it.", "next": "preview", "brain_id": bid, "build": _build_plan(choice)})


def _readback(a, answers: dict):
    """The rule readback to ask now, dressed as the owner sees it: the rules typed
    for every count and total first, then those kept for one calculation."""
    rb = findings.readback(a, answers) or rules.scoped_readback(a, answers)
    return rules.dress(a, rb) if rb is not None else None


def _jit_room(answers: dict) -> int:
    """How many just-in-time questions may still be asked: rule readbacks and
    just-in-time questions share one budget of extra prompts per workbook."""
    return max(0, rules.MAX_READBACKS - sum(1 for k in answers or {} if k.startswith(findings.READBACK)))


def _build_plan(choice: dict) -> dict:
    """How the agent should build the pick: a deterministic export when one exists."""
    label = " ".join((choice.get("labels") or []) + [choice.get("options", [""])[0] if choice.get("options")
                                                      else ""]).lower()
    for kind, words in (("guide", ("guide", "next owner")), ("dictionary", ("dictionary",)),
                        ("blueprint", ("blueprint", "app plan"))):
        if any(w in label for w in words):
            return {"what": (choice.get("labels") or [""])[0], "how": f"sb.py export <file> --kind {kind}"}
    label, text = (choice.get("labels") or [""])[0], (choice.get("text") or "").strip()
    return {"what": f"{label}: {text}" if label and text else (label or text),
            "how": "Build it with code as a new file, after reading the brain with sb.py read <file>."}


def cmd_suggest(args):
    ctx = Ctx(args.files)
    bid = ctx.focus()["brain_id"] if not ctx.f()["has_brain"] else ctx.f()["brain_id"]
    answers = answers_for(ctx, bid)
    text, items = say.suggestions(ctx.a, answers)
    emit({"say": text, "next": "answer_user",
          "suggestions": [{k: v for k, v in i.items() if k != "score"} for i in items]})


def cmd_preview(args):
    ctx = Ctx(args.files)
    out = []
    for f in ctx.files:
        if args.only and f["path"] != os.path.abspath(args.only):
            continue
        recs, private = compose_for(ctx, f, tab_state="hidden" if args.hidden else "visible",
                                    said_by=args.said_by, include_business=args.include_business_terms)
        kind = "csv" if f["path"].lower().endswith((".csv", ".tsv")) else "xlsx"
        out.append(say.save_preview(recs, private, name=f["name"], kind=kind,
                                    tab_state="hidden" if args.hidden else "visible"))
        if args.show_rows:
            rows = [r for r in recs if r.get("_travel", "file") == "file"]
            out.append("\n".join(f"{r['record']} | {r['id']} | {r['source']} | {r['status']} | "
                                 f"{r['statement']}" for r in rows[:400]))
    q = say.save_question()
    # one protocol: the reply goes to sb.py answer like every other ask, and the save runs from there
    focus = ctx.focus()
    state = ctx.store.state(focus["brain_id"])
    ask = dict(state.get("save_ask") or {}) if state.get("pending") == ["_save"] else {
        "before": [] if state.get("pending") == ["_save_failed"] else list(state.get("pending") or [])}
    ask.update({"files": [os.path.abspath(p) for p in args.files], "only": args.only, "said_by": args.said_by,
                "include_business_terms": bool(args.include_business_terms)})
    state["save_ask"] = ask
    state["pending"] = ["_save"]
    ctx.store.save_state(focus["brain_id"], state)
    emit({"say": "\n\n".join(out), "next": "ask", "ask": interview.render_ask([q]),
          "ask_text": interview.render_text([q]),
          "then": "Pass the reply to sb.py answer <file> like any other answer; it saves where the owner picked. "
                  "Add --copy <new path> to save into a copy (an uploaded file), or --include-business-terms if "
                  "the owner said to put the business terms in the file."})


def cmd_save(args):
    ctx = Ctx(args.files)
    cards = []
    written = []
    changed = False               # a file changed under us: starting again, not a retry, reads it anew
    tab_state = "hidden" if args.hidden else "visible"
    for f in ctx.files:           # saving answers the preview's question, however it was run
        _clear_save_ask(ctx.store, f["brain_id"])
    for f in ctx.files:
        if args.only and f["path"] != os.path.abspath(args.only):
            continue
        recs, private = compose_for(ctx, f, tab_state=tab_state, said_by=args.said_by,
                                    include_business=args.include_business_terms)
        bid = f["brain_id"]
        travels = [r for r in recs if r.get("_travel", "file") == "file"]
        file_recs = brain_mod.for_tab(travels)
        from sheetbrain import graph as graph_mod
        # the drawing on the tab shows the whole graph of things; the rows hold only the notes.
        # A drawing that cannot be made never stops the save: the notes still go in.
        try:
            drawing = graph_mod.build(travels, title=f["name"], playbook=ctx.a.playbook,
                                      owners={"owner", args.said_by or "owner"})
        except Exception:  # noqa: BLE001
            drawing = None
        result = None
        warnings = []
        is_csv = f["path"].lower().endswith((".csv", ".tsv"))
        # where the brain goes, worked out first so a failed save can name the copy it did not make
        target = f["path"]
        if args.copy and is_csv:        # a copy of the CSV, with its brain next to the copy
            target = args.copy if len(ctx.files) == 1 and args.copy.lower().endswith(
                (".csv", ".tsv")) else os.path.join(args.copy, f["name"])
        elif args.copy:
            if len(ctx.files) == 1 and not os.path.splitext(args.copy)[1] and not os.path.isdir(args.copy):
                args.copy += os.path.splitext(f["path"])[1]      # 'h1' becomes 'h1.xlsx'
            target = args.copy if len(ctx.files) == 1 else os.path.join(args.copy, f["name"])
        target = os.path.abspath(target)
        copy = target if args.copy else ""
        # what was there before, so a copy this save starts and cannot finish is taken away again
        existed = os.path.exists(target)
        side_existed = is_csv and os.path.exists(brainzip.sidecar_path(target))
        back, reason, touched, wrote_copy = [], "", False, False
        if args.local_only:
            outcome = "local"
        elif _sha(f["path"]) != f["sha"]:
            outcome, reason = "failed", ("the file changed while I was working (someone saved it), so I didn't "
                                         "write to it. Run start again to pick up the new version")
            changed = True
        else:
            try:
                if copy:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                if is_csv:
                    if copy:
                        import shutil
                        shutil.copy2(f["path"], target)
                        wrote_copy = True
                    brainzip.write_sidecar(target, file_recs)
                    outcome = "sidecar"
                else:
                    result = brainzip.write_brain(f["path"], file_recs, dst=copy or None, state=tab_state,
                                                  backup_dir=ctx.store.backup_dir(bid), graph=drawing)
                    warnings += result.warnings
                    outcome = result.mode
                    wrote_copy = bool(copy)
                touched = True
            except (brainzip.BrainError, OSError) as e:
                outcome, reason = "failed", str(e)
            if touched:
                # saved means read back: the same notes, by id and count, as were written.
                # A half-written file can fail to parse in any way, and that is a failed save too.
                try:
                    back, _w, _info = brainzip.read_brain(target)
                except Exception as e:  # noqa: BLE001
                    back, outcome, reason = [], "failed", f"the file could not be read back ({type(e).__name__})"
                else:
                    if [r.get("id") for r in back] != [r.get("id") for r in file_recs]:
                        outcome, reason = "failed", (f"reading the brain back gave {len(back):,} of the "
                                                     f"{len(file_recs):,} notes written")
        if outcome == "failed" and copy and not existed:
            for p in (target, brainzip.sidecar_path(target) if is_csv and not side_existed else ""):
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
        copy_made = bool(copy) and wrote_copy and os.path.exists(target)
        saved = outcome in say.SAVED
        if outcome != "failed":
            _clear_save_failed(ctx.store, bid)
        ctx.store.save_records(bid, recs, "own")
        # the brain stays tied to the original: a saved copy carries the same brain id inside it,
        # so both find their answers (repointing to the copy orphaned the original's answers).
        # What the original holds is only what was verified in it; otherwise what it held before.
        known = (ctx.store.file(bid) or {}).get("tab_state") or "local"
        held = ("sidecar" if is_csv else tab_state) if saved and not copy else known
        ctx.store.upsert_file(bid, f["path"], "csv" if is_csv else "xlsx", "own", held,
                              file_fp(ctx.a, f["path"]), ctx.a.detection.get("archetype", ""))
        ctx.store.prune_backups(bid)
        for j in ctx.a.joins:
            if j["cross_file"] and (j["from_file"] == f["path"] or j["to_file"] == f["path"]):
                other = j["to_file"] if j["from_file"] == f["path"] else j["from_file"]
                ob = next((x["brain_id"] for x in ctx.files if x["path"] == other), "")
                ctx.store.save_link(bid, j["from_col"], ob, j["to_col"], os.path.basename(other),
                                    j["rows_matched"], j["band"])
        if saved:
            ctx.store.log(bid, "save", f"{os.path.basename(target)} {len(back)} records, read back")
        elif outcome == "local":
            ctx.store.log(bid, "save-local", f"{f['name']} {len(file_recs)} records on this machine")
        else:
            ctx.store.log(bid, "save-failed", f"{f['name']}: {reason}")
        write_brain_md(ctx, f, recs)
        drawn = len((drawing or {}).get("links") or []) if getattr(result, "drawing_part", None) else 0
        card = say.done_card(os.path.basename(target) if saved else f["name"], back if saved else file_recs,
                             len(private), outcome, result=result, reason=reason, copy=copy,
                             touched=touched and not copy, copy_made=copy_made, drawing_links=drawn,
                             unsure=bool(getattr(args, "unsure", False)))
        if warnings:
            card += "\n" + "\n".join(f"Note: {w}" for w in warnings)
        if under_sync_root(target) and saved:
            card += ("\nNote: this file is in a synced folder, so "
                     + ("the brain file next to it syncs too." if is_csv else "the brain tab syncs with it."))
        cards.append(card)
        written.append({"path": target, "original": f["path"], "brain_id": bid,
                        "records": len(back) if saved else len(file_recs),
                        "result": outcome if outcome != "failed" else "not_written", "verified": saved,
                        "reason": reason})
    failed = [w for w in written if w["result"] == "not_written"]
    if failed:
        # the chosen place did not take the brain: never switch to local quietly, ask. The reply
        # goes to sb.py answer, which reads it as a choice about the save, never as an answer
        q = _save_failed_q(changed)
        retry = {"files": [os.path.abspath(p) for p in args.files], "only": args.only, "hidden": args.hidden,
                 "copy": args.copy, "said_by": args.said_by,
                 "include_business_terms": args.include_business_terms, "changed": changed,
                 "brain_ids": sorted({w["brain_id"] for w in failed})}
        for bid in sorted({w["brain_id"] for w in failed} | {ctx.files[0]["brain_id"]}):
            state = ctx.store.state(bid)
            before = (list(state.get("pending") or []) if state.get("pending") != ["_save_failed"]
                      else (state.get("save_failed") or {}).get("pending", []))   # put back once settled
            state.update({"pending": ["_save_failed"], "save_failed": dict(retry, pending=before)})
            ctx.store.save_state(bid, state)
        first = ("start: sb.py start <file>" if changed else "retry: sb.py save <file> with the same options")
        emit({"ok": False, "say": "\n\n".join(cards), "next": "ask", "ask": interview.render_ask([q]),
              "ask_text": interview.render_text([q]), "written": written,
              "then": first + "; local: sb.py save <file> --local-only; stop: nothing more to run"}, 1)
    build = {}
    if ctx.files:
        choice = answers_for(ctx, ctx.files[0]["brain_id"]).get("_build")
        if choice and (choice.get("labels") or choice.get("text")):
            build = _build_plan(choice)
    tail = '\n\nWant the map? Say "show me the map".'
    if build.get("what"):
        tail = "\n\n" + brain_mod._sentence(f"Next I'll build what you picked: {build['what']}") + tail
    emit({"say": "\n\n".join(cards) + tail, "next": "build" if build.get("what") else "graph",
          "written": written, "build": build})


def _save_failed_q(changed: bool = False) -> interview.Q:
    """The question a failed save asks. When the file changed under us, starting again
    reads the new version; otherwise the same save is tried once more."""
    first = ({"id": "start", "label": "Start again", "desc": "Read the file as it is now, then save"} if changed
             else {"id": "retry", "label": "Try again", "desc": "Save the same way once more"})
    return interview.Q("_save_failed", "Save failed", "The brain did not reach the file. What now?",
                       [first, {"id": "local", "label": "This machine only", "desc": "Nothing is added to the file"},
                        {"id": "stop", "label": "Stop here", "desc": "The brain stays on this machine"}],
                       recommend=first["id"], kind="save", priority=1, source="builtin")


def _clear_save_ask(store, bid: str):
    """The save question is settled (or the save ran directly): what was pending before comes back."""
    state = store.state(bid)
    if state.get("pending") == ["_save"]:
        state["pending"] = (state.pop("save_ask", None) or {}).get("before", [])
        store.save_state(bid, state)


def _answer_save(ctx, args, bid, state):
    """The owner's reply to the preview's question: where the brain goes. In the
    file (a visible tab; a hidden one only when asked for), this machine only, or every line first; the
    save runs from here with the flags preview and answer were given (--copy,
    --include-business-terms). It is a choice about the save, never an answer."""
    info = state.get("save_ask") or {}
    q = say.save_question()
    raw = args.text if args.text is not None else (args.json or "")
    if args.json:
        try:
            raw = json.loads(args.json)
        except json.JSONDecodeError:
            pass
    ans = interview.parse_answers([q], raw).get("_save") or {}
    pick = next((o for o in ans.get("options") or [] if o != "not_sure"), None)
    unsure = pick is None and ans.get("not_sure") and not str(ans.get("text") or "").strip() and bool(
        str(raw if isinstance(raw, str) else "").strip() or raw)
    if pick is None:          # typed words: the choice they name, if exactly one fits
        words = str(ans.get("text") or (raw if isinstance(raw, str) else "")).lower()
        named = [oid for oid, pat in (("hidden", r"\bhidden\b"), ("local", r"\b(local|this machine|machine only)\b"),
                                      ("show", r"\b(show|every line|each line|rows first)\b"),
                                      ("file", r"\b(tab|in the file|into the file)\b")) if re.search(pat, words)]
        if "hidden" in named:
            named = [n for n in named if n != "file"]
        pick = named[0] if len(named) == 1 else None
    files = info.get("files") or [os.path.abspath(p) for p in args.files]
    said_by = args.said_by or info.get("said_by") or "owner"
    business = bool(args.include_business_terms or info.get("include_business_terms"))
    if pick is None and unsure:
        pick = "local"            # not sure where it goes: nothing is added to the file for now
    if pick is None:
        emit({"ok": False, "say": "I couldn't tell where the brain should go. Pick one, or say "
                                  "\"show every line\".", "next": "ask",
              "ask": interview.render_ask([q]), "ask_text": interview.render_text([q]), "brain_id": bid})
    if pick == "show":        # every line, then the same question again
        return cmd_preview(argparse.Namespace(files=files, hidden=False, show_rows=True, only=info.get("only"),
                                              said_by=said_by, include_business_terms=business))
    for f in ctx.files:
        _clear_save_ask(ctx.store, f["brain_id"])
    return cmd_save(argparse.Namespace(files=files, only=info.get("only"), hidden=pick == "hidden",
                                       copy=args.copy, said_by=said_by, include_business_terms=business,
                                       local_only=pick == "local", unsure=bool(unsure)))


def _clear_save_failed(store, bid: str):
    """The save is settled: the questions that were pending before it come back."""
    state = store.state(bid)
    if state.get("pending") == ["_save_failed"]:
        state["pending"] = (state.pop("save_failed", None) or {}).get("pending", [])
        store.save_state(bid, state)


def _answer_save_failed(ctx, args, bid, state):
    """The owner's reply to a failed save: try again, keep it on this machine, start
    again or stop. It is a choice about the save, so it is never kept as an answer."""
    info = state.get("save_failed") or {}
    q = _save_failed_q(info.get("changed", False))
    raw = args.text if args.text is not None else (args.json or "")
    if args.json:
        try:
            raw = json.loads(args.json)
        except json.JSONDecodeError:
            pass
    ans = interview.parse_answers([q], raw).get("_save_failed") or {}
    pick = next((o for o in ans.get("options") or [] if o != "not_sure"), None)
    if pick is None:          # typed words: the choice they name, if exactly one fits
        words = str(ans.get("text") or (raw if isinstance(raw, str) else "")).lower()
        named = [oid for oid, pat in ((q.options[0]["id"], r"\b(retry|again|start)\b"),
                                      ("local", r"\b(local|this machine|machine only)\b"),
                                      ("stop", r"\b(stop|done|leave it)\b")) if re.search(pat, words)]
        pick = named[0] if len(named) == 1 else None
    if pick is None:
        emit({"ok": False, "say": "I couldn't tell which you meant. Pick one of the three.", "next": "ask",
              "ask": interview.render_ask([q]), "ask_text": interview.render_text([q]), "brain_id": bid})
    for b in set(info.get("brain_ids") or []) | {bid}:
        _clear_save_failed(ctx.store, b)
    if pick == "stop":
        emit({"say": "Stopped here. The brain is kept on this machine.", "next": "done", "brain_id": bid})
    if pick == "start":
        emit({"say": "Starting again, so I read the file as it is now.", "next": "start", "brain_id": bid})
    again = argparse.Namespace(files=info.get("files") or args.files, only=info.get("only"),
                               hidden=bool(info.get("hidden")), copy=info.get("copy"),
                               said_by=info.get("said_by") or "owner",
                               include_business_terms=bool(info.get("include_business_terms")),
                               local_only=pick == "local")
    return cmd_save(again)


def cmd_graph(args):
    ctx = Ctx(args.files, expand=False)       # the map of THIS file (a saved copy included), not the session's
    f = ctx.f()
    # the tab holds the notes; the dots (vendors, hotels, links) are rebuilt from the data. This
    # machine's copy has both; for a file from someone else, compose them fresh around its notes
    recs = [r for r in ctx.store.records(f["brain_id"]) if r.get("_travel", "file") == "file"]
    if not any(r.get("kind") in ("thing", "formula_block") for r in recs):
        recs, _ = compose_for(ctx, f, tab_state="visible", said_by=args.said_by if hasattr(args, "said_by") else "owner")
        recs = [r for r in recs if r.get("_travel", "file") == "file"]
    from sheetbrain import graph as graph_mod
    g = graph_mod.build(recs, title=os.path.basename(f["path"]), playbook=ctx.a.playbook,
                        private_count=len(ctx.store.private(f["brain_id"])),
                        owners={"owner", getattr(args, "said_by", None) or "owner"})
    out = args.out or os.path.join(ctx.store.cache_dir(f["brain_id"]), "graph.html")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(g, fh, indent=1, default=str)
    try:
        from sheetbrain import render
        path = render.render(g, out)
    except ImportError:
        path = out.rsplit(".", 1)[0] + ".json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(g, fh, default=str)
    # sb never starts another program: the user opens the page (a local file with no network access)
    emit({"say": f"The map: {path}\nOpen it in a browser. It is a local page that needs no internet.",
          "next": "done", "path": path, "nodes": len(g["nodes"]), "links": len(g["links"])})


def cmd_check(args):
    ctx = Ctx(args.files, expand=False)
    lines = []
    reports = []
    for f in ctx.files:
        if not f["has_brain"]:
            lines.append(f"{f['name']} has no brain yet.")
            continue
        rep = fresh.check(ctx.a, f["path"], f["records"])
        lines.append(rep["line"])
        reports.append({"file": f["path"], "changed": rep["changed"], "rows_delta": rep["rows_delta"],
                        "new_values": rep["new_values"], "renamed": rep["renamed"],
                        "added_cols": rep["added_cols"], "removed_cols": rep["removed_cols"],
                        "outdated": [r["id"] for r in rep["outdated"]],
                        "aged": [r["id"] for r in rep["aged"]], "refreshed": rep["refreshed"]})
    nxt = "review" if any(r["outdated"] or r["aged"] for r in reports) else "answer_user"
    emit({"say": "\n".join(lines), "next": nxt, "reports": reports})


def cmd_review(args):
    ctx = Ctx(args.files)
    f = ctx.f()
    if not f["has_brain"]:
        fail(f"{f['name']} has no brain yet.")
    rep = fresh.check(ctx.a, f["path"], f["records"])
    qs = fresh.review_questions(rep)
    if not qs:
        emit({"say": "Nothing needs review. The brain is current.", "next": "answer_user"})
    if not (args.json or args.text):
        emit({"say": rep["line"], "next": "ask", "ask": interview.render_ask(qs),
              "ask_text": interview.render_text(qs)})
    raw = json.loads(args.json) if args.json else args.text
    parsed = interview.parse_answers(qs, raw)
    bid = f["brain_id"]
    names = privacy.entity_names(ctx.a)
    headers = privacy.vocabulary(ctx.a)
    done = []
    for q in qs:
        ans = parsed.get(q.id)
        if not ans:
            continue
        r = q.meta["record"]
        new_fp = fp_like(ctx.a, f["path"], parse_deps(r.get("depends_on", "")), r.get("data_fp", ""))
        ov = {"at": now(), "data_fp": new_fp}
        if "remove" in ans["options"]:
            ov["removed"] = True
        elif ans.get("text"):
            # the same screen as every other answer: the owner's words in their order, only the
            # private sentences taken out, and business terms kept on this machine
            kept, private, commercial = privacy.screen(ans["text"], names, headers, privacy.workbook_amounts(ctx.a))
            for sent, reason in private:
                ctx.store.add_private(bid, sent, reason)
            if kept and not brain_mod.reads_as_command(kept, headers):
                ov["text"] = kept if kept.endswith((".", "!", "?")) else kept + "."
                if commercial:
                    ov["commercial"] = True
        elif "still" not in ans["options"]:
            continue
        ctx.store.save_answer(bid, f"_status:{r['id']}", ov)
        done.append(r["id"])
    if done:
        f["records"] = apply_overrides(f["records"], answers_for(ctx, bid))
    emit({"say": f"Updated {len(done)} note{'s' if len(done) != 1 else ''}. Saving the brain again keeps "
                 "the file current.", "next": "save", "updated": done})


def cmd_read(args):
    ctx = Ctx(args.files, expand=False)
    packs = []
    for f in ctx.files:
        if not f["has_brain"]:
            recs = ctx.store.records(f["brain_id"])
            if not recs:
                packs.append(f"{f['name']} has no brain yet.")
                continue
            f["records"] = recs
        rep = fresh.check(ctx.a, f["path"], f["records"]) if ctx.a else {"line": ""}
        recs = list(f["records"])
        if f["origin"] == "own":
            # on the owner's own machine, the notes kept out of the file are part of the brain too
            have = {r.get("id") for r in recs}
            recs += [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in ctx.store.records(f["brain_id"], travel="machine") if r.get("id") not in have]
        packs.append(say.context_pack(f["name"], recs, f["info"], origin=f["origin"],
                                      check_line=rep["line"], full=bool(getattr(args, "all", False))))
    emit({"say": "\n\n".join(packs), "next": "answer_user"})


def cmd_share(args):
    ctx = Ctx(args.files, analyze=False)
    f = ctx.f()
    recs = f["records"]
    if not recs:
        fail(f"{f['name']} has no brain, so nothing extra travels with it.")
    if args.make_copy:
        out = args.out or new_file(_suffix(f["path"], "-" + args.make_copy))
        if args.make_copy == "nobrain":
            if f["path"].lower().endswith(".csv"):
                import shutil
                shutil.copy2(f["path"], out)
            else:
                brainzip.remove_brain(f["path"], dst=out)
        elif args.make_copy == "nocommercial":
            keep = brain_mod.for_tab(passable(f, [r for r in recs if r.get("class") != "commercial"]))
            brainzip.write_brain(f["path"], keep, dst=out,
                                 state=f["info"].get("state", "visible") if f["info"].get("state") in
                                 ("visible", "hidden") else "visible")
        else:
            fail("--make-copy must be nobrain or nocommercial")
        emit({"say": f"Made a copy: {out}. The original is unchanged.", "next": "done", "path": out})
    facts = sum(1 for r in recs if r.get("record") == "fact")
    conns = sum(1 for r in recs if r.get("record") in ("edge",))
    ins = sum(1 for r in recs if r.get("record") == "insight")
    links = [r for r in recs if r.get("record") == "link"]
    commercial = [r for r in recs if r.get("class") == "commercial"]
    private = ctx.store.private(f["brain_id"])
    state = f["info"].get("state", "sidecar")
    lines = [f"Before you send {f['name']}:",
             f"Travels with it ({'a CSV sidecar that only goes if you send it too' if state == 'sidecar' else f'the {state} _brain tab, readable by anyone who opens the file'}):",
             f"- {facts} facts, {conns} connections, {ins} insights"]
    if links:
        lines.append("- Mentions other files by name only: " + ", ".join(r.get("label", "") for r in links[:5])
                     + " (none of their data)")
    if commercial:
        lines.append(f"- Business-sensitive: {len(commercial)} note{'s' if len(commercial) != 1 else ''}, "
                     f"for example \"{commercial[0]['statement'][:100]}\"")
    lines.append("Stays on this machine:")
    lines.append(f"- {len(private)} private note{'s' if len(private) != 1 else ''}")
    lines.append("- The full map between your files")
    if f["info"].get("shared_strings"):
        lines.append("Note: a spreadsheet app saved this file after my last update, so old brain text can "
                     "still sit inside the file (not visible, but readable by tools). Open and save it "
                     "once in Excel before sending.")
    q = interview.Q("_share", "Share", "How do you want to send it?",
                    [{"id": "asis", "label": "Send it as is", "desc": "The file already has everything"},
                     {"id": "nobrain", "label": "A copy without the brain", "desc": "Makes a new file"},
                     {"id": "nocommercial", "label": "Copy without business notes",
                      "desc": "Keeps the brain, drops business-sensitive notes"}],
                    recommend="nocommercial" if commercial else "asis",
                    recommend_basis="Business terms stay with you" if commercial else "",
                    kind="share", source="builtin")
    emit({"say": "\n".join(lines), "next": "ask", "ask": interview.render_ask([q]),
          "ask_text": interview.render_text([q]),
          "then": "copy: sb.py share <file> --make-copy nobrain|nocommercial [--out PATH]"})


def _suffix(path: str, suf: str) -> str:
    base, ext = os.path.splitext(path)
    return f"{base}{suf}{ext}"


def cmd_remove(args):
    ctx = Ctx(args.files, analyze=False)
    f = ctx.f()
    try:
        if f["path"].lower().endswith(".csv"):
            side = brainzip.sidecar_path(f["path"])
            if not os.path.exists(side):
                fail("This CSV has no brain file next to it.")
            os.remove(side)
            emit({"say": f"Removed {os.path.basename(side)}. The CSV was never changed.", "next": "done"})
        res = brainzip.remove_brain(f["path"], backup_dir=ctx.store.backup_dir(f["brain_id"]))
    except brainzip.BrainError as e:
        fail(str(e))
    ctx.store.log(f["brain_id"], "remove", f["name"])
    emit({"say": f"Removed the brain tab from {f['name']}. A backup is on this machine. The notes stay in "
                 "the local index unless you delete them.", "next": "done", "backup": res.backup})


def cmd_describe(args):
    ctx = Ctx(args.files)
    f = ctx.f()
    bid = f["brain_id"]
    if args.json:
        try:
            data = json.loads(args.json)
        except json.JSONDecodeError as e:
            fail(f"--json is not valid JSON: {e}")
        cur = descriptions(ctx, bid)
        n = 0
        for k, v in data.items():
            if isinstance(v, str) and v.strip() and re.match(r"^.*?\.\{.*\}$", k):
                cur[k] = v.strip()[:300]
                n += 1
        ctx.store.write_json(bid, "descriptions.json", cur)
        emit({"say": f"Saved {n} column descriptions as guesses (marked unconfirmed).", "next": "save"})
    role_cols = {(r["table"], r["header"]) for r in ctx.a.detection["roles"].values()}
    have = descriptions(ctx, bid)
    units = []
    for t in ctx.a.tables:
        if ctx.a.file_of[t.tid] != f["path"]:
            continue
        cols = []
        for c in ctx.a.cols[t.tid]:
            key = f"{t.sheet}.{{{c.header}}}"
            if c.type == "empty" or (t.tid, c.header) in role_cols or key in have:
                continue
            d = c.to_dict(samples=True)
            cols.append({"key": key, **{k: d[k] for k in d if k in ("type", "semantic", "distinct", "samples",
                                                                     "top", "min", "max", "shapes")}})
        for i in range(0, len(cols), 40):
            units.append({"sheet": t.sheet, "title": t.title, "columns": cols[i:i + 40]})
    emit({"say": f"{sum(len(u['columns']) for u in units)} columns have no known meaning yet.",
          "next": "describe",
          "instructions": "For each column, write one short plain sentence saying what it most likely "
                          "holds. Base it only on the header and the sample values. Never copy a person's "
                          "name, email or phone into a description. Do the units one by one. Pass JSON "
                          "{\"Sheet.{Header}\": \"sentence\"} to sb.py describe <file> --json - on standard "
                          "input, inside a quoted heredoc, as SKILL.md shows.",
          "units": units[:25]})


def cmd_profile(args):
    ctx = Ctx(args.files)
    s = ctx.a.summary(samples=True)
    txt = json.dumps(s, default=str)
    if len(txt) > 9000:
        for t in s["tables"]:
            t["columns"] = [{k: v for k, v in c.items() if k in ("header", "type", "semantic", "distinct",
                                                                  "blank_rate")} for c in t["columns"]]
        txt = json.dumps(s, default=str)
    bid = ctx.f()["brain_id"]
    p = ctx.store.write_json(bid, "profile.json", ctx.a.summary())
    emit({"say": f"Profile of {', '.join(f['name'] for f in ctx.files)} (full version: {p}).",
          "next": "answer_user", "profile": json.loads(txt[:60000]) if len(txt) <= 60000 else {"path": p}})


def cmd_private(args):
    ctx = Ctx(args.files, analyze=False)
    f = ctx.f()
    if args.release:
        rel = ctx.store.release_private(args.release)
        if not rel:
            fail("No private note with that id.")
        ctx.store.save_answer(f["brain_id"], f"_released:{args.release}",
                              {"options": [], "labels": [], "text": rel["text"], "not_sure": False,
                               "header": "Owner note", "kind": "history", "at": now(),
                               "fact": {"kind": "history", "class": "data", "depends": [],
                                        "statement": "{answer_text}"}})
        emit({"say": "Released. It will go into the file at the next save.", "next": "save"})
    items = ctx.store.private(f["brain_id"])
    emit({"say": "\n".join(f"- [{p['id']}] {p['text']} ({p['reason']})" for p in items) or
                 "No private notes for this file.", "next": "answer_user", "private": items})


def cmd_export(args):
    from sheetbrain import export as export_mod
    ctx = Ctx(args.files)
    f = ctx.f()
    recs = f["records"] or [{k: v for k, v in r.items() if not k.startswith("_")}
                            for r in ctx.store.records(f["brain_id"])]
    if not recs:
        recs, _ = compose_for(ctx, f, tab_state="visible", said_by="owner")
    recs = passable(f, recs)
    stem = os.path.splitext(f["path"])[0]          # an export never overwrites a file it did not name itself
    if args.kind == "guide":
        out = args.out or new_file(f"{stem} - guide.md")
        text = export_mod.guide(ctx.a, f["path"], recs)
    elif args.kind == "blueprint":
        out = args.out or new_file(f"{stem} - app blueprint.md")
        text = export_mod.blueprint(ctx.a, f["path"], recs)
    else:
        out = args.out or new_file(f"{stem} - data dictionary.csv")
        export_mod.dictionary(ctx.a, f["path"], recs, out)
        text = None
    if text is not None:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    emit({"say": f"Wrote {os.path.basename(out)} next to the file. The spreadsheet itself is unchanged.",
          "next": "done", "path": out})




# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(prog="sb.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"sheet-geek {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, files=True):
        sp = sub.add_parser(name, help=help_, description=help_)
        if files:
            sp.add_argument("files", nargs="+", help="spreadsheet files (.xlsx, .xlsm, .csv)")
        sp.set_defaults(fn=fn)
        return sp

    s = add("start", cmd_start, "Study the sheet(s) with code, then return the readout and the first questions.")
    s.add_argument("--no-questions", action="store_true", help="build from what the data shows, no grill")
    s.add_argument("--regrill", action="store_true", help="ask again even if a brain exists")
    s = add("answer", cmd_answer, "Record answers to the current round; returns the next round or the suggestions.")
    s.add_argument("--json", help="structured answers: {question text, header or id: label(s)}")
    s.add_argument("--text", help='text reply like "1a 2b 3 my own words", or "ok"')
    s.add_argument("--brain-file", help="which file the answers belong to (default: the first without a brain)")
    s.add_argument("--copy", help="answering where the brain goes: write to this new file (or folder) instead of in place")
    s.add_argument("--include-business-terms", action="store_true",
                   help="answering where the brain goes: also put business terms in the file")
    s.add_argument("--said-by", default=None, help="answering where the brain goes: name shown on the owner's notes")
    add("suggest", cmd_suggest, "What the brain makes possible, ranked for the owner's goal.")
    s = add("preview", cmd_preview, "Show exactly what will go into the file and what stays on this machine.")
    s.add_argument("--hidden", action="store_true")
    s.add_argument("--show-rows", action="store_true")
    s.add_argument("--only")
    s.add_argument("--said-by", default="owner")
    s.add_argument("--include-business-terms", action="store_true")
    s = add("save", cmd_save, "Write the brain into the file (surgically, backup first) and the local index.")
    s.add_argument("--include-business-terms", action="store_true",
                   help="also put contract terms, markups and rebate rates in the file (default: this machine only)")
    s.add_argument("--hidden", action="store_true", help="hidden tab (hidden is not private)")
    s.add_argument("--local-only", action="store_true", help="add nothing to the file")
    s.add_argument("--copy", help="write to this new file (or folder, for several files) instead of in place")
    s.add_argument("--only")
    s.add_argument("--said-by", default="owner", help="name shown on the owner's notes (default: owner)")
    s = add("graph", cmd_graph, "Draw the map as a local HTML file.")
    s.add_argument("--open", action="store_true", help=argparse.SUPPRESS)   # accepted from older scripts; sb opens nothing
    s.add_argument("--out")
    s.add_argument("--json-out")
    s.add_argument("--said-by", default="owner", help="the name saved on the owner's notes, if not 'owner'")
    add("check", cmd_check, "Freshness: what changed since the brain was saved.")
    s = add("review", cmd_review, "Ask about notes that may be out of date, or record the answers.")
    s.add_argument("--json")
    s.add_argument("--text")
    s = add("read", cmd_read, "The brain as notes for an agent to load before answering questions.")
    s.add_argument("--all", action="store_true", help="every note of every section (the owner's notes are always all there)")
    s = add("share", cmd_share, "What travels if you send this file; make a copy without the brain or business notes.")
    s.add_argument("--make-copy", choices=["nobrain", "nocommercial"])
    s.add_argument("--out")
    add("remove", cmd_remove, "Remove the brain tab (the file goes back to how it was).")
    s = add("describe", cmd_describe, "List columns needing a plain-English meaning, or save model-written ones.")
    s.add_argument("--json")
    add("profile", cmd_profile, "Print the computed profile (bounded).")
    s = add("export", cmd_export, "Write a guide for the next owner, a data dictionary, or an app blueprint.")
    s.add_argument("--kind", choices=["guide", "dictionary", "blueprint"], default="guide")
    s.add_argument("--out")
    s = add("private", cmd_private, "List private notes kept on this machine, or release one into the file.")
    s.add_argument("--release")
    args = p.parse_args(argv)
    for attr in ("json", "text"):
        if getattr(args, attr, None) is not None:
            setattr(args, attr, arg_text(getattr(args, attr)))
    try:
        args.fn(args)
    except brainzip.BrainError as e:
        fail(str(e))
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        fail(f"Something went wrong inside sb ({type(e).__name__}: {e}). A backup is taken before any file is changed in place.", 2)


if __name__ == "__main__":
    main()
