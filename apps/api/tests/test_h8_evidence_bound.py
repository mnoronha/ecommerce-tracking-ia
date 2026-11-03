from copy import deepcopy
import pytest
from app.core.h8_evidence_guard import evaluate,SAFE_CAMPAIGN_DETAIL,bound_context
from app.core.campaign_period import build,read,persist

def context():
    metrics={k:{'value':100,'value_status':'PROVISIONAL','unit':'BRL'} for k in ('revenue_business','total_spend','mer','meta_spend','google_spend')}
    metrics['ga4_sessions']={'value':2181404,'value_status':'OK','unit':'sessions'}
    return bound_context('lk-sneakers',{'start':'2026-09-01','end':'2026-09-30'},{'metrics':metrics,'source_snapshot_id':'snapshot-1'})

def payload(action=SAFE_CAMPAIGN_DETAIL,purpose='INVESTIGATE_CAMPAIGN_DETAIL',refs=None):
    return {'recommendations':[{'id':'r1','action':action,'purpose':purpose,'evidence_refs':refs or ['gap:meta_campaigns']}],
            'diagnosis':[],'priorities':[]}

def test_missing_campaigns_allows_only_limited_investigation():
    gate=evaluate(payload(),context())
    assert gate['UNSUPPORTED_RECOMMENDATION_COUNT']==0
    assert gate['H8_CLIENT_READY']=='NO'

def test_null_optional_sections_never_invent_content_or_crash():
    gate=evaluate({'recommendations':None,'diagnosis':None,'priorities':None,'next_month_priorities':None},context())
    assert gate['H8_CLIENT_READY']=='NO' and gate['UNSUPPORTED_RECOMMENDATION_COUNT']>0

@pytest.mark.parametrize('status,expected',[(401,'ACCESS_MISSING'),(403,'PERMISSION_DENIED'),(500,'ERROR')])
def test_provider_failure_is_not_missing_or_zero(monkeypatch,status,expected):
    import httpx
    from app.core import canonical_reports,campaign_period
    from app.core.collectors import campaign_period as provider
    store=Store();monkeypatch.setattr(canonical_reports,'CoreStore',lambda db:store)
    def denied(*args):
        response=httpx.Response(status,request=httpx.Request('GET','https://provider.invalid'))
        response.raise_for_status()
    monkeypatch.setattr(provider,'fetch_meta',denied);monkeypatch.setattr(provider,'fetch_google',denied)
    docs=campaign_period.capture(object(),'lk-sneakers','2026-09-01','2026-09-30','s',{}, {},collected_at='now')
    assert all(d['source_state']==expected and d['rows']==[] and d['data_validation']=='BLOCKED' for d in docs.values())

@pytest.mark.parametrize('action',['Escalar campanhas de maior eficiência','Aumentar orçamento das campanhas','Redistribuir investimento entre campanhas'])
def test_scale_without_campaign_volume_blocked(action):
    assert evaluate(payload(action,'SCALE_CAMPAIGN'),context())['UNSUPPORTED_RECOMMENDATION_COUNT']>0

@pytest.mark.parametrize('action',['Aumentar budget em +15%','Exigir ROAS >12x','Destinar R$280k ao Google','Ter 10% das vendas via remarketing','Manter CTR >1%','Dobrar investimento'])
def test_invented_thresholds_blocked(action):
    assert evaluate(payload(action,'RELATIVE_BASELINE',['metric:total_spend']),context())['UNSUPPORTED_NUMERIC_TARGET_COUNT']>0

def test_real_target_requires_metric_value_and_unit_binding():
    c=context();c['metrics']['mer'].update(target=5,unit='x')
    p=payload('Manter MER em 5x','RELATIVE_BASELINE',['target:mer'])
    p['recommendations'][0]['numeric_criteria']=[dict(evidence_ref='target:mer',metric_key='mer',value=5,unit='x')]
    assert evaluate(p,c)['UNSUPPORTED_NUMERIC_TARGET_COUNT']==0
    p['recommendations'][0]['numeric_criteria'][0]['metric_key']='roas_meta'
    assert evaluate(p,c)['UNSUPPORTED_NUMERIC_TARGET_COUNT']>0

def test_anomalous_ga4_not_a_remarketing_argument():
    p=payload('Usar 2.181.404 sessões GA4 como audiência de remarketing','AUDIENCE_REMARKETING',['metric:ga4_sessions'])
    assert evaluate(p,context())['ANOMALOUS_SOURCE_USED_AS_EVIDENCE']>0
    c=context();c['measurement_confidence']={'ga4':'VALID'}
    assert evaluate(p,c)['ANOMALOUS_SOURCE_USED_AS_EVIDENCE']==0

def test_ga4_measurement_review_remains_possible():
    assert evaluate(payload('Revisar a medição GA4 antes de avaliar audiência','MEASUREMENT_REVIEW',['metric:ga4_sessions']),context())['ANOMALOUS_SOURCE_USED_AS_EVIDENCE']==0

def test_factual_metric_in_action_is_not_an_invented_target():
    p=payload('Apresentar investimento de R$ 100 no relatório','OBSERVED_FACT',['metric:total_spend'])
    assert evaluate(p,context())['UNSUPPORTED_NUMERIC_TARGET_COUNT']==0

@pytest.mark.parametrize('statement',[
    'Meta: alteração de orçamento de R$ 230,00 para R$ 260,00.',
    '100% das entradas de Meta têm change_type=OTHER.',
    'A receita foi R$ 1.037.982,69 frente à meta mensal de R$ 1.000.000,00; ambos PROVISIONAL.',
    'Meta registrou R$ 36.246,61 e ROAS de 13,9147x.',
])
def test_observed_platform_figures_are_not_proposed_targets(statement):
    p=payload();p['executive_summary']={'bullets':[statement]}
    assert evaluate(p,context())['UNSUPPORTED_NUMERIC_TARGET_COUNT']==0

@pytest.mark.parametrize('statement',[
    'Aumentar orçamento em 15%.',
    'Meta proposta: ROAS de 12x.',
    'Definir meta de R$ 280k para Google.',
    'Não aumentar orçamento agora; mas destinar R$ 280k ao Google.',
])
def test_narrative_numeric_instructions_still_require_evidence(statement):
    p=payload();p['executive_summary']={'bullets':[statement]}
    assert evaluate(p,context())['UNSUPPORTED_NUMERIC_TARGET_COUNT']>0

@pytest.mark.parametrize('action',[
    'Revisar CPA e ROAS sem ampliar ou reduzir verba até existir regra documentada de volume.',
    'Não escalar campanhas antes de validar volume suficiente.',
])
def test_do_not_scale_is_not_a_scaling_instruction(action):
    p=payload(action,'REVIEW_CAMPAIGN',['gap:meta_campaigns'])
    assert evaluate(p,context())['UNSUPPORTED_RECOMMENDATION_COUNT']==0

@pytest.mark.parametrize('action',[
    'Não escalar campanhas agora, mas aumentar o orçamento das campanhas.',
    'Revisar campanhas sem alterar criativos; ampliar investimento depois.',
])
def test_local_negation_cannot_hide_later_scaling_instruction(action):
    p=payload(action,'REVIEW_CAMPAIGN',['gap:meta_campaigns'])
    assert evaluate(p,context())['UNSUPPORTED_RECOMMENDATION_COUNT']>0

def test_aov_does_not_prove_repeat_purchase():
    p=payload();p['executive_summary']={'bullets':['Cada cliente adquirido tem alto potencial de retorno via recompra']}
    assert evaluate(p,context())['UNSUPPORTED_BUSINESS_CLAIM_COUNT']>0
    p['executive_summary']['bullets']=['O ticket médio reforça o posicionamento de alto valor.']
    assert evaluate(p,context())['UNSUPPORTED_BUSINESS_CLAIM_COUNT']==0

@pytest.mark.parametrize('refs',[[{}],[['bad']],None,'metric:mer'])
def test_malformed_refs_fail_closed_without_crash(refs):
    p=payload();p['recommendations'][0]['evidence_refs']=refs
    assert evaluate(p,context())['UNSUPPORTED_RECOMMENDATION_COUNT']>0

@pytest.mark.parametrize('criterion',[None,[],1,{'evidence_ref':{}}])
def test_malformed_numeric_criterion(criterion):
    p=payload();p['recommendations'][0]['numeric_criteria']=[criterion]
    assert evaluate(p,context())['UNSUPPORTED_NUMERIC_TARGET_COUNT']>0

def campaign_doc(platform='meta',client='lk-sneakers'):
    conv='purchases' if platform=='meta' else 'purchase_conversions'
    metrics={platform+'_spend':{'value':100},('meta_conversions' if platform=='meta' else 'google_purchase_conversions'):{'value':2},
             ('meta_conversion_value' if platform=='meta' else 'google_purchase_conversion_value'):{'value':400}}
    return build(client,platform,'2026-09-01','2026-09-30','snapshot-1',[
        dict(campaign_id='c1',campaign_name='Campaign',status='ACTIVE',campaign_type='SEARCH',spend=100,purchase_value=400,**{conv:2})],
        metrics,collected_at='2026-11-03T12:00:00Z',complete=True)

def test_complete_campaigns_unlock_gate_not_arbitrary_volume():
    c=context();c['campaign_performance']={p:campaign_doc(p) for p in ('meta','google')}
    assert evaluate(payload(),c)['H8_CLIENT_READY']=='YES'
    assert evaluate(payload('Escalar campanhas','SCALE_CAMPAIGN',['campaign:meta:c1']),c)['H8_CLIENT_READY']=='NO'

def test_cross_client_period_and_rolling_rows_cannot_unlock():
    for field,value in [('client_id','dipua'),('period',{'start':'2026-10-01','end':'2026-10-31'}),('grain','ROLLING_WINDOW'),('source_snapshot_id','other-snapshot')]:
        c=context();doc=campaign_doc();doc[field]=value;c['campaign_performance']={'meta':doc}
        assert evaluate(payload(),c)['META_CAMPAIGNS']=='MISSING'

def test_google_fractional_purchases_and_null_semantics():
    doc=campaign_doc('google');assert doc['rows'][0]['ecommerce_cpa']==50
    assert doc['rows'][0]['brand_classification']=='UNKNOWN'
    assert doc['status_semantics']=='CURRENT_AT_COLLECTION'
    metrics={'google_spend':{'value':100},'google_purchase_conversions':{'value':0.5},'google_purchase_conversion_value':{'value':0}}
    doc=build('lk-sneakers','google','2026-09-01','2026-09-30','s',[
        dict(campaign_id='1',campaign_name='X',campaign_type='SEARCH',status='PAUSED',spend=100,purchase_conversions=.5,purchase_value=0)],
        metrics,collected_at='now',complete=True)
    assert doc['data_validation']=='PASS' and doc['rows'][0]['purchase_conversions']==.5
    assert doc['rows'][0]['ecommerce_roas']==0

def test_reconciliation_or_collection_failure_remains_explicit():
    doc=campaign_doc();assert doc['data_validation']=='PASS'
    doc2=build('lk-sneakers','meta','2026-09-01','2026-09-30','s',doc['rows'],{},collected_at='now',complete=True)
    assert doc2['source_state']=='PARTIAL' and doc2['data_validation']=='BLOCKED'
    assert build('lk-sneakers','meta','2026-09-01','2026-09-30','s',[],{},collected_at='now',complete=False)['source_state']=='MISSING'

class Store:
    def __init__(self):self.rows={}
    def get(self,key):return self.rows.get(key)
    def cas(self,key,revision,doc):self.rows[key]={'document':deepcopy(doc),'revision':1};return True

def test_immutable_period_storage_idempotency_isolation():
    store=Store();doc=campaign_doc();persist(store,doc);persist(store,doc)
    assert len(store.rows)==1
    assert read(store,'dipua','meta','2026-09-01','2026-09-30','snapshot-1')['source_state']=='MISSING'
    doc['rows'][0]['spend']=999
    with pytest.raises(ValueError,match='IMMUTABLE'):persist(store,doc)

def test_renderer_only_claims_campaigns_rendered_when_actual_html_exists():
    from app.core.canonical_email_renderer import render_monthly_email,_C
    c=context();c['campaign_performance']={p:campaign_doc(p) for p in ('meta','google')}
    for doc in c['campaign_performance'].values():doc['rows'][0]['campaign_name']='<script>not executable</script>'
    content={'client_id':'lk-sneakers','analysis_period':c['period'],
             'facts':[{'metric_key':k,**v} for k,v in c['metrics'].items()],
             'campaign_performance':c['campaign_performance'],'source_snapshot_id':'snapshot-1','analysis_layer':payload()}
    artifact={'client_id':'lk-sneakers','period_start':'2026-09-01','period_end':'2026-09-30','content':content}
    html,validation=render_monthly_email(artifact)
    assert validation['meta_campaigns_rendered'] and validation['goog_campaigns_rendered']
    assert '&lt;script&gt;' in html and '<script>not executable</script>' not in html
    assert _C['accent']=='#00C4B4' and _C['bg']=='#080E1A'
    content['campaign_performance']={}
    html,validation=render_monthly_email(artifact)
    assert not validation['meta_campaigns_rendered'] and not validation['goog_campaigns_rendered']
    assert validation['h8_evidence_quality_gate']['H8_CLIENT_READY']=='NO'
