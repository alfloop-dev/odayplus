"""Failure modes of the pinned data-platform foundation client."""

from __future__ import annotations


class ContractClientError(RuntimeError):
    """Base class for every foundation-client failure."""


class PinError(ContractClientError):
    """`config/oday_data_contracts.toml` is missing, malformed, or incomplete."""


class ArtifactDigestError(ContractClientError):
    """A vendored release artifact does not match the checksum recorded in the pin."""


class IncompatibleContractError(ContractClientError):
    """The released foundation package no longer satisfies the pin.

    Raised when the release identity moved, when a pinned kernel or internal
    contract disappeared, changed version, or changed schema content, or when
    the producer declared a breaking change the consumer has not accepted.
    """


class GeneratedClientStaleError(ContractClientError):
    """The checked-in generated models no longer match the pinned schemas."""
