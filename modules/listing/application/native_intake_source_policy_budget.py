"""Adopt a source-policy root's existing SQL deadline without extending it.

The caller owns the SERIALIZABLE tenant root and applies its original SQL
timeouts before construction. Closed native fact projections then share a
conservative monotonic remainder of that same deadline. No policy, source,
human or execution grant is issued here.
"""
from __future__ import annotations

import time
from datetime import datetime

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyConflict


class NativeSourcePolicyAdmissionBudget(NativeIntakeCommandBudget):
    def __init__(self, source, *, sql_deadline: datetime):
        if (not isinstance(sql_deadline, datetime) or sql_deadline.tzinfo is None
                or sql_deadline.utcoffset() is None):
            raise ValueError("An existing timezone-aware source-policy SQL root deadline is required")
        self.source, self.deadline = source, sql_deadline
        started = time.monotonic()
        observed = source._clock()
        remaining = (sql_deadline - observed).total_seconds()
        # This adapter accepts only the original at-most750ms admission root,
        # never a business lease/deadline supplied as a replacement root.
        if remaining > 0.750:
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_DEADLINE_NOT_ADMISSION_ROOT")
        # Observed SQL time occurs after `started`; charging its full query
        # interval makes this conservative. Neither SQL nor monotonic time is
        # reset by a later metadata lookup or guard callback.
        self._monotonic_deadline = started + remaining
        self.check_in_memory()

    def check_in_memory(self):
        try:
            super().check_in_memory()
        except NativeAssistedEntryConflict as error:
            if str(error) != "NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED":
                raise
            raise NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_DEADLINE_EXPIRED") from None
