#!/usr/bin/env python3
"""Read-only runtime/archive evidence collector for the authorized correction."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/home/lupin/odayplus')
OUT = Path(__file__).resolve().parent
RUNTIME = Path('/home/lupin/oday-plus-supervisor-runtime-current').resolve()
os.environ.update({
    'PANTHEON_STATUS_ROOT': str(ROOT),
    'ORCH_STATUS_ROOT': str(ROOT),
    'ORCH_CONFIG_PATH': str(ROOT / '.orchestrator/config.json'),
    'PANTHEON_CONFIG_PATH': str(ROOT / '.orchestrator/config.json'),
})
sys.path[:0] = [str(RUNTIME / '.orchestrator'), str(RUNTIME / 'scripts')]
import supervisor
import task_archive


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect():
    board = json.loads((ROOT / 'ai-status.json').read_text())
    task_map = {t['id']: t for t in board['tasks']}
    resolver = task_archive.TaskResolver(board['tasks'])
    plan = json.loads((OUT / 'plan.json').read_text())
    checkpoint_path = ROOT / '.orchestrator/recovery-receipts/history-recovery-checkpoint-20260908T235531Z.json'
    checkpoint = json.loads(checkpoint_path.read_text())
    evidence_paths = [
        checkpoint_path,
        Path(checkpoint['batch_path']),
        Path(checkpoint['maintenance_hold']['path']),
        ROOT / 'ai-task-archive/index.json',
    ]
    archive_files = sorted((ROOT / 'ai-task-archive/tasks').glob('*.json'))
    downstream = {
        'ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001': 'ODP-STRUCTURAL-REMEDIATION-CLOSEOUT-001',
        'ODP-MODELREADY-QUALITY-NULLABLE-001': 'ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001',
    }
    targets = []
    for row in plan['targets']:
        task_id = row['task_id']
        original = task_archive.load_archived_snapshot(task_id)
        effective = resolver.get(task_id)
        target = downstream[task_id]
        dependent = task_map[target]
        targets.append({
            'task_id': task_id,
            'original_status': original['task']['status'],
            'snapshot_sha256': digest(task_archive.archive_task_path(task_id)),
            'effective_status': effective['status'],
            'dependency_satisfied': resolver.dependency_satisfied(task_id),
            'correction': effective.get('correction'),
            'downstream_task_id': target,
            'downstream_status': dependent['status'],
            'downstream_declares_target_dependency': task_id in dependent.get('depends_on', []),
            'downstream_admission_by_runtime': supervisor.dependencies_satisfied(dependent, resolver, {'done'}),
        })
    reconstructed = []
    for path in archive_files:
        snapshot = json.loads(path.read_text())
        task = snapshot.get('task', {})
        if task.get('history_recovery', {}).get('reconstructed') is True:
            task_id = snapshot['task_id']
            reconstructed.append({
                'task_id': task_id,
                'snapshot_sha256': digest(path),
                'effective_status': resolver.get(task_id)['status'],
                'dependency_satisfied': resolver.dependency_satisfied(task_id),
            })
    placeholders = []
    for task_id in checkpoint['board_placeholders_intended']:
        task = task_map.get(task_id)
        placeholders.append({
            'task_id': task_id,
            'source': resolver.source(task_id),
            'status': task.get('status') if task else None,
            'non_dispatchable': task.get('non_dispatchable') if task else None,
            'waiting_for': task.get('waiting_for') if task else None,
        })
    return {
        'observed_at': datetime.now(UTC).isoformat(),
        'observed_by': 'Codex',
        'runtime_path': str(RUNTIME),
        'runtime_sha': subprocess.check_output(['git', '-C', str(RUNTIME), 'rev-parse', 'HEAD'], text=True).strip(),
        'runtime_module_paths': {'supervisor': supervisor.__file__, 'task_archive': task_archive.__file__},
        'targets': targets,
        'original_archive_sha256': {str(f.relative_to(ROOT)): digest(f) for f in archive_files},
        'immutable_evidence_sha256': {str(f): digest(f) for f in evidence_paths},
        'reconstructed': reconstructed,
        'placeholders': placeholders,
        'named_gate_readback': {
            task_id: {
                'source': resolver.source(task_id),
                **{key: (resolver.get(task_id) or {}).get(key) for key in (
                    'status', 'terminal_outcome', 'waiting_for', 'review_reopen_count'
                )},
            }
            for task_id in ['ODP-DRIFT-SECURITY-VERIFY-003', 'ODP-AVM-QUALITY-NULLABLE-001']
        },
    }


if __name__ == '__main__':
    result = collect()
    output = OUT / sys.argv[1]
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'receipt': str(output), 'runtime_sha': result['runtime_sha'], 'targets': result['targets']}, ensure_ascii=False))
