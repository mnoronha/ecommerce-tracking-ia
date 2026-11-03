"""Pure H8 evidence policy. No network, store, renderer, metric computation or sends."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re, unicodedata

PURPOSES = {'INVESTIGATE_CAMPAIGN_DETAIL','MEASUREMENT_REVIEW','INVESTIGATE_BUSINESS_CONTEXT',
            'REVIEW_CAMPAIGN','SCALE_CAMPAIGN','REALLOCATE_BUDGET','RELATIVE_BASELINE',
            'RETENTION_ANALYSIS','AUDIENCE_REMARKETING','OBSERVED_FACT','TEMPORAL_ASSOCIATION'}
ANCHORS = {'CLIENT_TARGET','HISTORICAL_BASELINE','DOCUMENTED_STRATEGY',
           'EXPLICIT_BUSINESS_RULE','SUPPORTED_BENCHMARK'}
SAFE_CAMPAIGN_DETAIL = 'Identificar, no detalhamento por campanha, onde está concentrada a eficiência antes de redistribuir investimento.'
GOOD = {'READY','OK','PARTIAL','PROVISIONAL','VERIFIED_CURRENT','VALID'}
REQUIRED = {
    'meta': ('campaign_id','campaign_name','status','spend','purchases','purchase_value','roas','cpa'),
    'google': ('campaign_id','campaign_name','campaign_type','status','spend','purchase_conversions',
               'purchase_value','ecommerce_roas','ecommerce_cpa'),
}

def text(value):
    s = str(value or '').lower()
    return ''.join(c for c in unicodedata.normalize('NFD',s) if unicodedata.category(c) != 'Mn')

def number(value):
    if value is None or isinstance(value,bool):return None
    try:
        n=Decimal(str(value));return n if n.is_finite() else None
    except (InvalidOperation,ValueError):return None

def fields(value):
    if isinstance(value,str):return [value]
    if isinstance(value,dict):return [s for k,v in value.items() if k not in ('evidence_refs','numeric_criteria') for s in fields(v)]
    if isinstance(value,list):return [s for v in value for s in fields(v)]
    return []

def campaign_availability(context, platform):
    envelope=context.get('campaign_performance',{}).get(platform,{})
    period=context.get('period')
    if envelope.get('client_id') != context.get('client_id') or envelope.get('period') != period:
        return 'MISSING'
    if not context.get('source_snapshot_id') or envelope.get('source_snapshot_id')!=context['source_snapshot_id']:return 'MISSING'
    if envelope.get('source_role') != 'CORE_CANONICAL' or envelope.get('grain') != 'PERIOD_ROLLUP':return 'MISSING'
    if envelope.get('source_state') not in ('READY','PARTIAL') or envelope.get('data_validation')!='PASS':return 'MISSING'
    rows=envelope.get('rows')
    if not isinstance(rows,list) or not rows:return 'NO_DATA'
    if any(not isinstance(r,dict) or any(k not in r for k in REQUIRED[platform]) for r in rows):return 'PARTIAL'
    for r in rows:
        if not r.get('campaign_id') or not r.get('campaign_name') or r.get('status') in (None,'UNKNOWN'):return 'PARTIAL'
        if platform=='google' and not r.get('campaign_type'):return 'PARTIAL'
        purchases=r.get('purchases' if platform=='meta' else 'purchase_conversions')
        if any(number(v) is None or number(v)<0 for v in (r.get('spend'),purchases,r.get('purchase_value'))):return 'PARTIAL'
        # Null is legitimate for a mathematically undefined denominator, never zero-filled.
        if number(r['spend'])>0 and number(r.get('roas' if platform=='meta' else 'ecommerce_roas')) is None:return 'PARTIAL'
        if number(purchases)>0 and number(r.get('cpa' if platform=='meta' else 'ecommerce_cpa')) is None:return 'PARTIAL'
    return 'AVAILABLE'

def catalog(context):
    out={}
    for k,m in context.get('metrics',{}).items():
        if not isinstance(m,dict):continue
        if number(m.get('value')) is not None and m.get('value_status','UNKNOWN') in GOOD:
            out['metric:'+k]={'kind':'METRIC','metric_key':k,'value':m['value'],'unit':m.get('unit')}
        if number(m.get('target')) is not None:
            out['target:'+k]={'kind':'ANCHOR','anchor_type':'CLIENT_TARGET','metric_key':k,'value':m['target'],'unit':m.get('unit')}
    for k,v in context.get('historical_baselines',{}).items():
        if isinstance(v,dict) and number(v.get('value')) is not None and v.get('period'):
            out['baseline:'+k]={'kind':'ANCHOR','anchor_type':'HISTORICAL_BASELINE',**deepcopy(v)}
    for v in context.get('approved_numeric_rules',[]):
        if not isinstance(v,dict) or v.get('anchor_type') not in ANCHORS or number(v.get('value')) is None:continue
        if v.get('verification_status')!='VERIFIED_CURRENT' or not v.get('source') or not v.get('rule_id'):continue
        if v['anchor_type']=='SUPPORTED_BENCHMARK' and v.get('statistically_supported') is not True:continue
        out['rule:'+v['rule_id']]={'kind':'ANCHOR',**deepcopy(v)}
    for platform in ('meta','google'):
        state=campaign_availability(context,platform)
        out['gap:'+platform+'_campaigns']={'kind':'GAP','state':state}
        if state=='AVAILABLE':
            env=context['campaign_performance'][platform]
            for r in env['rows']:
                out['campaign:'+platform+':'+str(r['campaign_id'])]={'kind':'CAMPAIGN','platform':platform,
                    'volume_sufficient':r.get('volume_sufficient') is True and bool(env.get('volume_rule_ref')),
                    'row':deepcopy(r)}
    for k,v in context.get('explicit_gaps',{}).items():
        out['gap:'+k]={'kind':'GAP','state':v}
    if context.get('business_truth'):
        out['context:business_truth']={'kind':'CONTEXT','value':deepcopy(context['business_truth'])}
    for channel,value in (context.get('change_summary') or {}).items():
        if value:out['context:change_summary:'+channel]={'kind':'CONTEXT','value':deepcopy(value)}
    for index,value in enumerate(context.get('anomalies') or []):
        if isinstance(value,dict):out['anomaly:'+str(index)]={'kind':'ANOMALY','value':deepcopy(value)}
    return out

def bound_context(client_id,period,manifest):
    """Only the immutable Core handoff/artifact may authorize numeric evidence."""
    metrics=manifest.get('metrics') or {m['metric_key']:m for m in manifest.get('facts',[]) if isinstance(m,dict) and m.get('metric_key')}
    required=('revenue_business','total_spend','mer','meta_spend','google_spend')
    valid=all(isinstance(metrics.get(k),dict) and number(metrics[k].get('value')) is not None and metrics[k].get('value_status') in GOOD for k in required)
    return {'client_id':client_id,'period':period,'metrics':metrics,
            'source_snapshot_id':manifest.get('source_snapshot_id'),
            'data_validation':'PASS' if valid else 'BLOCKED',
            'campaign_performance':manifest.get('campaign_performance') or {},
            'measurement_confidence':manifest.get('measurement_confidence') or {'ga4':'UNKNOWN'},
            'historical_baselines':manifest.get('historical_baselines') or {},
            'approved_numeric_rules':manifest.get('approved_numeric_rules') or [],
            'business_truth':manifest.get('business_truth'),
            'change_summary':manifest.get('change_summary') or {},
            'anomalies':manifest.get('anomalies') or (manifest.get('analysis_layer') or {}).get('anomalies_detected') or [],
            'explicit_gaps':manifest.get('explicit_gaps') or {}}

def proposed_numbers(action):
    values=[]
    for match in re.finditer(r'(?:r\$\s*(\d[\d.,]*)(\s*k)?|(\d+(?:[.,]\d+)?)\s*(%|x\b)|(?:>|<|>=|<=)\s*(\d+(?:[.,]\d+)?))',text(action)):
        currency,thousand,unit_value,unit,comparison=match.groups()
        raw=currency or unit_value or comparison
        if currency:
            raw=raw.replace('.','').replace(',','.') if ',' in raw else raw.replace('.','') if re.fullmatch(r'\d{1,3}(?:\.\d{3})+',raw) else raw
        else:raw=raw.replace(',','.')
        n=number(raw)
        if n is not None:values.append((n*(1000 if thousand else 1),'BRL' if currency else '%' if unit=='%' else 'x' if unit else None))
    return values

def affirmative_directives(statement, verbs):
    """Negation applies locally, never to a later contrasting instruction."""
    for clause in re.split(r'[;.!?]|\b(?:mas|porem|entretanto|contudo)\b', text(statement)):
        for match in re.finditer(r'\b(?:'+verbs+r')\b', clause):
            prefix=clause[:match.start()]
            if re.search(r'\b(?:nao|sem|evitar|impedir|bloquear|proibir)\s+(?:(?:se|deve|devera|pode|recomenda|recomendar|de|ou|ampliar|reduzir)\s+){0,4}$',prefix):
                continue
            yield clause[match.start():]

def numeric_proposal(action):
    # "Meta" names a platform as well as a goal. Historical platform figures
    # and comparisons with a canonical goal are not proposed numeric targets.
    return bool(list(affirmative_directives(action,
        'aumentar|reduzir|elevar|destinar|alocar|escalar|exigir|atingir|buscar|manter|ter|definir|estabelecer|fixar|adotar')) or
        re.search(r'\b(?:meta|limite|objetivo)\s+(?:propost[oa]|recomendad[oa]|sugerid[oa])\b|[<>]',text(action)))

def evaluate(payload,context):
    c=catalog(context);issues=[];unsupported=numeric_bad=anomalous=business_bad=0
    confidence=context.get('measurement_confidence',{})
    measurement=confidence if isinstance(confidence,str) else confidence.get('ga4',confidence.get('status'))
    if isinstance(measurement,dict):measurement=measurement.get('status')
    valid_ga4=measurement=='VALID'
    recs=payload.get('recommendations') or []
    units=[('recommendation',r) for r in recs]+[('diagnosis',d) for d in (payload.get('diagnosis') or [])]+[('priority',p) for p in (payload.get('priorities') or [])]+[('priority',p) for p in (payload.get('next_month_priorities') or [])]
    if not recs:unsupported+=1;issues.append({'code':'RECOMMENDATIONS_MISSING'})
    for kind,unit in units:
        if not isinstance(unit,dict):unsupported+=1;issues.append({'code':'UNSTRUCTURED_CLAIM'});continue
        ref=unit.get('id') or str(len(issues));refs=unit.get('evidence_refs',[]);purpose=unit.get('purpose')
        if not isinstance(refs,list) or any(not isinstance(x,str) for x in refs):refs=[]
        linked=[c[x] for x in refs if x in c]
        if not refs or len(linked)!=len(refs) or purpose not in PURPOSES:
            unsupported+=1;issues.append({'claim':ref,'code':'UNBOUND_OR_UNKNOWN_EVIDENCE'})
        action=unit.get('action') or unit.get('recommendation') or unit.get('recommended_action') or ''
        combined=text(' '.join(fields(unit)))
        scaling=purpose in ('SCALE_CAMPAIGN','REALLOCATE_BUDGET') or any(
            re.search(r'\b(campanhas?|investimento|verba|orcamento)\b',directive)
            for directive in affirmative_directives(action,'escalar|ampliar|aumentar|redistribuir|realocar'))
        safe_detail=purpose=='INVESTIGATE_CAMPAIGN_DETAIL' and text(action).strip(' .')==text(SAFE_CAMPAIGN_DETAIL).strip(' .')
        if scaling and not safe_detail:
            campaign_refs=[v for v in linked if v['kind']=='CAMPAIGN']
            if not campaign_refs or not all(v['volume_sufficient'] for v in campaign_refs):
                unsupported+=1;issues.append({'claim':ref,'code':'CAMPAIGN_EVIDENCE_OR_VOLUME_MISSING',
                    'safe_alternative':SAFE_CAMPAIGN_DETAIL})
        ga4_refs=any(x in ('metric:ga4_sessions','metric:sessions','metric:ga4_cvr') for x in refs)
        marketing=purpose in ('AUDIENCE_REMARKETING','SCALE_CAMPAIGN','REALLOCATE_BUDGET') or bool(re.search(r'\b(remarketing|audiencia|cvr|qualidade de trafego)\b',combined))
        using_sessions=ga4_refs or ('sess' in combined and ('ga4' in combined or re.search(r'\d[\d.,]*\s*sessoes',combined)))
        if not valid_ga4 and marketing and using_sessions and purpose!='MEASUREMENT_REVIEW':
            anomalous+=1;issues.append({'claim':ref,'code':'ANOMALOUS_GA4_USED_FOR_DECISION'})
        criteria=unit.get('numeric_criteria',[]);verified=[]
        for criterion in criteria if isinstance(criteria,list) else []:
            if not isinstance(criterion,dict) or not isinstance(criterion.get('evidence_ref'),str):
                numeric_bad+=1;issues.append({'claim':ref,'code':'MALFORMED_NUMERIC_CRITERION'});continue
            anchor=c.get(criterion.get('evidence_ref'),{})
            if anchor.get('kind')=='ANCHOR' and anchor.get('anchor_type') in ANCHORS and criterion.get('metric_key') and criterion.get('metric_key')==anchor.get('metric_key') and number(criterion.get('value'))==number(anchor.get('value')) and criterion.get('unit')==anchor.get('unit'):
                verified.append((number(criterion['value']),criterion.get('unit')))
            else:numeric_bad+=1;issues.append({'claim':ref,'code':'UNSUPPORTED_NUMERIC_CRITERION'})
        for n,u in proposed_numbers(action) if numeric_proposal(action) else []:
            if not any(n==v and (u is None or u==unit_value) for v,unit_value in verified):
                numeric_bad+=1;issues.append({'claim':ref,'code':'UNANCHORED_NUMERIC_TARGET'})
        if re.search(r'\b(dobrar|triplicar|duplicar)\b',text(action)) and not verified:
            numeric_bad+=1;issues.append({'claim':ref,'code':'UNANCHORED_IMPLICIT_MULTIPLIER'})
        if re.search(r'\b(recompra|ltv|retencao)\b',combined) and re.search(r'\b(alto potencial|alto retorno|elevado retorno|cada cliente|garante|garantido)\b',combined):
            if not any(v.get('metric_key') in ('repeat_purchase_rate','ltv','customer_lifetime_value','retention_rate','cohort_retention') for v in linked):
                business_bad+=1;issues.append({'claim':ref,'code':'UNBOUND_RETENTION_CLAIM'})
    # Unsupported business/audience claims can also occur outside recommendations.
    for statement in fields({k:v for k,v in payload.items() if k not in ('performance_drilldown','analysis_engine_metadata')}):
        s=text(statement)
        action_texts={text(u.get('action') or u.get('recommendation') or u.get('recommended_action') or '') for _,u in units if isinstance(u,dict)}
        if s not in action_texts and proposed_numbers(s) and numeric_proposal(s):
            numeric_bad+=1;issues.append({'code':'UNSTRUCTURED_NUMERIC_TARGET'})
        if re.search(r'\b(nao ha|sem evidencia|nao permite|nao sustenta|nao inferir|nao se pode)\b',s):continue
        if ('recompra' in s or 'ltv' in s or 'retencao' in s) and re.search(r'\b(alto potencial|alto retorno|elevado retorno|cada cliente|garante|garantido)\b',s):
            proof=any('metric:'+k in c for k in ('repeat_purchase_rate','ltv','customer_lifetime_value','retention_rate','cohort_retention'))
            if not proof:business_bad+=1;issues.append({'code':'UNSUPPORTED_REPEAT_PURCHASE_CLAIM'})
        if not valid_ga4 and re.search(r'\d[\d.,]*\s*sessoes',s) and re.search(r'(potencial|tamanho|base|audiencia).{0,60}remarketing|remarketing.{0,60}(potencial|audiencia)',s):
            anomalous+=1;issues.append({'code':'ANOMALOUS_GA4_AUDIENCE_CLAIM'})
    meta,google=(campaign_availability(context,p) for p in ('meta','google'))
    data=context.get('data_validation')
    ready=(data=='PASS' and meta=='AVAILABLE' and google=='AVAILABLE' and unsupported==numeric_bad==anomalous==business_bad==0)
    return {'DATA_VALIDATION':data or 'BLOCKED','META_CAMPAIGNS':meta,'GOOGLE_CAMPAIGNS':google,
        'UNSUPPORTED_RECOMMENDATION_COUNT':unsupported,'UNSUPPORTED_NUMERIC_TARGET_COUNT':numeric_bad,
        'ANOMALOUS_SOURCE_USED_AS_EVIDENCE':anomalous,'UNSUPPORTED_BUSINESS_CLAIM_COUNT':business_bad,
        'H8_CLIENT_READY':'YES' if ready else 'NO','issues':issues,'policy_version':'h8.evidence-bound.v1'}
