#!/usr/bin/env python3
"""Verify every name the extracted modules expect to be injected actually exists.

`dispatch_engine`, `worker_lifecycle`, `worker_failure_policy` and
`worker_workspace` do not import most of what they call. Each `@_entrypoint`
wrapper runs `_sync_supervisor_scope()` first, which copies supervisor's module
namespace into the module's own globals, so those names only resolve at CALL
time. To stop the linter objecting, all four carry a blanket

    # ruff: noqa: F821

which switches off undefined-name checking for roughly 7,100 lines -- the four
largest files in the package. A typo, or a helper that supervisor stops
exporting, therefore surfaces as a NameError in a live supervisor loop rather
than at lint or import time.

This test restores that check. It resolves each module's free names statically
and asserts supervisor can supply them, so the failure moves back to CI.

It is deliberately NOT a fix for the injection itself. Untangling 364 injected
names across 168 entrypoints is a real refactor; this only rebuilds the safety
net that suppressing F821 removed.
"""
from __future__ import annotations

import ast
import builtins
import importlib
import re
import sys
import unittest
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import supervisor

# Every module that relies on `_sync_supervisor_scope` instead of imports.
INJECTED_MODULES = (
    "dispatch_engine",
    "worker_lifecycle",
    "worker_failure_policy",
    "worker_workspace",
)


def _bound_names(tree: ast.Module) -> set[str]:
    """Names the module defines for itself, so they need no injection.

    Deliberately over-collects rather than under-collects: a name wrongly treated
    as bound only weakens the check, while one wrongly treated as free would fail
    the build for something that was never broken.
    """
    bound: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            # Covers assignments, walrus targets, for/with targets, comprehension
            # targets and tuple unpacking -- all are Name nodes in a Store context.
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


def _free_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = _bound_names(tree)
    used = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return used - bound


class InjectedScopeIntegrityTests(unittest.TestCase):
    def test_every_injected_name_exists_on_supervisor(self) -> None:
        for module in INJECTED_MODULES:
            with self.subTest(module=module):
                free = _free_names(THIS_DIR / f"{module}.py")
                missing = sorted(name for name in free if not hasattr(supervisor, name))
                self.assertEqual(
                    missing,
                    [],
                    f"{module}.py calls {missing} but supervisor does not define them. "
                    "`_sync_supervisor_scope` only copies what supervisor has, and "
                    "`# ruff: noqa: F821` means nothing else will catch this -- it "
                    "would surface as a NameError inside a live supervisor loop.",
                )

    def test_every_injected_name_survives_the_module_own_sync_rule(self) -> None:
        """`hasattr(supervisor, name)` is necessary but NOT sufficient.

        Each module filters what it copies. Asking only whether supervisor defines
        a name misses one that supervisor has and the module's own rule discards --
        which is exactly what happened to `_reset_queue_record_for_redispatch`:
        `process_queue` called it, supervisor defined it, and `worker_lifecycle`
        skipped every `_`-prefixed key, so the call site was a NameError on the
        Antigravity pool-fallback path. Run each module's real sync and ask the
        module, not supervisor.
        """
        for module_name in INJECTED_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                module._sync_supervisor_scope()
                missing = sorted(
                    name
                    for name in _free_names(THIS_DIR / f"{module_name}.py")
                    if not hasattr(module, name)
                )
                self.assertEqual(
                    missing,
                    [],
                    f"{module_name}.py calls {missing}, which survive neither its own "
                    "definitions nor what its `_sync_supervisor_scope` copies in. "
                    "Every call would raise NameError.",
                )

    def test_the_modules_agree_on_what_sync_skips(self) -> None:
        """One rule, or static analysis of this package cannot be trusted.

        Two of the four used to skip every `_`-prefixed key and two only `__`, so
        whether a single-underscore helper resolved depended on which file asked.
        """
        rules = {}
        for module_name in INJECTED_MODULES:
            source = (THIS_DIR / f"{module_name}.py").read_text(encoding="utf-8")
            matches = re.findall(r'key\.startswith\("(_+)"\)', source)
            self.assertEqual(len(matches), 1, f"{module_name}: expected one sync prefix rule")
            rules[module_name] = matches[0]
        self.assertEqual(
            len(set(rules.values())),
            1,
            f"the sync rules have diverged again: {rules}",
        )

    def test_the_modules_actually_depend_on_injection(self) -> None:
        """Guard the premise: if a module stops needing injection, drop its noqa."""
        for module in INJECTED_MODULES:
            with self.subTest(module=module):
                self.assertTrue(
                    _free_names(THIS_DIR / f"{module}.py"),
                    f"{module}.py no longer has free names, so it no longer needs "
                    "`_sync_supervisor_scope` or its blanket `# ruff: noqa: F821`.",
                )

    def test_analyser_treats_locally_bound_names_as_bound(self) -> None:
        """A false positive here would fail the build for working code."""
        import tempfile

        source = '''
import os
CONST = 1
def outer(arg, *args, **kwargs):
    local = arg
    for item in args:
        with open(item) as handle:
            [x for x in handle]
        (walrus := item)
    try:
        pass
    except OSError as exc:
        del exc
    lam = lambda inner: inner
    return os, CONST, local, kwargs, walrus, lam
class Thing:
    attr = CONST
'''
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.py"
            p.write_text(source, encoding="utf-8")
            self.assertEqual(_free_names(p), set())

    def test_analyser_reports_a_genuinely_free_name(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.py"
            p.write_text("def f():\n    return injected_helper()\n", encoding="utf-8")
            self.assertEqual(_free_names(p), {"injected_helper"})


if __name__ == "__main__":
    unittest.main()
