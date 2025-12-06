// FILE: app.js
// BIST Asenax dashboard + Asenax vs Yahoo comparison + Telegram + self-ping
// Backend fetch interval: 60s. Frontend auto-refresh: 5s.
// Usage: set env vars (see README below) or rely on defaults in code.

const express = require('express');
const fetch = require('node-fetch');
const path = require('path');
const os = require('os');

const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// ----------------- CONFIG (env overrides) -----------------
const PORT = process.env.PORT ? parseInt(process.env.PORT) : 10000;
const ASENAX_URL = process.env.ASENAX_URL || 'https://api.asenax.com/bist'; // default (adjust if you have a different endpoint)
const FETCH_INTERVAL = parseInt(process.env.FETCH_INTERVAL || '60'); // seconds between backend fetches
const FRONTEND_REFRESH = parseInt(process.env.FRONTEND_REFRESH || '5'); // seconds for frontend auto refresh
const SELF_URL = process.env.SELF_URL || ''; // optional Render public URL to self-ping
const SELF_PING_INTERVAL = parseInt(process.env.SELF_PING_INTERVAL || (4 * 60)); // seconds
const TELEGRAM_TOKEN = process.env.TELEGRAM_TOKEN || "8588829956:AAEK2-wa75CoHQPjPFEAUU_LElRBduC-_TU";
const CHAT_IDS = (process.env.CHAT_IDS || "661794787").split(',').map(s=>s.trim());

// Symbols list (BIST tickers with .IS for Yahoo queries). Edit if needed.
const SYMBOLS = [
  "AKBNK.IS","ARCLK.IS","ASELS.IS","BIMAS.IS","EKGYO.IS","EREGL.IS","FROTO.IS","GARAN.IS","HEKTS.IS","ISCTR.IS",
  "KCHOL.IS","KOZAA.IS","KOZAL.IS","KRDMD.IS","PETKM.IS","PGSUS.IS","SAHOL.IS","SASA.IS","SISE.IS","TCELL.IS",
  "THYAO.IS","TUPRS.IS","YKBNK.IS","ALARK.IS","ENKAI.IS","TOASO.IS","SOKM.IS","TTKOM.IS","VESTL.IS","MGROS.IS",
  "HALKB.IS","ISGYO.IS","KARSN.IS","LOGO.IS","NETAS.IS","ODAS.IS","OTKAR.IS","OYAKC.IS","TKFEN.IS","TMSN.IS",
  // page2 & page3 continue - full combined list (approx 130)
  "AKSA.IS","ALBRK.IS","ANSGR.IS","AYDEM.IS","CIMSA.IS","ENJSA.IS","GUBRF.IS","KONTR.IS","KARTN.IS","KONYA.IS",
  "KORDS.IS","MAALT.IS","MAVI.IS","OZKGY.IS","PENTA.IS","QUAGR.IS","SAFKR.IS","SELEC.IS","SEYKM.IS","SNGYO.IS",
  "SODA.IS","SRVGY.IS","SUBAS.IS","TRGYO.IS","TTRAK.IS","ULKER.IS","VAKBN.IS","ZOREN.IS","AKGRT.IS","AKFGY.IS",
  "ARZUM.IS","ASUZU.IS","ATLAS.IS","AVOD.IS","AYGAZ.IS","BAGFS.IS","BIZIM.IS","BRISA.IS","BRKO.IS","CCOLA.IS",
  "DEVA.IS","DOAS.IS","DOHOL.IS","DMSAS.IS","ECILC.IS","EGEEN.IS","EGSER.IS","EPLAS.IS","GEDIK.IS","GLYHO.IS",
  "GSRAY.IS","KERVT.IS","KIPA.IS","KSKUT.IS","LUKSK.IS","METRO.IS","NTHOL.IS","OYLUM.IS","PAPIL.IS","PEGAS.IS",
  "PRKME.IS","RYSAS.IS"
];

// In-memory store
let lastAsenax = {};     // { SYMBOL: { price, ts, raw } }
let lastYahoo = {};      // { SYMBOL: { price, ts, raw } }
let comparisons = {};    // { SYMBOL: { asenaxPrice, yahooPrice, priceDiff, pctDiff, timeDiffSec, lastUpdated } }
let lastSignals = [];    // array of {symbol, type, desc, time}

// ----------------- HELPERS -----------------
function telegramSend(text) {
  for (const cid of CHAT_IDS) {
    const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`;
    const payload = { chat_id: cid, text: text, parse_mode: 'HTML' };
    fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) })
      .catch(e => console.error('tg send err', e.message || e));
  }
}

// Yahoo chart API fetch (approx real-time)
async function fetchYahoo(symbol) {
  try {
    // symbol like "GARAN.IS"
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1m&range=1d`;
    const r = await fetch(url, { timeout: 15000 });
    if (!r.ok) return null;
    const j = await r.json();
    // navigate to chart result
    const result = j.chart && j.chart.result && j.chart.result[0];
    if (!result) return null;
    const timestamps = result.timestamp;
    const indicators = result.indicators && result.indicators.quote && result.indicators.quote[0];
    if (!timestamps || !indicators) return null;
    const lastIdx = timestamps.length - 1;
    const lastTs = timestamps[lastIdx] * 1000;
    const lastClose = indicators.close[lastIdx];
    return { price: lastClose, ts: new Date(lastTs).toISOString(), raw: j };
  } catch (e) {
    return null;
  }
}

// Asenax API fetch — this endpoint may return a table; implement flexible parsing
async function fetchAsenaxAll() {
  try {
    const r = await fetch(ASENAX_URL, { timeout: 15000 });
    if (!r.ok) return null;
    const j = await r.json();
    // try to extract mapping symbol -> price
    // Common shapes: array of items with symbol/code and last price OR object with data property
    let map = {};
    if (Array.isArray(j)) {
      for (const it of j) {
        const code = (it.symbol || it.code || it.ticker || it.s || it.kod || it.name || "").toString().toUpperCase();
        const price = parseFloat(it.price || it.last || it.close || it.fiyat || it.p || it.kapanis || it.c);
        if (code && !isNaN(price)) map[code] = { price, raw: it };
      }
    } else if (j && typeof j === 'object') {
      // try j.data or j.result
      const arr = j.data || j.result || j.rows || j.items || null;
      if (Array.isArray(arr)) {
        for (const it of arr) {
          const code = (it.symbol || it.code || it.ticker || it.s || it.kod || it.name || "").toString().toUpperCase();
          const price = parseFloat(it.price || it.last || it.close || it.fiyat || it.p || it.kapanis || it.c);
          if (code && !isNaN(price)) map[code] = { price, raw: it };
        }
      } else {
        // fallback: map keys if they look like symbols
        for (const k of Object.keys(j)) {
          const maybe = k.toString().toUpperCase();
          const val = j[k];
          const price = parseFloat(val && (val.price || val.last || val.close || val.fiyat || val));
          if (!isNaN(price)) map[maybe] = { price, raw: val };
        }
      }
    }
    return map;
  } catch (e) {
    console.error('asenax fetch error', e.message || e);
    return null;
  }
}

// Normalize ticker: BIST:GARAN -> GARAN.IS (for Yahoo)
function toYahoo(sym) {
  // accepts forms like "BIST:GARAN" or "GARAN" or "GARAN.IS"
  let s = (sym || '').toString();
  if (s.includes(':')) s = s.split(':')[1];
  if (s.endsWith('.IS')) return s;
  return s + '.IS';
}

// core fetch+compare task
async function fetchAndCompare() {
  try {
    // 1) fetch Asenax general
    const asmap = await fetchAsenaxAll();
    if (!asmap) {
      console.warn('Asenax returned null or empty');
    }
    // map to uppercase simple codes e.g. GARAN
    // iterate SYMBOLS list and fetch Yahoo per symbol
    for (const s of SYMBOLS) {
      const code = s.replace('.IS','').toUpperCase();
      // Asenax price lookup keys may be like GARAN or BIST:GARAN; try variants
      let axVal = null;
      if (asmap) {
        axVal = asmap[code] || asmap['BIST:'+code] || asmap[code+'.IS'] || asmap[code + ' '];
      }
      if (axVal) {
        lastAsenax[code] = { price: axVal.price, ts: new Date().toISOString(), raw: axVal.raw };
      }
      // yahoo fetch
      const yahooSym = toYahoo(code);
      const y = await fetchYahoo(yahooSym);
      if (y && typeof y.price === 'number') {
        lastYahoo[code] = { price: y.price, ts: y.ts, raw: y.raw };
      }
      // compose comparison
      const a = lastAsenax[code] && lastAsenax[code].price;
      const yprice = lastYahoo[code] && lastYahoo[code].price;
      const atime = lastAsenax[code] && lastAsenax[code].ts;
      const ytime = lastYahoo[code] && lastYahoo[code].ts;
      if (a != null && yprice != null) {
        const priceDiff = parseFloat((a - yprice).toFixed(6));
        const pct = yprice !== 0 ? parseFloat(((priceDiff / yprice) * 100).toFixed(4)) : 0;
        const timeDiffSec = atime && ytime ? (Date.parse(atime) - Date.parse(ytime)) / 1000.0 : null;
        comparisons[code] = {
          asenaxPrice: a, yahooPrice: yprice, priceDiff, pctDiff: pct,
          asenaxTime: atime, yahooTime: ytime, timeDiffSec, lastUpdated: new Date().toISOString()
        };
      } else if (a != null) {
        comparisons[code] = { asenaxPrice: a, yahooPrice: null, lastUpdated: new Date().toISOString() };
      } else if (yprice != null) {
        comparisons[code] = { asenaxPrice: null, yahooPrice: yprice, lastUpdated: new Date().toISOString() };
      }
    }

    // optional: generate simple "alerts" if priceDiff or pctDiff exceeds thresholds
    // Example: if pctDiff > 0.5% or timeDiffSec > 5s, push as "data discrepancy" signal
    const now = new Date().toISOString();
    for (const [code, comp] of Object.entries(comparisons)) {
      if (!comp) continue;
      const pct = comp.pctDiff || 0;
      const tdiff = comp.timeDiffSec || 0;
      // add a simple signal for large discrepancy
      if (Math.abs(pct) > 0.5 || Math.abs(tdiff) > 10) {
        const key = `${code}|DISCREP|${now}`;
        const s = { key, symbol: code, type: 'DISCREPANCY', desc: `pctDiff=${pct}%, timeDiff=${tdiff}s`, time: now };
        lastSignals.unshift(s);
        if (lastSignals.length > 500) lastSignals.pop();
        telegramSendDiscrepancy(s);
      }
    }

  } catch (e) {
    console.error('fetchAndCompare error', e && (e.stack || e.message || e));
  }
}

function telegramSendDiscrepancy(signal) {
  const txt = `⚠️ <b>VERI FARKI:</b> ${signal.symbol}\n${signal.desc}\nZaman: ${signal.time}`;
  telegramSend(txt);
}

// initial fetch
fetchAndCompare();
setInterval(fetchAndCompare, Math.max(10, FETCH_INTERVAL) * 1000);

// ----------------- SELF PING to avoid sleep (if SELF_URL set) -----------------
if (SELF_URL) {
  setInterval(() => {
    try {
      fetch(SELF_URL).catch(()=>{});
    } catch(e) {}
  }, Math.max(30, SELF_PING_INTERVAL) * 1000);
}

// ----------------- API endpoints -----------------
app.get('/health', (req,res) => res.json({ ok: true, ts: new Date().toISOString() }));
app.get('/api/comparisons', (req,res) => res.json({ ok:true, count: Object.keys(comparisons).length, data: comparisons }));
app.get('/api/signals', (req,res) => res.json({ ok:true, total: lastSignals.length, data: lastSignals }));
app.get('/api/lastAsenax', (req,res) => res.json({ ok:true, data: lastAsenax }));
app.get('/api/lastYahoo', (req,res) => res.json({ ok:true, data: lastYahoo }));

// Serve dashboard
app.get('/', (req, res) => {
  res.setHeader('Content-Type','text/html; charset=utf-8');
  res.send(renderHTML());
});

// static minimal CSS/JS embedded in HTML for single-file deploy
function renderHTML(){
  const refresh = FRONTEND_REFRESH;
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>BIST Terminal - Midas / Asenax Comparison</title>
<style>
  :root{--bg:#0b0c10;--panel:#0f1418;--muted:#9aa6b2;--accent:#22c55e;--danger:#ef4444;--card:#0e1620}
  body{margin:0;font-family:Inter,Arial,Helvetica;background:var(--bg);color:#e6eef6}
  .wrap{max-width:1400px;margin:10px auto;padding:12px}
  .header{display:flex;align-items:center;justify-content:space-between}
  h1{margin:0;font-size:18px}
  .controls{display:flex;gap:8px;align-items:center}
  button{background:#111827;border:1px solid rgba(255,255,255,0.03);color:#e6eef6;padding:8px 10px;border-radius:8px;cursor:pointer}
  .grid{display:grid;grid-template-columns:1fr 420px;gap:12px;margin-top:12px}
  .panel{background:var(--panel);padding:12px;border-radius:10px;box-shadow:0 6px 20px rgba(2,6,23,0.6)}
  table{width:100%;border-collapse:collapse}
  th,td{padding:8px 6px;text-align:left;border-bottom:1px solid rgba(255,255,255,0.02);font-size:13px}
  thead th{color:var(--muted);font-size:12px}
  .sym{font-weight:700}
  .up{color:var(--accent)}
  .down{color:var(--danger)}
  .muted{color:var(--muted);font-size:12px}
  .signal{background:rgba(34,197,94,0.12);color:var(--accent);padding:6px;border-radius:6px;display:inline-block}
  .discrep{background:rgba(239,68,68,0.08);color:var(--danger);padding:6px;border-radius:6px}
  .terminal{height:70vh;overflow:auto;font-family:monospace;font-size:13px;background:#05070a;padding:10px;border-radius:6px}
  @media(max-width:900px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>BIST Terminal — Asenax vs Yahoo (Midas JSON)</h1>
      <div class="controls">
        <div class="muted">Backend fetch interval: ${FETCH_INTERVAL}s • Front refresh: ${refresh}s</div>
        <button id="refreshBtn">Yenile</button>
      </div>
    </div>

    <div class="grid">
      <div class="panel">
        <h3 style="margin:0 0 8px 0">Sembol Karşılaştırma</h3>
        <table id="tbl">
          <thead><tr><th>Sembol</th><th>Asenax</th><th>Yahoo</th><th>Fark (TL)</th><th>%</th><th>ZamanΔ (s)</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>

      <div class="panel">
        <h3 style="margin:0 0 8px 0">Sinyaller & Log</h3>
        <div class="terminal" id="terminal"></div>
      </div>
    </div>
  </div>

<script>
const REFRESH = ${refresh} * 1000;
const API_BASE = '';
async function fetchJSON(url){ try { const r=await fetch(url); return await r.json(); } catch(e){ return null; } }

async function render(){
  const comp = await fetchJSON('/api/comparisons');
  const sig = await fetchJSON('/api/signals');
  const tbody = document.querySelector('#tbl tbody');
  tbody.innerHTML = '';
  if (comp && comp.data){
    const entries = Object.entries(comp.data).sort((a,b)=> {
      const pa = a[1].pctDiff || 0; const pb = b[1].pctDiff || 0; return Math.abs(pb)-Math.abs(pa);
    });
    for (const [sym, obj] of entries){
      const tr = document.createElement('tr');
      const tdSym = document.createElement('td'); tdSym.innerHTML = '<span class="sym">'+sym+'</span>';
      const tdA = document.createElement('td'); tdA.innerText = obj.asenaxPrice!=null ? obj.asenaxPrice : '—';
      const tdY = document.createElement('td'); tdY.innerText = obj.yahooPrice!=null ? obj.yahooPrice : '—';
      const tdD = document.createElement('td'); tdD.innerText = obj.priceDiff!=null ? obj.priceDiff : '—';
      const tdP = document.createElement('td'); tdP.innerText = obj.pctDiff!=null ? obj.pctDiff+'%' : '—';
      const tdT = document.createElement('td'); tdT.innerText = obj.timeDiffSec!=null ? obj.timeDiffSec+'s' : '—';
      if (obj.pctDiff && Math.abs(obj.pctDiff) > 0.2) tdP.className = Math.abs(obj.pctDiff) > 0.5 ? 'down' : 'muted';
      tr.appendChild(tdSym); tr.appendChild(tdA); tr.appendChild(tdY); tr.appendChild(tdD); tr.appendChild(tdP); tr.appendChild(tdT);
      tbody.appendChild(tr);
    }
  }
  // terminal logs
  const term = document.getElementById('terminal');
  term.innerHTML = '';
  if (sig && sig.data){
    for (const s of sig.data.slice(0,200)){
      const line = document.createElement('div');
      if (s.type === 'DISCREPANCY') line.innerHTML = '<span class="discrep">[DISCREP]</span> <b>' + s.symbol + '</b> ' + s.desc + ' <span class="muted">(' + s.time + ')</span>';
      else line.innerHTML = '<span class="signal">[SIGNAL]</span> <b>' + s.symbol + '</b> ' + (s.desc||'') + ' <span class="muted">(' + s.time + ')</span>';
      term.appendChild(line);
    }
  } else {
    term.innerHTML = '<div class="muted">Sinyal yok</div>';
  }
}

document.getElementById('refreshBtn').addEventListener('click', render);
render();
setInterval(render, REFRESH);
</script>
</body>
</html>`;
}

// Telegram helper exported
function telegramSend(msg){
  for (const cid of CHAT_IDS){
    const url = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`;
    fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ chat_id: cid, text: msg, parse_mode:'HTML' }) })
      .catch(()=>{});
  }
}

// Also expose raw health
app.listen(PORT, () => {
  console.log(`BIST Terminal app listening on port ${PORT}`);
});
