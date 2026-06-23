/**
 * GTM Custom HTML Tag — UTM → Shopify cart attributes
 *
 * Cole este conteúdo em um tag do tipo "HTML Personalizado" no GTM.
 * Trigger: All Pages
 *
 * O que faz:
 *   1. Lê UTMs/gclid/fbclid da URL atual
 *   2. Persiste em localStorage por 30 dias (last-click: nova visita com UTM sobrescreve)
 *   3. Escreve como cart attributes `_utm_*` no carrinho Shopify via /cart/update.js
 *   4. Também captura _fbp/_fbc dos cookies para o CAPI Meta
 *   5. Reescreve após /cart/add.js e /cart/change.js (hook de fetch)
 *
 * Esses atributos são lidos pelo shopify_adapter.py nos note_attributes do webhook,
 * restaurando a cascata de atribuição (level 2 = UTM cookie de 30d).
 */
(function () {
  'use strict';

  var STORAGE_KEY  = '_noro_utm';
  var EXPIRY_MS    = 30 * 24 * 60 * 60 * 1000; // 30 dias
  var UTM_KEYS     = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'];
  var CLICK_KEYS   = ['gclid', 'gbraid', 'wbraid', 'fbclid'];

  // ── 1. Ler parâmetros da URL atual ────────────────────────────────────────
  function getParam(name) {
    try {
      return new URLSearchParams(window.location.search).get(name) || null;
    } catch (e) { return null; }
  }

  // ── 2. LocalStorage helpers ───────────────────────────────────────────────
  function loadStored() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || !data._ts) return null;
      if (Date.now() - data._ts > EXPIRY_MS) {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      return data;
    } catch (e) { return null; }
  }

  function saveStored(obj) {
    try {
      obj._ts = Date.now();
      localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch (e) {}
  }

  // ── 3. Capturar UTMs da URL e persistir ──────────────────────────────────
  var fresh = {};
  UTM_KEYS.concat(CLICK_KEYS).forEach(function (k) {
    var v = getParam(k);
    if (v) fresh[k] = v;
  });

  var hasSignal = fresh.utm_source || fresh.gclid || fresh.gbraid || fresh.fbclid;
  var stored    = loadStored();

  if (hasSignal) {
    // Nova sessão de campanha → sobrescreve (last-click)
    saveStored(fresh);
    stored = fresh;
  } else if (!stored) {
    // Nenhum UTM na URL e nada salvo → nada a fazer
    stored = null;
  }

  // ── 4. Ler _fbp / _fbc dos cookies Meta ──────────────────────────────────
  function getCookie(name) {
    try {
      var re = new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)');
      var m  = document.cookie.match(re);
      return m ? decodeURIComponent(m[1]) : null;
    } catch (e) { return null; }
  }

  // ── 5. Montar payload de atributos para o carrinho ────────────────────────
  function buildAttrs() {
    var attrs = {};
    if (stored) {
      if (stored.utm_source)   attrs['_utm_source']   = stored.utm_source;
      if (stored.utm_medium)   attrs['_utm_medium']   = stored.utm_medium;
      if (stored.utm_campaign) attrs['_utm_campaign'] = stored.utm_campaign;
      if (stored.utm_term)     attrs['_utm_term']     = stored.utm_term;
      if (stored.utm_content)  attrs['_utm_content']  = stored.utm_content;
      if (stored.gclid)        attrs['_gclid']        = stored.gclid;
      if (stored.gbraid)       attrs['_gbraid']       = stored.gbraid;
      if (stored.wbraid)       attrs['_wbraid']       = stored.wbraid;
      if (stored.fbclid)       attrs['_fbclid']       = stored.fbclid;
    }
    // _fbp e _fbc são lidos frescos dos cookies a cada chamada
    var fbp = getCookie('_fbp');
    var fbc = getCookie('_fbc');
    if (fbp) attrs['_fbp'] = fbp;
    if (fbc) attrs['_fbc'] = fbc;
    return attrs;
  }

  // ── 6. Escrever no carrinho Shopify ───────────────────────────────────────
  var _writing = false;
  function writeToCart() {
    if (_writing) return;
    var attrs = buildAttrs();
    if (Object.keys(attrs).length === 0) return;
    _writing = true;
    fetch('/cart/update.js', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ attributes: attrs }),
    })
      .catch(function () {})
      .finally(function () { _writing = false; });
  }

  // Escreve na carga da página
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', writeToCart);
  } else {
    writeToCart();
  }

  // Eventos de atualização de carrinho emitidos por temas Shopify
  ['cart:updated', 'cart:refresh', 'cart:add'].forEach(function (evt) {
    document.addEventListener(evt, writeToCart);
  });

  // ── 7. Hook de fetch — reescreve após /cart/add.js e /cart/change.js ─────
  //    Garante que os atributos chegam mesmo quando o tema usa AJAX para
  //    adicionar itens sem recarregar a página.
  var _origFetch = window.fetch;
  window.fetch = function (resource, init) {
    var url = (typeof resource === 'string') ? resource
            : (resource && resource.url) ? resource.url : '';
    var promise = _origFetch.apply(this, arguments);
    if (/\/cart\/(add|change)\.js/.test(url)) {
      promise.then(function () {
        // Pequeno delay para o tema processar antes de sobreescrever
        setTimeout(writeToCart, 200);
      }).catch(function () {});
    }
    return promise;
  };

})();
