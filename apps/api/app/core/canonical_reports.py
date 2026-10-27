"""Canonical report lifecycle. No generation engine, publisher or send imports.
Core owns this store. Hermes accesses it only through Agency API.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json,os,uuid

FLAG_NAMES=('CANONICAL_WEEKLY_REVIEW_ENABLED','CANONICAL_MONTHLY_REVIEW_ENABLED','REPORT_PUBLISH_ENABLED','REPORT_DELIVERY_ENABLED')
LIFECYCLE={'DRAFT':{'READY_FOR_REVIEW'},'READY_FOR_REVIEW':{'APPROVED','SUPERSEDED'},'APPROVED':{'PUBLISHED','SUPERSEDED'},'PUBLISHED':{'SUPERSEDED'},'SUPERSEDED':set()}
DELIVERY_STATES=('DELIVERY_PENDING','DELIVERED','DELIVERY_FAILED')
class ReportError(ValueError):pass

def digest(value):return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class Flags:
 CANONICAL_WEEKLY_REVIEW_ENABLED:bool=False
 CANONICAL_MONTHLY_REVIEW_ENABLED:bool=False
 REPORT_PUBLISH_ENABLED:bool=False
 REPORT_DELIVERY_ENABLED:bool=False
 @classmethod
 def environment(cls):return cls(**{k:os.getenv(k,'false').strip().lower()=='true' for k in FLAG_NAMES})

class Reports:
 def __init__(self,store,flags=None):self.store=store;self.flags=flags or Flags.environment()
 def get(self,report_id,version):
  row=self.store.get(f'report:{report_id}:v{version}')
  if row is None:raise ReportError('REPORT_NOT_FOUND')
  return deepcopy(row['document'])
 def mutate(self,key,make):
  for _ in range(8):
   old=self.store.get(key); new=make(deepcopy(old['document']) if old else None)
   if old and new==old['document']:return new
   if self.store.cas(key,old['revision'] if old else None,new):return deepcopy(new)
  raise ReportError('CONCURRENT_UPDATE_RETRY_REQUIRED')
 def generate(self,artifact):
  a=deepcopy(artifact);engine=a.get('canonical_engine')
  if engine not in ('H7','H8'):raise ReportError('CANONICAL_ENGINE_REQUIRED')
  if not getattr(self.flags,'CANONICAL_WEEKLY_REVIEW_ENABLED' if engine=='H7' else 'CANONICAL_MONTHLY_REVIEW_ENABLED'):raise ReportError('GENERATION_DISABLED')
  required=('client_id','period_start','period_end','version','generated_at','engine_version','source_state','certification_status','review_status','content','evidence_refs')
  if any(k not in a for k in required):raise ReportError('ARTIFACT_FIELDS_REQUIRED')
  from datetime import date,datetime
  start,end=date.fromisoformat(a['period_start']),date.fromisoformat(a['period_end'])
  if end<start or datetime.fromisoformat(a['generated_at'].replace('Z','+00:00')).tzinfo is None:raise ReportError('INVALID_PERIOD_OR_CLOCK')
  if type(a['version']) is not int or a['version']<1 or not a['client_id'] or not a['engine_version']:raise ReportError('INVALID_VERSION_OR_IDENTITY')
  if end>=datetime.fromisoformat(a['generated_at'].replace('Z','+00:00')).date():raise ReportError('PERIOD_NOT_CLOSED')
  if engine=='H7' and ((end-start).days!=6 or start.weekday()!=0 or end.weekday()!=6):raise ReportError('WEEKLY_PERIOD_REQUIRED')
  if engine=='H8':
   import calendar
   if start.day!=1 or start.year!=end.year or start.month!=end.month or end.day!=calendar.monthrange(start.year,start.month)[1]:raise ReportError('CLOSED_MONTH_REQUIRED')
  if a['review_status']=='BLOCKED' or not a['content'] or not a['evidence_refs']:raise ReportError('REVIEW_NOT_READY')
  c=a['content']
  if not isinstance(c,dict) or c.get('client_id')!=a['client_id'] or c.get('analysis_period')!={'start':a['period_start'],'end':a['period_end']}:raise ReportError('ARTIFACT_SCOPE_MISMATCH')
  if c.get('status')=='BLOCKED' or not any(c.get(k) for k in ('facts','channels','business_truth')):raise ReportError('EMPTY_OR_PLACEHOLDER_CONTENT')
  if 'narrative pending hermes' in json.dumps(c).lower():raise ReportError('PLACEHOLDER_FORBIDDEN')
  typ='weekly' if engine=='H7' else 'monthly'
  rid=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps([a['client_id'],typ,a['period_start'],a['period_end']])))
  key=f'report:{rid}:v{a["version"]}'
  # Hash excludes generation timestamp: retries reuse the first immutable capture.
  ah=digest({k:v for k,v in a.items() if k!='generated_at'})
  ik=digest([a['client_id'],typ,a['period_start'],a['period_end'],a['engine_version'],a['version']])
  doc={**a,'report_id':rid,'report_type':typ,'artifact_hash':ah,'idempotency_key':ik,'status':'DRAFT','approved_at':None,'approved_by':None,'published_at':None}
  def create(old):
   if old:
    if old['artifact_hash']!=ah or old['idempotency_key']!=ik:raise ReportError('IMMUTABLE_VERSION_CONFLICT')
    return old
   return doc
  return self.mutate(key,create)
 def transition(self,report_id,version,target,*,actor,at,expected_hash,superseded_by_version=None):
  if not actor or actor.strip().lower() in ('hermes','hermes_service','platform_web','agency_admin','system','scheduler'):raise ReportError('HUMAN_ACTOR_REQUIRED')
  if target=='PUBLISHED' and not self.flags.REPORT_PUBLISH_ENABLED:raise ReportError('PUBLICATION_DISABLED')
  def change(old):
   if old is None:raise ReportError('REPORT_NOT_FOUND')
   if old['artifact_hash']!=expected_hash:raise ReportError('APPROVAL_HASH_MISMATCH')
   if old['status']=='SUPERSEDED':raise ReportError('INVALID_TRANSITION')
   if old['status']==target:return old
   if target not in LIFECYCLE.get(old['status'],set()):raise ReportError('INVALID_TRANSITION')
   old['status']=target
   if target=='APPROVED':old.update(approved_by=actor,approved_at=at,approval_hash=expected_hash)
   if target=='PUBLISHED':
    if old.get('approval_hash')!=old['artifact_hash']:raise ReportError('APPROVED_VERSION_REQUIRED')
    old['published_at']=at
   if target=='SUPERSEDED':
    old.update(superseded_at=at,superseded_by_actor=actor)
    if superseded_by_version is not None:old['superseded_by_version']=superseded_by_version
   return old
  return self.mutate(f'report:{report_id}:v{version}',change)
 def prepare_delivery(self,delivery_id,report_id,version,*,channel,recipient_hash,actor,at):
  # Preparation is explicit and gated; no network transport exists in this module.
  if not self.flags.REPORT_DELIVERY_ENABLED:raise ReportError('DELIVERY_DISABLED')
  uuid.UUID(delivery_id)
  if not actor or channel not in ('EMAIL','WHATSAPP'):raise ReportError('INVALID_DELIVERY_REQUEST')
  if len(recipient_hash)!=64 or any(c not in '0123456789abcdef' for c in recipient_hash):raise ReportError('HASHED_RECIPIENT_REQUIRED')
  report=self.get(report_id,version)
  if report['status']!='PUBLISHED':raise ReportError('EXACT_PUBLISHED_VERSION_REQUIRED')
  binding={'report_id':report_id,'report_version':version,'artifact_hash':report['artifact_hash'],'client_id':report['client_id'],'channel':channel,'recipient':recipient_hash}
  doc={**binding,'delivery_id':delivery_id,'status':'DELIVERY_PENDING','attempt':0,'sent_at':None,'provider_message_id':None,'error':None,'requested_by':actor,'requested_at':at}
  def create(old):
   if old:
    if any(old.get(k)!=v for k,v in binding.items()):raise ReportError('DELIVERY_ID_CONFLICT')
    return old
   return doc
  return self.mutate('delivery:'+delivery_id,create)
 def get_delivery(self,delivery_id):
  row=self.store.get('delivery:'+delivery_id)
  if row is None:raise ReportError('DELIVERY_NOT_FOUND')
  return deepcopy(row['document'])
 def record_delivery_result(self,delivery_id,*,artifact_hash,provider_message_id=None,error_code=None):
  # Trusted future Core worker only. Not an API action, approval or a send.
  if bool(provider_message_id)==bool(error_code):raise ReportError('EXACT_PROVIDER_RESULT_REQUIRED')
  if error_code and error_code not in ('PROVIDER_REJECTED','TIMEOUT_REQUIRES_RECONCILIATION','TRANSPORT_UNAVAILABLE'):raise ReportError('SANITIZED_ERROR_CODE_REQUIRED')
  def finish(old):
   if old is None:raise ReportError('DELIVERY_NOT_FOUND')
   if old['artifact_hash']!=artifact_hash:raise ReportError('DELIVERY_HASH_MISMATCH')
   state='DELIVERED' if provider_message_id else 'DELIVERY_FAILED'
   if old['status']!='DELIVERY_PENDING':
    if old['status']==state and old.get('provider_message_id')==provider_message_id and old.get('error')==error_code:return old
    raise ReportError('DELIVERY_RESULT_CONFLICT')
   from datetime import datetime,timezone
   old.update(status=state,attempt=old.get('attempt',0)+1,provider_message_id=provider_message_id,error=error_code,
    sent_at=datetime.now(timezone.utc).isoformat() if provider_message_id else None)
   return old
  return self.mutate('delivery:'+delivery_id,finish)
 def retry_delivery(self,delivery_id):
  # Reset DELIVERY_FAILED → DELIVERY_PENDING for explicit human retry via /send.
  def reset(old):
   if old is None:raise ReportError('DELIVERY_NOT_FOUND')
   if old['status']=='DELIVERED':raise ReportError('DELIVERY_ALREADY_COMPLETED')
   if old['status']!='DELIVERY_FAILED':raise ReportError('DELIVERY_NOT_FAILED')
   old.update(status='DELIVERY_PENDING',error=None,provider_message_id=None,sent_at=None)
   return old
  return self.mutate('delivery:'+delivery_id,reset)
 def dispatch(self,*args,**kwargs):raise ReportError('NO_DELIVERY_TRANSPORT_INSTALLED')

class CoreStore:
 """Used only in Core process; atomic CAS RPC, never exposed directly to Hermes."""
 def __init__(self,db):self.db=db
 def get(self,key):
  rows=self.db.table('core_canonical_report_records').select('revision,document').eq('entity_key',key).limit(1).execute().data or []
  return rows[0] if rows else None
 def cas(self,key,revision,document):
  value=self.db.rpc('canonical_report_cas',{'p_key':key,'p_revision':revision,'p_document':document}).execute().data
  return value is True
