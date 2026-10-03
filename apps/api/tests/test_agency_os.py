import copy
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.routers import agency_os


def sample(schema):
    if '$ref' in schema:
        return sample(agency_os.CONTRACT_SCHEMA['$defs'][schema['$ref'].split('/')[-1]])
    if 'const' in schema:
        return schema['const']
    if 'enum' in schema:
        return schema['enum'][0]
    if 'anyOf' in schema:
        return sample(schema['anyOf'][-1])
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = 'null' if 'null' in kind else kind[0]
    if kind == 'object':
        return {key: sample(schema['properties'][key]) for key in schema.get('required', [])}
    if kind == 'array':
        return [sample(schema['items']) for _ in range(schema.get('minItems', 0))]
    if kind == 'string':
        return {'date': '2026-09-01', 'date-time': '2026-10-02T12:00:00Z'}.get(schema.get('format'), 'unknown')
    if kind == 'boolean':
        return True
    if kind in ('number', 'integer'):
        return 0
    return None


@pytest.fixture
def contract():
    result = sample(agency_os.CONTRACT_SCHEMA)
    result['report']['type'] = 'monthly'
    result['report']['client_slug'] = 'lk-sneakers'
    result['report']['period']['end'] = '2026-09-30'
    result['client']['slug'] = 'lk-sneakers'
    result['business'] = sample(agency_os.CONTRACT_SCHEMA['$defs']['ecommerce_business'])
    result['paid_media'] = sample(agency_os.CONTRACT_SCHEMA['$defs']['ecommerce_paid_media'])
    result['governance']['completeness']['status'] = 'PARTIAL'
    result['provenance']['source_run_id'] = 'synthetic-test-run'
    agency_os.CONTRACT_VALIDATOR.validate(result)
    return result


@pytest.fixture
def endpoint(monkeypatch):
    rows = []

    class Store:
        def rpc(self, name, payload):
            assert name == 'agency_store_report_contract'
            assert set(payload) == {'p_row'}
            rows.append(copy.deepcopy(payload['p_row']))
            return self

        def execute(self):
            return type('Receipt', (), {'data': {'status': 'accepted', 'disposition': 'LATEST'}})()

    monkeypatch.setattr(agency_os, 'get_supabase', lambda: Store())
    monkeypatch.setattr(agency_os.settings, 'AGENCY_OS_INGEST_KEY', 'synthetic-test-key')
    app = FastAPI()
    app.include_router(agency_os.router)
    return TestClient(app), rows


def send(endpoint, body, authorized=True):
    client, _ = endpoint
    headers = {'Authorization': 'Bearer synthetic-test-key'} if authorized else {}
    return client.post('/agency/ingest/report-contract', headers=headers, json=body)


def test_contract_only_monthly_metadata_and_verbatim_json(endpoint, contract):
    original = copy.deepcopy(contract)
    response = send(endpoint, {'contract': contract})
    assert response.status_code == 200
    assert response.json()['report_type'] == 'monthly'
    row = endpoint[1][0]
    assert row['report_type'] == 'monthly'
    assert row['source_run_id'] == 'synthetic-test-run'
    assert row['comparison_period_start'] is None
    assert row['contract'] == original
    assert row['contract']['business']['revenue']['value'] is None


def test_weekly_preserves_zero_and_status(endpoint, contract):
    contract['report']['type'] = 'weekly'
    contract['paid_media']['total_spend']['value'] = 0
    contract['paid_media']['total_spend']['status'] = 'available'
    response = send(endpoint, {'contract': contract})
    assert response.status_code == 200
    assert endpoint[1][0]['report_type'] == 'weekly'
    assert endpoint[1][0]['contract'] == contract


@pytest.mark.parametrize('metadata', [
    {'report_type': 'weekly'}, {'source_run_id': 'different-run'}, {'generated_at': '2026-10-03T12:00:00Z'},
])
def test_conflicting_envelope_is_rejected(endpoint, contract, metadata):
    assert send(endpoint, {'contract': contract, **metadata}).status_code == 400
    assert not endpoint[1]


@pytest.mark.parametrize('mutation', ['missing_governance', 'incomplete_comparison', 'bad_date', 'reversed_period', 'unknown_client', 'client_mismatch'])
def test_invalid_contracts_do_not_write(endpoint, contract, mutation):
    if mutation == 'missing_governance':
        del contract['governance']
    elif mutation == 'incomplete_comparison':
        contract['report']['comparison_period'] = {'start': None, 'end': None}
    elif mutation == 'bad_date':
        contract['report']['period']['start'] = '2026-09-99'
    elif mutation == 'reversed_period':
        contract['report']['period']['start'] = '2026-10-01'
    elif mutation == 'unknown_client':
        contract['report']['client_slug'] = 'seventh-client'
    else:
        contract['client']['slug'] = 'dipua'
    assert send(endpoint, {'contract': contract}).status_code == 400
    assert not endpoint[1]


def test_authentication_is_required(endpoint, contract):
    assert send(endpoint, {'contract': contract}, authorized=False).status_code == 401
    assert not endpoint[1]


@pytest.mark.parametrize('disposition', ['LATEST', 'REPLAY', 'ARCHIVED'])
def test_atomic_receipt_and_hash_are_returned(endpoint, contract, monkeypatch, disposition):
    import hashlib
    import json
    class Store:
        def rpc(self, name, payload):
            assert payload['p_row']['contract'] == contract
            return self
        def execute(self):
            return type('Receipt', (), {'data': {'status':'accepted','disposition':disposition}})()
    monkeypatch.setattr(agency_os, 'get_supabase', lambda: Store())
    response = send(endpoint, {'contract':contract})
    assert response.status_code == 200
    assert response.json()['disposition'] == disposition
    assert response.json()['contract_hash'] == hashlib.sha256(json.dumps(contract,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def test_database_identity_conflict_is_not_accepted(endpoint, contract, monkeypatch):
    class Store:
        def rpc(self, *args): return self
        def execute(self): return type('Receipt', (), {'data':{'status':'conflict','reason':'SOURCE_RUN_EVIDENCE_IMMUTABLE'}})()
    monkeypatch.setattr(agency_os, 'get_supabase', lambda: Store())
    assert send(endpoint, {'contract':contract}).status_code == 409


@pytest.mark.parametrize('receipt', [None, {}, {'status':'accepted','disposition':'UNKNOWN'}])
def test_missing_or_invalid_persistence_receipt_is_failure(endpoint, contract, monkeypatch, receipt):
    class Store:
        def rpc(self, *args): return self
        def execute(self): return type('Receipt', (), {'data':receipt})()
    monkeypatch.setattr(agency_os, 'get_supabase', lambda: Store())
    assert send(endpoint, {'contract':contract}).status_code == 500
