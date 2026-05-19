# 🔍 Queries para Validação no Supabase

Execute estas queries no Supabase SQL Editor para validar se não há outras anomalias.

---

## 1️⃣ Procurar por Orders com Múltiplos Purchase Events

```sql
-- Se houver >1 capi_sent ou capi_event_sent por order, há problema
SELECT 
  o.id,
  o.platform_order_id,
  o.created_at,
  o.capi_sent,
  o.capi_last_error,
  COUNT(*) as event_count
FROM orders o
WHERE o.created_at BETWEEN '2026-05-14'::date AND '2026-05-18'::date
  AND o.client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
GROUP BY o.id, o.platform_order_id, o.created_at, o.capi_sent, o.capi_last_error
HAVING COUNT(*) > 1
ORDER BY event_count DESC
LIMIT 50;
```

**Esperado**: Nenhuma linha (0 duplicatas)  
**Se houver linhas**: Há ordens com múltiplos registros (problema!)

---

## 2️⃣ Contar Purchase Events por Order

```sql
-- Procurar por ordens que dispararam múltiplos Purchase CAPI
SELECT 
  o.id,
  o.platform_order_id,
  o.total_price,
  COUNT(we.id) as tracking_events,
  MAX(we.created_at) as last_event
FROM orders o
LEFT JOIN tracking_events we 
  ON we.visitor_id = o.visitor_id 
  AND we.created_at >= o.created_at - interval '1 hour'
  AND we.created_at <= o.created_at + interval '2 hours'
WHERE o.created_at BETWEEN '2026-05-14'::date AND '2026-05-18'::date
  AND o.client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
GROUP BY o.id, o.platform_order_id, o.total_price
HAVING COUNT(we.id) > 5
ORDER BY tracking_events DESC
LIMIT 50;
```

**Esperado**: Poucas linhas (tracking events normais)  
**Se houver muitas linhas**: Há burst de eventos duplicados

---

## 3️⃣ Verificar capi_sent Status

```sql
-- Quantos orders têm capi_sent=true vs false?
SELECT 
  capi_sent,
  COUNT(*) as order_count,
  SUM(total_price) as total_value,
  AVG(total_price) as avg_value
FROM orders
WHERE created_at BETWEEN '2026-05-14'::date AND '2026-05-18'::date
  AND client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
GROUP BY capi_sent;
```

**Esperado**:
- capi_sent=true: ~24 orders (pedidos reais que foram disparados)
- capi_sent=false: ~0 orders (nenhum pendente)

**Se houver diferença**: Há pedidos não processados

---

## 4️⃣ Procurar por CAPI Errors

```sql
-- Pedidos que falharam ao disparar para Meta
SELECT 
  id,
  platform_order_id,
  total_price,
  capi_last_error,
  created_at
FROM orders
WHERE created_at BETWEEN '2026-05-14'::date AND '2026-05-18'::date
  AND client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
  AND capi_last_error IS NOT NULL
ORDER BY created_at DESC
LIMIT 50;
```

**Esperado**: Poucos ou nenhum erro  
**Se houver muitos**: Há problemas com autenticação/token Meta

---

## 5️⃣ Comparar Volume de Events

```sql
-- Volume de eventos antes (08-11) vs durante problema (14-18) vs depois fix (19+)
SELECT 
  DATE(created_at) as date,
  event_type,
  COUNT(*) as count
FROM events
WHERE client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
  AND created_at >= '2026-05-08'::date
GROUP BY DATE(created_at), event_type
ORDER BY date DESC, count DESC;
```

**Esperado**:
- 08-11: Volume normal (~200-300 events/dia)
- 14-18: Volume similar + anomalias
- 19+: Volume volta ao normal

---

## 6️⃣ Validar Order Total vs CAPI Value

```sql
-- Conferir se ordem com total_price=0 está sendo enviada
SELECT 
  id,
  platform_order_id,
  total_price,
  capi_sent,
  created_at
FROM orders
WHERE created_at BETWEEN '2026-05-14'::date AND '2026-05-18'::date
  AND client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
  AND (total_price IS NULL OR total_price <= 0)
ORDER BY created_at DESC
LIMIT 50;
```

**Esperado**: Nenhuma linha (nenhum pedido com valor 0)  
**Se houver linhas**: Há draft orders sendo enviados como purchases

---

## 7️⃣ Procurar por Duplicated Event IDs

```sql
-- Se há eventos com mesmo event_id, há duplicação
SELECT 
  event_id,
  COUNT(*) as count
FROM events
WHERE client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
  AND created_at >= '2026-05-14'::date
GROUP BY event_id
HAVING COUNT(*) > 1
ORDER BY count DESC
LIMIT 50;
```

**Esperado**: Nenhuma linha (nenhum event_id duplicado)  
**Se houver linhas**: Há problema grave de duplicação

---

## 8️⃣ Timeline de Webhook Processing

```sql
-- Quando os webhooks foram recebidos?
SELECT 
  DATE(created_at) as date,
  EXTRACT(HOUR FROM created_at) as hour,
  COUNT(*) as webhook_count
FROM webhook_deliveries
WHERE client_id = (SELECT id FROM clients WHERE pixel_id = 'lk-sneakers')
  AND created_at >= '2026-05-14'::date
GROUP BY DATE(created_at), EXTRACT(HOUR FROM created_at)
ORDER BY date DESC, hour DESC;
```

**Esperado**: Distribuição normal de webhooks  
**Se houver spike**: Houve burst de eventos em horário específico

---

## 🎯 Resumo Esperado

Se tudo estiver OK, você deve ver:
- ✅ 0 duplicatas
- ✅ ~24 ordens com capi_sent=true
- ✅ 0 CAPI errors (ou poucos)
- ✅ 0 ordens com valor=0
- ✅ 0 event_ids duplicados
- ✅ Distribuição normal de webhooks

Se algum teste falhar, há um problema adicional a investigar.

---

**Próximo Passo**: Execute essas queries e compartilhe os resultados se encontrar anomalias.
