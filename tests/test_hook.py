"""The Claude Code hooks never block a prompt, stay under Claude Code's
10,000-character cap without cutting a note, and never call a file a brain
when it has none."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOOK = os.path.join(ROOT, "hooks", "hook.py")
DEMO = os.path.join(ROOT, "demo", "try-it")


def hook(event, payload, env):
    p = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=120)
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"] if p.stdout.strip() else ""
    return p.returncode, ctx


@pytest.fixture()
def work(tmp_path):
    shutil.copy(os.path.join(DEMO, "Finance model - WITH brain.xlsx"), tmp_path / "finance.xlsx")
    shutil.copy(os.path.join(DEMO, "Hotel purchases - WITH brain.xlsx"), tmp_path / "hotel.xlsx")
    shutil.copy(os.path.join(DEMO, "Finance model - NO brain.xlsx"), tmp_path / "plain.xlsx")
    env = dict(os.environ, HOME=str(tmp_path / "user"), SHEET_GEEK_HOME=str(tmp_path / "index"))
    return tmp_path, env


@pytest.mark.parametrize("prompt", ["what does finance.xlsx say about churn?", "and hotel.xlsx?",
                                    "compare finance.xlsx, hotel.xlsx"])
def test_a_prompt_naming_a_file_with_a_brain_is_never_blocked(work, prompt):
    tmp, env = work
    code, ctx = hook("prompt", {"prompt": prompt, "cwd": str(tmp)}, env)
    assert code == 0
    assert len(ctx) <= 9500
    assert ctx.count("<brain-notes") == ctx.count("</brain-notes>")
    if "finance" in prompt:
        assert '<brain-notes file="finance.xlsx"' in ctx
    if prompt == "and hotel.xlsx?":            # its notes run past the cap: a pointer, not a cut note
        assert "hotel.xlsx has a brain from Sheet Geek, too long to include here" in ctx


def test_a_broken_file_fails_open(work):
    tmp, env = work
    (tmp / "broken.xlsx").write_bytes(b"PK\x03\x04 not really a workbook")
    code, ctx = hook("prompt", {"prompt": "what is in broken.xlsx?", "cwd": str(tmp)}, env)
    assert code == 0 and ctx == ""


def test_naming_a_file_without_a_brain_does_not_make_it_one(work):
    tmp, env = work
    code, ctx = hook("prompt", {"prompt": "look at plain.xlsx", "cwd": str(tmp)}, env)
    assert code == 0 and ctx == ""
    code, ctx = hook("session-start", {"cwd": str(tmp)}, env)
    assert code == 0 and "plain.xlsx" not in ctx


def test_the_skill_itself_carries_no_hook_code():
    # the hooks live in the Claude Code plugin; the skill uploaded to other platforms has none
    sb = open(os.path.join(ROOT, "skills", "sheet-geek", "scripts", "sb.py"), encoding="utf-8").read()
    assert "additionalContext" not in sb and "def _hook" not in sb
