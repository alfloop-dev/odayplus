#!/usr/bin/env python3
"""Verification script for ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001.

Validates that:
1. build_release_handoff correctly accepts and validates approved masked data snapshots.
2. build_release_handoff correctly accepts and validates previous release manifests for rollback.
3. Canonical manifest digests are recomputed and matched across Schema v2 constraints.
4. Missing snapshot, missing rollback, unmasked snapshot, or forged rollback manifests fail closed.
5. Workflow contracts in deploy-dev.yml wire all required parameters to build_release_handoff.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.build_release_handoff import main as handoff_main
from delivery_toolchain.release.release_manifest import (
    compute_data_contract_digest,
    compute_manifest_digest,
    compute_migration_digest,
    compute_source_policy_digest,
    validate_manifest,
    validate_release_admission,
)

SAMPLE_SHA_CURRENT = 'a' * 40
SAMPLE_SHA_PREV = 'b' * 40


def test_positive_flow_with_snapshot_file_and_rollback_manifest(tmp_path: Path) -> None:
    print('[1/5] Testing positive flow with snapshot file & rollback manifest...')
    data_snapshot = {
        'id': 'snap-approved-20260901-001',
        'uri': 'gs://odayplus-snapshots/masked/snap-approved-20260901-001.tar.gz',
        'content_sha256': 'sha256:' + '1' * 64,
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
        'masked': True,
    }
    snap_file = tmp_path / 'approved_snapshot.json'
    snap_file.write_text(json.dumps(data_snapshot, indent=2), encoding='utf-8')

    prev_manifest = {
        'schema_version': 2,
        'release_id': f'odp-{SAMPLE_SHA_PREV[:12]}',
        'candidate_sha': SAMPLE_SHA_PREV,
        'components': {
            'api': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '2' * 64},
            'web': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + '3' * 64},
            'worker': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + '4' * 64},
            'scheduler': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + '5' * 64},
            'migration': {
                'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + '4' * 64,
                'shares_image_with': 'worker',
            },
        },
        'migration_digest': compute_migration_digest(root=ROOT),
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
        'source_policy_digest': compute_source_policy_digest(root=ROOT),
        'external_sources_expected_enabled': [],
        'sbom_refs': ['asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '6' * 64],
        'signature_refs': ['asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '7' * 64],
        'created_at': '2026-08-25T12:00:00+00:00',
        'created_by_workflow': f'github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@{SAMPLE_SHA_PREV}',
        'data_snapshot': {
            'id': 'snap-prev-001',
            'uri': 'gs://odayplus-snapshots/masked/snap-prev-001.tar.gz',
            'content_sha256': 'sha256:' + '8' * 64,
            'data_contract_digest': compute_data_contract_digest(root=ROOT),
            'masked': True,
        },
        'rollback_release': {
            'release_id': 'odp-legacy-000',
            'candidate_sha': '0' * 40,
            'manifest_digest': 'sha256:' + '9' * 64,
            'components': {
                'api': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '2' * 64},
                'web': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + '3' * 64},
                'worker': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + '4' * 64},
                'scheduler': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + '5' * 64},
            },
            'data_snapshot': {
                'id': 'snap-prev-000',
                'uri': 'gs://odayplus-snapshots/masked/snap-prev-000.tar.gz',
                'content_sha256': 'sha256:' + '8' * 64,
                'data_contract_digest': compute_data_contract_digest(root=ROOT),
                'masked': True,
            },
        },
        'release_status': 'ready',
    }
    prev_manifest['manifest_digest'] = compute_manifest_digest(prev_manifest)
    prev_manifest_path = tmp_path / 'PREV_RELEASE_MANIFEST.json'
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images.json'
    manifest_out = tmp_path / 'manifest.json'

    argv = [
        '--release-sha', SAMPLE_SHA_CURRENT,
        '--created-at', '2026-08-26T12:00:00+00:00',
        '--component', 'api=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'a' * 64,
        '--component', 'web=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + 'b' * 64,
        '--component', 'worker=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + 'c' * 64,
        '--component', 'scheduler=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + 'd' * 64,
        '--sbom-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'e' * 64,
        '--signature-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'f' * 64,
        '--data-snapshot-file', str(snap_file),
        '--rollback-manifest', str(prev_manifest_path),
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    exit_code = handoff_main(argv)
    assert exit_code == 0, 'Expected handoff_main to return 0'
    assert images_out.exists(), 'images_out should exist'
    assert manifest_out.exists(), 'manifest_out should exist'

    manifest = json.loads(manifest_out.read_text(encoding='utf-8'))
    assert manifest['candidate_sha'] == SAMPLE_SHA_CURRENT
    assert manifest['data_snapshot']['id'] == 'snap-approved-20260901-001'
    assert manifest['data_snapshot']['masked'] is True
    assert manifest['rollback_release']['candidate_sha'] == SAMPLE_SHA_PREV
    assert manifest['rollback_release']['manifest_digest'] == prev_manifest['manifest_digest']
    assert validate_manifest(manifest) == []
    assert validate_release_admission(manifest) == []
    print('  -> PASSED: Schema v2 manifest produced with exact snapshot and rollback bindings.')


def test_missing_snapshot_fails_closed(tmp_path: Path) -> None:
    print('[2/5] Testing fail-closed when data snapshot is missing...')
    images_out = tmp_path / 'images_fail.json'
    manifest_out = tmp_path / 'manifest_fail.json'
    argv = [
        '--release-sha', SAMPLE_SHA_CURRENT,
        '--created-at', '2026-08-26T12:00:00+00:00',
        '--component', 'api=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'a' * 64,
        '--component', 'web=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + 'b' * 64,
        '--component', 'worker=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + 'c' * 64,
        '--component', 'scheduler=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + 'd' * 64,
        '--sbom-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'e' * 64,
        '--signature-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'f' * 64,
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    exit_code = handoff_main(argv)
    assert exit_code != 0, 'Missing snapshot must fail closed'
    assert not manifest_out.exists(), 'Manifest must not be written on failure'
    print('  -> PASSED: Missing snapshot rejected.')


def test_unmasked_snapshot_fails_closed(tmp_path: Path) -> None:
    print('[3/5] Testing fail-closed when data snapshot is unmasked...')
    images_out = tmp_path / 'images_fail_unmasked.json'
    manifest_out = tmp_path / 'manifest_fail_unmasked.json'
    argv = [
        '--release-sha', SAMPLE_SHA_CURRENT,
        '--created-at', '2026-08-26T12:00:00+00:00',
        '--component', 'api=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'a' * 64,
        '--component', 'web=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + 'b' * 64,
        '--component', 'worker=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + 'c' * 64,
        '--component', 'scheduler=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + 'd' * 64,
        '--sbom-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'e' * 64,
        '--signature-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'f' * 64,
        '--data-snapshot-id', 'snap-unmasked-001',
        '--data-snapshot-uri', 'gs://odayplus-snapshots/unmasked/snap.tar.gz',
        '--data-snapshot-content-sha256', 'sha256:' + '1' * 64,
        '--data-snapshot-unmasked',
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    exit_code = handoff_main(argv)
    assert exit_code != 0, 'Unmasked snapshot must fail closed'
    assert not manifest_out.exists(), 'Manifest must not be written on failure'
    print('  -> PASSED: Unmasked snapshot rejected.')


def test_tampered_rollback_manifest_fails_closed(tmp_path: Path) -> None:
    print('[4/5] Testing fail-closed when rollback manifest digest is tampered...')
    tampered_manifest = {
        'schema_version': 2,
        'release_id': f'odp-{SAMPLE_SHA_PREV[:12]}',
        'candidate_sha': SAMPLE_SHA_PREV,
        'components': {
            'api': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '2' * 64},
            'web': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + '3' * 64},
            'worker': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + '4' * 64},
            'scheduler': {'image': 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + '5' * 64},
        },
        'migration_digest': compute_migration_digest(root=ROOT),
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
        'source_policy_digest': compute_source_policy_digest(root=ROOT),
        'external_sources_expected_enabled': [],
        'sbom_refs': ['asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '6' * 64],
        'signature_refs': ['asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + '7' * 64],
        'created_at': '2026-08-25T12:00:00+00:00',
        'created_by_workflow': f'github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@{SAMPLE_SHA_PREV}',
        'data_snapshot': {
            'id': 'snap-prev-001',
            'uri': 'gs://odayplus-snapshots/masked/snap-prev-001.tar.gz',
            'content_sha256': 'sha256:' + '8' * 64,
            'data_contract_digest': compute_data_contract_digest(root=ROOT),
            'masked': True,
        },
        'release_status': 'ready',
        'manifest_digest': 'sha256:' + '0' * 64,
    }
    tampered_file = tmp_path / 'TAMPERED_MANIFEST.json'
    tampered_file.write_text(json.dumps(tampered_manifest, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images_fail_tampered.json'
    manifest_out = tmp_path / 'manifest_fail_tampered.json'
    argv = [
        '--release-sha', SAMPLE_SHA_CURRENT,
        '--created-at', '2026-08-26T12:00:00+00:00',
        '--component', 'api=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'a' * 64,
        '--component', 'web=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + 'b' * 64,
        '--component', 'worker=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + 'c' * 64,
        '--component', 'scheduler=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + 'd' * 64,
        '--sbom-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'e' * 64,
        '--signature-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'f' * 64,
        '--data-snapshot-id', 'snap-approved-001',
        '--data-snapshot-uri', 'gs://odayplus-snapshots/masked/snap-approved-001.tar.gz',
        '--data-snapshot-content-sha256', 'sha256:' + '1' * 64,
        '--rollback-manifest', str(tampered_file),
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    exit_code = handoff_main(argv)
    assert exit_code != 0, 'Tampered rollback manifest must fail closed'
    assert not manifest_out.exists(), 'Manifest must not be written on failure'
    print('  -> PASSED: Tampered rollback manifest rejected.')


def test_workflow_contract() -> None:
    print('[5/5] Testing deploy-dev.yml workflow contracts...')
    workflow_path = ROOT / '.github/workflows/deploy-dev.yml'
    content = workflow_path.read_text(encoding='utf-8')

    assert 'data_snapshot_id:' in content
    assert 'data_snapshot_uri:' in content
    assert 'data_snapshot_content_sha:' in content
    assert 'data_snapshot_file:' in content
    assert 'rollback_manifest:' in content
    assert 'DATA_SNAPSHOT_FILE:' in content
    assert 'DATA_SNAPSHOT_ID:' in content
    assert 'DATA_SNAPSHOT_URI:' in content
    assert 'DATA_SNAPSHOT_CONTENT_SHA:' in content
    assert 'ROLLBACK_MANIFEST:' in content
    assert '--data-snapshot-file' in content
    assert '--rollback-manifest' in content
    print('  -> PASSED: Workflow contract verified.')


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        test_positive_flow_with_snapshot_file_and_rollback_manifest(tmp_path)
        test_missing_snapshot_fails_closed(tmp_path)
        test_unmasked_snapshot_fails_closed(tmp_path)
        test_tampered_rollback_manifest_fails_closed(tmp_path)
        test_workflow_contract()
    print("\nALL VERIFICATIONS PASSED SUCCESSFULLY (EXIT=0).")
    return 0


if __name__ == '__main__':
    sys.exit(main())
