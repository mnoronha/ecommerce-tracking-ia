from types import SimpleNamespace
from datetime import date
from fastapi import APIRouter,FastAPI,HTTPException
from fastapi.testclient import TestClient
from app.agency_api.v1.campaign_period import register_campaign_period_read
from app.core.collectors import campaign_period as provider
import pytest

def api(monkeypatch,allowed=None):
    import app.core.campaign_period as core
    calls=[]
    monkeypatch.setattr(core,'select_snapshot',lambda db,c,s,e: calls.append((c,s,e)) or None)
    auth=SimpleNamespace(client_ids=allowed)
    def authorize():return auth
    router=APIRouter();register_campaign_period_read(router,load_db=lambda:object(),
        load_client_meta=lambda c:{'id':'uuid-lk','client_id':'lk-sneakers'} if c in ('lk-sneakers','pixel-lk') else None,
        auth_dependency=authorize)
    app=FastAPI();app.include_router(router)
    return TestClient(app),app,authorize,calls

def test_read_healthy_missing_canonical_slug_no_writes(monkeypatch):
    client,_,_,calls=api(monkeypatch,['lk-sneakers'])
    r=client.get('/clients/pixel-lk/campaign-performance?period_start=2026-09-01&period_end=2026-09-30&platform=google')
    assert r.status_code==200
    assert r.json()['client_id']=='lk-sneakers' and r.json()['source_state']=='MISSING'
    assert r.json()['rows']==[] and len(calls)==1
    assert client.post('/clients/lk-sneakers/campaign-performance').status_code==405

def test_auth_scope_and_window_before_storage(monkeypatch):
    client,app,auth,calls=api(monkeypatch,['dipua'])
    path='/clients/lk-sneakers/campaign-performance?period_start=2026-09-01&period_end=2026-09-30&platform=meta'
    assert client.get(path).status_code==403 and calls==[]
    def deny():raise HTTPException(401,'auth required')
    app.dependency_overrides[auth]=deny
    assert client.get(path).status_code==401 and calls==[]

class Response:
    def __init__(self,data):self.data=data
    def raise_for_status(self):pass
    def json(self):return self.data

def test_google_purchase_category_pagination_fractional_and_status(monkeypatch):
    monkeypatch.setattr(provider,'_get_google_token',lambda t:'ephemeral-test')
    calls=[]
    responses=[{'results':[{'campaign':{'id':'1','name':'Search','status':'ENABLED','advertisingChannelType':'SEARCH'},'metrics':{'costMicros':'100000000'}}]},
        {'results':[{'campaign':{'id':'1'},'segments':{'conversionActionCategory':'LEAD'},'metrics':{'conversions':100,'conversionsValue':99999}}],'nextPageToken':'next'},
        {'results':[{'campaign':{'id':'1'},'segments':{'conversionActionCategory':'PURCHASE'},'metrics':{'conversions':'.5','conversionsValue':'75'}}]}]
    def post(url,headers,json,timeout):calls.append(json);return Response(responses.pop(0))
    monkeypatch.setattr(provider.httpx,'post',post)
    rows,complete=provider.fetch_google({'google_ads_customer_id':'1','google_ads_refresh_token':'test'},date(2026,9,1),date(2026,9,30))
    assert complete and rows[0]['purchase_conversions']==.5 and rows[0]['purchase_value']==75
    assert rows[0]['campaign_type']=='SEARCH' and rows[0]['spend']==100
    assert calls[-1]['pageToken']=='next'

def test_meta_pagination_fixed_origin_not_next_url_and_purchase_priority(monkeypatch):
    responses=[{'data':[{'campaign_id':'1','campaign_name':'Meta','spend':'20','actions':[{'action_type':'purchase','value':'2'},{'action_type':'omni_purchase','value':'2'}],'action_values':[{'action_type':'purchase','value':'50'}]}],
                'paging':{'next':'https://untrusted.invalid/secret','cursors':{'after':'a'}}},
               {'data':[]},{'data':[{'id':'1','status':'ACTIVE'}]}]
    urls=[]
    def get(url,headers,params,timeout):urls.append(url);return Response(responses.pop(0))
    monkeypatch.setattr(provider.httpx,'get',get)
    rows,complete=provider.fetch_meta({'meta_ad_account_id':'act_1','meta_access_token':'test'},'2026-09-01','2026-09-30')
    assert complete and rows[0]['purchases']==2 and rows[0]['purchase_value']==50
    assert all(url.startswith('https://graph.facebook.com/') for url in urls)

def test_repeated_provider_page_fails_closed(monkeypatch):
    monkeypatch.setattr(provider.httpx,'post',lambda *a,**kw:Response({'results':[],'nextPageToken':'repeat'}))
    with pytest.raises(ValueError,match='REPEATED_PAGE_TOKEN'):provider._google_pages('https://googleads.googleapis.com',{},'query')
