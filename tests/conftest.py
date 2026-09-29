"""Shared test setup: the synthetic seeds and the noise-budget fixtures.

A test that takes `synth_seed` runs once per seed (20 by default; set SB_SEEDS
to run fewer while working). The noise-budget books hold none of the seven
development workbooks: evals/fixtures/finance_model.xlsx is a copy of one, so it
was taken out of synth.NOISE_FIXTURES (older v0.1 regression tests elsewhere
still read it; see dev/v02/batch-notes.md).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "spreadsheet-brain",
                                "scripts"))

import synth  # noqa: E402

SEEDS = list(range(int(os.environ.get("SB_SEEDS", "20"))))


def pytest_generate_tests(metafunc):
    if "synth_seed" in metafunc.fixturenames:
        metafunc.parametrize("synth_seed", SEEDS)


@pytest.fixture()
def noise_fixtures():
    """Books no new detector may ask about: each item is the file list for one build."""
    return [list(paths) for paths in synth.NOISE_FIXTURES]
