# Guia de Verificação de Tracking e EMQ

## ✅ Melhorias Implementadas (Commit 8118b91)

### 1. **Captura de Facebook Login ID (+8% EMQ)**
- Função `captureFacebookLoginId()` adiciona ao código
- Detecta login via Facebook SDK (`FB.getLoginStatus()`)
- Armazena em cookie `_fblogin` (90 dias)
- Injeta no carrinho como atributo Shopify

### 2. **Captura de Date of Birth (+6% EMQ)**
- Função `captureDateOfBirth()` escaneia formulários
- Detecta campos: `birth_date`, `dob`, `date_of_birth`, `birthDate`, `birth-date`
- Normaliza para YYYYMMDD (ex: 19900515)
- Armazena em cookie `_dob` (90 dias)
- Injeta no carrinho após detectar mudanças

### 3. **Listeners de Eventos**
- `change` event: re-captura DOB quando campo é preenchido
- `submit` event: captura antes de enviar para checkout

---

## 🔍 Como Verificar os DDOs (Dados de Tracking)

### **Via Browser Console (F12)**

```javascript
// Ver visitor ID
console.log(ET.getVisitorId());

// Ver todos os IDs de publicidade
{
  visitor: ET.getVisitorId(),
  fbp: ET.getFbp(),
  fbc: ET.getFbc(),
  gclid: ET.getGclid(),
  ga_client: ET.getGaClientId(),
  fblogin: document.cookie.match(/(?:^|;)\s*_fblogin=([^;]*)/)?.[1],
  dob: document.cookie.match(/(?:^|;)\s*_dob=([^;]*)/)?.[1]
}
```

### **Via Network Tab**
1. Abrir F12 → **Network**
2. Filtrar por `/pixel/events`
3. Clicar em um POST
4. Ver **Request Headers** e **Request Body**
5. Procurar por: `fbp`, `fbc`, `gclid`, `ga_client_id`, `ttclid`

### **Via Meta Pixel Test Tool**
1. Acessar https://developers.facebook.com/tools/events-manager/
2. Abrir **Test Events**
3. Procurar por **Purchase** events recentes
4. Validar que estão chegando ao pixel da Meta

---

## 🎯 Checklist: EMQ Coverage

Cada campo abaixo melhora Event Match Quality:

```
[✓] fbp (_fbp cookie) — Meta browser ID
[✓] fbc (_fbc cookie) — Meta click ID (se houver fbclid na URL)
[✓] Email (em)        — Customer data (Shopify)
[✓] Phone (ph)        — Customer data (Shopify)
[✓] First/Last Name   — Customer data (Shopify)
[✓] Address (ct/st/zp) — Shipping address (Shopify)
[✓] IP & User-Agent   — Browser request headers (Shopify)
[✓] GA Client ID (gcid) — _ga cookie via JavaScript
[✓] Google Click ID (gclid) — URL param ou cookie
[✓] Facebook Login ID (+8%) — NEW: FB.getLoginStatus()
[✓] Date of Birth (+6%)     — NEW: Form field detection
[ ] External ID (customer_id) — Se disponível em Shopify
```

---

## 🚨 Possíveis Erros do Console (F12)

### **1. Erro: "Cannot read property 'getLoginStatus' of undefined"**
- **Causa**: Facebook SDK não carregou
- **Solução**: Meta Pixel tag precisa estar na página (ela inclui FB SDK)
- **Verificar**: Network → buscar `connect.facebook.net`

### **2. CORS Error no /pixel/events**
- **Erro**: `Access to XMLHttpRequest blocked by CORS`
- **Causa**: Problema na origem da requisição
- **Verificar**: Logs do API (Railway dashboard)

### **3. Erro no /cart/update.js**
- **Erro**: `404 Not Found` ou `403 Forbidden`
- **Causa**: Endpoint Shopify inacessível ou permissões insuficientes
- **Solução**: Verificar se ScriptTag tem permissão `write_orders`

### **4. "injectSingleAttribute is not defined"**
- **Causa**: Scoping de função incorreto
- **Solução**: Verificar que todas as funções estão dentro do IIFE (function wrapper)

---

## 📊 Dados Esperados (Exemplo de Purchase Event)

```json
{
  "client_id": "lk-sneakers",
  "event_type": "purchase",
  "visitor_id": "abc123def456",
  "session_id": "xyz789",
  "page_url": "https://example.com/orders/12345",
  "referrer": null,
  "utm": null,
  "timestamp": "2026-05-19T10:30:00Z",
  "fbp": "fb.1.1234567890.987654321",
  "fbc": "fb.1.1234567890.AaBbCc",
  "ga_client_id": "123.456",
  "gclid": "gclid_value_here",
  "ttclid": null,
  "metadata": {
    "user_agent": "Mozilla/5.0...",
    "ip": "192.168.1.1",
    "device_type": "mobile",
    "facebook_login": "123456789",
    "date_of_birth": "19900515"
  }
}
```

---

## 🔧 Debugging Avançado

### **Ver quanto de EMQ está sendo alcançado**
1. Ir para Meta Ads Manager → Events Manager
2. Procurar o Pixel
3. Ver "Pixel Events Per Day" e "Event Quality"
4. EMQ % aparece lá (objetivo: >50%)

### **Ver se data está chegando no Meta CAPI**
1. Shopify Orders → Abrir um pedido
2. Na aba **Timeline**, procurar `capi_event_sent`
3. Clicar para ver o JSON enviado ao Meta
4. Validar campos: `user_data`, `custom_data`, `event_id`

### **Teste com Meta Conversion API Debugger**
```bash
curl -X POST \
  'https://graph.facebook.com/v19.0/{PIXEL_ID}/events?access_token={ACCESS_TOKEN}' \
  -H 'Content-Type: application/json' \
  -d '{
    "data": [{
      "event_name": "Purchase",
      "event_time": '$(date +%s)',
      "action_source": "website",
      "event_id": "test_event_123",
      "user_data": {
        "em": ["sha256_hash_of_email"],
        "fbp": "fb.1.123.456",
        "login_id": "user_id_123"
      },
      "custom_data": {
        "value": 99.99,
        "currency": "BRL",
        "order_id": "order_123"
      }
    }],
    "test_event_code": "TEST123"
  }'
```

---

## 📝 Arquivo Modificado

- `apps/api/static/tracker.js` — adicionadas funções de captura de EMQ
- Compatível com Shopify, Nuvemshop, WooCommerce (via cart attributes)

## ⚠️ Próximos Passos

1. **Testar em produção** (LK Sneakers)
2. **Monitorar EMQ** — Meta Ads Manager → Events Manager
3. **Validar dados** — verificar se _fblogin e _dob aparecem nos webhook de Shopify
4. **Medir impacto** — CAPI Quality Rating e Match Rate

---

**Última atualização**: 2026-05-19 (commit 8118b91)
