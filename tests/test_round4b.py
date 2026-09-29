"""Practice round 4b: what to build first is a request for this session, not knowledge
about the data, so it stays in this machine's store and never travels in the file."""
import os
import sys

import pytest

xlsxwriter = pytest.importorskip("xlsxwriter")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "skills", "sheet-geek", "scripts"))
import synth  # noqa: E402
from sheetbrain.analyze import Analysis  # noqa: E402
from sheetbrain.brain import Composer  # noqa: E402


def test_the_build_pick_never_travels_in_the_file(tmp_path):
    book = tmp_path / "b.xlsx"
    synth.build(book, 1, "odd_group")
    a = Analysis([str(book)])
    answers = {"_build": {"options": [], "labels": ["Sales picture"], "text": "", "kind": "build",
                          "header": "Build", "prompt": "What should I build for you first?"}}
    recs = Composer(a, str(book), "b1", answers).compose()
    build = [r for r in recs if r.get("id") == "f:_build"]
    assert build and all(r.get("_travel") == "machine" for r in build)
