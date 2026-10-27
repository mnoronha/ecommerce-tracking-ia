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

@router.post('/delivery-receipts')
def prepare_delivery(body:Delivery,auth:AuthContext=Depends(SCOPE_PLATFORM_WRITE)):
 if not Flags.environment().REPORT_DELIVERY_ENABLED:raise HTTPException(409,'DELIVERY_DISABLED')
 s=service()
 try:
  scope(auth,s.get(body.report_id,body.report_version)['client_id'])
  return s.prepare_delivery(body.delivery_id,body.report_id,body.report_version,channel=body.channel,recipient_hash=body.recipient_hash,actor=auth.subject or body.actor,at=datetime.now(timezone.utc).isoformat())
 except ReportError as e:fail(e)
