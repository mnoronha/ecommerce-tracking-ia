from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import ast,importlib.util,json,sys,threading,types,uuid
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.core import canonical_reports as m
WORK=Path('C:/Users/maico/Documents/Codex/2026-10-05/referenced-chatgpt-conversation-this-is-an/outputs')

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
bridge=load('canonical_bridge_under_test',WORK/'report-canonicalization-implementation/canonical_bridge.py')

class Memory:
 def __init__(self):self.rows={};self.lock=threading.RLock()
 def get(self,key):
  with self.lock:return deepcopy(self.rows.get(key))
 def cas(self,key,revision,document):
  with self.lock:
   old=self.rows.get(key)
   if (old['revision'] if old else None)!=revision:return False
   self.rows[key]={'revision':1 if old is None else old['revision']+1,'document':deepcopy(document)};return True

def artifact(**kw):
 a={'client_id':'fixture','canonical_engine':'H7','engine_version':'code-sha256','version':1,'generated_at':'2026-10-19T12:00:00Z',
 'period_start':'2026-10-12','period_end':'2026-10-18','source_state':[{'source_state':'PARTIAL'}],
 'certification_status':[{'metric_key':'mer','certification_status':'PROVISIONAL'}],'review_status':'PARTIAL',
 'evidence_refs':['synthetic-only:api:snapshot'],
 'content':{'client_id':'fixture','analysis_period':{'start':'2026-10-12','end':'2026-10-18'},'status':'PARTIAL','facts':[{'mer':None,'target_ratio':None}]}}
 a.update(kw);return a

def service():return m.Reports(Memory(),m.Flags(True,True,True,True))
def published(s,version=1):
 r=s.generate(artifact(version=version))
 for t in ('READY_FOR_REVIEW','APPROVED','PUBLISHED'):r=s.transition(r['report_id'],version,t,actor='synthetic-human',at='2026-10-20T12:00:00Z',expected_hash=r['artifact_hash'])
 return r

@pytest.mark.parametrize('flag',m.FLAG_NAMES)
def test_default_flags_off(monkeypatch,flag):
 for k in m.FLAG_NAMES:monkeypatch.delenv(k,raising=False)
 assert not getattr(m.Flags.environment(),flag)

@pytest.mark.parametrize('engine',['H7','H8'])
def test_disabled_generation_no_store_access(engine):
 class Never:
  def get(self,*a):raise AssertionError('DB reached')
 with pytest.raises(m.ReportError,match='GENERATION_DISABLED'):m.Reports(Never(),m.Flags()).generate(artifact(canonical_engine=engine))


def test_generate_draft_no_approval_publish_send():
 s=service();r=s.generate(artifact());assert r['status']=='DRAFT' and r['approved_at'] is None and r['published_at'] is None
 assert r['content']['facts'][0]['target_ratio'] is None
 assert len(s.store.rows)==1

@pytest.mark.parametrize('target',['APPROVED','PUBLISHED','DELIVERED'])
def test_cannot_skip_review(target):
 s=service();r=s.generate(artifact())
 with pytest.raises(m.ReportError,match='INVALID_TRANSITION'):s.transition(r['report_id'],1,target,actor='human',at='now',expected_hash=r['artifact_hash'])


def test_approve_publish_do_not_queue_delivery():
 s=service();r=published(s);assert r['status']=='PUBLISHED' and r['approved_by']=='synthetic-human'
 assert not any(k.startswith('delivery:') for k in s.store.rows)
 assert len(s.store.rows)==1


def test_generate_idempotent_and_version_immutable():
 s=service();a=artifact();r=s.generate(a);again=s.generate(a)
 assert r==again and len(s.store.rows)==1
 a['content']['facts'][0]['mer']=5
 with pytest.raises(m.ReportError,match='IMMUTABLE'):s.generate(a)
 assert s.get(r['report_id'],1)['content']['facts'][0]['mer'] is None


def test_exact_approved_version_not_latest():
 s=service();old=published(s);new=s.generate(artifact(version=2))
 d=s.prepare_delivery(str(uuid.uuid4()),old['report_id'],1,channel='EMAIL',recipient_hash='a'*64,actor='human',at='now')
 assert d['report_version']==1 and d['artifact_hash']==old['artifact_hash']
 assert s.get(new['report_id'],2)['status']=='DRAFT'
 with pytest.raises(m.ReportError,match='PUBLISHED'):s.prepare_delivery(str(uuid.uuid4()),new['report_id'],2,channel='EMAIL',recipient_hash='a'*64,actor='human',at='now')


def test_delivery_idempotency_and_binding_conflict_no_send():
 s=service();r=published(s);did=str(uuid.uuid4());args=(did,r['report_id'],1)
 kw={'channel':'EMAIL','recipient_hash':'a'*64,'actor':'human','at':'now'}
 a=s.prepare_delivery(*args,**kw);b=s.prepare_delivery(*args,**kw)
 assert a==b and a['attempt']==0 and a['status']=='DELIVERY_PENDING'
 assert a['provider_message_id'] is None and a['sent_at'] is None
 with pytest.raises(m.ReportError,match='DELIVERY_ID_CONFLICT'):s.prepare_delivery(*args,**{**kw,'recipient_hash':'b'*64})
 with pytest.raises(m.ReportError,match='NO_DELIVERY_TRANSPORT'):s.dispatch(did)

@pytest.mark.parametrize('content',[{}, {'client_id':'other','analysis_period':{},'facts':[1]}, {'client_id':'fixture','analysis_period':{'start':'2026-10-12','end':'2026-10-18'},'facts':[{'text':'Narrative pending Hermes analysis'}]}])
def test_no_placeholder_or_wrong_scope(content):
 with pytest.raises(m.ReportError):service().generate(artifact(content=content))


def test_concurrent_generation_single_record():
 from concurrent.futures import ThreadPoolExecutor
 s=service()
 with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(lambda _:s.generate(artifact()),range(24)))
 assert len({r['report_id'] for r in rows})==1 and len(s.store.rows)==1


def test_human_hash_binding():
 s=service();r=s.generate(artifact())
 with pytest.raises(m.ReportError,match='HASH_MISMATCH'):s.transition(r['report_id'],1,'READY_FOR_REVIEW',actor='human',at='now',expected_hash='0'*64)


def test_pub_flag_independent():
 s=service();r=s.generate(artifact())
 s.flags=m.Flags(True,True,False,False)
 s.transition(r['report_id'],1,'READY_FOR_REVIEW',actor='human',at='now',expected_hash=r['artifact_hash'])
 s.transition(r['report_id'],1,'APPROVED',actor='human',at='now',expected_hash=r['artifact_hash'])
 with pytest.raises(m.ReportError,match='PUBLICATION_DISABLED'):s.transition(r['report_id'],1,'PUBLISHED',actor='human',at='now',expected_hash=r['artifact_hash'])


def test_scheduler_default_off_and_existing_jobs_untouched(monkeypatch):
 for k in m.FLAG_NAMES:monkeypatch.delenv(k,raising=False)
 class Scheduler:
  def add_job(self,*a,**kw):raise AssertionError('Job added while OFF')
 bridge.register_jobs(Scheduler(),None,None,None)
 main=(ROOT/'app/main.py').read_text(encoding='utf-8')
 for name in ('weekly_review','monthly_review','weekly_reports','monthly_reports'):assert 'id="'+name+'"' in main
 assert 'reports.send_weekly_reports' in main and 'reports.send_monthly_reports' in main

@pytest.mark.parametrize('engine',['H7','H8'])
def test_real_engines_generate_nonplaceholder(engine,monkeypatch):
 sys.path.insert(0,str(WORK/'alignment-sprint-h7'));sys.path.insert(0,str(WORK/'alignment-sprint-h8'))
 fixture_module=__import__('test_h7' if engine=='H7' else 'test_h8')
 raw=fixture_module.fixture();raw['client_id']='fixture'
 now=datetime(2026,10,19,12,tzinfo=timezone.utc);period=bridge.closed_period(engine,now,'America/Sao_Paulo')
 if engine=='H7':
  engine_module=fixture_module.w
  periods=engine_module.windows('America/Sao_Paulo',now)
 else:engine_module=fixture_module.m;periods=engine_module.periods('America/Sao_Paulo',now)
 for name,window in raw['windows'].items():
  p=periods[name];window['period']=p;window['metrics']['period']=p;window['metrics']['client_id']='fixture'
 raw['evidence']=[{'source':'AGENCY_API','request_id':'synthetic-only','schema_validation':'PASS'}]
 runtime_path=WORK/('alignment-sprint-h7/weekly_runtime.py' if engine=='H7' else 'alignment-sprint-h8/monthly_runtime.py')
 calls=[]
 def collect(client,**kw):calls.append(kw);return deepcopy(raw)
 module=types.SimpleNamespace(__file__=str(runtime_path),collect=collect,diagnostics=lambda r,**kw:r,build=engine_module.build)
 s=service()
 class API:
  saved=None
  def lookup(self,**kw):return self.saved
  def create(self,a):self.saved=s.generate(a);return self.saved
 api=API();b=bridge.Bridge(api,{engine:module})
 monkeypatch.setenv('CANONICAL_WEEKLY_REVIEW_ENABLED' if engine=='H7' else 'CANONICAL_MONTHLY_REVIEW_ENABLED','true')
 r=b.generate('fixture',engine,now=now,timezone_name='America/Sao_Paulo')
 assert r['status']=='DRAFT' and r['canonical_engine']==engine and r['content']['facts']
 assert r['period_start']==period['start'] and r['period_end']==period['end']
 assert r['certification_status'] and any(x['certification_status']=='PROVISIONAL' for x in r['certification_status'])
 assert b.generate('fixture',engine,now=now,timezone_name='America/Sao_Paulo')==r and len(calls)==1
 if engine=='H8':assert calls[0]['analysis']==period


def test_core_files_do_not_import_legacy_senders():
 source=(ROOT/'app/core/canonical_reports.py').read_text(encoding='utf-8-sig')
 assert 'send_report_now' not in source and 'publish_narrative_report' not in source
 assert 'NO_DELIVERY_TRANSPORT_INSTALLED' in source

@pytest.fixture
def http_api(monkeypatch):
 from fastapi import FastAPI,Header,HTTPException
 from fastapi.testclient import TestClient
 for k in m.FLAG_NAMES:monkeypatch.setenv(k,'true')
 def dependency(allowed):
  def get(x_scope:str=Header('hermes_service')):
   if x_scope not in allowed:raise HTTPException(403,'SCOPE_DENIED')
   return types.SimpleNamespace(scope=x_scope,client_ids=None,subject='fixture-human' if x_scope=='platform_web' else None)
  return get
 tree=ast.parse((ROOT/'app/agency_api/v1/canonical_reports.py').read_text(encoding='utf-8-sig'))
 tree.body=[n for n in tree.body if not isinstance(n,ast.ImportFrom) or n.level==0]
 mod=types.ModuleType('canonical_http_fixture');sys.modules[mod.__name__]=mod
 mod.__dict__.update(Reports=m.Reports,CoreStore=m.CoreStore,Flags=m.Flags,ReportError=m.ReportError,AuthContext=types.SimpleNamespace,
 SCOPE_READ_ANY=dependency(('agency_admin','hermes_service','platform_web')),
 SCOPE_HERMES_WRITE=dependency(('agency_admin','hermes_service')),
 SCOPE_PLATFORM_WRITE=dependency(('agency_admin','platform_web')))
 exec(compile(tree,'canonical_http_fixture','exec'),mod.__dict__)
 s=service();mod.service=lambda:s
 app=FastAPI();app.include_router(mod.router)
 return TestClient(app),s


def test_http_generate_read_and_hermes_cannot_approve(http_api):
 c,s=http_api;r=c.post('/agency/v1/canonical-reports/versions',json={'artifact':artifact()})
 assert r.status_code==200,r.text
 row=r.json();path=f'/agency/v1/canonical-reports/{row["report_id"]}/versions/1'
 assert c.get(path).json()['artifact_hash']==row['artifact_hash']
 assert c.post(path+'/transitions',json={'target':'APPROVED','actor':'not-trusted-hermes','artifact_hash':row['artifact_hash']}).status_code==403
 assert s.get(row['report_id'],1)['status']=='DRAFT'


def test_http_human_lifecycle_publish_never_delivery(http_api):
 c,s=http_api;r=c.post('/agency/v1/canonical-reports/versions',json={'artifact':artifact()}).json()
 path=f'/agency/v1/canonical-reports/{r["report_id"]}/versions/1/transitions'
 for target in ('READY_FOR_REVIEW','APPROVED','PUBLISHED'):
  resp=c.post(path,json={'target':target,'actor':'human','artifact_hash':r['artifact_hash']},headers={'X-Scope':'platform_web'})
  assert resp.status_code==200,resp.text
 assert len(s.store.rows)==1
 assert resp.json()['approved_by']=='fixture-human'


def test_http_generation_off_before_db(http_api,monkeypatch):
 c,s=http_api;monkeypatch.setenv('CANONICAL_WEEKLY_REVIEW_ENABLED','false')
 assert c.post('/agency/v1/canonical-reports/versions',json={'artifact':artifact()}).status_code==409
 assert not s.store.rows


def test_shared_http_transport_echo_and_exact_paths():
 import httpx
 r=service().generate(artifact())
 seen=[]
 def handle(req):
  seen.append(str(req.url))
  if req.method=='GET':return httpx.Response(404,json={'detail':'REPORT_NOT_FOUND'})
  return httpx.Response(200,json=r,headers={'X-Request-ID':req.headers['X-Request-ID']})
 shared=types.SimpleNamespace(http=httpx.Client(base_url='https://test.invalid',transport=httpx.MockTransport(handle)),policy=types.SimpleNamespace(timeout_seconds=2))
 api=bridge.AgencyReportAPI(shared)
 assert api.lookup(client='fixture',period={'start':'2026-10-12','end':'2026-10-18'},engine='H7',engine_version='code-sha256',version=1) is None
 assert api.create(artifact())['artifact_hash']==r['artifact_hash']
 assert all('/agency/v1/canonical-reports/' in x for x in seen)
 shared.http.close()

@pytest.mark.parametrize('result',[{'provider_message_id':'synthetic-provider-only'},{'error_code':'PROVIDER_REJECTED'}])
def test_delivery_receipt_outcomes_are_separate_and_idempotent(result):
 s=service();r=published(s);did=str(uuid.uuid4())
 s.prepare_delivery(did,r['report_id'],1,channel='EMAIL',recipient_hash='a'*64,actor='human',at='now')
 a=s.record_delivery_result(did,artifact_hash=r['artifact_hash'],**result)
 b=s.record_delivery_result(did,artifact_hash=r['artifact_hash'],**result)
 assert a==b and a['attempt']==1
 assert a['status']==('DELIVERED' if 'provider_message_id' in result else 'DELIVERY_FAILED')
 assert s.get(r['report_id'],1)['status']=='PUBLISHED'


def test_real_route_auth_rejects_hermes_human_transitions(monkeypatch):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from app.agency_api.v1 import canonical_reports as routes,auth
 monkeypatch.setattr(auth.settings,'AGENCY_API_HERMES_KEY','synthetic-only-hermes-key')
 for k in m.FLAG_NAMES:monkeypatch.setenv(k,'true')
 s=service();monkeypatch.setattr(routes,'service',lambda:s)
 app=FastAPI();app.include_router(routes.router)
 with TestClient(app) as c:
  h={'Authorization':'Bearer synthetic-only-hermes-key'}
  r=c.post('/agency/v1/canonical-reports/versions',json={'artifact':artifact()},headers=h)
  assert r.status_code==200,r.text
  row=r.json()
  assert c.post(f'/agency/v1/canonical-reports/{row["report_id"]}/versions/1/transitions',json={'target':'APPROVED','actor':'human','artifact_hash':row['artifact_hash']},headers=h).status_code==403


def test_published_read_independent_of_generation_flag(http_api,monkeypatch):
 c,s=http_api;r=published(s)
 monkeypatch.setenv('CANONICAL_WEEKLY_REVIEW_ENABLED','false')
 monkeypatch.setenv('CANONICAL_MONTHLY_REVIEW_ENABLED','false')
 response=c.get(f'/agency/v1/canonical-reports/{r["report_id"]}/versions/1')
 assert response.status_code==200 and response.json()['status']=='PUBLISHED'
