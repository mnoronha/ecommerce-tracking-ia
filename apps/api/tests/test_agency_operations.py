import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.routers import agency_os


@pytest.fixture
def contract():
    capabilities = {key: False for key in agency_os.OPERATIONS_SCHEMA['properties']['capabilities']['required']}
    result = {
        'schema_version': 'norolabs-operations-contract-v1', 'client_slug': 'lk-sneakers',
        'source_run_id': 'synthetic-operations-test', 'generated_at': '2026-10-02T12:00:00Z',
        'current_period': '2026-10', 'registry_generated_at': None, 'capabilities': capabilities,
        'truth_gates': {'business_truth': 'access_missing'}, 'target_truth': {'state': 'active', 'period': '2026-09', 'status': 'ACTIVE'},
        'source_status': {'ga4': 'access_missing'}, 'alerts': [], 'human_dependencies': [],
        'workflows': {
            'creative': {'state': 'BLOCKED', 'service_status': 'UNKNOWN', 'blockers': ['APPROVED_SEED_ASSET_MISSING'], 'asset_ids': [], 'human_review_required': True, 'automatic_publication': False},
            'campaign': {'state': 'BLOCKED', 'blockers': ['CURRENT_TARGET_TRUTH_MISSING'], 'create_paused': True, 'activate_after_creation': False, 'human_review_required': True},
        },
        'governance': {'human_review_required': True, 'decision_automation': False, 'execution': False, 'performance_alerts_enabled': False},
        'provenance': {'registry': 'synthetic', 'alerts': 'synthetic', 'client_truth': 'synthetic', 'scope': 'operational_only'},
    }
    agency_os.OPERATIONS_VALIDATOR.validate(result)
    return result


@pytest.fixture
def endpoint(monkeypatch):
    state = {'rows': [], 'matches': [{'id': 'synthetic-client-id'}]}

    class Query:
        def __init__(self, name):
            self.name, self.filters = name, {}
            self.new = None
        def select(self, fields): return self
        def eq(self, key, value): self.filters[key] = value; return self
        def limit(self, value): return self
        def insert(self, row): self.new = copy.deepcopy(row); return self
        def execute(self):
            if self.name == 'clients':
                assert self.filters == {'pixel_id': 'lk-sneakers'}
                return SimpleNamespace(data=state['matches'])
            assert self.name == 'agency_operations_contracts'
            if self.new is not None:
                state['rows'].append(self.new)
                return SimpleNamespace(data=[self.new])
            return SimpleNamespace(data=[row for row in state['rows'] if all(row[key] == value for key, value in self.filters.items())])

    monkeypatch.setattr(agency_os, 'get_supabase', lambda: SimpleNamespace(table=lambda name: Query(name)))
    monkeypatch.setattr(agency_os.settings, 'AGENCY_OS_INGEST_KEY', 'synthetic-test-key')
    app = FastAPI()
    app.include_router(agency_os.router)
    return TestClient(app), state


def send(endpoint, contract, auth=True):
    return endpoint[0].post('/agency/ingest/operations-contract', json={'contract': contract}, headers={'Authorization': 'Bearer synthetic-test-key'} if auth else {})


def test_authenticated_verbatim_storage_and_receipt(endpoint, contract):
    result = send(endpoint, contract)
    assert result.status_code == 200
    row = endpoint[1]['rows'][0]
    assert row['contract'] == contract
    assert row['contract']['source_status']['ga4'] == 'access_missing'
    expected = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert result.json()['contract_hash'] == expected


def test_retry_idempotent_but_different_evidence_is_rejected(endpoint, contract):
    assert send(endpoint, contract).status_code == 200
    assert send(endpoint, contract).status_code == 200
    assert len(endpoint[1]['rows']) == 1
    contract['source_status']['ga4'] = 'available'
    assert send(endpoint, contract).status_code == 409
    assert len(endpoint[1]['rows']) == 1


def test_ingest_requires_existing_bearer_key(endpoint, contract):
    assert send(endpoint, contract, False).status_code == 401
    assert endpoint[1]['rows'] == []


@pytest.mark.parametrize('section,field', [('capabilities','execution'),('governance','decision_automation'),('governance','performance_alerts_enabled')])
def test_automation_cannot_be_enabled_in_transport(endpoint, contract, section, field):
    contract[section][field] = True
    assert send(endpoint, contract).status_code == 400
    assert endpoint[1]['rows'] == []


def test_unknown_client_and_injected_kpi_rejected(endpoint, contract):
    contract['client_slug'] = 'colab55'
    assert send(endpoint, contract).status_code == 400
    contract['client_slug'] = 'lk-sneakers'
    contract['business_revenue'] = 0
    assert send(endpoint, contract).status_code == 400


def test_ambiguous_or_missing_routing_blocked(endpoint, contract):
    endpoint[1]['matches'] = []
    assert send(endpoint, contract).status_code == 409
    endpoint[1]['matches'] = [{'id':'a'}, {'id':'b'}]
    assert send(endpoint, contract).status_code == 409


def test_foreign_client_dependency_rejected(endpoint, contract):
    contract['human_dependencies'] = [{'dependency_id':'TEST','client_slug':'dipua','type':'BUSINESS_DATA','status':'OPEN','blocking_level':'BLOCKING','requested_action':'Synthetic','created_at':'2026-10-02T12:00:00Z'}]
    assert send(endpoint, contract).status_code == 400


def test_invalid_date_rejected(endpoint, contract):
    contract['generated_at'] = 'invalid'
    assert send(endpoint, contract).status_code == 400


def health_snapshot(client):
    module = {'state': 'MISSING', 'stale': None, 'errors': [], 'blockers': [], 'last_run': None, 'last_success': None}
    return {'schema': 'norolabs.system-health.v1', 'client_slug': client, 'generated_at': '2026-10-03T12:00:00Z', 'state': 'UNKNOWN', 'modules': {key: copy.deepcopy(module) for key in ('collection','performance_truth','weekly','monthly','alerts','contract_sync')}, 'policy_ref': 'config/source-freshness-policy.yaml', 'canonical_kpis_recomputed': False, 'execution': False, 'decision_automation': False}


def test_health_verbatim_preserves_missing_and_unknown(endpoint, contract):
    contract['provenance']['system_health'] = health_snapshot(contract['client_slug'])
    assert send(endpoint, contract).status_code == 200
    stored = endpoint[1]['rows'][0]['contract']['provenance']['system_health']
    assert stored == contract['provenance']['system_health']
    assert stored['modules']['collection']['last_success'] is None


def test_foreign_client_health_rejected(endpoint, contract):
    contract['provenance']['system_health'] = health_snapshot('dipua')
    assert send(endpoint, contract).status_code == 400
    assert endpoint[1]['rows'] == []


@pytest.mark.parametrize('field', ['execution','decision_automation','canonical_kpis_recomputed'])
def test_health_cannot_enable_execution_or_kpi_recalculation(endpoint, contract, field):
    contract['provenance']['system_health'] = health_snapshot(contract['client_slug'])
    contract['provenance']['system_health'][field] = True
    assert send(endpoint, contract).status_code == 400


def test_health_injected_business_metric_rejected(endpoint, contract):
    contract['provenance']['system_health'] = health_snapshot(contract['client_slug'])
    contract['provenance']['system_health']['modules']['collection']['revenue'] = 0
    assert send(endpoint, contract).status_code == 400
