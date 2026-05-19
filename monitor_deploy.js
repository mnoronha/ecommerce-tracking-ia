#!/usr/bin/env node
const https = require('https');

const TIMEOUT = 5 * 60 * 1000; // 5 minutos
const INTERVAL = 10 * 1000; // 10 segundos
const startTime = Date.now();

function log(status, message) {
  const timestamp = new Date().toLocaleTimeString();
  const symbols = {
    '🔄': '⏳',
    '✅': '✓',
    '❌': '✗',
    '⚠️': '!'
  };
  console.log(`[${timestamp}] ${status} ${message}`);
}

async function checkDeploy() {
  return new Promise((resolve) => {
    https.get('https://ecommerce-tracking-ia-production.up.railway.app/static/tracker.js?client_id=lk-sneakers',
      { timeout: 5000 },
      (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
          const hasEMQ = data.includes('captureFacebookLoginId') && data.includes('captureDateOfBirth');
          const hasInject = data.includes('injectSingleAttribute');
          resolve({ ok: res.statusCode === 200 && hasEMQ && hasInject, size: data.length });
        });
      }
    ).on('error', () => resolve({ ok: false, error: true }));
  });
}

async function monitor() {
  log('🔄', 'Monitorando deployment...');

  let lastCheck = null;
  while (Date.now() - startTime < TIMEOUT) {
    const result = await checkDeploy();

    if (result.error) {
      log('⚠️', 'API inacessível (pode estar compilando)');
    } else if (result.ok) {
      log('✅', `Deployment pronto! Tracker.js com EMQ (${result.size} bytes)`);
      console.log('\n✨ Novo código em produção! Teste em: https://lk-sneakers.myshopify.com\n');
      return true;
    } else {
      log('🔄', `Tracker.js carregado (${result.size} bytes) mas EMQ ainda não ativo`);
    }

    await new Promise(r => setTimeout(r, INTERVAL));
  }

  log('❌', 'Timeout — deployment demorou mais de 5 minutos');
  return false;
}

monitor().then(success => process.exit(success ? 0 : 1));
