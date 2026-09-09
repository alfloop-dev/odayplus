#!/usr/bin/env python3
"""Coordinate the approved CLI operation; never write canonical data directly."""
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/home/lupin/odayplus')
OUT = Path(__file__).resolve().parent
LINK = Path('/home/lupin/oday-plus-supervisor-runtime-current')
ENV = {**os.environ, 'AI_NAME': 'Codex', 'PANTHEON_STATUS_ROOT': str(ROOT),
       'ORCH_STATUS_ROOT': str(ROOT), 'ORCH_CONFIG_PATH': str(ROOT / '.orchestrator/config.json'),
       'PANTHEON_CONFIG_PATH': str(ROOT / '.orchestrator/config.json')}


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def command(label, args, cwd=ROOT):
    started = now()
    result = subprocess.run(args, cwd=cwd, env=ENV, text=True, capture_output=True, timeout=180)
    receipt = {'started_at': started, 'finished_at': now(), 'argv': args,
               'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    save(label + '.json', receipt)
    print(label, 'exit', result.returncode, flush=True)
    result.check_returncode()
    return result.stdout


def running(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        return text[text.rfind(')') + 2:].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def active_workers():
    state = json.loads((ROOT / '.orchestrator/state.json').read_text())
    active = {'running', 'starting', 'waiting_approval', 'suspended_approval',
              'retry_backoff', 'manual_pending', 'stalled'}
    return [(key, row.get('task_id')) for key, row in state.get('workers', {}).items()
            if row.get('status') in active]


def protected_files():
    paths = [ROOT / name for name in [
        'ai-status.json', 'ai-activity-log.jsonl', 'current-work.md', 'dashboard-bundle.json',
        '.orchestrator/state.json', 'docs-site/ai-status.json', 'docs-site/ai-activity-log.jsonl',
        'docs-site/current-work.md', 'docs-site/dashboard-bundle.json', 'docs-site/orchestrator-state.json']]
    paths.extend((ROOT / 'ai-task-archive').rglob('*.json'))
    return {str(path): sha(path) for path in paths if path.is_file()}


def main():
    plan_path = OUT / 'plan.json'
    plan = json.loads(plan_path.read_text())
    rollout = json.loads((OUT / 'rollout-preflight.json').read_text())
    runtime = LINK.resolve()
    runtime_sha = subprocess.check_output(['git', '-C', str(runtime), 'rev-parse', 'HEAD'], text=True).strip()
    assert runtime_sha == rollout['target_sha'], 'runtime has not been deployed to the reviewed revision'
    assert 'archive_recovery_invalidate' in (runtime / 'scripts/ai_status.py').read_text()
    assert not active_workers(), 'workers are still active'
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        assert not any(arg.endswith(b'/scripts/ai_status.py') for arg in argv), 'another canonical CLI is active'
    pid = int((ROOT / '.orchestrator/supervisor.pid').read_text().strip())
    supervisor_argv = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
    supervisor_cwd = Path(f'/proc/{pid}/cwd').resolve()
    supervisor_scripts = [
        (supervisor_cwd / os.fsdecode(arg)).resolve()
        for arg in supervisor_argv if arg.endswith(b'/supervisor.py')
    ]
    assert runtime / '.orchestrator/supervisor.py' in supervisor_scripts, 'PID is not the deployed supervisor'
    record = {'type': 'archive_recovery_invalidation_maintenance', 'actor': 'Codex',
              'coordination_task_id': plan['coordination_task_id'], 'plan_sha256': sha(plan_path),
              'runtime_sha': runtime_sha, 'runtime_path': str(runtime), 'supervisor_pid_before': pid,
              'started_at': now(), 'status': 'starting', 'targets_applied': []}
    lock = None
    stopped = False
    try:
        os.kill(pid, signal.SIGTERM)
        stopped = True
        deadline = time.monotonic() + 25
        while running(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not running(pid), 'supervisor did not stop gracefully'
        lock = (ROOT / '.orchestrator/supervisor.lock').open('a+')
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not active_workers(), 'worker started while entering maintenance'
        record.update(status='maintenance_active', supervisor_stopped_at=now(),
                      singleton_lock_held=True, active_workers=[])
        save('maintenance-window.json', record)
        command('before-readback-command', ['python3', str(OUT / 'readback.py'), 'before-apply.json'])
        before = json.loads((OUT / 'before-apply.json').read_text())
        baseline = protected_files()
        base_args = []
        for index, target in enumerate(plan['targets'], 1):
            path = ROOT / 'ai-task-archive/tasks' / (target['task_id'] + '.json')
            assert sha(path) == target['expected_snapshot_sha256'], 'original target snapshot drifted'
            args = [str(ROOT / 'scripts/ai-status.sh'), 'archive_recovery_invalidate', target['task_id'],
                    '--coordination-task', plan['coordination_task_id'], '--reason', target['reason'],
                    '--evidence-ref', target['evidence_ref'], '--expected-sha256', target['expected_snapshot_sha256']]
            preview = json.loads(command(f'dry-run-{index}', args))
            assert preview['status'] == 'dry_run'
            assert protected_files() == baseline, 'dry run changed canonical data'
            base_args.append(args)
        record['dry_runs_preserved_canonical_files'] = True
        save('maintenance-window.json', record)
        for index, args in enumerate(base_args, 1):
            command(f'confirm-{index}', args + ['--confirm'])
            record['targets_applied'].append(plan['targets'][index - 1]['task_id'])
            save('maintenance-window.json', record)
        command('after-readback-command', ['python3', str(OUT / 'readback.py'), 'after-apply.json'])
        after = json.loads((OUT / 'after-apply.json').read_text())
        assert before['original_archive_sha256'] == after['original_archive_sha256']
        assert before['immutable_evidence_sha256'] == after['immutable_evidence_sha256']
        assert before['placeholders'] == after['placeholders']
        assert before['named_gate_readback'] == after['named_gate_readback']
        for row in after['targets']:
            assert row['original_status'] == 'done' and row['effective_status'] == 'blocked'
            assert row['dependency_satisfied'] is False and row['downstream_admission_by_runtime'] is False
            assert row['correction']['actor'] == 'Codex'
        affected = {row['task_id'] for row in plan['targets']}
        assert [row for row in before['reconstructed'] if row['task_id'] not in affected] == [
            row for row in after['reconstructed'] if row['task_id'] not in affected]
        for index, target in enumerate(plan['targets'], 1):
            command(f'canonical-show-{index}', [str(ROOT / 'scripts/ai-status.sh'), 'show', target['task_id']])
        record.update(status='applied_and_verified', verified_at=now(),
                      original_archives_unchanged=len(before['original_archive_sha256']),
                      immutable_evidence_unchanged=True, placeholders_unchanged=True,
                      named_gates_unchanged=True, unaffected_reconstructed_unchanged=True)
    except BaseException as exc:
        record.update(status='failed_or_partial', error=repr(exc))
        raise
    finally:
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        if stopped:
            try:
                command('supervisor-restart', ['bash', str(LINK / 'scripts/run-supervisor-watchdog.sh'),
                        '--config', str(ROOT / '.orchestrator/config.json'), '--restart'], cwd=LINK)
                record['restart_command_succeeded'] = True
            except BaseException as exc:
                record['restart_command_succeeded'] = False
                record['restart_error'] = repr(exc)
        record['finished_at'] = now()
        save('maintenance-window.json', record)
        print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
