"""Pure cursor deadline controls; these doubles prove no SQL authority."""
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from modules.listing.application.native_intake_assisted_entry_policy import (
    NativeAssistedEntryConflict,
)
from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
from modules.listing.application.native_intake_requester_authority import (
    _ReadConnection,
    _ReadCursor,
)


class RecordingClock:
    def __init__(self):
        self.boundaries = []

    def _clock(self):
        return datetime.now(UTC)

    def _admission_budget(self, deadline):
        self.boundaries.append(deadline)


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append("execute")

    def fetchone(self):
        self.calls.append("fetchone")
        return ("DATA_OWNER",)

    def fetchall(self):
        self.calls.append("fetchall")
        return [("DATA_OWNER",)]


@pytest.mark.parametrize("method", ["fetchone", "fetchall"])
def test_expiry_after_real_query_boundary_cannot_return_even_buffered_identity_rows(method):
    source, cursor = RecordingClock(), RecordingCursor()
    before = NativeIntakeCommandBudget(source)
    original = before.deadline
    reader = _ReadCursor(cursor, before)
    reader.execute("SELECT role FROM identity.account_roles WHERE account_id = %s", ("synthetic-account",))
    assert cursor.calls == ["execute"] and source.boundaries == [original]
    before._monotonic_deadline = time.monotonic() - 1
    with pytest.raises(NativeAssistedEntryConflict, match="^NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED$"):
        getattr(reader, method)()
    assert cursor.calls == ["execute"] and source.boundaries == [original] and before.deadline == original


def test_expired_original_root_cannot_open_a_local_cursor_or_change_either_deadline():
    class Connection:
        @contextmanager
        def cursor(self):
            pytest.fail("Expired root reached local cursor construction")
            yield

    source = RecordingClock()
    before = NativeIntakeCommandBudget(source)
    original = before.deadline
    before._monotonic_deadline = time.monotonic() - 1
    with pytest.raises(NativeAssistedEntryConflict, match="^NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED$"):
        with _ReadConnection(Connection(), before).cursor():
            pytest.fail("Expired root yielded a cursor")
    assert before.deadline == original and source.boundaries == []


def test_arbitrary_callback_checks_are_preserved_even_when_it_exposes_a_memory_method():
    class AdditionalChecks:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls == 2:
                raise PermissionError("Synthetic current authority lost")

        def check_in_memory(self):
            pytest.fail("Unknown callback was replaced by a narrower check")

    cursor, before = RecordingCursor(), AdditionalChecks()
    reader = _ReadCursor(cursor, before)
    reader.execute("SELECT role FROM identity.account_roles WHERE account_id = %s", ("synthetic-account",))
    with pytest.raises(PermissionError, match="^Synthetic current authority lost$"):
        reader.fetchone()
    assert cursor.calls == ["execute"] and before.calls == 2
