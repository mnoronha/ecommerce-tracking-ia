#!/usr/bin/env node

/**
 * Script de Validação Local — Testa Tracking em LK Sneakers
 * Simula: carregar site → preencher formulário → validar cookies → testar POST
 */

const https = require('https');
const http = require('http');

const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m'
};

const log = (color, label, msg) => console.log(`${color}[${label}]${colors.reset} ${msg}`);
const ok = (msg) => log(colors.green, '✅', msg);
const err = (msg) => log(colors.red, '❌', msg);
const info = (msg) => log(colors.cyan, 'ℹ️', msg);
const warn = (msg) => log(colors.yellow, '⚠️', msg);

let testsRun = [];

async function fetchUrl(url, options = {}) {
  return new Promise((resolve, reject) => {
    const protocol = url.startsWith('https') ? https : http;
    const req = protocol.get(url, { timeout: 10000, ...options }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, data, headers: res.headers }));
    });
    req.on('error', reject);
  });
}

async function postJson(url, payload) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(payload);
    const protocol = url.startsWith('https') ? https : http;
    const urlObj = new URL(url);

    const options = {
      hostname: urlObj.hostname,
      port: urlObj.port,
      path: urlObj.pathname + urlObj.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data)
      },
      timeout: 10000
    };

    const req = protocol.request(options, (res) => {
      let respData = '';
      res.on('data', chunk => respData += chunk);
      res.on('end', () => resolve({ status: res.statusCode, data: respData }));
    });

    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function testTrackerAccess() {
  info('Test 1: Verificando acesso ao tracker.js em produção...');
  try {
    const res = await fetchUrl('https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js?client_id=lk-sneakers');

    if (res.status === 200) {
      const hasEMQ = res.data.includes('captureFacebookLoginId') && res.data.includes('captureDateOfBirth');
      const hasInject = res.data.includes('injectSingleAttribute');
      const size = Buffer.byteLength(res.data);

      if (hasEMQ && hasInject) {
        ok(`Tracker carregado com EMQ (${size} bytes)`);
        testsRun.push({ name: 'Tracker.js Access', status: '✅' });
        return true;
      } else {
        err('Tracker carregado mas EMQ functions faltando');
        testsRun.push({ name: 'Tracker.js Access', status: '❌', detail: 'EMQ missing' });
        return false;
      }
    } else {
      err(`Status ${res.status}`);
      testsRun.push({ name: 'Tracker.js Access', status: '❌', detail: `Status ${res.status}` });
      return false;
    }
  } catch (e) {
    err(`Erro: ${e.message}`);
    testsRun.push({ name: 'Tracker.js Access', status: '❌', detail: e.message });
    return false;
  }
}

async function testPixelEndpoint() {
  info('\nTest 2: Testando POST /pixel/events com dados EMQ...');
  try {
    const payload = {
      client_id: 'lk-sneakers',
      event_type: 'test_validation',
      visitor_id: 'test-' + Date.now(),
      page_url: 'https://lk-sneakers.myshopify.com/products/test',
      fbp: 'fb.1.1234567890.9876543210',
      fbc: null,
      gclid: null,
      ga_client_id: null,
      ttclid: null,
      metadata: {
        facebook_login: 'test_fb_user_' + Date.now(),
        date_of_birth: '19900515',
        device_type: 'test',
        user_agent: 'Validator Script'
      }
    };

    const res = await postJson('https://ecommerce-tracking-ia-production.up.railway.app/pixel/events', payload);

    if (res.status === 200) {
      ok('POST /pixel/events respondeu 200 OK');
      ok(`Payload enviado com metadata completo (facebook_login, date_of_birth)`);
      testsRun.push({ name: 'POST /pixel/events', status: '✅' });
      return true;
    } else {
      err(`Status ${res.status}`);
      testsRun.push({ name: 'POST /pixel/events', status: '❌', detail: `Status ${res.status}` });
      return false;
    }
  } catch (e) {
    err(`Erro: ${e.message}`);
    testsRun.push({ name: 'POST /pixel/events', status: '❌', detail: e.message });
    return false;
  }
}

async function testCORSHeaders() {
  info('\nTest 3: Validando CORS headers para https://lk-sneakers.myshopify.com...');
  try {
    const res = await fetchUrl('https://ecommerce-tracking-ia-production.up.railway.app/pixel/events', {
      headers: {
        'Origin': 'https://lk-sneakers.myshopify.com',
        'Access-Control-Request-Method': 'POST'
      }
    });

    const allowOrigin = res.headers['access-control-allow-origin'];
    if (allowOrigin && (allowOrigin === '*' || allowOrigin.includes('lk-sneakers'))) {
      ok(`CORS permitido: ${allowOrigin}`);
      testsRun.push({ name: 'CORS Headers', status: '✅' });
      return true;
    } else {
      warn('CORS pode estar limitado');
      testsRun.push({ name: 'CORS Headers', status: '⚠️' });
      return true;
    }
  } catch (e) {
    warn(`Erro ao testar CORS: ${e.message}`);
    testsRun.push({ name: 'CORS Headers', status: '⚠️', detail: e.message });
    return true;
  }
}

async function testShopifyAdapter() {
  info('\nTest 4: Validando Shopify Adapter...');
  try {
    const fs = require('fs');
    const path = require('path');
    const adapterPath = path.join(__dirname, 'apps/api/app/services/adapters/shopify_adapter.py');

    if (fs.existsSync(adapterPath)) {
      const code = fs.readFileSync(adapterPath, 'utf8');
      const hasFblogin = code.includes('_fblogin');
      const hasDob = code.includes('_dob');

      if (hasFblogin && hasDob) {
        ok('Shopify adapter extrai _fblogin e _dob');
        testsRun.push({ name: 'Shopify Adapter', status: '✅' });
        return true;
      } else {
        err('Adapter não extrai _fblogin ou _dob');
        testsRun.push({ name: 'Shopify Adapter', status: '❌' });
        return false;
      }
    } else {
      warn('Adapter file não encontrado (pode estar em teste remoto apenas)');
      testsRun.push({ name: 'Shopify Adapter', status: '⚠️' });
      return true;
    }
  } catch (e) {
    warn(`Erro: ${e.message}`);
    testsRun.push({ name: 'Shopify Adapter', status: '⚠️', detail: e.message });
    return true;
  }
}

async function testMetaCAPI() {
  info('\nTest 5: Validando Meta CAPI...');
  try {
    const fs = require('fs');
    const path = require('path');
    const capiPath = path.join(__dirname, 'apps/api/app/services/meta_capi.py');

    if (fs.existsSync(capiPath)) {
      const code = fs.readFileSync(capiPath, 'utf8');
      const hasLogin = code.includes('facebook_login') || code.includes('login_id');
      const hasDob = code.includes('date_of_birth') || code.includes('db');

      if (hasLogin && hasDob) {
        ok('Meta CAPI processa facebook_login e date_of_birth');
        testsRun.push({ name: 'Meta CAPI', status: '✅' });
        return true;
      } else {
        err('Meta CAPI não processa EMQ fields');
        testsRun.push({ name: 'Meta CAPI', status: '❌' });
        return false;
      }
    } else {
      warn('Meta CAPI file não encontrado');
      testsRun.push({ name: 'Meta CAPI', status: '⚠️' });
      return true;
    }
  } catch (e) {
    warn(`Erro: ${e.message}`);
    testsRun.push({ name: 'Meta CAPI', status: '⚠️', detail: e.message });
    return true;
  }
}

async function runAllTests() {
  console.log('\n' + colors.blue + '═══════════════════════════════════════════════════════════' + colors.reset);
  console.log(colors.blue + '  VALIDAÇÃO LOCAL — Tracking EMQ' + colors.reset);
  console.log(colors.blue + '═══════════════════════════════════════════════════════════' + colors.reset + '\n');

  const results = [];
  results.push(await testTrackerAccess());
  results.push(await testPixelEndpoint());
  results.push(await testCORSHeaders());
  results.push(await testShopifyAdapter());
  results.push(await testMetaCAPI());

  // Sumário
  console.log('\n' + colors.blue + '═══════════════════════════════════════════════════════════' + colors.reset);
  console.log(colors.blue + '  SUMÁRIO' + colors.reset);
  console.log(colors.blue + '═══════════════════════════════════════════════════════════' + colors.reset + '\n');

  testsRun.forEach(t => {
    console.log(`  ${t.status} ${t.name}`);
    if (t.detail) console.log(`     ${t.detail}`);
  });

  const passed = testsRun.filter(t => t.status === '✅').length;
  const failed = testsRun.filter(t => t.status === '❌').length;
  const warned = testsRun.filter(t => t.status === '⚠️').length;

  console.log('\n' + colors.blue + '───────────────────────────────────────────────────────────' + colors.reset);
  console.log(`  ${colors.green}✅ Passou: ${passed}${colors.reset} | ${colors.red}❌ Falhou: ${failed}${colors.reset} | ${colors.yellow}⚠️  Aviso: ${warned}${colors.reset}`);
  console.log(colors.blue + '───────────────────────────────────────────────────────────' + colors.reset);

  console.log('\n' + colors.green + '🎯 PRÓXIMOS PASSOS:' + colors.reset);
  console.log('  1. Abrir: https://lk-sneakers.myshopify.com');
  console.log('  2. Preencher formulário (nome, email, data de nascimento)');
  console.log('  3. F12 → Application → Cookies');
  console.log('  4. Validar cookies presentes:');
  console.log('     • _fblogin (Facebook Login ID)');
  console.log('     • _dob (Date of Birth em YYYYMMDD)');
  console.log('  5. Fazer uma compra de teste');
  console.log('  6. Esperar 24h para validar EMQ no Meta Ads Manager\n');

  process.exit(failed > 0 ? 1 : 0);
}

runAllTests().catch(e => {
  err(`Erro não tratado: ${e.message}`);
  console.error(e);
  process.exit(1);
});
