"""Compatibility import for the product-owned model-ready contracts.

New code must import :mod:`models.model_ready.contracts`. This wrapper remains
temporarily so existing operational commands and external callers do not break
while the product/development boundary migration is in progress.
"""

from models.model_ready.contracts import *  # noqa: F403
