"""The Claude plugin directory's checker reads every file under 256 KiB that is not an image or font,
and at most 512 files; anything past that holds every version for a human reviewer
(claude.com/docs/plugins/pre-submission-checklist, "Files in the plugin folder"). 0.2.3 waited weeks in
review over analyze.py at 317 KB. These keep every file the directory installs inside the limits."""
import importlib.util
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 256 * 1024
MEDIA = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ttf", ".otf", ".woff", ".woff2")

_spec = importlib.util.spec_from_file_location("build_release", os.path.join(ROOT, "dev", "build_release.py"))
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


def shipped() -> list:
    """Every file the directory installs: tracked or new, minus what build_release leaves out."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, check=True).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return sorted(r for r in set(filter(None, out.split("\0")))
                  if not r.startswith(build_release.DEV_ONLY) and os.path.isfile(os.path.join(ROOT, r)))


def test_every_shipped_file_is_under_the_directory_limit():
    sizes = {r: os.path.getsize(os.path.join(ROOT, r)) for r in shipped() if not r.lower().endswith(MEDIA)}
    big = {r: n for r, n in sizes.items() if n >= LIMIT}
    assert not big, f"over 256 KiB, so the directory holds the version for a reviewer; split these: {big}"


def test_shipped_file_count_is_under_the_directory_limit():
    assert len(shipped()) <= 512
