"""Read-only campaign period contract; no materialization in a GET."""
from datetime import date
from typing import Any,Literal
from fastapi import Depends,HTTPException,Query
from pydantic import BaseModel,Field,ConfigDict

class PeriodCampaignRow(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    campaign_id:str|None=None
    campaign_name:str|None=None
    status:str|None=None
    campaign_type:str|None=None
    brand_classification:str='UNKNOWN'
    spend:float|None=None
    purchases:float|None=None
    purchase_conversions:float|None=None
    purchase_value:float|None=None
    roas:float|None=None
    cpa:float|None=None
    ecommerce_roas:float|None=None
    ecommerce_cpa:float|None=None

class CampaignPeriodOut(BaseModel):
    schema_version:Literal['campaign-period.v1']='campaign-period.v1'
    client_id:str
    platform:Literal['meta','google']
    period:dict[str,str]
    source_snapshot_id:str|None=None
    source_role:Literal['CORE_CANONICAL']='CORE_CANONICAL'
    grain:Literal['PERIOD_ROLLUP']='PERIOD_ROLLUP'
    source_state:str
    data_validation:Literal['PASS','BLOCKED']
    certification_status:str
    collected_at:str|None=None
    status_as_of:str|None=None
    status_semantics:str|None=None
    rows:list[PeriodCampaignRow]=Field(default_factory=list)
    reconciliation:list[dict]=Field(default_factory=list)
    limitations:list[str]=Field(default_factory=list)
    selection:dict=Field(default_factory=dict)

def register_campaign_period_read(router,*,load_db,load_client_meta,auth_dependency):
    @router.get('/clients/{client_id}/campaign-performance',response_model=CampaignPeriodOut,summary='Exact-period canonical purchase campaign evidence')
    def campaign_performance(client_id:str,period_start:date,period_end:date,
                             platform:Literal['meta','google']=Query(...),auth:Any=Depends(auth_dependency)):
        if period_end<period_start:raise HTTPException(422,detail='invalid period')
        meta=load_client_meta(client_id)
        if not meta or not meta.get('client_id'):raise HTTPException(404,detail='client not found')
        slug=str(meta['client_id']);allowed=getattr(auth,'client_ids',None)
        if allowed is not None and not ({slug,client_id,str(meta.get('id') or '')}&set(map(str,allowed))):
            raise HTTPException(403,detail='client scope denied')
        from ...core.canonical_reports import CoreStore
        from ...core.campaign_period import select_snapshot,read
        try:
            db=load_db();snapshot=select_snapshot(db,slug,period_start,period_end)
            return read(CoreStore(db),slug,platform,period_start,period_end,str(snapshot['id']) if snapshot else None)
        except Exception:raise HTTPException(503,detail='canonical campaign evidence unavailable') from None
