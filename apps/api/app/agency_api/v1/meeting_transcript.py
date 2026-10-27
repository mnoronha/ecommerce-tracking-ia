"""Canonical, authenticated transcript read. Core storage stays behind Agency API."""
from datetime import datetime
from hashlib import sha256
from typing import Any,Literal
from uuid import UUID
import re
from fastapi import Depends,HTTPException,Query
from pydantic import BaseModel,Field

class TranscriptSegmentOut(BaseModel):
    index:int
    speaker:str|None=None
    text:str
    start_seconds:float|None=None
    end_seconds:float|None=None

class MeetingTranscriptOut(BaseModel):
    schema_version:Literal['1.1']='1.1'
    meeting_id:str
    client_id:str
    provider:str
    provider_meeting_id:str|None=None
    transcript_id:str
    version:str
    transcript_hash:str
    hash_algorithm:Literal['sha256']='sha256'
    raw_text:str
    language:str|None=None
    created_at:datetime
    segments:list[TranscriptSegmentOut]=Field(default_factory=list)
    timing_status:Literal['NOT_PERSISTED']='NOT_PERSISTED'
    provenance:Literal['CORE_PERSISTED_TRANSCRIPT']='CORE_PERSISTED_TRANSCRIPT'

def register_transcript_read(router,*,load_db,load_client_meta,auth_dependency):
    @router.get('/meetings/{meeting_id}/transcript',response_model=MeetingTranscriptOut,
                summary='Read canonical meeting transcript in an explicit client scope',
                openapi_extra={'x-grant-scopes':['agency_admin','hermes_service','platform_web']})
    def read_meeting_transcript(meeting_id:UUID,client_id:str=Query(...,min_length=1),auth:Any=Depends(auth_dependency)):
        # Auth precedes any transcript lookup. Explicit client scope also prevents
        # a service reader from accidentally consuming a foreign meeting ID.
        if getattr(auth,'scope',None) not in ('agency_admin','hermes_service','platform_web'):
            raise HTTPException(403,detail='transcript read scope denied')
        meta=load_client_meta(client_id)
        if not meta or not meta.get('client_id'):
            raise HTTPException(404,detail='meeting transcript not found')
        slug=str(meta['client_id']);identities={slug,str(meta.get('id') or ''),client_id}
        allowed=getattr(auth,'client_ids',None)
        if allowed is not None and not (identities & set(map(str,allowed))):
            raise HTTPException(403,detail='client scope denied')
        db=load_db();mid=str(meeting_id)
        try:
            result=db.table('core_meetings').select('id,client_id,provider,external_id').eq('id',mid).limit(1).execute()
        except Exception:
            raise HTTPException(503,detail='meeting transcript unavailable') from None
        rows=result.data or []
        if not rows or str(rows[0].get('client_id')) not in identities:
            raise HTTPException(404,detail='meeting transcript not found')
        meeting=rows[0]
        try:
            result=(db.table('core_meeting_transcripts').select('id,meeting_id,raw_text,language,created_at')
                    .eq('meeting_id',mid).order('created_at',desc=True).order('id',desc=True).limit(1).execute())
        except Exception:
            raise HTTPException(503,detail='meeting transcript unavailable') from None
        transcripts=result.data or []
        if not transcripts or not isinstance(transcripts[0].get('raw_text'),str) or not transcripts[0]['raw_text'].strip():
            raise HTTPException(404,detail='meeting transcript not found')
        transcript=transcripts[0]
        if str(transcript.get('meeting_id'))!=mid:
            raise HTTPException(503,detail='meeting transcript linkage invalid')
        raw=transcript['raw_text'];segments=[]
        # Fireflies ingestion currently persists speaker-labelled text but drops
        # provider timings. Preserve labels literally; never invent timestamps.
        for i,line in enumerate(raw.splitlines()):
            if not line.strip():continue
            match=re.fullmatch(r'([^:\n]{1,120}): (.+)',line)
            segments.append(TranscriptSegmentOut(index=i,speaker=match[1] if match else None,text=match[2] if match else line))
        return MeetingTranscriptOut(meeting_id=mid,client_id=slug,provider=str(meeting['provider']),
            provider_meeting_id=meeting.get('external_id'),transcript_id=str(transcript['id']),version=str(transcript['id']),
            transcript_hash=sha256(raw.encode('utf-8')).hexdigest(),raw_text=raw,language=transcript.get('language'),
            created_at=transcript['created_at'],segments=segments)
