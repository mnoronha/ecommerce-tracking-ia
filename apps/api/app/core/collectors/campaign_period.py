"""Opt-in exact-period Core provider reads. No platform mutations or credential logs."""
from datetime import datetime, timezone
import json
import httpx
from ...config import settings
from ...services.spend_sync import _get_google_token

def _google_pages(url,headers,query):
    rows=[];token=None;seen=set()
    for _ in range(1000):
        body={'query':query}
        if token:body['pageToken']=token
        response=httpx.post(url,headers=headers,json=body,timeout=40)
        response.raise_for_status();data=response.json();rows.extend(data.get('results') or [])
        token=data.get('nextPageToken')
        if not token:return rows
        if token in seen:raise ValueError('REPEATED_PAGE_TOKEN')
        seen.add(token)
    raise ValueError('PAGE_LIMIT')

def fetch_google(c,start,end):
    customer=c.get('google_ads_customer_id');refresh=c.get('google_ads_refresh_token')
    if not customer or not refresh:return [],False
    token=_get_google_token(refresh)
    if not token:return [],False
    headers={'Authorization':'Bearer '+token,'developer-token':settings.GOOGLE_ADS_DEVELOPER_TOKEN}
    manager=c.get('google_ads_login_customer_id')
    if manager:headers['login-customer-id']=manager.replace('-','').replace(' ','')
    url='https://googleads.googleapis.com/v23/customers/'+customer.replace('-','').replace(' ','')+'/googleAds:search'
    window=f"WHERE segments.date BETWEEN '{start}' AND '{end}'"
    base=_google_pages(url,headers,'SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, metrics.cost_micros FROM campaign '+window)
    purchase=_google_pages(url,headers,'SELECT campaign.id, campaign.status, segments.conversion_action_category, metrics.conversions, metrics.conversions_value FROM campaign '+window)
    purchases={}
    for row in purchase:
        if row.get('segments',{}).get('conversionActionCategory')!='PURCHASE':continue
        cid=str(row['campaign']['id']);m=row.get('metrics') or {};tot=purchases.setdefault(cid,[0.0,0.0])
        tot[0]+=float(m.get('conversions',0));tot[1]+=float(m.get('conversionsValue',0))
    rows=[]
    for row in base:
        campaign=row['campaign'];cid=str(campaign['id']);p=purchases.get(cid,[0.0,0.0])
        # A completed category query establishes absence of PURCHASE rows as zero.
        # Generic conversions are deliberately never used as ecommerce purchases.
        rows.append(dict(campaign_id=cid,campaign_name=campaign.get('name'),status=campaign.get('status'),
                         campaign_type=campaign.get('advertisingChannelType'),spend=int(row.get('metrics',{}).get('costMicros',0))/1_000_000,
                         purchase_conversions=p[0],purchase_value=p[1]))
    if set(purchases)-{r['campaign_id'] for r in rows}:return rows,False
    return rows,True

def _meta_pages(url,headers,params):
    rows=[];seen=set();params=dict(params)
    for _ in range(1000):
        response=httpx.get(url,headers=headers,params=params,timeout=40)
        response.raise_for_status();data=response.json();rows.extend(data.get('data') or [])
        paging=data.get('paging') or {}
        if not paging.get('next'):return rows
        cursor=(paging.get('cursors') or {}).get('after')
        if not cursor or cursor in seen:raise ValueError('META_PAGINATION_INCOMPLETE')
        seen.add(cursor);params['after']=cursor
    raise ValueError('PAGE_LIMIT')

def _purchase(items):
    for action in ('purchase','omni_purchase','offsite_conversion.fb_pixel_purchase'):
        values=[float(v['value']) for v in items if v.get('action_type')==action]
        if values:return sum(values)
    return 0.0

def fetch_meta(c,start,end):
    account=c.get('meta_ad_account_id');token=c.get('meta_access_token')
    if not account or not token:return [],False
    root='https://graph.facebook.com/v22.0/act_'+account.removeprefix('act_')
    headers={'Authorization':'Bearer '+token}
    raw=_meta_pages(root+'/insights',headers,{'level':'campaign','fields':'campaign_id,campaign_name,spend,actions,action_values',
        'time_range':json.dumps({'since':str(start),'until':str(end)}),'limit':500})
    metadata=_meta_pages(root+'/campaigns',headers,{'fields':'id,status,effective_status','limit':500})
    statuses={str(r['id']):r.get('effective_status') or r.get('status') for r in metadata}
    rows=[dict(campaign_id=str(r['campaign_id']),campaign_name=r.get('campaign_name'),status=statuses.get(str(r['campaign_id'])),
               spend=float(r['spend']),purchases=_purchase(r.get('actions') or []),
               purchase_value=_purchase(r.get('action_values') or [])) for r in raw]
    return rows,True
