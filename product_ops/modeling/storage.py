"""Compatibility import for the product-owned model-ready storage interfaces.

New code must import :mod:`models.model_ready.storage`. This wrapper is not a
runtime authority and may be removed after callers finish migrating.
"""

from models.model_ready.storage import *  # noqa: F403
