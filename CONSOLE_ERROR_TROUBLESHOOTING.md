# Guia de Troubleshooting — Erros do Console (F12)

## 🔴 Erros Comuns e Soluções

### **1. Erro: "Uncaught ReferenceError: injectSingleAttribute is not defined"**

**Sintomas no F12:**
```
Uncaught ReferenceError: injectSingleAttribute is not defined
    at captureFacebookLoginId (tracker.js:507)
```

**Solução:**
- ✅ **RESOLVIDO** no commit 8118b91
- A função `injectSingleAttribute()` foi adicionada antes de `captureFacebookLoginId()`
- Certifique-se que está usando a versão mais recente do tracker.js

**Verificar:**
```bash
grep -n "function injectSingleAttribute" apps/api/static/tracker.js
# Deve retornar a linha 550
```

---

### **2. Erro: "FB.getLoginStatus is not a function"**

**Sintomas:**
```
Uncaught TypeError: FB.getLoginStatus is not a function
    at captureFacebookLoginId (tracker.js:502)
```

**Causas:**
1. Facebook SDK não foi carregado (Meta Pixel ausente)
2. FB objeto não está disponível

**Solução:**
```javascript
// No console (F12):
console.log(typeof FB);
// Deve retornar "object" se Meta Pixel estiver funcionando

// Ver se Meta Pixel está no DOM:
console.log(document.querySelector('script[src*="connect.facebook.net"]'));
```

**Verificação no código:**
- Confirmar que a função verifica `typeof FB === 'undefined'` (✅ linha 500)
- Isso já está implementado para evitar erros

---

### **3. Erro: "Cannot read property 'name' of null"**

**Sintomas:**
```
Uncaught TypeError: Cannot read property 'name' of null
    at HTMLDocument.<anonymous> (tracker.js:640)
```

**Causa:** O evento `change` está disparando em elementos que podem não ter a propriedade `name`

**Solução Implementada:**
```javascript
// Código seguro (linha 640-641):
var name = (e.target.name || '').toLowerCase();
if (name.includes('birth') || name.includes('dob') || name.includes('date')) {
```

- ✅ **JÁ RESOLVIDO** — adiciona string vazia como fallback

---

### **4. Erro: "TypeError: Cannot read property 'action' of undefined"**

**Sintomas:**
```
TypeError: Cannot read property 'action' of undefined
    at HTMLDocument.<anonymous> (tracker.js:648)
```

**Causa:** Listener de `submit` está acionado por elementos que não têm `action`

**Solução Implementada:**
```javascript
// Código seguro (linha 648):
if (e.target && e.target.action && e.target.action.includes('/checkout')) {
```

- ✅ **JÁ RESOLVIDO** — verifica `e.target` antes de acessar `action`

---

### **5. CORS Error: "Access to fetch blocked by CORS policy"**

**Sintomas no Network Tab:**
```
POST /pixel/events
Status: 0 (CORS error)
Error: Access to XMLHttpRequest at 'https://api.example.com/pixel/events'
from origin 'https://shop.example.com' has been blocked by CORS policy
```

**Causa:** O servidor de API não tem CORS habilitado para o domínio da loja

**Solução:**
1. Verificar se API está respondendo com headers CORS corretos:
```bash
curl -H "Origin: https://lk-sneakers.myshopify.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type" \
  -X OPTIONS https://ecommerce-tracking-ia-production.up.railway.app/pixel/events -v
```

2. Verificar arquivo `apps/api/app/main.py` para CORS config:
```python
# Deve incluir:
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ou lista específica de domínios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

3. Se CORS não está configurado, adicionar em `main.py`:
```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

### **6. Erro: "404 Not Found" no /cart/update.js**

**Sintomas no Network Tab:**
```
POST /cart/update.js
Status: 404
Body: Not Found
```

**Causa:** Endpoint Shopify inacessível ou ScriptTag sem permissão

**Solução:**
1. Verificar permissões da ScriptTag no Shopify Admin:
   - Settings → Apps and integrations → Develop apps
   - Ver o app/script que está instalando o tracker
   - Permissões devem incluir: `write_orders`, `read_orders`

2. Verificar se ScriptTag está usando a URL correta:
   - Deve ser: `https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js?client_id=lk-sneakers`

3. Verificar manualmente se o tracker.js está acessível:
```bash
curl https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js \
  -I -s | head -5
# Deve retornar HTTP 200
```

---

### **7. Erro: "sessionStorage is not available"**

**Sintomas:**
```
Uncaught SecurityError: Failed to read the 'sessionStorage' property from 'Window':
Access is denied for this document.
```

**Causa:** Página carregada em contexto sandboxado ou private mode

**Solução Implementada:**
```javascript
// Código seguro (linha 572):
try {
  if (sessionStorage.getItem(_CART_INJECT_KEY)) return;
  sessionStorage.setItem(_CART_INJECT_KEY, '1');
} catch (e) { /* private mode — proceed anyway */ }
```

- ✅ **JÁ RESOLVIDO** — continua funcionando mesmo em private mode

---

## ✅ Verificação Completa

Execute este script no console do navegador (F12) para validar:

```javascript
// 1. Verificar se tracker está carregado
console.log('ET object:', typeof window.ET !== 'undefined' ? '✅ OK' : '❌ FALHA');

// 2. Verificar identificadores principais
console.log('Visitor ID:', window.ET?.getVisitorId() ? '✅ OK' : '❌ FALHA');
console.log('FBP:', window.ET?.getFbp() ? '✅ OK' : '❌ FALHA');
console.log('GClid:', window.ET?.getGclid() ? window.ET?.getGclid() : '⏭️ Não detectado');

// 3. Verificar cookies de EMQ
const cookies = {
  fblogin: document.cookie.match(/(?:^|;)\s*_fblogin=([^;]*)/)?.[1],
  dob: document.cookie.match(/(?:^|;)\s*_dob=([^;]*)/)?.[1]
};
console.log('Cookies EMQ:', cookies);

// 4. Verificar FB SDK
console.log('FB SDK:', typeof FB !== 'undefined' ? '✅ OK' : '❌ FALHA');

// 5. Verifi car se fetch está funcionando
fetch('/pixel/events', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ client_id: 'test', event_type: 'test' })
}).then(r => console.log('Fetch:', r.status >= 200 && r.status < 300 ? '✅ OK' : '⚠️ ' + r.status))
  .catch(e => console.log('Fetch:', '❌ ' + e.message));

// 6. Sumário
console.log('═══════════════════');
console.log('Tracker Status: OK ✅' );
console.log('═══════════════════');
```

---

## 🧪 Teste de Evento

Dispare um evento manualmente no console:

```javascript
// Simular um purchase (teste)
window.ET.track('purchase', {
  order_id: 'TEST-12345',
  order_value: 99.99,
  product_id: 'SKU-001',
  product_name: 'Produto Teste'
});

// Depois, verificar no Network tab se o POST foi feito
```

---

## 📞 Próximas Ações

Se após seguir este guia ainda houver erros:

1. **Verificar logs da API:**
   - Railway Dashboard → Logs
   - Buscar por erros de `/pixel/events`

2. **Verificar dados chegando:**
   - Abrir Shopify Order
   - Ver se `_fblogin` e `_dob` aparecem em `note_attributes`

3. **Testar CAPI:**
   - Meta Ads Manager → Events Manager
   - Procurar por eventos "Purchase" recentes
   - Validar EMQ Quality

---

**Última atualização**: 2026-05-19
**Commit**: 8118b91
