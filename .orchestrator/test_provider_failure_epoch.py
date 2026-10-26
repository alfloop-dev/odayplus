import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_state
import supervisor


@pytest.mark.parametrize('new_time', ['2099-09-11T01:00:00.900000Z', '2099-09-11T01:00:01.900000Z', '2099-09-11T01:00:00.100000Z', '2099-09-11T00:59:59.900000Z'])
@pytest.mark.parametrize('legacy_initial', [False, True])
def test_stale_live_pause_cannot_replace_new_failure_before_clear(tmp_path, new_time, legacy_initial):
    config = {
        'paths': {'state_file': str(tmp_path / 'state.json'), 'event_queue': str(tmp_path / 'queue.jsonl')},
        'providers': {'codex': {'delivery_mode': 'codex', 'quota_group': 'codex'}},
    }
    Path(config['paths']['event_queue']).write_text('')
    original = runtime_state.default_state()

    def fail(state, run_id, at):
        instant = datetime.fromisoformat(at.replace('Z', '+00:00'))
        class FrozenClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant
        with (
            mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture-auth'),
            mock.patch.object(supervisor, 'datetime', FrozenClock),
            mock.patch.object(supervisor, 'utc_now', return_value=instant.replace(microsecond=0).isoformat().replace('+00:00', 'Z')),
            mock.patch.object(supervisor, 'write_activity_log'),
            mock.patch.object(supervisor.model_rotation, 'rotation_enabled', return_value=False),
        ):
            supervisor.mark_provider_dispatch_paused(
                config, state, 'codex', 'usage limit reached', worker_run_id=run_id,
                failure_kind='quota_terminal',
                worker={'run_id': run_id, 'logical_agent_id': 'codex', 'provider': 'codex', 'auth_identity_hash': 'fixture-auth'},
            )

    fail(original, 'old-run', '2099-09-11T01:00:00.100000Z')
    if legacy_initial:
        original['provider_guardrails']['dispatch_pauses']['codex'].pop('failure_epoch', None)
    runtime_state.save_runtime_state(config, original)
    stale_writer = runtime_state.load_runtime_state(config)
    new_writer = runtime_state.load_runtime_state(config)
    fail(new_writer, 'new-run', new_time)
    runtime_state.save_runtime_state(config, new_writer)
    assert runtime_state.load_runtime_state(config)['provider_guardrails']['dispatch_pauses']['codex']['worker_run_id'] == 'new-run'

    # A writer which only loaded the old failure now performs an unrelated save.
    runtime_state.save_runtime_state(config, stale_writer)
    after_stale_save = runtime_state.load_runtime_state(config)
    observed_run = after_stale_save['provider_guardrails']['dispatch_pauses']['codex']['worker_run_id']

    # The operator still has the old failure snapshot and clears only that incident.
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture-auth'),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:01:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
    ):
        supervisor.clear_provider_dispatch_pause(config, original, 'codex')
    runtime_state.save_runtime_state(config, original)
    after_clear = runtime_state.load_runtime_state(config)
    assert (
        observed_run,
        after_clear['provider_guardrails']['dispatch_pauses'].get('codex', {}).get('worker_run_id'),
    ) == ('new-run', 'new-run')

    # Clearing the actual latest incident retires its known legacy predecessor
    # too; an unrelated save from that predecessor must not recreate the pause.
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture-auth'),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:02:00Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
    ):
        assert supervisor.clear_provider_dispatch_pause(config, after_clear, 'codex')
    runtime_state.save_runtime_state(config, after_clear)
    runtime_state.save_runtime_state(config, stale_writer)
    assert 'codex' not in runtime_state.load_runtime_state(config)['provider_guardrails']['dispatch_pauses']


@pytest.mark.parametrize('new_time', [
    pytest.param('2099-09-11T01:00:02.900000Z', id='forward-clock-control'),
    pytest.param('2099-09-11T00:59:59.900000Z', id='clock-back-after-formal-clear'),
    pytest.param('2099-09-11T01:00:00.100000Z', id='identical-time-after-clear'),
])
def test_new_failure_after_clear_survives_retained_tombstone(tmp_path, new_time):
    config = {
        'paths': {'state_file': str(tmp_path / 'state.json'), 'event_queue': str(tmp_path / 'queue.jsonl')},
        'providers': {'codex': {'delivery_mode': 'codex', 'quota_group': 'codex'}},
    }
    Path(config['paths']['event_queue']).write_text('')

    def fail(state, run, at):
        instant = datetime.fromisoformat(at.replace('Z', '+00:00'))
        class FrozenClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant
        with (
            mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture-auth'),
            mock.patch.object(supervisor, 'datetime', FrozenClock),
            mock.patch.object(supervisor, 'write_activity_log'),
            mock.patch.object(supervisor.model_rotation, 'rotation_enabled', return_value=False),
        ):
            assert supervisor.mark_provider_dispatch_paused(
                config, state, 'codex', 'usage limit reached', worker_run_id=run,
                failure_kind='quota_terminal',
                worker={'run_id': run, 'logical_agent_id': 'codex', 'provider': 'codex', 'auth_identity_hash': 'fixture-auth'},
            )

    state = runtime_state.default_state()
    fail(state, 'old-run', '2099-09-11T01:00:00.100000Z')
    old_epoch = state['provider_guardrails']['dispatch_pauses']['codex']['failure_epoch']
    runtime_state.save_runtime_state(config, state)
    stale_failure = deepcopy(state)
    with (
        mock.patch.object(supervisor, 'provider_auth_identity_hash', return_value='fixture-auth'),
        mock.patch.object(supervisor, 'utc_now', return_value='2099-09-11T01:00:01Z'),
        mock.patch.object(supervisor, 'write_activity_log'),
    ):
        assert supervisor.clear_provider_dispatch_pause(config, state, 'codex')
    runtime_state.save_runtime_state(config, state)
    state = runtime_state.load_runtime_state(config)
    assert not state['provider_guardrails']['dispatch_pauses']
    stale_clear = deepcopy(state)
    fail(state, 'new-run', new_time)
    assert state['provider_guardrails']['dispatch_pauses']['codex']['failure_epoch'] > old_epoch
    runtime_state.save_runtime_state(config, state)
    saved = runtime_state.load_runtime_state(config)
    assert saved['provider_guardrails']['dispatch_pauses'].get('codex', {}).get('worker_run_id') == 'new-run'
    # Unrelated saves of pre-clear and post-clear snapshots must preserve the
    # new incident without resurrecting the old one.
    for stale in (stale_failure, stale_clear):
        runtime_state.save_runtime_state(config, stale)
        saved = runtime_state.load_runtime_state(config)
        assert saved['provider_guardrails']['dispatch_pauses']['codex']['worker_run_id'] == 'new-run'


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('case', ['matching', 'other-auth', 'other-provider', 'runless-pause'])
def test_clock_floor_scope_and_stale_save(tmp_path, legacy, case):
    config = {'paths': {'state_file': str(tmp_path/'state.json'), 'event_queue': str(tmp_path/'queue.jsonl')}, 'providers': {'codex': {'delivery_mode': 'codex', 'quota_group': 'codex'}, 'claude': {'delivery_mode':'claude','quota_group':'claude'}}}
    (tmp_path/'queue.jsonl').write_text('')
    def fail(state, provider, auth, run, at):
        instant = datetime.fromisoformat(at.replace('Z','+00:00'))
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return instant
        with mock.patch.object(supervisor,'datetime',Clock), mock.patch.object(supervisor,'provider_auth_identity_hash',return_value=auth), mock.patch.object(supervisor,'write_activity_log'), mock.patch.object(supervisor.model_rotation,'rotation_enabled',return_value=False):
            supervisor.mark_provider_dispatch_paused(config,state,provider,'usage limit reached',worker_run_id=run,failure_kind='quota_terminal',worker={'run_id':run,'logical_agent_id':provider,'provider':provider,'auth_identity_hash':auth})
    state=runtime_state.default_state()
    fail(state,'codex','auth-a',None if case=='runless-pause' else 'old-run','2099-09-11T01:00:00.100000Z')
    if legacy:
        state['provider_guardrails']['dispatch_pauses']['codex'].pop('failure_epoch')
    runtime_state.save_runtime_state(config,state)
    old_snapshot=runtime_state.load_runtime_state(config)
    with mock.patch.object(supervisor,'provider_auth_identity_hash',return_value='auth-a'),mock.patch.object(supervisor,'utc_now',return_value='2099-09-11T01:00:01Z'),mock.patch.object(supervisor,'write_activity_log'):
        supervisor.clear_provider_dispatch_pause(config,state,'codex')
    runtime_state.save_runtime_state(config,state)
    state=runtime_state.load_runtime_state(config)
    stale_clear=deepcopy(state)
    provider='claude' if case=='other-provider' else 'codex'
    auth='auth-b' if case=='other-auth' else 'auth-a'
    fail(state,provider,auth,'new-run','2099-09-11T00:59:59.900000Z')
    epoch=state['provider_guardrails']['dispatch_pauses'][provider]['failure_epoch']
    if case in ('other-auth','other-provider'):
        assert epoch == runtime_state.provider_failure_epoch({'paused_at':'2099-09-11T00:59:59.900000Z'})
    runtime_state.save_runtime_state(config,state)
    runtime_state.save_runtime_state(config,old_snapshot)
    runtime_state.save_runtime_state(config,stale_clear)
    saved=runtime_state.load_runtime_state(config)
    assert saved['provider_guardrails']['dispatch_pauses'][provider]['worker_run_id']=='new-run'
