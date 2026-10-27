"""Portable tests of the installed Hermes reader; HTTPX transport is memory-only."""
import importlib.util,sys,uuid,hashlib
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
ROOT=Path(__file__).resolve().parents[3]
sp=importlib.util.spec_from_file_location('hermes_transcript_reader',ROOT/'integrations/hermes/agency_adapter/meeting_io.py');io=importlib.util.module_from_spec(sp);sys.modules[sp.name]=io;sp.loader.exec_module(io)
MID=str(uuid.uuid4());TID=str(uuid.uuid4());RAW='Ana: Vamos verificar o tracking.'
def rows(client='lk-sneakers'):
 m={'id':MID,'client_id':client,'provider':'fireflies','external_id':'TEST_ONLY_PROVIDER_ID','title':'Unit fixture','participants':[],'transcript_status':'ready','processing_status':'pending','created_at':'2026-10-27T12:00:00Z','updated_at':'2026-10-27T12:00:00Z'}
 t={'schema_version':'1.1','meeting_id':MID,'client_id':client,'provider':'fireflies','provider_meeting_id':'TEST_ONLY_PROVIDER_ID','transcript_id':TID,'version':TID,'transcript_hash':hashlib.sha256(RAW.encode()).hexdigest(),'raw_text':RAW,'created_at':'2026-10-27T12:00:00Z','segments':[],'timing_status':'NOT_PERSISTED','provenance':'CORE_PERSISTED_TRANSCRIPT'};return m,t
class Transport(httpx.BaseTransport):
 def __init__(self,m,t,status=200):self.m=m;self.t=t;self.status=status;self.evidence=[];self.client_id='lk-sneakers';self.calls=[];self._inner=httpx.MockTransport(self.response)
 def response(self,r):
  self.calls.append((r.method,r.url.path,dict(r.url.params)));is_transcript=r.url.path.endswith('/transcript');return httpx.Response(self.status if is_transcript else 200,json=self.t if is_transcript else self.m,headers={'X-Request-ID':r.headers['X-Request-ID']})
 def handle_request(self,r):return io.scoped_request(self,r)
def reader(m=None,t=None,status=200):
 base_m,base_t=rows();transport=Transport(m or base_m,t or base_t,status);client=SimpleNamespace(http=httpx.Client(base_url='https://tracking.noroia.com.br',transport=transport),controlled_transport=transport);a=SimpleNamespace(client_id='lk-sneakers',profile=SimpleNamespace(client_id='lk-sneakers',identities=('lk-sneakers',)));a._AgencyAdapter__client=client;return io.MeetingReads(a),transport

def test_scoped_get_hash_and_request_id():
 r,t=reader();out=r.transcript(MID);assert out['transcript']['version']==TID;assert t.calls[-1]==('GET','/agency/v1/meetings/'+MID+'/transcript',{'client_id':'lk-sneakers'});assert out['receipts'][-1]['schema_validation']=='PASS' and out['receipts'][-1]['echo_confirmed']
@pytest.mark.parametrize('case',['wrong_client','wrong_meeting','wrong_provider','wrong_provider_id','wrong_hash','missing_version','title_only'])
def test_foreign_or_invalid_binding_denied(case):
 m,t=rows()
 if case=='wrong_client':t['client_id']='dipua'
 elif case=='wrong_meeting':t['meeting_id']=str(uuid.uuid4())
 elif case=='wrong_provider':t['provider']='manual'
 elif case=='wrong_provider_id':t['provider_meeting_id']='different'
 elif case=='wrong_hash':t['transcript_hash']='bad'
 elif case=='missing_version':t['version']=''
 else:t['raw_text']=m['title'];t['transcript_hash']=hashlib.sha256(t['raw_text'].encode()).hexdigest()
 r,_=reader(m,t)
 with pytest.raises(io.MeetingReadError):r.transcript(MID)
@pytest.mark.parametrize('status',[401,403,404,503])
def test_http_error_has_no_fallback(status):
 r,t=reader(status=status)
 with pytest.raises(io.MeetingReadError,match='HTTP_'+str(status)):r.transcript(MID)
 assert len(t.calls)==2 and all(v[0]=='GET' for v in t.calls)
def test_manual_provider_is_not_reused():
 m,t=rows();m['provider']='manual';r,transport=reader(m,t)
 with pytest.raises(io.MeetingReadError,match='REAL_FIREFLIES'):r.transcript(MID)
 assert len(transport.calls)==1
