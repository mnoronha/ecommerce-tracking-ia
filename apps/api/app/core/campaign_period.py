"""Core-owned, exact-period campaign evidence linked to one metric snapshot.

Never aggregate the legacy ad_campaigns date column: it contains rolling windows.
The existing CAS persists immutable evidence, not a competing metric registry.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import httpx

PLATFORMS = ('meta', 'google')

def numeric(value):
    if value is None or isinstance(value, bool): return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n >= 0 else None
    except (InvalidOperation, ValueError): return None

def key(client, platform, start, end, snapshot):
    return 'campaign-period:' + sha256(json.dumps([client, platform, str(start), str(end), str(snapshot)]).encode()).hexdigest()

def missing(client, platform, start, end, snapshot, reason='NOT_MATERIALIZED'):
    return dict(schema_version='campaign-period.v1', client_id=client, platform=platform,
                period={'start':str(start),'end':str(end)}, source_snapshot_id=snapshot,
                source_role='CORE_CANONICAL', grain='PERIOD_ROLLUP', source_state='MISSING',
                data_validation='BLOCKED', certification_status='PROVISIONAL', rows=[], limitations=[reason])

def build(client, platform, start, end, snapshot, rows, metrics, *, collected_at, complete):
    doc=missing(client,platform,start,end,snapshot)
    doc.update(collected_at=collected_at, status_as_of=collected_at, status_semantics='CURRENT_AT_COLLECTION', limitations=[])
    if not complete:
        doc['limitations']=['PROVIDER_COLLECTION_INCOMPLETE']; return doc
    normalized=[]; valid=True; seen=set()
    conv='purchases' if platform=='meta' else 'purchase_conversions'
    for raw in rows:
        r={k:raw.get(k) for k in ('campaign_id','campaign_name','status','campaign_type','spend',conv,'purchase_value')}
        if not r['campaign_id'] or r['campaign_id'] in seen or not r['campaign_name']: valid=False
        seen.add(r['campaign_id'])
        if not r['status'] or (platform=='google' and not r['campaign_type']):valid=False
        if any(numeric(r[k]) is None for k in ('spend',conv,'purchase_value')):valid=False
        spend,purchases,value=(numeric(r[k]) for k in ('spend',conv,'purchase_value'))
        r['roas' if platform=='meta' else 'ecommerce_roas']=float(value/spend) if value is not None and spend is not None and spend>0 else None
        r['cpa' if platform=='meta' else 'ecommerce_cpa']=float(spend/purchases) if spend is not None and purchases is not None and purchases>0 else None
        # Brand is a business classification, never guessed from a name.
        r['brand_classification']=raw.get('brand_classification') or 'UNKNOWN'
        normalized.append(r)
    doc['rows']=normalized
    mapping={'spend':platform+'_spend',conv:'meta_conversions' if platform=='meta' else 'google_purchase_conversions',
             'purchase_value':'meta_conversion_value' if platform=='meta' else 'google_purchase_conversion_value'}
    reconciliation=[]
    for field,metric in mapping.items():
        expected=numeric((metrics.get(metric) or {}).get('value'))
        values=[numeric(r[field]) for r in normalized]
        actual=sum(values,Decimal(0)) if all(v is not None for v in values) else None
        # Only declared currency/count rounding tolerance, not an efficiency threshold.
        tolerance=Decimal('0.01')*max(1,len(normalized))
        ok=expected is not None and actual is not None and abs(actual-expected)<=tolerance
        reconciliation.append(dict(metric_key=metric, expected=None if expected is None else float(expected),
                                   actual=None if actual is None else float(actual), tolerance=float(tolerance), passed=ok))
        valid=valid and ok
    doc.update(reconciliation=reconciliation,source_state='READY' if valid and rows else 'NO_DATA' if valid else 'PARTIAL',
               data_validation='PASS' if valid else 'BLOCKED')
    doc['selection']={'top_spend_campaign_ids':[r['campaign_id'] for r in sorted(normalized,key=lambda r:numeric(r.get('spend')) or Decimal(0),reverse=True)[:5]],
                      'top_efficiency_campaign_ids':[],
                      'efficiency_selection_state':'MISSING_DOCUMENTED_VOLUME_RULE'}
    if not valid:doc['limitations'].append('CAMPAIGN_FIELDS_OR_SNAPSHOT_RECONCILIATION_FAILED')
    return doc

def persist(store, doc):
    k=key(doc['client_id'],doc['platform'],doc['period']['start'],doc['period']['end'],doc['source_snapshot_id'])
    old=store.get(k)
    if old:
        if old['document']!=doc:raise ValueError('IMMUTABLE_CAMPAIGN_PERIOD_CONFLICT')
        return old['document']
    if not store.cas(k,None,doc):raise ValueError('CAMPAIGN_PERIOD_CONCURRENT_WRITE')
    return deepcopy(doc)

def read(store, client, platform, start, end, snapshot):
    row=store.get(key(client,platform,start,end,snapshot)) if snapshot else None
    doc=row['document'] if row else missing(client,platform,start,end,snapshot)
    expected=(client,platform,{'start':str(start),'end':str(end)},snapshot)
    if (doc.get('client_id'),doc.get('platform'),doc.get('period'),doc.get('source_snapshot_id'))!=expected:
        raise ValueError('CAMPAIGN_PERIOD_SCOPE_MISMATCH')
    return deepcopy(doc)

def select_snapshot(db,client,start,end):
    from .snapshot_selector import select_best_snapshot
    rows=(db.table('core_metric_snapshots').select('id,period_start,period_end,metrics,health,computed_at')
          .eq('client_id',client).eq('period_start',str(start)).eq('period_end',str(end)).execute()).data or []
    return select_best_snapshot(rows) if rows else None

def capture(db,client,start,end,snapshot,metrics,credentials,*,collected_at):
    from .canonical_reports import CoreStore
    from .collectors.campaign_period import fetch_meta,fetch_google
    store=CoreStore(db);out={}
    for platform,fetch in (('meta',fetch_meta),('google',fetch_google)):
        try:
            rows,complete=fetch(credentials,start,end)
            doc=build(client,platform,start,end,snapshot,rows,metrics,collected_at=collected_at,complete=complete)
        except httpx.HTTPStatusError as exc:
            status=exc.response.status_code
            doc=missing(client,platform,start,end,snapshot,'PROVIDER_HTTP_'+str(status))
            doc['source_state']='PERMISSION_DENIED' if status==403 else 'ACCESS_MISSING' if status==401 else 'ERROR'
        except Exception:
            # No credentials, provider response, or exception string enters evidence/logs.
            doc=missing(client,platform,start,end,snapshot,'PROVIDER_READ_UNAVAILABLE')
            doc['source_state']='ERROR'
        out[platform]=persist(store,doc)
    return out
