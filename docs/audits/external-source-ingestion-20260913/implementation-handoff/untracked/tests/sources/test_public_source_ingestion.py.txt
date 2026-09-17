"""Regressions for official API wire shapes (synthetic rows, never live evidence)."""
import hashlib
import json

import dagster as dg
import httpx
import pytest

from oday_data_platform.defs.external.mof_moi import mof_business_registrations_raw
from oday_data_platform.defs.external.ris_nlsc import ris_population_release
from oday_data_platform.external.acquisition.kernel import LiveAcquisitionKernel
from oday_data_platform.external.sources.mof_business.parser import parse_mof_json
from oday_data_platform.external.sources.ris.adapter import RISAdapter
from oday_data_platform.external.sources.ris.official import ris_source_uri
from oday_data_platform.infra.resources import ExternalSourceResource


def official_row(code='63000050001'):
    return {'statistic_yyymm': '11508', 'district_code': code, 'site_id': '臺北市中正區',
            'village': '測試里', 'household_no': '2', 'people_total': '3',
            'people_total_m': '1', 'people_total_f': '2'}


def envelope(rows, page=1, pages=1):
    return {'responseCode': 'OD-0101-S', 'responseData': rows, 'page': str(page),
            'totalPage': str(pages), 'pageDataSize': str(len(rows)), 'totalDataSize': '7781'}


def test_ris_real_wire_columns_and_code_hierarchy():
    valid, bad = RISAdapter().parse_raw(json.dumps(envelope([official_row()])).encode(), '2026-08')
    assert not bad
    assert (valid[0].county_code, valid[0].town_code, valid[0].village_code) == ('63000', '63000050', '63000050001')
    assert (valid[0].population_total, valid[0].household_count) == (3, 2)
    assert valid[0].town_name == '中正區'


@pytest.mark.parametrize('mutation', [
    {'responseCode': 'OD-0101-E'}, {'pageDataSize': '9'}, {'page': '2'}, {'responseData': {}},
])
def test_ris_bad_api_envelope_rejected(mutation):
    payload = {**envelope([official_row()]), **mutation}
    with pytest.raises(ValueError):
        RISAdapter().parse_raw(json.dumps(payload), '2026-08')


@pytest.mark.parametrize('mutation', [
    {'statistic_yyymm': '11507'}, {'district_code': '63000'}, {'people_total_f': '-1'},
    {'people_age_000_m': '1'},
])
def test_ris_wrong_month_code_or_incomplete_demographics_quarantined(mutation):
    valid, bad = RISAdapter().parse_raw([{**official_row(), **mutation}], '2026-08')
    assert not valid and len(bad) == 1


def test_ris_complete_age_series_and_arithmetic():
    row = official_row()
    row.update({f'people_age_{age:03d}_{sex}': '0' for age in range(100) for sex in ('m', 'f')})
    row.update(people_age_100up_m='0', people_age_100up_f='0', people_age_000_m='1', people_age_080_f='2')
    valid, bad = RISAdapter().parse_raw([row], '2026-08')
    assert not bad and valid[0].age_brackets == {'0-14': 1, '15-64': 0, '65+': 2}
    row['people_age_080_f'] = '3'
    valid, bad = RISAdapter().parse_raw([row], '2026-08')
    assert not valid and bad


def test_filtered_ris_page_is_not_national_total():
    raw = json.dumps(envelope([official_row()])).encode()
    assert LiveAcquisitionKernel._response_shape(raw, {}, 1000, {'COUNTY': '臺北市'}) == (1, 1, False)
    raw = json.dumps(envelope([official_row()], 1, 2)).encode()
    assert LiveAcquisitionKernel._response_shape(raw, {}, 1000) == (1, None, True)


def test_ris_month_is_in_requested_endpoint():
    assert ris_source_uri('2026-08').endswith('/11508')
    with pytest.raises(ValueError):
        ris_source_uri('2026-13')


def test_mof_official_json_fields_preserve_candidate_semantics():
    records = parse_mof_json(json.dumps([{'ban': '12345678', 'businessNm': '測試營業人',
        'businessAddress': '臺北市中正區測試路1號', 'capitalAmount': '10000',
        'businessSetupDate': '1150801', 'businessType': '獨資', 'isUseInvoice': 'Y',
        'industryCd': '561113', 'industryNm': '餐館', 'industryCd1': '563199', 'industryNm1': '飲料',
        'industryCd2': '472914', 'industryNm2': '飲品', 'industryCd3': '472999', 'industryNm3': '食品'}]))
    row = records[0]
    assert row.business_name == '測試營業人' and row.established_date == '2026-08-01'
    assert len(row.industry_codes) == 4 and row.capital_amount == 10000
    assert row.status == 'UNKNOWN' and not row.is_active
    assert row.is_candidate_evidence and not row.is_physical_store_truth


def run_asset(monkeypatch, tmp_path, asset, handler):
    monkeypatch.setenv('EMGI_EVIDENCE_ROOT', str(tmp_path))
    monkeypatch.setenv('RIS_RELEASE_KEY', '2026-08')
    monkeypatch.setattr(ExternalSourceResource, 'client', lambda self, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)))
    return dg.materialize([asset], resources={'external_sources': ExternalSourceResource(max_retries=1)})


def test_ris_asset_acquires_each_page_and_preserves_original_bytes(monkeypatch, tmp_path):
    bodies = {n: json.dumps(envelope([official_row(f'6300005000{n}')], n, 2)).encode() for n in (1, 2)}
    seen = []
    def handler(request):
        page = int(request.url.params['PAGE']); seen.append(page)
        assert request.url.path.endswith('/ODRP014/11508')
        return httpx.Response(200, content=bodies[page])
    result = run_asset(monkeypatch, tmp_path, ris_population_release, handler)
    value = result.output_for_node('ris_population_release')
    assert seen == [1, 2] and value['receipt']['parsed_record_count'] == 2
    assert value['receipt']['page_count'] == 2
    assert value['receipt']['content_sha256'] == hashlib.sha256(value['raw_bytes']).hexdigest()
    assert value['receipt']['acquisition_mode'] == 'LIVE_PAGINATED_DERIVATION'
    assert value['receipt']['independent_totals_verified'] is False
    for body in bodies.values():
        assert (tmp_path / (hashlib.sha256(body).hexdigest() + '.bin')).read_bytes() == body


@pytest.mark.parametrize('failure', ['repeated_page', 'changed_page_count', 'page_budget', 'duplicate_admin'])
def test_ris_pagination_failures_do_not_publish_complete_release(monkeypatch, tmp_path, failure):
    if failure == 'page_budget':
        monkeypatch.setenv('RIS_MAX_PAGES', '1')
    def handler(request):
        page = int(request.url.params['PAGE'])
        code = '63000050001' if failure == 'duplicate_admin' else f'6300005000{page}'
        payload = envelope([official_row(code)], 1 if failure == 'repeated_page' else page,
                           3 if failure == 'changed_page_count' and page == 2 else 2)
        return httpx.Response(200, json=payload)
    if failure == 'duplicate_admin':
        result = run_asset(monkeypatch, tmp_path, ris_population_release, handler)
        metadata = result.asset_materializations_for_node('ris_population_release')[0].metadata
        assert metadata['is_valid'].value is False and metadata['quarantined_records'].value == 1
    else:
        with pytest.raises(ValueError):
            run_asset(monkeypatch, tmp_path, ris_population_release, handler)


def test_mof_page_request_and_repeated_capture_have_distinct_identities(monkeypatch, tmp_path):
    seen = []
    def handler(request):
        seen.append(dict(request.url.params))
        assert request.url.host == 'eip.fia.gov.tw'
        return httpx.Response(200, json=[])
    runs = [run_asset(monkeypatch, tmp_path, mof_business_registrations_raw, handler) for _ in range(2)]
    receipts = [r.asset_materializations_for_node('mof_business_registrations_raw')[0].metadata for r in runs]
    assert seen == [{'limit': '100', 'offset': '0'}] * 2
    assert receipts[0]['execution_id'].value != receipts[1]['execution_id'].value
    assert receipts[0]['acquisition_scope'].value == 'first_page_only'
