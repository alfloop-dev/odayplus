"""Capture real public-source bytes through existing Dagster assets and read them back.

This is a local integration verifier, not a release publisher. Runtime source-policy
checks remain in the shared kernel. No GCS generation or legal approval is issued.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import dagster as dg

from oday_data_platform.defs.external.mof_moi import mof_business_registrations_raw
from oday_data_platform.defs.external.ris_nlsc import ris_population_release
from oday_data_platform.external.sources.mof_business.parser import parse_mof_json
from oday_data_platform.external.sources.ris.adapter import RISAdapter
from oday_data_platform.external.sources.ris.official import ris_response_rows
from oday_data_platform.infra.resources import ExternalSourceResource


def capture(source: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    raw_root = output / 'raw'
    os.environ['EMGI_EVIDENCE_ROOT'] = str(raw_root.resolve())
    asset = mof_business_registrations_raw if source == 'mof' else ris_population_release
    node = 'mof_business_registrations_raw' if source == 'mof' else 'ris_population_release'
    result = dg.materialize([asset], resources={'external_sources': ExternalSourceResource(max_retries=1)})
    metadata = {key: value.value for key, value in result.asset_materializations_for_node(node)[0].metadata.items()}
    pages = metadata.get('pages') or [metadata]
    retained = []
    normalized = []
    quarantined = []
    source_rows = []
    for page in pages:
        path = raw_root / (page['content_sha256'] + '.bin')
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != page['content_sha256'] or len(body) != page['content_size_bytes']:
            raise ValueError('Retained source bytes failed independent disk readback')
        if source == 'mof':
            rows = parse_mof_json(body)
            bad = []
        else:
            source_rows.extend(ris_response_rows(json.loads(body)))
            rows, bad = RISAdapter().parse_raw(body, metadata['release_key'])
        normalized.extend(row.model_dump(mode='json') for row in rows)
        quarantined.extend(item.model_dump(mode='json') for item in bad)
        retained.append({**page, 'storage_uri': path.resolve().as_uri(),
                         'readback_sha256': digest, 'readback_verified': True,
                         'parsed_record_count': len(rows), 'quarantined_count': len(bad),
                         'gcs_generation': None})
    if source == 'ris':
        rows, bad = RISAdapter().parse_raw(source_rows, metadata['release_key'])
        normalized = [row.model_dump(mode='json') for row in rows]
        quarantined = [row.model_dump(mode='json') for row in bad]
        if len(normalized) != metadata['parsed_record_count']:
            raise ValueError('Retained page parsing differs from the materialized release')
    if not normalized or quarantined or metadata.get('is_valid') is False:
        raise ValueError('Acquired bytes did not produce a valid nonempty source dataset')
    normalized_path = output / 'normalized.jsonl'
    normalized_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in normalized))
    report = {'schema': 'emgi.public-source-local-capture.v1', 'source': source,
              'captured_at': datetime.now(timezone.utc).isoformat(),
              'scope': metadata.get('requested_scope', {'limit': 100, 'offset': 0}),
              'source_terms': 'https://data.gov.tw/license',
              'source_catalog': 'https://data.gov.tw/dataset/9400' if source == 'mof' else 'https://data.gov.tw/dataset/77132',
              'mode': 'LIVE_LOCAL_INTEGRATION', 'release_ready': False,
              'independent_totals_verified': metadata.get('independent_totals_verified', False),
              'parsed_record_count': len(normalized), 'quarantined_count': len(quarantined),
              'pages': retained, 'normalized_uri': normalized_path.resolve().as_uri(),
              'normalized_sha256': hashlib.sha256(normalized_path.read_bytes()).hexdigest(),
              'upstream_completeness': 'partial_national_page' if source == 'mof' else 'all_pages_of_requested_scope'}
    (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['mof', 'ris'], required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private local directory for original and normalized bytes')
    args = parser.parse_args()
    report = capture(args.source, args.output)
    print(json.dumps({key: report[key] for key in ('source', 'mode', 'parsed_record_count', 'quarantined_count', 'release_ready')}))


if __name__ == '__main__':
    main()
