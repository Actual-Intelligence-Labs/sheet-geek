"""Every synthetic trap book driven through the CLI to a verified save, with two
owners: one who picks the first option everywhere (rules, readbacks, multi-tab
leave-outs) and one who is never sure. A crash anywhere in start, answer,
preview or save fails here, whichever detector asked the question. So does a
question that shows an unfilled slot ('{role:...}'), or a note that quotes the
tool's instruction to type as if the owner said it."""
import json
import os
import re
import subprocess
import sys

import pytest

pytest.importorskip("xlsxwriter")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
import synth  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "..", "skills", "sheet-geek", "scripts"))
from sheetbrain import brainzip  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
SB = os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py")


_SLOT = re.compile(r"\{(?:role|values|count|rows|sum):[a-z_]+\}")


def sb(env, *args, stdin=None):
    p = subprocess.run([sys.executable, SB, *args], input=stdin, capture_output=True, text=True, env=env,
                       timeout=300)
    assert p.stdout, p.stderr
    return json.loads(p.stdout)


def _reply(r, policy):
    n = len(re.findall(r"(?m)^\s*(\d+)\. ", r.get("ask_text") or ""))
    n = n or len(((r.get("ask") or {}).get("questions")) or []) or 1
    return "\n".join(f"{i}a" if policy == "first" else f"{i} not sure" for i in range(1, n + 1))


@pytest.mark.parametrize("policy", ["first", "not_sure"])
@pytest.mark.parametrize("name", sorted(synth.TRAPS))
def test_every_trap_book_reaches_a_verified_save(tmp_path, name, policy):
    book = tmp_path / f"{name}.xlsx"
    synth.build(book, 1, name)
    env = dict(os.environ, SPREADSHEET_BRAIN_HOME=str(tmp_path / "home"))
    r = sb(env, "start", str(book))
    assert r.get("ok") is not False, r
    for _ in range(20):
        if r.get("next") != "ask":
            break
        # every slot a question names is filled before the owner sees it ('{role:...}' never shown)
        assert not _SLOT.search(json.dumps(r.get("ask") or {})) and not _SLOT.search(r.get("ask_text") or ""), r
        r = sb(env, "answer", str(book), "--text", "-", stdin=_reply(r, policy))
        assert r.get("next") != "stop" and r.get("ok") is not False, r
    assert r.get("next") in ("preview", "save", "build"), r
    out = str(tmp_path / f"out_{name}.xlsx")
    r = sb(env, "save", str(book), "--copy", out)
    assert r["ok"] and all(w.get("verified") for w in r.get("written") or []), r
    # a note quotes what the owner picked and saw, never the tool's instruction to type
    told = [x["statement"] for x in brainzip.read_brain(out)[0] if x.get("source") == "told"]
    assert not [s for s in told if re.search(r'\("Type |\("Skip it', s)], told
