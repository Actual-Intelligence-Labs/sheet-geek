#!/usr/bin/env python3
"""Answer runs for the pre-registered test (evals/PREREG-2026-09-26.md).

Every trial runs cold in a clean room, in its own folder holding only that
trial's workbook copies. Resumable: a trial whose record says ok is skipped.
Stops launching new trials on a usage-limit message and exits 3, so a later
launch picks up where it stopped.

    /usr/bin/python3 run_answers.py prep     # build trial folders, verify them
    /usr/bin/python3 run_answers.py run      # run all trials not yet ok
    /usr/bin/python3 run_answers.py status
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
B = json.load(open(os.path.join(R, "businesses.json")))
MODELS = {"haiku": ("claude", "claude-haiku-4-5"), "sonnet": ("claude", "claude-sonnet-5"),
          "opus": ("claude", "claude-opus-5-5"), "luna": ("codex", "gpt-5.6-luna")}
REPS = [1, 2]
CONDS = ["none", "brain"]
TIMEOUT = 1200
CODEX = "/Users/zachkellman/.local/bin/codex"
CLAUDE = "/Users/zachkellman/.local/bin/claude"
SANDBOX = '{"sandbox":{"enabled":true,"autoAllowBashIfSandboxed":true}}'
HOME = os.path.expanduser("~")
# Protected: this session's scratch (keys, briefs, brains, other trials), Claude's shared temp,
# the repo, the vault, personal folders, both CLIs' homes (transcripts, history), judge roots
PROTECTED = ["/private/tmp/claude-501", "/tmp/claude-501", "/private/tmp/claude/", "/tmp/claude/",
             "/private/tmp/sbj-", "/tmp/sbj-"] + [
    f"{HOME}/{d}" for d in ("lab", "ai-os", "agent-os", "AIL", "Documents", "Desktop", "Downloads",
                            ".claude", ".codex", ".agents", ".claude.json")]
TMP_FILES = ("py", "txt", "md", "json", "csv", "xlsx", "xlsm", "xls", "tsv", "sh", "log")
DENY = (["//private/tmp/claude-501/**", "//tmp/claude-501/**", "//private/tmp/sbj-*/**"]
        + [f"/{HOME}/{d}/**" for d in ("lab", "ai-os", "agent-os", "AIL", "Documents", "Desktop", "Downloads",
                                       ".claude", ".codex", ".agents")]
        + [f"//{t}/*.{e}" for t in ("private/tmp", "tmp") for e in TMP_FILES])
_V4 = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "deny_v4.json")))
# CLI folders left by earlier (pilot) trials: named one by one, since a new trial's own
# folder has the same shape and must stay readable
_OLD_SLUGS = sorted(
    [f"/{HOME}/.claude/projects/{n}/**" for n in os.listdir(f"{HOME}/.claude/projects") if n.startswith("-private-tmp-sb")]
    + [f"//private/tmp/claude-501/{n}/**" for n in os.listdir("/private/tmp/claude-501") if n.startswith("-private-tmp-sb")])
# shell commands never wait for an approval nobody can give; the OS sandbox still
# enforces every read and write rule, and nothing may run outside it
TRIAL_SETTINGS = json.dumps({"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                                         "allowUnsandboxedCommands": False},
                             "permissions": {"allow": ["Bash"],
                                             "deny": [f"Read({d})" for d in _V4["read"] + _OLD_SLUGS]
                                             + [f"Edit({d})" for d in _V4["edit"]]}})
ISOLATION = "v5.1"
CODEX_OFF = ["--disable", "plugins", "--disable", "apps"]
ALLOWED = ("/usr/bin/", "/bin/", "/usr/lib/", "/usr/sbin/", "/usr/local/", "/dev/", "/System/",
           "/Library/Developer/", f"{HOME}/Library/Python/", f"{HOME}/.cache/codex-runtimes")
_ROOTS = r"(/private/tmp|/tmp|/Users|/private|" + re.escape(HOME) + r")"
_SH = r"(?:^|[\s;&|(`])(?:find|rg|fd|du|tree|ls|mdfind)\s+(?:-\S+\s+)*"
_PY = r"(?:os\.walk|os\.listdir|os\.scandir|glob\.glob|Path)\(\s*[rbf]?[\"']"
SEARCH_ROOT = re.compile(_SH + r"/(?=[\s\"')]|$)|" + _PY + r"/[\"']|\bgrep\b[^\n]*\s-\w*[rR]\w*\b[^\n]*\s/(?=\s|$)", re.M)
BROAD = re.compile(_SH + _ROOTS + r"/?(?=[\s\"')]|$)|" + _PY + _ROOTS + r"/?[\"']|\bgrep\b[^\n]*\s-\w*[rR]\w*\b[^\n]*\s"
                   + _ROOTS + r"/?(?=\s|$)", re.M)
PATH_RE = re.compile(r"(?<![\w.~$-])(/(?:[\w.@+-]+/?)+)")
LIMIT = re.compile(r"hit your (session|usage|weekly|daily) limit|usage limit|limit\s*·\s*resets|rate.limit", re.I)
stop = threading.Event()
lock = threading.Lock()


def message(b: str) -> str:
    v = B[b]
    files = v["files"]
    lead = (f"I just got {'these files' if len(files) > 1 else 'this file'} from a colleague: "
            f"{' and '.join(files)}.")
    qs = " ".join(f"({i + 1}) {p['ask']}" for i, p in enumerate(v["plain"]))
    return (f"{lead} {v['open']}\nAlso: {qs}\nPython 3 with openpyxl is at /usr/bin/python3 if you want to open "
            f"the files. Work numbers out from the files; don't guess. Answer in plain words, under 500 words total.")


def units():
    out = [(b, m, r) for b in B for m in MODELS for r in REPS]
    rng = random.Random("prereg-2026-09-26-order")
    rng.shuffle(out)
    trials = []
    for b, m, r in out:
        conds = list(CONDS)
        rng.shuffle(conds)
        trials += [(b, m, r, c) for c in conds]
    return trials


def troot(b, m, r, c, attempt):
    """Each trial runs in its own root with nothing else under it: an AI that
    searches the folders above its own finds only its own files."""
    import hashlib
    h = hashlib.sha256(f"{b}|{m}|{r}|{c}|{attempt}|sbt|{ISOLATION}".encode()).hexdigest()[:12]
    return f"/private/tmp/sbt-{h}"


def outside(text, root, kind="codex", isolation=None):
    """What a trial could have seen from outside its own root: protected places
    (for Claude, reads there are blocked by the OS, so only other trials' roots,
    stale /tmp files and broad searches count), other trials' roots, stale files
    at the top of /tmp, and searches of the whole disk or of a shared root."""
    hits = set()
    text = re.sub(r"(^|[\s\"'(])~(?=/|\s|$)", lambda m: m.group(1) + HOME, text or "")   # shell ~ only
    text = text.replace("${HOME}", HOME).replace("$HOME", HOME)
    if SEARCH_ROOT.search(text):
        hits.add("/ (a search of the whole disk)")
    if re.search(r"\b(mdfind|locate|mdls)\b", text):
        hits.add("(Spotlight or locate search)")
    for m in BROAD.finditer(text):
        where = next((g for g in m.groups() if g), "")
        if kind == "codex" or where.rstrip("/") in ("/private/tmp", "/tmp", "/private"):
            hits.add(f"{where} (a search of a shared folder)")
    if "../../" in text:
        hits.add("../../ (above the trial folder)")
    own = (root, root.replace("/private", "", 1))
    slug = root.replace("/", "-") + "-work"                  # the CLI's own folders for this trial
    blocked = kind == "claude" and (isolation or ISOLATION) in ("v5", "v5.1")   # OS read denial in force
    for m in PATH_RE.finditer(text):
        p = m.group(1).rstrip("/.,;:)'\"")
        if not p or p == "/" or p.startswith(own) or p.startswith(ALLOWED):
            continue
        if re.match(r"^(/private)?/tmp/sb[tj]-", p):
            hits.add(p)                                   # another trial's or a judge's root
            continue
        m2 = re.match(r"^(?:/private/tmp/claude-501|/tmp/claude-501|" + re.escape(HOME) + r"/\.claude/projects)/([^/]+)", p)
        if m2 and m2.group(1).startswith("-private-tmp-sb") and m2.group(1) != slug:
            hits.add(p)                                   # another trial's CLI folder
            continue
        if kind == "claude" and re.match(r"^(/private)?/tmp/claude-501/[^/]+\.[A-Za-z0-9]+$", p) and os.path.exists(p):
            if not blocked:
                hits.add(p)                               # a loose file in Claude's shared temp
            continue
        if re.match(r"^(/private)?/tmp/claude/", p):
            if not (kind == "claude" and (isolation or ISOLATION) == "v5.1"):
                hits.add(p)                               # Claude's second shared temp, readable before v5.1
            continue
        if re.match(r"^(/private)?/tmp/[^/]+\.(" + "|".join(TMP_FILES) + r")$", p) and os.path.exists(p):
            if not blocked:
                hits.add(p)                               # a stale file at the top of /tmp
            continue
        if kind == "codex" and p.startswith(tuple(PROTECTED)):
            hits.add(p)
    return sorted(hits)


def rec_path(b, m, r, c):
    return os.path.join(R, "trials", b, m, f"r{r}", f"{c}.json")


def sources(b, c):
    if c == "none":
        return [os.path.join(R, "src", b, f) for f in B[b]["files"]]
    brains = json.load(open(os.path.join(R, "brains.json")))
    return [os.path.join(brains[b]["dir"], f) for f in B[b]["files"]]


def fresh_folder(root, b, c):
    if os.path.exists(root):
        shutil.rmtree(root)
    d = os.path.join(root, "work")
    os.makedirs(d)
    os.makedirs(os.path.join(root, "tmp"))
    for s in sources(b, c):
        shutil.copy2(s, os.path.join(d, os.path.basename(s)))
    return d


def clean_env(root):
    return {"HOME": HOME, "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("USER", ""),
            "PATH": f"/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:{HOME}/.local/bin", "SHELL": "/bin/zsh",
            "LANG": "en_US.UTF-8", "TMPDIR": os.path.join(root, "tmp")}


def sheet_values(path):
    import warnings
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True)
    out = {}
    for ws in wb.worksheets:
        if ws.title == "_brain":
            continue
        out[ws.title] = [tuple(row) for row in ws.iter_rows(values_only=True)]
    wb.close()
    return out


def has_brain(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
    return 'name="_brain"' in wb


def prep():
    """Check every brain file holds exactly the source data plus a _brain tab."""
    brains = json.load(open(os.path.join(R, "brains.json")))
    problems = []
    for b in [x for x in B if x in brains]:
        for f in B[b]["files"]:
            src = os.path.join(R, "src", b, f)
            bra = os.path.join(brains[b]["dir"], f)
            if has_brain(src):
                problems.append(f"{b}/{f}: source has a _brain tab")
            if not has_brain(bra):
                problems.append(f"{b}/{f}: brain copy has no _brain tab")
            if sheet_values(src) != sheet_values(bra):
                problems.append(f"{b}/{f}: data differs between source and brain copy")
        print(f"{b}: checked {len(B[b]['files'])} file(s)")
    print(json.dumps(problems, indent=1) if problems else "all brain copies: same data as the source, plus _brain")
    return not problems


def run_claude(model, d, msg, log_path, root):
    cmd = [CLAUDE, "-p", msg, "--model", model, "--setting-sources", "local", "--strict-mcp-config",
           "--no-session-persistence", "--settings", TRIAL_SETTINGS, "--output-format", "stream-json", "--verbose"]
    env = clean_env(root)
    with open(log_path, "w") as log:
        p = subprocess.Popen(cmd, cwd=d, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL)
        try:
            p.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return {"status": "timeout"}
    result = None
    touched = saw = False
    reach = set()
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                result = ev
            elif ev.get("type") == "assistant":
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use":
                        reach.update(outside(_raw(c.get("input")), root, "claude"))
                        if "_brain" in json.dumps(c.get("input")):
                            touched = True
            elif ev.get("type") == "user":
                for c in (ev.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        reach.update(outside(_raw(c.get("content")), root, "claude"))
                        if "_brain" in json.dumps(c.get("content")):
                            saw = True
    if result is None:
        return {"status": "error", "detail": "no result event"}
    text = result.get("result") or ""
    if result.get("is_error") or not text.strip():
        return {"status": "limit" if LIMIT.search(text) else "error", "detail": text[:500]}
    if reach:
        return {"status": "leak", "outside_paths": sorted(reach)[:40], "answer_discarded": text}
    return {"status": "ok", "answer": text, "cost_usd": result.get("total_cost_usd"),
            "turns": result.get("num_turns"), "duration_ms": result.get("duration_ms"),
            "models_used": list((result.get("modelUsage") or {}).keys()), "read_brain_tab": touched,
            "saw_brain_tab": saw}


def run_codex(model, d, msg, log_path, root):
    out = log_path.replace(".log", ".md")
    if os.path.exists(out):
        os.remove(out)
    cmd = [CODEX, "exec", "--skip-git-repo-check", "-s", "read-only", "-m", model, *CODEX_OFF,
           "-c", 'model_reasoning_effort="medium"', "-C", d, "-o", out, msg]
    with open(log_path, "w") as log:
        p = subprocess.Popen(cmd, cwd=d, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                             env=clean_env(root), stdin=subprocess.DEVNULL)
        t0 = time.time()
        try:
            p.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            return {"status": "timeout"}
    logtext = open(log_path, encoding="utf-8", errors="replace").read()
    text = open(out, encoding="utf-8", errors="replace").read() if os.path.exists(out) else ""
    if not text.strip():
        return {"status": "limit" if LIMIT.search(logtext) else "error", "detail": logtext[-800:]}
    after = logtext.split("\ncodex\n", 1)[-1]          # what the model did, after the echoed prompt
    reach = outside(after, root, "codex")
    if reach:
        return {"status": "leak", "outside_paths": reach[:40], "answer_discarded": text}
    return {"status": "ok", "answer": text, "duration_ms": int((time.time() - t0) * 1000),
            "read_brain_tab": "_brain" in logtext}


def one(trial):
    b, m, r, c = trial
    rp = rec_path(b, m, r, c)
    if os.path.exists(rp):
        prev = json.load(open(rp))
        if prev.get("status") == "ok":
            return prev
        tries = prev.get("tries", 0)
        if tries >= 2:                  # re-run once; a second failure drops the pair (reported)
            return prev
    else:
        tries = 0
    if stop.is_set():
        return None
    root = troot(b, m, r, c, tries + 1)
    d = fresh_folder(root, b, c)
    kind, model = MODELS[m]
    msg = message(b)
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    log_path = os.path.join(os.path.dirname(rp), f"{c}.try{tries + 1}.log")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    out = (run_claude(model, d, msg, log_path, root) if kind == "claude"
           else run_codex(model, d, msg, log_path, root))
    shutil.rmtree(root, ignore_errors=True)       # nothing of a finished trial stays findable
    rec = {"business": b, "set": B[b]["set"], "model": m, "model_id": model, "rep": r, "cond": c,
           "isolation": ISOLATION, "root": root, "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
           "tries": tries + (0 if out["status"] == "limit" else 1), "message": msg, **out}
    with open(rp + ".tmp", "w") as fh:
        json.dump(rec, fh, indent=1)
    os.replace(rp + ".tmp", rp)
    with lock:
        print(f"{rec['finished']} {b:9} {m:6} r{r} {c:5} {out['status']}"
              + (f" {len(out.get('answer', ''))}ch" if out["status"] == "ok" else f" {out.get('detail', '')[:120]!r}"),
              flush=True)
    if out["status"] == "limit":
        stop.set()
    return rec


def run(only=None, models=None):
    """Five Claude and three Codex workers; never two trials of one business at
    once, so no trial can meet a file another trial of its business left behind."""
    pending = [t for t in units() if (not only or t[0] in only) and (not models or t[1] in models)]
    busy = set()
    guard = threading.Lock()

    def worker(kind):
        while not stop.is_set():
            pick = None
            with guard:
                mine = [t for t in pending if MODELS[t[1]][0] == kind]
                if not mine:
                    return
                pick = next((t for t in mine if t[0] not in busy), None)
                if pick:
                    pending.remove(pick)
                    busy.add(pick[0])
            if pick is None:
                time.sleep(3)
                continue
            try:
                one(pick)
            finally:
                with guard:
                    busy.discard(pick[0])

    with ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(worker, "claude") for _ in range(5)] + [ex.submit(worker, "codex") for _ in range(3)]
        for f in futs:
            f.result()
    status()
    if stop.is_set():
        print("stopped on a usage limit; launch again after it resets")
        sys.exit(3)


def _raw(x):
    """Tool input or result as plain text with real newlines."""
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return "\n".join(_raw(v) for v in x.values())
    if isinstance(x, list):
        return "\n".join(_raw(v) for v in x)
    return "" if x is None else str(x)


def scan_log(kind, log_path, root, isolation=None):
    text = open(log_path, encoding="utf-8", errors="replace").read()
    if kind == "codex":
        return outside(text.split("\ncodex\n", 1)[-1], root, "codex")
    reach = set()
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                reach.update(outside(_raw(c.get("input")), root, "claude", isolation))
            elif isinstance(c, dict) and c.get("type") == "tool_result":
                reach.update(outside(_raw(c.get("content")), root, "claude", isolation))
    return sorted(reach)


def rescan():
    """Apply the current detector to every trial flagged as reaching outside."""
    changed = 0
    for t in units():
        rp = rec_path(*t)
        if not os.path.exists(rp):
            continue
        rec = json.load(open(rp))
        if rec.get("status") != "leak":
            continue
        log_path = os.path.join(os.path.dirname(rp), f"{t[3]}.try{rec['tries']}.log")
        reach = scan_log(MODELS[t[1]][0], log_path, rec["root"], rec.get("isolation"))
        if reach:
            rec["outside_paths"] = reach[:40]
            print("still outside:", t, reach[:3])
        else:
            rec["status"] = "ok"
            rec["answer"] = rec.pop("answer_discarded")
            rec["rescanned"] = "cleared: the first scan flagged only non-paths or Codex's own runtime"
            rec.pop("outside_paths", None)
            changed += 1
            print("cleared:", t)
        with open(rp + ".tmp", "w") as fh:
            json.dump(rec, fh, indent=1)
        os.replace(rp + ".tmp", rp)
    print("cleared", changed)


def status():
    counts = {}
    for t in units():
        rp = rec_path(*t)
        s = json.load(open(rp)).get("status") if os.path.exists(rp) else "todo"
        counts[s] = counts.get(s, 0) + 1
    print("status:", counts, flush=True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    if what == "prep":
        sys.exit(0 if prep() else 1)
    elif what == "rescan":
        rescan()
    elif what == "run":
        args = sys.argv[2:]
        models = {a.split("=", 1)[1] for a in args if a.startswith("model=")} or None
        run({a for a in args if "=" not in a} or None, models)
    else:
        status()
