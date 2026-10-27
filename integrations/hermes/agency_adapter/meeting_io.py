"""Meeting GET capability on the existing AgencyClient connection. No writer."""
from contextvars import ContextVar
from pathlib import Path
import json,re,uuid,hashlib
from datetime import datetime,timezone
from copy import deepcopy
import jsonschema
CAP=ContextVar('hermes_meeting_read',default=None)
class MeetingReadError(Exception):pass

def scoped_request(transport,request):
 cap=CAP.get()
 if cap is None:return None
 if cap['transport'] is not transport or request.method!='GET' or request.content:raise MeetingReadError('MEETING_READ_ONLY')
 if request.url.scheme!='https' or request.url.host!='tracking.noroia.com.br' or request.url.port not in (443,None):raise MeetingReadError('MEETING_DESTINATION_DENIED')
 if request.url.path!=cap['path'] or list(request.url.params.multi_items())!=cap['query']:raise MeetingReadError('MEETING_SCOPE_DENIED')
 r=transport._inner.handle_request(request)
 cap['receipt'].update(http_status=r.status_code,echo_confirmed=r.headers.get('X-Request-ID')==request.headers.get('X-Request-ID'))
 transport.evidence.append(cap['receipt']);return r

def contract():
 p=Path(__file__).with_name('meeting-contract.json');raw=p.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=p.with_suffix('.sha256').read_text().strip():raise MeetingReadError('MEETING_CONTRACT_CHANGED')
 return json.loads(raw)

class MeetingReads:
 def __init__(self,adapter):
  self.adapter=adapter;self.client=adapter._AgencyAdapter__client;self.profile=adapter.profile;self.receipts=[]
 def get(self,path,*,limit=None):
  slug=self.profile.client_id;listpath='/agency/v1/clients/'+slug+'/meetings'
  if path!=listpath and not re.fullmatch(r'/agency/v1/meetings/[0-9a-f-]{36}(?:/transcript)?',path):raise MeetingReadError('MEETING_ROUTE_DENIED')
  transcript=path.endswith('/transcript')
  query=[('client_id',slug)] if transcript else [] if limit is None else [('limit',str(limit))]
  if limit is not None and (path!=listpath or type(limit) is not int or not 1<=limit<=200):raise MeetingReadError('INVALID_LIMIT')
  rid=str(uuid.uuid4());receipt={'operation':'get_meeting_transcript' if transcript else 'list_meetings' if path==listpath else 'get_meeting','client_id':slug,'endpoint':path,'request_id':rid,'http_status':None,'schema_validation':'NOT_VALIDATED','echo_confirmed':False,'fallback_used':'NO','direct_db_access':'NO','timestamp':datetime.now(timezone.utc).isoformat()}
  token=CAP.set({'transport':self.client.controlled_transport,'path':path,'query':query,'receipt':receipt})
  try:
   r=self.client.http.get(path,params=dict(query),headers={'X-Request-ID':rid,'X-Correlation-ID':rid})
   if r.status_code!=200:raise MeetingReadError('MEETING_HTTP_'+str(r.status_code))
   if not receipt['echo_confirmed']:raise MeetingReadError('X_REQUEST_ID_MISMATCH')
   data=r.json();c=contract();schema={'type':'array','items':{'$ref':'#/components/schemas/MeetingOut'}} if path==listpath else {'$ref':'#/components/schemas/MeetingTranscriptOut' if transcript else '#/components/schemas/MeetingOut'}
   jsonschema.Draft202012Validator(dict(schema,components=c['components']),format_checker=jsonschema.FormatChecker()).validate(data)
   rows=data if isinstance(data,list) else [data]
   if any(row['client_id'] not in self.profile.identities for row in rows):raise MeetingReadError('MEETING_CLIENT_ISOLATION_DENIED')
   for row in rows:
    if row.get('summary') and row['summary']['meeting_id']!=row['id']:raise MeetingReadError('SUMMARY_MEETING_LINK_DENIED')
   receipt['schema_validation']='PASS';self.receipts.append(deepcopy(receipt));return data
  finally:CAP.reset(token)
 def list(self):return self.get('/agency/v1/clients/'+self.profile.client_id+'/meetings',limit=200)
 def detail(self,meeting_id):
  if str(uuid.UUID(meeting_id))!=meeting_id:raise MeetingReadError('CANONICAL_MEETING_ID_REQUIRED')
  return self.get('/agency/v1/meetings/'+meeting_id)

 def transcript(self,meeting_id):
  meeting=self.detail(meeting_id)
  if meeting['provider']!='fireflies' or not meeting.get('external_id'):raise MeetingReadError('REAL_FIREFLIES_MEETING_REQUIRED')
  data=self.get('/agency/v1/meetings/'+meeting_id+'/transcript')
  if data['meeting_id']!=meeting_id or data['provider']!='fireflies' or data['provider_meeting_id']!=meeting['external_id']:raise MeetingReadError('FIREFLIES_TRANSCRIPT_IDENTITY_MISMATCH')
  if hashlib.sha256(data['raw_text'].encode('utf-8')).hexdigest()!=data['transcript_hash']:raise MeetingReadError('TRANSCRIPT_HASH_MISMATCH')
  if not data['raw_text'].strip() or data['raw_text'].strip()==meeting['title'].strip():raise MeetingReadError('REAL_UTTERANCES_REQUIRED_TITLE_IS_NOT_TRANSCRIPT')
  if not data.get('version') or not data.get('transcript_id'):raise MeetingReadError('CANONICAL_TRANSCRIPT_VERSION_REQUIRED')
  return {'meeting':meeting,'transcript':data,'receipts':deepcopy(self.receipts)}
