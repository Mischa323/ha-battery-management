"""The card's own logic, which Python cannot reach.

Nobody has yet looked at how this card renders - that is still open - but two
parts of it are arithmetic rather than appearance, and those can be pinned:
where the bars land (including the negative prices a dynamic tariff really
produces) and what picking the card out of Home Assistant's card list gives
you. Both run under Node, skipped where Node is absent.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKS = sorted((ROOT / "tests/card").glob("*.mjs"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)


def test_there_are_checks_to_run():
    """A guard on the guard: an empty glob would pass silently."""
    assert CHECKS


@pytest.mark.parametrize("check", CHECKS, ids=lambda p: p.stem)
def test_card_check(check: pathlib.Path):
    result = subprocess.run(
        ["node", str(check)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
