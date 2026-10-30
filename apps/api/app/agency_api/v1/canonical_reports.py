"""Canonical report API; no calls into legacy publication or transport code."""
from datetime import datetime,timezone
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal,Optional
from .auth import SCOPE_READ_ANY,SCOPE_HERMES_WRITE,SCOPE_PLATFORM_WRITE,AuthContext
from ...core.canonical_reports import Reports,CoreStore,Flags,ReportError
router=APIRouter(prefix='/agency/v1/canonical-reports',tags=['canonical-reports'])

def service():
 from ...database import get_supabase
 return Reports(CoreStore(get_supabase()))

def fail(exc):raise HTTPException(404 if str(exc)=='REPORT_NOT_FOUND' else 409,detail=str(exc))

def scope(auth,client):
 if auth.client_ids is not None and client not in auth.client_ids:raise HTTPException(403,'CLIENT_SCOPE_DENIED')

class Artifact(BaseModel):
 model_config=ConfigDict(extra='forbid')
 client_id:str=Field(min_length=1)
 canonical_engine:Literal['H7','H8']
 engine_version:str=Field(min_length=1)
 version:int=Field(ge=1)
 generated_at:str
 period_start:str
 period_end:str
 source_state:list[dict]
 certification_status:list[dict]
 review_status:Literal['READY','PARTIAL','COMPLETE']
 content:dict
 evidence_refs:list[dict|str]=Field(min_length=1)
class ReportVersion(Artifact):
 report_id:str
 report_type:Literal['weekly','monthly']
 artifact_hash:str
 idempotency_key:str
 status:Literal['DRAFT','READY_FOR_REVIEW','APPROVED','PUBLISHED']
 approved_at:str|None=None
 approved_by:str|None=None
 published_at:str|None=None
 approval_hash:str|None=None
 analysis_request_id:str|None=None
class Generate(BaseModel):
 artifact:Artifact
class Transition(BaseModel):
 target:str
 actor:str=Field(min_length=1)
 artifact_hash:str=Field(pattern='^[a-f0-9]{64}$')
class Delivery(BaseModel):
 delivery_id:str
 report_id:str
 report_version:int=Field(ge=1)
 channel:str
 recipient_hash:str=Field(pattern='^[a-f0-9]{64}$')
 actor:str=Field(min_length=1)
class DeliveryExecute(BaseModel):
 recipient:str=Field(min_length=3)
 actor:str=Field(min_length=1)
class RecipientIn(BaseModel):
 model_config=ConfigDict(extra='forbid')
 id:Optional[str]=None
 name:str=Field(min_length=1,max_length=120)
 email:str=Field(min_length=3,max_length=254)
 active:bool=True
class DeliverySettingsIn(BaseModel):
 model_config=ConfigDict(extra='forbid')
 weekly_enabled:bool=False
 monthly_enabled:bool=False
 weekly_day_of_week:int=Field(default=0,ge=0,le=6)
 weekly_hour:int=Field(default=8,ge=0,le=23)
 monthly_day_of_month:int=Field(default=1,ge=1,le=28)
 monthly_hour:int=Field(default=8,ge=0,le=23)
 report_timezone:str='America/Sao_Paulo'
 recipients:list[RecipientIn]=[]
class HermesAnalysis(BaseModel):
 model_config=ConfigDict(extra='forbid')
 analysis_request_id:str=Field(min_length=1)
 report_version:int=Field(ge=1)
 executive_summary:dict
 channel_analysis:dict
 performance_drilldown:Optional[dict]=None
 diagnosis:list[dict]
 recommendations:list[dict]
 priorities:list[dict]
 limitations:Optional[str]=None
 change_digest_section:Optional[dict]=None
 monthly_comparison:Optional[dict]=None
 trends:Optional[dict]=None
 actions:Optional[dict]=None
 outcomes:Optional[dict]=None
 learnings:Optional[dict]=None
 next_month_priorities:Optional[list]=None
 analysis_quality:Optional[float]=Field(None,ge=0.0,le=10.0)
 analysis_engine_metadata:Optional[dict]=None
_ARTIFACT_HASH_EXCLUDE=frozenset({'report_id','report_type','artifact_hash','idempotency_key','status','approved_at','approved_by','published_at','approval_hash','generated_at','superseded_at','superseded_by_actor','superseded_by_version'})

@router.get('/list',tags=['canonical-reports'])
def list_canonical_reports(
 client_id:str=Query(...,description='Client slug or pixel_id'),
 status:Optional[str]=Query(None,description='DRAFT|READY_FOR_REVIEW|APPROVED|PUBLISHED|SUPERSEDED'),
 report_type:Optional[str]=Query(None,description='weekly|monthly'),
 limit:int=Query(30,ge=1,le=100),
 auth:AuthContext=Depends(SCOPE_READ_ANY),
)->list[dict]:
 from ...database import get_supabase
 from ..v1.router import _resolve_slug
 slug=_resolve_slug(client_id) or client_id
 scope(auth,slug)
 sb=get_supabase()
 try:
  rows=sb.table('core_canonical_report_records').select('entity_key,document').contains('document',{'client_id':slug}).execute().data or []
 except Exception as exc:
  raise HTTPException(503,'database error')
 out=[]
 for r in rows:
  doc=r.get('document') or {}
  if status and doc.get('status')!=status.upper():continue
  if report_type and doc.get('report_type')!=report_type.lower():continue
  out.append(doc)
 out.sort(key=lambda d:(d.get('period_start',''),d.get('version',1)),reverse=True)
 return out[:limit]

@router.get('/delivery-settings',tags=['canonical-reports'])
def get_delivery_settings(client_id:str=Query(...),auth:AuthContext=Depends(SCOPE_READ_ANY))->dict:
 from ...database import get_supabase
 scope(auth,client_id)
 sb=get_supabase()
 try:rows=sb.table('core_report_delivery_settings').select('*').eq('client_id',client_id).limit(1).execute().data or []
 except Exception:raise HTTPException(503,'database error')
 _timing_defaults={'weekly_day_of_week':0,'weekly_hour':8,'monthly_day_of_month':1,'monthly_hour':8,'report_timezone':'America/Sao_Paulo'}
 if not rows:return{'client_id':client_id,'weekly_enabled':False,'monthly_enabled':False,'human_approval_required':True,'recipients':[],'updated_at':None,**_timing_defaults}
 r=rows[0];return{'client_id':r['client_id'],'weekly_enabled':r['weekly_enabled'],'monthly_enabled':r['monthly_enabled'],'human_approval_required':True,'recipients':r.get('recipients') or [],'weekly_day_of_week':r.get('weekly_day_of_week',0),'weekly_hour':r.get('weekly_hour',8),'monthly_day_of_month':r.get('monthly_day_of_month',1),'monthly_hour':r.get('monthly_hour',8),'report_timezone':r.get('report_timezone','America/Sao_Paulo'),'updated_at':r.get('updated_at')}

@router.put('/delivery-settings',tags=['canonical-reports'])
def put_delivery_settings(client_id:str=Query(...),body:DeliverySettingsIn=...,auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE))->dict:
 import uuid as _uuid
 from datetime import datetime as _dt,timezone as _tz
 from ...database import get_supabase
 scope(auth,client_id)
 sb=get_supabase()
 now=_dt.now(_tz.utc).isoformat()
 try:
  existing=sb.table('core_report_delivery_settings').select('id,recipients').eq('client_id',client_id).limit(1).execute().data or []
  old_by_id={r['id']:r for r in(existing[0].get('recipients') or [])}if existing else{}
  recs=[]
  for ri in body.recipients:
   rid=ri.id or str(_uuid.uuid4())
   old=old_by_id.get(rid,{})
   recs.append({'id':rid,'name':ri.name.strip(),'email':ri.email.strip().lower(),'active':ri.active,'created_at':old.get('created_at',now),'updated_at':now})
  payload={'weekly_enabled':body.weekly_enabled,'monthly_enabled':body.monthly_enabled,'human_approval_required':True,'recipients':recs,'weekly_day_of_week':body.weekly_day_of_week,'weekly_hour':body.weekly_hour,'monthly_day_of_month':body.monthly_day_of_month,'monthly_hour':body.monthly_hour,'report_timezone':body.report_timezone,'updated_at':now}
  if existing:sb.table('core_report_delivery_settings').update(payload).eq('client_id',client_id).execute()
  else:sb.table('core_report_delivery_settings').insert({**payload,'client_id':client_id}).execute()
 except Exception as exc:raise HTTPException(503,f'database error:{str(exc)[:80]}')
 return{'client_id':client_id,**payload}

@router.post('/versions',response_model=ReportVersion)
def generate(body:Generate,auth:AuthContext=Depends(SCOPE_HERMES_WRITE)):
 artifact=body.artifact.model_dump()
 scope(auth,artifact['client_id'])
 engine=artifact['canonical_engine'];flags=Flags.environment()
 enabled=flags.CANONICAL_WEEKLY_REVIEW_ENABLED if engine=='H7' else flags.CANONICAL_MONTHLY_REVIEW_ENABLED if engine=='H8' else False
 if not enabled:raise HTTPException(409,'GENERATION_DISABLED')
 try:return service().generate(artifact)
 except (ReportError,ValueError,TypeError) as e:fail(e)

@router.get('/{report_id}/versions/{version}',response_model=ReportVersion)
def read(report_id:str,version:int,auth:AuthContext=Depends(SCOPE_READ_ANY)):
 flags=Flags.environment()
 if not (flags.CANONICAL_WEEKLY_REVIEW_ENABLED or flags.CANONICAL_MONTHLY_REVIEW_ENABLED or flags.REPORT_PUBLISH_ENABLED or flags.REPORT_DELIVERY_ENABLED):raise HTTPException(404,'CANONICAL_REPORTS_DISABLED')
 try:r=service().get(report_id,version)
 except ReportError as e:fail(e)
 scope(auth,r['client_id']);return r

@router.post('/{report_id}/versions/{version}/transitions',response_model=ReportVersion)
def transition(report_id:str,version:int,body:Transition,auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE)):
 flags=Flags.environment()
 if not any(getattr(flags,k) for k in ('CANONICAL_WEEKLY_REVIEW_ENABLED','CANONICAL_MONTHLY_REVIEW_ENABLED','REPORT_PUBLISH_ENABLED')):raise HTTPException(409,'CANONICAL_REPORTS_DISABLED')
 if body.target=='PUBLISHED' and not Flags.environment().REPORT_PUBLISH_ENABLED:raise HTTPException(409,'PUBLICATION_DISABLED')
 s=service()
 try:
  scope(auth,s.get(report_id,version)['client_id'])
  return s.transition(report_id,version,body.target,actor=auth.subject or body.actor,at=datetime.now(timezone.utc).isoformat(),expected_hash=body.artifact_hash)
 except ReportError as e:fail(e)

@router.get('/{report_id}/deliveries',tags=['canonical-reports'])
def list_report_deliveries(
 report_id:str,
 auth:AuthContext=Depends(SCOPE_READ_ANY),
)->list[dict]:
 from ...database import get_supabase
 sb=get_supabase()
 try:
  rows=sb.table('core_canonical_report_records').select('entity_key,document').like('entity_key','delivery:%').contains('document',{'report_id':report_id}).execute().data or []
 except Exception:
  raise HTTPException(503,'database error')
 out=[]
 for r in rows:
  doc=r.get('document') or {}
  try:scope(auth,doc.get('client_id',''))
  except HTTPException:continue
  # Never expose recipient plaintext; hash is already stored
  out.append({k:v for k,v in doc.items() if k!='recipient'})
 out.sort(key=lambda d:d.get('requested_at',''),reverse=True)
 return out

@router.get('/{report_id}/artifact',tags=['canonical-reports'])
def get_artifact(report_id:str,version:int=Query(1,ge=1),auth:AuthContext=Depends(SCOPE_READ_ANY))->dict:
 flags=Flags.environment()
 if not any(getattr(flags,k) for k in('CANONICAL_WEEKLY_REVIEW_ENABLED','CANONICAL_MONTHLY_REVIEW_ENABLED','REPORT_PUBLISH_ENABLED','REPORT_DELIVERY_ENABLED')):raise HTTPException(404,'CANONICAL_REPORTS_DISABLED')
 try:doc=service().get(report_id,version)
 except ReportError as e:fail(e)
 scope(auth,doc['client_id'])
 from ...core.canonical_reports import digest as _digest
 check={k:v for k,v in doc.items() if k not in _ARTIFACT_HASH_EXCLUDE}
 if _digest(check)!=doc.get('artifact_hash'):raise HTTPException(409,'ARTIFACT_INTEGRITY_ERROR')
 return{'report':doc,'integrity':'VERIFIED','download_note':'SUPERSEDED' if doc.get('status')=='SUPERSEDED' else None}

@router.post('/delivery-receipts')
def prepare_delivery(body:Delivery,auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE)):
 if not Flags.environment().REPORT_DELIVERY_ENABLED:raise HTTPException(409,'DELIVERY_DISABLED')
 s=service()
 try:
  scope(auth,s.get(body.report_id,body.report_version)['client_id'])
  return s.prepare_delivery(body.delivery_id,body.report_id,body.report_version,channel=body.channel,recipient_hash=body.recipient_hash,actor=auth.subject or body.actor,at=datetime.now(timezone.utc).isoformat())
 except ReportError as e:fail(e)

@router.post('/pilot-run',tags=['canonical-reports'],summary='Manual H7/H8 generation — bypasses env flags, agency admin only')
def pilot_run(
 client_id:str=Query(...,description='Client slug'),
 report_type:str=Query(...,pattern='^(weekly|monthly)$'),
 period_start:Optional[str]=Query(None,description='YYYY-MM-DD override'),
 period_end:Optional[str]=Query(None,description='YYYY-MM-DD override'),
 version:Optional[int]=Query(None,ge=1,description='Artifact version; auto-increments past existing immutable records if omitted'),
 auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE),
)->dict:
 from ...database import get_supabase
 from ...core.canonical_reports import CoreStore,Flags,Reports,ReportError
 from datetime import date,timedelta as _td
 scope(auth,client_id)
 sb=get_supabase()
 today=date.today()
 if period_start and period_end:
  ps=date.fromisoformat(period_start);pe=date.fromisoformat(period_end)
 elif report_type=='weekly':
  pe=today-_td(days=today.weekday()+1);ps=pe-_td(days=6)
 else:
  first=date(today.year,today.month,1);pe=first-_td(days=1);ps=date(pe.year,pe.month,1)
 if report_type=='weekly':
  from ...core.canonical_weekly_scheduler import _build_artifact as _ba
 else:
  from ...core.canonical_monthly_scheduler import _build_artifact as _ba
 pilot_flags=Flags(CANONICAL_WEEKLY_REVIEW_ENABLED=True,CANONICAL_MONTHLY_REVIEW_ENABLED=True,REPORT_PUBLISH_ENABLED=False,REPORT_DELIVERY_ENABLED=False)
 rpts=Reports(CoreStore(sb),flags=pilot_flags)
 # Try specified version; if omitted, start at 1 and auto-increment past immutable conflicts.
 start_v=version or 1;max_v=start_v+19
 for v in range(start_v,max_v+1):
  artifact=_ba(client_id,ps,pe,sb,version=v)
  try:
   doc=rpts.generate(artifact);break
  except ReportError as exc:
   s=str(exc)
   if 'IMMUTABLE_VERSION_CONFLICT' in s:
    if version is not None or v==max_v:return{'status':'ALREADY_EXISTS','client_id':client_id,'report_type':report_type,'period_start':str(ps),'period_end':str(pe),'version':v}
    continue
   raise HTTPException(409,s)
 import uuid as _uuid
 from ...core.analysis_layer import build_hermes_handoff_from_artifact as _build_handoff
 ver=doc['version']
 if doc['status']=='DRAFT':
  analysis_status=doc.get('content',{}).get('analysis_layer',{}).get('status')
  if analysis_status=='WAITING_FOR_HERMES_ANALYSIS':
   analysis_request_id=str(_uuid.uuid5(_uuid.NAMESPACE_URL,f'analysis-request:{doc["report_id"]}:v{ver}'))
   handoff=_build_handoff(analysis_request_id,doc['report_id'],ver,doc,period_type=report_type)
   rpts.write_analysis_request(analysis_request_id,handoff)
   return{'client_id':client_id,'report_type':report_type,'report_id':doc['report_id'],'version':ver,'status':'WAITING_FOR_HERMES_ANALYSIS','analysis_request_id':analysis_request_id,'period_start':str(ps),'period_end':str(pe)}
  try:
   doc=rpts.transition(doc['report_id'],ver,'READY_FOR_REVIEW',actor='canonical_pilot_run',at=datetime.now(timezone.utc).isoformat(),expected_hash=doc['artifact_hash'])
  except ReportError:pass
 return{'client_id':client_id,'report_type':report_type,'report_id':doc['report_id'],'version':ver,'status':doc['status'],'period_start':str(ps),'period_end':str(pe)}

@router.get('/analysis-requests/pending',tags=['canonical-reports'],summary='List pending Hermes analysis requests — polled by Hermes worker')
def list_pending_analysis_requests(
 client_id:Optional[str]=Query(None),
 limit:int=Query(50,ge=1,le=200),
 auth:AuthContext=Depends(SCOPE_HERMES_WRITE),
)->list[dict]:
 from ...database import get_supabase
 sb=get_supabase()
 try:
  rows=(sb.table('core_canonical_report_records')
   .select('entity_key,document')
   .like('entity_key','analysis-request:%')
   .contains('document',{'status':'PENDING'})
   .order('entity_key',desc=False)
   .limit(limit)
   .execute()).data or []
 except Exception as exc:
  raise HTTPException(503,f'database error:{str(exc)[:80]}')
 out=[]
 for row in rows:
  doc=row.get('document') or {}
  if client_id and doc.get('client_id')!=client_id:continue
  if auth.client_ids is not None and doc.get('client_id') not in auth.client_ids:continue
  out.append({'analysis_request_id':doc.get('analysis_request_id'),'report_id':doc.get('report_id'),'client_id':doc.get('client_id'),'report_type':doc.get('report_type'),'report_version':doc.get('report_version'),'period_start':doc.get('period_start'),'period_end':doc.get('period_end'),'created_at':doc.get('requested_at')})
 return out

@router.post('/{report_id}/analysis',tags=['canonical-reports'],summary='Hermes analysis callback — posts structured analysis for a WAITING report')
def post_analysis(report_id:str,body:HermesAnalysis,auth:AuthContext=Depends(SCOPE_HERMES_WRITE))->dict:
 import uuid as _uuid
 from ...core.analysis_layer import validate_hermes_response
 s=service()
 try:
  base=s.get(report_id,body.report_version)
 except ReportError as e:fail(e)
 scope(auth,base['client_id'])
 analysis_status=base.get('content',{}).get('analysis_layer',{}).get('status')
 if analysis_status not in ('WAITING_FOR_HERMES_ANALYSIS',):
  raise HTTPException(409,f'REPORT_NOT_WAITING_FOR_ANALYSIS:{analysis_status}')
 try:
  validate_hermes_response(body.model_dump())
 except ValueError as e:
  raise HTTPException(422,f'INVALID_HERMES_PAYLOAD:{str(e)[:200]}')
 try:
  doc=s.apply_analysis(
   report_id,body.report_version,body.model_dump(),body.analysis_request_id,
   actor='canonical_analysis_callback',at=datetime.now(timezone.utc).isoformat(),
  )
 except ReportError as e:fail(e)
 return{'report_id':report_id,'version':doc['version'],'status':doc['status'],'analysis_request_id':body.analysis_request_id}

@router.get('/{report_id}/analysis-request',tags=['canonical-reports'],summary='Get pending Hermes handoff contract for a report')
def get_analysis_request(report_id:str,version:int=Query(1,ge=1),auth:AuthContext=Depends(SCOPE_READ_ANY))->dict:
 import uuid as _uuid
 s=service()
 try:
  base=s.get(report_id,version)
 except ReportError as e:fail(e)
 scope(auth,base['client_id'])
 analysis_request_id=str(_uuid.uuid5(_uuid.NAMESPACE_URL,f'analysis-request:{report_id}:v{version}'))
 try:
  return s.get_analysis_request(analysis_request_id)
 except ReportError as e:
  raise HTTPException(404,str(e))

@router.post('/delivery-receipts/{delivery_id}/send')
def send_delivery(delivery_id:str,body:DeliveryExecute,auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE)):
 if not Flags.environment().REPORT_DELIVERY_ENABLED:raise HTTPException(409,'DELIVERY_DISABLED')
 from ...core.report_email_transport import dispatch_delivery
 s=service()
 try:
  receipt=s.get_delivery(delivery_id)
  scope(auth,receipt['client_id'])
  result=dispatch_delivery(delivery_id,body.recipient,s)
  if result['status']=='DELIVERY_FAILED':
   raise HTTPException(409,f"DELIVERY_FAILED:{result.get('error','TRANSPORT_UNAVAILABLE')}")
  return result
 except ReportError as e:
  err=str(e)
  if err=='DELIVERY_NOT_FOUND':raise HTTPException(404,err)
  raise HTTPException(409,err)
