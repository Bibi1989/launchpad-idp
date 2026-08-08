// Launchpad-generated Node.js/Express service with health + dependency checks.
'use strict';

const express = require('express');

const APP_NAME = process.env.ENVIRONMENT_NAME || 'launchpad-app';
const APP_VERSION = process.env.APP_VERSION || '1.0.0';
const NAMESPACE = process.env.POD_NAMESPACE || 'default';
const POD_NAME = process.env.POD_NAME || require('os').hostname();
const REPLICA_COUNT = process.env.REPLICA_COUNT || '1';
const PORT = parseInt(process.env.PORT || '3000', 10);
const DATABASE_URL =
  process.env.DATABASE_URL || process.env.MYSQL_URL || process.env.MONGODB_URI || null;
const REDIS_URL = process.env.REDIS_URL || null;
const STARTED_AT = Date.now();

let lastDbSuccess = null;
let lastRedisSuccess = null;

const DASHBOARD_HTML = "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\" />\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n<title>Launchpad Service Dashboard</title>\n<style>\n:root { color-scheme: light dark; --bg: #0b1120; --card: #111a2e; --card-border: #1e293b; --text: #f1f5f9; --text-muted: #94a3b8; --accent: #0ea5e9; --inset: #090e17; }\n* { box-sizing: border-box; }\nbody { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, \"Helvetica Neue\", Arial, sans-serif; background: var(--bg); color: var(--text); }\n.wrap { max-width: 1024px; margin: 0 auto; padding: 40px 24px 64px; }\n.breadcrumbs { font-size: 11px; font-weight: 600; letter-spacing: 0.1em; color: var(--text-muted); margin-bottom: 32px; display: flex; align-items: center; gap: 8px; }\n.breadcrumbs .active { color: var(--accent); }\n.header-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 40px; border-bottom: 1px solid var(--card-border); padding-bottom: 24px; }\nh1 { font-size: 28px; margin: 0 0 8px 0; font-weight: 600; letter-spacing: -0.02em; }\n.sub { color: var(--text-muted); font-size: 14px; margin: 0 0 12px 0; }\n.target-line { font-size: 13px; color: var(--text-muted); margin: 0; }\ncode { background: var(--inset); padding: 4px 8px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; color: var(--accent); border: 1px solid var(--card-border); }\n.status-bar { display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }\n.auto-refresh { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); display: flex; align-items: center; gap: 6px; }\n.btn { background: var(--card); border: 1px solid var(--card-border); color: var(--text); padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; display: flex; align-items: center; gap: 8px; transition: all 0.2s; }\n.btn:hover { background: var(--card-border); }\n.dot { width: 8px; height: 8px; border-radius: 50%; background: #64748b; display: inline-block; }\n.dot.up { background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.18); }\n.dot.down, .dot.err { background: #ef4444; box-shadow: 0 0 0 3px rgba(239,68,68,.18); }\n.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }\n.card { background: var(--card); border: 1px solid var(--card-border); border-radius: 12px; padding: 0; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1); }\n.card-header { padding: 20px 24px; border-bottom: 1px solid var(--card-border); display: flex; justify-content: space-between; align-items: flex-start; background: rgba(15,23,42,0.5); }\n.card-header-left p { margin: 0 0 4px 0; font-size: 11px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); }\n.card-header-left h3 { margin: 0; font-size: 16px; font-weight: 600; color: var(--text); }\n.pill { font-size: 12px; font-weight: 500; padding: 4px 10px; border-radius: 999px; display: inline-flex; align-items: center; gap: 6px; }\n.pill.up { background: rgba(34,197,94,.1); color: #4ade80; border: 1px solid rgba(34,197,94,.2); }\n.pill.err, .pill.down { background: rgba(239,68,68,.1); color: #f87171; border: 1px solid rgba(239,68,68,.2); }\n.pill.na { background: rgba(148,163,184,.1); color: #cbd5e1; border: 1px solid rgba(148,163,184,.2); }\n.field { padding: 16px 24px; border-bottom: 1px solid var(--card-border); display: flex; justify-content: space-between; align-items: center; }\n.field-label { font-size: 11px; font-weight: 600; letter-spacing: 0.05em; color: var(--text-muted); }\n.field-val { font-size: 14px; font-weight: 500; }\n.flex-row { display: flex; padding: 16px 24px; gap: 16px; border-bottom: 1px solid var(--card-border); }\n.inset-box { background: var(--inset); border: 1px solid var(--card-border); border-radius: 8px; padding: 12px 16px; flex: 1; }\n.inset-box-label { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; color: var(--text-muted); margin-bottom: 4px; }\n.inset-box-val { font-size: 16px; font-weight: 500; }\n.text-block { padding: 24px; font-size: 13px; color: var(--text-muted); line-height: 1.5; border-bottom: 1px solid var(--card-border); flex-grow: 1; }\n.card-footer { padding: 16px 24px; background: rgba(15,23,42,0.3); margin-top: auto; }\n.card-link { font-size: 12px; font-weight: 600; letter-spacing: 0.05em; color: var(--accent); cursor: pointer; text-decoration: none; }\n.card-link:hover { text-decoration: underline; }\na { color: var(--accent); text-decoration: none; }\na:hover { text-decoration: underline; }\n</style>\n</head>\n<body>\n<div class=\"wrap\">\n  <div class=\"breadcrumbs\">\n    <span>INFRASTRUCTURE</span> &rsaquo; <span class=\"active\">BACKEND STATUS DASHBOARD</span>\n  </div>\n  <div class=\"header-top\">\n    <div>\n      <h1 id=\"appname\">Launchpad Service</h1>\n      <p class=\"sub\">Live health dashboard &middot; endpoints: <a href=\"/health\">/health</a> &middot; <a href=\"/ready\">/ready</a> &middot; <a href=\"/info\">/info</a> &middot; <a href=\"/api/status\">/api/status</a></p>\n      <p class=\"target-line\">Target: <code id=\"target-url\"></code></p>\n    </div>\n    <div class=\"status-bar\">\n      <div class=\"auto-refresh\">\n        <span id=\"appdot\" class=\"dot up\"></span> AUTO-REFRESHING EVERY 5S\n      </div>\n      <button class=\"btn\" onclick=\"refresh()\">\n        <svg width=\"14\" height=\"14\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M21 2v6h-6\"/><path d=\"M3 12a9 9 0 0 1 15-6.7L21 8\"/><path d=\"M3 22v-6h6\"/><path d=\"M21 12a9 9 0 0 1-15 6.7L3 16\"/></svg>\n        Refresh Status\n      </button>\n    </div>\n  </div>\n\n  <div class=\"grid\">\n    <div class=\"card\">\n      <div class=\"card-header\">\n        <div class=\"card-header-left\">\n          <p>APPLICATION</p>\n          <h3>BACKEND SERVICE</h3>\n        </div>\n        <div id=\"app-status\" class=\"pill up\">\n          <span class=\"dot\"></span> Healthy\n        </div>\n      </div>\n      <div class=\"field\">\n        <span class=\"field-label\">VERSION</span>\n        <div class=\"field-val\" id=\"app-version\">-</div>\n      </div>\n      <div class=\"field\">\n        <span class=\"field-label\">UPTIME</span>\n        <div class=\"field-val\" id=\"app-uptime\">-</div>\n      </div>\n      <div class=\"card-footer\">\n        <span class=\"card-link\">VIEW LOGS</span>\n      </div>\n    </div>\n    <div class=\"card\">\n      <div class=\"card-header\">\n        <div class=\"card-header-left\">\n          <p>DEPLOYMENT</p>\n          <h3>METADATA</h3>\n        </div>\n        <div class=\"pill up\">\n          <span class=\"dot\"></span> Active\n        </div>\n      </div>\n      <div class=\"field\">\n        <span class=\"field-label\">NAMESPACE</span>\n        <div class=\"field-val\" id=\"k8s-ns\">-</div>\n      </div>\n      <div class=\"flex-row\">\n        <div class=\"inset-box\">\n          <div class=\"inset-box-label\">POD</div>\n          <div class=\"inset-box-val\" id=\"k8s-pod\" style=\"font-size:12px;word-break:break-all\">-</div>\n        </div>\n        <div class=\"inset-box\">\n          <div class=\"inset-box-label\">REPLICAS</div>\n          <div class=\"inset-box-val\" id=\"k8s-replicas\">-</div>\n        </div>\n      </div>\n      <div class=\"card-footer\">\n        <span class=\"card-link\">INSPECT POD</span>\n      </div>\n    </div>\n    <div class=\"card\" id=\"db-card\">\n      <div class=\"card-header\">\n        <div class=\"card-header-left\">\n          <p>DATABASE</p>\n          <h3 id=\"db-kind\">POSTGRESQL</h3>\n        </div>\n        <div id=\"db-pill\" class=\"pill na\">\n          <span class=\"dot\"></span> Configured\n        </div>\n      </div>\n      <div class=\"field\">\n        <span class=\"field-label\">LAST SUCCESS</span>\n        <div class=\"field-val\" id=\"db-last\">-</div>\n      </div>\n      <div class=\"text-block\" id=\"db-err\" style=\"color:#f87171\">\n      </div>\n      <div class=\"card-footer\">\n        <span class=\"card-link\">CREDENTIALS</span>\n      </div>\n    </div>\n    <div class=\"card\" id=\"redis-card\">\n      <div class=\"card-header\">\n        <div class=\"card-header-left\">\n          <p>CACHE</p>\n          <h3>REDIS CACHE</h3>\n        </div>\n        <div id=\"redis-pill\" class=\"pill na\">\n          <span class=\"dot\"></span> Configured\n        </div>\n      </div>\n      <div class=\"flex-row\">\n        <div class=\"inset-box\">\n          <div class=\"inset-box-label\">LATENCY</div>\n          <div class=\"inset-box-val\" id=\"redis-latency\">-</div>\n        </div>\n        <div class=\"inset-box\">\n          <div class=\"inset-box-label\">LAST SUCCESS</div>\n          <div class=\"inset-box-val\" id=\"redis-last\" style=\"font-size:12px;word-break:break-all\">-</div>\n        </div>\n      </div>\n      <div class=\"text-block\" id=\"redis-err\" style=\"color:#f87171\">\n      </div>\n      <div class=\"card-footer\">\n        <span class=\"card-link\">METRICS</span>\n      </div>\n    </div>\n  </div>\n</div>\n<script>\ndocument.getElementById(\"target-url\").textContent = window.location.origin;\nfunction fmtDep(pillId, errId, dep) {\n  var pill = document.getElementById(pillId);\n  var err = document.getElementById(errId);\n  err.textContent = \"\";\n  if (!dep || !dep.configured) { pill.className = \"pill na\"; pill.innerHTML = '<span class=\"dot\"></span> Not configured'; return; }\n  if (dep.connected) { pill.className = \"pill up\"; pill.innerHTML = '<span class=\"dot\"></span> Connected'; }\n  else {\n    pill.className = \"pill down\"; pill.innerHTML = '<span class=\"dot\"></span> Disconnected';\n    if (dep.error) err.textContent = dep.error;\n  }\n}\nasync function refresh() {\n  try {\n    var res = await fetch(\"/api/status\", { cache: \"no-store\" });\n    var s = await res.json();\n    document.getElementById(\"appname\").textContent = s.app.name;\n    document.title = s.app.name + \" - Dashboard\";\n    var appDot = document.getElementById(\"appdot\");\n    var appStatus = document.getElementById(\"app-status\");\n    var up = s.app.status === \"healthy\";\n    appDot.className = \"dot \" + (up ? \"up\" : \"down\");\n    appStatus.className = \"pill \" + (up ? \"up\" : \"down\");\n    appStatus.innerHTML = '<span class=\"dot\"></span> ' + (up ? \"Healthy\" : \"Unhealthy\");\n    document.getElementById(\"app-version\").textContent = s.app.version;\n    document.getElementById(\"app-uptime\").textContent = s.app.uptimeSeconds + \"s\";\n    document.getElementById(\"k8s-ns\").textContent = s.kubernetes.namespace;\n    document.getElementById(\"k8s-pod\").textContent = s.kubernetes.pod;\n    document.getElementById(\"k8s-replicas\").textContent = s.kubernetes.replicas;\n    fmtDep(\"db-pill\", \"db-err\", s.database);\n    document.getElementById(\"db-kind\").textContent = (s.database.kind || \"database\").toUpperCase();\n    document.getElementById(\"db-last\").textContent = s.database.lastSuccess || \"never\";\n    fmtDep(\"redis-pill\", \"redis-err\", s.redis);\n    document.getElementById(\"redis-latency\").textContent =\n      (s.redis.latencyMs != null) ? (s.redis.latencyMs + \" ms\") : \"-\";\n    document.getElementById(\"redis-last\").textContent = s.redis.lastSuccess || \"never\";\n    document.getElementById(\"db-card\").style.display = s.database.configured ? \"flex\" : \"none\";\n    document.getElementById(\"redis-card\").style.display = s.redis.configured ? \"flex\" : \"none\";\n  } catch (e) {\n    document.getElementById(\"appdot\").className = \"dot down\";\n  }\n}\nrefresh();\nsetInterval(refresh, 5000);\n</script>\n</body>\n</html>\n";

function nowIso() {
  return new Date().toISOString();
}

async function checkDatabase() {
  const url = DATABASE_URL;
  if (!url) {
    return { configured: false, connected: false, error: null, kind: null, lastSuccess: lastDbSuccess };
  }
  try {
    if (url.startsWith('postgres')) {
      const { Client } = require('pg');
      const client = new Client({ connectionString: url, connectionTimeoutMillis: 3000 });
      await client.connect();
      await client.query('SELECT 1');
      await client.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'postgresql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mysql')) {
      const mysql = require('mysql2/promise');
      const conn = await mysql.createConnection(url);
      await conn.query('SELECT 1');
      await conn.end();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mysql', lastSuccess: lastDbSuccess };
    } else if (url.startsWith('mongodb')) {
      const { MongoClient } = require('mongodb');
      const client = new MongoClient(url, { serverSelectionTimeoutMS: 3000 });
      await client.connect();
      await client.db().command({ ping: 1 });
      await client.close();
      lastDbSuccess = nowIso();
      return { configured: true, connected: true, error: null, kind: 'mongodb', lastSuccess: lastDbSuccess };
    }
    return { configured: true, connected: false, error: 'unsupported database scheme', kind: null, lastSuccess: lastDbSuccess };
  } catch (err) {
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), kind: null, lastSuccess: lastDbSuccess };
  }
}

async function checkRedis() {
  if (!REDIS_URL) {
    return { configured: false, connected: false, error: null, latencyMs: null, lastSuccess: lastRedisSuccess };
  }
  let client;
  try {
    const redis = require('redis');
    client = redis.createClient({ url: REDIS_URL, socket: { connectTimeout: 3000 } });
    client.on('error', () => {});
    await client.connect();
    const start = process.hrtime.bigint();
    await client.ping();
    const latency = Number(process.hrtime.bigint() - start) / 1e6;
    await client.quit();
    lastRedisSuccess = nowIso();
    return { configured: true, connected: true, error: null, latencyMs: Math.round(latency * 100) / 100, lastSuccess: lastRedisSuccess };
  } catch (err) {
    try { if (client) await client.disconnect(); } catch (e) { /* ignore */ }
    return { configured: true, connected: false, error: String(err.message || err).slice(0, 200), latencyMs: null, lastSuccess: lastRedisSuccess };
  }
}

async function buildStatus() {
  const [database, redisStatus] = await Promise.all([checkDatabase(), checkRedis()]);
  return {
    app: {
      name: APP_NAME,
      version: APP_VERSION,
      status: 'healthy',
      uptimeSeconds: Math.round((Date.now() - STARTED_AT) / 100) / 10,
    },
    kubernetes: { namespace: NAMESPACE, pod: POD_NAME, replicas: REPLICA_COUNT, deployment: 'app' },
    database,
    redis: redisStatus,
    timestamp: nowIso(),
  };
}

const app = express();

app.get('/health', (req, res) => res.json({ status: 'ok', timestamp: nowIso() }));

app.get('/ready', async (req, res) => {
  const status = await buildStatus();
  const problems = [];
  if (status.database.configured && !status.database.connected) problems.push('database');
  if (status.redis.configured && !status.redis.connected) problems.push('redis');
  res.status(problems.length ? 503 : 200).json({
    status: problems.length ? 'degraded' : 'ready',
    problems,
    timestamp: nowIso(),
  });
});

app.get('/info', (req, res) => res.json({
  name: APP_NAME,
  version: APP_VERSION,
  namespace: NAMESPACE,
  pod: POD_NAME,
  replicas: REPLICA_COUNT,
  port: PORT,
  dependencies: { database: !!DATABASE_URL, redis: !!REDIS_URL },
}));

app.get('/api/status', async (req, res) => res.json(await buildStatus()));

app.get('/', (req, res) => res.type('html').send(DASHBOARD_HTML));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`${APP_NAME} listening on :${PORT}`);
});
