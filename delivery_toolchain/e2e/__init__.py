"""End-to-end, staging-proof, and product-release gate tooling."""

import sys

from delivery_toolchain.e2e import _support

# Gate files remain directly executable by CI while also being importable as a
# package in tests. Direct execution resolves ``_support.py`` beside the script;
# package imports resolve the same module through this alias.
sys.modules.setdefault("_support", _support)
