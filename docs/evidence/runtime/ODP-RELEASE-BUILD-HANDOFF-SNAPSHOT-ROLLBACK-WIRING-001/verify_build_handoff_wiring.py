#!/usr/bin/env python3
"""Verification script for ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001.

Validates that:
1. build_release_handoff correctly accepts and validates approved masked data snapshots.
2. build_release_handoff correctly accepts and validates previous release manifests for rollback.
3. Canonical manifest digests are recomputed and matched across Schema v2 constraints.
4. Missing snapshot, missing rollback, unmasked snapshot, or forged rollback manifests fail closed.
5. Two sources for one approved binding fail closed instead of one silently winning.
6. A remote rollback URI is rejected by the name it was passed, not by a pathlib-mangled one.
7. Workflow contracts in deploy-dev.yml wire all required parameters to build_release_handoff.py.

Every fail-closed check asserts *why* it failed. A check that only asserts a
non-zero exit passes for the wrong reason as soon as an unrelated binding is
missing, which is how an unmasked-snapshot case once "passed" because no
rollback manifest had been supplied at all.
"""

from __future__ import annotations

import contextlib
import io
import json
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


def run_handoff(argv: list[str]) -> tuple[int, str]:
    """Run the handoff CLI, echoing its stderr and returning it for assertions."""

    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        exit_code = handoff_main(argv)
    captured = buffer.getvalue()
    # Echo once, on stdout, so the transcript keeps the reason next to its check.
    print(captured, end='')
    return exit_code, captured


def expect_rejection(argv: list[str], manifest_out: Path, reason: str) -> None:
    """A fail-closed check is only met when the stated reason is the actual one."""

    exit_code, stderr = run_handoff(argv)
    assert exit_code != 0, 'Expected the handoff to fail closed'
    assert not manifest_out.exists(), 'Manifest must not be written on failure'
    assert reason in stderr, (
        f'Rejected, but not for the reason under test.\n'
        f'  expected reason to contain: {reason!r}\n'
        f'  actual stderr: {stderr!r}'
    )


def create_prev_manifest_file(tmp_path: Path) -> Path:
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
            'object_generation': 122,
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
                'object_generation': 121,
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
    return prev_manifest_path


def test_positive_flow_with_snapshot_file_and_rollback_manifest(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[1/12] Testing positive flow with snapshot file & rollback manifest...')
    data_snapshot = {
        'id': 'snap-approved-20260901-001',
        'uri': 'gs://odayplus-snapshots/masked/snap-approved-20260901-001.tar.gz',
        'object_generation': 123,
        'content_sha256': 'sha256:' + '1' * 64,
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
        'masked': True,
    }
    snap_file = tmp_path / 'approved_snapshot.json'
    snap_file.write_text(json.dumps(data_snapshot, indent=2), encoding='utf-8')

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
    exit_code, _stderr = run_handoff(argv)
    assert exit_code == 0, 'Expected handoff_main to return 0'
    assert images_out.exists(), 'images_out should exist'
    assert manifest_out.exists(), 'manifest_out should exist'

    manifest = json.loads(manifest_out.read_text(encoding='utf-8'))
    assert manifest['candidate_sha'] == SAMPLE_SHA_CURRENT
    assert manifest['data_snapshot']['id'] == 'snap-approved-20260901-001'
    assert manifest['data_snapshot']['masked'] is True
    assert manifest['rollback_release']['candidate_sha'] == SAMPLE_SHA_PREV
    assert validate_manifest(manifest) == []
    assert validate_release_admission(manifest) == []
    print('  -> PASSED: Schema v2 manifest produced with exact snapshot and rollback bindings.')


def test_missing_snapshot_fails_closed(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[2/12] Testing fail-closed when data snapshot is missing...')
    images_out = tmp_path / 'images_fail_missing_snap.json'
    manifest_out = tmp_path / 'manifest_fail_missing_snap.json'
    argv = [
        '--release-sha', SAMPLE_SHA_CURRENT,
        '--created-at', '2026-08-26T12:00:00+00:00',
        '--component', 'api=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'a' * 64,
        '--component', 'web=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/web@sha256:' + 'b' * 64,
        '--component', 'worker=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/worker@sha256:' + 'c' * 64,
        '--component', 'scheduler=asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/scheduler@sha256:' + 'd' * 64,
        '--sbom-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'e' * 64,
        '--signature-ref', 'asia-east1-docker.pkg.dev/odayplus/oday-plus-dev/api@sha256:' + 'f' * 64,
        '--external-source', 'listing_raw_snapshot',
        '--rollback-manifest', str(prev_manifest_path),
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    expect_rejection(argv, manifest_out, '缺少 masked data snapshot 參照')
    print('  -> PASSED: Missing snapshot rejected.')


def test_unmasked_snapshot_cli_fails_closed(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[3/12] Testing fail-closed when CLI specifies --data-snapshot-unmasked (with valid rollback manifest)...')
    images_out = tmp_path / 'images_fail_unmasked_cli.json'
    manifest_out = tmp_path / 'manifest_fail_unmasked_cli.json'
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
        '--rollback-manifest', str(prev_manifest_path),
        '--images-output', str(images_out),
        '--manifest-output', str(manifest_out),
    ]
    expect_rejection(argv, manifest_out, 'manifest.data_snapshot.masked must be True')
    print('  -> PASSED: Unmasked snapshot CLI rejected.')


def test_snapshot_file_missing_masked_fails_closed(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[4/12] Testing fail-closed when snapshot file is missing masked field (no automatic backfill)...')
    snap_no_masked = {
        'id': 'snap-nomasked-001',
        'uri': 'gs://odayplus-snapshots/masked/snap-nomasked-001.tar.gz',
        'content_sha256': 'sha256:' + '1' * 64,
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
    }
    snap_file = tmp_path / 'snapshot_no_masked.json'
    snap_file.write_text(json.dumps(snap_no_masked, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images_fail_no_masked.json'
    manifest_out = tmp_path / 'manifest_fail_no_masked.json'
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
    expect_rejection(argv, manifest_out, 'manifest.data_snapshot missing required field: masked')
    print('  -> PASSED: Snapshot file missing masked rejected.')


def test_snapshot_file_missing_contract_digest_fails_closed(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[5/12] Testing fail-closed when snapshot file is missing data_contract_digest (no automatic backfill)...')
    snap_no_digest = {
        'id': 'snap-nodigest-001',
        'uri': 'gs://odayplus-snapshots/masked/snap-nodigest-001.tar.gz',
        'content_sha256': 'sha256:' + '1' * 64,
        'masked': True,
    }
    snap_file = tmp_path / 'snapshot_no_digest.json'
    snap_file.write_text(json.dumps(snap_no_digest, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images_fail_no_digest.json'
    manifest_out = tmp_path / 'manifest_fail_no_digest.json'
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
    expect_rejection(argv, manifest_out, 'manifest.data_snapshot missing required field: data_contract_digest')
    print('  -> PASSED: Snapshot file missing data_contract_digest rejected.')


def test_snapshot_file_unmasked_fails_closed(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[6/12] Testing fail-closed when snapshot file specifies masked: false...')
    snap_unmasked = {
        'id': 'snap-unmasked-001',
        'uri': 'gs://odayplus-snapshots/unmasked/snap-unmasked-001.tar.gz',
        'content_sha256': 'sha256:' + '1' * 64,
        'data_contract_digest': compute_data_contract_digest(root=ROOT),
        'masked': False,
    }
    snap_file = tmp_path / 'snapshot_unmasked.json'
    snap_file.write_text(json.dumps(snap_unmasked, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images_fail_unmasked_file.json'
    manifest_out = tmp_path / 'manifest_fail_unmasked_file.json'
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
    expect_rejection(argv, manifest_out, 'manifest.data_snapshot.masked must be True')
    print('  -> PASSED: Snapshot file with masked: false rejected.')


def test_tampered_rollback_manifest_fails_closed(tmp_path: Path) -> None:
    print('[7/12] Testing fail-closed when rollback manifest digest is tampered...')
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
    expect_rejection(argv, manifest_out, 'manifest.manifest_digest does not match its canonical immutable payload')
    print('  -> PASSED: Tampered rollback manifest rejected.')


def _base_argv(images_out: Path, manifest_out: Path) -> list[str]:
    return [
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


APPROVED_SNAPSHOT = {
    'id': 'snap-approved-20260901-001',
    'uri': 'gs://odayplus-snapshots/masked/snap-approved-20260901-001.tar.gz',
    'object_generation': 123,
    'content_sha256': 'sha256:' + '1' * 64,
}


def test_snapshot_channels_are_mutually_exclusive(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[9/12] Testing fail-closed when the snapshot file and the inline snapshot fields are both supplied...')
    snapshot = dict(APPROVED_SNAPSHOT)
    snapshot['data_contract_digest'] = compute_data_contract_digest(root=ROOT)
    snapshot['masked'] = True
    snap_file = tmp_path / 'vars_channel_snapshot.json'
    snap_file.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')

    images_out = tmp_path / 'images_dual_snapshot.json'
    manifest_out = tmp_path / 'manifest_dual_snapshot.json'
    argv = _base_argv(images_out, manifest_out) + [
        # What an operator dispatched...
        '--data-snapshot-id', 'snap-dispatch-approved-001',
        '--data-snapshot-uri', 'gs://odayplus-snapshots/masked/snap-dispatch-approved-001.tar.gz',
        '--data-snapshot-content-sha256', 'sha256:' + '3' * 64,
        # ...and what a lingering repository var would have substituted for it.
        '--data-snapshot-file', str(snap_file),
        '--rollback-manifest', str(prev_manifest_path),
    ]
    expect_rejection(argv, manifest_out, 'approved masked data snapshot 有兩條互斥的來源')
    print('  -> PASSED: The file channel cannot silently replace the dispatched snapshot.')


def test_rollback_channels_are_mutually_exclusive(tmp_path: Path, prev_manifest_path: Path) -> None:
    print('[10/12] Testing fail-closed when both rollback flags are supplied...')
    images_out = tmp_path / 'images_dual_rollback.json'
    manifest_out = tmp_path / 'manifest_dual_rollback.json'
    argv = _base_argv(images_out, manifest_out) + [
        '--data-snapshot-id', APPROVED_SNAPSHOT['id'],
        '--data-snapshot-uri', APPROVED_SNAPSHOT['uri'],
        '--data-snapshot-content-sha256', APPROVED_SNAPSHOT['content_sha256'],
        '--rollback-manifest', str(prev_manifest_path),
        '--rollback-release-file', str(prev_manifest_path),
    ]
    expect_rejection(argv, manifest_out, '--rollback-release-file')
    print('  -> PASSED: Neither rollback flag is discarded in favour of the other.')


def test_remote_rollback_uri_is_rejected_unmangled(tmp_path: Path) -> None:
    print('[11/12] Testing fail-closed when the rollback manifest is a remote URI...')
    remote_uri = 'gs://odayplus-releases/manifests/PREV_RELEASE_MANIFEST.json'
    images_out = tmp_path / 'images_remote_rollback.json'
    manifest_out = tmp_path / 'manifest_remote_rollback.json'
    argv = _base_argv(images_out, manifest_out) + [
        '--data-snapshot-id', APPROVED_SNAPSHOT['id'],
        '--data-snapshot-uri', APPROVED_SNAPSHOT['uri'],
        '--data-snapshot-content-sha256', APPROVED_SNAPSHOT['content_sha256'],
        '--rollback-manifest', remote_uri,
    ]
    exit_code, stderr = run_handoff(argv)
    assert exit_code != 0, 'A remote rollback URI must fail closed'
    assert not manifest_out.exists(), 'Manifest must not be written on failure'
    assert remote_uri in stderr, 'The rejection must quote the URI that was passed'
    assert 'gs:/o' not in stderr, 'pathlib must not collapse gs:// into gs:/ in the message'
    print('  -> PASSED: Rejected by its own name, not by a mangled path.')


def test_either_snapshot_channel_alone_still_produces_the_same_manifest(
    tmp_path: Path, prev_manifest_path: Path
) -> None:
    print('[12/12] Testing that exclusivity refuses ambiguity, not the channels themselves...')
    snapshot = dict(APPROVED_SNAPSHOT)
    snapshot['data_contract_digest'] = compute_data_contract_digest(root=ROOT)
    snapshot['masked'] = True
    snap_file = tmp_path / 'file_channel_snapshot.json'
    snap_file.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')

    file_manifest_out = tmp_path / 'manifest_file_channel.json'
    file_code, _ = run_handoff(
        _base_argv(tmp_path / 'images_file_channel.json', file_manifest_out) + [
            '--data-snapshot-file', str(snap_file),
            '--rollback-manifest', str(prev_manifest_path),
        ]
    )

    inline_manifest_out = tmp_path / 'manifest_inline_channel.json'
    inline_code, _ = run_handoff(
        _base_argv(tmp_path / 'images_inline_channel.json', inline_manifest_out) + [
            '--data-snapshot-id', snapshot['id'],
            '--data-snapshot-uri', snapshot['uri'],
            '--data-snapshot-object-generation', str(snapshot['object_generation']),
            '--data-snapshot-content-sha256', snapshot['content_sha256'],
            '--rollback-manifest', str(prev_manifest_path),
        ]
    )

    assert (file_code, inline_code) == (0, 0), 'Each channel alone must still succeed'
    file_manifest = json.loads(file_manifest_out.read_text(encoding='utf-8'))
    inline_manifest = json.loads(inline_manifest_out.read_text(encoding='utf-8'))
    assert file_manifest == inline_manifest, 'The same approved snapshot must yield the same manifest'
    assert validate_release_admission(file_manifest) == []
    print('  -> PASSED: Both channels still admit, and agree on the same manifest digest.')


def test_workflow_contract() -> None:
    print('[8/12] Testing deploy-dev.yml workflow contracts...')
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
    # The rollback input once offered an example path that has never existed.
    phantom = 'docs/evidence/gates/PREV_RELEASE_MANIFEST.json'
    assert phantom not in content, f'deploy-dev.yml still cites {phantom}, which does not exist'
    assert not (ROOT / phantom).exists(), 'fixture assumption: that path is genuinely absent'
    print('  -> PASSED: Workflow contract verified; no phantom example path.')


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        prev_manifest_path = create_prev_manifest_file(tmp_path)
        test_positive_flow_with_snapshot_file_and_rollback_manifest(tmp_path, prev_manifest_path)
        test_missing_snapshot_fails_closed(tmp_path, prev_manifest_path)
        test_unmasked_snapshot_cli_fails_closed(tmp_path, prev_manifest_path)
        test_snapshot_file_missing_masked_fails_closed(tmp_path, prev_manifest_path)
        test_snapshot_file_missing_contract_digest_fails_closed(tmp_path, prev_manifest_path)
        test_snapshot_file_unmasked_fails_closed(tmp_path, prev_manifest_path)
        test_tampered_rollback_manifest_fails_closed(tmp_path)
        test_workflow_contract()
        test_snapshot_channels_are_mutually_exclusive(tmp_path, prev_manifest_path)
        test_rollback_channels_are_mutually_exclusive(tmp_path, prev_manifest_path)
        test_remote_rollback_uri_is_rejected_unmangled(tmp_path)
        test_either_snapshot_channel_alone_still_produces_the_same_manifest(
            tmp_path, prev_manifest_path
        )
    print("\nALL VERIFICATIONS PASSED SUCCESSFULLY (EXIT=0).")
    return 0


if __name__ == '__main__':
    sys.exit(main())
