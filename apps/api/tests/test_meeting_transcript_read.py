"""Direct endpoint unit tests with an in-memory Core storage double; not live HTTP evidence.
ASGI TestClient was unavailable in this Windows session; no network bypass used.
"""
import importlib.util,sys,uuid,hashlib
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI,APIRouter,HTTPException
from urllib.parse import urlsplit,parse_qs
import inspect
import pytest
P=Path(__file__).parent
sp=importlib.util.spec_from_file_location('canonical_meeting_transcript',P.parents[0]/'app/agency_api/v1/meeting_transcript.py');mod=importlib.util.module_from_spec(sp);sys.modules[sp.name]=mod;sp.loader.exec_module(mod)
MID=str(uuid.uuid4());TID=str(uuid.uuid4());CID=str(uuid.uuid4());TEXT='Ana: Decidimos revisar o tracking.\nAna: Ana vai verificar o pixel.'
class Query:
 def __init__(self,rows,log,name):self.rows=rows;self.log=log;self.name=name
 def select(self,*a,**kw):return self
 def eq(self,key,value):self.rows=[r for r in self.rows if str(r.get(key))==str(value)];return self
 def limit(self,*a,**kw):return self
 def order(self,*a,**kw):return self
 def execute(self):return SimpleNamespace(data=self.rows)
 def insert(self,*a,**kw):pytest.fail('READ_ENDPOINT_WROTE_STORAGE')
 update=insert;delete=insert;upsert=insert
class DB:
 def __init__(self,foreign=False,missing=False,broken=False):
  self.log=[];self.broken=broken;self.rows={'core_meetings':[{'id':MID,'client_id':'dipua' if foreign else CID,'provider':'fireflies','external_id':'fireflies-real-test-fixture-id'}], 'core_meeting_transcripts':[] if missing else [{'id':TID,'meeting_id':MID,'raw_text':TEXT,'language':'pt','created_at':'2026-10-27T12:00:00Z'}]}
 def table(self,name):
  self.log.append(name)
  if self.broken:raise RuntimeError('SECRET_SHOULD_NEVER_LEAK')
  return Query(list(self.rows[name]),self.log,name)
def app(db,scope='hermes_service',allowed=None):
 async def auth():
  if scope=='NO_AUTH':raise HTTPException(401,detail='unauthorized')
  return SimpleNamespace(scope=scope,client_ids=allowed)
 api=FastAPI();router=APIRouter(prefix='/agency/v1');mod.register_transcript_read(router,load_db=lambda:db,load_client_meta=lambda client:{'client_id':'lk-sneakers','id':CID} if client in ('lk-sneakers','pixel-lk') else None,auth_dependency=auth);api.include_router(router)
 route=next(r for r in router.routes if getattr(r,'path','')=='/agency/v1/meetings/{meeting_id}/transcript')
 class DirectEndpoint:
  def get(self,path):
   parts=urlsplit(path);query=parse_qs(parts.query)
   if 'client_id' not in query:
    assert next(p for p in route.dependant.query_params if p.name=='client_id').field_info.is_required()
    return SimpleNamespace(status_code=422,json=lambda:{'detail':'required client_id'},text='required client_id')
   try:
    if scope=='NO_AUTH':raise HTTPException(401,detail='unauthorized')
    model=route.endpoint(uuid.UUID(parts.path.split('/')[-2]),client_id=query['client_id'][0],auth=SimpleNamespace(scope=scope,client_ids=allowed))
    data=model.model_dump(mode='json');return SimpleNamespace(status_code=200,json=lambda:data,text=str(data))
   except HTTPException as exc:
    data={'detail':exc.detail};return SimpleNamespace(status_code=exc.status_code,json=lambda:data,text=str(data))
 return DirectEndpoint()
def url(client='lk-sneakers'):return '/agency/v1/meetings/'+MID+'/transcript?client_id='+client

def test_canonical_binding_hash_and_labels_no_write():
 db=DB();r=app(db).get(url());assert r.status_code==200;d=r.json();assert d['meeting_id']==MID and d['client_id']=='lk-sneakers' and d['provider']=='fireflies';assert d['provider_meeting_id']=='fireflies-real-test-fixture-id' and d['transcript_id']==TID and d['version']==TID and d['raw_text']==TEXT;assert d['transcript_hash']==hashlib.sha256(TEXT.encode()).hexdigest();assert d['segments'][0]['speaker']=='Ana' and d['segments'][0]['start_seconds'] is None and d['timing_status']=='NOT_PERSISTED';assert db.log==['core_meetings','core_meeting_transcripts']
@pytest.mark.parametrize('scope,status',[('NO_AUTH',401),('client_viewer',403),('tracking_ingest',403),('hermes_service',200),('platform_web',200),('agency_admin',200)])
def test_explicit_actor_scope_guard(scope,status):
 db=DB();r=app(db,scope).get(url());assert r.status_code==status
 if status!=200:assert db.log==[]

@pytest.mark.parametrize('case',['foreign_meeting','foreign_principal','empty_scope','wrong_client','unknown_meeting','missing_transcript'])
def test_no_cross_client_or_missing_transcript(case):
 db=DB(foreign=case=='foreign_meeting',missing=case=='missing_transcript');allowed=['dipua'] if case=='foreign_principal' else [] if case=='empty_scope' else None;target=url('dipua' if case=='wrong_client' else 'lk-sneakers')
 if case=='unknown_meeting':target=target.replace(MID,str(uuid.uuid4()))
 r=app(db,allowed=allowed).get(target);assert r.status_code in (403,404)
 assert 'raw_text' not in r.json()
 if case!='missing_transcript':assert 'core_meeting_transcripts' not in db.log

def test_explicit_client_required():
 db=DB();r=app(db).get('/agency/v1/meetings/'+MID+'/transcript');assert r.status_code==422 and not db.log

def test_no_summary_or_provider_fallback():
 db=DB(missing=True);r=app(db).get(url());assert r.status_code==404 and set(db.log)=={'core_meetings','core_meeting_transcripts'}

def test_no_upstream_error_secret_leak():
 db=DB(broken=True);r=app(db).get(url());assert r.status_code==503 and 'SECRET' not in r.text

def test_alias_resolves_to_canonical_slug():
 assert app(DB()).get(url('pixel-lk')).json()['client_id']=='lk-sneakers'

def test_transcript_wrong_meeting_denied():
 db=DB();db.rows['core_meeting_transcripts'][0]['meeting_id']=str(uuid.uuid4());r=app(db).get(url());assert r.status_code==404

def test_stable_repeated_read_no_persistence():
 db=DB();client=app(db);assert client.get(url()).json()==client.get(url()).json()

def test_unlabelled_line_has_unknown_speaker():
 db=DB();db.rows['core_meeting_transcripts'][0]['raw_text']='Discussão ainda sem decisão.';d=app(db).get(url()).json();assert d['segments'][0]['speaker'] is None and d['segments'][0]['text']=='Discussão ainda sem decisão.'



