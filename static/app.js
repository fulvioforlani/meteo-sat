/* METEO SAT — interfaccia client
 * Mappa Leaflet con layer canvas propri: ogni tile passa da una cache (blob + bitmap) e,
 * se richiesto, da una LUT a 256 colori. Animazione con precaricamento a pool limitato.
 */
'use strict';

/* =========================================================================
 * 1. Utilità
 * ========================================================================= */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const pad = n => String(n).padStart(2, '0');
const MIN = 60000;
const icon = (id, cls = 'i') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtD = d => `${pad(d.getUTCDate())}-${pad(d.getUTCMonth() + 1)}-${String(d.getUTCFullYear()).slice(2)}`;
const fmtT = d => `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
const _itFmt = new Intl.DateTimeFormat('it-IT', { timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
function itParts(d) { return Object.fromEntries(_itFmt.formatToParts(d).map(p => [p.type, p.value])); }
const fmtIt = d => { const p = itParts(d); return `${p.day}-${p.month}-${p.year} ${p.hour}:${p.minute}`; };
const fmtItT = d => { const p = itParts(d); return `${p.hour}:${p.minute}`; };
const isoZ = d => d.toISOString().slice(0, 19) + 'Z';
const num = (n, dec = 1) => Number(n).toLocaleString('it-IT', { minimumFractionDigits: dec, maximumFractionDigits: dec });
function parseTime(s) {
  if (s == null || s === '') return null;
  if (s instanceof Date) return s;
  if (typeof s === 'number') return new Date(s);
  const str = String(s).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) return new Date(str + 'T00:00:00Z');
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(str) ? str : str + 'Z');
  return isNaN(d) ? null : d;
}
function fmtLat(v) { return `${num(Math.abs(v), 2)}° ${v >= 0 ? 'N' : 'S'}`; }
function fmtLon(v) { v = ((v + 540) % 360) - 180; return `${num(Math.abs(v), 2)}° ${v >= 0 ? 'E' : 'O'}`; }
function dms(v, pos, neg) {
  const tot = Math.round(Math.abs(v) * 3600), d = Math.floor(tot / 3600), m = Math.floor((tot % 3600) / 60), s = tot % 60; // niente 60″
  return `${d}° ${pad(m)}′ ${pad(s)}″ ${v >= 0 ? pos : neg}`;
}
const store_ = {
  get(k, def) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* archiviazione non disponibile */ } },
};
/* rAF che funziona anche con la scheda in secondo piano (rAF li' e' sospeso) */
const raf = fn => (document.hidden ? setTimeout(fn, 16) : requestAnimationFrame(fn));
const nextPaint = () => new Promise(r => raf(() => raf(r)));
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
async function getJSON(url, timeout = 15000) {
  const ac = new AbortController(); const t = setTimeout(() => ac.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ac.signal, cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally { clearTimeout(t); }
}

function toast(msg, kind = 'info', ms = 4200) {
  const el = document.createElement('div');
  el.className = `toast glass ${kind}`;
  el.innerHTML = icon(kind === 'err' ? 'alert' : kind === 'ok' ? 'check' : 'info') + `<div>${msg}</div>`;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 320); }, ms);
}

/* =========================================================================
 * 2. Stato
 * ========================================================================= */
const S = {
  mode: 'api',               // 'api' = backend locale, 'mock' = dimostrativa diretta su NASA GIBS
  groups: [], byId: new Map(),
  stack: [],                 // [{id, op, vis, pal, layer}]  indice 0 = in cima
  sel: null,                 // id del layer selezionato per la palette
  frames: [], idx: 0, stat: [],
  range: 180, custom: null, stepMin: 0,
  live: true, playing: false, dir: 1, loop: 'loop', fps: 6,
  events: [], evCats: new Set(), evSev: 0, evSel: null, follow: null, accum: false,
  cmp: { on: false, id: null, off: -1440, op: 1, pos: 0.5, layer: null },
  lastUpd: null,
};
const cat = id => S.byId.get(id);

/* =========================================================================
 * 3. Catalogo layer
 * ========================================================================= */
function satName(id) {
  const m = [
    [/^NAT_goes19/i, 'GOES-19 originale'], [/^NAT_goes18/i, 'GOES-18 originale'], [/^NAT_himawari9/i, 'Himawari-9 originale'],
    [/^GOES-East/i, 'GOES-East'], [/^GOES-West/i, 'GOES-West'], [/^Himawari/i, 'Himawari-9'],
    [/^MetopB/i, 'Metop-B AVHRR'], [/^MetopC/i, 'Metop-C AVHRR'], [/^GeoRing/i, 'Anello geostazionario (5 satelliti)'],
    [/^MTG/i, 'MTG Meteosat-12'], [/^MSG_IODC/i, 'Meteosat Oceano Indiano'], [/^MSG/i, 'MSG Meteosat'], [/meteosat|seviri|fci/i, 'Meteosat'], [/VIIRS_NOAA21/i, 'NOAA-21 VIIRS'], [/VIIRS_NOAA20/i, 'NOAA-20 VIIRS'],
    [/VIIRS_SNPP/i, 'Suomi NPP VIIRS'], [/MODIS_Terra/i, 'Terra MODIS'], [/MODIS_Aqua/i, 'Aqua MODIS'], [/MODIS_Combined/i, 'Terra/Aqua MODIS'],
    [/^HLS_S30/i, 'Sentinel-2 MSI (HLS)'], [/^HLS_L30/i, 'Landsat 8/9 OLI (HLS)'], [/^OPERA_.*HLS/i, 'OPERA (Landsat + Sentinel-2)'],
    [/^AIRS/i, 'Aqua AIRS'], [/^OMI/i, 'Aura OMI'], [/^MOPITT/i, 'Terra MOPITT'], [/^TEMPO/i, 'TEMPO'], [/TROPOMI|S5P/i, 'Sentinel-5P'],
    [/IMERG/i, 'GPM IMERG'], [/BlueMarble/i, 'Blue Marble'], [/Black_Marble/i, 'Black Marble'], [/Coastline|Reference/i, 'Riferimento'],
  ];
  for (const [re, n] of m) if (re.test(id)) return n;
  return id.split('_')[0];
}
function normLayer(l, g) {
  const ext = String(l.ext || 'png').replace(/^.*\//, '').replace(/^\./, '').replace('jpeg', 'jpg');
  let tt = l.tempoTipo || null;
  if (!tt && l.maxTime) tt = String(l.maxTime).includes('T') ? 'datetime' : 'date';
  const min = parseTime(l.minTime), max = parseTime(l.maxTime);
  const lvl = /Level(\d+)/.exec(l.tms || '');
  const maxZoom = +l.maxZoom || (lvl ? +lvl[1] : 8);
  const passo = +l.passoMin || (tt === 'date' ? 1440 : tt ? 10 : null);
  const timed = !!tt && !!max && !(min && +min === +max);
  return {
    ...l, ext, tempoTipo: tt, passoMin: passo, min, max, maxZoom, timed,
    tipo: l.tipo || 'overlay', grp: g.id, grpNome: g.nome, sat: satName(l.id),
    nome: l.nome || l.id.replace(/_/g, ' '),
    isRef: /Coastline|Reference|Borders|Graticule|Labels/i.test(l.id),
    geo: l.tempoTipo === 'datetime' || (tt === 'datetime' && passo < 1440),
  };
}
function loadCatalog(data) {
  S.groups = []; S.byId.clear();
  for (const g of data.groups || []) {
    const G = { id: g.id, nome: g.nome || g.id, layers: [] };
    for (const l of g.layers || []) { const L = normLayer(l, G); if (!S.byId.has(L.id)) { S.byId.set(L.id, L); G.layers.push(L); } }
    if (G.layers.length) S.groups.push(G);
  }
}
function findLayer(...res) {
  for (const L of S.byId.values()) if (res.every(r => r.test(L.id))) return L;
  return null;
}
function tileURL(L, ts, z, x, y) {
  const n = 1 << z;
  if (y < 0 || y >= n) return null;
  x = ((x % n) + n) % n;
  // risoluzione piena: tasselli ricostruiti dal server dai file originali GOES/Himawari
  if (L.nativo) return S.mode === 'api' ? `/api/nativo/${L.nativo.sat}/${L.nativo.banda}/${ts}/${z}/${y}/${x}` : null;
  // accumulo: mm caduti dal primo fotogramma dell'animazione all'istante ts (calcolato dal server sui valori WCS)
  if (accOn(L)) return `/api/accumulo/${L.id}/${accFrom(L)}/${ts}/${z}/${y}/${x}`;
  if (S.mode === 'api') return `/api/tile/${L.id}/${ts}/${z}/${y}/${x}`;
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${L.id}/default/${ts}/${L.tms}/${z}/${y}/${x}.${L.ext}`;
}
/* modalita' accumulo (solo layer di precipitazione con valori reali, backend locale, senza confronto) */
function accOn(L) { return !!(S.accum && L && L.accumulo && S.mode === 'api' && !S.cmp.on); }
function accFrom(L) { return layerTime(L, S.frames[0] || new Date()); }
/* i layer giornalieri polari usano il giorno UTC completo piu' recente */
function dayCap() { const d = new Date(); return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - 86400000; }
function layerTime(L, t) {
  if (!L.tempoTipo || !L.max) return 'default';
  if (!L.timed) return L.tempoTipo === 'date' ? L.max.toISOString().slice(0, 10) : isoZ(L.max);
  let ms = +t;
  let hi = +L.max;
  if (L.tempoTipo === 'date' && hi > dayCap() && (!L.min || dayCap() >= +L.min)) hi = dayCap();
  if (ms > hi) ms = hi;
  if (L.min && ms < +L.min) ms = +L.min;
  if (L.tempoTipo === 'date') return new Date(ms).toISOString().slice(0, 10);
  const step = (L.passoMin || 10) * MIN;
  // orbite polari (Metop): istanti irregolari; l'ultimo fotogramma usa l'istante esatto dell'ultima orbita,
  // gli altri l'istante arrotondato a 10 min (EUMETView risponde con l'orbita piu' vicina)
  if (L.orbite) return ms > hi - 10 * MIN ? isoZ(L.max) : isoZ(new Date(Math.floor(ms / (10 * MIN)) * 10 * MIN));
  return isoZ(new Date(Math.floor(ms / step) * step));
}
/* istante usato da un elemento della pila: data fissata (es. Sentinel-2 del giorno trovato) o quello del fotogramma */
function entryTime(e, Lr, t) { return (e && e.fixT) || layerTime(Lr, t); }
function nativeKm(z, lat = 0) { return 40075.016686 * Math.cos(lat * Math.PI / 180) / (256 * Math.pow(2, z)); }

/* =========================================================================
 * 4. Palette, LUT e decodifica dei colori GIBS
 * ========================================================================= */
const BUILTIN = [
  { id: 'orig', nome: 'Originale', stops: null },
  { id: 'grigi', nome: 'Grigi', stops: [[0, '#000000'], [1, '#ffffff']] },
  { id: 'ir', nome: 'IR classica', stops: [[0, '#060606'], [0.571, '#dedede'], [0.5711, '#1f5fd1'], [0.643, '#2bd1e6'], [0.6431, '#25b84a'], [0.714, '#c8ee3a'], [0.7141, '#ffe23a'], [0.786, '#ff8a1e'], [0.7861, '#e8231b'], [0.857, '#6e0b0b'], [0.8571, '#151515'], [0.929, '#6a6a6a'], [0.9291, '#ffffff'], [1, '#ff6bff']] },
  { id: 'arcobaleno', nome: 'Arcobaleno', stops: [[0, '#30123b'], [0.15, '#4662d7'], [0.3, '#36aaf9'], [0.45, '#1ae4b6'], [0.6, '#72fe5e'], [0.72, '#c8ef34'], [0.84, '#fbb938'], [0.93, '#f56918'], [1, '#c42503']] },
  { id: 'vapore', nome: 'Vapore acqueo', stops: [[0, '#2a1608'], [0.3, '#9a6a35'], [0.48, '#e9dcc0'], [0.6, '#f4f6f8'], [0.78, '#5ba4dc'], [1, '#0a2b78']] },
  { id: 'fuoco', nome: 'Fuoco / termica', stops: [[0, '#000000'], [0.3, '#5a0a00'], [0.55, '#c42a00'], [0.75, '#ff8a00'], [0.9, '#ffe14a'], [1, '#ffffff']] },
  { id: 'inverso', nome: 'Inverso', stops: [[0, '#ffffff'], [1, '#000000']] },
];
let customPals = store_.get('meteoSat.palette', []);
const allPals = () => [...BUILTIN, ...customPals];
const palById = id => allPals().find(p => p.id === id) || BUILTIN[0];
const defPal = L => {
  const d = decoderFor(L);
  return { id: 'orig', b: 0, c: 0, g: 1, min: d ? -90 : 0, max: d ? 50 : 255, cut: false, key: false, kT: d ? -25 : 110, kW: d ? 15 : 60 };
};
const palKey = p => `${p.id}|${p.b}|${p.c}|${p.g}|${p.min}|${p.max}|${p.cut ? 1 : 0}|${p.key ? `${p.kT}_${p.kW}` : 0}|${palById(p.id).v || 0}`;
/* palette attiva: ricolora e/o rende trasparente (trasparenza morbida anche con i colori originali) */
const palActive = p => !!p && (p.id !== 'orig' || !!p.key);
const hex2 = h => { h = h.replace('#', ''); return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; };
function gradCSS(stops) {
  if (!stops) return 'linear-gradient(90deg,#1b2a38,#3a5068 40%,#ffb547 70%,#62e3d8)';
  return `linear-gradient(90deg,${stops.map(([p, c]) => `${c} ${(p * 100).toFixed(2)}%`).join(',')})`;
}
const lutCache = new Map();
function getLUT(p) {
  const pal = palById(p.id); const key = `${p.id}|${p.b}|${p.c}|${p.g}|${pal.v || 0}`;
  let lut = lutCache.get(key);
  if (lut) return lut;
  lut = new Uint8ClampedArray(256 * 3);
  const stops = (pal.stops || BUILTIN[1].stops).map(([q, c]) => [q, hex2(c)]).sort((a, b) => a[0] - b[0]);
  const f = Math.tan((clamp(p.c, -99, 99) / 100 + 1) * Math.PI / 4);
  for (let i = 0; i < 256; i++) {
    let v = i / 255;
    v = (v - 0.5) * f + 0.5 + p.b / 200;
    v = Math.pow(clamp(v, 0, 1), 1 / (p.g || 1));
    let j = 0; while (j < stops.length - 2 && v > stops[j + 1][0]) j++;
    const [p0, c0] = stops[j], [p1, c1] = stops[Math.min(j + 1, stops.length - 1)];
    const t = p1 > p0 ? clamp((v - p0) / (p1 - p0), 0, 1) : 0;
    for (let k = 0; k < 3; k++) lut[i * 3 + k] = c0[k] + (c1[k] - c0[k]) * t;
  }
  lutCache.set(key, lut);
  return lut;
}

/* decodificatori: colore del tile -> valore fisico (es. temperatura di brillanza in °C) */
const CMAPS = [{ re: /Band13|Clean_Infrared/i, url: 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/Clean_Longwave_Infrared_Window_Band.xml' }];
const decoders = new Map(); // url -> {state:'loading'|'ok'|'err', map, units, entries, idx:Map}
function decoderUrl(L) {
  if (!L) return null;
  const lg = L.legenda;
  if (typeof lg === 'string' && /colormaps.*\.xml$/i.test(lg)) return lg;
  if (lg && typeof lg === 'object' && lg.colormap) return lg.colormap;
  const m = CMAPS.find(c => c.re.test(L.id));
  return m ? m.url : null;
}
function decoderFor(L) { const u = decoderUrl(L); const d = u && decoders.get(u); return d && d.state === 'ok' ? d : null; }
async function ensureDecoder(L) {
  const u = decoderUrl(L);
  if (!u || decoders.has(u)) return;
  const d = { state: 'loading' }; decoders.set(u, d);
  try {
    const r = await fetch(u); if (!r.ok) throw new Error(r.status);
    const xml = new DOMParser().parseFromString(await r.text(), 'text/xml');
    const map = new Map(), entries = [], nodata = new Set();
    const cm = xml.querySelector('ColorMap'); d.units = cm?.getAttribute('units') || '';
    for (const e of xml.querySelectorAll('ColorMapEntry')) {
      const [r1, g1, b1] = e.getAttribute('rgb').split(',').map(Number); const key = (r1 << 16) | (g1 << 8) | b1;
      if (e.getAttribute('transparent') === 'true' || e.getAttribute('nodata') === 'true') { nodata.add(key); continue; }
      const nums = (e.getAttribute('value') || '').replace(/[[\]()]/g, '').split(',').map(s => s.trim());
      let a = parseFloat(nums[0]), b = parseFloat(nums[1] ?? nums[0]);
      if (!isFinite(a)) a = b - 0.5; if (!isFinite(b)) b = a + 0.5;
      const v = (a + b) / 2; if (!isFinite(v)) continue;
      if (!map.has(key)) { map.set(key, v); entries.push([r1, g1, b1, v]); }
    }
    Object.assign(d, { state: 'ok', map, entries, nodata, idx: new Map(), lo: Math.min(...entries.map(e => e[3])), hi: Math.max(...entries.map(e => e[3])) });
    // una volta pronto, i layer che lo usano ricalcolano palette e sonda
    for (const e of S.stack) if (decoderUrl(cat(e.id)) === u) {
      if (e.pal.id === 'orig' || (e.pal.min === 0 && e.pal.max === 255)) { e.pal.min = -90; e.pal.max = 50; }
      e.layer.repaint();
    }
    if (S.cmp.layer && decoderUrl(cat(S.cmp.id)) === u) S.cmp.layer.repaint();
    if (S.sel && decoderUrl(cat(S.sel)) === u) renderPalettePanel();
  } catch (err) {
    d.state = 'err';
  }
}
function decodeRGB(d, key) {
  let v = d.map.get(key);
  if (v !== undefined) return v;
  if (d.nodata.has(key)) return null;
  const r = key >> 16, g = (key >> 8) & 255, b = key & 255; let best = 1e9;
  for (const e of d.entries) { const q = (e[0] - r) ** 2 + (e[1] - g) ** 2 + (e[2] - b) ** 2; if (q < best) { best = q; v = e[3]; } }
  d.map.set(key, v);
  return v;
}
/* applica la palette a un ImageData del tile (in place) */
function applyPalette(img, L, p) {
  const lut = getLUT(p), a = img.data, dec = decoderFor(L);
  const lo = p.min, hi = p.max, span = (hi - lo) || 1;
  if (dec) {
    const k = `${lo}|${hi}`; let idx = dec.idx.get(k);
    if (!idx) { idx = new Map(); if (dec.idx.size > 8) dec.idx.clear(); dec.idx.set(k, idx); }
    for (let i = 0; i < a.length; i += 4) {
      if (a[i + 3] === 0) continue;
      const key = (a[i] << 16) | (a[i + 1] << 8) | a[i + 2];
      let n = idx.get(key);
      if (n === undefined) {
        const T = decodeRGB(dec, key);
        n = T == null ? -2 : (hi - T) / span; // freddo = alto
        idx.set(key, n);
      }
      if (n === -2 || (p.cut && n < 0)) { a[i + 3] = 0; continue; }
      if (p.key) { const T = hi - n * span; a[i + 3] = a[i + 3] * clamp((p.kT - T) / (p.kW || 1), 0, 1); if (!a[i + 3]) continue; }
      if (p.id === 'orig') continue;
      const j = Math.round(clamp(n, 0, 1) * 255) * 3;
      a[i] = lut[j]; a[i + 1] = lut[j + 1]; a[i + 2] = lut[j + 2];
    }
  } else {
    for (let i = 0; i < a.length; i += 4) {
      if (a[i + 3] === 0) continue;
      const lum = 0.299 * a[i] + 0.587 * a[i + 1] + 0.114 * a[i + 2];
      const n = (lum - lo) / span;
      if (p.cut && n < 0) { a[i + 3] = 0; continue; }
      if (p.key) { a[i + 3] = a[i + 3] * clamp((lum - p.kT) / (p.kW || 1), 0, 1); if (!a[i + 3]) continue; }
      if (p.id === 'orig') continue;
      const j = Math.round(clamp(n, 0, 1) * 255) * 3;
      a[i] = lut[j]; a[i + 1] = lut[j + 1]; a[i + 2] = lut[j + 2];
    }
  }
  if (p.key) despeckle(img);
}
/* toglie i pixel isolati (rumore: grigi chiari scambiati per nubi fredde) dopo la trasparenza morbida */
function despeckle(img) {
  const a = img.data, w = img.width, h = img.height, al = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) al[i] = a[i * 4 + 3] > 24 ? 1 : 0;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const k = y * w + x; if (!al[k]) continue;
    let n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (!dx && !dy) continue; const xx = x + dx, yy = y + dy;
      if (xx < 0 || yy < 0 || xx >= w || yy >= h) { n += 1; continue; }   // bordo del tassello: non giudicare
      n += al[yy * w + xx];
    }
    if (n < 3) a[k * 4 + 3] = 0;
  }
}

/* =========================================================================
 * 5. Cache tile e coda di download (max 6 richieste in parallelo)
 * ========================================================================= */
const TS = {
  blobs: new Map(), bytes: 0, maxBytes: 380 * 1024 * 1024,
  bmps: new Map(), maxBmps: 700,
  pend: new Map(), q: [], active: 0, max: 10, fails: 0,
  natActive: 0, natMax: 4,
};
const palCache = new Map(); const PALMAX = 320;
function touch(map, k) { const v = map.get(k); map.delete(k); map.set(k, v); return v; }
function putBlob(url, blob) {
  TS.blobs.set(url, blob); TS.bytes += blob ? blob.size : 64;
  while (TS.bytes > TS.maxBytes && TS.blobs.size) {
    const [k, b] = TS.blobs.entries().next().value; TS.blobs.delete(k); TS.bytes -= b ? b.size : 64;
  }
}
function putBmp(url, bmp) {
  TS.bmps.set(url, bmp);
  while (TS.bmps.size > TS.maxBmps) { const [k, b] = TS.bmps.entries().next().value; TS.bmps.delete(k); b && b.close && b.close(); }
}
/* se il server non risponde, rallenta (niente raffiche di richieste) e riprova da solo */
let pumpLater = 0;
function pump() {
  const wait = (TS.backoff || 0) - Date.now();
  if (wait > 0) { if (!pumpLater) pumpLater = setTimeout(() => { pumpLater = 0; pump(); }, wait); netUI(); return; }
  const max = TS.fails >= 8 ? 1 : TS.max;
  while (TS.active < max && TS.q.length) {
    // i tasselli "originali" possono metterci 10-60 s: al massimo NATMAX insieme, cosi'
    // le connessioni del browser restano libere per gli altri layer
    let i = 0;
    if (TS.natActive >= TS.natMax) { i = TS.q.findIndex(j => !j.nat); if (i < 0) break; }
    const [job] = TS.q.splice(i, 1); TS.active++; if (job.nat) TS.natActive++;
    job.run().finally(() => { TS.active--; if (job.nat) TS.natActive--; pump(); netUI(); });
  }
  netUI();
}
// anche gli accumuli sono lenti la prima volta (una griglia WCS per ogni 10-15 min): stessa coda "lenta"
const isNatUrl = url => url.startsWith('/api/nativo/') || url.startsWith('/api/accumulo/');
/* il browser apre al massimo 6 connessioni per indirizzo: meta' dei tasselli va a "localhost" e meta' a
   "127.0.0.1" (stesso server), cosi' le richieste in parallelo raddoppiano. La chiave in cache resta l'URL relativo. */
const ALT_HOST = (() => {
  const h = location.hostname, p = location.port ? ':' + location.port : '';
  if (h === '127.0.0.1') return `${location.protocol}//localhost${p}`;
  if (h === 'localhost') return `${location.protocol}//127.0.0.1${p}`;
  return null;
})();
function fetchTarget(url) {
  if (!ALT_HOST || S.mode !== 'api' || !url.startsWith('/api/')) return url;
  let h = 0; for (let i = url.length - 12; i < url.length; i++) h = (h * 31 + url.charCodeAt(i)) | 0;
  return (h & 1) ? ALT_HOST + url : url;
}
/* scarica il blob (o null per tile inesistente). undefined = annullato/errore rete */
function fetchBlob(url, urgent, gen) {
  if (TS.blobs.has(url)) return Promise.resolve(touch(TS.blobs, url));
  const p = TS.pend.get(url);
  if (p) {
    if (urgent) { const i = TS.q.findIndex(j => j.url === url); if (i > 0) { const [j] = TS.q.splice(i, 1); j.kind = 'view'; TS.q.unshift(j); } else if (i === 0) TS.q[0].kind = 'view'; }
    return p.promise;
  }
  let resolve; const promise = new Promise(r => (resolve = r));
  const nat = isNatUrl(url);
  const job = {
    url, kind: urgent ? 'view' : 'pre', gen, resolve, nat,
    run: async () => {
      const ac = new AbortController(); const t = setTimeout(() => ac.abort(), nat ? 150000 : 25000);
      try {
        const r = await fetch(fetchTarget(url), { signal: ac.signal, mode: 'cors' });
        // originali: server occupato o errore temporaneo -> non e' un tile vuoto, si riprova piu' tardi
        if (nat && r.ok && r.headers.get('X-Tile-Vuoto') && r.headers.get('X-Cache') === 'riprova') {
          TS.pend.delete(url); resolve(undefined); return;
        }
        let blob = null;
        if (r.ok) { blob = await r.blob(); if (!blob.size || !/image/.test(blob.type || 'image')) blob = null; }
        else if (r.status >= 500) { TS.fails++; TS.pend.delete(url); resolve(undefined); return; }
        putBlob(url, blob); TS.pend.delete(url);
        if (TS.fails >= 8) { TS.fails = 0; onNetworkBack(); }
        TS.fails = 0; resolve(blob);
      } catch {
        if (nat) { TS.pend.delete(url); resolve(undefined); return; } // lento, non rete giu'
        TS.fails++; TS.pend.delete(url); resolve(undefined);
        if (TS.fails >= 8) TS.backoff = Date.now() + Math.min(30000, 2000 * Math.floor(TS.fails / 8));
      }
      finally { clearTimeout(t); }
    },
  };
  TS.pend.set(url, { promise, job });
  urgent ? TS.q.unshift(job) : TS.q.push(job);
  pump();
  return promise;
}
function onNetworkBack() {
  setTimeout(() => {
    toast('Collegamento ripristinato: ricarico le immagini.', 'ok', 2500);
    for (const e of S.stack) if (e.vis) e.layer.repaint();
    if (S.cmp.layer) S.cmp.layer.repaint();
    schedulePreload();
  }, 0);
}
function cancelPreload(gen) {
  TS.q = TS.q.filter(j => { if (j.kind === 'pre' && j.gen !== gen) { TS.pend.delete(j.url); j.resolve(undefined); return false; } return true; });
}
/* tassello tolto dalla mappa (zoom/spostamento): la sua richiesta ancora in coda non serve piu' */
function dropQueued(url) {
  if (!url) return;
  const i = TS.q.findIndex(j => j.url === url && j.kind === 'view');
  if (i < 0) return;
  const [j] = TS.q.splice(i, 1); TS.pend.delete(url); j.resolve(undefined);
}
const decoding = new Map();
/* bitmap decodificata pronta per il disegno: ImageBitmap | null (vuoto) | undefined (non disponibile) */
function getBitmap(url, urgent) {
  if (TS.bmps.has(url)) return Promise.resolve(touch(TS.bmps, url));
  if (decoding.has(url)) return decoding.get(url);
  const p = fetchBlob(url, urgent).then(async blob => {
    if (blob === undefined) return undefined;
    if (blob === null) { putBmp(url, null); return null; }
    try { const b = await createImageBitmap(blob); putBmp(url, b); return b; } catch { putBmp(url, null); return null; }
  }).finally(() => decoding.delete(url));
  decoding.set(url, p);
  return p;
}

/* =========================================================================
 * 6. Layer canvas Leaflet
 * ========================================================================= */
const SatLayer = L.GridLayer.extend({
  initialize(entry, timeFn, opts) {
    this.entry = entry; this.timeFn = timeFn;
    L.GridLayer.prototype.initialize.call(this, opts);
  },
  onAdd(m) {
    L.GridLayer.prototype.onAdd.call(this, m);
    // tassello rimosso prima di arrivare: la sua richiesta in coda viene annullata (niente lavoro inutile)
    this.on('tileunload', this._onUnload, this);
  },
  onRemove(m) { this.off('tileunload', this._onUnload, this); L.GridLayer.prototype.onRemove.call(this, m); },
  _onUnload(ev) { const c = ev.tile; if (c && c._want && !c._raw) dropQueued(c._want); },
  createTile(coords, done) {
    const c = document.createElement('canvas'); c.width = c.height = 256; c.className = 'sat-tile';
    c._co = coords;
    let fin = () => { fin = null; raf(() => done(null, c)); };
    this.paint(c, () => fin && fin());
    return c;
  },
  /* segnaposto: lo stesso istante preso da un tassello gia' in memoria a un altro zoom.
     Padre (z-1..z-5) ritagliato e ingrandito, poi i 4 figli (z+1) se ci sono: niente schermo vuoto
     dopo uno zoom, l'animazione continua sfocata e si affina quando arrivano i tasselli veri. */
  fallbackParts(co, t, kick) {
    const parts = [];
    for (let dz = 1; dz <= 5 && co.z - dz >= 0; dz++) {
      const pz = co.z - dz, px = co.x >> dz, py = co.y >> dz;
      const u = this.urlFor({ z: pz, x: px, y: py }, t); if (!u) break;
      const b = TS.bmps.get(u);
      if (b !== undefined) { const n = 256 >> dz; parts.push([b, (co.x - (px << dz)) * n, (co.y - (py << dz)) * n, n, 0, 0, 256]); break; }
      if (TS.blobs.get(u)) { if (kick) kick(u); break; }
    }
    const Lr = cat(this.entry.id);
    if (co.z + 1 <= Lr.maxZoom) {
      const kids = [];
      for (let k = 0; k < 4; k++) {
        const cx = co.x * 2 + (k & 1), cy = co.y * 2 + (k >> 1);
        const u = this.urlFor({ z: co.z + 1, x: cx, y: cy }, t);
        const b = u && TS.bmps.get(u);
        if (b !== undefined && u) kids.push([b, 0, 0, 256, (k & 1) * 128, (k >> 1) * 128, 128]);
        else if (u && TS.blobs.get(u) && kick) kick(u);
      }
      if (kids.length === 4 || (kids.length && parts.length)) parts.push(...kids);
    }
    return parts;
  },
  drawFallback(c, url, t, Lr) {
    const parts = this.fallbackParts(c._co, t, u => getBitmap(u, false).then(() => {
      // il padre era solo compresso in memoria: decodificato, ridisegna se il vero non e' ancora arrivato
      if (c._want === url && c._raw !== url && this._map) { const p = this.fallbackParts(c._co, t); if (p.length) drawParts(c, p, Lr, this.entry.pal); }
    }));
    if (!parts.length) return false;
    drawParts(c, parts, Lr, this.entry.pal);
    return true;
  },
  urlFor(co, t) {
    const Lr = cat(this.entry.id);
    return tileURL(Lr, entryTime(this.entry, Lr, t), co.z, co.x, co.y);
  },
  paint(c, fin) {
    const Lr = cat(this.entry.id);
    const url = this.urlFor(c._co, this.timeFn());
    if (!url) { fin && fin(); return; }
    if (c._want !== url) c._retry = 0;
    c._want = url;
    const ready = TS.bmps.get(url);
    if (ready !== undefined) { drawTile(c, ready, Lr, this.entry.pal, url); fin && fin(); return; }
    // segnaposto immediato dallo zoom vicino; il tassello "conta" come caricato (Leaflet non tiene schermo vuoto)
    const fb = this.drawFallback(c, url, this.timeFn(), Lr);
    if (fb) { fin && fin(); fin = null; }
    // urgente solo se serve davvero ora: durante l'animazione un fotogramma col segnaposto non scavalca l'ordine
    // del precaricamento (altrimenti ogni fotogramma mostrato rimetterebbe in testa i suoi tasselli e nessuno
    // si completerebbe); i tasselli fuori schermo tenuti da keepBuffer non passano mai davanti
    const urgent = this.inView(c._co) && !(fb && S.playing);
    getBitmap(url, urgent).then(b => {
      if (c._want === url && b !== undefined) drawTile(c, b, Lr, this.entry.pal, url);
      // tassello originale non ancora pronto (server lento/occupato): riprova da solo, senza errori
      if (b === undefined && (Lr.nativo || accOn(Lr)) && (c._retry || 0) < 6) {
        c._retry = (c._retry || 0) + 1;
        setTimeout(() => { if (c._want === url && this._map && c.isConnected) this.paint(c); }, 3000 * c._retry);
      }
      fin && fin();
    });
  },
  repaint() { if (!this._map) return; for (const k in this._tiles) this.paint(this._tiles[k].el); },
  currentTiles() { const out = []; for (const k in this._tiles) { const t = this._tiles[k]; if (t.current) out.push(t); } return out; },
  /* solo i tasselli che toccano davvero lo schermo (keepBuffer ne tiene altri intorno, non vanno aspettati) */
  inView(co) {
    if (!this._map) return false;
    const m = this._map, sc = m.getZoomScale(m.getZoom(), co.z) * 256, b = m.getPixelBounds();
    return co.x * sc < b.max.x && (co.x + 1) * sc > b.min.x && co.y * sc < b.max.y && (co.y + 1) * sc > b.min.y;
  },
  visibleTiles() { return this.currentTiles().filter(t => this.inView(t.coords)); },
});
function drawParts(c, parts, Lr, pal) {
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.clearRect(0, 0, 256, 256);
  const p = palActive(pal);
  ctx.imageSmoothingEnabled = !p; // con palette si ricolorano i valori: niente sfumature che li mescolano
  for (const [b, sx, sy, sw, dx, dy, dw] of parts) if (b) ctx.drawImage(b, sx, sy, sw, sw, dx, dy, dw, dw);
  ctx.imageSmoothingEnabled = true;
  if (p) { const img = ctx.getImageData(0, 0, 256, 256); applyPalette(img, Lr, pal); ctx.putImageData(img, 0, 0); }
  else if (LEG.hl && LEG.hl.id === Lr.id) legHighlightCanvas(c, Lr);
  c._raw = null; c._fb = 1;
}
function drawTile(c, bmp, Lr, pal, url) {
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.clearRect(0, 0, 256, 256);
  c._raw = url; c._fb = 0;
  if (!bmp) return;
  if (!palActive(pal)) { ctx.drawImage(bmp, 0, 0, 256, 256); if (LEG.hl && LEG.hl.id === Lr.id) legHighlightCanvas(c, Lr); return; }
  const key = url + '|' + palKey(pal) + (decoderFor(Lr) ? '|d' : '|l');
  let img = palCache.get(key);
  if (img) touch(palCache, key);
  else {
    ctx.drawImage(bmp, 0, 0, 256, 256);
    img = ctx.getImageData(0, 0, 256, 256);
    applyPalette(img, Lr, pal);
    palCache.set(key, img);
    while (palCache.size > PALMAX) palCache.delete(palCache.keys().next().value);
  }
  ctx.putImageData(img, 0, 0);
}

/* =========================================================================
 * 7. Mappa
 * ========================================================================= */
const map = L.map('map', {
  zoomControl: false, worldCopyJump: true, minZoom: 2, maxZoom: 16, zoomSnap: 1,
  center: [35, -25], zoom: 3, preferCanvas: false, attributionControl: true,
});
map.attributionControl.setPrefix(false);
map.attributionControl.addAttribution('Immagini <a href="https://earthdata.nasa.gov/gibs" target="_blank" rel="noopener">NASA GIBS</a>, EUMETSAT, originali NOAA GOES/Himawari su AWS, eventi NASA EONET e GDACS');
L.control.zoom({ position: 'bottomright', zoomInTitle: 'Avvicina', zoomOutTitle: 'Allontana' }).addTo(map);
map.createPane('cmp').style.zIndex = 350;
const evLayer = L.layerGroup().addTo(map);
const trkLayer = L.layerGroup().addTo(map);

/* =========================================================================
 * 8. Pila dei livelli
 * ========================================================================= */
const frameTime = () => S.frames[S.idx] || new Date();
function mkEntry(id, o = {}) {
  const Lr = cat(id);
  const e = { id, blend: o.blend || '', op: 1, vis: o.vis ?? true, pal: Object.assign(defPal(Lr), o.pal || {}), fixT: o.fixT || null };
  e.layer = new SatLayer(e, frameTime, {
    tileSize: 256, maxNativeZoom: Lr.maxZoom, maxZoom: 18, opacity: e.op,
    updateWhenZooming: false, updateWhenIdle: true, keepBuffer: 3, className: 'sat-layer',
  });
  ensureDecoder(Lr);
  return e;
}
function insertIndex(Lr) {
  if (Lr.isRef) return 0;
  const refs = S.stack.filter(e => cat(e.id).isRef).length;
  if (Lr.tipo !== 'base') return refs;
  const nonBase = S.stack.filter(e => cat(e.id).isRef || cat(e.id).tipo !== 'base').length;
  return nonBase;
}
function addLayer(id, o = {}, silent) {
  if (!cat(id) || S.stack.some(e => e.id === id)) return;
  const e = mkEntry(id, o);
  const at = o.at ?? insertIndex(cat(id));
  S.stack.splice(at, 0, e);
  if (!silent) afterStackChange();
}
function removeLayer(id, silent) {
  const i = S.stack.findIndex(e => e.id === id); if (i < 0) return;
  const [e] = S.stack.splice(i, 1); map.removeLayer(e.layer);
  if (e.auto && !silent && typeof AUTODET !== 'undefined') AUTODET.dismissed.add(e.id);
  if (S.sel === id) S.sel = null;
  if (!silent) afterStackChange();
}
/* modalita' di fusione (mix-blend-mode) del layer con quelli sotto */
const BLENDS = [['', 'Automatica (si combina con i livelli sotto)'], ['normal', 'Normale (copre i livelli sotto)'], ['multiply', 'Moltiplica (scurisce, ombre)'], ['screen', 'Scherma (schiarisce, luci)'], ['overlay', 'Sovrapponi (contrasto)'], ['soft-light', 'Luce soffusa'], ['hard-light', 'Luce intensa'], ['color', 'Solo colore (tinge)'], ['luminosity', 'Solo luminosità']];
/* Combinazione automatica: tutti i livelli restano al 100% ma si vedono insieme, in qualunque ordine.
   Il livello piu' in basso e' normale; sopra di lui: rilievo/batimetria = overlay (ombre sul sotto),
   dati colorati (SST, pioggia, aerosol...) = color (colori sopra la luminosita' del sotto),
   immagini satellitari = overlay; punti/linee (fuochi, coste) restano normali. Si puo' forzare "Normale" per livello. */
function effBlend(e) {
  if (e.blend) return e.blend === 'normal' ? '' : e.blend;
  const Lr = cat(e.id); if (!Lr || Lr.isRef) return '';
  const vis = S.stack.filter(x => x.vis && cat(x.id) && !cat(x.id).isRef);
  if (!vis.length || vis[vis.length - 1] === e) return '';
  if (/Thermal_Anomalies|FireTemp|Fire_|Hotspot|Fuochi/i.test(e.id)) return '';
  if (/Relief|Bathymetry|Black_Marble|BlackMarble/i.test(e.id)) return 'overlay';
  if (Lr.tipo === 'overlay') return 'color';
  return 'overlay';
}
function applyBlend(e) { const c = e.layer.getContainer && e.layer.getContainer(); if (c) c.style.mixBlendMode = effBlend(e); }
function syncMapLayers() {
  const n = S.stack.length;
  S.stack.forEach((e, i) => {
    e.layer.setZIndex(n - i + 1);
    e.layer.setOpacity(e.op);
    if (e.vis && !map.hasLayer(e.layer)) map.addLayer(e.layer);
    if (!e.vis && map.hasLayer(e.layer)) map.removeLayer(e.layer);
    applyBlend(e);
  });
  updateOverzoom();
}
/* oltre lo zoom nativo Leaflet ingrandisce il tassello: fino a 2x resta liscio,
   da 4x in su pixel netti (image-rendering: pixelated) invece di una macchia sfocata */
function updateOverzoom() {
  const z = map.getZoom();
  const lays = S.stack.map(e => e.layer).concat(S.cmp && S.cmp.layer ? [S.cmp.layer] : []);
  for (const ly of lays) {
    const c = ly.getContainer && ly.getContainer(), Lr = cat(ly.entry.id);
    if (c && Lr) c.classList.toggle('overzoom-px', z - Lr.maxZoom >= 2);
  }
}
map.on('zoomend', updateOverzoom);
function afterStackChange(keepTime = true) {
  if (typeof autoDetChip === 'function') queueMicrotask(autoDetChip);
  if (S.sel && !S.stack.some(e => e.id === S.sel)) S.sel = null;
  if (!S.sel) S.sel = (S.stack.find(e => cat(e.id).timed && !cat(e.id).isRef) || S.stack[0])?.id || null;
  syncMapLayers();
  computeFrames(keepTime);
  renderStack(); renderCatalog(); renderPalettePanel(); fillCompareSelect();
  accUI(); updateStatus(); schedulePreload(); scheduleHash();
}

/* --- interfaccia pila --- */
function renderStack() {
  const box = $('#stack');
  $('#stackCount').textContent = S.stack.length;
  if (!S.stack.length) {
    box.innerHTML = `<div class="empty">Nessun layer in mappa. Scegline uno dal catalogo qui sotto.</div>`;
    return;
  }
  box.innerHTML = S.stack.map((e, i) => {
    const Lr = cat(e.id), pal = palById(e.pal.id);
    const freq = e.fixT ? `del ${fmtD(parseTime(e.fixT))}` : !Lr.timed ? 'fisso' : Lr.tempoTipo === 'date' ? 'giornaliero' : `ogni ${Lr.passoMin} min`;
    return `<div class="srow ${e.vis ? '' : 'off'} ${S.sel === e.id ? 'is-sel' : ''}" data-id="${esc(e.id)}">
      <button class="ibtn" data-a="vis" aria-pressed="${e.vis}" title="${e.vis ? 'Nascondi' : 'Mostra'}">${icon(e.vis ? 'eye' : 'eyeoff')}</button>
      <span class="nm" data-a="sel" title="${esc(Lr.nome)}">${esc(Lr.nome)}</span>
      <span class="ops">
        <button class="ibtn" data-a="pal" title="Palette e regolazioni">${icon('palette')}</button>
        <button class="ibtn" data-a="up" title="Sposta sopra" ${i === 0 ? 'disabled' : ''}>${icon('up')}</button>
        <button class="ibtn" data-a="down" title="Sposta sotto" ${i === S.stack.length - 1 ? 'disabled' : ''}>${icon('down')}</button>
        <button class="ibtn" data-a="rm" title="Togli dalla mappa">${icon('x')}</button>
      </span>
      <span class="meta"><span>${esc(Lr.sat)}</span><span>${freq}</span>${e.pal.id !== 'orig' ? `<span class="pal-chip" style="background:${gradCSS(pal.stops)}" title="${esc(pal.nome)}"></span>` : ''}</span>
      ${e.pal.id === 'orig' && (accOn(Lr) ? Lr.scalaAccumulo : Lr.scala) ? `<div class="lg">${legendHTML(accOn(Lr) ? Lr.scalaAccumulo : Lr.scala, accOn(Lr) ? `dalle ${fmtT(parseTime(accFrom(Lr)))} UTC` : '')}</div>` : ''}
      <details class="leg" data-id="${esc(e.id)}" ${LEG.open.has(e.id) ? 'open' : ''}><summary>${icon('chev', 'i chev')}Legenda: che cosa significano i colori</summary><div class="leg-b">${legPanelHTML(Lr)}</div></details>
    </div>`;
  }).join('');
  S.stack.forEach(e => ensureLegend(cat(e.id)));
  renderMapLegend();
}
/* legenda a colori reale del prodotto: blocchi uguali per classe, etichette ai confini */
const fmtV = v => (v < 0.01 ? '0' : num(v, v < 1 ? 1 : 0));
function legendHTML(sc, extra = '') {
  const v = sc.voci || []; if (!v.length) return '';
  const n = v.length, every = n > 9 ? 2 : 1;
  const open = v[n - 1][1] == null;
  // etichette ai confini fra le classi; l'ultima classe aperta si legge "50+" al suo inizio
  const ticks = v.map((x, i) => {
    if (i === n - 1 && open) return `<span style="left:${(i / n) * 100}%;transform:translateX(-50%)">${fmtV(x[0])}+</span>`;
    if (i % every || (open && i === n - 2 && every > 1)) return '';
    return `<span style="left:${(i / n) * 100}%">${fmtV(x[0])}</span>`;
  }).join('') + (open ? '' : `<span style="left:100%">${fmtV(v[n - 1][1])}</span>`);
  return `<div class="lg-t"><span>${esc(sc.titolo || 'Legenda')}${extra ? ` <b>${esc(extra)}</b>` : ''}</span><b>${esc(sc.unita || '')}</b></div>
    <div class="lg-bar ${sc.continua ? 'cont' : ''}" style="${sc.continua ? `background:linear-gradient(90deg,${v.map(x => x[2]).join(',')})` : ''}">${sc.continua ? '' : v.map(x => `<i style="background:${x[2]}" title="${fmtV(x[0])}${x[1] == null ? '+' : '–' + fmtV(x[1])} ${esc(sc.unita || '')}"></i>`).join('')}</div>
    <div class="lg-ticks">${ticks}</div>`;
}
/* classe della legenda piu' vicina a un colore (per la sonda dei prodotti a classi) */
function scalaClass(sc, r, g, b) {
  let best = null, bd = 1e9;
  for (const x of sc.voci || []) { const [R, G, B] = hex2(x[2]); const d = (R - r) ** 2 + (G - g) ** 2 + (B - b) ** 2; if (d < bd) { bd = d; best = x; } }
  return bd < 2500 ? best : null;
}
const classTxt = (x, u) => `${x[1] == null ? '≥ ' + fmtV(x[0]) : fmtV(x[0]) + '–' + fmtV(x[1])} ${u}`;
$('#stack').addEventListener('click', ev => {
  const b = ev.target.closest('[data-a]'); const row = ev.target.closest('.srow'); if (!b || !row) return;
  const id = row.dataset.id, i = S.stack.findIndex(e => e.id === id), e = S.stack[i];
  switch (b.dataset.a) {
    case 'vis': e.vis = !e.vis; afterStackChange(); break;
    case 'sel': S.sel = id; renderStack(); renderPalettePanel(); break;
    case 'pal': S.sel = id; renderStack(); renderPalettePanel(); showTab('tPalette'); break;
    case 'up': if (i > 0) { S.stack.splice(i - 1, 0, S.stack.splice(i, 1)[0]); afterStackChange(); } break;
    case 'down': if (i < S.stack.length - 1) { S.stack.splice(i + 1, 0, S.stack.splice(i, 1)[0]); afterStackChange(); } break;
    case 'rm': removeLayer(id); break;
  }
});
$('#stack').addEventListener('input', ev => {
  if (ev.target.dataset.a !== 'op') return;
  const id = ev.target.closest('.srow').dataset.id, e = S.stack.find(x => x.id === id);
  e.op = ev.target.value / 100; e.layer.setOpacity(e.op);
  ev.target.nextElementSibling.textContent = `${ev.target.value}%`;
  scheduleHash();
});

/* --- catalogo --- */
let catFilter = '';
const openGroups = new Set(store_.get('meteoSat.gruppiAperti', ['visibile', 'ir']));
function renderCatalog() {
  const box = $('#catalog'); const q = catFilter.trim().toLowerCase();
  let total = 0;
  box.innerHTML = S.groups.map(g => {
    const ls = g.layers.filter(Lr => !q || `${Lr.nome} ${Lr.id} ${Lr.descr || ''} ${Lr.sat} ${g.nome}`.toLowerCase().includes(q));
    total += ls.length;
    if (!ls.length) return '';
    const open = q || openGroups.has(g.id);
    return `<details class="grp" data-g="${esc(g.id)}" ${open ? 'open' : ''}>
      <summary>${icon('chev', 'i chev')}${esc(g.nome)}<span class="n">${ls.length}</span></summary>
      ${ls.map(Lr => {
        const on = S.stack.some(e => e.id === Lr.id);
        const km = nativeKm(Lr.maxZoom);
        const fk = v => v < 1 ? num(v * 1000, 0) + ' m' : num(v, v < 10 ? 1 : 0) + ' km';
        // gruppo alta risoluzione: risoluzione del sensore + zoom massimo dei tasselli (oltre = ingrandimento)
        const res = g.id === 'nativo' && Lr.risoluzioneKm
          ? `risoluzione vera ${fk(Lr.risoluzioneKm)} al nadir (zoom massimo ${Lr.maxZoom}), ritardo 10-15 min`
          : g.id === 'altarisoluzione' && Lr.risoluzioneKm
          ? `risoluzione nativa ${fk(Lr.risoluzioneKm)} (tasselli fino a zoom ${Lr.maxZoom}, oltre si ingrandisce)`
          : `~${fk(km)}/px`;
        const tag = !Lr.timed ? '' : Lr.tempoTipo === 'date' ? '<span class="tag">giornaliero</span>' : `<span class="tag live">${Lr.passoMin} min</span>`;
        return `<label class="lrow" title="${esc(Lr.id)}">
          <input type="checkbox" data-id="${esc(Lr.id)}" ${on ? 'checked' : ''}>
          <span class="t">${esc(Lr.nome)}</span>${tag}
          <span class="d">${esc(Lr.sat)}, ${res}${Lr.descr ? '. ' + esc(Lr.descr) : ''}</span>
        </label>`;
      }).join('')}
    </details>`;
  }).join('') || `<div class="empty">Nessun layer corrisponde a “${esc(catFilter)}”.</div>`;
  $('#catCount').textContent = `${S.byId.size} disponibili`;
}
$('#catalog').addEventListener('change', ev => {
  const id = ev.target.dataset.id; if (!id) return;
  ev.target.checked ? addLayer(id) : removeLayer(id);
});
$('#catalog').addEventListener('toggle', ev => {
  const d = ev.target.closest('details'); if (!d || catFilter) return;
  d.open ? openGroups.add(d.dataset.g) : openGroups.delete(d.dataset.g);
  store_.set('meteoSat.gruppiAperti', [...openGroups]);
}, true);
$('#catFilter').addEventListener('input', debounce(ev => { catFilter = ev.target.value; renderCatalog(); }, 120));

/* =========================================================================
 * 9. Fotogrammi, timeline e animazione
 * ========================================================================= */
const MAXF = 120;
function drivers() { return S.stack.filter(e => e.vis).map(e => cat(e.id)).filter(Lr => Lr.timed); }
function computeFrames(keepTime = true) {
  const prev = S.frames[S.idx]; const wasLast = S.idx >= S.frames.length - 1;
  const D = drivers(); const geo = D.filter(Lr => Lr.tempoTipo === 'datetime' && Lr.passoMin < 1440);
  // il layer geostazionario piu' in alto comanda passo e ultimo orario; gli altri si agganciano
  const primary = geo.find(Lr => !Lr.isRef);
  let base, end;
  if (primary) { base = primary.passoMin; end = +primary.max; }
  else if (D.length) { base = 1440; end = Math.max(...D.map(Lr => Math.min(+Lr.max, Lr.tempoTipo === 'date' ? Math.max(dayCap(), +(Lr.min || 0)) : +Lr.max))); }
  else { base = 10; end = Math.floor(Date.now() / (10 * MIN)) * 10 * MIN - 30 * MIN; }
  let from, to;
  if (S.custom) { from = +S.custom.from; to = Math.min(+S.custom.to, end); if (to < from) to = +S.custom.to; }
  else { to = end; from = end - S.range * MIN; }
  let step = Math.max(base, S.stepMin || 0);
  const dur = to - from;
  if (dur / (step * MIN) + 1 > MAXF) step = Math.ceil(dur / MIN / (MAXF - 1) / base) * base;
  const sMs = step * MIN, bMs = base * MIN;
  // orbite polari: fotogrammi all'indietro dall'istante esatto dell'ultima orbita (niente griglia fissa)
  if (!(primary && primary.orbite && !S.custom)) to = Math.floor(to / bMs) * bMs;
  const fr = [];
  for (let t = to; t >= from - 1 && fr.length < MAXF; t -= sMs) fr.unshift(new Date(t));
  if (!fr.length) fr.push(new Date(to));
  S.frames = fr; S.step = step;
  if (keepTime && prev && !(S.live && wasLast)) {
    let best = fr.length - 1, bd = Infinity;
    fr.forEach((t, i) => { const d = Math.abs(t - prev); if (d < bd) { bd = d; best = i; } });
    S.idx = best;
  } else S.idx = fr.length - 1;
  S.stat = fr.map(() => ({ tot: 0, done: 0, err: 0 }));
  buildStrip();
  // l'istante mostrato puo' essere cambiato (nuovo ultimo orario): ridisegna
  for (const e of S.stack) if (e.vis && map.hasLayer(e.layer)) e.layer.repaint();
  if (S.cmp.layer) S.cmp.layer.repaint();
  updateFrameUI();
  if (S.accum) renderStack(); // l'etichetta "accumulo dalle..." segue il primo fotogramma
}

/* --- striscia fotogrammi --- */
const strip = $('#strip');
function buildStrip() {
  const n = S.frames.length;
  strip.innerHTML = S.frames.map(() => '<div class="tick"><i></i></div>').join('') + '<div class="cursor" id="cursor"></div>';
  strip.setAttribute('aria-valuemax', n - 1);
  // etichette asse: intervallo "rotondo" con al massimo ~8 etichette
  const total = n > 1 ? (S.frames[n - 1] - S.frames[0]) / MIN : 0;
  const steps = [10, 20, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080];
  const every = steps.find(s => total / s <= 7) || 10080;
  const ax = $('#axis'); let html = '';
  let prevBucket = null;
  S.frames.forEach((t, i) => {
    // etichetta sul primo fotogramma di ogni nuovo intervallo "rotondo"
    const bucket = Math.floor(+t / MIN / every);
    const first = prevBucket === null;
    if (!first && bucket !== prevBucket) {
      const isDay = t.getUTCHours() * 60 + t.getUTCMinutes() < S.step || every >= 1440;
      const lbl = isDay || S.step >= 1440 ? `${pad(t.getUTCDate())}-${pad(t.getUTCMonth() + 1)}` : fmtT(t);
      html += `<span class="${isDay ? 'day' : ''}" style="left:${((i + 0.5) / n) * 100}%">${lbl}</span>`;
      strip.children[i].classList.add('hr');
    }
    prevBucket = bucket;
  });
  if (!html && n) html = `<span style="left:${(0.5 / n) * 100}%">${fmtT(S.frames[0])}</span>` + (n > 1 ? `<span style="left:${((n - 0.5) / n) * 100}%">${fmtT(S.frames[n - 1])}</span>` : '');
  ax.innerHTML = html;
  paintTicks();
}
let ticksRaf = 0;
function paintTicks() {
  if (ticksRaf) return;
  ticksRaf = raf(() => {
    ticksRaf = 0;
    const kids = strip.children; let tot = 0, done = 0, pronti = 0, mostrabili = 0;
    S.stat.forEach((s, i) => {
      const el = kids[i]; if (!el) return;
      const f = s.tot ? s.done / s.tot : 0;
      const full = s.tot > 0 && s.done >= s.tot && !s.err;
      // fotogramma non ancora completo ma gia' mostrabile con il segnaposto dello zoom vicino
      const prov = !full && frameState(i) > 0;
      el.firstChild.style.height = `${Math.round((prov ? Math.max(f, 0.35) : f) * 100)}%`;
      el.classList.toggle('full', full);
      el.classList.toggle('prov', prov);
      el.classList.toggle('err', s.err > 0 && s.done >= s.tot);
      el.classList.toggle('cur', i === S.idx);
      tot += s.tot; done += s.done; if (full || !s.tot) pronti++; if (full || prov || !s.tot) mostrabili++;
    });
    const pr = $('#preload'); const f = tot ? done / tot : 1;
    pr.firstChild.style.width = `${f * 100}%`;
    pr.classList.toggle('done', f >= 1);
    S.preloadPct = f;
    // "N fotogrammi su M pronti" con stima del tempo che manca (dal ritmo di arrivo dall'ultimo zoom/spostamento)
    const n = S.stat.length, lab = $('#frReady');
    if (lab) {
      if (f >= 1 || n < 2) { lab.hidden = true; }
      else {
        const el_s = (performance.now() - (S.preT0 || performance.now())) / 1000, fatti = done - (S.preDone0 || 0);
        const eta = fatti > 0 && el_s > 1.5 ? (tot - done) * el_s / fatti : null;
        lab.hidden = false;
        lab.textContent = `${pronti} fotogrammi su ${n} pronti` + (mostrabili > pronti ? ` (${mostrabili} già visibili in anteprima)` : '') + (eta ? `, circa ${fmtSec(eta)}` : '');
      }
      S.readyTxt = lab.hidden ? '' : `${pronti}/${n} fotogrammi pronti`;
    }
    netUI();
  });
}
function placeCursor() {
  const n = S.frames.length, c = $('#cursor'); if (!c) return;
  c.style.left = `${((S.idx + 0.5) / n) * 100}%`;
  strip.setAttribute('aria-valuenow', S.idx);
  const t = S.frames[S.idx]; if (t) strip.setAttribute('aria-valuetext', `${fmtD(t)} ${fmtT(t)} UTC`);
}
function idxFromX(x) { const r = strip.getBoundingClientRect(); return clamp(Math.floor(((x - r.left) / r.width) * S.frames.length), 0, S.frames.length - 1); }
let scrubbing = false;
strip.addEventListener('pointerdown', ev => { scrubbing = true; strip.setPointerCapture(ev.pointerId); pause(); setFrame(idxFromX(ev.clientX)); });
strip.addEventListener('pointermove', ev => {
  const i = idxFromX(ev.clientX), t = S.frames[i], h = $('#stripHover');
  if (t) { h.style.display = 'block'; h.textContent = `${fmtD(t)} ${fmtT(t)} UTC`; const r = strip.getBoundingClientRect(); h.style.left = `${((i + 0.5) / S.frames.length) * r.width}px`; }
  if (scrubbing && i !== S.idx) setFrame(i);
});
strip.addEventListener('pointerup', () => { scrubbing = false; });
strip.addEventListener('pointerleave', () => { $('#stripHover').style.display = 'none'; });

/* --- cambio fotogramma --- */
function setFrame(i) {
  i = clamp(i, 0, S.frames.length - 1);
  S.idx = i;
  for (const e of S.stack) if (e.vis) e.layer.repaint();
  if (S.cmp.layer) S.cmp.layer.repaint();
  updateFrameUI();
  scheduleHash();
}
function updateFrameUI() {
  const t = frameTime();
  $('#frTime').innerHTML = `${fmtT(t)}<small>UTC</small>`;
  const daily = S.step >= 1440;
  $('#frSub').textContent = `${fmtD(t)}${daily ? '' : `, Italia ${fmtItT(t)}`}  (${S.idx + 1}/${S.frames.length})`;
  $('#stIt').textContent = fmtIt(t);
  placeCursor(); paintTicks();
  updateSwipeLabels();
}

/* --- precaricamento: tutti i fotogrammi per la vista corrente ---
   Ordine (coda FIFO, le richieste della vista passano sempre davanti):
   1) solo per gli originali: bozza veloce a zoom piu' basso (z-2) per TUTTI i fotogrammi:
      con i segnaposto l'animazione gira subito, sfocata;
   2) risoluzione piena: fotogramma corrente e poi i successivi nell'ordine di riproduzione;
   3) in silenzio: anello di un tassello attorno alla vista, zoom precedente e (parte centrale) zoom successivo,
      cosi' piccoli spostamenti e un passo di rotella trovano gia' i tasselli in memoria. */
let preGen = 0;
function viewCoords(Lr, dz = 0, marg = 0, frac = 1) {
  const zoom = map.getZoom(), zt = Math.min(Math.round(zoom), Lr.maxZoom), z = zt + dz;
  if (z < 0 || z > Lr.maxZoom) return [];
  const n = 1 << z;
  const b = map.getPixelBounds(), s = Math.pow(2, z - zoom);
  let mnx = b.min.x, mny = b.min.y, mxx = b.max.x, mxy = b.max.y;
  if (frac < 1) { const cx = (mnx + mxx) / 2, cy = (mny + mxy) / 2, hw = (mxx - mnx) * frac / 2, hh = (mxy - mny) * frac / 2; mnx = cx - hw; mxx = cx + hw; mny = cy - hh; mxy = cy + hh; }
  const x0 = Math.floor(mnx * s / 256) - marg, x1 = Math.floor((mxx * s - 1) / 256) + marg;
  const y0 = clamp(Math.floor(mny * s / 256) - marg, 0, n - 1), y1 = clamp(Math.floor((mxy * s - 1) / 256) + marg, 0, n - 1);
  const out = [], seen = new Set();
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    const wx = ((x % n) + n) % n, k = `${wx}:${y}`; if (seen.has(k)) continue; seen.add(k); out.push({ z, x: wx, y });
  }
  return out;
}
function preloadTargets() {
  const t = [];
  for (const e of S.stack) { const Lr = cat(e.id); if (e.vis && Lr.timed) t.push({ Lr, off: 0, e }); }
  if (S.cmp.on && S.cmp.id && cat(S.cmp.id)?.timed) t.push({ Lr: cat(S.cmp.id), off: S.cmp.off });
  return t;
}
/* originali: chiede al server di preparare in background gli indici dei file di ogni fotogramma
   (una volta sola per istante: dopo zoom o spostamenti gli indici gia' letti si riusano) */
const natPrepSent = new Set();
function natPrepare(targets) {
  if (S.mode !== 'api' || S.frames.length < 2) return;
  for (const { Lr, off } of targets) {
    if (!Lr.nativo) continue;
    // prima il fotogramma corrente e i successivi: sono quelli che servono per partire
    const ord = playOrder().map(i => S.frames[i]);
    const ts = [...new Set(ord.map(t => layerTime(Lr, new Date(+t + off * MIN))))]
      .filter(x => !natPrepSent.has(`${Lr.id}|${x}`));
    if (!ts.length) continue;
    ts.forEach(x => natPrepSent.add(`${Lr.id}|${x}`));
    fetch(`/api/nativo/prepara/${Lr.nativo.sat}/${Lr.nativo.banda}?t=${encodeURIComponent(ts.join(','))}`).catch(() => {});
  }
}
/* indici dei fotogrammi nell'ordine in cui verranno mostrati, a partire da quello corrente */
function playOrder() {
  const n = S.frames.length, out = [];
  for (let k = 0; k < n; k++) out.push((S.idx + k) % n);
  return out;
}
function preload() {
  const gen = ++preGen; cancelPreload(gen);
  const targets = preloadTargets().map(o => ({ ...o, co: viewCoords(o.Lr) }));
  natPrepare(targets);
  const n = S.frames.length, order = playOrder();
  S.stat = S.frames.map(() => ({ tot: 0, done: 0, err: 0 }));
  const seenUrl = new Set();
  const tsOf = (o, t) => entryTime(o.e, o.Lr, new Date(+t + o.off * MIN));
  const want = (o, url, st) => {
    if (!url) return;
    if (st) st.tot++;
    if (TS.blobs.has(url)) { if (st) st.done++; return; }
    if (seenUrl.has(url) && !TS.pend.has(url)) { if (st) st.done++; return; }
    seenUrl.add(url);
    fetchBlob(url, false, gen).then(b => {
      if (gen !== preGen || !st) return;
      // i tasselli originali lenti non sono errori: verranno ripresi al prossimo giro
      st.done++; if (b === undefined && !o.Lr.nativo && !accOn(o.Lr)) st.err++;
      paintTicks();
    });
  };
  // 1) bozza a zoom piu' basso
  for (const o of targets) {
    let manca = 0;
    for (const fi of order) { const ts = tsOf(o, S.frames[fi]); for (const c of o.co) { const u = tileURL(o.Lr, ts, c.z, c.x, c.y); if (u && !TS.blobs.has(u)) manca++; } }
    // solo per gli originali (ogni tassello costa secondi): per i layer NASA/EUMETSAT la bozza ritarderebbe
    // la risoluzione piena (misurato: +30 s su 19 fotogrammi); dopo uno zoom bastano i segnaposto gia' in memoria
    if (!manca || !o.Lr.nativo) continue;
    o.bozza = o.Lr.nativo ? 2 : 1;
    const co = viewCoords(o.Lr, -o.bozza);
    for (const fi of order) { const ts = tsOf(o, S.frames[fi]); for (const c of co) want(o, tileURL(o.Lr, ts, c.z, c.x, c.y), null); }
  }
  // 2) risoluzione piena, fotogramma per fotogramma
  for (const fi of order) {
    const st = S.stat[fi], t = S.frames[fi];
    for (const o of targets) { const ts = tsOf(o, t); for (const c of o.co) want(o, tileURL(o.Lr, ts, c.z, c.x, c.y), st); }
  }
  // 3) in silenzio (solo se il lavoro resta ragionevole: niente raffiche verso i server)
  for (const o of targets) {
    if (o.Lr.nativo || accOn(o.Lr) || n * o.co.length > 2400) continue;
    const extra = [...viewCoords(o.Lr, 0, 1), ...(o.bozza ? [] : viewCoords(o.Lr, -1)), ...viewCoords(o.Lr, 1, 0, 0.5)];
    for (const fi of order) { const ts = tsOf(o, S.frames[fi]); for (const c of extra) want(o, tileURL(o.Lr, ts, c.z, c.x, c.y), null); }
  }
  S.preT0 = performance.now(); S.preDone0 = S.stat.reduce((a, x) => a + x.done, 0);
  paintTicks();
}
const schedulePreload = debounce(preload, 350);
map.on('moveend zoomend', () => { schedulePreload(); updateStatus(); scheduleHash(); });

/* stato di un fotogramma per la vista attuale: 2 = tutto alla risoluzione piena (in memoria),
   1 = mostrabile (alcuni tasselli con il segnaposto dello zoom vicino), 0 = manca qualcosa */
function frameState(i) {
  const t = S.frames[i]; if (!t) return 0;
  let st = 2;
  const chk = (ly, tt) => {
    for (const tile of ly.visibleTiles()) {
      const u = ly.urlFor(tile.coords, tt); if (!u) continue;
      if (TS.bmps.has(u) || TS.blobs.has(u)) continue;
      let kicked = false;
      const parts = ly.fallbackParts(tile.coords, tt, pu => { kicked = true; getBitmap(pu, false); });
      if (parts.length || kicked) { st = 1; continue; }
      return false;
    }
    return true;
  };
  for (const e of S.stack) { if (!e.vis || !cat(e.id).timed) continue; if (!chk(e.layer, t)) return 0; }
  if (S.cmp.layer && !chk(S.cmp.layer, new Date(+t + S.cmp.off * MIN))) return 0;
  return st;
}
/* decodifica i tasselli gia' scaricati del fotogramma (pochi millisecondi), senza aspettare la rete */
function decodeFrame(i) {
  const t = S.frames[i]; if (!t) return Promise.resolve();
  const jobs = [];
  const go = (ly, tt) => { for (const tile of ly.visibleTiles()) { const u = ly.urlFor(tile.coords, tt); if (u && !TS.bmps.has(u) && TS.blobs.get(u)) jobs.push(getBitmap(u, false)); } };
  for (const e of S.stack) if (e.vis && cat(e.id).timed) go(e.layer, t);
  if (S.cmp.layer) go(S.cmp.layer, new Date(+t + S.cmp.off * MIN));
  return Promise.all(jobs);
}

/* prepara un fotogramma: tutte le tile visibili decodificate alla risoluzione piena (usato dalle esportazioni) */
function prepareFrame(i) {
  const t = S.frames[i]; if (!t) return Promise.resolve();
  const jobs = [];
  for (const e of S.stack) {
    if (!e.vis || !cat(e.id).timed) continue;
    for (const tile of e.layer.visibleTiles()) { const u = e.layer.urlFor(tile.coords, t); if (u) jobs.push(getBitmap(u, true)); }
  }
  if (S.cmp.layer) {
    const tc = new Date(+t + S.cmp.off * MIN);
    for (const tile of S.cmp.layer.visibleTiles()) { const u = S.cmp.layer.urlFor(tile.coords, tc); if (u) jobs.push(getBitmap(u, true)); }
  }
  return Promise.all(jobs);
}

/* --- riproduzione ---
   Non aspetta piu' la risoluzione piena: mostra i fotogrammi gia' mostrabili (anche col segnaposto) e salta
   quelli non ancora arrivati, cosi' l'animazione parte con 2-3 fotogrammi e si arricchisce mentre gira. */
let playTimer = null;
function stepFrom(i, dir) {
  const n = S.frames.length; if (n < 2) return null;
  if (S.loop === 'loop') return [(i + 1) % n, 1];
  if (S.loop === 'once') return i + 1 < n ? [i + 1, 1] : null;
  let j = i + dir; if (j < 0 || j >= n) { dir = -dir; j = i + dir; }
  return [j, dir];
}
function nextIndex() { const r = stepFrom(S.idx, S.dir); if (!r) return null; S.dir = r[1]; return r[0]; }
async function tickPlay() {
  if (!S.playing) return;
  const t0 = performance.now(), n = S.frames.length;
  let i = S.idx, d = S.dir, pick = null, first = null;
  for (let k = 0; k < n; k++) {
    const r = stepFrom(i, d); if (!r) break;
    [i, d] = r; if (!first) first = r;
    if (frameState(i) > 0) { pick = r; break; }
    if (S.loop === 'once') break; // "una volta sola": nessun salto, aspetta il successivo
  }
  if (!first) { pause(); return; }
  if (!pick || pick[0] === S.idx) {
    // nessun altro fotogramma ancora mostrabile: si aspetta il precaricamento
    $('#tPlay').classList.add('wait');
    playTimer = setTimeout(tickPlay, 300); return;
  }
  $('#tPlay').classList.remove('wait');
  await Promise.race([decodeFrame(pick[0]), sleep(400)]);
  if (!S.playing) return;
  S.dir = pick[1];
  setFrame(pick[0]);
  let delay = 1000 / S.fps;
  if (pick[0] === n - 1 && S.loop !== 'bounce') delay += 900; // pausa sull'ultimo fotogramma
  playTimer = setTimeout(tickPlay, Math.max(0, delay - (performance.now() - t0)));
}
function play() {
  if (S.frames.length < 2) { toast('Un solo fotogramma in questo intervallo: allarga il periodo o aggiungi un layer geostazionario.', 'info'); return; }
  if (S.playing) return;
  S.playing = true; S.dir = 1;
  if (S.loop === 'once' && S.idx >= S.frames.length - 1) setFrame(0);
  playUI(); tickPlay();
}
function pause() { S.playing = false; clearTimeout(playTimer); $('#tPlay').classList.remove('wait'); playUI(); }
function playUI() {
  const b = $('#tPlay');
  b.innerHTML = `<svg><use href="#i-${S.playing ? 'pause' : 'play'}"/></svg>`;
  b.setAttribute('aria-label', S.playing ? 'Ferma animazione' : 'Avvia animazione');
}
$('#tPlay').onclick = () => (S.playing ? pause() : play());
$('#tPrev').onclick = () => { pause(); setFrame(S.idx - 1); };
$('#tNext').onclick = () => { pause(); setFrame(S.idx + 1); };
$('#tFirst').onclick = () => { pause(); setFrame(0); };
$('#tLast').onclick = () => { pause(); setFrame(S.frames.length - 1); };
const LOOPS = { loop: ['loop', 'Modalità: ciclo continuo'], bounce: ['bounce', 'Modalità: avanti e indietro'], once: ['once', 'Modalità: una volta sola'] };
function loopUI() { const [ic, t] = LOOPS[S.loop]; const b = $('#loopBtn'); b.innerHTML = icon(ic); b.title = t; b.setAttribute('aria-label', t); }
$('#loopBtn').onclick = () => { S.loop = { loop: 'bounce', bounce: 'once', once: 'loop' }[S.loop]; loopUI(); toast(LOOPS[S.loop][1], 'info', 1800); };
$('#fps').oninput = ev => { S.fps = +ev.target.value; $('#fpsO').textContent = `${S.fps} f/s`; };
$('#stepSel').onchange = ev => { S.stepMin = +ev.target.value; computeFrames(true); schedulePreload(); scheduleHash(); };

/* --- intervalli --- */
function rangeUI() {
  $$('#ranges .rbtn').forEach(b => b.setAttribute('aria-pressed', S.custom ? String(b.dataset.r === 'custom') : String(+b.dataset.r === S.range)));
  $('#liveBtn').setAttribute('aria-pressed', String(S.live));
}
$('#ranges').addEventListener('click', ev => {
  const b = ev.target.closest('.rbtn'); if (!b) return;
  if (b.dataset.r === 'custom') { openRangeModal(); return; }
  S.range = +b.dataset.r; S.custom = null; S.live = true;
  computeFrames(false); rangeUI(); schedulePreload(); scheduleHash();
});
$('#liveBtn').onclick = () => {
  S.live = !S.live;
  if (S.live) { S.custom = null; refreshLatest(true); }
  rangeUI(); scheduleHash();
};
/* --- accumulo delle precipitazioni --- */
function accUI() {
  const has = S.mode === 'api' && S.stack.some(e => e.vis && cat(e.id)?.accumulo);
  const b = $('#accBtn'); if (!b) return;
  b.hidden = !has;
  if (!has && S.accum) S.accum = false;
  b.setAttribute('aria-pressed', String(S.accum));
}
$('#accBtn')?.addEventListener('click', () => {
  S.accum = !S.accum;
  if (S.accum && S.cmp.on) { S.accum = false; toast('Chiudi prima il confronto: l’accumulo funziona sulla vista singola.', 'info'); accUI(); return; }
  const span = S.frames.length > 1 ? (S.frames[S.frames.length - 1] - S.frames[0]) / MIN : 0;
  if (S.accum) {
    if (span > 1440) toast('Accumulo: al massimo 24 ore. Scegli 1h, 3h, 6h, 12h o 24h.', 'err', 6000);
    else toast('Accumulo attivo: ogni fotogramma mostra i millimetri caduti dall’inizio dell’intervallo (somma dei valori reali EUMETSAT ogni 10-15 min). La prima volta in una zona nuova il calcolo richiede da 30 secondi a qualche minuto.', 'ok', 8000);
  }
  accUI();
  for (const e of S.stack) if (e.vis) e.layer.repaint();
  renderStack(); schedulePreload();
});
function toLocalInput(d) { return isoZ(d).slice(0, 16); }
function openRangeModal() {
  const to = S.frames[S.frames.length - 1] || new Date(), from = S.frames[0] || new Date(+to - 3 * 3600000);
  $('#rFrom').value = toLocalInput(from); $('#rTo').value = toLocalInput(to);
  openModal('#rangeModal');
}
$('#rApply').onclick = () => {
  const f = parseTime($('#rFrom').value + ':00Z'), t = parseTime($('#rTo').value + ':00Z');
  if (!f || !t || t <= f) { toast('Controlla le date: la fine deve essere dopo l’inizio.', 'err'); return; }
  S.custom = { from: f, to: t }; S.live = false;
  closeModals(); computeFrames(false); rangeUI(); schedulePreload(); scheduleHash();
};

/* =========================================================================
 * 10. Stato, rete e risoluzione
 * ========================================================================= */
function netUI() {
  const busy = TS.active + TS.q.length;
  const el = $('#stNet'), t = $('#stNetT');
  el.classList.toggle('busy', busy > 0);
  const natBusy = TS.natActive + TS.q.filter(j => j.nat).length;
  if (TS.fails >= 6 && busy === 0) t.textContent = 'Rete non raggiungibile: uso solo la cache';
  else if (natBusy > 0) t.textContent = `${S.accum ? 'Calcolo l’accumulo' : 'Preparo i dati originali'}… (${natBusy} tasselli${S.preloadPct < 1 ? `, ${Math.round((S.preloadPct || 0) * 100)}%` : ''})${S.readyTxt ? ' · ' + S.readyTxt : ''}`;
  else if (busy > 0) t.textContent = S.preloadPct < 1 ? `Precaricamento ${Math.round((S.preloadPct || 0) * 100)}%` : `${busy} tile in arrivo`;
  else t.textContent = 'Pronto';
}
function updateStatus() {
  const vis = S.stack.filter(e => e.vis).map(e => cat(e.id));
  const sats = [...new Set(vis.filter(Lr => !Lr.isRef && !/Marble/.test(Lr.id)).map(Lr => Lr.sat))];
  $('#stSrc').textContent = sats.join(', ') || '--';
  // i punti fuoco (vettoriali, disegnati a ogni zoom) non dicono la risoluzione: preferisci un layer immagine
  const top = vis.find(Lr => Lr.timed && !Lr.isRef && !Lr.vettoriale) || vis.find(Lr => Lr.timed && !Lr.isRef) || vis[0];
  if (top) {
    const lat = map.getCenter().lat, cur = nativeKm(map.getZoom(), lat);
    const nat = top.vettoriale && top.risoluzioneKm ? top.risoluzioneKm : nativeKm(top.maxZoom, lat);
    const f = v => v < 1 ? `${num(v * 1000, 0)} m` : `${num(v, v < 10 ? 1 : 0)} km`;
    $('#stRes').textContent = `nativa ~${f(nat)}/px, vista ~${f(cur)}/px${map.getZoom() > top.maxZoom ? ' (ingrandita)' : ''}`;
  } else $('#stRes').textContent = '--';
}

/* =========================================================================
 * 11. Anomalie (EONET + GDACS)
 * ========================================================================= */
const CATS = {
  tempeste: { nome: 'Cicloni e tempeste', ic: 'storm', c: '#8fb6ff' },
  incendi: { nome: 'Incendi', ic: 'fire', c: '#ff7a45' },
  vulcani: { nome: 'Vulcani', ic: 'volcano', c: '#ea4f86' },
  polveri: { nome: 'Polveri e foschia', ic: 'dust', c: '#dcb36c' },
  alluvioni: { nome: 'Alluvioni', ic: 'flood', c: '#4fc3f7' },
  ghiacci: { nome: 'Ghiacci', ic: 'ice', c: '#bfe6ff' },
  terremoti: { nome: 'Terremoti', ic: 'quake', c: '#c9a0ff' },
  altro: { nome: 'Altro', ic: 'dot', c: '#a3b6c9' },
};
function normCat(c) {
  const s = String(c || '').toLowerCase();
  if (/storm|cyclon|tc|uragan|tifon|cicl|tempest/.test(s)) return 'tempeste';
  if (/fire|wf|incend/.test(s)) return 'incendi';
  if (/volc|vo\b|^vo$|vulcan/.test(s)) return 'vulcani';
  if (/dust|haze|polv|fosch/.test(s)) return 'polveri';
  if (/flood|^fl$|alluv/.test(s)) return 'alluvioni';
  if (/ice|ghiacc|snow/.test(s)) return 'ghiacci';
  if (/quake|^eq$|terremot/.test(s)) return 'terremoti';
  return 'altro';
}
function normSev(g) {
  if (typeof g === 'number') return clamp(Math.round(g), 1, 3);
  const s = String(g || '').toLowerCase();
  if (/red|ross|alta|high|3/.test(s)) return 3;
  if (/orange|aranc|media|medium|2/.test(s)) return 2;
  return 1;
}
const SEVC = { 1: 'var(--sev1)', 2: 'var(--sev2)', 3: 'var(--sev3)' };
const SEVN = { 1: 'bassa', 2: 'media', 3: 'alta' };
function normEvents(raw) {
  const arr = Array.isArray(raw) ? raw : raw?.events || raw?.eventi || [];
  return arr.map(e => ({
    id: String(e.id ?? Math.random()), fonte: e.fonte || '', titolo: e.titolo || e.title || 'Evento senza nome',
    cat: normCat(e.categoria), sev: normSev(e.gravita), lat: +e.lat, lon: +e.lon, data: parseTime(e.data),
    trk: Array.isArray(e.traiettoria) ? e.traiettoria.filter(p => isFinite(p[0]) && isFinite(p[1])) : null, url: e.url,
    paese: e.paese || '', intensita: e.intensita || '', tc: e.ciclone || null,
  })).filter(e => isFinite(e.lat) && isFinite(e.lon))
    .sort((a, b) => b.sev - a.sev || (b.data || 0) - (a.data || 0));
}
async function loadEvents(manual) {
  try {
    const raw = await getJSON(S.mode === 'api' ? '/api/events' : '_mock/events.json', 20000);
    S.events = normEvents(raw);
    renderEvents();
    followTick();
    if (manual) toast(`Anomalie aggiornate: ${S.events.length} eventi aperti.`, 'ok', 2200);
  } catch {
    if (!S.events.length) $('#evList').innerHTML = `<div class="empty">Non riesco a scaricare gli eventi in questo momento. Riprovo da solo fra 10 minuti, oppure premi ${icon('refresh')}.</div>`;
    else toast('Aggiornamento anomalie non riuscito: mostro l’ultimo elenco scaricato.', 'err');
  }
}
const evVisible = () => S.events.filter(e => (!S.evCats.size || S.evCats.has(e.cat)) && e.sev >= S.evSev);
function renderEvents() {
  const counts = {}; S.events.forEach(e => (counts[e.cat] = (counts[e.cat] || 0) + 1));
  $('#evCats').innerHTML = Object.entries(CATS).filter(([k]) => counts[k]).map(([k, c]) =>
    `<button class="chip" style="--c:${c.c}" data-c="${k}" aria-pressed="${S.evCats.has(k)}"><span class="dot"></span>${c.nome}<span class="n">${counts[k]}</span></button>`).join('');
  const hi = S.events.filter(e => e.sev === 3).length, cyc = S.events.filter(e => e.cat === 'tempeste').length;
  $('#evSummary').innerHTML = `<div><b>${S.events.length}</b><span>eventi aperti</span></div><div><b style="color:var(--sev3)">${hi}</b><span>gravità alta</span></div><div><b style="color:var(--c-storm)">${cyc}</b><span>cicloni</span></div>`;
  const vis = evVisible();
  $('#evList').innerHTML = vis.length ? vis.map(e => {
    const c = CATS[e.cat];
    return `<div class="ev-wrap"><button class="ev" style="--c:${c.c}" data-id="${esc(e.id)}" ${S.evSel === e.id ? 'aria-current="true"' : ''}>
      <span class="ico">${icon(c.ic)}<span class="sev" style="background:${SEVC[e.sev]}" title="Gravità ${SEVN[e.sev]}"></span></span>
      <span class="t">${esc(e.titolo)}</span>
      <span class="m"><span>${e.data ? fmtD(e.data) : 'data n.d.'}</span>${e.paese ? `<span>${esc(e.paese)}</span>` : `<span>${fmtLat(e.lat)}, ${fmtLon(e.lon)}</span>`}${e.fonte ? `<span>${esc(e.tc?.fonti?.join(' + ') || e.fonte)}</span>` : ''}${e.trk && e.trk.length > 1 ? '<span class="trk">traiettoria</span>' : ''}</span>
    </button>${e.tc ? tcCard(e) : ''}</div>`;
  }).join('') : `<div class="empty">${S.events.length ? 'Nessun evento con questi filtri.' : 'Nessuna anomalia aperta in questo momento.'}</div>`;
  followUI();
  drawEventMarkers(vis);
}

/* ---------- Cicloni tropicali: colori Saffir-Simpson, scheda, popup, traccia ---------- */
const TCC = { TD: '#5ebaff', TS: '#00faf4', 1: '#ffffcc', 2: '#ffe775', 3: '#ffc140', 4: '#ff8f20', 5: '#ff6060', NT: '#b8c4cf' };
const TCN = { TD: 'Depressione tropicale (< 63 km/h)', TS: 'Tempesta tropicale (63-118 km/h)', 1: 'Categoria 1 (119-153 km/h)', 2: 'Categoria 2 (154-177 km/h)',
  3: 'Categoria 3 (178-208 km/h)', 4: 'Categoria 4 (209-251 km/h)', 5: 'Categoria 5 (≥ 252 km/h)', NT: 'Non tropicale o residuo' };
const tcCol = k => TCC[k] || '#9fb3c8';
const tcBadge = k => (k == null ? '?' : /^\d$/.test(String(k)) ? `Cat. ${k}` : k === 'NT' ? 'NT' : k);
const kmh = kt => (kt == null ? null : Math.round(kt * 1.852));
const tUI = iso => { const d = parseTime(iso); return d ? `${fmtD(d)} ${fmtT(d)} UTC (Italia ${fmtItT(d)})` : 'n.d.'; };
function tcMoveTxt(c) {
  if (c.direzione == null && c.velocitaKmh == null) return 'n.d.';
  return `${c.direzioneNome ? 'verso ' + c.direzioneNome : ''}${c.direzione != null ? ` (${c.direzione}°)` : ''}${c.velocitaKmh != null ? `, ${num(c.velocitaKmh, 0)} km/h` : ''}`;
}
function tcCard(e) {
  const c = e.tc, k = c.categoria;
  const vecchio = c.oreDaUltimoDato != null && c.oreDaUltimoDato > 30;
  return `<div class="tc-card" style="--c:${tcCol(k)}">
    <div class="hd"><span class="tc-badge">${esc(tcBadge(k))}</span><span><b>${esc(c.tipoNome)}</b>${c.categoriaNome ? ', ' + esc(c.categoriaNome) + ' Saffir-Simpson' : ''}<br>${esc(c.bacinoNome || '')}</span></div>
    <div class="tc-grid">
      <div><small>Vento massimo</small><b>${c.ventoKmh != null ? `${c.ventoKmh} km/h (${c.ventoKt} kt)` : c.ventoMaxKmh ? `n.d. (picco ${Math.round(c.ventoMaxKmh)} km/h)` : 'n.d.'}</b></div>
      <div><small>Pressione minima</small><b>${c.pressione ? c.pressione + ' hPa' : 'n.d.'}</b></div>
      <div><small>Spostamento</small><b>${esc(tcMoveTxt(c))}</b></div>
      <div><small>Raffiche</small><b>${c.raffKmh ? c.raffKmh + ' km/h' : 'n.d.'}</b></div>
    </div>
    <div class="tc-note">${vecchio ? `<b style="color:var(--live)">Ultimo dato del ${tUI(c.istante)}: il sistema potrebbe essersi dissolto.</b>` : `Posizione del ${tUI(c.istante)}`}${c.nota ? '. ' + esc(c.nota) : ''}${c.previsione?.length > 1 ? `. Previsione fino al ${fmtD(parseTime(c.previsione[c.previsione.length - 1][2]))}` : ''}</div>
    <div class="acts">
      <button class="btn" data-follow="${esc(e.id)}" aria-pressed="${S.follow === e.id}">${icon('target')}${S.follow === e.id ? 'Smetti di seguire' : 'Segui ciclone'}</button>
      <button class="btn" data-tcpop="${esc(e.id)}">${icon('info')}Dettagli</button>
    </div></div>`;
}
function tcPopupHTML(e) {
  const c = e.tc, k = c.categoria;
  const rag = c.raggi ? ['34', '50', '64'].filter(x => c.raggi[x]).map(x => {
    const r = c.raggi[x]; return `<tr><td>Venti ≥ ${x} kt (${kmh(+x)} km/h)</td><td>NE ${r.ne}, SE ${r.se}, SO ${r.sw}, NO ${r.nw} km</td></tr>`;
  }).join('') : '';
  const prevUlt = c.previsione?.length > 1 ? c.previsione[c.previsione.length - 1] : null;
  return `<div class="tc-pop" style="--c:${tcCol(k)}">
    <h3>${esc(c.titolo)}</h3>
    <div><span class="tc-badge">${esc(tcBadge(k))}</span> <span class="muted small">${esc(TCN[k] || 'categoria non disponibile')}</span></div>
    <table>
      <tr><td>Posizione</td><td>${fmtLat(c.lat)}, ${fmtLon(c.lon)}<br><span class="muted small">${tUI(c.istante)}</span></td></tr>
      <tr><td>Vento sostenuto</td><td>${c.ventoKmh != null ? `<b>${c.ventoKmh} km/h</b> (${c.ventoKt} kt)` : 'n.d.'}${c.notaVento ? `<br><span class="muted small">${esc(c.notaVento)}</span>` : ''}</td></tr>
      <tr><td>Raffiche</td><td>${c.raffKmh ? `${c.raffKmh} km/h (${c.raffiche} kt)` : 'n.d.'}</td></tr>
      <tr><td>Pressione</td><td>${c.pressione ? c.pressione + ' hPa' : 'n.d.'}</td></tr>
      <tr><td>Spostamento</td><td>${esc(tcMoveTxt(c))}</td></tr>
      <tr><td>Bacino</td><td>${esc(c.bacinoNome || '')}</td></tr>
      ${rag}
      ${prevUlt ? `<tr><td>Previsione</td><td>fino al ${tUI(prevUlt[2])}${prevUlt[3] != null ? `, ${kmh(prevUlt[3])} km/h` : ''}</td></tr>` : ''}
      <tr><td>Cono</td><td>${c.cono ? 'incertezza ufficiale ' + esc(c.conoFonte) : 'non pubblicato per questo bacino' + (c.raggi ? ' (mostro i raggi di vento)' : '')}</td></tr>
      <tr><td>Fonti</td><td>${esc((c.fonti || [c.fonte]).join(' + '))}${c.avviso ? `<br><span class="muted small">${esc(c.avviso)}</span>` : ''}</td></tr>
    </table>
    <div class="acts" style="display:flex;gap:6px;flex-wrap:wrap">
      <button class="btn" data-follow="${esc(e.id)}">${icon('target')}${S.follow === e.id ? 'Smetti di seguire' : 'Segui ciclone'}</button>
      ${c.url ? `<a class="btn" href="${esc(c.url)}" target="_blank" rel="noopener">${icon('link')}Bollettino</a>` : ''}
    </div></div>`;
}
/* poligono dei raggi di vento per quadrante (km) attorno al centro */
function quadPoly(lat, lon, r) {
  const R = 6371, rad = Math.PI / 180, pts = [];
  const qs = [['ne', 0, 90], ['se', 90, 180], ['sw', 180, 270], ['nw', 270, 360]];
  for (const [q, a0, a1] of qs) {
    const d = (r[q] || 0) / R;
    for (let b = a0; b <= a1; b += 10) {
      const br = b * rad, la1 = lat * rad;
      const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(br));
      const lo2 = lon * rad + Math.atan2(Math.sin(br) * Math.sin(d) * Math.cos(la1), Math.cos(d) - Math.sin(la1) * Math.sin(la2));
      pts.push([la2 / rad, lo2 / rad]);
    }
  }
  return pts;
}
const TC_SVG = '<svg viewBox="0 0 40 40" aria-hidden="true"><path d="M20 4c-6 0-11 3-13 8 3-2 6-3 9-2-5 2-8 6-8 11 0 6 5 11 12 11 6 0 11-3 13-8-3 2-6 3-9 2 5-2 8-6 8-11 0-6-5-11-12-11z" fill="var(--c)" stroke="#0b1825" stroke-width="1.5"/><circle cx="20" cy="20" r="5.2" fill="#0b1825"/></svg>';
function drawCyclone(e) {
  const c = e.tc;
  const raw = [...(c.passato || []).map(p => ({ p, f: false })), ...(c.previsione || []).map(p => ({ p, f: true }))];
  const all = unwrapTrack(raw.map(r => [r.p[0], r.p[1], r]))
    .map(q => ({ lat: q[0], lon: q[1], t: q[2].p[2], kt: q[2].p[3], k: q[2].p[4], f: q[2].f }));
  // centro attuale riallineato alla stessa "copia" del mondo della traccia
  let cl = c.lon;
  if (all.length) { const ref = all[Math.max(0, (c.passato || []).length - 1)].lon; while (cl - ref > 180) cl -= 360; while (cl - ref < -180) cl += 360; }
  if (c.cono && c.cono.length > 2) {
    const cono = unwrapTrack(c.cono.map(p => [p[0], p[1], null])).map(p => { let lo = p[1]; while (lo - cl > 180) lo -= 360; while (lo - cl < -180) lo += 360; return [p[0], lo]; });
    L.polygon(cono, { color: '#e4edf6', weight: 1, opacity: 0.55, fillColor: '#e4edf6', fillOpacity: 0.1, dashArray: '3 4', interactive: false }).addTo(trkLayer);
  }
  if (c.raggi) {
    for (const [kt, col] of [['34', '#ffe14a'], ['50', '#ff9f1c'], ['64', '#ff3b3b']]) {
      const r = c.raggi[kt]; if (!r || !(r.ne || r.se || r.sw || r.nw)) continue;
      L.polygon(quadPoly(c.lat, cl, r), { color: col, weight: 1.2, opacity: 0.85, fillColor: col, fillOpacity: 0.08, interactive: false }).addTo(trkLayer);
    }
  }
  // segmenti colorati per categoria (passato pieno, previsione tratteggiata)
  for (let i = 1; i < all.length; i++) {
    const a = all[i - 1], b = all[i];
    L.polyline([[a.lat, a.lon], [b.lat, b.lon]], { color: tcCol(a.k), weight: b.f ? 2.5 : 3.5, opacity: 0.95, dashArray: b.f ? '7 6' : null, interactive: false }).addTo(trkLayer);
  }
  // collegamento fra ultimo punto osservato e posizione attuale (se diversi)
  const nPass = (c.passato || []).length;
  const every = Math.max(1, Math.ceil(nPass / 16));
  all.forEach((p, i) => {
    const isPrev = p.f;
    if (!isPrev && i % every && i !== nPass - 1) return;
    const d = parseTime(p.t);
    const m = L.marker([p.lat, p.lon], {
      icon: L.divIcon({ className: '', html: `<div class="tc-pt ${isPrev ? 'f' : ''}" style="--c:${tcCol(p.k)}"></div>`, iconSize: [9, 9], iconAnchor: [5, 5] }), keyboard: false,
    });
    m.bindTooltip(`<b>${esc(c.nome)}</b> ${isPrev ? '<i>previsione</i>' : ''}<br>${d ? `${fmtD(d)} ${fmtT(d)} UTC<br>Italia ${fmtIt(d)}` : ''}<br>${p.kt != null ? `${kmh(p.kt)} km/h (${p.kt} kt), ` : ''}${esc(tcBadge(p.k))}`, { className: 'trk-tip', direction: 'top', offset: [0, -5] });
    m.addTo(trkLayer);
    // etichette orarie sui punti di previsione (gg-mm hh UTC)
    if (isPrev && d) L.marker([p.lat, p.lon], { icon: L.divIcon({ className: '', html: `<div class="tc-lbl">${pad(d.getUTCDate())}-${pad(d.getUTCMonth() + 1)} ${pad(d.getUTCHours())}Z</div>`, iconSize: [70, 12], iconAnchor: [-7, 6] }), interactive: false, keyboard: false }).addTo(trkLayer);
  });
  const sym = L.marker([c.lat, cl], {
    icon: L.divIcon({ className: '', html: `<div class="tc-sym ${c.emisfero === 'S' ? 'sh' : 'nh'}" style="--c:${tcCol(c.categoria)}">${TC_SVG}<b>${/^\d$/.test(String(c.categoria)) ? c.categoria : ''}</b></div>`, iconSize: [34, 34], iconAnchor: [17, 17] }),
    title: c.titolo, riseOnHover: true, zIndexOffset: 800,
  });
  sym.bindPopup(() => tcPopupHTML(e), { maxWidth: 340, className: 'tc-popup' });
  sym.bindTooltip(`<b>${esc(c.titolo)}</b><br>${c.ventoKmh != null ? c.ventoKmh + ' km/h, ' : ''}${esc(tcBadge(c.categoria))}`, { className: 'trk-tip', direction: 'top', offset: [0, -16] });
  sym.addTo(evLayer);
  e._sym = sym;
}
/* legenda delle categorie sulla mappa (solo quando ci sono cicloni) */
/* legenda orizzontale sopra la barra dell'animazione (fuori dai pannelli laterali) */
const TCL = { TD: 'TD < 63', TS: 'TS 63-118', 1: 'Cat. 1 119-153', 2: 'Cat. 2 154-177', 3: 'Cat. 3 178-208', 4: 'Cat. 4 209-251', 5: 'Cat. 5 ≥ 252', NT: 'non tropicale' };
const tcLegend = (() => {
  const d = document.createElement('div'); d.className = 'tc-legend'; d.hidden = true;
  d.setAttribute('role', 'note'); d.setAttribute('aria-label', 'Legenda cicloni');
  d.innerHTML = '<h4>Cicloni, vento km/h</h4>' + ['TD', 'TS', 1, 2, 3, 4, 5, 'NT'].map(k => `<div style="--c:${TCC[k]}" title="${TCN[k]}"><i></i>${TCL[k]}</div>`).join('')
    + '<div class="ln"><i></i>previsione</div><div class="cn"><i></i>cono di incertezza</div>'
    + '<div><i style="background:none;border:1.5px solid #ffe14a;height:9px"></i>raggi vento 34/50/64 kt</div>'
    + `<button class="ibtn x" title="Nascondi legenda">${icon('x')}</button>`;
  let chiusa = false; // chiusa solo fino al prossimo caricamento della pagina
  d.querySelector('button').onclick = () => { chiusa = true; d.hidden = true; };
  document.body.appendChild(d);
  return { addTo() { d.hidden = chiusa; }, remove() { d.hidden = true; } };
})();
let tcLegendOn = false;
/* seguire un ciclone: centra la mappa e anima il layer migliore; ad ogni aggiornamento (10 min) si ricentra */
function followUI() {
  const box = $('#evFollow'); if (!box) return;
  const e = S.follow && S.events.find(x => x.id === S.follow);
  if (!e) { box.hidden = true; box.innerHTML = ''; return; }
  box.hidden = false;
  box.innerHTML = `<span class="led"></span><span>Segui <b>${esc(e.tc?.titolo || e.titolo)}</b>: aggiorno ogni 10 minuti</span><button class="ibtn" data-follow="${esc(e.id)}" title="Smetti di seguire">${icon('x')}</button>`;
}
function setFollow(id) {
  if (S.follow === id) { S.follow = null; toast('Non seguo più il ciclone.', 'info', 2000); renderEvents(); return; }
  const e = S.events.find(x => x.id === id); if (!e) return;
  S.follow = id; map.closePopup();
  focusEvent(e, true);
  renderEvents();
}
function followTick() {
  if (!S.follow) return;
  const e = S.events.find(x => x.id === S.follow);
  if (!e) { toast('Il ciclone seguito non è più nell’elenco degli eventi aperti.', 'info'); S.follow = null; followUI(); return; }
  const c = map.getCenter(), d = Math.abs(c.lat - e.lat) + Math.abs(((c.lng - e.lon + 540) % 360) - 180);
  if (d > 0.05) map.panTo([e.lat, e.lon], { animate: !document.hidden });
}
document.addEventListener('click', ev => {
  const f = ev.target.closest('[data-follow]'); if (f) { ev.stopPropagation(); setFollow(f.dataset.follow); return; }
  const p = ev.target.closest('[data-tcpop]');
  if (p) { const e = S.events.find(x => x.id === p.dataset.tcpop); if (e && e._sym) { map.flyTo([e.lat, e.lon], Math.max(map.getZoom(), 5), { duration: 1 }); map.once('moveend', () => e._sym.openPopup()); } }
});
function unwrapTrack(pts) {
  let prev = null;
  return pts.map(p => { let lon = +p[1]; if (prev !== null) { while (lon - prev > 180) lon -= 360; while (lon - prev < -180) lon += 360; } prev = lon; return [+p[0], lon, p[2]]; });
}
function drawEventMarkers(vis) {
  evLayer.clearLayers(); trkLayer.clearLayers();
  let nTc = 0;
  for (const e of vis) {
    const c = CATS[e.cat];
    if (e.tc) {
      try { drawCyclone(e); nTc++; continue; } catch (err) { console.warn('ciclone non disegnato', e.id, err); }
    }
    if (e.trk && e.trk.length > 1) {
      const pts = unwrapTrack(e.trk);
      L.polyline(pts.map(p => [p[0], p[1]]), { color: c.c, weight: 2, opacity: 0.85, dashArray: '5 5', interactive: false }).addTo(trkLayer);
      // al massimo ~14 punti con orario: la traccia resta leggibile anche per cicloni lunghi
      const every = Math.max(1, Math.ceil(pts.length / 14));
      pts.forEach((p, i) => {
        if (i % every && i !== pts.length - 1) return;
        const t = parseTime(p[2]);
        L.marker([p[0], p[1]], { icon: L.divIcon({ className: '', html: '<div class="trk-pt"></div>', iconSize: [8, 8], iconAnchor: [4, 4] }), keyboard: false })
          .bindTooltip(`${esc(e.titolo)}<br>${t ? `${fmtD(t)} ${fmtT(t)} UTC` : ''}`, { className: 'trk-tip', direction: 'top', offset: [0, -4] }).addTo(trkLayer);
      });
    }
    const m = L.marker([e.lat, e.lon], {
      icon: L.divIcon({ className: '', html: `<div class="evm s${e.sev}" style="--c:${c.c}">${icon(c.ic)}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] }),
      title: e.titolo, riseOnHover: true,
    });
    m.bindTooltip(`<b>${esc(e.titolo)}</b><br>${c.nome}, gravità ${SEVN[e.sev]}`, { className: 'trk-tip', direction: 'top', offset: [0, -14] });
    m.on('click', () => focusEvent(e));
    m.addTo(evLayer);
  }
  tcLegendSync();
}
/* la legenda dei cicloni compare solo quando almeno un ciclone e' nella vista */
function tcLegendSync() {
  const b = map.getBounds().pad(0.15);
  const vis = S.events.some(e => e._sym && evLayer.hasLayer(e._sym) && b.contains(e._sym.getLatLng()));
  if (vis && !tcLegendOn) { tcLegend.addTo(map); tcLegendOn = true; }
  if (!vis && tcLegendOn) { tcLegend.remove(); tcLegendOn = false; }
}
map.on('moveend', () => tcLegendSync());
$('#evCats').addEventListener('click', ev => {
  const b = ev.target.closest('.chip'); if (!b) return;
  const k = b.dataset.c; S.evCats.has(k) ? S.evCats.delete(k) : S.evCats.add(k); renderEvents();
});
$('#evSev').addEventListener('click', ev => {
  const b = ev.target.closest('button'); if (!b) return;
  S.evSev = +b.dataset.v; $$('#evSev button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); renderEvents();
});
$('#evList').addEventListener('click', ev => {
  const b = ev.target.closest('.ev'); if (!b) return;
  const e = S.events.find(x => x.id === b.dataset.id); if (e) focusEvent(e);
});
$('#evRefresh').onclick = () => loadEvents(true);

/* scelta automatica del layer migliore per evento e posizione */
function geoFamily(lon) {
  lon = ((lon + 540) % 360) - 180;
  if (lon <= -105 || lon >= 170) return 'GOES-West';
  if (lon < -25) return 'GOES-East';
  if (lon >= 75) return 'Himawari';
  return 'Meteosat';
}
function geoLayer(fam, kind) {
  const famRe = { 'GOES-West': /^GOES-West/i, 'GOES-East': /^GOES-East/i, Himawari: /^Himawari/i, Meteosat: /meteosat|msg|mtg|seviri|fci/i }[fam];
  const kinds = {
    color: [/GeoColou?r/i, /natural|true.?colou?r|rgb/i, /Visible|VIS06/i],
    ir: [/Band13|Clean_Infrared/i, /IR.?10|10[._]?[58]|infra/i],
    dust: [/Dust/i], fire: [/FireTemp|Fire/i], airmass: [/Air_?Mass/i],
  }[kind];
  for (const k of kinds) { const Lr = findLayer(famRe, k); if (Lr && Lr.timed) return Lr; }
  return null;
}
function polarLayer(kind) {
  const opts = {
    color: [[/VIIRS_NOAA20_CorrectedReflectance_TrueColor/], [/VIIRS_SNPP_CorrectedReflectance_TrueColor/], [/MODIS_Terra_CorrectedReflectance_TrueColor/], [/CorrectedReflectance_TrueColor/]],
    fire: [[/VIIRS_NOAA20_Thermal_Anomalies/], [/VIIRS_SNPP_Thermal_Anomalies/], [/Thermal_Anomalies/]],
    aod: [[/AOD/i]], so2: [[/SO2/i]], rain: [[/IMERG/i, /Rate/i], [/IMERG/i]],
  }[kind] || [];
  for (const res of opts) { const Lr = findLayer(...res); if (Lr) return Lr; }
  return null;
}
function focusEvent(e, seguendo) {
  if (S.follow && S.follow !== e.id && !seguendo) S.follow = null; // un altro evento scelto: smetto di seguire
  S.evSel = e.id; renderEvents();
  const fam = geoFamily(e.lon);
  const plan = { layers: [], range: 360, zoom: 6 };
  const g = k => geoLayer(fam, k), P = k => polarLayer(k);
  const push = (Lr, o = {}) => Lr && plan.layers.push({ id: Lr.id, ...o });
  switch (e.cat) {
    case 'tempeste': {
      const ir = g('ir');
      if (ir) push(ir, { pal: { id: 'ir' } }); else push(g('color') || P('color'));
      plan.range = 720; plan.zoom = 5; break;
    }
    case 'incendi': push(g('color') || P('color')); push(g('fire') || P('fire'), { op: 0.85 }); plan.zoom = 7; break;
    case 'vulcani': push(g('color') || P('color')); if (!g('color')) push(P('so2'), { op: 0.7 }); plan.zoom = 7; break;
    case 'polveri': { const d = g('dust'); if (d) push(d); else { push(P('color')); push(P('aod'), { op: 0.7 }); } plan.range = 720; plan.zoom = 5; break; }
    case 'alluvioni': push(g('color') || P('color')); push(P('rain'), { op: 0.75 }); plan.range = 1440; plan.zoom = 6; break;
    default: push(g('color') || P('color')); plan.zoom = 6;
  }
  if (!plan.layers.length) { map.flyTo([e.lat, e.lon], plan.zoom); toast('Nessun layer adatto per questa zona: ho solo centrato la mappa.', 'info'); return; }
  pause();
  // tieni sfondo e riferimenti, sostituisci le immagini
  for (const x of [...S.stack]) { const Lr = cat(x.id); if (!Lr.isRef && Lr.timed) removeLayer(x.id, true); }
  if (!CO.on) { CO.on = true; coastApply(true); }
  // il primo layer del piano e' la base, i successivi gli stanno sopra (sotto i riferimenti)
  const at = S.stack.filter(x => cat(x.id).isRef).length;
  plan.layers.forEach(o => {
    const Lr = cat(o.id); addLayer(o.id, { op: o.op, at, pal: o.pal ? Object.assign(defPal(Lr), o.pal) : undefined }, true);
  });
  S.sel = plan.layers[0].id;
  const anyDaily = plan.layers.every(o => cat(o.id).tempoTipo === 'date');
  S.range = anyDaily ? 4320 : plan.range; S.custom = null; S.live = true;
  rangeUI();
  afterStackChange(false);
  map.once('moveend', () => { preload(); setTimeout(play, 400); });
  if (document.hidden) map.setView([e.lat, e.lon], plan.zoom, { animate: false });
  else map.flyTo([e.lat, e.lon], plan.zoom, { duration: 1.4 });
  const nomi = plan.layers.map(o => cat(o.id).nome).join(' + ');
  toast(`${esc(e.titolo)}: attivato ${esc(nomi)}, animazione ultime ${anyDaily ? '3 giornate' : (S.range / 60) + ' ore'}.${seguendo ? ' Lo seguo: ogni 10 minuti ricentro la mappa e aggiorno l’animazione.' : ''}`, 'ok', 5200);
}

/* =========================================================================
 * 11b. "Alta risoluzione qui": il layer piu' nitido davvero disponibile per un punto
 * ========================================================================= */
function sunZenith(d, lat, lon) {
  const rad = Math.PI / 180;
  const doy = (Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5;
  const hr = d.getUTCHours() + d.getUTCMinutes() / 60;
  const g = 2 * Math.PI / 365 * (doy - 1 + (hr - 12) / 24);
  const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const dec = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const ha = ((hr * 60 + eqt + 4 * lon) / 4 - 180) * rad;
  const cz = Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(ha);
  return Math.acos(clamp(cz, -1, 1)) / rad;
}
/* angolo al centro della Terra fra il punto e il punto sub-satellite (gradi): < ~68 = vista buona */
function geoAngle(lat, lon, sublon) {
  const rad = Math.PI / 180;
  return Math.acos(clamp(Math.cos(lat * rad) * Math.cos((lon - sublon) * rad), -1, 1)) / rad;
}
const NAT_SATS = [
  { sat: 'goes19', lon: -75.2, nome: 'GOES-East' },
  { sat: 'goes18', lon: -137.0, nome: 'GOES-West' },
  { sat: 'himawari9', lon: 140.7, nome: 'Himawari-9' },
];
const fkm = v => v < 1 ? `${num(v * 1000, 0)} m` : `${num(v, v < 10 ? 1 : 0)} km`;
/* Sentinel-2 HLS: giorno piu' recente (fino a 10 giorni fa) con dati veri nel punto, provando il tassello reale */
async function probeHLS(lat, lon, z) {
  const Lr = cat('HLS_S30_Nadir_BRDF_Adjusted_Reflectance');
  if (!Lr || S.mode !== 'api') return null;
  z = Math.min(z, Lr.maxZoom);
  const p = map.project([lat, lon], z), tx = Math.floor(p.x / 256), ty = Math.floor(p.y / 256);
  const px = Math.floor(p.x - tx * 256), py = Math.floor(p.y - ty * 256);
  const today = Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate());
  const days = Array.from({ length: 11 }, (_, i) => new Date(today - i * 864e5).toISOString().slice(0, 10))
    .filter(d => (!Lr.max || +parseTime(d) <= +Lr.max) && (!Lr.min || +parseTime(d) >= +Lr.min));
  const cv = document.createElement('canvas'); cv.width = cv.height = 256;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  const prova = async d => {
    const url = tileURL(Lr, d, z, tx, ty); if (!url) return null;
    const bmp = await getBitmap(url, true); if (!bmp) return null;
    ctx.clearRect(0, 0, 256, 256); ctx.drawImage(bmp, 0, 0);
    const a = ctx.getImageData(0, 0, 256, 256).data;
    let pieni = 0; for (let i = 3; i < a.length; i += 16) if (a[i] > 0) pieni++;
    // il punto (con un intorno di 4 px) deve essere coperto, non solo il tassello
    let qui = false;
    for (let dy = -4; dy <= 4 && !qui; dy += 2) for (let dx = -4; dx <= 4; dx += 2) {
      const x = clamp(px + dx, 0, 255), y = clamp(py + dy, 0, 255);
      if (a[(y * 256 + x) * 4 + 3] > 0) { qui = true; break; }
    }
    return qui ? { d, frac: pieni / (a.length / 16) } : null;
  };
  let ok = null;
  for (let i = 0; i < days.length && !ok; i += 3) {       // di norma basta uno dei primi giorni: meno richieste
    const ris = await Promise.all(days.slice(i, i + 3).map(d => prova(d).catch(() => null)));
    ok = ris.find(Boolean) || null;
  }
  return ok ? { Lr, ...ok } : null;
}
async function altaRisoluzioneQui(ll) {
  ll = ll || map.getCenter();
  const lat = ll.lat, lon = ((ll.lng + 540) % 360) - 180;
  const refs = () => S.stack.filter(x => cat(x.id).isRef).length;
  // 1) GOES/Himawari originali se il punto e' ben visto da un geostazionario NOAA/JMA
  const vista = NAT_SATS.map(s => ({ ...s, g: geoAngle(lat, lon, s.lon) })).filter(s => s.g < 68).sort((a, b) => a.g - b.g);
  const plan = []; // dall'alto verso il basso
  let main = null, nota = '', zoom = 9;
  for (const s of vista) {
    const tc = cat(`NAT_${s.sat}_truecolor`), gc = cat(`NAT_${s.sat}_geocolor`);
    if (!tc && !gc) continue;
    const t = (tc || gc).max || new Date();
    const giorno = sunZenith(t, lat, lon) < 84;
    main = giorno && tc ? tc : (gc || tc);
    nota = giorno ? '' : ' È notte qui: GeoColor originale (nubi dall’infrarosso, senza luci delle città).';
    plan.push({ Lr: main });
    break;
  }
  // 2) Europa e Africa: Meteosat MTG (WMS) + colori reali VIIRS/MODIS del giorno
  const eurAfr = lon >= -30 && lon <= 60 && lat >= -40 && lat <= 75;
  const polare = ['VIIRS_NOAA20_CorrectedReflectance_TrueColor', 'VIIRS_NOAA21_CorrectedReflectance_TrueColor',
    'VIIRS_SNPP_CorrectedReflectance_TrueColor', 'MODIS_Terra_CorrectedReflectance_TrueColor'].map(cat).find(Boolean);
  if (!main) {
    const mtg = eurAfr ? (cat('MTG_GeoColour') || cat('MTG_TrueColour')) : null;
    if (polare) plan.push({ Lr: polare });
    if (mtg) plan.push({ Lr: mtg });
    main = plan[0] && plan[0].Lr;
    zoom = 9;
  }
  if (!main) { toast('Nessun layer ad alta risoluzione disponibile per questo punto.', 'err'); return; }
  pause();
  for (const x of [...S.stack]) { const Lr = cat(x.id); if (!Lr.isRef && Lr.timed) removeLayer(x.id, true); }
  if (!CO.on) { CO.on = true; coastApply(true); }
  [...plan].reverse().forEach(o => addLayer(o.Lr.id, { at: refs() }, true));
  S.sel = main.id;
  // le animazioni dei dati originali sono pesanti: intervallo corto (1 ora)
  if (main.nativo) S.range = 60;
  S.custom = null;
  S.live = true; rangeUI();
  afterStackChange(false);
  if (document.hidden) map.setView([lat, lon], zoom, { animate: false });
  else map.flyTo([lat, lon], zoom, { duration: 1.2 });
  let msg;
  if (main.nativo) {
    msg = `Alta risoluzione qui: <b>${esc(main.nome)}</b>, risoluzione vera ${fkm(main.risoluzioneKm)} (zoom ${zoom}), ritardo 10-15 min.${nota} ` +
      'Il primo caricamento di una zona nuova richiede 10-60 secondi.';
  } else {
    const nomi = plan.map(o => `${esc(o.Lr.nome)} (${fkm(o.Lr.risoluzioneKm || nativeKm(o.Lr.maxZoom, lat))})`).join(' sopra ');
    msg = `Alta risoluzione qui: ${nomi}. ${eurAfr ? 'GOES e Himawari non vedono questa zona: l’immagine polare è del giorno, sotto c’è Meteosat MTG in diretta (spegni la prima con l’occhio per vederlo).' : 'Nessun geostazionario NOAA/JMA vede bene questa zona: mosaico polare del giorno.'}`;
  }
  toast(msg + ' Cerco anche Sentinel-2…', 'ok', 8000);
  // 3) sempre: Sentinel-2 HLS 30 m sopra, se c'e' un passaggio negli ultimi 10 giorni
  try {
    const h = await probeHLS(lat, lon, zoom);
    if (!h) { toast('Sentinel-2 30 m: nessun passaggio con dati in questo punto negli ultimi 10 giorni.', 'info', 5000); return; }
    removeLayer(h.Lr.id, true);
    addLayer(h.Lr.id, { at: refs(), fixT: h.d }, true);
    afterStackChange(true);
    const oggi = new Date(), gg = Math.round((Date.UTC(oggi.getUTCFullYear(), oggi.getUTCMonth(), oggi.getUTCDate()) - +parseTime(h.d)) / 864e5);
    toast(`Aggiunto sopra: <b>Sentinel-2 30 m</b> del ${fmtD(parseTime(h.d))} (${gg === 0 ? 'oggi' : gg === 1 ? 'ieri' : gg + ' giorni fa'}), copre circa il ${Math.round(h.frac * 100)}% del tassello centrale. È un’istantanea fissa: spegnila con l’occhio per vedere il satellite in diretta.`, 'ok', 8000);
  } catch { /* Sentinel-2 e' facoltativo */ }
}
$('#btnHiRes')?.addEventListener('click', () => altaRisoluzioneQui());

/* --- dettaglio automatico: oltre lo zoom nativo passa da solo a VIS06 (0,5 km) o Sentinel-2 (30 m) --- */
const AUTODET = { on: true, key: null, busy: false, dismissed: new Set() };
try { AUTODET.on = localStorage.getItem('autoDet') !== '0'; } catch { /* storage non disponibile */ }
function autoDetUI() { const b = $('#btnAutoDet'); if (b) b.setAttribute('aria-pressed', AUTODET.on); }
function autoDetClear(keepId) {
  let ch = false;
  for (const e of [...S.stack]) if (e.auto && e.id !== keepId) { removeLayer(e.id, true); ch = true; }
  return ch;
}
async function autoDetCheck() {
  if (!AUTODET.on || !booted || AUTODET.busy || gifBusy || MP4.busy || S.cmp.on) return;
  const z = Math.round(map.getZoom());
  const base = S.stack.filter(e => e.vis && !e.auto && !cat(e.id).isRef && !cat(e.id).vettoriale);
  const zNat = Math.max(0, ...base.map(e => cat(e.id).maxZoom || 0));
  if (!base.length || z <= zNat) {                       // niente ingrandimento: via i layer automatici
    AUTODET.dismissed.clear(); AUTODET.key = null;
    if (autoDetClear()) afterStackChange(true);
    return;
  }
  const c = map.getCenter(), lat = c.lat, lon = ((c.lng + 540) % 360) - 180;
  const refs = () => S.stack.filter(x => cat(x.id).isRef).length;
  const geo = base.some(e => /^(MTG|MSG)_/.test(e.id) || cat(e.id).categoria === 'geostazionario');
  const vis06 = cat('MTG_VIS06'), hls = cat('HLS_S30_Nadir_BRDF_Adjusted_Reflectance');
  const eurAfr = lon >= -30 && lon <= 60 && lat >= -40 && lat <= 75;
  AUTODET.busy = true;
  try {
    // 1) un solo livello di zoom in piu': VIS06 (di giorno, Europa-Africa), serve solo se parte da MTG/MSG
    if (vis06 && geo && eurAfr && z <= vis06.maxZoom && zNat < vis06.maxZoom && sunZenith(vis06.max || new Date(), lat, lon) < 84) {
      if (AUTODET.dismissed.has(vis06.id)) return;
      if (!S.stack.some(e => e.id === vis06.id)) {
        autoDetClear();
        addLayer(vis06.id, { at: refs() }, true);
        S.stack.find(e => e.id === vis06.id).auto = true;
        afterStackChange(true);
        toast(`Zoom oltre il dettaglio del satellite: aggiunto <b>${esc(vis06.nome)}</b> (${fkm(vis06.risoluzioneKm || 0.5)}, scala di grigi, solo di giorno). Si toglie da solo quando riduci lo zoom.`, 'info', 6500);
      }
      return;
    }
    // 2) zoom alto: Sentinel-2 HLS 30 m del giorno piu' recente con dati nel punto
    if (hls && z >= 8 && S.mode === 'api') {
      if (AUTODET.dismissed.has(hls.id)) return;
      if (S.stack.some(e => e.id === hls.id && !e.auto)) return;      // gia' messo a mano
      const tp = map.project(c, 8), key = `${Math.floor(tp.x / 256)}_${Math.floor(tp.y / 256)}`;
      if (key === AUTODET.key && S.stack.some(e => e.auto && e.id === hls.id)) return;
      AUTODET.key = key;
      const h = await probeHLS(lat, lon, z);
      if (Math.round(map.getZoom()) < 8) return;                       // nel frattempo ha cambiato zoom
      const cur = S.stack.find(e => e.id === hls.id && e.auto);
      if (!h) {
        if (cur) { autoDetClear(); afterStackChange(true); }
        else toast('Sentinel-2 30 m: nessun passaggio con dati qui negli ultimi 10 giorni, resta l’immagine ingrandita.', 'info', 4500);
        return;
      }
      if (cur && cur.fixT === h.d) return;
      autoDetClear();
      addLayer(hls.id, { at: refs(), fixT: h.d }, true);
      S.stack.find(e => e.id === hls.id).auto = true;
      afterStackChange(true);
      const oggi = new Date(), gg = Math.round((Date.UTC(oggi.getUTCFullYear(), oggi.getUTCMonth(), oggi.getUTCDate()) - +parseTime(h.d)) / 864e5);
      toast(`Zoom oltre il dettaglio del satellite: aggiunto <b>Sentinel-2 30 m</b> del ${fmtD(parseTime(h.d))} (${gg === 0 ? 'oggi' : gg === 1 ? 'ieri' : gg + ' giorni fa'}). Istantanea fissa a strisce, sotto resta il satellite live. Si toglie da solo quando riduci lo zoom.`, 'ok', 7000);
    }
  } finally { AUTODET.busy = false; }
}
/* etichetta sulla mappa: dice cosa e' stato aggiunto da solo e permette di toglierlo */
function autoDetChip() {
  let el = document.getElementById('autoDetChip');
  if (!el) {
    el = document.createElement('div'); el.id = 'autoDetChip'; el.className = 'auto-chip'; el.hidden = true;
    el.innerHTML = '<span class="txt"></span><button type="button" aria-label="Togli il livello aggiunto in automatico" title="Togli">✕</button>';
    el.querySelector('button').onclick = () => {
      for (const e of [...S.stack]) if (e.auto) { AUTODET.dismissed.add(e.id); removeLayer(e.id, true); }
      afterStackChange(true);
    };
    document.body.appendChild(el);
  }
  const a = S.stack.find(e => e.auto);
  el.hidden = !a;
  if (a) {
    const Lr = cat(a.id);
    el.querySelector('.txt').textContent = 'Auto dettaglio: ' + Lr.nome + (a.fixT ? ' del ' + fmtD(parseTime(a.fixT)) : '');
  }
}
const autoDetSoon = debounce(() => { autoDetCheck().catch(() => {}); }, 700);
map.on('zoomend moveend', autoDetSoon);
$('#btnAutoDet')?.addEventListener('click', () => {
  AUTODET.on = !AUTODET.on; autoDetUI();
  try { localStorage.setItem('autoDet', AUTODET.on ? '1' : '0'); } catch { /* ok */ }
  if (AUTODET.on) { AUTODET.dismissed.clear(); AUTODET.key = null; autoDetCheck().catch(() => {}); }
  else if (autoDetClear()) afterStackChange(true);
  toast(AUTODET.on ? 'Dettaglio automatico attivo: oltre lo zoom del satellite passa a VIS06 o Sentinel-2.' : 'Dettaglio automatico spento.', 'info', 3500);
});
autoDetUI();
document.addEventListener('click', ev => {
  const b = ev.target.closest('[data-hires]'); if (!b) return;
  const [la, lo] = b.dataset.hires.split(',').map(Number);
  map.closePopup(); altaRisoluzioneQui(L.latLng(la, lo));
});

/* =========================================================================
 * 12. Palette: pannello ed editor
 * ========================================================================= */
const selEntry = () => S.stack.find(e => e.id === S.sel);
function renderPalettePanel() {
  const e = selEntry(), box = $('#palTarget');
  if (!e) {
    box.innerHTML = `<span class="muted small">Aggiungi un layer alla mappa per colorarlo.</span>`;
    $('#palGrid').innerHTML = ''; $('#palAdjust').hidden = true; return;
  }
  const Lr = cat(e.id), dec = decoderFor(Lr);
  box.innerHTML = `${icon('palette')}<div style="flex:1;min-width:0"><label class="sr" for="palSel">Layer da colorare</label>
    <select class="sel" id="palSel" style="width:100%">${S.stack.map(x => `<option value="${esc(x.id)}" ${x.id === e.id ? 'selected' : ''}>${esc(cat(x.id).nome)}</option>`).join('')}</select>
    <div class="muted small" style="margin-top:4px">${dec ? 'Valori decodificati in ' + esc(dec.units || 'unità fisiche') + ': la sonda mostra la temperatura reale.' : 'Palette applicata alla luminosità dell’immagine.'}</div>
    ${typeof Lr.legenda === 'string' && /^https?:/.test(Lr.legenda) && e.pal.id === 'orig' ? `<div class="muted small" style="margin-top:8px">Legenda originale</div><img class="legend-img" src="${esc(Lr.legenda)}" alt="Legenda di ${esc(Lr.nome)}" onerror="this.previousElementSibling.remove();this.remove()">` : ''}</div>`;
  $('#palSel').onchange = ev => { S.sel = ev.target.value; renderStack(); renderPalettePanel(); };
  $('#palGrid').innerHTML = allPals().map(p => `<button class="pal-card" data-p="${esc(p.id)}" aria-pressed="${e.pal.id === p.id}">
      <span class="bar" style="background:${gradCSS(p.stops)}"></span>${esc(p.nome)}</button>`).join('');
  const on = e.pal.id !== 'orig';
  // fusione con i layer sotto
  $('#blendSel').innerHTML = BLENDS.map(([v, n]) => `<option value="${v}" ${(e.blend || '') === v ? 'selected' : ''}>${n}</option>`).join('');
  $('#keyOn').checked = !!e.pal.key; $('#keyT').disabled = $('#keyW').disabled = !e.pal.key;
  $('#keyTL').textContent = dec ? 'Trasparente se più caldo di' : 'Trasparente se più scuro di';
  const kr = dec ? [-90, 50, 1, 2, 80] : [0, 255, 1, 2, 255];
  Object.assign($('#keyT'), { min: kr[0], max: kr[1] }); Object.assign($('#keyW'), { min: kr[3], max: kr[4] });
  $('#keyT').value = e.pal.kT; $('#keyW').value = e.pal.kW;
  $('#keyTo').textContent = e.pal.kT + (dec ? ' °C' : ''); $('#keyWo').textContent = e.pal.kW + (dec ? ' °C' : '');
  $('#palAdjust').hidden = false;
  $$('#palAdjust input').forEach(i => (i.disabled = !on));
  $('#palReset').disabled = !on;
  const r = dec ? [-95, 60] : [0, 255];
  for (const k of ['adjMin', 'adjMax']) { $('#' + k).min = r[0]; $('#' + k).max = r[1]; }
  $('#adjMinL').textContent = dec ? 'Soglia fredda' : 'Livello minimo';
  $('#adjMaxL').textContent = dec ? 'Soglia calda' : 'Livello massimo';
  $('#adjCut').parentElement.lastChild.textContent = dec ? ' Rendi trasparente ciò che è più caldo della soglia' : ' Rendi trasparente sotto il livello minimo';
  setAdj(e.pal, dec);
  renderLegend(e, dec);
  if (!on) $('#palTicks').innerHTML = '<span>Scegli una scala colori per attivare le regolazioni</span>';
}
function setAdj(p, dec) {
  const u = dec ? ' °C' : '';
  $('#adjB').value = p.b; $('#adjBo').textContent = p.b;
  $('#adjC').value = p.c; $('#adjCo').textContent = p.c;
  $('#adjG').value = p.g; $('#adjGo').textContent = num(p.g, 2);
  $('#adjMin').value = p.min; $('#adjMino').textContent = p.min + u;
  $('#adjMax').value = p.max; $('#adjMaxo').textContent = p.max + u;
  $('#adjCut').checked = !!p.cut;
}
function renderLegend(e, dec) {
  const lut = getLUT(e.pal), stops = [];
  for (let i = 0; i <= 32; i++) { const j = Math.round(i / 32 * 255) * 3; stops.push(`rgb(${lut[j]},${lut[j + 1]},${lut[j + 2]}) ${(i / 32 * 100).toFixed(1)}%`); }
  $('#palLegend').style.background = e.pal.id === 'orig' ? gradCSS(null) : `linear-gradient(90deg,${stops.join(',')})`;
  const lo = e.pal.min, hi = e.pal.max, u = dec ? '°' : '';
  // a sinistra il valore mappato a indice 0: per l'infrarosso e' la soglia calda
  const a = dec ? hi : lo, b = dec ? lo : hi;
  $('#palTicks').innerHTML = [0, 0.25, 0.5, 0.75, 1].map(f => `<span>${Math.round(a + (b - a) * f)}${u}</span>`).join('');
}
const repaintSel = debounce(() => { const e = selEntry(); if (e) { e.layer.repaint(); if (S.cmp.layer && S.cmp.id === e.id) S.cmp.layer.repaint(); } scheduleHash(); }, 50);
$('#palGrid').addEventListener('click', ev => {
  const b = ev.target.closest('.pal-card'); const e = selEntry(); if (!b || !e) return;
  e.pal.id = b.dataset.p;
  renderPalettePanel(); renderStack(); repaintSel();
  const cp = customPals.find(p => p.id === e.pal.id);
  cp ? openEditor(cp) : closeEditor();
});
function adjInput(k, el, out, fmt) {
  el.addEventListener('input', () => {
    const e = selEntry(); if (!e) return;
    let v = +el.value;
    if (k === 'min' && v >= e.pal.max) v = e.pal.max - 1;
    if (k === 'max' && v <= e.pal.min) v = e.pal.min + 1;
    e.pal[k] = v; el.value = v; out.textContent = fmt(v);
    renderLegend(e, decoderFor(cat(e.id))); repaintSel();
  });
}
const unit = () => (decoderFor(cat(S.sel)) ? ' °C' : '');
adjInput('b', $('#adjB'), $('#adjBo'), v => v);
adjInput('c', $('#adjC'), $('#adjCo'), v => v);
adjInput('g', $('#adjG'), $('#adjGo'), v => num(v, 2));
adjInput('min', $('#adjMin'), $('#adjMino'), v => v + unit());
adjInput('max', $('#adjMax'), $('#adjMaxo'), v => v + unit());
$('#blendSel').onchange = ev => { const e = selEntry(); if (!e) return; e.blend = ev.target.value; applyBlend(e); scheduleHash(); };
$('#keyOn').onchange = ev => { const e = selEntry(); if (!e) return; e.pal.key = ev.target.checked; renderPalettePanel(); repaintSel(); };
for (const [k, o] of [['kT', 'keyTo'], ['kW', 'keyWo']]) $('#' + (k === 'kT' ? 'keyT' : 'keyW')).addEventListener('input', ev => {
  const e = selEntry(); if (!e) return; e.pal[k] = +ev.target.value; $('#' + o).textContent = ev.target.value + (decoderFor(cat(e.id)) ? ' °C' : ''); repaintSel();
});
$('#adjCut').onchange = ev => { const e = selEntry(); if (!e) return; e.pal.cut = ev.target.checked; repaintSel(); };
$('#palReset').onclick = () => { const e = selEntry(); if (!e) return; const id = e.pal.id; e.pal = Object.assign(defPal(cat(e.id)), { id }); renderPalettePanel(); repaintSel(); };

/* editor palette personalizzate */
let draft = null;
function openEditor(p) {
  draft = p; $('#palEditor').hidden = false; $('#palName').value = p.nome; $('#palDel').hidden = !customPals.includes(p);
  renderStops();
}
function closeEditor() { draft = null; $('#palEditor').hidden = true; }
function renderStops() {
  draft.stops.sort((a, b) => a[0] - b[0]);
  $('#palEdBar').style.background = gradCSS(draft.stops);
  $('#palStops').innerHTML = draft.stops.map(([q, c], i) => `<div class="stop-row" data-i="${i}">
      <input type="color" value="${c}" aria-label="Colore ${i + 1}">
      <input type="range" min="0" max="100" value="${Math.round(q * 100)}" aria-label="Posizione colore ${i + 1}">
      <output>${Math.round(q * 100)}%</output>
      <button class="ibtn" title="Togli colore" ${draft.stops.length <= 2 ? 'disabled' : ''}>${icon('trash')}</button></div>`).join('');
}
function draftChanged(reRender) {
  draft.v = (draft.v || 0) + 1; lutCache.clear();
  $('#palEdBar').style.background = gradCSS([...draft.stops].sort((a, b) => a[0] - b[0]));
  if (reRender) renderStops();
  const e = selEntry(); if (e && e.pal.id === draft.id) { renderLegend(e, decoderFor(cat(e.id))); repaintSel(); }
  if (customPals.includes(draft)) savePals();
  $$('#palGrid .pal-card').forEach(b => { if (b.dataset.p === draft.id) b.querySelector('.bar').style.background = gradCSS(draft.stops); });
}
$('#palStops').addEventListener('input', ev => {
  const row = ev.target.closest('.stop-row'); if (!row) return; const i = +row.dataset.i;
  if (ev.target.type === 'color') draft.stops[i][1] = ev.target.value;
  else { draft.stops[i][0] = ev.target.value / 100; row.querySelector('output').textContent = `${ev.target.value}%`; }
  draftChanged(false);
});
$('#palStops').addEventListener('change', ev => { if (ev.target.type === 'range') renderStops(); });
$('#palStops').addEventListener('click', ev => {
  const b = ev.target.closest('.ibtn'); if (!b || draft.stops.length <= 2) return;
  draft.stops.splice(+b.closest('.stop-row').dataset.i, 1); draftChanged(true);
});
$('#palAddStop').onclick = () => {
  const s = draft.stops; let gi = 0, gap = 0;
  for (let i = 0; i < s.length - 1; i++) if (s[i + 1][0] - s[i][0] > gap) { gap = s[i + 1][0] - s[i][0]; gi = i; }
  s.splice(gi + 1, 0, [(s[gi][0] + s[gi + 1][0]) / 2, '#ffffff']); draftChanged(true);
};
$('#palNew').onclick = () => {
  const e = selEntry(); const base = palById(e?.pal.id).stops || BUILTIN[2].stops;
  const p = { id: 'u_' + Date.now().toString(36), nome: `Palette ${customPals.length + 1}`, stops: base.map(s => [...s]) };
  customPals.push(p); savePals();
  if (e) e.pal.id = p.id;
  renderPalettePanel(); openEditor(p); repaintSel();
};
$('#palName').oninput = ev => { if (!draft) return; draft.nome = ev.target.value.slice(0, 40) || 'Senza nome'; savePals(); };
$('#palSave').onclick = () => { savePals(); renderPalettePanel(); renderStack(); toast(`Palette “${esc(draft.nome)}” salvata.`, 'ok', 2000); };
$('#palDel').onclick = () => {
  if (!draft) return;
  customPals = customPals.filter(p => p !== draft); savePals();
  for (const e of S.stack) if (e.pal.id === draft.id) { e.pal.id = 'orig'; e.layer.repaint(); }
  closeEditor(); renderPalettePanel(); renderStack();
};
function savePals() { store_.set('meteoSat.palette', customPals.map(({ id, nome, stops }) => ({ id, nome, stops }))); }

/* =========================================================================
 * 13. Confronto a tendina
 * ========================================================================= */
function fillCompareSelect() {
  const sel = $('#cmpLayer'), cur = S.cmp.id;
  sel.innerHTML = S.groups.map(g => `<optgroup label="${esc(g.nome)}">${g.layers.filter(Lr => !Lr.isRef).map(Lr => `<option value="${esc(Lr.id)}">${esc(Lr.nome)}</option>`).join('')}</optgroup>`).join('');
  if (cur) sel.value = cur;
}
function cmpTime() { return new Date(+frameTime() + S.cmp.off * MIN); }
function buildCompareLayer() {
  if (S.cmp.layer) { map.removeLayer(S.cmp.layer); S.cmp.layer = null; }
  if (!S.cmp.on || !S.cmp.id || !cat(S.cmp.id)) return;
  const Lr = cat(S.cmp.id);
  const same = S.stack.find(e => e.id === S.cmp.id);
  const entry = { id: S.cmp.id, op: S.cmp.op, vis: true, pal: same ? same.pal : defPal(Lr) };
  ensureDecoder(Lr);
  S.cmp.layer = new SatLayer(entry, cmpTime, { pane: 'cmp', tileSize: 256, maxNativeZoom: Lr.maxZoom, maxZoom: 18, opacity: S.cmp.op, updateWhenZooming: false, keepBuffer: 1 });
  S.cmp.layer.addTo(map);
  updateOverzoom();
  clipCompare();
}
function clipCompare() {
  const sw = $('#swipe');
  sw.classList.toggle('on', S.cmp.on);
  if (!S.cmp.on) return;
  const size = map.getSize(), x = Math.round(size.x * S.cmp.pos);
  sw.style.left = `${x}px`;
  const nw = map.containerPointToLayerPoint([0, 0]), se = map.containerPointToLayerPoint(size);
  map.getPane('cmp').style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${nw.x + x}px)`;
  updateSwipeLabels();
}
function updateSwipeLabels() {
  if (!S.cmp.on) return;
  const top = S.stack.find(e => e.vis && !cat(e.id).isRef);
  const t = frameTime(), tc = cmpTime();
  $('#swipeA').textContent = `${top ? cat(top.id).nome : 'Vista attuale'}, ${fmtD(t)} ${fmtT(t)}`;
  $('#swipeB').textContent = `${cat(S.cmp.id)?.nome || ''}, ${fmtD(tc)} ${fmtT(tc)}`;
}
function setCompare(on) {
  S.cmp.on = on;
  if (on && !S.cmp.id) { const d = S.stack.find(e => cat(e.id).timed && !cat(e.id).isRef) || S.stack[0]; S.cmp.id = d?.id || [...S.byId.keys()][0]; }
  $('#cmpLayer').value = S.cmp.id || '';
  $('#cmpOffset').value = String(S.cmp.off);
  $('#cmpToggle').textContent = on ? 'Disattiva confronto' : 'Attiva confronto';
  $('#cmpToggle').classList.toggle('primary', !on);
  $('#btnCompare').setAttribute('aria-pressed', String(on));
  buildCompareLayer(); clipCompare(); schedulePreload(); scheduleHash();
}
$('#cmpToggle').onclick = () => setCompare(!S.cmp.on);
$('#cmpLayer').onchange = ev => { S.cmp.id = ev.target.value; if (S.cmp.on) { buildCompareLayer(); schedulePreload(); } scheduleHash(); };
$('#cmpOffset').onchange = ev => { S.cmp.off = +ev.target.value; if (S.cmp.layer) S.cmp.layer.repaint(); updateSwipeLabels(); schedulePreload(); scheduleHash(); };
$('#cmpOpacity').oninput = ev => { S.cmp.op = ev.target.value / 100; $('#cmpOpacityO').textContent = ev.target.value; if (S.cmp.layer) S.cmp.layer.setOpacity(S.cmp.op); };
map.on('move zoom resize viewreset zoomend moveend', clipCompare);
(() => {
  const h = $('#swipeHandle'); let drag = false;
  h.addEventListener('pointerdown', ev => { drag = true; h.setPointerCapture(ev.pointerId); ev.preventDefault(); });
  h.addEventListener('pointermove', ev => { if (!drag) return; const r = map.getContainer().getBoundingClientRect(); S.cmp.pos = clamp((ev.clientX - r.left) / r.width, 0.02, 0.98); clipCompare(); });
  h.addEventListener('pointerup', () => { drag = false; scheduleHash(); });
  h.addEventListener('keydown', ev => {
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') { ev.preventDefault(); ev.stopPropagation(); S.cmp.pos = clamp(S.cmp.pos + (ev.key === 'ArrowLeft' ? -0.03 : 0.03), 0.02, 0.98); clipCompare(); }
  });
})();

/* =========================================================================
 * 14. Sonda e coordinate
 * ========================================================================= */
const probeEl = $('#probe');
const pxCanvas = document.createElement('canvas'); pxCanvas.width = pxCanvas.height = 1;
const pxCtx = pxCanvas.getContext('2d', { willReadFrequently: true });
/* colore ORIGINALE (prima della palette) del pixel sotto latlng, letto da un tassello gia' in memoria:
   zoom corrente (al massimo quello nativo), altrimenti i padri fino a 3 livelli sopra. null = non ancora caricato */
function pixelAt(e, latlng, t) {
  const Lr = cat(e.id); if (!Lr) return null;
  const z0 = Math.min(Math.round(map.getZoom()), Lr.maxZoom), ts = entryTime(e, Lr, t);
  // punti (fuochi): niente padri, a zoom piu' bassi i simboli coprono aree molto piu' grandi del vero
  for (let z = z0; z >= Math.max(0, z0 - (Lr.vettoriale ? 0 : 3)); z--) {
    const p = map.project(latlng, z);
    const tx = Math.floor(p.x / 256), ty = Math.floor(p.y / 256);
    const url = tileURL(Lr, ts, z, tx, ty); if (!url) return null;
    if (!TS.bmps.has(url)) continue;
    const bmp = TS.bmps.get(url);
    if (!bmp) return { r: 0, g: 0, b: 0, a: 0 }; // tassello vuoto (fuori copertura)
    const px = Math.floor(p.x - tx * 256), py = Math.floor(p.y - ty * 256);
    pxCtx.clearRect(0, 0, 1, 1); pxCtx.drawImage(bmp, px, py, 1, 1, 0, 0, 1, 1);
    const [r, g, b, a] = pxCtx.getImageData(0, 0, 1, 1).data;
    return { r, g, b, a };
  }
  return null;
}
function sampleLayer(e, latlng, t) {
  const Lr = cat(e.id); if (!Lr) return null;
  const q = pixelAt(e, latlng, t); if (!q) return null;
  const { r, g, b, a } = q;
  if (!a) return { empty: true, Lr };
  const dec = decoderFor(Lr);
  if (dec) { const v = decodeRGB(dec, (r << 16) | (g << 8) | b); return { Lr, rgb: [r, g, b], val: v, units: dec.units }; }
  const sc = e.pal.id === 'orig' ? (accOn(Lr) ? Lr.scalaAccumulo : Lr.scala) : null;
  if (sc) { const x = scalaClass(sc, r, g, b); if (x) return { Lr, rgb: [r, g, b], cls: classTxt(x, sc.unita || ''), acc: accOn(Lr) }; }
  return { Lr, rgb: [r, g, b] };
}
function probeValue(latlng) {
  let t = frameTime();
  // con il confronto attivo, a destra della tendina si legge il layer di confronto
  if (S.cmp.on && S.cmp.layer) {
    const cp = map.latLngToContainerPoint(latlng);
    if (cp.x > map.getSize().x * S.cmp.pos) { const s = sampleLayer(S.cmp.layer.entry, latlng, cmpTime()); if (s && !s.empty) return s; }
  }
  for (const e of S.stack) { if (!e.vis || cat(e.id).isRef) continue; const s = sampleLayer(e, latlng, t); if (s && !s.empty) return s; }
  return null;
}
function probeHTML(latlng, s) {
  let v = '';
  if (s) {
    if (s.val != null) v = `<b>${num(s.val, 1)} ${esc(s.units || '')}</b><br>`;
    else if (s.cls) v = `<b>${esc(s.cls)}</b>${s.acc ? ' caduti' : ''}<br>`;
    v += `<span class="sw" style="background:rgb(${s.rgb.join(',')})"></span>${esc(s.Lr.nome)}<br>`;
  }
  return `${v}${fmtLat(latlng.lat)}, ${fmtLon(latlng.lng)}`;
}
/* sonda + legenda sotto il cursore: aggiornata al massimo ogni 50 ms (lettura di 1 pixel per layer da tasselli in memoria) */
let probeTimer = 0, probeLast = 0, probeLL = null, probeXY = null;
function probeUpdate() {
  probeTimer = 0; probeLast = performance.now(); if (!probeLL) return;
  const s = probeValue(probeLL);
  $('#stLL').textContent = `${fmtLat(probeLL.lat)}, ${fmtLon(probeLL.lng)}${s && s.val != null ? `, ${num(s.val, 1)} ${s.units || ''}` : s && s.cls ? `, ${s.cls}` : ''}`;
  const rows = LEG.hover ? legHoverRows(probeLL) : null;
  probeEl.classList.toggle('leg', !!(rows && rows.length));
  probeEl.innerHTML = rows && rows.length ? `${rows.map(legRowHTML).join('')}<div class="lgh-c">${fmtLat(probeLL.lat)}, ${fmtLon(probeLL.lng)}</div>` : probeHTML(probeLL, s);
  probeEl.style.display = 'block';
  const w = probeEl.offsetWidth, h = probeEl.offsetHeight;
  const x = probeXY.clientX + 16 + w > innerWidth ? Math.max(4, probeXY.clientX - w - 14) : probeXY.clientX + 16;
  const y = probeXY.clientY + 14 + h > innerHeight - 4 ? Math.max(4, probeXY.clientY - h - 12) : probeXY.clientY + 14;
  probeEl.style.left = `${x}px`; probeEl.style.top = `${y}px`;
}
map.on('mousemove', ev => {
  probeLL = ev.latlng; probeXY = ev.originalEvent;
  if (probeTimer) return;
  probeTimer = setTimeout(() => raf(probeUpdate), Math.max(0, 50 - (performance.now() - probeLast)));
});
map.on('mouseout', () => { probeLL = null; clearTimeout(probeTimer); probeTimer = 0; probeEl.style.display = 'none'; });
map.on('movestart zoomstart', () => { probeEl.style.display = 'none'; });
map.on('click', ev => {
  const ll = ev.latlng, s = probeValue(ll), lon = ((ll.lng + 540) % 360) - 180;
  const dec = `${ll.lat.toFixed(4)}, ${lon.toFixed(4)}`;
  const html = `<div style="min-width:210px">
    ${s && s.val != null ? `<div style="font-size:20px;font-weight:650;font-stretch:85%">${num(s.val, 1)} ${esc(s.units)}</div><div class="muted small" style="margin-bottom:6px">${esc(s.Lr.nome)}, ${fmtD(frameTime())} ${fmtT(frameTime())} UTC</div>` : ''}
    <div id="popVal"></div>
    ${(() => { const rows = legHoverRows(ll); return rows.length ? `<div class="pop-leg">${rows.map(legRowHTML).join('')}</div>` : ''; })()}
    <div>${dms(ll.lat, 'N', 'S')}<br>${dms(lon, 'E', 'O')}</div>
    <div class="muted small" style="margin:4px 0 8px">${dec}</div>
    <button class="btn" data-copy="${dec}">${icon('copy')}Copia coordinate</button>
    <button class="btn" data-hires="${ll.lat.toFixed(5)},${lon.toFixed(5)}" style="margin-top:6px" title="Attiva il layer più nitido disponibile per questo punto">${icon('target')}Alta risoluzione qui</button></div>`;
  L.popup({ maxWidth: 320 }).setLatLng(ll).setContent(html).openOn(map);
  popupRealValue(ll, lon);
});
/* precipitazioni: valore vero nel punto (GetFeatureInfo EUMETSAT) o accumulo calcolato dal server */
async function popupRealValue(ll, lon) {
  const e = S.stack.find(x => x.vis && cat(x.id)?.valoreReale); if (!e || S.mode !== 'api') return;
  const Lr = cat(e.id), t = entryTime(e, Lr, frameTime());
  const box = () => document.getElementById('popVal');
  if (box()) box().innerHTML = `<div class="muted small" style="margin-bottom:6px">Leggo il valore reale di ${esc(Lr.nome)}…</div>`;
  try {
    let html;
    if (accOn(Lr)) {
      const r = await getJSON(`/api/accumulo/${encodeURIComponent(Lr.id)}/${accFrom(Lr)}/${t}?lat=${ll.lat.toFixed(4)}&lon=${lon.toFixed(4)}`, 120000);
      html = r.mm == null ? `<div class="muted small">${r.fuoriDisco ? 'Fuori dalla vista del satellite' : 'Accumulo non disponibile'}</div>`
        : `<div style="font-size:20px;font-weight:650;font-stretch:85%">${num(r.mm, 1)} mm</div><div class="muted small" style="margin-bottom:6px">pioggia caduta dalle ${fmtT(parseTime(r.da))} alle ${fmtT(parseTime(r.a))} UTC del ${fmtD(parseTime(r.a))} (${r.fotogrammi}/${r.fotogrammiTotali} immagini), ${esc(Lr.nome)}</div>`;
    } else {
      const r = await getJSON(`/api/valore/${encodeURIComponent(Lr.id)}?t=${encodeURIComponent(t)}&lat=${ll.lat.toFixed(4)}&lon=${lon.toFixed(4)}`, 30000);
      html = r.valore == null ? `<div class="muted small" style="margin-bottom:6px">${esc(r.nota || r.errore || 'Nessun valore in questo punto')}</div>`
        : `<div style="font-size:20px;font-weight:650;font-stretch:85%">${num(r.valore, 1)} mm/h</div><div class="muted small" style="margin-bottom:6px">${r.valore === 0 ? 'nessuna pioggia, ' : ''}valore reale ${esc(Lr.nome)}, ${fmtD(parseTime(r.tempo))} ${fmtT(parseTime(r.tempo))} UTC${r.qualita != null ? `, qualità ${r.qualita}%` : ''}</div>`;
    }
    if (box()) box().innerHTML = html;
  } catch { if (box()) box().innerHTML = '<div class="muted small" style="margin-bottom:6px">Valore reale non disponibile adesso.</div>'; }
}
document.addEventListener('click', ev => {
  const b = ev.target.closest('[data-copy]'); if (!b) return;
  navigator.clipboard?.writeText(b.dataset.copy).then(() => toast('Coordinate copiate negli appunti.', 'ok', 1600), () => toast('Copia non riuscita: seleziona il testo a mano.', 'err'));
});

/* =========================================================================
 * 14b. Legende a colori di tutti i layer (/api/legende/{layer})
 * Dati: voci {c, nome, def, da?, a?, certo?, alt?, gruppo?} + scala {entries:[[r,g,b,da,a,(gruppo)]]} per i
 * prodotti con valori reali. Il colore del pixel (originale, prima della palette) viene confrontato in spazio Lab
 * con la scala (valore numerico) o con i colori tipici delle voci; oltre la soglia: "colore non in legenda".
 * ========================================================================= */
const LEG = {
  data: new Map(), pend: new Map(), fail: new Map(),
  open: new Set(store_.get('meteoSat.legAperte', [])),
  map: store_.get('meteoSat.legMappa', true),     // legenda compatta sulla mappa
  hover: store_.get('meteoSat.legHover', true),   // definizioni sotto il cursore
  hl: null,                                       // voce evidenziata sulla mappa {id, vi}
};
const legId = Lr => (Lr ? (accOn(Lr) ? '_accumulo' : Lr.id) : null);
const legOf = Lr => (Lr ? LEG.data.get(legId(Lr)) || null : null);
const TIPO_LEG = { A: 'Scala di valori', B: 'Composizione RGB', C: 'Colori reali / riferimento' };
function rgb2lab(r, g, b) {
  const lin = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const R = lin(r), G = lin(g), B = lin(b);
  const f = t => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const fx = f((R * 0.4124 + G * 0.3576 + B * 0.1805) / 0.95047), fy = f(R * 0.2126 + G * 0.7152 + B * 0.0722), fz = f((R * 0.0193 + G * 0.1192 + B * 0.9505) / 1.08883);
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}
function prepLegend(d) {
  d._cand = [];
  // voci "(giorno)"/"(notte)": sui geostazionari si usano solo quelle adatte all'ora del punto (sole sopra/sotto l'orizzonte)
  const dn = v => (/\(giorno\)$/.test(v.nome) ? 'g' : /\(notte\)$/.test(v.nome) ? 'n' : '');
  d._dn = d.voci.some(v => dn(v));
  d.voci.forEach((v, i) => { if (!v.nomatch) for (const c of [v.c, ...(v.alt || [])]) if (c) { const [r, g, b] = hex2(c); d._cand.push([...rgb2lab(r, g, b), i, dn(v)]); } });
  if (d.scala && d.scala.entries) d._ent = d.scala.entries.map(e => rgb2lab(e[0], e[1], e[2]));
  d._memo = new Map();
  return d;
}
function ensureLegend(Lr) {
  const id = legId(Lr);
  if (!id || S.mode !== 'api' || LEG.data.has(id) || LEG.pend.has(id) || Date.now() - (LEG.fail.get(id) || 0) < 60000) return;
  const p = getJSON(`/api/legende/${encodeURIComponent(id)}`, 30000)
    .then(d => { LEG.data.set(id, prepLegend(d)); LEG.fail.delete(id); legendsChanged(); })
    .catch(() => { LEG.fail.set(id, Date.now()); })
    .finally(() => LEG.pend.delete(id));
  LEG.pend.set(id, p);
}
const legendsChanged = debounce(() => renderStack(), 80);
const legMid = e => (e[3] == null ? e[4] : e[4] == null ? e[3] : (e[3] + e[4]) / 2);
/* voce di legenda per il colore r,g,b: {vi (indice voce o -1), e (voce della scala), grp, d (Delta E)} o null */
function legMatch(d, r, g, b, fase = '') {
  const key = ((r << 16) | (g << 8) | b) * 4 + (fase === 'g' ? 1 : fase === 'n' ? 2 : 0);
  let m = d._memo.get(key);
  if (m !== undefined) return m;
  m = null;
  const L = rgb2lab(r, g, b), dist = q => Math.hypot(q[0] - L[0], q[1] - L[1], q[2] - L[2]);
  if (d._ent && !d.qualitativa) {
    // voce della scala: distanza RGB come il decodificatore della sonda (stesso valore), soglia in Delta E Lab
    let bi = -1, bq = 1e9;
    const en = d.scala.entries;
    for (let i = 0; i < en.length; i++) { const q = (en[i][0] - r) ** 2 + (en[i][1] - g) ** 2 + (en[i][2] - b) ** 2; if (q < bq) { bq = q; bi = i; } }
    const bd = bi >= 0 ? dist(d._ent[bi]) : 1e9;
    if (bi >= 0 && bd <= d.soglia) {
      const e = d.scala.entries[bi], grp = e.length > 5 ? e[5] : 0, v = legMid(e);
      const vi = grp > 0 ? d.voci.findIndex(x => x.gruppo === grp)
        : d.voci.findIndex(x => 'da' in x && x.gruppo == null && (x.da == null || v >= x.da) && (x.a == null || v < x.a));
      m = { vi, e, grp, d: bd };
    }
  }
  if (!m && d._cand.length) {
    let bi = -1, bd = 1e9;
    for (const q of d._cand) { if (fase && q[4] && q[4] !== fase) continue; const x = dist(q); if (x < bd) { bd = x; bi = q[3]; } }
    if (bd <= d.soglia) m = { vi: bi, d: bd };
  }
  if (d._memo.size > 8000) d._memo.clear();
  d._memo.set(key, m);
  return m;
}
const legNum = (v, d) => num(v, Math.abs(v) >= 1000 ? 0 : d.dec ?? 1);
const legU = d => (d.unita ? ` ${d.unita}` : '');
function legValTxt(d, e) {
  const lo = e[3], hi = e[4];
  if (lo == null) return `< ${legNum(hi, d)}${legU(d)}`;
  if (hi == null) return `≥ ${legNum(lo, d)}${legU(d)}`;
  if (d.scala.classi || d.classi) return `${legNum(lo, d)}–${legNum(hi, d)}${legU(d)}`;
  return `${legNum((lo + hi) / 2, d)}${legU(d)}`;
}
function legRangeTxt(d, v) {
  if (!('da' in v)) return '';
  if (v.da == null) return `sotto ${legNum(v.a, d)}${legU(d)}`;
  if (v.a == null) return `da ${legNum(v.da, d)}${legU(d)} in su`;
  return `${legNum(v.da, d)} – ${legNum(v.a, d)}${legU(d)}`;
}
/* barra colori di una scala: stop del gradiente (o blocchi per le classi) e tacche ai confini delle voci */
function legBar(d) {
  if (d._bar !== undefined) return d._bar;
  if (!d.scala || d.qualitativa) return (d._bar = null);
  const s = d.scala.entries.filter(e => !(e.length > 5 && e[5] > 0)).slice().sort((a, b) => legMid(a) - legMid(b)), n = s.length;
  if (!n) return (d._bar = null);
  const col = e => `rgb(${e[0]},${e[1]},${e[2]})`;
  const classi = !!(d.scala.classi || n <= 16);
  const step = Math.max(1, Math.ceil(n / 96)), stops = [];
  if (!classi) { for (let i = 0; i < n; i += step) stops.push([i / (n - 1 || 1), col(s[i])]); stops.push([1, col(s[n - 1])]); }
  const pos = v => { let i = s.findIndex(e => e[4] == null || v < e[4]); if (i < 0) i = n - 1; return classi ? i / n : i / (n - 1 || 1); };
  let ticks = [...new Set(d.voci.filter(v => v.gruppo == null && v.da != null).map(v => v.da))].map(v => [pos(v), legNum(v, d)]);
  if (ticks.length > 6) ticks = ticks.filter((_, i) => i % 2 === 0);
  return (d._bar = { classi, stops, blocks: classi ? s.map(col) : null, ticks });
}
function legBarHTML(d) {
  const b = legBar(d); if (!b) return '';
  const bg = b.classi ? '' : `background:linear-gradient(90deg,${b.stops.map(([p, c]) => `${c} ${(p * 100).toFixed(1)}%`).join(',')})`;
  return `<div class="lg-bar ${b.classi ? '' : 'cont'}" style="${bg}">${b.classi ? b.blocks.map(c => `<i style="background:${c}"></i>`).join('') : ''}</div>
    <div class="lg-ticks leg-ticks">${b.ticks.map(([p, t]) => `<span style="left:${(p * 100).toFixed(1)}%">${esc(t)}</span>`).join('')}</div>`;
}
/* --- pannello Livelli: legenda comprimibile sotto ogni layer --- */
function legPanelHTML(Lr) {
  const d = legOf(Lr);
  if (!d) return `<div class="muted small">${S.mode !== 'api' ? 'Legenda disponibile solo con il server locale.' : 'Carico la legenda…'}</div>`;
  const e = S.stack.find(x => x.id === Lr.id), palOn = e && e.pal.id !== 'orig';
  return `<div class="leg-head"><span class="leg-tipo t${d.tipo}">${TIPO_LEG[d.tipo] || ''}</span><b>${esc(d.titolo)}</b>${d.unita ? `<span class="muted"> · ${esc(d.unita)}</span>` : ''}</div>
    ${legBarHTML(d)}
    <ul class="leg-list">${d.voci.map((v, i) => `<li><button type="button" class="leg-voce" data-leg="${esc(Lr.id)}" data-vi="${i}" aria-pressed="${!!(LEG.hl && LEG.hl.id === Lr.id && LEG.hl.vi === i)}" title="Evidenzia sulla mappa dove compare questo colore">
      <span class="sw" style="background:${esc(v.c)}"></span><span class="leg-t"><b>${esc(v.nome)}</b>${legRangeTxt(d, v) ? `<span class="leg-r">${esc(legRangeTxt(d, v))}</span>` : ''}${v.certo === false ? '<span class="tag-ind">indicativo</span>' : ''}<span class="leg-def">${esc(v.def)}</span></span></button></li>`).join('')}</ul>
    ${palOn ? '<div class="muted small leg-note">Palette attiva: in mappa i colori sono cambiati; la sonda legge comunque il colore originale.</div>' : ''}
    ${d.nota ? `<div class="muted small leg-note">${esc(d.nota)}</div>` : ''}
    <div class="muted small leg-note">Fonte: ${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.fonte)}</a>` : esc(d.fonte || '')}</div>`;
}
$('#stack').addEventListener('toggle', ev => {
  const det = ev.target.closest && ev.target.closest('details.leg'); if (!det) return;
  det.open ? LEG.open.add(det.dataset.id) : LEG.open.delete(det.dataset.id);
  store_.set('meteoSat.legAperte', [...LEG.open]);
}, true);
/* --- voce evidenziata sulla mappa: i pixel di altri colori vengono attenuati (solo colori originali) --- */
function legHighlight(id, vi) {
  const prev = LEG.hl;
  LEG.hl = prev && prev.id === id && prev.vi === vi ? null : { id, vi };
  const e = S.stack.find(x => x.id === id);
  if (LEG.hl && e && e.pal.id !== 'orig') toast('Per evidenziare una voce rimetti la palette “Originale”: con un’altra palette i colori in mappa sono diversi.', 'info', 5000);
  if (LEG.hl && S.playing) pause();
  for (const x of S.stack) if ((prev && x.id === prev.id) || (LEG.hl && x.id === LEG.hl.id)) x.layer.repaint();
  renderStack(); renderMapLegend();
}
function legHighlightCanvas(c, Lr) {
  const hl = LEG.hl, d = legOf(Lr); if (!hl || !d || hl.id !== Lr.id) return;
  const ctx = c.getContext('2d', { willReadFrequently: true }), img = ctx.getImageData(0, 0, 256, 256), a = img.data;
  for (let i = 0; i < a.length; i += 4) {
    if (a[i + 3] < 16) continue;
    const m = legMatch(d, a[i], a[i + 1], a[i + 2]);
    if (!m || m.vi !== hl.vi) { const l = (a[i] + a[i + 1] + a[i + 2]) / 3 * 0.35; a[i] = a[i + 1] = a[i + 2] = l; a[i + 3] *= 0.55; }
  }
  ctx.putImageData(img, 0, 0);
}
/* --- legenda compatta sulla mappa (layer selezionato + altri attivi, max 3) --- */
const mapLeg = (() => {
  const d = document.createElement('section'); d.className = 'map-legend glass'; d.setAttribute('aria-label', 'Legenda della mappa');
  document.body.appendChild(d); return d;
})();
function mapLegLayers() {
  const vis = S.stack.filter(e => e.vis && cat(e.id) && !cat(e.id).isRef);
  const sel = vis.find(e => e.id === S.sel);
  return [...(sel ? [sel] : []), ...vis.filter(e => e !== sel)].slice(0, 3).map(e => cat(e.id));
}
function renderMapLegend() {
  const left = $('#leftPanel'), leftOpen = left && !left.classList.contains('hidden');
  mapLeg.classList.toggle('shift', leftOpen && !mobile());
  const tc = document.querySelector('.tc-legend');
  mapLeg.classList.toggle('up', !!(tc && !tc.hidden)); // legenda dei cicloni visibile in basso: sali sopra
  const lays = S.mode === 'api' ? mapLegLayers() : [];
  if (!lays.length) { mapLeg.hidden = true; return; }
  mapLeg.hidden = false;
  if (!LEG.map) {
    mapLeg.classList.add('min');
    mapLeg.innerHTML = `<button type="button" class="ml-show" data-ml="show" title="Mostra la legenda dei colori">${icon('palette')}Legenda</button>`;
    return;
  }
  mapLeg.classList.remove('min');
  lays.forEach(ensureLegend);
  const body = lays.map(Lr => {
    const d = legOf(Lr);
    if (!d) return `<div class="ml-lay"><div class="ml-t">${esc(Lr.nome)}</div><div class="muted small">Carico la legenda…</div></div>`;
    const hlHere = LEG.hl && LEG.hl.id === Lr.id;
    return `<div class="ml-lay"><div class="ml-t" title="${esc(d.titolo)}">${esc(Lr.nome)}${d.unita ? ` <span class="muted">(${esc(d.unita)})</span>` : ''}</div>
      ${legBarHTML(d)}
      <div class="ml-chips">${d.voci.map((v, i) => `<button type="button" class="ml-chip" data-leg="${esc(Lr.id)}" data-vi="${i}" aria-pressed="${!!(hlHere && LEG.hl.vi === i)}" title="${esc(v.nome)}: ${esc(v.def)}"><span class="sw" style="background:${esc(v.c)}"></span>${esc(v.nome)}</button>`).join('')}</div>
      ${hlHere ? `<div class="ml-tip"><b>${esc(d.voci[LEG.hl.vi]?.nome || '')}</b> ${esc(d.voci[LEG.hl.vi]?.def || '')}${d.voci[LEG.hl.vi]?.certo === false ? ' <span class="tag-ind">indicativo</span>' : ''}<br><span class="muted">Sulla mappa sono attenuati gli altri colori.</span> <button type="button" class="linkbtn" data-ml="nohl">Togli evidenziazione</button></div>` : ''}
    </div>`;
  }).join('');
  mapLeg.innerHTML = `<div class="ml-h"><h4>Legenda</h4>
      <label class="ml-opt" title="Mostra nome e definizione del colore sotto il puntatore"><input type="checkbox" data-ml="hover" ${LEG.hover ? 'checked' : ''}> Definizioni al passaggio</label>
      <button type="button" class="ibtn x" data-ml="hide" title="Nascondi la legenda" aria-label="Nascondi la legenda">${icon('x')}</button></div>
    <div class="ml-b">${body}</div>`;
}
mapLeg.addEventListener('click', ev => {
  const b = ev.target.closest('[data-ml], [data-leg]'); if (!b) return;
  if (b.dataset.leg) { legHighlight(b.dataset.leg, +b.dataset.vi); return; }
  const a = b.dataset.ml;
  if (a === 'hide' || a === 'show') { LEG.map = a === 'show'; store_.set('meteoSat.legMappa', LEG.map); renderMapLegend(); }
  if (a === 'nohl' && LEG.hl) legHighlight(LEG.hl.id, LEG.hl.vi);
});
mapLeg.addEventListener('change', ev => {
  if (ev.target.dataset.ml === 'hover') { LEG.hover = ev.target.checked; store_.set('meteoSat.legHover', LEG.hover); }
});
['mousedown', 'dblclick', 'wheel', 'touchstart', 'pointerdown'].forEach(t => mapLeg.addEventListener(t, ev => ev.stopPropagation(), { passive: true }));
$('#stack').addEventListener('click', ev => {
  const b = ev.target.closest('.leg-voce'); if (!b) return;
  legHighlight(b.dataset.leg, +b.dataset.vi);
});
new MutationObserver(() => renderMapLegend()).observe($('#leftPanel'), { attributes: true, attributeFilter: ['class'] });
{ const tc = document.querySelector('.tc-legend'); if (tc) new MutationObserver(() => renderMapLegend()).observe(tc, { attributes: true, attributeFilter: ['hidden'] }); }
/* --- sotto il cursore: una riga per ogni layer visibile (dal piu' alto), saltando i pixel trasparenti --- */
function legHoverRows(latlng) {
  const t = frameTime(), out = [], cands = [];
  if (S.cmp.on && S.cmp.layer) {
    const cp = map.latLngToContainerPoint(latlng);
    if (cp.x > map.getSize().x * S.cmp.pos) cands.push([S.cmp.layer.entry, cmpTime(), true]);
  }
  for (const e of S.stack) if (e.vis) cands.push([e, t, false]);
  for (const [e, tt, isCmp] of cands) {
    if (out.length >= 4) break;
    const Lr = cat(e.id); if (!Lr) continue;
    const px = pixelAt(e, latlng, tt);
    if (!px || px.a < 48) continue;
    const d = legOf(Lr);
    if (!d) ensureLegend(Lr);
    let fase = '';
    if (d && d._dn && (Lr.geo || Lr.nativo || Lr.categoria === 'geostazionario') && !Lr.orbite) {
      const z = sunZenith(parseTime(entryTime(e, Lr, tt)) || tt, latlng.lat, latlng.lng);
      fase = z < 85 ? 'g' : z > 95 ? 'n' : '';
    }
    out.push({ Lr, d, px, m: d ? legMatch(d, px.r, px.g, px.b, fase) : null, isCmp });
  }
  return out;
}
function legRowHTML(r) {
  const { Lr, d, px, m } = r, pc = `rgb(${px.r},${px.g},${px.b})`;
  const lay = `<div class="lgh-l">${esc(Lr.nome)}${r.isCmp ? ' (confronto)' : ''}</div>`;
  if (!d) return `<div class="lgh-r"><span class="sw" style="background:${pc}"></span><div>${lay}</div></div>`;
  if (!m) return `<div class="lgh-r"><span class="sw nl" style="background:${pc}"></span><div><div class="lgh-n muted">Colore non in legenda</div>${lay}</div></div>`;
  const v = m.vi >= 0 ? d.voci[m.vi] : null;
  const val = m.e ? legValTxt(d, m.e) : '';
  // riquadro diviso: a sinistra il colore del pixel, a destra il colore di riferimento della voce
  return `<div class="lgh-r"><span class="sw" title="Sinistra: colore sotto il puntatore. Destra: colore della voce in legenda" style="background:${v ? `linear-gradient(90deg,${pc} 50%,${esc(v.c)} 50%)` : pc}"></span><div>
    <div class="lgh-n">${v ? `<b>${esc(v.nome)}</b>` : ''}${val ? `<span class="lgh-v">${esc(val)}</span>` : ''}${v && (v.certo === false || !m.e) ? '<span class="tag-ind" title="Dedotto dal colore: non tiene conto del luogo (mare, montagna, deserto)">stima dal colore</span>' : ''}</div>
    ${v ? `<div class="lgh-d">${esc(v.def)}</div>` : ''}${lay}</div></div>`;
}

/* =========================================================================
 * 15. Ricerca luogo (Nominatim / coordinate)
 * ========================================================================= */
$('#searchForm').addEventListener('submit', async ev => {
  ev.preventDefault();
  const q = $('#searchInput').value.trim(), box = $('#searchResults'); if (!q) return;
  const m = q.match(/^\s*(-?\d+(?:[.,]\d+)?)\s*[,; ]\s*(-?\d+(?:[.,]\d+)?)\s*$/);
  if (m) {
    const lat = +m[1].replace(',', '.'), lon = +m[2].replace(',', '.');
    if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) { map.flyTo([lat, lon], Math.max(map.getZoom(), 7)); box.classList.remove('open'); return; }
  }
  box.innerHTML = '<button disabled>Cerco…</button>'; box.classList.add('open');
  try {
    const r = await getJSON(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&accept-language=it&q=${encodeURIComponent(q)}`, 10000);
    box.innerHTML = r.length ? r.map(p => `<button type="button" data-lat="${p.lat}" data-lon="${p.lon}" data-bb="${(p.boundingbox || []).join(',')}">${esc(p.name || p.display_name.split(',')[0])}<small>${esc(p.display_name)}</small></button>`).join('')
      : `<button type="button" disabled>Nessun luogo trovato per “${esc(q)}”.</button>`;
  } catch {
    box.innerHTML = '<button type="button" disabled>Ricerca non disponibile: puoi scrivere le coordinate, es. 45.07, 7.68</button>';
  }
});
$('#searchResults').addEventListener('click', ev => {
  const b = ev.target.closest('button[data-lat]'); if (!b) return;
  const bb = b.dataset.bb.split(',').map(Number);
  if (bb.length === 4 && bb.every(isFinite)) map.flyToBounds([[bb[0], bb[2]], [bb[1], bb[3]]], { maxZoom: 9, duration: 1.2 });
  else map.flyTo([+b.dataset.lat, +b.dataset.lon], 8);
  $('#searchResults').classList.remove('open');
});
document.addEventListener('click', ev => { if (!ev.target.closest('#searchForm')) $('#searchResults').classList.remove('open'); });

/* =========================================================================
 * 16. Esportazione PNG e GIF
 * ========================================================================= */
/* o (facoltativo, usato dal video): {canvas, W, H, overlay(ctx, W, H)} = tela riusata, dimensioni esatte, sovrimpressione in pixel finali */
function composeCanvas(outW, stamp = true, o = {}) {
  const size = map.getSize(), rect = map.getContainer().getBoundingClientRect();
  const s = outW ? outW / size.x : 1;
  const cv = o.canvas || document.createElement('canvas');
  const W = o.W || Math.round(size.x * s), H = o.H || Math.round(size.y * s);
  if (cv.width !== W) cv.width = W;
  if (cv.height !== H) cv.height = H;
  const ctx = cv.getContext('2d');
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
  ctx.fillStyle = '#06101a'; ctx.fillRect(0, 0, cv.width, cv.height);
  const sx = W / size.x;
  ctx.scale(sx, H / size.y);
  // solo le tele dei tasselli (riempite da ImageBitmap di blob scaricati con fetch CORS: sempre leggibili);
  // un tassello che non si disegna viene saltato e contato, non blocca l'esportazione
  cv._skipped = 0;
  const drawLayer = lyr => {
    for (const t of lyr.currentTiles()) {
      if (!(t.el instanceof HTMLCanvasElement)) { cv._skipped++; continue; }
      const r = t.el.getBoundingClientRect();
      if (r.right < rect.left || r.left > rect.right || r.bottom < rect.top || r.top > rect.bottom) continue;
      // tassello disegnato 1:1 quando possibile; ingrandito (oltre lo zoom nativo) a pixel netti, senza sfumare
      ctx.imageSmoothingEnabled = r.width * sx < 1022; ctx.imageSmoothingQuality = 'high';   // come nel browser: pixel netti solo oltre 4x
      try { ctx.drawImage(t.el, r.left - rect.left, r.top - rect.top, r.width, r.height); } catch { cv._skipped++; }
    }
  };
  for (const e of [...S.stack].reverse()) { if (!e.vis) continue; ctx.globalAlpha = e.op; ctx.globalCompositeOperation = effBlend(e) || 'source-over'; drawLayer(e.layer); ctx.globalCompositeOperation = 'source-over'; }
  if (S.cmp.on && S.cmp.layer) {
    ctx.save(); ctx.beginPath(); ctx.rect(size.x * S.cmp.pos, 0, size.x, size.y); ctx.clip();
    ctx.globalAlpha = S.cmp.op; drawLayer(S.cmp.layer); ctx.restore();
    ctx.globalAlpha = 1; ctx.fillStyle = 'rgba(228,237,246,.9)'; ctx.fillRect(size.x * S.cmp.pos - 1, 0, 2, size.y);
  }
  ctx.globalAlpha = 1;
  // coste, laghi, fiumi e confini vettoriali: sopra i satelliti come in mappa
  if (coastVectorOn()) { const pb = map.getPixelBounds(); drawCoastCtx(ctx, map.getZoom(), pb.min.x, pb.min.y, 0, 0, size.x, size.y); }
  // tracce e marcatori degli eventi (nel video tutto piu' grande in proporzione, cosi' si legge)
  const U = o.overlay ? clamp(size.y / 700, 1.25, 3) : 1;
  trkLayer.eachLayer(l => {
    if (l.getLatLng && l.options && l.options.icon && l.options.icon.options && /tc-pt/.test(l.options.icon.options.html || '')) {
      const p = map.latLngToContainerPoint(l.getLatLng()), m = /--c:([^;"]+)/.exec(l.options.icon.options.html || '');
      if (p.x < -10 || p.y < -10 || p.x > size.x + 10 || p.y > size.y + 10) return;
      ctx.beginPath(); ctx.arc(p.x, p.y, 4 * U, 0, Math.PI * 2); ctx.fillStyle = m ? m[1] : '#9fb3c8'; ctx.fill();
      ctx.lineWidth = 1.5 * U; ctx.strokeStyle = 'rgba(6,16,26,.9)'; ctx.stroke(); return;
    }
    if (!l.getLatLngs) return;
    const op = l.options, poly = !!op.fillColor;
    ctx.save(); ctx.globalAlpha = op.opacity != null ? op.opacity : 1;
    ctx.strokeStyle = op.color; ctx.lineWidth = Math.max(1.5, op.weight || 2) * U;
    ctx.setLineDash(op.dashArray ? String(op.dashArray).split(/[ ,]+/).map(v => +v * U) : (poly ? [] : [5 * U, 5 * U])); ctx.beginPath();
    // le traiettorie possono essere multi-linea (array di array, es. a cavallo dell'antimeridiano)
    const pts = l.getLatLngs() || [], parts = Array.isArray(pts[0]) ? pts : [pts];
    for (const part of parts) (part || []).filter(Boolean).forEach((ll, i) => { const p = map.latLngToContainerPoint(ll); i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); });
    if (poly) { ctx.closePath(); ctx.globalAlpha = op.fillOpacity != null ? op.fillOpacity : 0.1; ctx.fillStyle = op.fillColor; ctx.fill(); ctx.globalAlpha = op.opacity != null ? op.opacity : 1; }
    ctx.stroke(); ctx.restore();
  });
  const tcDraw = [];
  for (const e of evVisible()) {
    const p = map.latLngToContainerPoint([e.lat, e.lon]); if (p.x < 0 || p.y < 0 || p.x > size.x || p.y > size.y) continue;
    if (e.tc && o.overlay) { tcDraw.push([e, p]); continue; }
    ctx.beginPath(); ctx.arc(p.x, p.y, 6, 0, Math.PI * 2); ctx.fillStyle = 'rgba(6,16,26,.85)'; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = CATS[e.cat].c; ctx.stroke();
  }
  // cicloni nel video: simbolo grande che gira + nome e forza accanto
  for (const [e, p] of tcDraw) drawCycloneCtx(ctx, e, p, U, size);
  if (o.overlay) { ctx.setTransform(1, 0, 0, 1, 0, 0); o.overlay(ctx, W, H); }
  else if (stamp) drawStamp(ctx, size.x, size.y);
  return cv;
}
/* ciclone nel video: spirale colorata per categoria (gira con lo scorrere del video) con etichetta nome + categoria + vento */
const TC_PATH = new Path2D('M20 4c-6 0-11 3-13 8 3-2 6-3 9-2-5 2-8 6-8 11 0 6 5 11 12 11 6 0 11-3 13-8-3 2-6 3-9 2 5-2 8-6 8-11 0-6-5-11-12-11z');
function drawCycloneCtx(ctx, e, p, U, size) {
  const c = e.tc, col = tcCol(c.categoria), R = 19 * U;
  ctx.save(); ctx.translate(p.x, p.y);
  ctx.beginPath(); ctx.arc(0, 0, R * 1.55, 0, Math.PI * 2); ctx.strokeStyle = col; ctx.globalAlpha = 0.55; ctx.lineWidth = 2 * U; ctx.stroke(); ctx.globalAlpha = 1;
  ctx.beginPath(); ctx.arc(0, 0, R * 1.08, 0, Math.PI * 2); ctx.fillStyle = 'rgba(6,16,26,.72)'; ctx.fill();
  ctx.save(); ctx.rotate((c.emisfero === 'S' ? 1 : -1) * (S.idx || 0) * 0.5); ctx.scale(R * 2 / 40, R * 2 / 40); ctx.translate(-20, -20);
  ctx.fillStyle = col; ctx.strokeStyle = '#0b1825'; ctx.lineWidth = 1.5; ctx.fill(TC_PATH); ctx.stroke(TC_PATH);
  ctx.beginPath(); ctx.arc(20, 20, 5.2, 0, Math.PI * 2); ctx.fillStyle = '#0b1825'; ctx.fill(); ctx.restore();
  const l1 = String(c.titolo || e.titolo || 'Ciclone'), l2 = [tcBadge(c.categoria), c.ventoKmh != null ? `${c.ventoKmh} km/h` : ''].filter(Boolean).join('  ·  ');
  const f1 = `700 ${Math.round(17 * U)}px Archivo, "Segoe UI", sans-serif`, f2 = `500 ${Math.round(13 * U)}px Archivo, "Segoe UI", sans-serif`;
  ctx.font = f1; const w1 = ctx.measureText(l1).width; ctx.font = f2; const w2 = ctx.measureText(l2).width;
  const bw = Math.max(w1, w2) + 22 * U, bh = 46 * U, right = p.x + R * 1.7 + bw < size.x - 8;
  const bx = right ? R * 1.7 : -R * 1.7 - bw, by = clamp(-bh / 2, -p.y + 8, size.y - 8 - p.y - bh);
  ctx.fillStyle = 'rgba(6,16,26,.84)'; ctx.fillRect(bx, by, bw, bh);
  ctx.fillStyle = col; ctx.fillRect(right ? bx : bx + bw - 4 * U, by, 4 * U, bh);
  ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
  ctx.fillStyle = '#ffffff'; ctx.font = f1; ctx.fillText(l1, bx + 12 * U, by + 20 * U);
  ctx.fillStyle = col; ctx.font = f2; ctx.fillText(l2, bx + 12 * U, by + 38 * U);
  ctx.restore();
}
function drawStamp(ctx, W, H) {
  const t = frameTime();
  const names = S.stack.filter(e => e.vis && !cat(e.id).isRef).map(e => cat(e.id).nome).slice(0, 3).join(' + ');
  const l1 = `${fmtD(t)}  ${fmtT(t)} UTC   (Italia ${fmtItT(t)})`;
  ctx.font = '600 15px Archivo, "Segoe UI", sans-serif';
  const w1 = ctx.measureText(l1).width; ctx.font = '400 12px Archivo, "Segoe UI", sans-serif';
  const w2 = ctx.measureText(names).width; const bw = Math.max(w1, w2) + 28;
  ctx.fillStyle = 'rgba(6,16,26,.78)'; ctx.fillRect(12, H - 62, bw, 50);
  ctx.fillStyle = '#62e3d8'; ctx.fillRect(12, H - 62, 3, 50);
  ctx.fillStyle = '#e4edf6'; ctx.font = '600 15px Archivo, "Segoe UI", sans-serif'; ctx.fillText(l1, 26, H - 41);
  ctx.fillStyle = '#a3b6c9'; ctx.font = '400 12px Archivo, "Segoe UI", sans-serif'; ctx.fillText(names, 26, H - 22);
  ctx.textAlign = 'right'; ctx.fillStyle = 'rgba(228,237,246,.7)'; ctx.font = '500 11px Archivo, "Segoe UI", sans-serif';
  ctx.fillText(`Meteo Sat, dati ${sourcesFor([...S.stack.filter(e => e.vis).map(e => e.id), ...(S.cmp.on && S.cmp.id ? [S.cmp.id] : [])])}`, W - 12, H - 14); ctx.textAlign = 'left';
  // legenda del layer principale (palette, scala o voci) in basso a destra, sopra la riga delle fonti
  const lg = legendFor();
  if (lg && W >= 360 && H >= 240) drawLegCanvas(ctx, lg, W - 22, H - 30, clamp(W / 1400, 0.75, 1.3));
}
/* nome file coerente per PNG, GIF e video: meteo_{layer}_{gg-mm-aa}_{hhmm}.{ext} */
function exportName(ext, t = frameTime()) {
  const e = S.stack.find(x => x.vis && !cat(x.id).isRef && cat(x.id).timed) || S.stack.find(x => x.vis && !cat(x.id).isRef) || S.stack[0];
  const Lr = e && cat(e.id);
  const slug = String(Lr ? Lr.nome : 'mappa').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'mappa';
  return `meteo_${slug}_${fmtD(t)}_${fmtT(t).replace(':', '')}.${ext}`;
}
function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}
/* la tela composta deve essere leggibile (nessun tassello "contaminato"): altrimenti errore chiaro */
function assertReadable(cv) {
  try { cv.getContext('2d').getImageData(0, 0, 1, 1); }
  catch { throw new Error('il browser considera non leggibile un tassello (protezione CORS): ricarica la pagina e riprova'); }
}
const errText = err => (err && err.message) ? err.message : String(err || 'errore sconosciuto');
const fmtSec = s => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.max(1, Math.round(s))} s`);
/* avanzamento con stima del tempo restante */
function progressText(phase, k, n, t0) {
  const el = (performance.now() - t0) / 1000;
  const eta = k > 0 && k < n ? `, mancano circa ${fmtSec(el / k * (n - k))}` : '';
  return `${phase} ${k} di ${n} (${fmtSec(el)}${eta})`;
}

/* pannello del risultato: anteprima, nome, MB, "Salva con nome…" e "Scarica" */
const EXP_TYPES = { png: ['Immagine PNG', 'image/png'], gif: ['Animazione GIF', 'image/gif'], mp4: ['Video MP4', 'video/mp4'], webm: ['Video WebM', 'video/webm'] };
function clearExportResult(box) {
  if (box._url) { URL.revokeObjectURL(box._url); box._url = null; }
  box._blob = null; box.innerHTML = ''; box.hidden = true;
}
function showExportResult(box, blob, name, note = '') {
  clearExportResult(box);
  const ext = name.split('.').pop().toLowerCase(), [desc, mime] = EXP_TYPES[ext] || ['File', blob.type];
  const url = box._url = URL.createObjectURL(blob); box._blob = blob;
  const isVideo = ext === 'mp4' || ext === 'webm', mb = blob.size / 1048576;
  const canPick = !!window.showSaveFilePicker;
  box.innerHTML = `<div class="exp-prev">${isVideo ? `<video src="${url}" controls muted loop playsinline preload="metadata"></video>` : `<img src="${url}" alt="Anteprima di ${esc(name)}">`}</div>
    <div class="exp-meta"><b>${esc(name)}</b><span>${desc}, ${num(mb, mb < 1 ? 2 : 1)} MB</span>${note ? `<span class="exp-note">${esc(note)}</span>` : ''}<span class="exp-saved" hidden></span></div>
    <div class="row exp-btns">${canPick ? '<button class="btn primary" data-act="saveas">Salva con nome…</button>' : ''}<button class="btn ${canPick ? 'ghost' : 'primary'}" data-act="dl">Scarica</button></div>
    ${isVideo ? `<div class="exp-yt"><input class="exp-yt-t" type="text" maxlength="100" value="${esc(name.replace(/\.[^.]+$/, ''))}" aria-label="Titolo su YouTube"><div class="row"><select class="exp-yt-p" aria-label="Visibilità"><option value="public">Pubblico</option><option value="unlisted">Non in elenco</option><option value="private">Privato</option></select><button class="btn primary" data-act="yt">Pubblica su YouTube</button></div><span class="exp-yt-s muted small" hidden></span></div>` : ''}
    <p class="muted small" style="margin:0">“Scarica” salva il file nella <b>Cartella Download del browser</b>.${canPick ? ' “Salva con nome…” ti fa scegliere cartella e nome.' : ''}</p>`;
  box.hidden = false;
  const saved = box.querySelector('.exp-saved');
  const done = txt => { saved.hidden = false; saved.textContent = txt; saved.className = 'exp-saved exp-note'; };
  const sa = box.querySelector('[data-act="saveas"]');
  if (sa) sa.onclick = async () => {
    try {
      const fh = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: desc, accept: { [mime]: ['.' + ext] } }] });
      const w = await fh.createWritable(); await w.write(blob); await w.close();
      done(`Salvato come “${fh.name}” nella cartella che hai scelto.`);
      toast(`Salvato: ${esc(fh.name)}`, 'ok', 4500);
    } catch (e) {
      if (e && e.name === 'AbortError') return; // finestra chiusa senza salvare
      saved.hidden = false; saved.className = 'exp-saved exp-err'; saved.textContent = `Salvataggio non riuscito: ${errText(e)}`;
      toast(`Salvataggio non riuscito: ${esc(errText(e))}`, 'err', 7000);
    }
  };
  const ytb = box.querySelector('[data-act="yt"]');
  if (ytb) ytb.onclick = async () => {
    const st = box.querySelector('.exp-yt-s'), tt = box.querySelector('.exp-yt-t').value.trim() || name;
    const pr = box.querySelector('.exp-yt-p').value;
    const say = (h, cls = '') => { st.hidden = false; st.className = 'exp-yt-s small ' + cls; st.innerHTML = h; };
    ytb.disabled = true; say('Invio del video a YouTube…');
    try {
      const r = await fetch('/api/youtube/carica', { method: 'POST', headers: { 'Content-Type': blob.type || 'video/mp4', 'X-Titolo': encodeURIComponent(tt), 'X-Descrizione': encodeURIComponent('Immagini da satellite: Meteo Sat.'), 'X-Privacy': pr }, body: blob });
      const j = await r.json(); if (!r.ok) throw new Error(j.errore || j.error || r.status);
      for (let i = 0; i < 1800; i++) {
        await new Promise(ok => setTimeout(ok, 2000));
        const q = await (await fetch('/api/youtube/stato/' + j.id, { method: 'POST', body: '' })).json();
        if (q.stato === 'fatto') { say(`Caricato (${pr === 'public' ? 'pubblico' : pr === 'unlisted' ? 'non in elenco' : 'privato'}): <a href="${q.link}" target="_blank" rel="noopener">${q.link}</a>`, 'exp-note'); toast('Video caricato su YouTube', 'ok', 4500); return; }
        if (q.stato === 'errore') throw new Error(q.errore);
        say('YouTube sta elaborando il caricamento…');
      }
      throw new Error('tempo scaduto');
    } catch (e) { say('Pubblicazione non riuscita: ' + esc(errText(e)), 'exp-err'); toast('Pubblicazione non riuscita', 'err', 6000); ytb.disabled = false; }
  };
  box.querySelector('[data-act="dl"]').onclick = () => {
    try {
      download(blob, name);
      done(`Scaricato “${name}” nella Cartella Download del browser.`);
      toast(`${esc(name)} scaricato nella Cartella Download del browser.`, 'ok', 4500);
    } catch (e) { toast(`Download non riuscito: ${esc(errText(e))}`, 'err', 7000); }
  };
}
function showExportError(msgEl, what, err) {
  console.warn(what, err);
  msgEl.innerHTML = `<span class="exp-err">${esc(what)} non riuscita: ${esc(errText(err))}.</span>`;
  toast(`${esc(what)} non riuscita: ${esc(errText(err))}`, 'err', 7000);
}

/* --- PNG --- */
const PNG = { busy: false, abort: false };
async function makePng() {
  if (PNG.busy) return;
  PNG.busy = true; PNG.abort = false; pause();
  const bar = $('#pngProg').firstChild, msg = $('#pngMsg'), box = $('#pngRes');
  clearExportResult(box); bar.style.width = '10%';
  $('#pngGo').disabled = true; $('#pngStop').hidden = false;
  try {
    msg.textContent = 'Aspetto che tutti i tasselli della vista siano arrivati…';
    await waitTiles(30000, () => PNG.abort);
    const miss = await prepareFrameFull(S.idx, () => PNG.abort);
    if (PNG.abort) throw new Mp4Annullato();
    bar.style.width = '60%'; msg.textContent = 'Compongo l’immagine…';
    setFrame(S.idx); await nextPaint();
    const cv = composeCanvas(0); assertReadable(cv);
    const blob = await new Promise((res, rej) => cv.toBlob(b => (b ? res(b) : rej(new Error('il browser non ha prodotto il PNG'))), 'image/png'));
    bar.style.width = '100%';
    const skip = miss + (cv._skipped || 0);
    msg.textContent = `Immagine pronta: ${cv.width} × ${cv.height} pixel.`;
    showExportResult(box, blob, exportName('png'), skip ? `${skip} tasselli non disponibili sono rimasti vuoti.` : '');
  } catch (err) {
    bar.style.width = '0';
    if (err instanceof Mp4Annullato) msg.textContent = 'Annullato.';
    else showExportError(msg, 'Creazione dell’immagine', err);
  } finally { PNG.busy = false; $('#pngGo').disabled = false; $('#pngStop').hidden = true; }
}
$('#btnPng').onclick = () => { openModal('#pngModal'); makePng(); };
$('#pngGo').onclick = () => makePng();
$('#pngStop').onclick = () => { PNG.abort = true; };

/* --- GIF --- */
$('#btnGif').onclick = () => {
  if (!gifBusy) {
    $('#gifProg').firstChild.style.width = '0'; $('#gifGo').disabled = false; clearExportResult($('#gifRes'));
    if (typeof GIF === 'undefined') { $('#gifMsg').innerHTML = '<span class="exp-err">Libreria GIF non caricata (cartella static/vendor): impossibile creare la GIF.</span>'; $('#gifGo').disabled = true; }
    else if (!S.frames.length) { $('#gifMsg').textContent = 'Nessun fotogramma da esportare.'; $('#gifGo').disabled = true; }
    else $('#gifMsg').textContent = `Fotogrammi da esportare: ${S.frames.length} (${fmtD(S.frames[0])} ${fmtT(S.frames[0])} → ${fmtT(S.frames[S.frames.length - 1])} UTC). Premi “Crea GIF”: qui sotto vedrai l’avanzamento e alla fine l’anteprima con i pulsanti per salvarla.`;
  }
  openModal('#gifModal');
};
let gifBusy = false, gifAbort = false, gifJob = null;
$('#gifToMp4').onclick = () => { if (gifBusy) return; closeModals(); $('#btnMp4').click(); };
$('#gifStop').onclick = () => { gifAbort = true; try { gifJob && gifJob.abort(); } catch { /* gia' ferma */ } };
$('#gifGo').onclick = async () => {
  if (gifBusy) return; gifBusy = true; gifAbort = false; pause();
  const keep = S.idx, n = S.frames.length, wSel = +$('#gifW').value, fps = +$('#gifFps').value, stamp = $('#gifStamp').checked;
  const size0 = map.getSize(), gW = even(wSel || size0.x - 1), gH = even(gW * size0.y / size0.x);
  const bar = $('#gifProg').firstChild, msg = $('#gifMsg'), box = $('#gifRes');
  clearExportResult(box);
  $('#gifGo').disabled = true; $('#gifStop').hidden = false; $('#gifClose').disabled = true;
  const t0 = performance.now(); let miss = 0, keepView = null;
  try {
    // come per l'MP4: mappa renderizzata fuori schermo esattamente alla larghezza scelta, tasselli 1:1
    msg.textContent = 'Preparo la mappa alla dimensione della GIF…';
    keepView = mp4Enter(exportGeometry(gW, gH));
    await waitTiles(30000, () => gifAbort); mp4Reserve();
    const gif = gifJob = new GIF({ workers: 2, quality: 6, workerScript: 'vendor/gif.worker.js', width: gW, height: gH });
    for (let i = 0; i < n; i++) {
      if (gifAbort) throw new Mp4Annullato();
      msg.textContent = progressText('Raccolgo il fotogramma', i + 1, n, t0) + '…';
      miss += await prepareFrameFull(i, () => gifAbort);
      if (gifAbort) throw new Mp4Annullato();
      setFrame(i); await nextPaint();
      const cv = composeCanvas(0, stamp, { W: gW, H: gH }); assertReadable(cv); miss += cv._skipped || 0;
      gif.addFrame(cv, { delay: i === n - 1 ? 1200 : Math.round(1000 / fps), copy: true });
      bar.style.width = `${((i + 1) / n) * 50}%`;
    }
    const t1 = performance.now();
    msg.textContent = 'Codifico la GIF…';
    gif.on('progress', p => {
      bar.style.width = `${50 + p * 50}%`;
      const el = (performance.now() - t1) / 1000;
      msg.textContent = `Codifico la GIF: ${Math.round(p * 100)}%${p > 0.05 && p < 1 ? `, mancano circa ${fmtSec(el / p * (1 - p))}` : ''}…`;
    });
    const blob = await new Promise((res, rej) => { gif.on('finished', res); gif.on('abort', () => rej(new Mp4Annullato())); gif.render(); });
    bar.style.width = '100%';
    msg.textContent = `GIF pronta: ${n} fotogrammi, creata in ${fmtSec((performance.now() - t0) / 1000)}. Scegli dove salvarla.`;
    showExportResult(box, blob, exportName('gif', S.frames[n - 1] || frameTime()), miss ? `${miss} tasselli non disponibili sono rimasti vuoti.` : '');
    toast('GIF pronta: scegli “Salva con nome…” o “Scarica”.', 'ok', 3500);
  } catch (err) {
    bar.style.width = '0';
    if (err instanceof Mp4Annullato || gifAbort) msg.textContent = 'Esportazione GIF annullata.';
    else showExportError(msg, 'Esportazione GIF', err);
  } finally {
    gifBusy = false; gifJob = null; $('#gifGo').disabled = false; $('#gifStop').hidden = true; $('#gifClose').disabled = false;
    if (keepView) mp4Leave(keepView); else setFrame(keep);
  }
};

/* =========================================================================
 * 16b. Esportazione video MP4 (H.264)
 * Metodo 1: WebCodecs VideoEncoder (avc1) + mp4-muxer nel browser, scrittura a blocchi.
 * Metodo 2 (riserva): fotogrammi JPEG al server, montaggio con ffmpeg se installato.
 * Metodo 3 (ultima risorsa): WebM/VP9 con MediaRecorder (non MP4, segnalato).
 * Stessa composizione di PNG e GIF (composeCanvas): layer, palette, confronto, eventi.
 * ========================================================================= */
const MP4 = { busy: false, abort: false, method: null, hw: false, cfg: null, ffmpeg: null, enc: null, rec: null };
const MP4_RES = { vista: null, 720: [1280, 720], 1080: [1920, 1080], 1440: [2560, 1440], 2160: [3840, 2160] };
/* Mbit/s a 1080p per preset; le altre risoluzioni scalano con i pixel^0.75 (4K Alta ~ 57 Mbit/s) */
const MP4_MBPS = { standard: 12, alta: 20, massima: 30 };
const MP4_MAX_FRAMES = 3600, MP4_MAX_RAM = 1536 * 1048576, MP4_WARN_RAM = 400 * 1048576;
const even = v => Math.max(2, Math.round(v / 2) * 2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
class Mp4Annullato extends Error {}

/* livello H.264 minimo per dimensione, frequenza e bitrate (tabella A-1; MaxBR profilo High = base x 1,25) */
function avcLevel(w, h, fps, bitrate = 0) {
  const fs = Math.ceil(w / 16) * Math.ceil(h / 16), mbps = fs * fps, kbps = bitrate / 1000;
  for (const [lv, maxFs, maxMbps, maxBr] of [[0x1f, 3600, 108000, 14000], [0x20, 5120, 216000, 20000], [0x28, 8192, 245760, 20000], [0x29, 8192, 245760, 50000], [0x2a, 8704, 522240, 50000], [0x32, 22080, 589824, 135000], [0x33, 36864, 983040, 240000], [0x34, 36864, 2073600, 240000]])
    if (fs <= maxFs && mbps <= maxMbps && kbps <= maxBr * 1.25) return lv;
  return null;
}
function mp4Bitrate(w, h, fps, q) {
  const px = (w * h) / (1920 * 1080);
  const fpsF = fps >= 20 ? 1 : fps >= 10 ? 0.85 : 0.7; // pochi fotogrammi al secondo: meno bit al secondo, stessa qualita' per fotogramma
  return Math.round(clamp((MP4_MBPS[q] || MP4_MBPS.alta) * 1e6 * Math.pow(px, 0.75) * fpsF, 4e6, 80e6));
}
/* Prima configurazione H.264 accettata dal browser (profilo High, poi Main, poi Baseline).
 * Ordine provato: 1) codificatore software (OpenH264) a bitrate costante: rispetta davvero il bitrate;
 * 2) GPU a quantizzatore fisso (provato su questo PC: in modalita' bitrate la GPU ignora la richiesta e
 *    produce ~1 Mbit/s a 1080p, immagine a blocchi); 3) scelta libera del browser. */
const MP4_QP = { standard: 24, alta: 20, massima: 17 };
async function findH264(w, h, fps, bitrate) {
  if (typeof VideoEncoder === 'undefined' || typeof VideoFrame === 'undefined' || typeof Mp4Muxer === 'undefined') return null;
  const lv = avcLevel(w, h, fps, bitrate); if (!lv) return null;
  const hx = lv.toString(16).padStart(2, '0');
  const modes = [
    { hardwareAcceleration: 'prefer-software', bitrateMode: 'constant', tipo: 'software' },
    { hardwareAcceleration: 'prefer-hardware', bitrateMode: 'quantizer', tipo: 'gpu-qp' },
    { bitrateMode: 'constant', tipo: 'auto' },
  ];
  for (const { tipo, ...mode } of modes) for (const prof of ['6400', '4d00', '42e0']) {
    const cfg = { codec: `avc1.${prof}${hx}`, width: w, height: h, bitrate, framerate: fps, avc: { format: 'avc' }, latencyMode: 'quality', ...mode };
    try {
      if (!(await VideoEncoder.isConfigSupported(cfg)).supported) continue;
      return { cfg, tipo, hw: tipo === 'gpu-qp', profilo: { '6400': 'High', '4d00': 'Main', '42e0': 'Baseline' }[prof], livello: (lv / 10).toFixed(1).replace('.', ',') };
    } catch { /* configurazione rifiutata: prova la successiva */ }
  }
  return null;
}
async function ffmpegOk() {
  if (MP4.ffmpeg !== null) return MP4.ffmpeg;
  if (S.mode !== 'api') return (MP4.ffmpeg = false);
  try { MP4.ffmpeg = !!(await getJSON('/api/video/stato', 5000)).ffmpeg; } catch { MP4.ffmpeg = false; }
  return MP4.ffmpeg;
}
const webmOk = () => typeof MediaRecorder !== 'undefined' && typeof HTMLCanvasElement.prototype.captureStream === 'function' && MediaRecorder.isTypeSupported('video/webm;codecs=vp9');

/* Geometria di rendering: la mappa viene ridimensionata ESATTAMENTE a W x H pixel (fuori schermo) e
 * portata al livello di zoom adatto a quella risoluzione, cosi' ogni pixel del video e' un pixel vero
 * del tassello: nessun ridimensionamento del fotogramma. Oltre lo zoom nativo i tasselli si ingrandiscono a pixel netti. */
function exportGeometry(W, H) {
  const size = map.getSize(), z0 = map.getZoom();
  const zNat = Math.max(...S.stack.filter(e => e.vis && !cat(e.id).isRef).map(e => cat(e.id).maxZoom || 0), S.cmp.on && S.cmp.id ? cat(S.cmp.id)?.maxZoom || 0 : 0, 0);
  const dz = clamp(Math.round(Math.log2(W / size.x)), map.getMinZoom() - z0, map.getMaxZoom() - z0);
  return { W, H, cw: W, ch: H, dz, zoom: z0 + dz, oltreNativo: zNat > 0 && z0 + dz > zNat, area: (W * H) / (size.x * size.y) / Math.pow(4, dz) };
}
function mp4Geometry(res) {
  const size = map.getSize();
  if (!MP4_RES[res]) return exportGeometry(even(size.x - 1), even(size.y - 1));
  return exportGeometry(...MP4_RES[res]);
}
function mp4Frames() {
  const a = +$('#mp4From').value || 0, b = +$('#mp4To').value, st = +$('#mp4Step').value || 1;
  const lo = Math.min(a, b), hi = Math.max(a, b), out = [];
  for (let i = lo; i <= hi; i += st) out.push(i);
  if (out[out.length - 1] !== hi) out.push(hi);
  return out;
}
function mp4Plan() {
  const fps = clamp(+$('#mp4Fps').value || 8, 4, 30), rep = clamp(+$('#mp4Rep').value || 1, 1, 10);
  const pauseN = Math.round((+$('#mp4Pause').value || 0) * fps), idxs = mp4Frames();
  const perPass = idxs.length + pauseN, total = perPass * rep;
  const g = mp4Geometry($('#mp4Res').value), q = $('#mp4Q').value;
  const bitrate = mp4Bitrate(g.W, g.H, fps, q), dur = total / fps;
  return { fps, rep, pauseN, idxs, perPass, total, g, q, bitrate, dur, estB: bitrate * dur / 8 * 1.05 };
}
function mp4FileName(ext = 'mp4') {
  const idxs = mp4Frames();
  return exportName(ext, S.frames[idxs[idxs.length - 1]] || frameTime());
}
function sourcesFor(ids) {
  const s = new Set();
  for (const id of ids) {
    const Lr = cat(id); if (!Lr || Lr.isRef) continue;
    if (Lr.nativo) s.add('NOAA/JMA originali'); else if (/^(MTG|MSG|Metop|GeoRing)/i.test(id) || /eumetsat/i.test(Lr.grpNome || '')) s.add('EUMETSAT'); else s.add('NASA GIBS');
  }
  return [...s].join(' · ') || 'NASA GIBS';
}

/* --- sovrimpressione del video: data/ora, satellite/layer, legenda, scala, logo --- */
function legendFor() {
  const sel = S.stack.find(e => e.id === S.sel && e.vis), cands = [sel, ...S.stack.filter(e => e.vis)].filter(Boolean);
  for (const e of cands) {
    const Lr = cat(e.id); if (!Lr || Lr.isRef) continue;
    if (e.pal.id !== 'orig') {
      const dec = decoderFor(Lr), lut = getLUT(e.pal);
      return { kind: 'lut', lut, a: dec ? e.pal.max : e.pal.min, b: dec ? e.pal.min : e.pal.max, u: dec ? '°C' : '', titolo: `${Lr.nome}: ${palById(e.pal.id).nome}` };
    }
    const sc = accOn(Lr) ? Lr.scalaAccumulo : Lr.scala;
    if (sc && sc.voci && sc.voci.length) return { kind: 'scala', sc, titolo: sc.titolo || Lr.nome };
    const d = legOf(Lr);
    if (d && d.voci.length) return { kind: 'leg', d, titolo: Lr.nome };
  }
  return null;
}
/* riquadro legenda su canvas (PNG, GIF, MP4): angolo in basso a destra con bordo destro xr e fondo yb; ritorna l'altezza */
function drawLegCanvas(ctx, lg, xr, yb, u) {
  const F = (w, px) => `${w} ${Math.round(px * u)}px Archivo, "Segoe UI", sans-serif`;
  const box = (x, y, w, h) => { ctx.fillStyle = 'rgba(6,16,26,.74)'; ctx.fillRect(x, y, w, h); };
  const lw = Math.round(300 * u), bh2 = 12 * u, x = xr - lw;
  ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left';
  const bar = lg.kind === 'leg' ? legBar(lg.d) : null;
  if (lg.kind === 'leg' && !bar) {
    // composizioni RGB e colori reali: griglia di voci (colore + nome)
    const vs = lg.d.voci.slice(0, 10), cols = vs.length > 5 ? 2 : 1, rows = Math.ceil(vs.length / cols), rh = 16 * u, cw = lw / cols;
    const hh = 30 * u + rows * rh, y = yb - hh;
    box(x - 10 * u, y, lw + 20 * u, hh);
    ctx.font = F(500, 11.5); ctx.fillStyle = '#a3b6c9'; ctx.fillText(lg.titolo.slice(0, 48), x, y + 17 * u);
    vs.forEach((v, i) => {
      const cx = x + Math.floor(i / rows) * cw, cy = y + 26 * u + (i % rows) * rh;
      ctx.fillStyle = v.c; ctx.fillRect(cx, cy, 11 * u, 11 * u);
      ctx.strokeStyle = 'rgba(228,237,246,.35)'; ctx.lineWidth = Math.max(1, u); ctx.strokeRect(cx, cy, 11 * u, 11 * u);
      ctx.font = F(500, 10.5); ctx.fillStyle = '#e4edf6';
      let s = v.nome; while (s.length > 4 && ctx.measureText(s).width > cw - 20 * u) s = s.slice(0, -2);
      ctx.fillText(s === v.nome ? s : s + '…', cx + 16 * u, cy + 9.5 * u);
    });
    return hh;
  }
  const hh = 58 * u, y = yb - hh;
  box(x - 10 * u, y, lw + 20 * u, hh);
  const unita = lg.kind === 'scala' ? lg.sc.unita : lg.kind === 'leg' ? lg.d.unita : '';
  ctx.font = F(500, 11.5); ctx.fillStyle = '#a3b6c9'; ctx.fillText(lg.titolo.slice(0, 48) + (unita ? `  (${unita})` : ''), x, y + 17 * u);
  const by = y + 24 * u;
  if (lg.kind === 'lut') {
    for (let i = 0; i < lw; i++) { const j = Math.round(i / (lw - 1) * 255) * 3; ctx.fillStyle = `rgb(${lg.lut[j]},${lg.lut[j + 1]},${lg.lut[j + 2]})`; ctx.fillRect(x + i, by, 1.5, bh2); }
    ctx.font = F(500, 11); ctx.fillStyle = '#e4edf6';
    [0, 0.5, 1].forEach(f => { ctx.textAlign = f === 0 ? 'left' : f === 1 ? 'right' : 'center'; ctx.fillText(`${Math.round(lg.a + (lg.b - lg.a) * f)}${lg.u}`, x + lw * f, by + bh2 + 15 * u); });
  } else if (lg.kind === 'scala') {
    const v = lg.sc.voci, n = v.length;
    if (lg.sc.continua) { const gr = ctx.createLinearGradient(x, 0, x + lw, 0); v.forEach((c, i) => gr.addColorStop(n > 1 ? i / (n - 1) : 0, c[2])); ctx.fillStyle = gr; ctx.fillRect(x, by, lw, bh2); }
    else v.forEach((c, i) => { ctx.fillStyle = c[2]; ctx.fillRect(x + (i / n) * lw, by, lw / n + 0.5, bh2); });
    ctx.font = F(500, 11); ctx.fillStyle = '#e4edf6';
    const open = v[n - 1][1] == null;
    [[0, 'left', fmtV(v[0][0])], [0.5, 'center', fmtV(v[Math.floor(n / 2)][0])], [1, 'right', open ? `${fmtV(v[n - 1][0])}+` : fmtV(v[n - 1][1])]]
      .forEach(([f, al, s]) => { ctx.textAlign = al; ctx.fillText(s, x + lw * f, by + bh2 + 15 * u); });
  } else {
    if (bar.classi) bar.blocks.forEach((c, i, a) => { ctx.fillStyle = c; ctx.fillRect(x + (i / a.length) * lw, by, lw / a.length + 0.5, bh2); });
    else { const gr = ctx.createLinearGradient(x, 0, x + lw, 0); bar.stops.forEach(([p, c]) => gr.addColorStop(p, c)); ctx.fillStyle = gr; ctx.fillRect(x, by, lw, bh2); }
    ctx.font = F(500, 10.5); ctx.fillStyle = '#e4edf6';
    let lastR = -1e9;
    bar.ticks.forEach(([p, s]) => {
      const tx = x + lw * p, w = ctx.measureText(s).width;
      const al = p < 0.06 ? 'left' : p > 0.94 ? 'right' : 'center', l = al === 'left' ? tx : al === 'right' ? tx - w : tx - w / 2;
      if (l < lastR + 6 * u) return; // niente etichette sovrapposte
      ctx.fillRect(tx - 0.5, by + bh2, Math.max(1, u), 3 * u);
      ctx.textAlign = al; ctx.fillText(s, tx, by + bh2 + 15 * u); lastR = l + w;
    });
  }
  ctx.textAlign = 'left';
  return hh;
}
function niceKm(maxKm) {
  const p = Math.pow(10, Math.floor(Math.log10(maxKm)));
  for (const m of [5, 2, 1]) if (m * p <= maxKm) return m * p;
  return p;
}
function drawVideoOverlay(ctx, W, H, opt) {
  const u = clamp(H / 900, 0.75, 2.6), F = (w, px) => `${w} ${Math.round(px * u)}px Archivo, "Segoe UI", sans-serif`;
  const t = frameTime(), m = Math.round(16 * u);
  ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'left'; ctx.globalAlpha = 1;
  const box = (x, y, w, h) => { ctx.fillStyle = 'rgba(6,16,26,.74)'; ctx.fillRect(x, y, w, h); };
  if (opt.stamp) {
    const vis = S.stack.filter(e => e.vis && !cat(e.id).isRef);
    const it = itParts(t), itTxt = `${it.day}-${it.month}-${it.year}` === fmtD(t) ? `${it.hour}:${it.minute}` : `${it.day}-${it.month}-${it.year} ${it.hour}:${it.minute}`;
    const l1 = `${fmtD(t)}   ${fmtT(t)} UTC`, l1b = `Italia ${itTxt}`;
    const names = vis.map(e => cat(e.id).nome).slice(0, 3).join(' + ') || 'Mappa';
    const sats = [...new Set(vis.map(e => cat(e.id).sat))].slice(0, 3).join(', ');
    const lines = [[F(600, 22), '#e4edf6', l1], [F(400, 13), '#a3b6c9', `${names}${sats ? `  ·  ${sats}` : ''}`]];
    if (S.cmp.on && S.cmp.id) {
      const off = S.cmp.off ? `, ${S.cmp.off > 0 ? '+' : '−'}${num(Math.abs(S.cmp.off) / 60, Math.abs(S.cmp.off) % 60 ? 1 : 0)} h` : '';
      lines.push([F(400, 12), '#62e3d8', `Confronto a tendina, a destra: ${cat(S.cmp.id)?.nome || S.cmp.id}${off}`]);
    }
    ctx.font = lines[0][0]; const w1 = ctx.measureText(l1).width;
    ctx.font = F(500, 15); const w1b = ctx.measureText(l1b).width;
    let bw = w1 + w1b + 26 * u;
    for (const [f, , s] of lines.slice(1)) { ctx.font = f; bw = Math.max(bw, ctx.measureText(s).width); }
    bw += 30 * u;
    const lh = [30, 20, 18].map(v => v * u), bh = 14 * u + lines.reduce((a, _, i) => a + lh[i], 0);
    const x = m, y = H - m - bh;
    box(x, y, bw, bh); ctx.fillStyle = '#62e3d8'; ctx.fillRect(x, y, Math.max(2, 3 * u), bh);
    let yy = y + 7 * u;
    lines.forEach(([f, c, s], i) => {
      yy += lh[i]; ctx.font = f; ctx.fillStyle = c; ctx.fillText(s, x + 15 * u, yy - 6 * u);
      if (i === 0) { ctx.font = F(500, 15); ctx.fillStyle = '#ffb547'; ctx.fillText(l1b, x + 15 * u + w1 + 26 * u, yy - 6 * u); }
    });
  }
  let rb = H - m; // bordo inferiore disponibile a destra
  if (opt.logo) {
    const src = `Dati: ${sourcesFor([...S.stack.filter(e => e.vis).map(e => e.id), ...(S.cmp.on && S.cmp.id ? [S.cmp.id] : [])])}`;
    ctx.font = F(500, 11); const sw = ctx.measureText(src).width;
    ctx.fillStyle = 'rgba(6,16,26,.6)'; ctx.fillRect(W - m - sw - 12 * u, rb - 15 * u, sw + 12 * u, 20 * u);
    ctx.textAlign = 'right'; ctx.fillStyle = 'rgba(228,237,246,.85)'; ctx.fillText(src, W - m - 6 * u, rb);
    rb -= 24 * u; ctx.textAlign = 'left';
    // logo in alto a sinistra: anello + nome su fondo scuro leggero (leggibile anche sopra le nubi)
    const r = 9 * u, cx = m + 10 * u + r, cy = m + 6 * u + r;
    ctx.font = F(650, 15); const lw0 = ctx.measureText('Meteo Sat').width;
    ctx.fillStyle = 'rgba(6,16,26,.55)'; ctx.fillRect(m, m, lw0 + r * 2 + 30 * u, r * 2 + 12 * u);
    ctx.globalAlpha = 0.9; ctx.lineWidth = Math.max(1.5, 2 * u); ctx.strokeStyle = '#62e3d8';
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.ellipse(cx, cy, r * 1.7, r * 0.55, -0.35, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#e4edf6'; ctx.font = F(650, 15); ctx.fillText('Meteo Sat', cx + r * 2 + 4 * u, cy + 5 * u);
    ctx.globalAlpha = 1;
  }
  if (opt.legend) {
    // scala delle distanze al centro della mappa
    const size = map.getSize(), lat = map.getCenter().lat;
    const kmPerPx = 40075.016686 * Math.cos(lat * Math.PI / 180) / (256 * Math.pow(2, map.getZoom())) * (size.x / W);
    const km = niceKm(kmPerPx * W * 0.16), len = km / kmPerPx;
    const yb = rb - 6 * u, xb = W - m - len;
    box(xb - 10 * u, yb - 24 * u, len + 20 * u, 32 * u);
    ctx.fillStyle = '#e4edf6'; ctx.fillRect(xb, yb - 3 * u, len, Math.max(2, 3 * u));
    ctx.fillRect(xb, yb - 9 * u, Math.max(1.5, 2 * u), 9 * u); ctx.fillRect(xb + len - Math.max(1.5, 2 * u), yb - 9 * u, Math.max(1.5, 2 * u), 9 * u);
    ctx.font = F(500, 12); ctx.textAlign = 'center'; ctx.fillText(`${km.toLocaleString('it-IT')} km`, xb + len / 2, yb - 11 * u); ctx.textAlign = 'left';
    rb = yb - 32 * u;
    const lg = legendFor();
    if (lg) drawLegCanvas(ctx, lg, W - m, rb, u);
  }
}

/* --- destinazioni dei dati MP4: memoria a blocchi (Blob finale) o file su disco --- */
function memSink() {
  const parts = []; let size = 0;
  return {
    write(data, pos) {
      const end = pos + data.byteLength; let cur = pos; const add = [];
      for (const p of parts) {
        const pe = p.pos + p.buf.byteLength;
        if (pe <= cur || p.pos >= end) continue;
        if (p.pos > cur) add.push({ pos: cur, buf: data.slice(cur - pos, p.pos - pos) });
        const a = Math.max(cur, p.pos), b = Math.min(end, pe);
        p.buf.set(data.subarray(a - pos, b - pos), a - p.pos); cur = b;
      }
      if (cur < end) add.push({ pos: cur, buf: data.slice(cur - pos, end - pos) });
      if (add.length) { parts.push(...add); parts.sort((x, y) => x.pos - y.pos); }
      size = Math.max(size, end);
    },
    get size() { return size; },
    async finish() {
      // i buchi sono lo spazio riservato al moov (box 'free'): su disco sarebbero zeri, qui li riempio
      let at = 0; const out = [];
      for (const p of parts) {
        if (p.pos < at) throw new Error('file MP4 incoerente');
        if (p.pos > at) out.push(new Uint8Array(p.pos - at));
        out.push(p.buf); at = p.pos + p.buf.byteLength;
      }
      return new Blob(out, { type: 'video/mp4' });
    },
    abort() { parts.length = 0; },
  };
}
function diskSink(writable) {
  let chain = Promise.resolve(), size = 0, err = null;
  return {
    write(data, pos) {
      const d = data.slice(); size = Math.max(size, pos + d.byteLength);
      chain = chain.then(() => writable.write({ type: 'write', position: pos, data: d })).catch(e => { err = err || e; });
    },
    get size() { return size; },
    async finish() { await chain; if (err) throw err; await writable.close(); return null; },
    async abort() { try { await chain; await writable.abort(); } catch { /* gia' chiuso */ } },
  };
}

/* --- preparazione: mappa alla geometria del video, tasselli tutti arrivati --- */
const mapLayers = () => [...S.stack.filter(e => e.vis).map(e => e.layer), ...(S.cmp.layer ? [S.cmp.layer] : [])];
async function waitTiles(ms = 30000, aborted = () => MP4.abort) {
  await nextPaint();
  const t0 = performance.now();
  while (performance.now() - t0 < ms && mapLayers().some(l => l._map && l.isLoading && l.isLoading())) { if (aborted()) return; await sleep(150); }
  await nextPaint();
}
/* fotogramma pronto: tutte le tile decodificate; quelle non ancora pronte (server lento) si riprovano */
async function prepareFrameFull(i, aborted = () => MP4.abort) {
  let miss = 0;
  for (let k = 0; k < 6; k++) {
    const r = await prepareFrame(i);
    miss = r.filter(b => b === undefined).length;
    if (!miss || aborted()) return miss;
    await sleep(700 * (k + 1));
  }
  return miss;
}
function mp4Enter(g) {
  const el = map.getContainer();
  const keep = { center: map.getCenter(), zoom: map.getZoom(), idx: S.idx, maxBmps: TS.maxBmps, style: el.getAttribute('style') };
  // mappa esattamente W x H pixel CSS (anche oltre lo schermo) al livello di zoom della risoluzione scelta
  Object.assign(el.style, { width: `${g.cw}px`, height: `${g.ch}px`, right: 'auto', bottom: 'auto' });
  map.invalidateSize({ pan: false });
  map.setView(keep.center, keep.zoom + g.dz, { animate: false });
  return keep;
}
/* tutte le tile di un fotogramma devono restare in memoria fino alla cattura */
function mp4Reserve() {
  const tiles = mapLayers().reduce((a, l) => a + l.currentTiles().length, 0);
  TS.maxBmps = Math.max(TS.maxBmps, tiles * 3 + 200);
}
function mp4Leave(keep) {
  const el = map.getContainer();
  if (keep.style == null) el.removeAttribute('style'); else el.setAttribute('style', keep.style);
  map.invalidateSize({ pan: false });
  map.setView(keep.center, keep.zoom, { animate: false });
  TS.maxBmps = keep.maxBmps;
  setFrame(keep.idx);
}

/* --- interfaccia --- */
function mp4FillFrames() {
  const opts = S.frames.map((t, i) => `<option value="${i}">${i + 1}. ${fmtD(t)} ${fmtT(t)} UTC</option>`).join('');
  $('#mp4From').innerHTML = opts; $('#mp4To').innerHTML = opts;
  $('#mp4From').value = '0'; $('#mp4To').value = String(Math.max(0, S.frames.length - 1));
}
let mp4InfoGen = 0;
async function mp4UpdateInfo() {
  const gen = ++mp4InfoGen, p = mp4Plan();
  const info = $('#mp4Info'), warn = $('#mp4Warn'), go = $('#mp4Go');
  const h = await findH264(p.g.W, p.g.H, p.fps, p.bitrate);
  if (gen !== mp4InfoGen) return;
  let method, label;
  if (h) { method = 'webcodecs'; label = `H.264 profilo ${h.profilo} livello ${h.livello} nel browser (WebCodecs, ${h.tipo === 'software' ? 'codificatore software a bitrate costante' : h.tipo === 'gpu-qp' ? `GPU a qualità costante QP ${MP4_QP[p.q] || 20}: il bitrate reale dipende dall’immagine` : 'codificatore scelto dal browser'})`; }
  else if (await ffmpegOk()) { method = 'ffmpeg'; label = 'H.264 con ffmpeg sul server locale (il browser non codifica H.264 a questa risoluzione)'; }
  else if (webmOk()) { method = 'webm'; label = 'WebM/VP9 con MediaRecorder: questo browser non sa creare MP4 H.264 e ffmpeg non è installato'; }
  else { method = null; label = 'Nessun metodo di codifica video disponibile in questo browser'; }
  if (gen !== mp4InfoGen) return;
  MP4.method = method; MP4.cfg = h && h.cfg; MP4.hw = !!(h && h.hw); MP4.tipo = h && h.tipo;
  const durTxt = p.dur >= 60 ? `${Math.floor(p.dur / 60)} min ${Math.round(p.dur % 60)} s` : `${num(p.dur, 1)} s`;
  const mb = p.estB / 1048576;
  const zTxt = `tasselli allo zoom ${p.g.zoom}${p.g.dz ? ` (${p.g.dz > 0 ? '+' : ''}${p.g.dz} rispetto allo schermo)` : ''}, 1 pixel del video = 1 pixel del tassello${p.g.oltreNativo ? ', oltre la risoluzione nativa: ingrandito a pixel netti' : ''}`;
  const areaTxt = Math.abs(p.g.area - 1) > 0.15 ? `; area inquadrata ${num(p.g.area, 1)} volte quella dello schermo` : '';
  info.innerHTML = `Risoluzione finale <b>${p.g.W} × ${p.g.H}</b> · <b>${p.fps} fps</b> · bitrate <b>${num(p.bitrate / 1e6, 0)} Mbit/s</b> · keyframe ogni 2 s<br>${p.idxs.length} fotogrammi della timeline${p.rep > 1 ? ` × ${p.rep}` : ''}${p.pauseN ? ` + pausa` : ''} = <b>${p.total}</b> fotogrammi, durata <b>${durTxt}</b>, file stimato <b>${num(mb, mb < 10 ? 1 : 0)} MB</b><br>Nitidezza: ${zTxt}${areaTxt}.<br>Metodo: ${label}.<br>File: ${esc(mp4FileName(method === 'webm' ? 'webm' : 'mp4'))}`;
  const w = [];
  const res4k = $('#mp4Res').querySelector('option[value="2160"]');
  if ($('#mp4Res').value === '2160' && method !== 'webcodecs') w.push('Il 4K non è supportato dal codificatore H.264 di questo browser/GPU: scegli 1080p.');
  if (res4k) res4k.textContent = '4K (3840 × 2160)' + (method === 'webcodecs' || $('#mp4Res').value !== '2160' ? '' : ', non supportato qui');
  if (p.total > MP4_MAX_FRAMES) w.push(`Troppi fotogrammi (${p.total}, massimo ${MP4_MAX_FRAMES}): riduci ripetizioni, fps o usa “1 su 2”.`);
  const disk = $('#mp4Disk').checked && method === 'webcodecs';
  if (!disk && p.estB > MP4_MAX_RAM) w.push(`Video troppo grande per la memoria (circa ${num(mb, 0)} MB): spunta “Scrivi direttamente su disco” o abbassa risoluzione/qualità.`);
  else if (!disk && p.estB > MP4_WARN_RAM) w.push(`Video grande (circa ${num(mb, 0)} MB): conviene “Scrivi direttamente su disco”.`);
  if ($('#mp4Res').value === 'vista' && devicePixelRatio > 1) w.push(`Schermo ad alta densità (×${num(devicePixelRatio, 2)}): per un video più definito scegli 1080p, 1440p o 4K.`);
  if (p.g.W * p.g.H >= 3840 * 2160 && p.idxs.length > 60) w.push('4K con molti fotogrammi: servono parecchi minuti e molti tasselli da scaricare.');
  if (method === 'webm') w.push('Verrà creato un file WebM (si apre con Chrome, VLC, YouTube), non MP4.');
  warn.hidden = !w.length; warn.textContent = w.join(' ');
  $('#mp4DiskRow').hidden = !(window.showSaveFilePicker && method === 'webcodecs');
  const blocked = !method || p.total > MP4_MAX_FRAMES || (!disk && p.estB > MP4_MAX_RAM) || ($('#mp4Res').value === '2160' && method !== 'webcodecs');
  if (!MP4.busy) go.disabled = blocked || S.frames.length < 1;
}
$('#btnMp4').onclick = () => {
  if (!S.frames.length) { toast('Nessun fotogramma da esportare.', 'info'); return; }
  if (!MP4.busy) {
    mp4FillFrames();
    $('#mp4Prog').firstChild.style.width = '0'; $('#mp4Msg').innerHTML = '&nbsp;'; clearExportResult($('#mp4Res2'));
    if (S.frames.length < 2) $('#mp4Msg').textContent = 'Un solo fotogramma nell’intervallo: il video mostrerà un’immagine ferma.';
    mp4UpdateInfo();
  }
  openModal('#mp4Modal');
};
['#mp4Res', '#mp4Q', '#mp4Fps', '#mp4From', '#mp4To', '#mp4Step', '#mp4Rep', '#mp4Pause', '#mp4Disk'].forEach(s => $(s).addEventListener('change', () => mp4UpdateInfo()));
$('#mp4Stop').onclick = () => { if (MP4.busy) { MP4.abort = true; $('#mp4Msg').textContent = 'Annullo…'; } };

$('#mp4Go').onclick = async () => {
  if (MP4.busy || !MP4.method) return;
  const p = mp4Plan(), method = MP4.method, name = mp4FileName(method === 'webm' ? 'webm' : 'mp4');
  const ov = { stamp: $('#mp4Stamp').checked, legend: $('#mp4Legend').checked, logo: $('#mp4Logo').checked };
  // il selettore file va chiesto subito, dentro il clic
  let writable = null;
  if (method === 'webcodecs' && $('#mp4Disk').checked && window.showSaveFilePicker) {
    try {
      const fh = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: 'Video MP4', accept: { 'video/mp4': ['.mp4'] } }] });
      writable = await fh.createWritable();
    } catch { return; } // finestra chiusa: non fare nulla
  }
  MP4.busy = true; MP4.abort = false; pause(); clearExportResult($('#mp4Res2'));
  const bar = $('#mp4Prog').firstChild, msg = $('#mp4Msg'), go = $('#mp4Go');
  go.disabled = true; $('#mp4Stop').hidden = false; $('#mp4Close').disabled = true;
  $$('#mp4Modal select, #mp4Modal input').forEach(x => (x.disabled = true));
  const t0 = performance.now();
  let keep = null, missing = 0, result = null;
  try {
    msg.textContent = 'Preparo la mappa alla risoluzione del video…';
    keep = mp4Enter(p.g);
    await waitTiles(); mp4Reserve();
    const cv = document.createElement('canvas'); cv.width = p.g.W; cv.height = p.g.H;
    const draw = async (i) => {
      missing += await prepareFrameFull(i);
      if (MP4.abort) throw new Mp4Annullato();
      setFrame(i); await nextPaint();
      composeCanvas(0, false, { canvas: cv, W: p.g.W, H: p.g.H, overlay: (ctx, W, H) => drawVideoOverlay(ctx, W, H, ov) });
      missing += cv._skipped || 0;
      if (!checked) { assertReadable(cv); checked = true; }
    };
    let checked = false;
    const step = (k, n, phase) => { bar.style.width = `${(k / n) * 100}%`; msg.textContent = progressText(phase, k, n, t0) + '…'; };
    if (method === 'webcodecs') result = await mp4WebCodecs(p, cv, draw, step, writable, name);
    else if (method === 'ffmpeg') result = await mp4Ffmpeg(p, cv, draw, step, name);
    else result = await mp4Webm(p, cv, draw, step, name);
    const secs = (performance.now() - t0) / 1000;
    bar.style.width = '100%';
    const warnTxt = missing ? `${missing} tasselli non arrivati in tempo sono rimasti vuoti.` : '';
    msg.textContent = `${result.kind} pronto: ${p.g.W} × ${p.g.H}, ${p.total} fotogrammi, ${num(p.dur, 1)} s, creato in ${fmtSec(secs)}.${writable ? '' : ' Scegli dove salvarlo.'}`;
    if (result.blob) {
      showExportResult($('#mp4Res2'), result.blob, name, warnTxt);
      toast(`Video ${result.kind} pronto: scegli “Salva con nome…” o “Scarica”.`, 'ok', 3500);
    } else {
      const box = $('#mp4Res2'); clearExportResult(box);
      box.innerHTML = `<div class="exp-meta"><b>${esc(name)}</b><span>Video MP4, ${num(result.bytes / 1048576, 1)} MB</span><span class="exp-note">Già salvato nella cartella che hai scelto all’inizio.</span>${warnTxt ? `<span>${esc(warnTxt)}</span>` : ''}</div>`;
      box.hidden = false;
      toast(`Salvato: ${esc(name)}`, 'ok', 4500);
    }
  } catch (err) {
    if (writable) { try { await writable.abort(); } catch { /* gia' chiuso */ } }
    if (err instanceof Mp4Annullato || MP4.abort) { msg.textContent = 'Esportazione annullata.'; bar.style.width = '0'; }
    else { bar.style.width = '0'; showExportError(msg, 'Esportazione del video', err); }
  } finally {
    try { MP4.enc && MP4.enc.state !== 'closed' && MP4.enc.close(); } catch { /* chiuso */ }
    try { MP4.rec && MP4.rec.state !== 'inactive' && MP4.rec.stop(); } catch { /* fermo */ }
    MP4.enc = null; MP4.rec = null;
    if (keep) mp4Leave(keep);
    MP4.busy = false; MP4.abort = false;
    $('#mp4Stop').hidden = true; $('#mp4Close').disabled = false;
    $$('#mp4Modal select, #mp4Modal input').forEach(x => (x.disabled = false));
    mp4UpdateInfo();
  }
};

/* metodo 1: WebCodecs + mp4-muxer */
async function mp4WebCodecs(p, cv, draw, step, writable, name) {
  const sink = writable ? diskSink(writable) : memSink();
  const target = new Mp4Muxer.StreamTarget({ onData: (data, pos) => sink.write(data, pos), chunked: true, chunkSize: 8 * 1048576 });
  const muxer = new Mp4Muxer.Muxer({ target, video: { codec: 'avc', width: p.g.W, height: p.g.H, frameRate: p.fps }, fastStart: { expectedVideoChunks: p.total }, firstTimestampBehavior: 'offset' });
  const dur = 1e6 / p.fps, stored = [];
  let encErr = null;
  const enc = MP4.enc = new VideoEncoder({
    output: (chunk, meta) => {
      muxer.addVideoChunk(chunk, meta);
      if (p.rep > 1) { const d = new Uint8Array(chunk.byteLength); chunk.copyTo(d); stored.push({ d, type: chunk.type, ts: chunk.timestamp }); }
    },
    error: e => { encErr = e; },
  });
  enc.configure({ ...MP4.cfg, bitrate: p.bitrate, framerate: p.fps });
  const gop = Math.max(1, Math.round(p.fps * 2));
  for (let k = 0; k < p.perPass; k++) {
    if (MP4.abort) throw new Mp4Annullato();
    if (encErr) throw encErr;
    if (k < p.idxs.length) await draw(p.idxs[k]); // la pausa finale riusa l'ultimo fotogramma composto
    const vf = new VideoFrame(cv, { timestamp: Math.round(k * dur), duration: Math.round(dur) });
    enc.encode(vf, MP4.cfg.bitrateMode === 'quantizer' ? { keyFrame: k % gop === 0, avc: { quantizer: MP4_QP[p.q] || 20 } } : { keyFrame: k % gop === 0 }); vf.close();
    while (enc.encodeQueueSize > 3 && !encErr) await sleep(4);
    step(k + 1, p.perPass, 'Codifico il fotogramma');
  }
  await enc.flush();
  if (encErr) throw encErr;
  enc.close(); MP4.enc = null;
  // ripetizioni: stessi blocchi gia' codificati, tempi spostati (nessuna ricodifica)
  for (let r = 1; r < p.rep; r++) {
    const off = r * p.perPass * dur;
    for (const c of stored) muxer.addVideoChunkRaw(c.d, c.type, Math.round(c.ts + off), Math.round(dur));
    if (MP4.abort) throw new Mp4Annullato();
  }
  stored.length = 0;
  muxer.finalize();
  const blob = await sink.finish();
  return { kind: 'MP4', bytes: blob ? blob.size : sink.size, blob };
}

/* metodo 2: ffmpeg sul server locale */
async function mp4Ffmpeg(p, cv, draw, step, name) {
  const post = async (url, body) => { const r = await fetch(url, { method: 'POST', body }); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.errore || `errore ${r.status}`); return j; };
  const { id } = await post('/api/video/inizio');
  try {
    for (let k = 0; k < p.idxs.length; k++) {
      await draw(p.idxs[k]);
      const jpg = await new Promise(res => cv.toBlob(res, 'image/jpeg', p.q === 'massima' ? 0.97 : 0.94));
      await post(`/api/video/${id}/f/${k}`, jpg);
      step(k + 1, p.idxs.length, 'Invio il fotogramma');
    }
    $('#mp4Msg').textContent = 'Monto il video con ffmpeg…';
    const crf = { standard: 21, alta: 18, massima: 15 }[p.q] || 18;
    await post(`/api/video/${id}/fine?fps=${p.fps}&ripeti=${p.rep}&pausa=${p.pauseN / p.fps}&crf=${crf}`);
    const r = await fetch(`/api/video/${id}/file`); if (!r.ok) throw new Error('video non scaricabile');
    const blob = new Blob([await r.blob()], { type: 'video/mp4' });
    return { kind: 'MP4', bytes: blob.size, blob };
  } catch (e) { fetch(`/api/video/${id}/annulla`, { method: 'POST' }).catch(() => {}); throw e; }
}

/* metodo 3: WebM/VP9 con MediaRecorder (tempo reale) */
async function mp4Webm(p, cv, draw, step, name) {
  // la registrazione e' in tempo reale: prima scarico tutti i fotogrammi, poi li compongo al ritmo giusto
  for (let k = 0; k < p.idxs.length; k++) { await prepareFrameFull(p.idxs[k]); step(k + 1, p.idxs.length, 'Scarico il fotogramma'); if (MP4.abort) throw new Mp4Annullato(); }
  const ctx = cv.getContext('2d'); ctx.fillStyle = '#06101a'; ctx.fillRect(0, 0, cv.width, cv.height);
  const stream = cv.captureStream(0), track = stream.getVideoTracks()[0], parts = [];
  const rec = MP4.rec = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9', videoBitsPerSecond: p.bitrate });
  rec.ondataavailable = e => e.data.size && parts.push(e.data);
  const stopped = new Promise(r => (rec.onstop = r));
  rec.start(1000);
  const dur = 1000 / p.fps; let n = 0;
  for (let r = 0; r < p.rep; r++) for (let k = 0; k < p.perPass; k++) {
    if (MP4.abort) { rec.stop(); throw new Mp4Annullato(); }
    const t1 = performance.now();
    if (k < p.idxs.length) await draw(p.idxs[k]);
    track.requestFrame(); n++;
    step(n, p.total, 'Registro il fotogramma');
    await sleep(Math.max(0, dur - (performance.now() - t1)));
  }
  rec.stop(); await stopped; MP4.rec = null;
  const blob = new Blob(parts, { type: 'video/webm' });
  return { kind: 'WebM', bytes: blob.size, blob };
}

/* =========================================================================
 * 17. Link condivisibile (hash) e viste salvate
 * ========================================================================= */
function stateHash() {
  const c = map.getCenter(), p = new URLSearchParams();
  p.set('c', `${c.lat.toFixed(3)},${(((c.lng + 540) % 360) - 180).toFixed(3)},${map.getZoom()}`);
  p.set('l', S.stack.filter(e => !e.auto).map(e => [e.id, Math.round(e.op * 100), e.vis ? 1 : 0, e.pal.id, e.pal.b, e.pal.c, e.pal.g, e.pal.min, e.pal.max, e.pal.cut ? 1 : 0, e.fixT || '', e.blend || '', e.pal.key ? `${e.pal.kT}_${e.pal.kW}` : ''].join('~')).join(','));
  if (S.custom) p.set('r', `${isoZ(S.custom.from)}_${isoZ(S.custom.to)}`); else p.set('r', S.range);
  if (S.stepMin) p.set('s', S.stepMin);
  if (!S.live && S.frames[S.idx]) p.set('t', isoZ(S.frames[S.idx]));
  if (S.cmp.on) p.set('cmp', `${S.cmp.id}~${S.cmp.off}~${S.cmp.pos.toFixed(3)}`);
  if (S.sel) p.set('sel', S.sel);
  p.set('co', coastHash());
  return p.toString();
}
const scheduleHash = debounce(() => { if (!booted) return; if (gifBusy || MP4.busy) { scheduleHash(); return; } // durante le esportazioni la mappa e' temporanea
  history.replaceState(null, '', '#' + stateHash()); }, 400);
function applyHash(h) {
  const p = new URLSearchParams(h.replace(/^#/, '')); if (!p.has('l') && !p.has('c')) return false;
  pause();
  for (const e of [...S.stack]) removeLayer(e.id, true);
  let ls = (p.get('l') || '').split(',').filter(Boolean);
  // coste: link nuovi con co=...; link vecchi con il layer NASA Coastlines_15m -> nuova linea vettoriale
  if (p.get('co')) coastFromHash(p.get('co'));
  else if (ls.some(s => s.split('~')[0] === NASA_COAST)) { CO.on = true; CO.fonte = 'ne'; }
  if (CO.fonte === 'ne') ls = ls.filter(s => s.split('~')[0] !== NASA_COAST);
  if (!ls.some(s => cat(s.split('~')[0]))) defaultStack(); // link con sola posizione: livelli predefiniti
  ls.forEach((s, i) => {
    const [id, op, vis, pid, b, c, g, mn, mx, cut, fixT, blend, keyS] = s.split('~');
    if (!cat(id)) return;
    const pal = { id: allPals().some(x => x.id === pid) ? pid : 'orig' };
    if (b !== undefined) Object.assign(pal, { b: +b || 0, c: +c || 0, g: +g || 1, min: +mn, max: +mx, cut: cut === '1' });
    if (!isFinite(pal.min) || !isFinite(pal.max)) { delete pal.min; delete pal.max; }
    if (keyS && /^-?[\d.]+_[\d.]+$/.test(keyS)) { const [kt, kw] = keyS.split('_').map(Number); Object.assign(pal, { key: true, kT: kt, kW: kw }); }
    addLayer(id, { blend: BLENDS.some(x => x[0] === blend) ? blend : '', op: op ? +op / 100 : 1, vis: vis !== '0', pal, at: S.stack.length, fixT: /^\d{4}-\d\d-\d\d/.test(fixT || '') ? fixT : null }, true);
  });
  const r = p.get('r') || '180';
  if (r.includes('_')) { const [a, b] = r.split('_').map(parseTime); if (a && b) { S.custom = { from: a, to: b }; S.live = false; } }
  else { S.range = +r || 180; S.custom = null; }
  S.stepMin = +(p.get('s') || 0); $('#stepSel').value = String(S.stepMin);
  const t = parseTime(p.get('t'));
  if (t) S.live = false; else if (!S.custom) S.live = true;
  S.sel = p.get('sel') || null;
  const cmp = p.get('cmp');
  if (cmp) { const [id, off, pos] = cmp.split('~'); S.cmp.id = cat(id) ? id : null; S.cmp.off = +off || 0; S.cmp.pos = clamp(+pos || 0.5, 0.02, 0.98); }
  const c = (p.get('c') || '').split(',').map(Number);
  if (c.length === 3 && c.every(isFinite)) map.setView([c[0], c[1]], c[2], { animate: false });
  S.frames = t ? [t] : S.frames; S.idx = 0;
  coastApply(true);
  afterStackChange(!!t);
  rangeUI();
  setCompare(!!cmp && !!S.cmp.id);
  return true;
}
$('#btnLink').onclick = () => {
  history.replaceState(null, '', '#' + stateHash());
  navigator.clipboard?.writeText(location.href).then(() => toast('Link della vista copiato: chi lo apre vede la stessa mappa.', 'ok', 2600), () => toast('Copia non riuscita: usa l’indirizzo nella barra del browser.', 'err'));
};
let views = store_.get('meteoSat.viste', []);
function renderViews() {
  $('#viewList').innerHTML = views.length ? views.map((v, i) => `<div class="view-item" data-i="${i}">
      <span class="t" data-a="go" role="button" tabindex="0">${esc(v.nome)}<small>Salvata il ${fmtD(new Date(v.d))}</small></span>
      <button class="ibtn" data-a="link" title="Copia link">${icon('link')}</button>
      <button class="ibtn" data-a="del" title="Elimina vista">${icon('trash')}</button></div>`).join('')
    : '<div class="empty">Nessuna vista salvata. Dai un nome alla vista attuale e premi Salva.</div>';
}
$('#viewForm').addEventListener('submit', ev => {
  ev.preventDefault(); const nome = $('#viewName').value.trim(); if (!nome) return;
  views.unshift({ nome, h: stateHash(), d: Date.now() }); views = views.slice(0, 50); store_.set('meteoSat.viste', views);
  $('#viewName').value = ''; renderViews(); toast(`Vista “${esc(nome)}” salvata.`, 'ok', 2000);
});
$('#viewList').addEventListener('click', ev => {
  const b = ev.target.closest('[data-a]'); if (!b) return; const i = +b.closest('.view-item').dataset.i, v = views[i];
  if (b.dataset.a === 'go') { applyHash(v.h); history.replaceState(null, '', '#' + v.h); toast(`Vista “${esc(v.nome)}” caricata.`, 'ok', 1800); }
  if (b.dataset.a === 'del') { views.splice(i, 1); store_.set('meteoSat.viste', views); renderViews(); }
  if (b.dataset.a === 'link') { const u = location.href.split('#')[0] + '#' + v.h; navigator.clipboard?.writeText(u).then(() => toast('Link copiato.', 'ok', 1500)); }
});
$('#viewList').addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.dataset.a === 'go') ev.target.click(); });
window.addEventListener('hashchange', () => { if (location.hash.slice(1) !== stateHash()) applyHash(location.hash); });

/* =========================================================================
 * 18. Diretta: ultimi orari ogni 10 minuti
 * ========================================================================= */
async function refreshLatest(force) {
  const ids = [...new Set(drivers().filter(Lr => Lr.tempoTipo === 'datetime').map(Lr => Lr.id))];
  if (S.cmp.on && cat(S.cmp.id)?.tempoTipo === 'datetime') ids.push(S.cmp.id);
  if (S.mode === 'api') {
    await Promise.all(ids.map(async id => {
      try {
        const r = await getJSON(`/api/latest/${encodeURIComponent(id)}`, 12000);
        const s = typeof r === 'string' ? r : r.latest || r.time || r.ultimo || r.maxTime || Object.values(r).find(v => typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v));
        const d = parseTime(s); const Lr = cat(id);
        if (d && Lr && (!Lr.max || +d !== +Lr.max)) Lr.max = d;
      } catch { /* resta l'ultimo orario noto */ }
    }));
  }
  S.lastUpd = new Date();
  $('#stUpd').textContent = fmtItT(S.lastUpd);
  if (S.live || force) { computeFrames(!S.live); schedulePreload(); }
}
setInterval(() => {
  if (gifBusy || MP4.busy) return; // durante un'esportazione la timeline non deve cambiare
  if (!S.lastUpd || Date.now() - S.lastUpd >= 10 * MIN) { if (S.live) refreshLatest(); loadEvents(); if (!S.live) { S.lastUpd = new Date(); $('#stUpd').textContent = fmtItT(S.lastUpd); } }
}, 30000);

/* =========================================================================
 * 19. Pannelli, finestre, tastiera
 * ========================================================================= */
const mobile = () => matchMedia('(max-width: 760px)').matches;
function togglePanel(side, force) {
  const p = side === 'left' ? $('#leftPanel') : $('#rightPanel');
  const show = force ?? p.classList.contains('hidden');
  p.classList.toggle('hidden', !show);
  $(side === 'left' ? '#btnLayers' : '#btnEvents').setAttribute('aria-pressed', String(show));
  if (show && mobile()) togglePanel(side === 'left' ? 'right' : 'left', false);
}
function showTab(id) {
  togglePanel('left', true);
  $$('.tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === id)));
  $$('.tabpane').forEach(p => (p.hidden = p.id !== id));
}
$$('.tab').forEach(t => (t.onclick = () => showTab(t.dataset.tab)));
$('#btnLayers').onclick = () => togglePanel('left');
$('#btnEvents').onclick = () => togglePanel('right');
$('#evClose').onclick = () => togglePanel('right', false);
$('#btnCompare').onclick = () => { setCompare(!S.cmp.on); if (S.cmp.on) showTab('tCompare'); };
$('#btnFull').onclick = () => { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.().catch(() => toast('Schermo intero non consentito dal browser.', 'err')); };
$('#btnHelp').onclick = () => openModal('#helpModal');
let lastFocus = null;
function openModal(sel) { lastFocus = document.activeElement; const m = $(sel); m.classList.add('open'); setTimeout(() => (m.querySelector('select, input, button.primary') || m.querySelector('button'))?.focus(), 30); }
function closeModals() { if (gifBusy || MP4.busy || PNG.busy) return; $$('.modal-bg.open').forEach(m => m.classList.remove('open')); lastFocus?.focus?.(); }
$$('.modal-bg').forEach(m => m.addEventListener('click', ev => { if (ev.target === m || ev.target.closest('[data-close]')) closeModals(); }));
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') { closeModals(); $('#searchResults').classList.remove('open'); return; }
  if (ev.target.closest('input, select, textarea') || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if ($('.modal-bg.open')) return;
  const k = ev.key;
  if (k === ' ') { ev.preventDefault(); S.playing ? pause() : play(); }
  else if (k === 'ArrowLeft') { ev.preventDefault(); pause(); setFrame(S.idx - 1); }
  else if (k === 'ArrowRight') { ev.preventDefault(); pause(); setFrame(S.idx + 1); }
  else if (k === 'Home') { ev.preventDefault(); pause(); setFrame(0); }
  else if (k === 'End') { ev.preventDefault(); pause(); setFrame(S.frames.length - 1); }
  else if (k === 'l' || k === 'L') togglePanel('left');
  else if (k === 'a' || k === 'A') togglePanel('right');
  else if (k === 'c' || k === 'C') $('#btnCompare').click();
  else if (k === 'f' || k === 'F') $('#btnFull').click();
  else if (k === 'h' || k === 'H') altaRisoluzioneQui();
  else if (k === '?') openModal('#helpModal');
});

/* =========================================================================
 * 19b. Coste, laghi, fiumi e confini (Natural Earth 1:10m, vettoriale)
 *  Linee disegnate su una tela propria SEMPRE SOPRA tutti i satelliti (pannello 'coste', z-index 450),
 *  nitide a ogni zoom, indipendenti dal caricamento dei tasselli. Dati in static/data (pubblico dominio,
 *  scaricati una volta: funzionano offline). La vecchia linea NASA (Coastlines_15m) resta come opzione.
 * ========================================================================= */
const COAST_SETS = [
  { id: 'coste', file: 'ne_coste', nome: 'Coste e isole', k: 1, dash: null },
  { id: 'laghi', file: 'ne_laghi', nome: 'Laghi', k: 0.85, dash: null },
  { id: 'fiumi', file: 'ne_fiumi', nome: 'Fiumi principali', k: 0.7, dash: null, op: 0.8 },
  { id: 'confini', file: 'ne_confini', nome: 'Confini di stato', k: 0.9, dash: [7, 3] },
  { id: 'regioni', file: 'ne_regioni', nome: 'Confini regionali', k: 0.6, dash: [3, 3], op: 0.75 },
];
const COAST_PRESETS = [['Bianco', '#ffffff'], ['Giallo', '#ffd84a'], ['Nero', '#101418'], ['Arancio', '#ff9a3c']];
const NASA_COAST = 'Coastlines_15m';
const CO = Object.assign({ on: true, fonte: 'ne', w: 1.3, col: '#ffffff', op: 0.9, alone: true,
  sets: { coste: true, laghi: true, fiumi: false, confini: true, regioni: false } }, store_.get('meteoSat.coste', {}));
CO.sets = Object.assign({ coste: true, laghi: true, fiumi: false, confini: true, regioni: false }, CO.sets || {});
const coastData = new Map(); // id -> {state:'loading'|'ok'|'err', lines:[{mz, bb:[x0,y0,x1,y1], p:Float32Array}]}
function coastSave() { store_.set('meteoSat.coste', CO); }
/* lon/lat -> pixel mondo a zoom 0 (256 px), come map.project(...,0) */
function coastLoad(set) {
  const d = coastData.get(set.id);
  if (d) return d.state === 'ok' ? Promise.resolve(d) : d.p || Promise.resolve(d);
  const rec = { state: 'loading', lines: [] };
  rec.p = fetch(`data/${set.file}.json`).then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(js => {
    const q = js.q || 10000, D = Math.PI / 180, LIM = 85.0511;
    for (const f of js.f) {
      const m = (f.length - 1) >> 1, p = new Float32Array(m * 2);
      let x = 0, y = 0, x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < m; i++) {
        x += f[1 + i * 2]; y += f[2 + i * 2];
        const lon = x / q, lat = clamp(y / q, -LIM, LIM);
        const X = (lon + 180) / 360 * 256, Y = (1 - Math.log(Math.tan(Math.PI / 4 + lat * D / 2)) / Math.PI) / 2 * 256;
        p[i * 2] = X; p[i * 2 + 1] = Y;
        if (X < x0) x0 = X; if (X > x1) x1 = X; if (Y < y0) y0 = Y; if (Y > y1) y1 = Y;
      }
      rec.lines.push({ mz: f[0] || 0, bb: [x0, y0, x1, y1], p });
    }
    rec.state = 'ok'; delete rec.p; return rec;
  }).catch(() => { rec.state = 'err'; delete rec.p; toast(`${set.nome}: dati non disponibili (static/data/${set.file}.json).`, 'err'); return rec; });
  coastData.set(set.id, rec);
  return rec.p;
}
const coastVectorOn = () => CO.on && CO.fonte === 'ne';
/* disegna le linee: punto (X,Y) a zoom 0 -> (X*s - ox, Y*s - oy); [vx0,vy0,vx1,vy1] = area visibile nelle stesse coordinate */
function drawCoastCtx(ctx, zoom, ox, oy, vx0, vy0, vx1, vy1, scaleW = 1) {
  if (!coastVectorOn()) return;
  const s = 256 * Math.pow(2, zoom) / 256, W = 256 * s; // larghezza del mondo in pixel
  const col = CO.col, lum = parseInt(col.slice(1, 3), 16) * 0.3 + parseInt(col.slice(3, 5), 16) * 0.59 + parseInt(col.slice(5, 7), 16) * 0.11;
  const alone = lum < 90 ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.55)';
  ctx.save(); ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  // copie del mondo visibili (antimeridiano, Pacifico)
  const kMin = Math.floor((vx0 + ox) / W), kMax = Math.floor((vx1 + ox) / W);
  const tol = 0.6; // punti piu' vicini di 0,6 px non si disegnano (alleggerisce gli zoom bassi)
  for (const set of COAST_SETS) {
    if (!CO.sets[set.id]) continue;
    const d = coastData.get(set.id);
    if (!d || d.state !== 'ok') { coastLoad(set).then(() => coastRedraw()); continue; }
    const w = Math.max(0.4, CO.w * set.k) * scaleW;
    const path = new Path2D();
    for (const ln of d.lines) {
      if (ln.mz > zoom + 1.5) continue; // laghi e fiumi minori solo da vicino
      for (let k = kMin; k <= kMax; k++) {
        const dx = k * W - ox;
        if (ln.bb[2] * s + dx < vx0 - 4 || ln.bb[0] * s + dx > vx1 + 4 || ln.bb[3] * s - oy < vy0 - 4 || ln.bb[1] * s - oy > vy1 + 4) continue;
        const p = ln.p, m = p.length >> 1;
        let lx = p[0] * s + dx, ly = p[1] * s - oy;
        path.moveTo(lx, ly);
        for (let i = 1; i < m; i++) {
          const x = p[i * 2] * s + dx, y = p[i * 2 + 1] * s - oy;
          if (i < m - 1 && Math.abs(x - lx) < tol && Math.abs(y - ly) < tol) continue;
          path.lineTo(x, y); lx = x; ly = y;
        }
      }
    }
    ctx.setLineDash(set.dash ? set.dash.map(v => v * scaleW) : []);
    if (CO.alone) { ctx.globalAlpha = CO.op * (set.op || 1) * 0.8; ctx.strokeStyle = alone; ctx.lineWidth = w + 1.6 * scaleW; ctx.stroke(path); }
    ctx.globalAlpha = CO.op * (set.op || 1); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.stroke(path);
  }
  ctx.restore();
}
map.createPane('coste').style.zIndex = 450; // sopra tasselli (200), confronto (350) e tracce (400); sotto marcatori e popup
map.getPane('coste').style.pointerEvents = 'none';
/* agganciato a _draw: L.Canvas ridisegna (e prima cancella) la tela da solo dopo ogni zoom/spostamento */
const CoastRenderer = L.Canvas.extend({
  _draw() {
    L.Canvas.prototype._draw.call(this);
    this._drawCoast();
  },
  _drawCoast() {
    const ctx = this._ctx, b = this._bounds; if (!ctx || !b) return;
    const po = this._map.getPixelOrigin();
    // il contesto e' gia' traslato di -b.min (coordinate "layer point" di Leaflet)
    drawCoastCtx(ctx, this._map.getZoom(), po.x, po.y, b.min.x, b.min.y, b.max.x, b.max.y);
  },
});
const coastRenderer = new CoastRenderer({ pane: 'coste', padding: 0.3 });
map.addLayer(coastRenderer);
function coastRedraw() { if (coastRenderer._map && coastRenderer._bounds) coastRenderer._redraw(); }
/* fonte NASA: il vecchio layer raster nella pila; Natural Earth: tela vettoriale */
function coastApply(silent) {
  let cambiato = false;
  if (CO.fonte === 'nasa' && cat(NASA_COAST)) {
    const has = S.stack.some(e => e.id === NASA_COAST);
    if (CO.on && !has) { addLayer(NASA_COAST, { at: 0 }, true); cambiato = true; }
    if (!CO.on && has) { removeLayer(NASA_COAST, true); cambiato = true; }
  }
  if (!silent && cambiato) afterStackChange();
  coastRedraw(); coastUI(); coastSave();
  if (booted) scheduleHash();
}
function coastUI() {
  const box = $('#coastBox'); if (!box) return;
  $('#coastOn').checked = CO.on;
  $('#coastFonte').value = CO.fonte;
  box.classList.toggle('off', !CO.on);
  $('#coastNe').hidden = CO.fonte !== 'ne';
  COAST_SETS.forEach(st => { const c = $(`#coastSet_${st.id}`); if (c) c.checked = !!CO.sets[st.id]; });
  $('#coastW').value = CO.w; $('#coastWo').textContent = num(CO.w, 1);
  $('#coastOp').value = Math.round(CO.op * 100); $('#coastOpo').textContent = Math.round(CO.op * 100);
  $('#coastCol').value = CO.col; $('#coastAlone').checked = CO.alone;
  $$('#coastPresets [data-col]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.col.toLowerCase() === CO.col.toLowerCase())));
}
(function coastInitUI() {
  const box = $('#coastSets');
  if (box) box.innerHTML = COAST_SETS.map(st => `<label class="check"><input type="checkbox" id="coastSet_${st.id}"> ${esc(st.nome)}</label>`).join('');
  const pr = $('#coastPresets');
  if (pr) pr.innerHTML = COAST_PRESETS.map(([n, c]) => `<button type="button" class="swatch" data-col="${c}" title="${n}" aria-label="Linee ${n.toLowerCase()}" style="--c:${c}"></button>`).join('');
  $('#coastOn')?.addEventListener('change', ev => { CO.on = ev.target.checked; coastApply(); });
  $('#coastFonte')?.addEventListener('change', ev => {
    CO.fonte = ev.target.value;
    if (CO.fonte === 'ne' && S.stack.some(e => e.id === NASA_COAST)) { removeLayer(NASA_COAST, true); afterStackChange(); }
    coastApply();
  });
  COAST_SETS.forEach(st => $(`#coastSet_${st.id}`)?.addEventListener('change', ev => { CO.sets[st.id] = ev.target.checked; if (ev.target.checked) coastLoad(st).then(coastRedraw); coastApply(true); }));
  $('#coastW')?.addEventListener('input', ev => { CO.w = +ev.target.value; coastApply(true); });
  $('#coastOp')?.addEventListener('input', ev => { CO.op = +ev.target.value / 100; coastApply(true); });
  $('#coastCol')?.addEventListener('input', ev => { CO.col = ev.target.value; coastApply(true); });
  $('#coastAlone')?.addEventListener('change', ev => { CO.alone = ev.target.checked; coastApply(true); });
  pr?.addEventListener('click', ev => { const b = ev.target.closest('[data-col]'); if (!b) return; CO.col = b.dataset.col; coastApply(true); });
  coastUI();
})();
/* stato nel link: co=acceso~fonte~spessore~colore~opacita~insiemi~alone */
function coastHash() {
  return [CO.on ? 1 : 0, CO.fonte, CO.w, CO.col.replace('#', ''), Math.round(CO.op * 100), COAST_SETS.map(st => (CO.sets[st.id] ? 1 : 0)).join(''), CO.alone ? 1 : 0].join('~');
}
function coastFromHash(v) {
  const [on, fonte, w, col, op, sets, alone] = String(v).split('~');
  CO.on = on !== '0'; CO.fonte = fonte === 'nasa' ? 'nasa' : 'ne';
  if (isFinite(+w) && +w > 0) CO.w = clamp(+w, 0.3, 6);
  if (/^[0-9a-f]{6}$/i.test(col || '')) CO.col = '#' + col;
  if (isFinite(+op) && op !== '') CO.op = clamp(+op / 100, 0.05, 1);
  if (sets) COAST_SETS.forEach((st, i) => { if (sets[i] != null) CO.sets[st.id] = sets[i] === '1'; });
  if (alone != null) CO.alone = alone !== '0';
}

/* =========================================================================
 * 20. Avvio
 * ========================================================================= */
let booted = false;
async function detectBackend() {
  try { await getJSON('/api/health', 4000); return 'api'; } catch { return 'mock'; }
}
function defaultStack() {
  const meteo = geoLayer('Meteosat', 'color');
  const add = (Lr, o) => Lr && addLayer(Lr.id, { ...o, at: S.stack.length }, true);
  // coste: linea vettoriale (Natural Earth) sempre sopra, non piu' un layer della pila
  if (CO.fonte === 'nasa') add(cat(NASA_COAST));
  if (meteo && meteo.timed) {
    add(meteo); map.setView([42, 12], 5, { animate: false });
  } else {
    add(findLayer(/^GOES-East_ABI_GeoColor$/) || geoLayer('GOES-East', 'color'));
    add(polarLayer('color'));
  }
  add(findLayer(/^BlueMarble_ShadedRelief_Bathymetry$/) || findLayer(/^BlueMarble/));
  S.range = 180;
}
async function boot() {
  const msg = t => ($('#bootMsg').textContent = t);
  S.mode = await detectBackend();
  let data = null;
  if (S.mode === 'api') {
    msg('Leggo il catalogo dei satelliti…');
    try { data = await getJSON('/api/layers', 45000); } catch { /* ricado sulla demo */ }
  }
  if (!data || !(data.groups || []).length) {
    if (S.mode === 'api') toast('Il server risponde ma il catalogo layer non è disponibile: uso il catalogo dimostrativo.', 'err', 7000);
    S.mode = 'mock';
    try { data = await getJSON('_mock/layers.json'); } catch { msg('Server Meteo non raggiungibile. Avvia Avvia_Meteo.bat e ricarica la pagina.'); return; }
  }
  $('#stDemo').hidden = S.mode !== 'mock';
  if (S.mode === 'mock') toast('Server Meteo non raggiungibile: modalità dimostrativa con immagini scaricate direttamente da NASA GIBS.', 'info', 7000);
  loadCatalog(data);
  renderCatalog(); fillCompareSelect(); renderViews(); loopUI(); playUI();
  if (!(location.hash && applyHash(location.hash))) { defaultStack(); afterStackChange(false); }
  booted = true; scheduleHash(); rangeUI();
  refreshLatest(true);
  loadEvents();
  if (mobile()) { togglePanel('left', false); togglePanel('right', false); }
  $('#boot').classList.add('gone'); setTimeout(() => $('#boot').remove(), 600);
  // in modalita' dimostrativa ricontrolla ogni minuto se il server e' tornato
  if (S.mode === 'mock') {
    const iv = setInterval(async () => {
      try { await getJSON('/api/health', 3000); clearInterval(iv); toast('Server Meteo collegato: ricarico con i dati completi…', 'ok'); setTimeout(() => location.reload(), 1200); } catch { /* ancora giu' */ }
    }, 60000);
  }
}
window.addEventListener('resize', debounce(() => { clipCompare(); schedulePreload(); }, 200));
boot();


/* ---------------------------------------------------------------- Vulcani (Sentinel-2 dal monitor) */
const VC = { list: [], cur: null, idx: 0, ov: null, ovOn: false };
function vcFile(v, r, kind) { return `/api/vulcani/file/${encodeURIComponent(v.nome)}/${encodeURIComponent(r.file + '_' + kind + '.jpg')}`; }
function vcDate(r) { const d = new Date(r.quando); return d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: '2-digit', timeZone: 'UTC' }) + ' ' + d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'; }
async function vcLoad() {
  try {
    const j = await (await fetch('/api/vulcani')).json();
    VC.list = j.vulcani || [];
    $('#vcRefresh').disabled = !!j.inCorso; $('#vcRefresh').textContent = j.inCorso ? 'Cerco…' : 'Aggiorna';
  } catch (e) { $('#vcList').innerHTML = '<div class="empty">Non riesco a leggere i vulcani.</div>'; return; }
  $('#vcList').innerHTML = VC.list.map(v => {
    const att = v.scene.filter(r => r.attivo).length, ult = v.scene[v.scene.length - 1];
    return `<button class="vc-item" data-vc="${v.chiave}"><b>${esc(v.nome)}</b><span class="muted small">${v.scene.length ? `${v.scene.length} scene · ultima ${vcDate(ult).slice(0, 8)}${att ? ` · <span class="vc-hot">${att} con lava</span>` : ''}` : 'nessuna scena ancora'}</span></button>`;
  }).join('');
  $$('#vcList .vc-item').forEach(b => (b.onclick = () => vcOpen(b.dataset.vc)));
  if (VC.cur) vcOpen(VC.cur.chiave, true);
}
function vcOpen(chiave, keep) {
  const v = VC.list.find(x => x.chiave === chiave); if (!v) return;
  VC.cur = v; $('#vcView').hidden = !v.scene.length; $('#vcTitle').textContent = v.nome;
  if (!keep) {
    VC.idx = Math.max(0, v.scene.length - 1);
    const a = v.scene.map((r, i) => (r.attivo ? i : -1)).filter(i => i >= 0); if (a.length) VC.idx = a[a.length - 1];
    map.flyTo([v.lat, v.lon], 11, { duration: 1.2 });
  }
  $('#vcSlide').max = Math.max(0, v.scene.length - 1); vcShow(VC.idx);
  const box = $('#vcVideoBox');
  box.innerHTML = v.video ? `<video src="/api/vulcani/file/_video/${encodeURIComponent(v.video)}" controls muted loop playsinline preload="metadata" style="width:100%"></video>
    <div class="exp-yt"><input class="exp-yt-t" id="vcYtT" type="text" maxlength="100" value="${esc(v.nome + ': attività vista dal satellite')}" aria-label="Titolo su YouTube"><div class="row"><select id="vcYtP" aria-label="Visibilità"><option value="public">Pubblico</option><option value="unlisted">Non in elenco</option><option value="private">Privato</option></select><button class="btn primary" id="vcYt">Pubblica su YouTube</button></div><span class="exp-yt-s muted small" id="vcYtS" hidden></span></div>` : '<p class="muted small">Il video si crea quando ci sono almeno 2 scene buone.</p>';
  if (v.video) $('#vcYt').onclick = () => vcPublish(v);
}
/* livelli del vulcano: si SOVRAPPONGONO (dal basso: colori reali, SWIR, bagliore lava trasparente), ognuno con visibilita' e opacita' */
const VC_LAYERS = [['colori-reali', 'Colori reali', 'jpg'], ['swir', 'Infrarosso SWIR', 'jpg'], ['glow', 'Bagliore lava (trasparente)', 'png']];
VC.lv = { 'colori-reali': { on: true, op: 100, bm: '' }, swir: { on: false, op: 100, bm: 'hard-light' }, glow: { on: true, op: 100, bm: '' } };
VC.ovs = {};
function vcUrl(v, r, k) { const ext = VC_LAYERS.find(x => x[0] === k)[2]; return `/api/vulcani/file/${encodeURIComponent(v.nome)}/${encodeURIComponent(r.file + '_' + k + '.' + ext)}`; }
function vcLayersUI() {
  $('#vcLayers').innerHTML = [...VC_LAYERS].reverse().map(([k, nome]) => `<div class="vc-lv"><label><input type="checkbox" data-vk="${k}" ${VC.lv[k].on ? 'checked' : ''}> ${nome}</label></div>`).join('');
  $$('#vcLayers [data-vk]').forEach(x => (x.onchange = () => { VC.lv[x.dataset.vk].on = x.checked; vcApply(); }));
}
function vcApply() {
  const v = VC.cur; if (!v || !v.scene.length) return;
  const r = v.scene[VC.idx];
  // anteprima nel pannello: stesse immagini sovrapposte
  const box = $('#vcStack');
  if (!box.dataset.k || box.dataset.k !== v.chiave + VC.idx) {
    box.dataset.k = v.chiave + VC.idx;
    box.innerHTML = VC_LAYERS.map(([k]) => `<img data-vl="${k}" src="${vcUrl(v, r, k)}" alt="">`).join('');
  }
  VC_LAYERS.forEach(([k]) => { const im = box.querySelector(`[data-vl="${k}"]`); if (im) { im.style.opacity = VC.lv[k].on ? 1 : 0; im.style.mixBlendMode = VC.lv[k].bm; } });
  // mappa
  const dl = v.latoKm / 2 / 111, dn = dl / Math.cos(v.lat * Math.PI / 180), bounds = [[v.lat - dl, v.lon - dn], [v.lat + dl, v.lon + dn]];
  VC_LAYERS.forEach(([k], n) => {
    let ov = VC.ovs[k];
    const want = VC.ovOn && VC.lv[k].on;
    if (!want) { if (ov) { map.removeLayer(ov); delete VC.ovs[k]; } return; }
    const url = vcUrl(v, r, k);
    if (ov && ov._url !== url) { map.removeLayer(ov); ov = null; }
    if (!ov) { ov = VC.ovs[k] = L.imageOverlay(url, bounds, { zIndex: 650 + n, interactive: false }).addTo(map); ov._url = url; }
    ov.setOpacity(1); ov.getElement().style.mixBlendMode = VC.lv[k].bm;
  });
}
function vcShow(i) {
  const v = VC.cur; if (!v || !v.scene.length) return;
  VC.idx = Math.min(Math.max(0, i), v.scene.length - 1); $('#vcSlide').value = VC.idx;
  const r = v.scene[VC.idx];
  $('#vcMeta').innerHTML = `<b>${vcDate(r)}</b> · ${r.attivo ? '<span class="vc-hot">attività termica: lava in superficie</span>' : (r.pixel_caldi >= 5 ? 'debole calore al cratere' : 'nessun calore anomalo')}<br><span class="muted">area calda ${num(r.area_ha, 1)} ha · nubi ${Math.round((r.nubi || 0) * 100)}% · ${esc(r.piattaforma || '')}</span>`;
  vcLayersUI(); vcApply();
}
function vcOverlay() { vcApply(); }
async function vcPublish(v) {
  const b = $('#vcYt'), st = $('#vcYtS'), pr = $('#vcYtP').value, tt = $('#vcYtT').value.trim() || v.nome;
  const say = (h, cls = '') => { st.hidden = false; st.className = 'exp-yt-s small ' + cls; st.innerHTML = h; };
  b.disabled = true; say('Invio del video a YouTube…');
  try {
    const blob = await (await fetch(`/api/vulcani/file/_video/${encodeURIComponent(v.video)}`)).blob();
    const desc = v.nome + " vista da Sentinel-2 (10 m): colori reali con il bagliore della lava nell'infrarosso. Dati: Copernicus Sentinel-2 / ESA.";
    const r = await fetch('/api/youtube/carica', { method: 'POST', headers: { 'Content-Type': 'video/mp4', 'X-Titolo': encodeURIComponent(tt), 'X-Descrizione': encodeURIComponent(desc), 'X-Privacy': pr }, body: blob });
    const j = await r.json(); if (!r.ok) throw new Error(j.errore || j.error || r.status);
    for (let i = 0; i < 1800; i++) {
      await new Promise(ok => setTimeout(ok, 2000));
      const q = await (await fetch('/api/youtube/stato/' + j.id, { method: 'POST', body: '' })).json();
      if (q.stato === 'fatto') { say(`Caricato: <a href="${q.link}" target="_blank" rel="noopener">${q.link}</a>`, 'exp-note'); toast('Video caricato su YouTube', 'ok', 4500); return; }
      if (q.stato === 'errore') throw new Error(q.errore);
    }
    throw new Error('tempo scaduto');
  } catch (e) { say('Pubblicazione non riuscita: ' + esc(errText(e)), 'exp-err'); b.disabled = false; }
}
$('#vcSlide').oninput = e => vcShow(+e.target.value);
$('#vcPrev').onclick = () => vcShow(VC.idx - 1);
$('#vcNext').onclick = () => vcShow(VC.idx + 1);
$('#vcMap').onclick = () => { VC.ovOn = !VC.ovOn; $('#vcMap').textContent = VC.ovOn ? 'Togli dalla mappa' : 'Mostra sulla mappa'; vcOverlay(); if (VC.ovOn && VC.cur) map.flyTo([VC.cur.lat, VC.cur.lon], 12, { duration: 1 }); };
$('#vcRefresh').onclick = async () => {
  await fetch('/api/vulcani/aggiorna'); toast('Cerco nuovi passaggi Sentinel-2: ci vuole qualche minuto.', 'info', 4000);
  $('#vcRefresh').disabled = true; $('#vcRefresh').textContent = 'Cerco…';
  const t = setInterval(async () => { const j = await (await fetch('/api/vulcani')).json(); if (!j.inCorso) { clearInterval(t); vcLoad(); toast('Vulcani aggiornati.', 'ok', 2500); } }, 8000);
};
$$('.tab').forEach(t => { if (t.dataset.tab === 'tVolc') t.addEventListener('click', vcLoad); });
