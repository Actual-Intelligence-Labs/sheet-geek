"""Build dist/sheet-geek.zip: the skill folder as claude.ai and other
apps expect it (SKILL.md at the top of the folder, no caches, no dev files).

Usage: python dev/build_release.py
"""
from __future__ import annotations

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "sheet-geek")
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


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


def build() -> str:
    check_frontmatter()
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "sheet-geek.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SKILL):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                if f.endswith((".pyc", ".DS_Store")):
                    continue
                p = os.path.join(base, f)
                arc = os.path.join("sheet-geek", os.path.relpath(p, SKILL))
                z.write(p, arc)
                n += 1
    print(f"{out} ({n} files, {os.path.getsize(out) // 1024} KB)")
    return out


if __name__ == "__main__":
    build()
