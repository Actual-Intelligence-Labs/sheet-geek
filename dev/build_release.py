"""Build the release files in dist/:

  sheet-geek.zip                    the skill folder as claude.ai and ChatGPT's skill upload expect it
                                    (SKILL.md at the top of the folder, no caches, no dev files)
  sheet-geek-openai-<version>.zip   the plugin for OpenAI's plugin directory (ChatGPT and Codex):
                                    one top folder with a portable plugin.json, the icon and the
                                    skill; no hooks and no .claude-plugin, which that upload refuses
  claude-plugin/                    the tree for Anthropic's plugin directory: this repository
                                    without demo/, dev/, evals/ and tests/, so a directory install
                                    (or the README's marketplace#release install) carries only what
                                    runs. It is committed as the `release` branch, which the
                                    directory listing follows.

Usage: python dev/build_release.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "sheet-geek")
DIST = os.path.join(ROOT, "dist")
REPO_URL = "https://github.com/Actual-Intelligence-Labs/sheet-geek"
SITE = "https://actualintelligencelabs.ai/sheet-geek"
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
DEV_ONLY = ("demo/", "dev/", "evals/", "tests/")     # left out of the Claude directory tree
ZIP_TIME = (2026, 1, 1, 0, 0, 0)                     # fixed, so the same source gives the same zip

LONG_DESCRIPTION = (
    "Sheet Geek gives a spreadsheet a brain: notes about what the data means, saved with the file.\n\n"
    "It reads every row of an .xlsx, .xlsm or .csv file with code, tells you in a few lines what the sheet "
    "is and what stands out, and asks only the handful of questions the data cannot answer: your goal, what a "
    "number really means, what to leave out. It saves what it learned as a _brain tab in the workbook (in "
    "ChatGPT, in a copy you download; for a CSV, in a .brain.json file next to it): what each column means, "
    "how tabs and files connect, what you said and what code counted, each note labeled and dated. An AI that "
    "opens the file later and reads its tabs can start from those notes. It can also draw a clickable map of "
    "how the data connects, flag notes that went stale when the data changed, and write a data dictionary, a "
    "guide for the next owner or an app blueprint.\n\n"
    "For anyone whose spreadsheet other people, or their AI tools, need to understand: finance models, "
    "purchasing logs, client trackers, inventories.\n\n"
    "Limits: it never changes your data cells. Notes kept on \"this machine only\" stay in a .sheet-geek "
    "folder on your computer; in ChatGPT they last only as long as the chat. Password-protected files and old "
    ".xls files are not supported, and it is not for health records, payment card data, government ID numbers "
    "or passwords. The code makes no network calls, never starts another program and never searches the web. "
    "Each brain's first note ends with one line naming Sheet Geek and Actual Intelligence Labs.\n\n"
    "Open source (Apache-2.0) by Actual Intelligence Labs."
)


def version() -> str:
    return json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8"))["version"]


def check_frontmatter() -> None:
    text = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        sys.exit("SKILL.md has no frontmatter")
    keys = {line.split(":", 1)[0].strip() for line in m.group(1).splitlines() if ":" in line and not line.startswith(" ")}
    extra = keys - ALLOWED_KEYS
    if extra:
        sys.exit(f"SKILL.md frontmatter has keys claude.ai rejects: {sorted(extra)}")
    desc = re.search(r"^description:\s*(.*)$", m.group(1), re.M)
    if desc and len(desc.group(1)) > 1024:
        sys.exit("description is over 1,024 characters")
    if chr(0x2014) in text:
        sys.exit("SKILL.md contains an em dash")


def skill_files():
    """(path on disk, path inside the skill folder) for every file that ships."""
    for base, dirs, files in os.walk(SKILL):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))   # no caches
        for f in sorted(files):
            if f.endswith((".pyc", ".DS_Store")) or f.startswith("."):
                continue
            p = os.path.join(base, f)
            yield p, os.path.relpath(p, SKILL)


def _add(z: zipfile.ZipFile, arc: str, data: bytes) -> None:
    info = zipfile.ZipInfo(arc, ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16      # a regular file, rw-r--r--
    z.writestr(info, data)


def build() -> str:
    check_frontmatter()
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "sheet-geek.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p, rel in skill_files():
            _add(z, f"sheet-geek/{rel}", open(p, "rb").read())
            n += 1
    print(f"{out} ({n} files, {os.path.getsize(out) // 1024} KB)")
    return out


def openai_manifest() -> dict:
    claude = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8"))
    interface = {
        "displayName": "Sheet Geek",
        "shortDescription": "Give any spreadsheet a brain",
        "longDescription": LONG_DESCRIPTION,
        "developerName": "Actual Intelligence Labs",
        "category": "Data & Analytics",
        "capabilities": ["Reads spreadsheets", "Asks a few questions", "Saves notes in the file or a copy",
                         "Draws a data map"],
        "websiteURL": SITE,
        "supportURL": f"{SITE}#support",
        "privacyPolicyURL": f"{SITE}/privacy",
        "termsOfServiceURL": f"{SITE}/terms",
        "defaultPrompt": ["Give this spreadsheet a brain.",
                          "What does each column in this workbook mean?",
                          "Make a data dictionary for this sheet."],
        "brandColor": "#FF7DB5",
        "composerIcon": "./assets/icon.png",
        "logo": "./assets/logo.png",
    }
    for key in ("displayName", "shortDescription"):
        if len(interface[key]) > 30:
            sys.exit(f"{key} is over 30 characters")
    if len(interface["longDescription"]) > 4000 or any(len(p) > 128 for p in interface["defaultPrompt"]):
        sys.exit("listing text is over OpenAI's limits")
    blurb = re.search(r'short_description: "(.*)"', open(os.path.join(SKILL, "agents", "openai.yaml"),
                                                          encoding="utf-8").read())
    if not blurb or not 25 <= len(blurb.group(1)) <= 64:
        sys.exit("agents/openai.yaml short_description must be 25 to 64 characters")
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": claude["name"],
        "version": claude["version"],
        "description": claude["description"],
        "author": claude["author"],
        "homepage": claude["homepage"],
        "repository": claude["repository"],
        "license": claude["license"],
        "keywords": claude["keywords"],
        "extensions": {"com.openai": {
            "interface": interface,
            "publication": {"release_notes": f"Version {claude['version']}."},
        }},
    }


def build_openai() -> str:
    check_frontmatter()
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, f"sheet-geek-openai-{version()}.zip")
    icon = open(os.path.join(ROOT, "assets", "icon.png"), "rb").read()
    n = 0
    with zipfile.ZipFile(out, "w") as z:
        _add(z, "sheet-geek/plugin.json", (json.dumps(openai_manifest(), indent=2) + "\n").encode())
        _add(z, "sheet-geek/assets/icon.png", icon)
        _add(z, "sheet-geek/assets/logo.png", icon)
        for name in ("LICENSE", "NOTICE"):
            _add(z, f"sheet-geek/{name}", open(os.path.join(ROOT, name), "rb").read())
        n += 5
        for p, rel in skill_files():
            _add(z, f"sheet-geek/skills/sheet-geek/{rel}", open(p, "rb").read())
            n += 1
    print(f"{out} ({n} files, {os.path.getsize(out) // 1024} KB)")
    return out


def _release_readme(text: str) -> str:
    """Links into folders the release leaves out point at the main branch instead."""
    def absolute(m):
        target = m.group(2)
        kind = "tree" if target.endswith("/") else "blob"
        return f"{m.group(1)}({REPO_URL}/{kind}/main/{target})"
    text = re.sub(r"(\[[^\]]*\])\(((?:demo|dev|evals|tests)/[^)]*)\)", absolute, text)
    return re.sub(r"(## Development\n).*?(?=\n## )",
                  rf"\1\nTests, demos and studies live on the main branch: {REPO_URL}#development\n", text, flags=re.S)


def build_claude_tree() -> str:
    out = os.path.join(DIST, "claude-plugin")
    shutil.rmtree(out, ignore_errors=True)
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    n = 0
    for rel in sorted(filter(None, tracked.decode().split("\0"))):
        if rel.startswith(DEV_ONLY) or not os.path.exists(os.path.join(ROOT, rel)):
            continue
        dest = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if rel == "README.md":
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(_release_readme(open(os.path.join(ROOT, rel), encoding="utf-8").read()))
        else:
            shutil.copy2(os.path.join(ROOT, rel), dest)
        n += 1
    print(f"{out} ({n} files)")
    return out


if __name__ == "__main__":
    build()
    build_openai()
    build_claude_tree()
