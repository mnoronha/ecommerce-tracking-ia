#!/usr/bin/env node

/**
 * Test Script — Validar Tracking em Produção
 * Verifica erros do console e dados capturados no site LK Sneakers
 */

const https = require('https');

// Cores para terminal
const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m'
};

function log(color, label, message) {
  console.log(`${color}[${label}]${colors.reset} ${message}`);
}

function success(msg) { log(colors.green, '✅', msg); }
function error(msg) { log(colors.red, '❌', msg); }
function info(msg) { log(colors.cyan, 'ℹ️', msg); }
function warn(msg) { log(colors.yellow, '⚠️', msg); }

async function fetchWithRetry(url, options = {}, retries = 3) {
  for (let i = 0; i < retries; i++) {
    try {
      return await new Promise((resolve, reject) => {
        https.get(url, { timeout: 10000 }, (res) => {
          let data = '';
          res.on('data', (chunk) => data += chunk);
          res.on('end', () => {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              resolve({ status: res.statusCode, data, headers: res.headers });
            } else {
              reject(new Error(`Status ${res.statusCode}`));
            }
          });
        }).on('error', reject);
      });
    } catch (e) {
      if (i === retries - 1) throw e;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

async function testTracker() {
  console.log('\n' + colors.blue + '═══════════════════════════════════════' + colors.reset);
  console.log(colors.blue + '  TESTE DE TRACKING — LK SNEAKERS' + colors.reset);
  console.log(colors.blue + '═══════════════════════════════════════' + colors.reset + '\n');

  const tests = [];

  // 1. Teste 1: API Health
  info('Test 1: Validando saúde da API...');
  try {
    const health = await fetchWithRetry('https://ecommerce-tracking-ia-production.up.railway.app/health');
    const data = JSON.parse(health.data);
    success(`API Status: ${data.status}, Version: ${data.version}`);
    tests.push({ name: 'API Health', status: '✅' });
  } catch (e) {
    error(`API indisponível: ${e.message}`);
    tests.push({ name: 'API Health', status: '❌', error: e.message });
  }

  // 2. Teste 2: Tracker.js Acessível
  info('\nTest 2: Validando acesso ao tracker.js...');
  try {
    const tracker = await fetchWithRetry('https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js?client_id=lk-sneakers');
    const hasEMQ = tracker.data.includes('captureFacebookLoginId') && tracker.data.includes('captureDateOfBirth');
    if (hasEMQ) {
      success('Tracker.js carregado com funções de EMQ');
      tests.push({ name: 'Tracker.js EMQ', status: '✅' });
    } else {
      warn('Tracker.js carregado mas sem funções de EMQ');
      tests.push({ name: 'Tracker.js EMQ', status: '⚠️' });
    }
  } catch (e) {
    error(`Erro ao carregar tracker.js: ${e.message}`);
    tests.push({ name: 'Tracker.js', status: '❌', error: e.message });
  }

  // 3. Teste 3: CORS Headers
  info('\nTest 3: Validando CORS headers...');
  try {
    const corsTest = await new Promise((resolve, reject) => {
      const options = {
        hostname: 'ecommerce-tracking-ia-production.up.railway.app',
        port: 443,
        path: '/pixel/events',
        method: 'OPTIONS',
        headers: {
          'Origin': 'https://lk-sneakers.myshopify.com',
          'Access-Control-Request-Method': 'POST'
        },
        timeout: 10000
      };

      https.request(options, (res) => {
        const allowOrigin = res.headers['access-control-allow-origin'];
        const allowMethods = res.headers['access-control-allow-methods'];
        resolve({ status: res.statusCode, allowOrigin, allowMethods });
      }).on('error', reject).end();
    });

    if (corsTest.allowOrigin || corsTest.allowOrigin === '*') {
      success(`CORS habilitado: ${corsTest.allowOrigin}`);
      tests.push({ name: 'CORS Headers', status: '✅' });
    } else {
      warn('CORS pode estar limitado');
      tests.push({ name: 'CORS Headers', status: '⚠️' });
    }
  } catch (e) {
    warn(`Erro ao testar CORS: ${e.message}`);
    tests.push({ name: 'CORS Headers', status: '⚠️' });
  }

  // 4. Teste 4: Pixel Event POST Simulado
  info('\nTest 4: Simulando POST /pixel/events...');
  try {
    const payload = JSON.stringify({
      client_id: 'lk-sneakers',
      event_type: 'test_tracking',
      visitor_id: 'test-' + Date.now(),
      page_url: 'https://lk-sneakers.myshopify.com/products/test',
      fbp: 'fb.1.1234567890.987654321',
      fbc: null,
      gclid: null,
      ga_client_id: null,
      ttclid: null,
      metadata: {
        facebook_login: 'test_fb_user_123',
        date_of_birth: '19900515',
        device_type: 'mobile',
        user_agent: 'Mozilla/5.0 Test'
      }
    });

    const postTest = await new Promise((resolve, reject) => {
      const options = {
        hostname: 'ecommerce-tracking-ia-production.up.railway.app',
        path: '/pixel/events',
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload)
        },
        timeout: 10000
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => resolve({ status: res.statusCode, data }));
      });

      req.on('error', reject);
      req.write(payload);
      req.end();
    });

    if (postTest.status === 200) {
      success('POST /pixel/events respondeu com 200 OK');
      tests.push({ name: 'POST /pixel/events', status: '✅' });
    } else {
      error(`POST retornou status ${postTest.status}`);
      tests.push({ name: 'POST /pixel/events', status: '❌' });
    }
  } catch (e) {
    error(`Erro ao fazer POST: ${e.message}`);
    tests.push({ name: 'POST /pixel/events', status: '❌', error: e.message });
  }

  // 5. Teste 5: Validar campos de EMQ
  info('\nTest 5: Validando campos de EMQ...');
  try {
    const tracker = await fetchWithRetry('https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js?client_id=lk-sneakers');

    const emqFields = {
      'captureFacebookLoginId': tracker.data.includes('function captureFacebookLoginId'),
      'captureDateOfBirth': tracker.data.includes('function captureDateOfBirth'),
      '_normalizeDOB': tracker.data.includes('function _normalizeDOB'),
      'injectSingleAttribute': tracker.data.includes('function injectSingleAttribute'),
      'FB.getLoginStatus': tracker.data.includes('FB.getLoginStatus'),
      'querySelector birth_date': tracker.data.includes('birth_date'),
    };

    let allPresent = true;
    for (const [field, present] of Object.entries(emqFields)) {
      const status = present ? '✅' : '❌';
      console.log(`  ${status} ${field}`);
      allPresent = allPresent && present;
    }

    if (allPresent) {
      success('Todos os campos de EMQ presentes');
      tests.push({ name: 'EMQ Fields', status: '✅' });
    } else {
      error('Alguns campos de EMQ estão faltando');
      tests.push({ name: 'EMQ Fields', status: '❌' });
    }
  } catch (e) {
    error(`Erro ao validar EMQ fields: ${e.message}`);
    tests.push({ name: 'EMQ Fields', status: '❌' });
  }

  // 6. Teste 6: Validar adapter Shopify
  info('\nTest 6: Validando Shopify adapter...');
  try {
    const adapterPath = '/c/Users/maico/ecommerce-tracking-ia/apps/api/app/services/adapters/shopify_adapter.py';
    const fs = require('fs');
    if (fs.existsSync(adapterPath)) {
      const adapterCode = fs.readFileSync(adapterPath, 'utf8');
      const hasFblogin = adapterCode.includes('_fblogin');
      const hasDob = adapterCode.includes('_dob');

      if (hasFblogin && hasDob) {
        success('Shopify adapter extrai _fblogin e _dob corretamente');
        tests.push({ name: 'Shopify Adapter', status: '✅' });
      } else {
        error('Shopify adapter não extrai _fblogin ou _dob');
        tests.push({ name: 'Shopify Adapter', status: '❌' });
      }
    } else {
      warn('Não consegui validar Shopify adapter');
      tests.push({ name: 'Shopify Adapter', status: '⚠️' });
    }
  } catch (e) {
    warn(`Erro ao validar adapter: ${e.message}`);
    tests.push({ name: 'Shopify Adapter', status: '⚠️' });
  }

  // Sumário
  console.log('\n' + colors.blue + '═══════════════════════════════════════' + colors.reset);
  console.log(colors.blue + '  SUMÁRIO DOS TESTES' + colors.reset);
  console.log(colors.blue + '═══════════════════════════════════════' + colors.reset + '\n');

  const passed = tests.filter(t => t.status === '✅').length;
  const failed = tests.filter(t => t.status === '❌').length;
  const warned = tests.filter(t => t.status === '⚠️').length;

  tests.forEach(t => {
    console.log(`  ${t.status} ${t.name}`);
    if (t.error) {
      console.log(`     Erro: ${t.error}`);
    }
  });

  console.log('\n' + colors.blue + '─────────────────────────────────────' + colors.reset);
  console.log(`  ${colors.green}✅ Passou: ${passed}${colors.reset}`);
  console.log(`  ${colors.red}❌ Falhou: ${failed}${colors.reset}`);
  console.log(`  ${colors.yellow}⚠️  Aviso: ${warned}${colors.reset}`);
  console.log(colors.blue + '─────────────────────────────────────' + colors.reset);

  console.log('\n' + colors.green + '✨ PRÓXIMOS PASSOS:' + colors.reset);
  console.log('  1. Acessar https://lk-sneakers.myshopify.com');
  console.log('  2. Abrir F12 → Console');
  console.log('  3. Preencher formulário de checkout');
  console.log('  4. Fazer uma compra de teste');
  console.log('  5. Validar que _fblogin e _dob aparecem no webhook\n');

  process.exit(failed > 0 ? 1 : 0);
}

testTracker().catch(e => {
  error(`Erro não tratado: ${e.message}`);
  console.error(e);
  process.exit(1);
});
