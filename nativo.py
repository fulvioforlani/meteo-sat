# -*- coding: utf-8 -*-
"""
nativo.py - tasselli alla RISOLUZIONE NATIVA dai file originali dei geostazionari.

Fonti (gratis, senza account, HTTPS anonimo sui bucket pubblici NOAA su AWS S3):
  GOES-East  = GOES-19  bucket noaa-goes19     prodotti ABI-L2-CMIPF (una banda, risoluzione piena)
  GOES-West  = GOES-18  bucket noaa-goes18                 + ABI-L2-MCMIPF (16 bande a 2 km, per zoom bassi)
  Himawari-9            bucket noaa-himawari9  AHI-L1b-FLDK (Himawari Standard Data, .DAT.bz2, 10 segmenti/banda)
  Meteosat MTG          -> solo stub documentato (mtg_stub), servono credenziali EUMETSAT.

Bande logiche (argomento `banda`):
  "vis"        rosso visibile 0,5 km (ABI C02 / AHI B03), riflettanza, grigio gamma 0,5
  "ir"         infrarosso 10,3 um 2 km (ABI C13 / AHI B13): GRIGIO CODIFICATO IN TEMPERATURA (vedi sotto)
  "truecolor"  colore vero: R=C02 0,5 km, B=C01 1 km, G sintetico 0,45R+0,10NIR+0,45B (ABI);
               AHI: R=B03 0,5 km, G=0,93*B02+0,07*B04, B=B01 (1 km). Correzione zenit solare + curva contrasto.
  "geocolor"   truecolor di giorno + nuvole IR (C13/B13) di notte su fondo blu, sfumatura sul terminatore.
  "C01".."C16" (GOES) o "B01".."B16" (Himawari): banda grezza; VIS/NIR in riflettanza, IR codificate come "ir".

Codifica IR (tasselli "ir" e bande IR grezze): PNG 8 bit grigio + alfa (modo LA)
  v = 0                         -> nessun dato (alfa 0)
  v = 1..255                    -> T[°C] = 60 - (v - 1) * 170 / 254     (passo 0,669 °C)
  cioe' v=1 -> +60 °C (nero), v=255 -> -110 °C (bianco): freddo = chiaro come le immagini IR classiche.
  colormap_ir_xml() produce una ColorMap in formato GIBS (rgb="v,v,v" value="[a,b)") che il decodificatore
  gia' presente in static/app.js sa leggere: basta dare al layer  legenda: {colormap: "<url di quel xml>"}.

Geometria: formule GOES-R PUG vol.3 sez. 4.2.8 (lat/lon -> angoli di scansione x,y -> colonna/riga) per ABI,
e CGMS LRIT/HRIT (COFF/CFAC/LOFF/LFAC) per AHI, con numpy diretto: per ogni tassello si calcolano solo i
65 536 pixel 256x256 (campionamento nearest = nessuna perdita di dettaglio fino allo zoom nativo).

Lettura parziale: i file GOES sono HDF5 con chunk a strisce di righe. Si legge l'indice dei chunk una volta
(h5py su file remoto via HTTP Range, ~5-10 s, poi salvato in .idx.npz) e da li' si scaricano SOLO i byte delle
strisce che servono al tassello (blocchi da 512 KB in parallelo, cache su disco). Himawari: si scaricano
solo i segmenti (1/10 di disco) toccati dal tassello, decompressi in .DAT e letti in memmap.

Cache: cache/nativo/ (blocchi, segmenti, indici, tasselli PNG finali). Pulizia automatica dei file piu' vecchi
oltre METEO_NATIVO_MAX_GB (default 20).

Dipendenze: numpy, pillow, requests, h5py (pip install h5py ; wheel cp314 disponibile).

API pubblica (pura, thread-safe):
  SATELLITI, BANDE, info()
  lista_istanti(sat, banda, ore=3)            -> ["2026-10-11T16:00:00Z", ...] (ordine crescente, passo 10 min)
  ultimo_istante(sat, banda)                  -> "2026-10-11T16:40:00Z" | None
  tassello(sat, banda, istante, z, x, y)      -> bytes PNG 256x256 (trasparente fuori disco); istante None = ultimo
  prepara(sat, banda, istante)                -> pre-scarica indici (per animazioni fluide)
  colormap_ir_xml()                           -> str XML
  pulisci_cache(max_gb=None)                  -> byte liberati
  mtg_stub(...)                               -> NotImplementedError (documentazione Meteosat MTG)
CLI: python nativo.py --help
"""
import bz2
import io
import json
import math
import os
import re
import sys
import threading
import time
import zlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from PIL import Image

QUI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("METEO_NATIVO_CACHE", os.path.join(QUI, "cache", "nativo"))
MAX_GB = float(os.environ.get("METEO_NATIVO_MAX_GB", "20"))
BLOCCO = 512 * 1024          # granularita' delle letture HTTP Range sui file GOES
POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="nativo")
_POOL_META = ThreadPoolExecutor(max_workers=16, thread_name_prefix="nativo-btree")  # letture piccole dei nodi B-tree
_POOL_PREP = ThreadPoolExecutor(max_workers=8, thread_name_prefix="nativo-prep")  # indici delle bande in parallelo
LRU_CHUNK_MB = 384           # chunk HDF5 decompressi tenuti in RAM (tasselli vicini condividono le strisce)
SOGLIA_2KM_M = 1400          # se un pixel del tassello e' >= 1,4 km a terra si usano i dati a 2 km

SATELLITI = {
    "goes19": {"nome": "GOES-East (GOES-19)", "bucket": "noaa-goes19", "tipo": "abi", "lon": -75.0, "sigla": "G19"},
    "goes18": {"nome": "GOES-West (GOES-18)", "bucket": "noaa-goes18", "tipo": "abi", "lon": -137.0, "sigla": "G18"},
    "himawari9": {"nome": "Himawari-9", "bucket": "noaa-himawari9", "tipo": "ahi", "lon": 140.7, "sigla": "H09"},
}
ALIAS = {"goes-east": "goes19", "goeseast": "goes19", "east": "goes19", "g19": "goes19",
         "goes-west": "goes18", "goeswest": "goes18", "west": "goes18", "g18": "goes18",
         "himawari": "himawari9", "h9": "himawari9", "h09": "himawari9", "himawari-9": "himawari9"}

BANDE = {
    "vis": {"nome": "Visibile rosso 0,64 um", "km": 0.5, "maxZoom": 9, "abi": ["C02"], "ahi": ["B03"]},
    "ir": {"nome": "Infrarosso 10,4 um (T brillanza)", "km": 2.0, "maxZoom": 7, "abi": ["C13"], "ahi": ["B13"]},
    "truecolor": {"nome": "Colore vero", "km": 0.5, "maxZoom": 9, "abi": ["C01", "C02", "C03"],
                  "ahi": ["B01", "B02", "B03", "B04"]},
    "geocolor": {"nome": "GeoColor (giorno colore vero, notte IR)", "km": 0.5, "maxZoom": 9,
                 "abi": ["C01", "C02", "C03", "C13"], "ahi": ["B01", "B02", "B03", "B04", "B13"]},
}
# risoluzione nativa per banda: ABI C02 0,5 km; C01,C03,C05 1 km; resto 2 km.  AHI B03 0,5; B01,B02,B04 1; resto 2
RES_ABI = {**{f"C{i:02d}": 2.0 for i in range(1, 17)}, "C02": 0.5, "C01": 1.0, "C03": 1.0, "C05": 1.0}
RES_AHI = {**{f"B{i:02d}": 2.0 for i in range(1, 17)}, "B03": 0.5, "B01": 1.0, "B02": 1.0, "B04": 1.0}

_sess_local = threading.local()


def _sess():
    s = getattr(_sess_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = "MeteoSat-locale/1.0"
        _sess_local.s = s
    return s


def _get(url, headers=None, timeout=60, tentativi=3, stream=False):
    err = None
    for k in range(tentativi):
        try:
            r = _sess().get(url, headers=headers or {}, timeout=timeout, stream=stream)
            if r.status_code in (200, 206):
                return r
            if r.status_code == 404:
                raise FileNotFoundError(url)
            err = RuntimeError(f"HTTP {r.status_code} {url}")
        except FileNotFoundError:
            raise
        except Exception as e:  # rete instabile: ritenta
            err = e
        time.sleep(0.5 * (k + 1))
    raise err


_locks = {}
_locks_g = threading.Lock()


def _lock(chiave):
    with _locks_g:
        l = _locks.get(chiave)
        if l is None:
            l = _locks[chiave] = threading.Lock()
        return l


def _scrivi_atomico(path, dati):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "wb") as f:
        f.write(dati)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------------------------------------
# Tempo e istanti
# ----------------------------------------------------------------------------------------------------------
def _sat(s):
    s = (s or "").lower()
    s = ALIAS.get(s, s)
    if s not in SATELLITI:
        raise ValueError(f"satellite sconosciuto: {s} (validi: {', '.join(SATELLITI)})")
    return s


def _parse_istante(t):
    if t is None:
        return None
    if isinstance(t, datetime):
        d = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    else:
        s = str(t).strip().replace("Z", "").replace("z", "")
        m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})[T _]?(\d{2}):?(\d{2})", s)
        if not m:
            raise ValueError(f"istante non valido: {t}")
        d = datetime(*map(int, m.groups()), tzinfo=timezone.utc)
    d = d.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return d.replace(minute=d.minute - d.minute % 10)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:00Z")


def _bande_fisiche(sat, banda):
    tipo = SATELLITI[sat]["tipo"]
    b = banda.lower()
    if b in BANDE:
        return BANDE[b][tipo]
    B = banda.upper()
    if tipo == "abi" and re.fullmatch(r"C(0[1-9]|1[0-6])", B):
        return [B]
    if tipo == "ahi" and re.fullmatch(r"B(0[1-9]|1[0-6])", B):
        return [B]
    raise ValueError(f"banda non valida per {sat}: {banda}")


# ---- listing S3 (con cache in RAM) ----
_list_cache = {}
_list_g = threading.Lock()


def _s3_list(bucket, prefix, delimiter=None, ttl=60):
    k = (bucket, prefix, delimiter)
    with _list_g:
        v = _list_cache.get(k)
        if v and time.time() - v[0] < v[1]:
            return v[2]
    chiavi, pref, token = [], [], None
    while True:
        url = f"https://{bucket}.s3.amazonaws.com/?list-type=2&prefix={prefix}"
        if delimiter:
            url += f"&delimiter={delimiter}"
        if token:
            url += "&continuation-token=" + requests.utils.quote(token, safe="")
        x = _get(url, timeout=30).text
        chiavi += [(a, int(b)) for a, b in re.findall(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", x, re.S)]
        pref += re.findall(r"<CommonPrefixes><Prefix>(.*?)</Prefix>", x)
        m = re.search(r"<NextContinuationToken>(.*?)</NextContinuationToken>", x)
        if "<IsTruncated>true" in x and m:
            token = m.group(1)
        else:
            break
    with _list_g:
        _list_cache[k] = (time.time(), ttl, (chiavi, pref))
    return chiavi, pref


def _abi_ora(sat, prodotto, d):
    """chiavi S3 di un'ora ABI: {(istante, banda): (key, size)}"""
    b = SATELLITI[sat]["bucket"]
    pref = f"{prodotto}/{d:%Y}/{d.timetuple().tm_yday:03d}/{d:%H}/"
    recente = datetime.now(timezone.utc) - d < timedelta(hours=1, minutes=15)
    chiavi, _ = _s3_list(b, pref, ttl=60 if recente else 3600)
    out = {}
    for k, sz in chiavi:
        m = re.search(r"-M\d(C\d\d)?_G\d\d_s(\d{4})(\d{3})(\d{2})(\d{2})", k)
        if not m:
            continue
        banda = m.group(1) or "ALL"
        t = datetime(int(m.group(2)), 1, 1, int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc) + timedelta(
            days=int(m.group(3)) - 1)
        t = t.replace(minute=t.minute - t.minute % 10)
        out[(t, banda)] = (k, sz)
    return out


def _abi_chiave(sat, prodotto, banda, d):
    v = _abi_ora(sat, prodotto, d.replace(minute=0)).get((d, banda if prodotto == "ABI-L2-CMIPF" else "ALL"))
    if not v:
        raise FileNotFoundError(f"{sat} {prodotto} {banda} {_iso(d)} non disponibile")
    return v


def _ahi_cartelle(d_giorno):
    b = SATELLITI["himawari9"]["bucket"]
    recente = datetime.now(timezone.utc) - d_giorno < timedelta(days=1, hours=1)
    _, pref = _s3_list(b, f"AHI-L1b-FLDK/{d_giorno:%Y/%m/%d}/", "/", ttl=60 if recente else 6 * 3600)
    out = []
    for p in pref:
        m = re.search(r"/(\d{2})(\d{2})/$", p)
        if m:
            out.append(d_giorno.replace(hour=int(m.group(1)), minute=int(m.group(2))))
    return out


def _ahi_completa(d, bande):
    _, nomi = _ahi_file(d, bande[0])
    chiavi, _ = _s3_list(SATELLITI["himawari9"]["bucket"], f"AHI-L1b-FLDK/{d:%Y/%m/%d/%H%M}/", ttl=60)
    presenti = {k.rsplit("/", 1)[-1] for k, _ in chiavi}
    return all(_ahi_file(d, b)[1][s].rsplit("/", 1)[-1] in presenti for b in bande for s in range(10))


def lista_istanti(sat, banda, ore=3):
    """Istanti disponibili (ISO UTC, crescenti, passo 10 min) nelle ultime `ore` ore."""
    sat = _sat(sat)
    bande = _bande_fisiche(sat, banda)
    ora = datetime.now(timezone.utc)
    inizio = ora - timedelta(hours=float(ore))
    out = []
    if SATELLITI[sat]["tipo"] == "abi":
        h = inizio.replace(minute=0, second=0, microsecond=0)
        while h <= ora:
            try:
                idx = _abi_ora(sat, "ABI-L2-CMIPF", h)
            except Exception:
                idx = {}
            tempi = sorted({t for (t, b) in idx})
            for t in tempi:
                if t >= inizio and all((t, b) in idx for b in bande):
                    out.append(t)
            h += timedelta(hours=1)
    else:
        g = inizio.replace(hour=0, minute=0, second=0, microsecond=0)
        cart = []
        while g <= ora:
            try:
                cart += _ahi_cartelle(g)
            except Exception:
                pass
            g += timedelta(days=1)
        cart = sorted(t for t in cart if t >= inizio)
        # le ultime cartelle possono essere ancora in scrittura: verifica che ci siano tutti i segmenti
        while cart and not _ahi_completa(cart[-1], bande):
            cart.pop()
        out = cart
    return [_iso(t) for t in out]


def ultimo_istante(sat, banda):
    for ore in (1, 3, 12):
        l = lista_istanti(sat, banda, ore)
        if l:
            return l[-1]
    return None


# ----------------------------------------------------------------------------------------------------------
# Lettura file GOES (HDF5/netCDF4) per blocchi HTTP Range + indice dei chunk
# ----------------------------------------------------------------------------------------------------------
class _Blocchi:
    """File remoto letto a blocchi da BLOCCO byte, cache sparsa su disco (un file per blocco)."""

    def __init__(self, bucket, key, size):
        self.url = f"https://{bucket}.s3.amazonaws.com/{key}"
        self.size = size
        self.dir = os.path.join(CACHE, bucket, key.rsplit("/", 1)[-1] + ".blk")
        self.nuovi = 0  # byte scaricati da questo oggetto

    def _p(self, i):
        return os.path.join(self.dir, f"{i:05d}")

    def _scarica(self, i):
        p = self._p(i)
        if os.path.exists(p):
            return
        with _lock(p):
            if os.path.exists(p):
                return
            a = i * BLOCCO
            b = min(self.size, a + BLOCCO) - 1
            d = _get(self.url, headers={"Range": f"bytes={a}-{b}"}).content
            if len(d) != b - a + 1:
                raise IOError(f"blocco incompleto {self.url} {i}")
            _scrivi_atomico(p, d)
            self.nuovi += len(d)
            _segnala_scrittura(len(d))

    def assicura(self, indici):
        mancanti = [i for i in sorted(set(indici)) if not os.path.exists(self._p(i))]
        if len(mancanti) == 1:
            self._scarica(mancanti[0])
        elif mancanti:
            list(POOL.map(self._scarica, mancanti))

    def leggi(self, a, n):
        i0, i1 = a // BLOCCO, (a + n - 1) // BLOCCO
        self.assicura(range(i0, i1 + 1))
        parti = []
        for i in range(i0, i1 + 1):
            with open(self._p(i), "rb") as f:
                base = i * BLOCCO
                s = max(a, base) - base
                e = min(a + n, base + BLOCCO) - base
                f.seek(s)
                parti.append(f.read(e - s))
        return b"".join(parti)


class _FileRemoto(io.RawIOBase):
    """Oggetto file per h5py (solo per leggere metadati e indice chunk)."""

    def __init__(self, blocchi):
        self.b, self.pos = blocchi, 0
        self.reg = None  # se lista: registra (posizione, byte, prime 4 byte) di ogni lettura

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, o, w=0):
        self.pos = o if w == 0 else (self.pos + o if w == 1 else self.b.size + o)
        return self.pos

    def readinto(self, buf):
        n = min(len(buf), self.b.size - self.pos)
        if n <= 0:
            return 0
        d = self.b.leggi(self.pos, n)
        buf[: len(d)] = d
        if self.reg is not None:
            self.reg.append((self.pos, len(d), bytes(d[:4])))
        self.pos += len(d)
        return len(d)


_h5_lock = threading.Lock()  # h5py su file-object Python: una costruzione indice alla volta


class _VarABI:
    """Variabile CMI (o CMI_Cnn di MCMIPF) di un file ABI: indice chunk + lettura puntuale."""

    def __init__(self, sat, key, size, var):
        self.blk = _Blocchi(SATELLITI[sat]["bucket"], key, size)
        self.var = var
        p = os.path.join(CACHE, SATELLITI[sat]["bucket"], key.rsplit("/", 1)[-1] + f".{var}.idx.npz")
        if os.path.exists(p):
            z = np.load(p, allow_pickle=False)
            self.off, self.siz, self.fm = z["off"], z["siz"], z["fm"]
            self.meta = json.loads(str(z["meta"]))
        else:
            with _lock(p):
                if os.path.exists(p):
                    return self.__init__(sat, key, size, var)
                if _usa_esterni() and _ESTERNI.costruisci(sat, key, size, var) and os.path.exists(p):
                    return self.__init__(sat, key, size, var)  # indice scritto dal processo ausiliario
                self._costruisci_indice()  # la parte h5py e' serializzata dentro (_h5_lock), il B-tree no
                buf = io.BytesIO()
                np.savez(buf, off=self.off, siz=self.siz, fm=self.fm, meta=np.array(json.dumps(self.meta)))
                _scrivi_atomico(p, buf.getvalue())
        m = self.meta
        self.shape, self.chunks = tuple(m["shape"]), tuple(m["chunks"])
        self.dtype = np.dtype(m["dtype"])

    def _costruisci_indice(self):
        """metadati con h5py (poche letture, serializzate da _h5_lock) + indice dei chunk letto DIRETTAMENTE dal
        B-tree HDF5 v1 livello per livello, con tutte le foglie scaricate in parallelo (piccole letture Range).
        Prima: chunk_iter di h5py leggeva le ~60 foglie una dopo l'altra (14 s per C02). Ora ~1-2 s.
        Se il formato non e' quello atteso si torna a chunk_iter (stesso risultato, piu' lento)."""
        with _h5_lock:
            radice = self._leggi_h5(iter_chunk=False)
        ok = False
        if radice:
            try:
                ok = self._btree(*radice)
            except Exception as e:  # formato inatteso: ripiego sicuro
                print(f"nativo: B-tree diretto non riuscito ({e}), uso h5py", file=sys.stderr)
        if not ok:
            with _h5_lock:
                self._leggi_h5(iter_chunk=True)

    def _btree(self, radice, nodo_n):
        ch = self.meta["chunks"]
        rank = len(ch)
        nci, ncj = -(-self.meta["shape"][0] // ch[0]), -(-self.meta["shape"][1] // ch[1])
        off = np.zeros((nci, ncj), np.int64)
        siz = np.zeros((nci, ncj), np.int64)
        fm = np.zeros((nci, ncj), np.int32)
        ksz = 8 + 8 * (rank + 1)  # dimensione chunk (4) + filter mask (4) + offset per dimensione (+1)
        livello, trovati = [radice], 0
        for _giro in range(8):
            if not livello:
                break
            dati = list(_POOL_META.map(lambda a: self._nodo(a, nodo_n), livello))
            prossimi = []
            for d in dati:
                if d[:4] != b"TREE" or d[4] != 1:
                    raise ValueError("nodo B-tree non valido")
                lev, used = d[5], int.from_bytes(d[6:8], "little")
                pos = 24
                for e in range(used):
                    k = d[pos:pos + ksz]
                    figlio = int.from_bytes(d[pos + ksz:pos + ksz + 8], "little")
                    pos += ksz + 8
                    if lev > 0:
                        prossimi.append(figlio)
                        continue
                    o = np.frombuffer(k[8:8 + 8 * rank], "<u8")
                    i, j = int(o[0]) // ch[0], int(o[1]) // ch[1]
                    off[i, j] = figlio
                    siz[i, j] = int.from_bytes(k[0:4], "little")
                    fm[i, j] = int.from_bytes(k[4:8], "little")
                    trovati += 1
            livello = prossimi
        if livello or trovati == 0 or trovati > nci * ncj:
            raise ValueError(f"B-tree incoerente ({trovati} chunk su {nci * ncj})")
        self.off, self.siz, self.fm = off, siz, fm
        return True

    def _nodo(self, a, n):
        """legge n byte all'indirizzo a: dai blocchi gia' in cache se ci sono, altrimenti piccola richiesta Range."""
        i0, i1 = a // BLOCCO, (a + n - 1) // BLOCCO
        if all(os.path.exists(self.blk._p(i)) for i in range(i0, i1 + 1)):
            return self.blk.leggi(a, n)
        d = _get(self.blk.url, headers={"Range": f"bytes={a}-{a + n - 1}"}, timeout=30).content
        if len(d) != n:
            raise IOError("lettura nodo incompleta")
        return d

    def _leggi_h5(self, iter_chunk):
        import h5py
        f = _FileRemoto(self.blk)
        h = h5py.File(f, "r")
        try:
            v = h[self.var]
            if v.compression not in ("gzip", None) or v.fletcher32 or v.scaleoffset:
                raise RuntimeError(f"filtri HDF5 non gestiti: {v.compression}")
            ch = v.chunks
            radice = None
            if iter_chunk:
                nci, ncj = -(-v.shape[0] // ch[0]), -(-v.shape[1] // ch[1])
                off = np.zeros((nci, ncj), np.int64)
                siz = np.zeros((nci, ncj), np.int64)
                fm = np.zeros((nci, ncj), np.int32)

                def cb(si):
                    i, j = si.chunk_offset[0] // ch[0], si.chunk_offset[1] // ch[1]
                    off[i, j], siz[i, j], fm[i, j] = si.byte_offset, si.size, si.filter_mask

                v.id.chunk_iter(cb)
                self.off, self.siz, self.fm = off, siz, fm
            else:
                # radice del B-tree: il primo nodo "TREE" che HDF5 legge cercando il chunk (0,0)
                f.reg = []
                try:
                    v.id.get_chunk_info_by_coord((0,) * len(v.shape))
                except Exception:
                    pass
                tree = [(p, n) for p, n, testa in f.reg if testa == b"TREE"]
                f.reg = None
                if tree:
                    radice = tree[0]
            a = v.attrs

            def num(nome, dflt):
                x = a.get(nome)
                return float(np.ravel(x)[0]) if x is not None else dflt

            pr = h["goes_imager_projection"].attrs
            xs, ys = h["x"].attrs, h["y"].attrs
            uns = a.get("_Unsigned")
            uns = (uns.decode() if isinstance(uns, bytes) else str(uns)).lower() == "true" if uns is not None else False
            dt = v.dtype.newbyteorder("<") if v.dtype.byteorder == "=" else v.dtype
            if uns:
                dt = np.dtype(dt.str.replace("i", "u"))
            t0 = h["t"][()] if "t" in h else 0.0  # secondi dal 2000-01-01 12:00 UTC
            self.meta = {
                "shape": list(v.shape), "chunks": list(ch), "dtype": dt.str, "shuffle": bool(v.shuffle),
                "gzip": v.compression == "gzip",
                "scale": num("scale_factor", 1.0), "offset": num("add_offset", 0.0),
                "fill": int(np.ravel(a.get("_FillValue", [-1]))[0]) & (0xFFFF if uns else 0xFFFFFFFF),
                "x_scale": float(np.ravel(xs["scale_factor"])[0]), "x_off": float(np.ravel(xs["add_offset"])[0]),
                "y_scale": float(np.ravel(ys["scale_factor"])[0]), "y_off": float(np.ravel(ys["add_offset"])[0]),
                "H": float(np.ravel(pr["perspective_point_height"])[0]) + float(np.ravel(pr["semi_major_axis"])[0]),
                "req": float(np.ravel(pr["semi_major_axis"])[0]), "rpol": float(np.ravel(pr["semi_minor_axis"])[0]),
                "lon0": float(np.ravel(pr["longitude_of_projection_origin"])[0]),
                "t": float(np.ravel(t0)[0]),
            }
            return radice
        finally:
            h.close()
            del h

    # ---- chunk decompressi con LRU condivisa ----
    def _chunk(self, i, j):
        k = (self.blk.url, self.var, i, j)
        v = _lru_get(k)
        if v is not None:
            return v
        raw = self.blk.leggi(int(self.off[i, j]), int(self.siz[i, j]))
        m = self.meta
        if m["gzip"] and not (self.fm[i, j] & 1):
            raw = zlib.decompress(raw)
        ch = self.chunks
        n = ch[0] * ch[1]
        if m["shuffle"] and not (self.fm[i, j] & 2):
            w = self.dtype.itemsize
            raw = np.frombuffer(raw, np.uint8).reshape(w, n).T.tobytes()
        a = np.frombuffer(raw, self.dtype).reshape(ch)
        _lru_put(k, a)
        return a

    def campiona(self, righe, colonne):
        """valori fisici (float32, NaN = nessun dato) per indici interi gia' validi."""
        ch = self.chunks
        ci, cj = righe // ch[0], colonne // ch[1]
        cod = ci.astype(np.int64) * 100000 + cj
        uniq = np.unique(cod)
        # prefetch in parallelo di tutti i blocchi necessari
        servono = []
        for c in uniq:
            i, j = int(c // 100000), int(c % 100000)
            if _lru_get((self.blk.url, self.var, i, j), tocca=False) is None:
                a, n = int(self.off[i, j]), int(self.siz[i, j])
                servono += range(a // BLOCCO, (a + n - 1) // BLOCCO + 1)
        self.blk.assicura(servono)
        out = np.empty(righe.shape, self.dtype)
        for c in uniq:
            i, j = int(c // 100000), int(c % 100000)
            sel = cod == c
            out[sel] = self._chunk(i, j)[righe[sel] - i * ch[0], colonne[sel] - j * ch[1]]
        m = self.meta
        val = out.astype(np.float32) * m["scale"] + m["offset"]
        val[out == m["fill"]] = np.nan
        return val


# ---- processi ausiliari per gli indici ----
# h5py esegue una sola operazione alla volta per processo (lock globale): preparando un'animazione con molti
# istanti le aperture dei file si mettevano in fila (fino a 30-40 s per istante). Gli indici dei fotogrammi da
# preparare si costruiscono quindi in N processi separati (senza finestra), che scrivono lo stesso .idx.npz
# in cache; i tasselli chiesti dalla mappa restano nel processo principale e non aspettano la coda.
N_ESTERNI = int(os.environ.get("METEO_NATIVO_PROCESSI", "3"))
_in_servizio = [False]


def _usa_esterni():
    return (N_ESTERNI > 0 and not _in_servizio[0]
            and threading.current_thread().name.startswith("nativo-prep"))


class _Esterni:
    def __init__(self, n):
        self.n, self.liberi, self.tutti = n, [], []
        self.cv = threading.Condition()
        self.guasto = 0

    def _avvia(self):
        import subprocess
        exe = sys.executable or "python"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        pr = subprocess.Popen([exe, os.path.abspath(__file__), "servizio-indici"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags,
                              env=dict(os.environ, METEO_NATIVO_CACHE=CACHE), cwd=QUI)
        self.tutti.append(pr)
        return pr

    def costruisci(self, sat, key, size, var):
        """True se un processo ausiliario ha scritto l'indice; False = fallo qui (ripiego)."""
        if self.guasto >= 3:
            return False
        with self.cv:
            while not self.liberi and len(self.tutti) >= self.n:
                self.cv.wait(5)
            if self.liberi:
                pr = self.liberi.pop()
            else:
                try:
                    pr = self._avvia()
                except Exception:
                    self.guasto += 1
                    return False
        ok = False
        try:
            pr.stdin.write((json.dumps({"sat": sat, "key": key, "size": size, "var": var}) + "\n").encode())
            pr.stdin.flush()
            r = json.loads(pr.stdout.readline() or b"{}")
            ok = bool(r.get("ok"))
            vivo = True
        except Exception:
            vivo = False
        with self.cv:
            if vivo and pr.poll() is None:
                self.liberi.append(pr)
            else:
                self.guasto += 1
                try:
                    pr.kill()
                except Exception:
                    pass
                self.tutti = [x for x in self.tutti if x is not pr]
            self.cv.notify()
        return ok

    def chiudi(self):
        for pr in self.tutti:
            try:
                pr.kill()
            except Exception:
                pass


_ESTERNI = _Esterni(N_ESTERNI)


def _servizio_indici():
    """ciclo del processo ausiliario: una richiesta JSON per riga su stdin, risposta JSON su stdout."""
    _in_servizio[0] = True
    out = sys.stdout.buffer
    for riga in sys.stdin.buffer:
        try:
            q = json.loads(riga)
            _VarABI(q["sat"], q["key"], int(q["size"]), q["var"])
            r = {"ok": True}
        except Exception as e:  # il processo principale ripiega da solo
            r = {"ok": False, "err": str(e)[:200]}
        out.write((json.dumps(r) + "\n").encode())
        out.flush()


_lru = OrderedDict()
_lru_bytes = [0]
_lru_g = threading.Lock()


def _lru_get(k, tocca=True):
    with _lru_g:
        v = _lru.get(k)
        if v is not None and tocca:
            _lru.move_to_end(k)
        return v


def _lru_put(k, a):
    with _lru_g:
        if k in _lru:
            return
        _lru[k] = a
        _lru_bytes[0] += a.nbytes
        while _lru_bytes[0] > LRU_CHUNK_MB * 1024 * 1024 and _lru:
            _, v = _lru.popitem(last=False)
            _lru_bytes[0] -= v.nbytes


_var_cache = {}
_var_g = threading.Lock()


def _var_abi(sat, banda, d, basso):
    """_VarABI per banda fisica Cnn all'istante d; basso=True -> MCMIPF 2 km."""
    if basso:
        key, size = _abi_chiave(sat, "ABI-L2-MCMIPF", banda, d)
        var = f"CMI_{banda}"
    else:
        key, size = _abi_chiave(sat, "ABI-L2-CMIPF", banda, d)
        var = "CMI"
    k = (key, var)
    with _var_g:
        v = _var_cache.get(k)
    if v is None:
        v = _VarABI(sat, key, size, var)
        with _var_g:
            if len(_var_cache) > 256:
                _var_cache.clear()
            _var_cache[k] = v
    return v


def _abi_scan(lat, lon, m):
    """GOES-R PUG: lat/lon (gradi) -> angoli x,y (rad) e maschera visibilita'."""
    la, lo = np.radians(lat), np.radians(lon - m["lon0"])
    req, rpol, H = m["req"], m["rpol"], m["H"]
    e2 = (req * req - rpol * rpol) / (req * req)
    phic = np.arctan((rpol * rpol) / (req * req) * np.tan(la))
    rc = rpol / np.sqrt(1 - e2 * np.cos(phic) ** 2)
    sx = H - rc * np.cos(phic) * np.cos(lo)
    sy = -rc * np.cos(phic) * np.sin(lo)
    sz = rc * np.sin(phic)
    vis = H * (H - sx) > sy * sy + (req * req) / (rpol * rpol) * sz * sz
    x = np.arcsin(-sy / np.sqrt(sx * sx + sy * sy + sz * sz))
    y = np.arctan(sz / sx)
    return x, y, vis


def _campiona_abi(var, lat, lon):
    m = var.meta
    x, y, vis = _abi_scan(lat, lon, m)
    c = np.rint((x - m["x_off"]) / m["x_scale"])
    r = np.rint((y - m["y_off"]) / m["y_scale"])
    ok = vis & (c >= 0) & (r >= 0) & (c < var.shape[1]) & (r < var.shape[0])
    out = np.full(lat.shape, np.nan, np.float32)
    if ok.any():
        out[ok] = var.campiona(r[ok].astype(np.int64), c[ok].astype(np.int64))
    return out


def _abi_tempo(var):
    return datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(seconds=var.meta.get("t", 0.0))


def _abi_visibile(sat, lat, lon):
    m = {"lon0": SATELLITI[sat]["lon"], "req": 6378137.0, "rpol": 6356752.31414, "H": 42164160.0}
    x, y, vis = _abi_scan(lat, lon, m)
    return vis & (np.abs(x) < 0.151865) & (np.abs(y) < 0.151865)


# ----------------------------------------------------------------------------------------------------------
# Himawari AHI (HSD): segmenti .DAT.bz2
# ----------------------------------------------------------------------------------------------------------
AHI_RES = {"B01": "R10", "B02": "R10", "B03": "R05", "B04": "R10"}
AHI_NOM = {"R05": (22000, 81865099), "R10": (11000, 40932549), "R20": (5500, 20466275)}  # righe/colonne, CFAC


def _ahi_file(d, banda):
    r = AHI_RES.get(banda, "R20")
    base = f"AHI-L1b-FLDK/{d:%Y/%m/%d/%H%M}/"
    return r, [base + f"HS_H09_{d:%Y%m%d_%H%M}_{banda}_FLDK_{r}_S{s + 1:02d}10.DAT.bz2" for s in range(10)]


class _SegAHI:
    def __init__(self, path):
        with open(path, "rb") as f:
            h = f.read(2048)
            nb = int(np.frombuffer(h, "<u2", 1, 3)[0])
            tot = int(np.frombuffer(h, "<u4", 1, 70)[0])
            f.seek(0)
            h = f.read(tot)
        blocchi, o = {}, 0
        for _ in range(nb):
            n, L = h[o], int(np.frombuffer(h, "<u2", 1, o + 1)[0])
            blocchi[n] = h[o:o + L]
            o += L
        f8 = lambda b, o: float(np.frombuffer(b, "<f8", 1, o)[0])
        u2 = lambda b, o: int(np.frombuffer(b, "<u2", 1, o)[0])
        b1, b2, b3, b5, b7 = blocchi[1], blocchi[2], blocchi[3], blocchi[5], blocchi[7]
        self.t = datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=f8(b1, 46))
        self.cols, self.righe = u2(b2, 5), u2(b2, 7)
        self.sublon = f8(b3, 3)
        self.cfac = int(np.frombuffer(b3, "<u4", 1, 11)[0])
        self.lfac = int(np.frombuffer(b3, "<u4", 1, 15)[0])
        self.coff = float(np.frombuffer(b3, "<f4", 1, 19)[0])
        self.loff = float(np.frombuffer(b3, "<f4", 1, 23)[0])
        self.H, self.re, self.rp = f8(b3, 27), f8(b3, 35), f8(b3, 43)
        self.banda = u2(b5, 3)
        self.lam = f8(b5, 5)
        self.err, self.fuori = u2(b5, 15), u2(b5, 17)
        self.gain, self.const = f8(b5, 19), f8(b5, 27)
        if self.banda <= 6:
            self.alb = f8(b5, 35)
            g2, c2 = f8(b5, 51), f8(b5, 59)  # coefficienti di calibrazione aggiornati (se presenti)
            if 0 < g2 < 10 * abs(self.gain) + 1:
                self.gain, self.const = g2, c2
        else:
            self.c0, self.c1, self.c2 = f8(b5, 35), f8(b5, 43), f8(b5, 51)
            self.cc, self.hh, self.kk = f8(b5, 83), f8(b5, 91), f8(b5, 99)
        self.riga1 = u2(b7, 5)  # prima riga (1-based) del segmento
        self.dati = np.memmap(path, np.uint16, "r", offset=tot, shape=(self.righe, self.cols))

    def fisico(self, cnt):
        bad = (cnt == self.err) | (cnt == self.fuori)
        rad = cnt.astype(np.float32) * self.gain + self.const
        if self.banda <= 6:
            v = rad * self.alb
        else:
            lam = self.lam * 1e-6
            with np.errstate(invalid="ignore", divide="ignore"):
                te = (self.hh * self.cc / (self.kk * lam)) / np.log(
                    2 * self.hh * self.cc ** 2 / (lam ** 5 * np.maximum(rad, 1e-6) * 1e6) + 1)
            v = (self.c0 + self.c1 * te + self.c2 * te * te).astype(np.float32)
        v = v.astype(np.float32)
        v[bad] = np.nan
        return v


_seg_cache = OrderedDict()
_seg_g = threading.Lock()


def _seg_ahi(key):
    with _seg_g:
        s = _seg_cache.get(key)
        if s is not None:
            _seg_cache.move_to_end(key)
            return s
    path = os.path.join(CACHE, "noaa-himawari9", key.rsplit("/", 1)[-1][:-4])  # senza .bz2
    if not os.path.exists(path):
        with _lock(path):
            if not os.path.exists(path):
                r = _get(f"https://noaa-himawari9.s3.amazonaws.com/{key}", timeout=120)
                d = bz2.decompress(r.content)
                _scrivi_atomico(path, d)
                _segnala_scrittura(len(d))
    s = _SegAHI(path)
    with _seg_g:
        _seg_cache[key] = s
        while len(_seg_cache) > 60:
            _seg_cache.popitem(last=False)
    return s


def _ahi_scan(lat, lon, sublon=140.7, H=42164.0, re_=6378.137, rp=6356.7523):
    """CGMS LRIT/HRIT: lat/lon -> x,y (gradi) e visibilita'."""
    la, lo = np.radians(lat), np.radians(lon - sublon)
    clat = np.arctan((rp * rp) / (re_ * re_) * np.tan(la))
    e2 = (re_ * re_ - rp * rp) / (re_ * re_)
    rl = rp / np.sqrt(1 - e2 * np.cos(clat) ** 2)
    r1 = H - rl * np.cos(clat) * np.cos(lo)
    r2 = -rl * np.cos(clat) * np.sin(lo)
    r3 = rl * np.sin(clat)
    rn = np.sqrt(r1 * r1 + r2 * r2 + r3 * r3)
    vis = H * (H - r1) > r2 * r2 + (re_ * re_) / (rp * rp) * r3 * r3
    x = np.degrees(np.arctan(-r2 / r1))
    y = np.degrees(np.arcsin(-r3 / rn))
    return x, y, vis


def _campiona_ahi(d, banda, lat, lon):
    res, chiavi = _ahi_file(d, banda)
    n, cfac = AHI_NOM[res]
    x, y, vis = _ahi_scan(lat, lon)
    off = n / 2 + 0.5
    c = np.rint(off + x * cfac / 2 ** 16)  # 1-based
    l = np.rint(off + y * cfac / 2 ** 16)
    ok = vis & (c >= 1) & (l >= 1) & (c <= n) & (l <= n)
    out = np.full(lat.shape, np.nan, np.float32)
    if not ok.any():
        return out, None
    per = n // 10
    segi = ((l - 1) // per).astype(np.int64)
    usati = sorted(set(segi[ok].tolist()))
    segs = dict(zip(usati, POOL.map(lambda s: _seg_ahi(chiavi[s]), usati)))
    for s, seg in segs.items():
        sel = ok & (segi == s)
        rr = (l[sel] - seg.riga1).astype(np.int64)
        cc = (c[sel] - 1).astype(np.int64)
        good = (rr >= 0) & (rr < seg.righe)
        v = np.full(rr.shape, np.nan, np.float32)
        v[good] = seg.fisico(np.asarray(seg.dati[rr[good], cc[good]]))
        out[sel] = v
    return out, next(iter(segs.values())).t


# ----------------------------------------------------------------------------------------------------------
# Geometria tasselli e rendering
# ----------------------------------------------------------------------------------------------------------
def _latlon_tassello(z, x, y, n=256):
    s = (np.arange(n) + 0.5) / n
    X = (x + s) / 2 ** z * 2 - 1
    Y = 1 - (y + s) / 2 ** z * 2
    lon = X * 180.0
    lat = np.degrees(np.arctan(np.sinh(Y * math.pi)))
    LON, LAT = np.meshgrid(lon, lat)
    return LAT, LON


def zenit_solare(d, lat, lon):
    """angolo zenitale solare (gradi), formule NOAA."""
    doy = d.timetuple().tm_yday
    hr = d.hour + d.minute / 60 + d.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hr - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
           + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hr * 60 + eqt + 4 * lon
    ha = np.radians(tst / 4 - 180)
    la = np.radians(lat)
    cz = np.sin(la) * math.sin(dec) + np.cos(la) * math.cos(dec) * np.cos(ha)
    return np.degrees(np.arccos(np.clip(cz, -1, 1)))


def _png(arr, modo):
    b = io.BytesIO()
    Image.fromarray(arr, modo).save(b, "PNG", compress_level=6)
    return b.getvalue()


_VUOTO = None


def png_trasparente():
    global _VUOTO
    if _VUOTO is None:
        _VUOTO = _png(np.zeros((256, 256, 2), np.uint8), "LA")
    return _VUOTO


def codifica_ir(t_kelvin):
    """T (K) -> grigio 1..255 (freddo = chiaro), 0 = nessun dato. Vedi docstring del modulo."""
    tc = t_kelvin - 273.15
    v = np.rint((60.0 - tc) * 254.0 / 170.0) + 1
    v = np.clip(v, 1, 255)
    v[~np.isfinite(t_kelvin)] = 0
    return v.astype(np.uint8)


def decodifica_ir(v):
    """grigio -> T (°C)."""
    return 60.0 - (np.asarray(v, np.float32) - 1) * 170.0 / 254.0


def colormap_ir_xml():
    """ColorMap in formato GIBS per il decodificatore di static/app.js."""
    righe = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<ColorMaps><ColorMap title="Temperatura di brillanza (nativo)" units="°C">',
             '<ColorMapEntry rgb="0,0,0" transparent="true" nodata="true" value="[]" label="Nessun dato"/>']
    for v in range(1, 256):
        t = 60.0 - (v - 1) * 170.0 / 254.0
        righe.append(f'<ColorMapEntry rgb="{v},{v},{v}" transparent="false" value="[{t - 0.3345:.4f},{t + 0.3345:.4f})" label="{t:.1f} °C"/>')
    righe.append("</ColorMap></ColorMaps>")
    return "\n".join(righe)


_CURVA_X = np.array([0, 25, 55, 100, 255], np.float32) / 255
_CURVA_Y = np.array([0, 90, 140, 175, 255], np.float32) / 255


def zenit_vista(lat, lon, sublon):
    """angolo zenitale di vista del satellite (gradi), terra sferica."""
    cg = np.cos(np.radians(lat)) * np.cos(np.radians(lon - sublon))
    g = np.arccos(np.clip(cg, -1, 1))
    H, R = 42164.0, 6371.0
    return np.degrees(np.arctan2(H * np.sin(g), H * np.cos(g) - R)) + np.degrees(g)


# spessore ottico Rayleigh approssimato per blu 0,47 / verde 0,51 / rosso 0,64 um
_TAU = {"b": 0.19, "g": 0.13, "r": 0.05}


def _colore_vero(r, g, b, sza, vza):
    """correzione Rayleigh semplificata (singolo scattering, fase ~1) + zenit solare limitato + curva contrasto."""
    mus = np.cos(np.radians(np.minimum(sza, 88.0)))
    muv = np.cos(np.radians(np.minimum(vza, 80.0)))
    m = np.minimum(1.0 / np.maximum(mus, 0.1) + 1.0 / np.maximum(muv, 0.15), 5.0)
    # la correzione di zenit e' piena fino a ~70 gradi, poi limitata (niente bordi bruciati)
    corr = 1.0 / np.maximum(mus, 0.35)
    out = []
    for c, k in ((r, "r"), (g, "g"), (b, "b")):
        ray = _TAU[k] * 0.7 * 0.125 * m  # rho_R ~ tau/4 * massa d'aria media (empirica, tarata a vista)
        c = c - np.minimum(ray, 0.9 * np.nan_to_num(c, nan=0))
        c = np.clip(c * corr / 1.0, 0, 1)
        out.append(np.interp(c, _CURVA_X, _CURVA_Y))
    return np.stack(out, -1)


def _sorgenti(sat, bande, d, lat, lon, px_m):
    """dizionario banda -> valori fisici campionati al tassello, + tempo scansione."""
    tipo = SATELLITI[sat]["tipo"]
    val, t = {}, None
    if tipo == "abi":
        basso = px_m >= SOGLIA_2KM_M
        for b in bande:
            v = _var_abi(sat, b, d, basso and RES_ABI[b] < 2.0)
            val[b] = _campiona_abi(v, lat, lon)
            t = t or _abi_tempo(v)
    else:
        for b in bande:
            val[b], tb = _campiona_ahi(d, b, lat, lon)
            t = t or tb
    return val, t or d


def _render(sat, banda, d, z, x, y):
    lat, lon = _latlon_tassello(z, x, y)
    tipo = SATELLITI[sat]["tipo"]
    if tipo == "abi":
        if not _abi_visibile(sat, lat, lon).any():
            return None
    else:
        if not _ahi_scan(lat, lon)[2].any():
            return None
    px_m = 156543.034 * math.cos(math.radians(float(np.clip(np.abs(lat).min(), 0, 85)))) / 2 ** z
    b = banda.lower()
    fis = _bande_fisiche(sat, banda)
    if b in ("truecolor", "geocolor"):
        sza = zenit_solare(d + timedelta(minutes=5), lat, lon)
        giorno = sza < 95
        vis_b = fis[:-1] if b == "geocolor" else fis
        if b == "geocolor":
            ir_b = fis[-1]
            v_ir, t = _sorgenti(sat, [ir_b], d, lat, lon, px_m)
            v = dict(v_ir)
            if giorno.any():
                v.update(_sorgenti(sat, vis_b, d, lat, lon, px_m)[0])
        else:
            v, t = _sorgenti(sat, vis_b, d, lat, lon, px_m)
        if tipo == "abi":
            R, B, N = v.get("C02"), v.get("C01"), v.get("C03")
            G = None if R is None else 0.45 * R + 0.10 * N + 0.45 * B
        else:
            R, B, G0, N = v.get("B03"), v.get("B01"), v.get("B02"), v.get("B04")
            G = None if R is None else 0.93 * G0 + 0.07 * N
        if R is not None:
            rgb_day = _colore_vero(R, G, B, sza, zenit_vista(lat, lon, SATELLITI[sat]["lon"]))
            valid_day = np.isfinite(R) & np.isfinite(G) & np.isfinite(B)
        if b == "truecolor":
            alpha = valid_day & giorno
            rgb = rgb_day
        else:
            T = v[ir_b]
            nub = np.clip((285.0 - T) / (285.0 - 205.0), 0, 1) ** 0.8
            fondo = np.array([0.03, 0.06, 0.16], np.float32)
            notte = fondo * (1 - nub[..., None]) + nub[..., None] * np.array([0.92, 0.94, 1.0], np.float32)
            w = np.clip((92.0 - sza) / 10.0, 0, 1)[..., None]
            if R is not None:
                rgb = np.where(np.isfinite(rgb_day), rgb_day, 0) * w + notte * (1 - w)
            else:
                rgb = notte
            alpha = np.isfinite(T)
        img = np.zeros(lat.shape + (4,), np.uint8)
        img[..., :3] = np.rint(np.nan_to_num(rgb) * 255).clip(0, 255)
        img[..., 3] = np.where(alpha, 255, 0)
        return _png(img, "RGBA")
    # banda singola
    v, t = _sorgenti(sat, fis, d, lat, lon, px_m)
    a = v[fis[0]]
    ir = (tipo == "abi" and int(fis[0][1:]) >= 7) or (tipo == "ahi" and int(fis[0][1:]) >= 7)
    img = np.zeros(lat.shape + (2,), np.uint8)
    ok = np.isfinite(a)
    if ir:
        img[..., 0] = codifica_ir(a)
    else:
        img[..., 0] = np.rint(np.sqrt(np.clip(np.nan_to_num(a), 0, 1)) * 255)
    img[..., 1] = np.where(ok, 255, 0)
    if not ok.any():
        return None
    return _png(img, "LA")


def _path_tassello(sat, banda, d, z, x, y):
    return os.path.join(CACHE, "tasselli", sat, banda.lower(), d.strftime("%Y%m%dT%H%M"), str(z), str(x), f"{y}.png")


def tassello(sat, banda, istante, z, x, y):
    """PNG 256x256 (bytes) in Web Mercator z/x/y. istante None -> ultimo disponibile.
    Fuori dal disco o dato assente -> PNG trasparente. Solleva FileNotFoundError se l'istante non esiste."""
    sat = _sat(sat)
    z, x, y = int(z), int(x), int(y)
    if istante is None:
        istante = ultimo_istante(sat, banda)
        if istante is None:
            raise FileNotFoundError("nessun istante disponibile")
    d = _parse_istante(istante)
    if not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
        return png_trasparente()
    p = _path_tassello(sat, banda, d, z, x, y)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    dati = _render(sat, banda, d, z, x, y)
    if dati is None:
        return png_trasparente()
    _scrivi_atomico(p, dati)
    _segnala_scrittura(len(dati))
    return dati


def prepara(sat, banda, istante):
    """pre-scarica gli indici dei chunk (GOES) per l'istante: chiamarla in background prima di un'animazione."""
    sat = _sat(sat)
    d = _parse_istante(istante)
    if SATELLITI[sat]["tipo"] != "abi":
        return
    lavori = []
    for b in _bande_fisiche(sat, banda):
        lavori.append((b, False))
        if RES_ABI[b] < 2.0:
            lavori.append((b, True))
    # in parallelo: la parte h5py resta serializzata, ma le letture del B-tree si sovrappongono
    # (pool dedicato: POOL e' usato dentro per i blocchi e annidarlo potrebbe bloccarsi)
    for r in list(_POOL_PREP.map(lambda bb: _var_abi(sat, bb[0], d, bb[1]), lavori)):
        pass


def info():
    """catalogo per l'integrazione nel backend (/api/layers)."""
    return {"satelliti": {k: {"nome": v["nome"], "lon": v["lon"]} for k, v in SATELLITI.items()},
            "bande": {k: {"nome": v["nome"], "km": v["km"], "maxZoom": v["maxZoom"]} for k, v in BANDE.items()},
            "passoMin": 10, "ir_codifica": "T[°C] = 60 - (v-1)*170/254, v=0 nessun dato",
            "cache": CACHE, "max_gb": MAX_GB}


# ----------------------------------------------------------------------------------------------------------
# Pulizia cache
# ----------------------------------------------------------------------------------------------------------
_scritti = [0]
_ultima_pulizia = [0.0]
_pul_g = threading.Lock()


def _segnala_scrittura(n):
    _scritti[0] += n
    if _scritti[0] > 512 * 1024 * 1024 or time.time() - _ultima_pulizia[0] > 600:
        _scritti[0] = 0
        _ultima_pulizia[0] = time.time()
        POOL.submit(pulisci_cache)


def pulisci_cache(max_gb=None):
    """cancella i file meno recenti (mtime) di cache/nativo finche' si scende al 90% del limite."""
    lim = (MAX_GB if max_gb is None else float(max_gb)) * 1024 ** 3
    if not _pul_g.acquire(blocking=False):
        return 0
    try:
        files, tot = [], 0
        for rad, _, nomi in os.walk(CACHE):
            for n in nomi:
                p = os.path.join(rad, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                files.append((max(st.st_mtime, st.st_atime), st.st_size, p))
                tot += st.st_size
        if tot <= lim:
            return 0
        files.sort()
        liberati, obiettivo = 0, tot - 0.9 * lim
        for _, sz, p in files:
            if liberati >= obiettivo:
                break
            if p.endswith(".idx.npz") and sz < 2_000_000:
                continue  # indici piccoli e costosi da ricostruire: si tengono
            try:
                os.remove(p)  # i .DAT in memmap aperti falliscono su Windows: si saltano
                liberati += sz
            except OSError:
                pass
        for rad, dirs, nomi in os.walk(CACHE, topdown=False):
            if not dirs and not nomi and rad != CACHE:
                try:
                    os.rmdir(rad)
                except OSError:
                    pass
        return liberati
    finally:
        _pul_g.release()


def dimensione_cache():
    tot = 0
    for rad, _, nomi in os.walk(CACHE):
        for n in nomi:
            try:
                tot += os.path.getsize(os.path.join(rad, n))
            except OSError:
                pass
    return tot


# ----------------------------------------------------------------------------------------------------------
# Meteosat MTG - STUB (non eseguito: mancano le credenziali EUMETSAT)
# ----------------------------------------------------------------------------------------------------------
def mtg_stub(consumer_key=None, consumer_secret=None, istante=None):
    """
    Meteosat Third Generation (MTG-I1, 0°): FCI a 0,5 km (canale vis_06 HR) / 1 km (ir_105 HR) ogni 10 min.
    NON gratuito-anonimo come NOAA: serve un account EUMETSAT (gratuito) e le chiavi API del Data Store
    (https://api.eumetsat.int/api-key/ -> consumer key + consumer secret). Piano d'integrazione:

      pip install eumdac            # client ufficiale EUMETSAT (wheel puro Python)
      import eumdac
      tok = eumdac.AccessToken((consumer_key, consumer_secret))
      ds = eumdac.DataStore(tok)
      coll = ds.get_collection("EO:EUM:DAT:0662")   # FCI Level 1c High Resolution Image Data (HRFI), 0 gradi
      #  (FDHSI = Full Disk High Spectral resolution Imagery, 16 canali 1-2 km: EO:EUM:DAT:0665)
      prodotti = coll.search(dtstart=t0, dtend=t1)    # un prodotto = una scansione ripetuta (RC) di 10 min
      for p in prodotti:
          with p.open() as fsrc: ...                   # zip con ~40 "chunk" netCDF (fasce orizzontali del disco)

    Come per Himawari, il disco e' diviso in chunk-fascia (BODY 0001..0040): si scaricano SOLO quelli che coprono
    il tassello (p.entries elenca i file; p.open(entry=...) scarica il singolo .nc). Dentro ogni .nc:
      data/<canale>/measured/effective_radiance (uint16, scale/offset), start_position_row/end_position_row,
      data/mtg_geos_projection (perspective_point_height 35786400, semi_major_axis 6378137, lon 0, sweep "y"),
      data/<canale>/measured/x, y (angoli di scansione rad, come ABI).
    Proiezione: identica ad _abi_scan() ma sweep_angle_axis="y" -> x = atan(-sy/sx)... cioe' usare _ahi_scan()
    in radianti con H=42164 km, sublon=0. Calibrazione: riflettanza = rad * pi * d^2 / (cos(sza) * irradianza
    solare del canale) per vis_06; IR: T = c2*nu / ln(1 + c1*nu^3/rad) con a,b,nu in data/<canale>/
    measured/radiance_to_bt_conversion_*. Limiti Data Store: download legati all'account, ~ 1 GB per ripetizione
    HRFI completa: con il metodo a chunk-fascia ~ 30-60 MB per una vista Italia.
    """
    raise NotImplementedError("MTG: servono consumer key/secret EUMETSAT (Data Store). Vedi docstring di mtg_stub.")


# ----------------------------------------------------------------------------------------------------------
# CLI di test
# ----------------------------------------------------------------------------------------------------------
def _deg2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def mosaico(sat, banda, istante, lat, lon, z, nx=3, ny=3):
    """immagine PIL RGBA di nx*ny tasselli centrata su lat/lon (per verifiche visive)."""
    fx, fy = _deg2tile(lat, lon, z)
    x0, y0 = int(fx) - nx // 2, int(fy) - ny // 2
    out = Image.new("RGBA", (256 * nx, 256 * ny), (0, 0, 0, 255))
    tempi = []
    for j in range(ny):
        for i in range(nx):
            t = time.time()
            png = tassello(sat, banda, istante, z, (x0 + i) % 2 ** z, y0 + j)
            tempi.append(time.time() - t)
            im = Image.open(io.BytesIO(png)).convert("RGBA")
            out.alpha_composite(im, (256 * i, 256 * j))
    return out, (x0, y0), tempi


def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Tasselli nativi GOES/Himawari")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("istanti"); a.add_argument("sat"); a.add_argument("banda"); a.add_argument("--ore", type=float, default=3)
    a = sp.add_parser("ultimo"); a.add_argument("sat"); a.add_argument("banda")
    a = sp.add_parser("tassello"); a.add_argument("sat"); a.add_argument("banda"); a.add_argument("istante")
    for k in "zxy":
        a.add_argument(k, type=int)
    a.add_argument("-o", default="tassello.png")
    a = sp.add_parser("mosaico"); a.add_argument("sat"); a.add_argument("banda"); a.add_argument("istante")
    a.add_argument("lat", type=float); a.add_argument("lon", type=float); a.add_argument("z", type=int)
    a.add_argument("--n", type=int, default=3); a.add_argument("-o", default="mosaico.png")
    a = sp.add_parser("pulizia"); a.add_argument("--max-gb", type=float, default=None)
    sp.add_parser("colormap"); sp.add_parser("info")
    o = ap.parse_args(argv)
    if o.cmd == "istanti":
        for t in lista_istanti(o.sat, o.banda, o.ore):
            print(t)
    elif o.cmd == "ultimo":
        print(ultimo_istante(o.sat, o.banda))
    elif o.cmd in ("tassello", "mosaico"):
        ist = None if o.istante in ("ultimo", "latest", "-") else o.istante
        if ist is None:
            ist = ultimo_istante(o.sat, o.banda)
        print("istante", ist)
        t = time.time()
        if o.cmd == "tassello":
            png = tassello(o.sat, o.banda, ist, o.z, o.x, o.y)
            with open(o.o, "wb") as f:
                f.write(png)
            print(f"{o.o}: {len(png)} byte in {time.time() - t:.2f} s")
        else:
            im, (x0, y0), tempi = mosaico(o.sat, o.banda, ist, o.lat, o.lon, o.z, o.n, o.n)
            im.save(o.o)
            print(f"{o.o}: tasselli z{o.z} x{x0}.. y{y0}.. totale {time.time() - t:.1f} s; per tassello "
                  f"primo {tempi[0]:.2f} s, mediana {sorted(tempi)[len(tempi) // 2]:.2f} s, max {max(tempi):.2f} s")
        print(f"cache {dimensione_cache() / 1e6:.0f} MB")
    elif o.cmd == "pulizia":
        print(f"liberati {pulisci_cache(o.max_gb) / 1e6:.0f} MB, cache ora {dimensione_cache() / 1e6:.0f} MB")
    elif o.cmd == "colormap":
        print(colormap_ir_xml())
    elif o.cmd == "info":
        print(json.dumps(info(), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:2] == ["servizio-indici"]:
        _servizio_indici()
    else:
        _cli(sys.argv[1:])
