"""Saving one settings screen must not erase the others.

Home Assistant replaces the *whole* options dict with whatever an options step
hands back. A step that returns only its own fields therefore deletes every
other section, silently - nothing errors, the settings are simply gone.

That is exactly what happened at the primary site: saving the tuning screen wiped
the three solar forecast sensors, and it was put down to the reboot that
happened to follow. So this is checked structurally, by reading the source,
because it must hold for steps nobody has written yet.
"""
from __future__ import annotations

import ast
import pathlib

SOURCE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components/battery_management/config_flow.py"
)


def options_flow() -> ast.ClassDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("OptionsFlow")
    )


def steps() -> list[ast.AsyncFunctionDef]:
    return [
        node
        for node in options_flow().body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.startswith("async_step_")
        and node.name != "async_step_init"
    ]


def creates(step: ast.AsyncFunctionDef) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(step)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "async_create_entry"
    ]


def test_every_step_is_found():
    """A guard on the guard: an empty list would pass everything below."""
    names = {step.name for step in steps()}

    assert "async_step_tuning" in names
    assert len(names) >= 5


def test_no_step_hands_back_its_own_input_as_the_whole_options():
    """The bug itself: `data=user_input` throws away every other section."""
    for step in steps():
        for call in creates(step):
            for keyword in call.keywords:
                if keyword.arg != "data":
                    continue
                assert not (
                    isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "user_input"
                ), f"{step.name} would erase the other settings"


def test_what_each_step_saves_was_merged_with_what_is_stored():
    """Either through the shared helper, or from a name built out of it."""
    merged_names = {"merged"}
    for step in steps():
        for call in creates(step):
            for keyword in call.keywords:
                if keyword.arg != "data":
                    continue
                value = keyword.value
                # `self._merged(...)` folds this step in; `dict(self._entry.
                # options)` passes everything through untouched, which the
                # unit-modes step does because its answers go to entry.data
                built_here = isinstance(value, ast.Call) and (
                    getattr(value.func, "attr", "") == "_merged"
                    or getattr(value.func, "id", "") == "dict"
                )
                from_merge = isinstance(value, ast.Name) and value.id in merged_names
                assert built_here or from_merge, (
                    f"{step.name} saves something that never saw the stored options"
                )
