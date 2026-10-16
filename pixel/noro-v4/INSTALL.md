# Noro Tracker v4.3.1 — instalação Shopify

## Arquivos

| Arquivo | Onde vai no Shopify |
| --- | --- |
| `noro-tracker.js` | Tema → Assets, como `noro-tracker.js` |
| `noro-tracker.liquid` | Tema → Snippets, como `noro-tracker.liquid` |
| `noro-shopify-web-pixel.js` (v2.1) | Configurações → Eventos do cliente → Adicionar pixel personalizado |

No `layout/theme.liquid`, antes de `</body>`:

```liquid
{% render 'noro-tracker' %}
```

## Configuração por cliente

No snippet e no pixel, substituir `__CLIENT_INGEST_TOKEN__` pelo token público
de ingestão do cliente (tabela `tracking_ingest_tokens`, só escrita, vinculado ao
cliente). Nunca usar tokens de admin, platform ou Hermes.

No snippet, `ga4MeasurementId` = ID do GA4 da loja (Enutri: `G-DXEZVE9CLN`).

## Backend (antes de instalar)

1. Aplicar `apps/api/migrations/067_noro_tracking_v43.sql` no Supabase.
2. Deploy da API com esta branch (a ingestão funciona mesmo antes da migração,
   mas sem os campos novos).
3. Webhooks do Shopify apontando para `/webhook/shopify/<pixel_id>`:
   `orders/create`, `orders/updated`, `orders/paid`, `orders/cancelled`, `refunds/create`.
4. Modo só-tracking (obrigatório nesta fase): no Railway, definir a variável
   `CONVERSION_SENDS_DISABLED` com o cliente (ex.: `enutri`; aceita slug,
   pixel_id ou UUID, separados por vírgula; `*` bloqueia todos). Com ela, nenhum
   envio de conversão sai para Google Ads, GA4, Meta, TikTok ou Pinterest para
   esse cliente, mesmo com credenciais preenchidas. O tracking continua: eventos,
   pedidos e `tracking_order_matches` (com o que seria enviado) seguem gravados.
   Para liberar os envios no futuro: remover o cliente da variável.

## Verificação

- `GET /agency/v1/clients/<cliente>/tracking/quality` → cobertura vs Shopify,
  taxa de match por fonte, consentimento, prontidão GA4 / Google Ads.
- `POST /agency/v1/clients/<cliente>/tracking/reconcile?days=7` (admin) →
  reconstrói as ligações a partir dos webhooks guardados.

## Checklist de teste (tema publicado, aba anônima, sem adblock)

- [ ] page_view chega com `properties.consent.source = "shopify"`
- [ ] URL com `?utm_source=teste&gclid=TESTE123456`: gclid só aparece com consentimento de marketing
- [ ] nenhum gclid em `url`, `landing_page` ou no cookie `_noro_lt`
- [ ] página de produto: view_item com `variant_id` e `sku`
- [ ] adicionar ao carrinho: exatamente UM add_to_cart (abrir /cart continua um)
- [ ] `/cart.js` mostra `_etv`, `_noro_sid`, `_gcid`, `_ga_sid`, `_utm_*`
- [ ] checkout: begin_checkout de intenção (storefront) e confirmado (pixel) com `checkout_token`
- [ ] pedido de teste: linha em `tracking_order_matches` com `match_source = cart_attributes`
- [ ] `shadow_ga4.session_id` preenchido; `shadow_google_ads.mode = click` quando houve gclid
- [ ] recarregar a página de obrigado: mesmo `event_id` de purchase (deduplicado)
- [ ] repetir em Safari iPhone e Chrome Android
