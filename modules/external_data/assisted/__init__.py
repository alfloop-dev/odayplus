"""Operator-assisted listing intake capabilities retained after XR-CUTOVER-001.

These capabilities operate on documents an operator submits (assisted
listing intake), not on external datasets fetched from a provider, so they are
deliberately kept outside ``modules.external_data.application``, whose
ingestion modules were decommissioned by the cutover.
"""

__all__: tuple[str, ...] = ()
