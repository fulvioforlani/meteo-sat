# -*- coding: utf-8 -*-
"""
METEO SAT - backend locale (porta 8952, solo 127.0.0.1).
Solo libreria standard Python 3.14.

Endpoint (vedi SPEC.md):
  GET /api/layers
  GET /api/tile/{layer}/{time}/{z}/{y}/{x}
  GET /api/events
  GET /api/latest/{layer}
  GET /api/health
  GET /api/nativo/{sat}/{banda}/{istante}/{z}/{y}/{x}   (tasselli risoluzione piena, nativo.py)
  GET /api/nativo/colormap.xml | info | prepara/{sat}/{banda}?t=ist1,ist2
  /            -> static/index.html   (static/* serviti come file)

Fonti: NASA GIBS WMTS epsg3857/best (+ GIBS WMS per i layer vettoriali dei fuochi),
EUMETSAT EUMETView WMS (Meteosat MTG/MSG, Europa-Africa), EONET v3, GDACS.
"""
import json
import logging
import logging.handlers
import math
import mimetypes
import os
import re
import gzip
import http.client
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import traduzioni  # tutte le traduzioni italiane delle stringhe esterne (titoli layer, eventi, paesi)
import legende  # legende a colori di tutti i layer (definizioni in italiano, scale reali): /api/legende

VERSIONE = "1.0"
HOST = "127.0.0.1"
PORTA = int(os.environ.get("METEO_PORTA", "8952"))

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
CACHE = os.path.join(BASE, "cache")
CACHE_TILE = os.path.join(CACHE, "tiles")
LOGFILE = os.path.join(BASE, "meteo.log")

GIBS_WMTS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
GIBS_CAPS_URL = GIBS_WMTS + "/wmts.cgi?SERVICE=WMTS&request=GetCapabilities"
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi"
EUM_WMS = "https://view.eumetsat.int/geoserver/wms"
EUM_CAPS_URL = EUM_WMS + "?service=WMS&request=GetCapabilities&version=1.3.0"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30"
GDACS_TC_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=TC"

CAPS_TTL = 6 * 3600
EVENTI_TTL = 600
UA = "MeteoSat-locale/1.0 (uso personale; contatto locale)"

NS_WMTS = {"w": "http://www.opengis.net/wmts/1.0", "ows": "http://www.opengis.net/ows/1.1"}
NS_WMS = {"w": "http://www.opengis.net/wms"}
XLINK = "{http://www.w3.org/1999/xlink}href"
R_MERC = 20037508.342789244

os.makedirs(CACHE_TILE, exist_ok=True)

# ---------------------------------------------------------------- nativo (opzionale)
# Tasselli alla risoluzione piena dai file originali GOES/Himawari (nativo.py). Serve numpy, pillow,
# requests; h5py solo per GOES. Se qualcosa manca il server funziona come prima, senza quel gruppo.
try:
    import nativo
    NATIVO_ERR = None
except Exception as _e:  # dipendenze mancanti o file assente
    nativo = None
    NATIVO_ERR = "%s: %s" % (type(_e).__name__, _e)
try:
    import h5py  # noqa: F401  (serve a nativo per leggere gli indici dei file GOES)
    H5PY_OK = True
except Exception:
    H5PY_OK = False

# ---------------------------------------------------------------- log
log = logging.getLogger("meteo")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(LOGFILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
log.addHandler(_h)
if sys.stderr is not None and "--console" in sys.argv:
    log.addHandler(logging.StreamHandler(sys.stderr))

STATS = {"avvio": time.time(), "richieste": 0, "tile_cache": 0, "tile_upstream": 0,
         "tile_vuoti": 0, "errori_upstream": 0, "errori_interni": 0}
_stats_lock = threading.Lock()


def stat(k, n=1):
    with _stats_lock:
        STATS[k] = STATS.get(k, 0) + n


# ---------------------------------------------------------------- PNG trasparente
def _png_trasparente(w=256, h=256):
    raw = b"".join(b"\x00" + b"\x00" * (w * 4) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


PNG_VUOTO = _png_trasparente()

# ---------------------------------------------------------------- HTTP upstream (pool limitato)
SEMAFORI = {
    "gibs.earthdata.nasa.gov": threading.BoundedSemaphore(10),  # = richieste parallele del browser (5+5)
    "view.eumetsat.int": threading.BoundedSemaphore(4),
}
SEM_ALTRI = threading.BoundedSemaphore(6)


class Mancante(Exception):
    """Risorsa non disponibile a monte (404/400/eccezione WMS)."""


# connessioni HTTPS riusate (keep-alive) verso i server di tasselli: niente nuova stretta di mano TLS
# a ogni tassello. Pool semplice per host; su qualsiasi problema si ripiega su urllib.
KEEPALIVE_HOST = {"gibs.earthdata.nasa.gov", "view.eumetsat.int"}
_conn_pool = {}
_conn_lock = threading.Lock()


def _scarica_keepalive(url, timeout, nuova=False):
    """(status, bytes, headers) con connessione riusata; solleva eccezione su errori di rete.
    Una connessione riusata puo' essere stata chiusa dal server mentre era ferma: in quel caso
    si riprova subito una volta con una connessione nuova (non conta come errore)."""
    u = urllib.parse.urlsplit(url)
    c = None
    if not nuova:
        with _conn_lock:
            lista = _conn_pool.setdefault(u.hostname, [])
            c = lista.pop() if lista else None
    riusata = c is not None
    if c is None:
        c = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout)
    else:
        c.timeout = timeout
        if c.sock is not None:
            c.sock.settimeout(timeout)
    try:
        c.request("GET", (u.path or "/") + ("?" + u.query if u.query else ""),
                  headers={"User-Agent": UA, "Accept": "*/*", "Connection": "keep-alive"})
        r = c.getresponse()
        dati = r.read()
        riusa = not r.will_close
    except Exception:
        c.close()
        if riusata:
            return _scarica_keepalive(url, timeout, nuova=True)
        raise
    if riusa:
        with _conn_lock:
            lista = _conn_pool.setdefault(u.hostname, [])
            if len(lista) < 12:
                lista.append(c)
                c = None
    if c is not None:
        c.close()
    return r.status, dati, r.headers


def scarica(url, timeout=20, tentativi=2, attesa_pool=30, richiedi_immagine=False):
    """GET con pool limitato per host, 1 retry su errori di rete/5xx.
    Ritorna (bytes, content_type, headers). Solleva Mancante per 4xx o risposta non-immagine."""
    host = urllib.parse.urlsplit(url).hostname or ""
    sem = SEMAFORI.get(host, SEM_ALTRI)
    if not sem.acquire(timeout=attesa_pool):
        raise TimeoutError("pool upstream saturo")
    try:
        ultimo = None
        for i in range(tentativi):
            try:
                st = None
                if host in KEEPALIVE_HOST:
                    st, dati, hdr = _scarica_keepalive(url, timeout)
                if st is not None and not (300 <= st < 400):  # redirect: lo segue urllib qui sotto
                    if st >= 500:
                        raise urllib.error.HTTPError(url, st, "HTTP %d" % st, hdr, None)
                    if 400 <= st < 500:
                        raise Mancante("HTTP %d" % st)
                    ct = (hdr.get("Content-Type") or "").lower()
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        dati = r.read()
                        ct = (r.headers.get("Content-Type") or "").lower()
                        hdr = r.headers
                if richiedi_immagine and not ct.startswith("image/"):
                    raise Mancante("risposta non immagine (%s): %s" % (ct, dati[:200]))
                return dati, ct, hdr
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    raise Mancante("HTTP %d" % e.code)
                ultimo = e
            except Mancante:
                raise
            except Exception as e:  # rete, timeout, reset
                ultimo = e
            if i + 1 < tentativi:
                time.sleep(0.6)
        stat("errori_upstream")
        raise ultimo if ultimo else RuntimeError("errore sconosciuto")
    finally:
        sem.release()


# ---------------------------------------------------------------- utilità tempo
def ora_utc():
    return datetime.now(timezone.utc)


_RE_DUR = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def durata_min(p):
    m = _RE_DUR.match(p or "")
    if not m:
        return None
    y, mo, w, d, h, mi, s = m.groups()
    tot = (int(y or 0) * 525600 + int(mo or 0) * 43200 + int(w or 0) * 10080 + int(d or 0) * 1440
           + int(h or 0) * 60 + int(mi or 0) + float(s or 0) / 60)
    return int(round(tot)) or None


def parse_iso(s):
    """Accetta 2026-10-11, 2026-10-11T17:50, ...:00Z, .000Z, +00:00. Ritorna datetime UTC o None."""
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1]
    s = re.sub(r"[+-]00:?00$", "", s)
    s = re.sub(r"\.\d+$", "", s)
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def fmt_dt(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_d(d):
    return d.strftime("%Y-%m-%d")


def floor_passo(d, passo):
    if passo and 0 < passo < 1440 and 1440 % passo == 0:
        minuti = d.hour * 60 + d.minute
        minuti -= minuti % passo
        return d.replace(hour=minuti // 60, minute=minuti % 60, second=0, microsecond=0)
    return d.replace(second=0, microsecond=0)


def norm_iso(s):
    d = parse_iso(s)
    return fmt_dt(d) if d else s


# ---------------------------------------------------------------- catalogo curato
# Gruppi in ordine di visualizzazione
GRUPPI = [
    ("visibile", "Visibile / GeoColor"),
    ("infrarosso", "Infrarosso"),
    ("vapore", "Vapore acqueo / masse d'aria"),
    ("temporali", "Nubi e temporali"),
    ("polveri", "Polveri e cenere"),
    ("metop", "Polari Metop (AVHRR, orbite accumulate)"),
    ("incendi", "Incendi"),
    ("altarisoluzione", "Alta risoluzione (zoom stretto)"),
    ("inquinamento", "Aerosol e inquinamento"),
    ("notte", "Notte"),
    ("oceano", "Oceano (temperatura del mare)"),
    ("precipitazioni", "Precipitazioni"),
    ("neve", "Neve e ghiaccio"),
    ("sfondi", "Sfondi"),
    ("riferimenti", "Riferimenti (coste, confini, nomi)"),
]

# Satelliti geostazionari: longitudine sub-satellite
GEO_LON = {"MTG": 0.0, "MSG": 0.0, "MSG-IODC": 45.5, "GOES-East": -75.2, "GOES-West": -137.0, "Himawari": 140.7}


def L(gruppo, id, nome, descr, **kw):
    d = {"gruppo": gruppo, "id": id, "nome": nome, "descr": descr}
    d.update(kw)
    return d


# fonte: "gibs" (WMTS, default) | "eum" (EUMETView WMS, remoto=nome WMS)
# tipo: base|overlay ; categoria: geostazionario|polare|prodotto|sfondo|riferimento
CURATI = [
    # ---------------- Visibile / GeoColor
    L("visibile", "MTG_GeoColour", "GeoColor Meteosat MTG", "Colori naturali di giorno; di notte nubi dall'infrarosso con luci delle città. Europa, Africa, Atlantico. Ogni 10 minuti.",
      fonte="eum", remoto="mtg_fd:rgb_geocolour", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("visibile", "MTG_TrueColour", "Colori reali Meteosat MTG", "Immagine a colori reali (solo di giorno), Europa-Africa, ogni 10 minuti.",
      fonte="eum", remoto="mtg_fd:rgb_truecolour", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("visibile", "MTG_VIS06", "Visibile 0,6 µm Meteosat MTG", "Canale visibile ad alta risoluzione (scala di grigi, solo di giorno), Europa-Africa.",
      fonte="eum", remoto="mtg_fd:vis06_hrfi", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=8, risKm=0.5, grigi=True),
    L("visibile", "MSG_HRV_Europa", "Visibile ad alta risoluzione (HRV) Europa, Meteosat MSG", "Composito ad alta risoluzione sull'Europa (Meteosat Seconda Generazione), ogni 15 minuti.",
      fonte="eum", remoto="msg_fes:rgb_eview", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("visibile", "GOES-East_ABI_GeoColor", "GeoColor GOES-East", "Colori naturali di giorno, nubi IR e luci notturne. Americhe e Atlantico, ogni 10 minuti.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=1.0),
    L("visibile", "GOES-West_ABI_GeoColor", "GeoColor GOES-West", "Colori naturali di giorno, nubi IR e luci notturne. Pacifico orientale e Americhe, ogni 10 minuti.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=1.0),
    L("visibile", "GOES-East_ABI_Band2_Red_Visible_1km", "Visibile rosso GOES-East", "Canale visibile 0,64 µm (scala di grigi), dettaglio delle nubi di giorno.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=0.5, grigi=True),
    L("visibile", "GOES-West_ABI_Band2_Red_Visible_1km", "Visibile rosso GOES-West", "Canale visibile 0,64 µm (scala di grigi), dettaglio delle nubi di giorno.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=0.5, grigi=True),
    L("visibile", "Himawari_AHI_Band3_Red_Visible_1km", "Visibile rosso Himawari", "Canale visibile 0,64 µm (scala di grigi). Asia orientale, Oceania e Pacifico occidentale.",
      sat="Himawari", tipo="base", categoria="geostazionario", risKm=0.5, grigi=True),
    L("visibile", "VIIRS_NOAA21_CorrectedReflectance_TrueColor", "Colori reali VIIRS NOAA-21", "Mosaico giornaliero a colori reali da satellite polare, 250 m.",
      sat="NOAA-21", tipo="base", categoria="polare", risKm=0.25),
    L("visibile", "VIIRS_NOAA20_CorrectedReflectance_TrueColor", "Colori reali VIIRS NOAA-20", "Mosaico giornaliero a colori reali da satellite polare, 250 m.",
      sat="NOAA-20", tipo="base", categoria="polare", risKm=0.25),
    L("visibile", "VIIRS_SNPP_CorrectedReflectance_TrueColor", "Colori reali VIIRS Suomi NPP", "Mosaico giornaliero a colori reali da satellite polare, 250 m.",
      sat="Suomi NPP", tipo="base", categoria="polare", risKm=0.25),
    L("visibile", "MODIS_Terra_CorrectedReflectance_TrueColor", "Colori reali MODIS Terra", "Mosaico giornaliero (passaggio del mattino), 250 m.",
      sat="Terra", tipo="base", categoria="polare", risKm=0.25),
    L("visibile", "MODIS_Aqua_CorrectedReflectance_TrueColor", "Colori reali MODIS Aqua", "Mosaico giornaliero (passaggio del pomeriggio), 250 m.",
      sat="Aqua", tipo="base", categoria="polare", risKm=0.25),

    # ---------------- Infrarosso
    L("infrarosso", "MTG_IR105", "Infrarosso 10,5 µm Meteosat MTG", "Temperatura delle cime delle nubi, giorno e notte (scala di grigi: chiaro = freddo/alto). Europa-Africa, ogni 10 minuti.",
      fonte="eum", remoto="mtg_fd:ir105_hrfi", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0, grigi=True),
    L("infrarosso", "MSG_IR108", "Infrarosso 10,8 µm Meteosat MSG", "Infrarosso termico classico di Meteosat, archivio dal 2020, ogni 15 minuti.",
      fonte="eum", remoto="msg_fes:ir108", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0, grigi=True),
    L("infrarosso", "MSG_IR039", "Infrarosso 3,9 µm Meteosat MSG", "Infrarosso a onda corta: nebbie notturne e punti caldi (incendi).",
      fonte="eum", remoto="msg_fes:ir039", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0, grigi=True),
    L("infrarosso", "GOES-East_ABI_Band13_Clean_Infrared", "Infrarosso 10,3 µm GOES-East", "Infrarosso \"pulito\" (scala di grigi), giorno e notte. Americhe e Atlantico.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=2.0, grigi=True),
    L("infrarosso", "GOES-West_ABI_Band13_Clean_Infrared", "Infrarosso 10,3 µm GOES-West", "Infrarosso \"pulito\" (scala di grigi), giorno e notte. Pacifico orientale.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=2.0, grigi=True),
    L("infrarosso", "Himawari_AHI_Band13_Clean_Infrared", "Infrarosso 10,4 µm Himawari", "Infrarosso \"pulito\" (scala di grigi), giorno e notte. Asia-Pacifico.",
      sat="Himawari", tipo="base", categoria="geostazionario", risKm=2.0, grigi=True),
    L("infrarosso", "MODIS_Terra_Cloud_Top_Temp_Day", "Temperatura cima nubi MODIS Terra", "Temperatura delle cime delle nubi stimata, prodotto giornaliero diurno.",
      sat="Terra", tipo="overlay", categoria="polare", risKm=5.0),

    # ---------------- Vapore / Air Mass
    L("vapore", "MSG_WV062", "Vapore acqueo 6,2 µm Meteosat MSG", "Umidità della media-alta troposfera (scala di grigi): correnti a getto, saccature, gocce fredde.",
      fonte="eum", remoto="msg_fes:wv062", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0, grigi=True),
    L("vapore", "MSG_AirMass", "Masse d'aria RGB Meteosat MSG", "RGB masse d'aria: rosso = aria secca stratosferica (ciclogenesi), verde = aria tropicale, blu/viola = aria polare.",
      fonte="eum", remoto="msg_fes:rgb_airmass", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("vapore", "MSG_TropicalAirmass", "Masse d'aria tropicali RGB Meteosat MSG", "Variante dell'RGB masse d'aria ottimizzata per le regioni tropicali.",
      fonte="eum", remoto="msg_fes:rgb_tropicalairmass", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("vapore", "GOES-East_ABI_Air_Mass", "Masse d'aria RGB GOES-East", "RGB masse d'aria: rosso = aria secca, verde = tropicale, blu = polare.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=2.0),
    L("vapore", "GOES-West_ABI_Air_Mass", "Masse d'aria RGB GOES-West", "RGB masse d'aria: rosso = aria secca, verde = tropicale, blu = polare.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=2.0),
    L("vapore", "Himawari_AHI_Air_Mass", "Masse d'aria RGB Himawari", "RGB masse d'aria: rosso = aria secca, verde = tropicale, blu = polare.",
      sat="Himawari", tipo="base", categoria="geostazionario", risKm=2.0),
    L("vapore", "MODIS_Terra_Water_Vapor_5km_Day", "Vapore acqueo totale MODIS Terra", "Contenuto di vapore acqueo in colonna, prodotto giornaliero.",
      sat="Terra", tipo="overlay", categoria="polare", risKm=5.0),

    # ---------------- Nubi e temporali
    L("temporali", "MTG_Fulmini", "Fulmini (Lightning Imager MTG)", "Area dei lampi accumulata ogni 5 minuti: celle temporalesche attive.",
      fonte="eum", remoto="mtg_fd:li_afa", sat="MTG", tipo="overlay", categoria="geostazionario", maxZoom=7, risKm=4.5),
    L("temporali", "MSG_RDT", "Temporali in rapido sviluppo (RDT)", "Celle convettive individuate e tracciate automaticamente (NWC SAF).",
      fonte="eum", remoto="msg_fes:rdt", sat="MSG", tipo="overlay", categoria="geostazionario", maxZoom=7, risKm=3.0),
    L("temporali", "MSG_Convection", "Convezione RGB Meteosat MSG", "Giallo intenso = nubi convettive con forti correnti ascensionali.",
      fonte="eum", remoto="msg_fes:rgb_convection", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("temporali", "MTG_CloudType", "Tipo di nubi RGB MTG", "Distingue nubi basse, medie, alte e ghiaccio.",
      fonte="eum", remoto="mtg_fd:rgb_cloudtype", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("temporali", "MTG_CloudPhase", "Fase delle nubi RGB MTG", "Nubi d'acqua contro nubi di ghiaccio.",
      fonte="eum", remoto="mtg_fd:rgb_cloudphase", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("temporali", "MTG_Nebbia", "Nebbia / nubi basse RGB MTG", "Nebbie e strati bassi, utile di notte (es. Pianura Padana).",
      fonte="eum", remoto="mtg_fd:rgb_fog", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=2.0),
    L("temporali", "MSG_IODC_Nebbia", "Nebbia / nubi basse RGB Meteosat Oceano Indiano",
      "Meteosat sull'Oceano Indiano (45,5°E): Medio Oriente, India, Africa orientale. Fatto per la NOTTE: nebbia e strati bassi "
      "verde-azzurro chiaro, nubi alte e spesse rosso-arancio, cirri sottili blu scuro, cielo sereno viola-rosa. "
      "Di giorno è poco affidabile (il canale 3,9 µm riflette il sole). Ogni 15 minuti, ~3 km al centro della vista, ritardo ~30 min.",
      fonte="eum", remoto="msg_iodc:rgb_fog", sat="MSG-IODC", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("temporali", "MSG_AltezzaNubi", "Altezza cima nubi Meteosat MSG", "Altezza stimata della sommità delle nubi.",
      fonte="eum", remoto="msg_fes:cth", sat="MSG", tipo="overlay", categoria="geostazionario", maxZoom=6, risKm=3.0),

    # ---------------- Polveri e cenere
    L("polveri", "MTG_Dust", "Polveri RGB Meteosat MTG", "Rosa-magenta = polvere sahariana in sospensione. Europa-Africa, ogni 10 minuti.",
      fonte="eum", remoto="mtg_fd:rgb_dust", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=2.0),
    L("polveri", "MSG_Dust", "Polveri RGB Meteosat MSG", "Rosa-magenta = polvere. Archivio dal 2020.",
      fonte="eum", remoto="msg_fes:rgb_dust", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("polveri", "MSG_IODC_Dust", "Polveri RGB Meteosat Oceano Indiano",
      "Stesso RGB polveri del Meteosat a 0° ma dal satellite sull'Oceano Indiano (45,5°E): Arabia, Iran, Pakistan, India, "
      "Corno d'Africa. Rosa-magenta = polvere sospesa; rosso scuro = nubi alte e spesse; nero = cirri sottili; "
      "deserti azzurro-violetto di giorno. Ogni 15 minuti, ~3 km al centro della vista, ritardo ~30 min.",
      fonte="eum", remoto="msg_iodc:rgb_dust", sat="MSG-IODC", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("polveri", "GeoRing_Dust", "Polveri RGB mosaico mondiale (anello geostazionario)",
      "Mosaico di tutto il globo (tranne i poli) fatto con i 5 geostazionari: Meteosat 0°, Meteosat Oceano Indiano, "
      "Himawari, GOES-West e GOES-East. Rosa-magenta = polvere, rosso scuro = nubi alte. Si vedono le giunture fra un "
      "satellite e l'altro. Solo ogni 3 ore, ritardo ~3 ore, risoluzione ~3-5 km.",
      fonte="eum", remoto="mumi:wideareacoverage_rgb_dust", sat="Multimissione", tipo="base", categoria="prodotto",
      maxZoom=6, risKm=4.0),
    L("polveri", "MSG_Cenere", "Cenere vulcanica RGB Meteosat MSG", "Rosso-arancio = cenere vulcanica e SO2.",
      fonte="eum", remoto="msg_fes:rgb_ash", sat="MSG", tipo="base", categoria="geostazionario", maxZoom=6, risKm=3.0),
    L("polveri", "GOES-East_ABI_Dust", "Polveri RGB GOES-East", "Rosa-magenta = polvere in sospensione.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=2.0),
    L("polveri", "GOES-West_ABI_Dust", "Polveri RGB GOES-West", "Rosa-magenta = polvere in sospensione.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=2.0),
    L("polveri", "AIRS_L2_Dust_Score_Day", "Indice polveri AIRS (giorno)", "Indice di presenza di polvere dal sondatore AIRS.",
      sat="Aqua", tipo="overlay", categoria="polare", risKm=13.5),
    L("polveri", "AIRS_L2_Dust_Score_Night", "Indice polveri AIRS (notte)", "Indice di presenza di polvere dal sondatore AIRS, passaggi notturni.",
      sat="Aqua", tipo="overlay", categoria="polare", risKm=13.5),

    # ---------------- Polari Metop (AVHRR) - verificati con tasselli reali l'11-10-26
    # Orbite irregolari (~101 min): il Capabilities dichiara PT1H40M ma gli istanti veri sono la fine di ogni orbita;
    # EUMETView risponde col dato piu' vicino (nearestValue). orbite=True: niente arrotondamento dell'istante e
    # ultimo dato letto dal Capabilities del singolo layer (piccolo, aggiornato) invece che con le sonde.
    # Metop-A (eps:m02_*) escluso: ultimo dato 15-11-21 07:46 UTC (satellite dismesso), oggi EUMETView risponde 502.
    L("metop", "MetopB_AVHRR_ColoriNebbia", "Colori naturali + nebbia Metop-B (6 orbite)",
      "Mosaico mondiale delle ultime 6 orbite AVHRR di Metop-B (circa 10 ore). Parte di GIORNO a colori naturali: vegetazione verde, "
      "deserti marroni, mare nero, nubi d'acqua bianche, nubi di ghiaccio e neve azzurro-turchese. Parte di NOTTE con l'RGB nebbia: "
      "nebbia e nubi basse verde-azzurro chiaro, nubi alte e spesse rosso-arancio (puntini gialli = cime freddissime), cielo sereno "
      "viola-rosa. Le strisce si vedono: ogni orbita ha un orario diverso. ~1 km al nadir (fino a 3-5 km ai bordi della striscia), "
      "nuova orbita ogni ~100 min, ritardo 3-5 ore.",
      fonte="eum", remoto="eps:m01_rgb_natural_fog", sat="Metop-B", tipo="base", categoria="polare", maxZoom=8, risKm=1.1,
      orbite=True),
    L("metop", "MetopC_AVHRR_ColoriNebbia", "Colori naturali + nebbia Metop-C (6 orbite)",
      "Come Metop-B ma dal satellite gemello Metop-C (stessa orbita, sfasata di circa mezza orbita): riempie i buchi fra le strisce "
      "di Metop-B. Giorno a colori naturali, notte con l'RGB nebbia (nebbia verde-azzurro chiaro, nubi alte rosso-arancio). "
      "~1 km al nadir, nuova orbita ogni ~100 min, ritardo 4-6 ore.",
      fonte="eum", remoto="eps:m03_rgb_natural_fog", sat="Metop-C", tipo="base", categoria="polare", maxZoom=8, risKm=1.1,
      orbite=True),
    L("metop", "MetopB_AVHRR_Nubi", "Nubi RGB Metop-B (accumulo del giorno)",
      "RGB AVHRR canali 1-2-4 accumulato su tutte le orbite DIURNE del giorno UTC (la parte notturna resta vuota). "
      "Nubi spesse e fredde bianche, nubi basse e calde giallo-crema, cirri sottili azzurro-blu, vegetazione verde, mare blu scuro. "
      "Utile per i temporali (incudini bianche). ~1 km al nadir, si completa orbita dopo orbita, ritardo 3-5 ore.",
      fonte="eum", remoto="eps:m01_rgb_124", sat="Metop-B", tipo="base", categoria="polare", maxZoom=8, risKm=1.1, orbite=True),
    L("metop", "MetopC_AVHRR_Nubi", "Nubi RGB Metop-C (accumulo del giorno)",
      "Come il precedente ma da Metop-C: nubi spesse bianche, basse giallo-crema, cirri azzurro-blu, vegetazione verde, mare blu scuro. "
      "~1 km al nadir, ritardo 4-6 ore.",
      fonte="eum", remoto="eps:m03_rgb_124", sat="Metop-C", tipo="base", categoria="polare", maxZoom=8, risKm=1.1, orbite=True),
    L("metop", "MetopB_AVHRR_IR108", "Infrarosso 10,8 µm Metop-B (6 orbite)",
      "Temperatura di brillanza delle ultime 6 orbite AVHRR, giorno e notte, in scala di grigi fissa 220-310 K "
      "(bianco = freddo/nubi alte, nero = caldo): si possono applicare le palette. Copre anche i poli, dove i geostazionari non vedono. "
      "Prodotto più sfumato del colori naturali (~2-4 km), nuova orbita ogni ~100 min, ritardo 3-5 ore.",
      fonte="eum", remoto="eps:m01_ir108", sat="Metop-B", tipo="base", categoria="polare", maxZoom=7, risKm=2.0, grigi=True,
      orbite=True),
    L("metop", "MetopC_AVHRR_IR108", "Infrarosso 10,8 µm Metop-C (6 orbite)",
      "Come il precedente ma da Metop-C: scala di grigi 220-310 K (bianco = freddo), palette applicabili. ~2-4 km, ritardo 4-6 ore.",
      fonte="eum", remoto="eps:m03_ir108", sat="Metop-C", tipo="base", categoria="polare", maxZoom=7, risKm=2.0, grigi=True,
      orbite=True),
    L("metop", "MetopB_Tracce", "Impronta delle orbite Metop-B",
      "Contorni arancioni (con velatura) delle strisce osservate nelle orbite più recenti di Metop-B: dice quali zone del mosaico "
      "sono fresche e dove passano le giunture. Da sovrapporre ai layer Metop-B.",
      fonte="eum", remoto="eps:m01_orbital_tracks", sat="Metop-B", tipo="overlay", categoria="polare", maxZoom=8, risKm=1.1,
      orbite=True, passo=100),

    # ---------------- Incendi
    L("incendi", "MTG_FireTemp", "Temperatura incendi RGB MTG", "Fuochi attivi in giallo-rosso, ogni 10 minuti su Europa-Africa.",
      fonte="eum", remoto="mtg_fd:rgb_firetemperature", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=2.0),
    L("incendi", "MTG_FRP", "Potenza radiativa fuochi (FRP) MTG", "Punti di incendio attivo con intensità.",
      fonte="eum", remoto="mtg_fd:frp", sat="MTG", tipo="overlay", categoria="geostazionario", maxZoom=8, risKm=2.0),
    L("incendi", "GOES-East_ABI_FireTemp", "Temperatura incendi RGB GOES-East", "Fuochi attivi in giallo-rosso.",
      sat="GOES-East", tipo="base", categoria="geostazionario", risKm=2.0),
    L("incendi", "GOES-West_ABI_FireTemp", "Temperatura incendi RGB GOES-West", "Fuochi attivi in giallo-rosso.",
      sat="GOES-West", tipo="base", categoria="geostazionario", risKm=2.0),
    L("incendi", "VIIRS_NOAA20_Thermal_Anomalies_375m_All", "Fuochi attivi VIIRS NOAA-20 (375 m)", "Anomalie termiche e incendi, giorno e notte (punti).",
      sat="NOAA-20", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=10),
    L("incendi", "VIIRS_NOAA21_Thermal_Anomalies_375m_All", "Fuochi attivi VIIRS NOAA-21 (375 m)", "Anomalie termiche e incendi, giorno e notte (punti).",
      sat="NOAA-21", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=10),
    L("incendi", "VIIRS_SNPP_Thermal_Anomalies_375m_All", "Fuochi attivi VIIRS Suomi NPP (375 m)", "Anomalie termiche e incendi, giorno e notte (punti).",
      sat="Suomi NPP", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=10),
    L("incendi", "MODIS_Combined_Thermal_Anomalies_All", "Fuochi attivi MODIS (1 km)", "Anomalie termiche Terra+Aqua (punti).",
      sat="Terra/Aqua", tipo="overlay", categoria="polare", risKm=1.0, maxZoom=9),

    # ---------------- Alta risoluzione (zoom stretto) - verificati con tile reali l'11-10-26
    L("altarisoluzione", "HLS_S30_Nadir_BRDF_Adjusted_Reflectance", "Sentinel-2 colori reali 30 m (HLS)",
      "Massimo dettaglio gratuito: città, campi, cicatrici di incendi, pennacchi di fumo. Ritardo di circa 2 giorni; "
      "copertura a strisce: ogni giorno solo le zone sorvolate (passaggio ogni 2-5 giorni), se la mappa è vuota torna indietro di qualche giorno.",
      sat="Sentinel-2", tipo="base", categoria="polare", risKm=0.03),
    L("altarisoluzione", "HLS_L30_Nadir_BRDF_Adjusted_Reflectance", "Landsat 8/9 colori reali 30 m (HLS)",
      "Come Sentinel-2 ma da Landsat: ritardo di circa 2-3 giorni, strisce più rade (passaggio ogni 8 giorni circa). "
      "Se la mappa è vuota torna indietro di qualche giorno.",
      sat="Landsat 8/9", tipo="base", categoria="polare", risKm=0.03),
    L("altarisoluzione", "OPERA_L3_DIST-ALERT-HLS_Color_Index", "Disturbo vegetazione 30 m (OPERA, da HLS)",
      "Aree dove la vegetazione è sparita di recente: aree bruciate, tagli, frane. Ritardo di circa 2 giorni, copertura a strisce come HLS.",
      sat="Landsat/Sentinel-2", tipo="overlay", categoria="prodotto", risKm=0.03),
    L("altarisoluzione", "VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1", "Fuoco e fumo VIIRS NOAA-20 (falsi colori)",
      "Bande M11-I2-I1: aree bruciate rosso-bruno, vegetazione verde, fuochi attivi arancio vivo, il fumo diventa quasi trasparente. "
      "375-750 m, aggiornato in giornata (1 passaggio diurno).",
      sat="NOAA-20", tipo="base", categoria="polare", risKm=0.375),
    L("altarisoluzione", "VIIRS_NOAA21_CorrectedReflectance_BandsM11-I2-I1", "Fuoco e fumo VIIRS NOAA-21 (falsi colori)",
      "Bande M11-I2-I1: aree bruciate rosso-bruno, vegetazione verde, fuochi attivi arancio vivo. 375-750 m, aggiornato in giornata.",
      sat="NOAA-21", tipo="base", categoria="polare", risKm=0.375),
    L("altarisoluzione", "VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1", "Fuoco e fumo VIIRS Suomi NPP (falsi colori)",
      "Bande M11-I2-I1: aree bruciate rosso-bruno, vegetazione verde. 375-750 m, aggiornato in giornata.",
      sat="Suomi NPP", tipo="base", categoria="polare", risKm=0.375),
    L("altarisoluzione", "VIIRS_NOAA21_SurfaceReflectance_BandsI1-I2-I1", "Banda I VIIRS NOAA-21 375 m (falsi colori)",
      "Solo bande I a 375 m (rosso + vicino infrarosso): vegetazione verde brillante, bruciato e città scuri. Ritardo circa 1 giorno.",
      sat="NOAA-21", tipo="base", categoria="polare", risKm=0.375),
    L("altarisoluzione", "VIIRS_SNPP_SurfaceReflectance_BandsI1-I2-I1", "Banda I VIIRS Suomi NPP 375 m (falsi colori)",
      "Solo bande I a 375 m (rosso + vicino infrarosso): vegetazione verde brillante, bruciato e città scuri. Ritardo circa 1 giorno.",
      sat="Suomi NPP", tipo="base", categoria="polare", risKm=0.375),
    L("altarisoluzione", "MODIS_Terra_CorrectedReflectance_Bands721", "Fuoco e fumo MODIS Terra (bande 7-2-1)",
      "Aree bruciate rosso-bruno, fuochi attivi arancio, fumo quasi trasparente. 250-500 m, passaggio del mattino, aggiornato in giornata.",
      sat="Terra", tipo="base", categoria="polare", risKm=0.25),
    L("altarisoluzione", "MODIS_Aqua_CorrectedReflectance_Bands721", "Fuoco e fumo MODIS Aqua (bande 7-2-1)",
      "Aree bruciate rosso-bruno, fuochi attivi arancio. 250-500 m, passaggio del pomeriggio, aggiornato in giornata.",
      sat="Aqua", tipo="base", categoria="polare", risKm=0.25),
    L("altarisoluzione", "VIIRS_NOAA20_Thermal_Anomalies_375m_Day", "Fuochi diurni VIIRS NOAA-20 (375 m)",
      "Punti di incendio rilevati nel passaggio diurno; disegnati a qualsiasi zoom, da sovrapporre a HLS o VIIRS. Aggiornato in giornata.",
      sat="NOAA-20", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=14),
    L("altarisoluzione", "VIIRS_NOAA20_Thermal_Anomalies_375m_Night", "Fuochi notturni VIIRS NOAA-20 (375 m)",
      "Punti di incendio rilevati nel passaggio notturno (i più affidabili: niente riflessi del sole). Aggiornato in giornata.",
      sat="NOAA-20", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=14),
    L("altarisoluzione", "VIIRS_NOAA21_Thermal_Anomalies_375m_Day", "Fuochi diurni VIIRS NOAA-21 (375 m)",
      "Punti di incendio rilevati nel passaggio diurno di NOAA-21. Aggiornato in giornata.",
      sat="NOAA-21", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=14),
    L("altarisoluzione", "VIIRS_NOAA21_Thermal_Anomalies_375m_Night", "Fuochi notturni VIIRS NOAA-21 (375 m)",
      "Punti di incendio rilevati nel passaggio notturno di NOAA-21.",
      sat="NOAA-21", tipo="overlay", categoria="polare", risKm=0.375, maxZoom=14),

    # ---------------- Aerosol e inquinamento
    L("inquinamento", "TROPOMI_L2_Nitrogen_Dioxide_Tropospheric_Column", "NO2 troposferico TROPOMI (globale)", "Biossido di azoto da traffico e industria, Sentinel-5P, giornaliero.",
      sat="Sentinel-5P", tipo="overlay", categoria="prodotto", risKm=5.5),
    L("inquinamento", "OMI_Nitrogen_Dioxide_Tropo_Column", "NO2 troposferico OMI (globale)", "Biossido di azoto, satellite Aura, giornaliero.",
      sat="Aura", tipo="overlay", categoria="prodotto", risKm=13.0),
    L("inquinamento", "TEMPO_L3_NO2_Vertical_Column_Troposphere", "NO2 orario TEMPO (Nord America)", "Biossido di azoto ora per ora, solo Nord America.",
      sat="TEMPO", tipo="overlay", categoria="prodotto", risKm=2.0),
    L("inquinamento", "TROPOMI_L2_Sulfur_Dioxide_Total_Vertical_Column", "SO2 totale TROPOMI", "Biossido di zolfo: vulcani e grandi impianti, giornaliero.",
      sat="Sentinel-5P", tipo="overlay", categoria="prodotto", risKm=5.5),
    L("inquinamento", "OMPS_SO2_Planetary_Boundary_Layer", "SO2 strato limite OMPS", "Biossido di zolfo vicino al suolo, giornaliero.",
      sat="Suomi NPP", tipo="overlay", categoria="prodotto", risKm=50.0),
    L("inquinamento", "AIRS_Prata_SO2_Index_Day", "Indice SO2 AIRS (giorno)", "Indice di biossido di zolfo (eruzioni).",
      sat="Aqua", tipo="overlay", categoria="prodotto", risKm=13.5),
    L("inquinamento", "AIRS_L2_Carbon_Monoxide_500hPa_Volume_Mixing_Ratio_Day", "CO a 500 hPa AIRS", "Monossido di carbonio in quota (fumo da grandi incendi).",
      sat="Aqua", tipo="overlay", categoria="prodotto", risKm=13.5),
    L("inquinamento", "MLS_CO_215hPa_Day", "CO a 215 hPa MLS", "Monossido di carbonio in alta troposfera (pennacchi di fumo trasportati lontano).",
      sat="Aura", tipo="overlay", categoria="prodotto", risKm=165.0),
    L("inquinamento", "AIRS_L2_Methane_400hPa_Volume_Mixing_Ratio_Day", "Metano a 400 hPa AIRS", "Metano (CH4) in media troposfera.",
      sat="Aqua", tipo="overlay", categoria="prodotto", risKm=13.5),
    L("inquinamento", "MODIS_Terra_AOD_Deep_Blue_Combined", "Aerosol (AOD) MODIS Terra", "Spessore ottico degli aerosol: polveri, fumo, smog.",
      sat="Terra", tipo="overlay", categoria="prodotto", risKm=10.0),
    L("inquinamento", "MODIS_Aqua_AOD_Deep_Blue_Combined", "Aerosol (AOD) MODIS Aqua", "Spessore ottico degli aerosol: polveri, fumo, smog.",
      sat="Aqua", tipo="overlay", categoria="prodotto", risKm=10.0),
    L("inquinamento", "VIIRS_NOAA20_AOD_Deep_Blue_Land_Ocean", "Aerosol (AOD) VIIRS NOAA-20", "Spessore ottico degli aerosol su terra e mare.",
      sat="NOAA-20", tipo="overlay", categoria="prodotto", risKm=6.0),
    L("inquinamento", "VIIRS_NOAA21_AOD_Deep_Blue_Land_Ocean", "Aerosol (AOD) VIIRS NOAA-21", "Spessore ottico degli aerosol su terra e mare.",
      sat="NOAA-21", tipo="overlay", categoria="prodotto", risKm=6.0),

    # ---------------- Notte
    L("notte", "VIIRS_NOAA20_DayNightBand", "Notte - banda giorno/notte VIIRS NOAA-20", "Luce visibile notturna: città, luna, aurore, pescherecci.",
      sat="NOAA-20", tipo="base", categoria="polare", risKm=0.75),
    L("notte", "VIIRS_NOAA21_DayNightBand", "Notte - banda giorno/notte VIIRS NOAA-21", "Luce visibile notturna: città, luna, aurore, pescherecci.",
      sat="NOAA-21", tipo="base", categoria="polare", risKm=0.75),
    L("notte", "VIIRS_SNPP_DayNightBand", "Notte - banda giorno/notte VIIRS Suomi NPP", "Luce visibile notturna: città, luna, aurore, pescherecci.",
      sat="Suomi NPP", tipo="base", categoria="polare", risKm=0.75),
    L("notte", "VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance", "Notte - radianza notturna NOAA-20", "Radianza della banda giorno/notte in falsi colori: luci, incendi, nubi illuminate dalla luna.",
      sat="NOAA-20", tipo="base", categoria="polare", risKm=0.75),
    L("notte", "VIIRS_Black_Marble", "Luci delle città (Black Marble 2016)", "Mosaico notturno senza nubi delle luci artificiali.",
      sat="Suomi NPP", tipo="base", categoria="sfondo", risKm=0.5),

    # ---------------- Oceano
    L("oceano", "GHRSST_L4_MUR_Sea_Surface_Temperature", "Temperatura superficiale del mare (MUR)", "Analisi giornaliera globale senza buchi, 1 km.",
      sat="Multi-satellite", tipo="overlay", categoria="prodotto", risKm=1.0),
    L("oceano", "GHRSST_L4_MUR_Sea_Surface_Temperature_Anomalies", "Anomalia temperatura del mare (MUR)", "Differenza dalla media climatica: mari più caldi o più freddi del normale.",
      sat="Multi-satellite", tipo="overlay", categoria="prodotto", risKm=1.0),
    L("oceano", "VIIRS_SNPP_L2_Sea_Surface_Temp_Night", "Temperatura del mare notturna VIIRS Suomi NPP", "Temperatura del mare misurata di notte, 750 m (con buchi dove ci sono nubi).",
      sat="Suomi NPP", tipo="overlay", categoria="prodotto", risKm=0.75),
    L("oceano", "MODIS_Aqua_L2_Sea_Surface_Temp_Day", "Temperatura del mare diurna MODIS Aqua", "Temperatura del mare misurata di giorno, 1 km (con buchi dove ci sono nubi).",
      sat="Aqua", tipo="overlay", categoria="prodotto", risKm=1.0),

    # ---------------- Precipitazioni
    # ordine = qualità: prima MTG (piu' fine e frequente), poi MSG, poi IMERG globale (in ritardo di ore)
    L("precipitazioni", "MTG_Precipitazioni", "Pioggia in atto MTG (H SAF H40B)",
      "Intensità di pioggia istantanea in mm/h: infrarosso Meteosat-12 FCI calibrato con i radiometri a microonde dei satelliti polari (H SAF). "
      "Ogni 10 minuti, griglia ~1,7 km, Europa-Africa-Atlantico (70°S-70°N, 70°O-70°E), ritardo ~35-40 min. Sonda con valore reale; "
      "nell'animazione si può attivare l'accumulo (mm caduti nell'intervallo).",
      fonte="eum", remoto="mtg_fd:h40b", sat="MTG", tipo="overlay", categoria="prodotto", maxZoom=7, risKm=1.7,
      wcs="mtg_fd__h40b", scala="h40b"),
    L("precipitazioni", "MSG_Precipitazioni", "Pioggia in atto MSG (H SAF H60B)",
      "Intensità di pioggia istantanea in mm/h da Meteosat Seconda Generazione (0°) + microonde, con correzione per i temporali (NEFODINA). "
      "Ogni 15 minuti, ~3 km, ritardo ~20-25 min, archivio dal 12-2022. Sonda con valore reale e accumulo nell'animazione.",
      fonte="eum", remoto="msg_fes:h60b", sat="MSG", tipo="overlay", categoria="prodotto", maxZoom=6, risKm=3.0,
      wcs="msg_fes__h60b", scala="h60b"),
    L("precipitazioni", "MSG_IODC_Precipitazioni", "Pioggia in atto Oceano Indiano (H SAF H63)",
      "Stesso prodotto H SAF dal Meteosat sull'Oceano Indiano (45,5°E): Medio Oriente, India, Oceano Indiano e cicloni del golfo del Bengala. "
      "Ogni 15 minuti, ~3-4 km, in mm/h.",
      fonte="eum", remoto="msg_iodc:h63", sat="MSG-IODC", tipo="overlay", categoria="prodotto", maxZoom=6, risKm=3.5,
      wcs="msg_iodc__h63", scala="h60b"),
    L("precipitazioni", "IMERG_Precipitation_Rate_30min", "Pioggia globale IMERG (30 minuti)",
      "Intensità di pioggia/neve in mm/h su tutto il globo (NASA GPM IMERG Early): utile per i cicloni fuori dalla vista Meteosat. "
      "Ogni 30 minuti, ~10 km, ritardo ~6-7 ore.",
      sat="GPM", tipo="overlay", categoria="prodotto", risKm=10.0, scala="imerg"),
    L("precipitazioni", "IMERG_Precipitation_Rate", "Pioggia globale IMERG (media giornaliera)", "Intensità media giornaliera di pioggia/neve globale in mm/h (giorno UTC).",
      sat="GPM", tipo="overlay", categoria="prodotto", risKm=10.0, scala="imerg"),

    # ---------------- Neve e ghiaccio
    L("neve", "MTG_Neve", "Neve RGB MTG", "Distingue neve al suolo (rosso-arancio) dalle nubi.",
      fonte="eum", remoto="mtg_fd:rgb_snow", sat="MTG", tipo="base", categoria="geostazionario", maxZoom=7, risKm=1.0),
    L("neve", "VIIRS_NOAA20_NDSI_Snow_Cover", "Copertura nevosa VIIRS NOAA-20", "Neve al suolo, prodotto giornaliero.",
      sat="NOAA-20", tipo="overlay", categoria="prodotto", risKm=0.375),
    L("neve", "MODIS_Terra_NDSI_Snow_Cover", "Copertura nevosa MODIS Terra", "Neve al suolo, prodotto giornaliero.",
      sat="Terra", tipo="overlay", categoria="prodotto", risKm=0.5),
    L("neve", "GHRSST_L4_MUR_Sea_Ice_Concentration", "Ghiaccio marino (MUR)", "Concentrazione del ghiaccio marino polare, analisi giornaliera.",
      sat="Multi-satellite", tipo="overlay", categoria="prodotto", risKm=1.0),

    # ---------------- Sfondi
    L("sfondi", "BlueMarble_ShadedRelief_Bathymetry", "Blue Marble con rilievo e fondali", "Sfondo fisso: terre, rilievi e batimetria.",
      tipo="base", categoria="sfondo", risKm=0.5),
    L("sfondi", "BlueMarble_NextGeneration", "Blue Marble", "Sfondo fisso: Terra senza nubi a colori reali.",
      tipo="base", categoria="sfondo", risKm=0.5),
    L("sfondi", "OSM_Land_Water_Map", "Terre e mari (OSM)", "Sfondo fisso: terre emerse e acque.",
      tipo="base", categoria="sfondo", risKm=0.3),

    # ---------------- Riferimenti
    L("riferimenti", "Coastlines_15m", "Linee di costa", "Coste ad alta risoluzione (OpenStreetMap).",
      tipo="overlay", categoria="riferimento", risKm=0.02),
    L("riferimenti", "Reference_Features_15m", "Confini e strade", "Confini di stato, regioni e strade principali.",
      tipo="overlay", categoria="riferimento", risKm=0.02),
    L("riferimenti", "Reference_Labels_15m", "Nomi di luoghi", "Etichette con i nomi di città e luoghi.",
      tipo="overlay", categoria="riferimento", risKm=0.02),
    L("riferimenti", "Graticule_15m", "Reticolo lat/lon", "Griglia di meridiani e paralleli.",
      tipo="overlay", categoria="riferimento", risKm=0.02),
]

# Passo di default per layer EUMETSAT (se il Capabilities non lo dice)
EUM_PASSO_DEFAULT = {"mtg_fd": 10, "msg_fes": 15, "msg_iodc": 15, "eps": 100, "mumi": 180}

# Scale colori REALI dei prodotti di precipitazione (verificate l'11-10-26):
#  h40b: GetLegendGraphic JSON di EUMETView (colormap a intervalli, mm/h)
#  h60b/h63: immagine di legenda EUMETView (stile msg_h60b_h63), colori campionati sui pixel
#  imerg: colormap GIBS GPM_Precipitation_Rate.xml (scala logaritmica 0,1-53 mm/h), campionata
SCALE = {
    "h40b": {"unita": "mm/h", "titolo": "Intensità di pioggia", "voci": [
        [0.001, 2, "#CCFFCC"], [2, 4, "#99E699"], [4, 7, "#66CC66"], [7, 10, "#33B333"], [10, 15, "#3399CC"],
        [15, 20, "#3366FF"], [20, 25, "#0000FF"], [25, 30, "#6600CC"], [30, 40, "#9933CC"], [40, 50, "#CC0099"],
        [50, None, "#800080"]]},
    "h60b": {"unita": "mm/h", "titolo": "Intensità di pioggia", "voci": [
        [0.1, 0.3, "#FFFFCC"], [0.3, 1, "#E3F4C0"], [1, 2, "#C6E8B3"], [2, 3, "#A2DAB7"], [3, 5, "#7ECCBA"],
        [5, 7, "#60C2BF"], [7, 10, "#41B7C4"], [10, 15, "#2FA4C2"], [15, 20, "#1D91BF"], [20, 30, "#2078B4"],
        [30, 50, "#225EA8"], [50, None, "#0C2C85"]]},
    "imerg": {"unita": "mm/h", "titolo": "Intensità di pioggia/neve", "continua": True, "voci": [
        [0.1, 0.2, "#00764E"], [0.2, 0.5, "#00971F"], [0.5, 1, "#4EC300"], [1, 2, "#C3E400"], [2, 3, "#FFB006"],
        [3, 5, "#FF7A1A"], [5, 10, "#FF4233"], [10, 20, "#E70000"], [20, 30, "#9C0000"], [30, 50, "#710000"],
        [50, None, "#390000"]]},
    # accumulo calcolato dal server (somma dei valori WCS reali): scala in mm
    "accumulo": {"unita": "mm", "titolo": "Pioggia caduta (accumulo)", "voci": [
        [0.2, 1, "#C8F0FF"], [1, 2, "#8FD3FF"], [2, 5, "#4FA8FF"], [5, 10, "#2A6CF0"], [10, 20, "#21B04B"],
        [20, 30, "#9BD12A"], [30, 50, "#FFE135"], [50, 75, "#FF9F1C"], [75, 100, "#F2421B"], [100, 150, "#C4126B"],
        [150, 200, "#8E1BA8"], [200, None, "#E6C8FF"]]},
}
legende.registra_scale(SCALE)


# ---------------------------------------------------------------- Capabilities
def _intervalli(valori):
    """['start/end/P', ...] -> lista (start, end, passoMin) normalizzati."""
    out = []
    for v in valori:
        v = (v or "").strip()
        if not v:
            continue
        parti = v.split("/")
        if len(parti) >= 2:
            out.append((parti[0], parti[1], durata_min(parti[2]) if len(parti) > 2 else None))
        else:
            out.append((parti[0], parti[0], None))
    return out


def parse_gibs_caps(dati):
    root = ET.fromstring(dati)
    indice = {}
    for x in root.findall("./w:Contents/w:Layer", NS_WMTS):
        try:
            ident = x.find("ows:Identifier", NS_WMTS).text
            titolo = x.find("ows:Title", NS_WMTS).text or ident
            fmt = (x.find("w:Format", NS_WMTS).text or "").strip()
            tms = x.find("w:TileMatrixSetLink/w:TileMatrixSet", NS_WMTS).text
            tmpl = None
            for u in x.findall("w:ResourceURL", NS_WMTS):
                if u.get("resourceType") == "tile":
                    tt = u.get("template") or ""
                    # ci sono due template (con e senza {Time}): quello senza = "default" (ultimo)
                    if tmpl is None or ("{Time}" in tt and "{Time}" not in tmpl):
                        tmpl = tt
            ext = tmpl.rsplit(".", 1)[-1] if tmpl else ("jpeg" if "jpeg" in fmt else "png")
            m = re.search(r"Level(\d+)", tms or "")
            maxz = int(m.group(1)) if m else 8
            leg = None
            for lg in x.findall("w:Style/w:LegendURL", NS_WMTS):
                href = lg.get(XLINK)
                if href and (leg is None or href.endswith("_H.svg")):
                    leg = href
            dim = x.find("w:Dimension", NS_WMTS)
            info = {"id": ident, "titolo": titolo, "formato": fmt, "tms": tms, "ext": ext, "maxZoom": maxz,
                    "legenda": leg, "fonte": "gibs", "statico": dim is None,
                    "vettoriale": "vector" in fmt, "tempoInUrl": bool(tmpl and "{Time}" in tmpl)}
            if dim is not None:
                iv = _intervalli([e.text for e in dim.findall("w:Value", NS_WMTS)])
                default = dim.findtext("w:Default", default=None, namespaces=NS_WMTS)
                if iv:
                    info["minTime"] = iv[0][0]
                    info["maxTime"] = iv[-1][1]
                    info["passoMin"] = iv[-1][2] or iv[0][2]
                    info["intervalli"] = [[a, b] for a, b, _ in iv[-10:]]
                info["defaultTime"] = default
                campione = (iv[-1][1] if iv else default) or ""
                info["tempoTipo"] = "datetime" if "T" in campione else "date"
            else:
                info.update(tempoTipo="date", passoMin=None, minTime=None, maxTime=None, defaultTime=None)
            if info["vettoriale"]:
                # i fuochi sono solo vettoriali (MVT) in WMTS: li facciamo rendere in PNG dal WMS GIBS
                info["ext"] = "png"
                info["tms"] = "WMS"
            indice[ident] = info
        except Exception as e:
            log.warning("layer GIBS non leggibile: %s", e)
    return indice


def parse_eum_caps(dati):
    root = ET.fromstring(dati)
    out = {}
    for l in root.iter("{http://www.opengis.net/wms}Layer"):
        n = l.find("w:Name", NS_WMS)
        if n is None or not n.text:
            continue
        info = {"titolo": l.findtext("w:Title", default=n.text, namespaces=NS_WMS)}
        d = l.find("w:Dimension", NS_WMS)
        if d is not None and d.get("name", "").lower() == "time":
            testo = (d.text or "").strip()
            pezzi = [p for p in testo.split(",") if p.strip()]
            if pezzi:
                primo = pezzi[0].split("/")
                ultimo = pezzi[-1].split("/")
                info["minTime"] = norm_iso(primo[0])
                info["maxTime"] = norm_iso(ultimo[1] if len(ultimo) > 1 else ultimo[0])
                if len(ultimo) > 2:
                    info["passoMin"] = durata_min(ultimo[2])
                info["intervalli"] = [[norm_iso(p.split("/")[0]), norm_iso(p.split("/")[1] if "/" in p else p)]
                                      for p in pezzi[-10:]]
            info["defaultTime"] = norm_iso(d.get("default")) if d.get("default") else None
        lg = l.find("w:Style/w:LegendURL/w:OnlineResource", NS_WMS)
        if lg is not None:
            info["legenda"] = lg.get(XLINK)
        out[n.text] = info
    return out


class Caps:
    """Capabilities GIBS + EUMETSAT con cache disco+memoria (6 h), refresh in background."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pronto = threading.Event()
        self.gibs = {}
        self.eum = {}
        self.t_gibs = 0
        self.t_eum = 0
        self.aggiornando = False
        self.ultimo_tentativo = time.time()
        self.catalogo = None
        self.avvisi = []

    def _carica_file(self, nome, url, parser, fallback=None):
        path = os.path.join(CACHE, nome)
        dati = None
        eta = None
        if os.path.exists(path):
            eta = time.time() - os.path.getmtime(path)
        if eta is None or eta > CAPS_TTL:
            try:
                dati, _, _ = scarica(url, timeout=120, tentativi=2)
                parsed = parser(dati)  # valida prima di salvare
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(dati)
                os.replace(tmp, path)
                log.info("Capabilities %s scaricato (%d byte, %d layer)", nome, len(dati), len(parsed))
                return parsed, time.time()
            except Exception as e:
                log.warning("download %s fallito: %s (uso copia locale se c'e')", nome, e)
        for p in (path, fallback):
            if p and os.path.exists(p):
                try:
                    with open(p, "rb") as f:
                        parsed = parser(f.read())
                    return parsed, os.path.getmtime(p)
                except Exception as e:
                    log.warning("copia %s illeggibile: %s", p, e)
        return {}, 0

    def aggiorna(self):
        with self.lock:
            if self.aggiornando:
                return
            self.aggiornando = True
        try:
            tmp_caps = os.path.join(os.environ.get("TEMP", ""), "gibs_caps.xml")
            g, tg = self._carica_file("gibs_caps.xml", GIBS_CAPS_URL, parse_gibs_caps, tmp_caps)
            e, te = self._carica_file("eum_caps.xml", EUM_CAPS_URL, parse_eum_caps)
            with self.lock:
                if g:
                    self.gibs, self.t_gibs = g, tg
                if e:
                    self.eum, self.t_eum = e, te
                self.catalogo = None
        except Exception:
            log.exception("aggiornamento Capabilities fallito")
        finally:
            with self.lock:
                self.aggiornando = False
            self.pronto.set()

    def assicura(self, attesa=150):
        if not self.pronto.is_set():
            self.pronto.wait(attesa)
        ora = time.time()
        if ((ora - self.t_gibs > CAPS_TTL or ora - self.t_eum > CAPS_TTL) and not self.aggiornando
                and ora - self.ultimo_tentativo > 600):
            self.ultimo_tentativo = ora
            threading.Thread(target=self.aggiorna, name="caps-refresh", daemon=True).start()

    # ---- risoluzione di un layer (curato o qualsiasi layer GIBS)
    def info_layer(self, lid):
        cat = self.costruisci()
        if lid in cat["perId"]:
            return cat["perId"][lid]
        g = self.gibs.get(lid)
        if g:
            return dict(g, fonte="gibs", remoto=lid, maxZoom=10 if g["vettoriale"] else g["maxZoom"])
        return None

    def costruisci(self):
        with self.lock:
            if self.catalogo is not None:
                return self.catalogo
            gruppi = {gid: {"id": gid, "nome": nome, "layers": []} for gid, nome in GRUPPI}
            perid = {}
            avvisi = []
            for c in CURATI:
                fonte = c.get("fonte", "gibs")
                if fonte == "gibs":
                    g = self.gibs.get(c["id"])
                    if not g:
                        avvisi.append("Layer GIBS assente nel Capabilities: " + c["id"])
                        continue
                    lay = {
                        "id": c["id"], "nome": c["nome"], "descr": c["descr"], "tms": g["tms"], "ext": g["ext"],
                        "tempoTipo": g["tempoTipo"], "passoMin": g.get("passoMin"),
                        "minTime": g.get("minTime"), "maxTime": g.get("maxTime"), "defaultTime": g.get("defaultTime"),
                        "intervalli": g.get("intervalli", []),
                        "maxZoom": c.get("maxZoom", g["maxZoom"]), "maxZoomNativo": g["maxZoom"],
                        "legenda": g.get("legenda"), "statico": g["statico"], "vettoriale": g["vettoriale"],
                        "fonte": "NASA GIBS", "titoloOriginale": traduzioni.traduci_titolo_layer(g["titolo"]),
                        "remoto": c["id"],
                    }
                    interno = dict(fonte="gibs", tempoInUrl=g["tempoInUrl"], formato=g["formato"])
                else:
                    e = self.eum.get(c["remoto"])
                    if self.eum and not e:
                        avvisi.append("Layer EUMETSAT assente nel Capabilities: " + c["remoto"])
                        continue
                    e = e or {}
                    pref = c["remoto"].split(":")[0]
                    leg = e.get("legenda")
                    if leg and "width=20&height=20" in leg:
                        leg = None  # EUMETView da' un quadratino 20x20 senza significato: meglio nessuna legenda
                    lay = {
                        "id": c["id"], "nome": c["nome"], "descr": c["descr"], "tms": "WMS", "ext": "png",
                        "tempoTipo": "datetime",
                        "passoMin": c.get("passo") or e.get("passoMin") or EUM_PASSO_DEFAULT.get(pref, 15),
                        "minTime": e.get("minTime"), "maxTime": e.get("maxTime"), "defaultTime": e.get("defaultTime"),
                        "intervalli": e.get("intervalli", []),
                        "maxZoom": c.get("maxZoom", 7), "maxZoomNativo": c.get("maxZoom", 7),
                        "legenda": leg, "statico": False, "vettoriale": False,
                        "fonte": "EUMETSAT", "titoloOriginale": traduzioni.traduci_titolo_layer(e.get("titolo", c["remoto"])),
                        "remoto": c["remoto"],
                    }
                    if c.get("orbite"):
                        lay["orbite"] = True
                    interno = dict(fonte="eum", tempoInUrl=True, formato="image/png")
                lay.update({
                    "tipo": c.get("tipo", "base"), "categoria": c.get("categoria", "prodotto"),
                    "satellite": c.get("sat"), "grigi": bool(c.get("grigi")),
                    "risoluzioneKm": c.get("risKm"), "geoLon": GEO_LON.get(c.get("sat")),
                    "gruppo": c["gruppo"],
                })
                if c.get("scala") in SCALE:
                    lay["scala"] = SCALE[c["scala"]]
                if c.get("wcs"):
                    # valori reali (sonda) e accumulo calcolato: /api/valore e /api/accumulo
                    lay["valoreReale"] = True
                    lay["accumulo"] = True
                    lay["scalaAccumulo"] = SCALE["accumulo"]
                    interno["wcs"] = c["wcs"]
                mt = parse_iso(lay.get("maxTime") or "")
                lay["datiFermi"] = bool(mt and c.get("categoria") != "sfondo"
                                        and (ora_utc() - mt).days > 30)
                if lay["datiFermi"]:
                    avvisi.append("Layer con dati fermi da oltre 30 giorni: %s (ultimo %s)" % (c["id"], lay["maxTime"]))
                gruppi[c["gruppo"]]["layers"].append(lay)
                perid[c["id"]] = dict(lay, **interno)
            self.avvisi = avvisi
            for a in avvisi:
                log.warning(a)
            self.catalogo = {
                "groups": [g for g in gruppi.values() if g["layers"]],
                "perId": perid,
                "aggiornato": fmt_dt(datetime.fromtimestamp(max(self.t_gibs, 1), timezone.utc)),
                "aggiornatoEumetsat": fmt_dt(datetime.fromtimestamp(max(self.t_eum, 1), timezone.utc)),
            }
            return self.catalogo


CAPS = Caps()

# ---------------------------------------------------------------- tile
_inflight = {}
_inflight_lock = threading.Lock()
_RE_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def tile_bbox(z, x, y):
    n = 2 ** z
    size = 2 * R_MERC / n
    minx = -R_MERC + x * size
    maxy = R_MERC - y * size
    return minx, maxy - size, minx + size, maxy


def url_wms(base, layer, tempo, z, x, y, extra=""):
    minx, miny, maxx, maxy = tile_bbox(z, x, y)
    q = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer, "STYLES": "",
        "FORMAT": "image/png", "TRANSPARENT": "true", "CRS": "EPSG:3857", "WIDTH": "256", "HEIGHT": "256",
        "BBOX": "%.4f,%.4f,%.4f,%.4f" % (minx, miny, maxx, maxy),
    }
    if tempo:
        q["TIME"] = tempo
    return base + "?" + urllib.parse.urlencode(q) + extra


def normalizza_tempo(info, t):
    """Ritorna (tempo_normalizzato | None per statico, datetime) oppure solleva ValueError."""
    if info.get("statico"):
        return None, None
    if t in ("default", "latest", "ultimo", "oggi", "now"):
        if t in ("latest", "ultimo"):
            lt = ultimo_disponibile(info["id"]) or {}
            t = lt.get("ultimo") or info.get("defaultTime") or info.get("maxTime") or ""
        else:
            t = info.get("defaultTime") or info.get("maxTime") or fmt_dt(ora_utc())
    d = parse_iso(t)
    if d is None:
        raise ValueError("tempo non valido")
    if info.get("tempoTipo") == "datetime":
        # orbite polari: istanti irregolari, EUMETView sceglie da solo l'orbita piu' vicina
        d = d.replace(second=0, microsecond=0) if info.get("orbite") else floor_passo(d, info.get("passoMin"))
        return fmt_dt(d), d
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return fmt_d(d), d


def ttl_tile(info, d, vuoto):
    """TTL in secondi per un tile in cache. None = permanente."""
    if info.get("statico"):
        return 30 * 86400
    eta = (ora_utc() - d).total_seconds() if d else 0
    if info.get("tempoTipo") == "datetime":
        if vuoto:
            return 300 if eta < 6 * 3600 else 86400
        return None  # un'immagine per un istante preciso non cambia
    # giornaliero: oggi/ieri si riempie con i passaggi
    if eta < 2 * 86400:
        return 1800 if vuoto else 3 * 3600
    return 86400 if vuoto else None


def path_tile(info, tkey, z, x, y):
    lid = _RE_SAFE.sub("_", info["id"])
    tk = _RE_SAFE.sub("_", tkey or "statico")
    return os.path.join(CACHE_TILE, lid, tk, str(z), "%d_%d.%s" % (x, y, info["ext"]))


def leggi_cache(path, ttl):
    """Ritorna ('img', bytes) | ('vuoto', None) | None se assente/scaduto."""
    ora = time.time()
    try:
        st = os.stat(path)
        if ttl is None or ora - st.st_mtime < ttl:
            with open(path, "rb") as f:
                return "img", f.read()
    except OSError:
        pass
    return None


def leggi_cache_vuoto(path, ttl):
    """None se nessun marcatore valido, altrimenti il contenuto (istante reale se 'agganciato', o '')."""
    try:
        st = os.stat(path + ".vuoto")
        if ora_ok(st.st_mtime, ttl):
            with open(path + ".vuoto", "r", encoding="utf-8") as f:
                return f.read().strip()
    except OSError:
        pass
    return None


def chiave_tempo(info, s):
    d = parse_iso(s or "")
    if d is None:
        return None
    return fmt_dt(d) if info.get("tempoTipo") == "datetime" else fmt_d(d)


def ora_ok(mtime, ttl):
    return ttl is None or time.time() - mtime < ttl


def scrivi_file(path, dati):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(dati)
    os.replace(tmp, path)


def url_upstream(info, tkey, z, x, y):
    if info["fonte"] == "eum":
        return url_wms(EUM_WMS, info["remoto"], tkey, z, x, y)
    if info.get("vettoriale"):
        return url_wms(GIBS_WMS, info["remoto"], tkey, z, x, y)
    if info.get("statico") or not info.get("tempoInUrl", True):
        return "%s/%s/default/default/%s/%d/%d/%d.%s" % (GIBS_WMTS, info["remoto"], info["tms"], z, y, x, info["ext"])
    return "%s/%s/default/%s/%s/%d/%d/%d.%s" % (GIBS_WMTS, info["remoto"], tkey, info["tms"], z, y, x, info["ext"])


def _vicino(info, tkey, reale):
    """True se l'istante reale dista al massimo un passo da quello richiesto."""
    a, b = parse_iso(tkey or ""), parse_iso(reale or "")
    if a is None or b is None:
        return False
    passo = info.get("passoMin") or (1440 if info.get("tempoTipo") == "date" else 10)
    return abs((a - b).total_seconds()) <= passo * 60


def _agganciato(info, tkey, reale, z, x, y):
    """Tile richiesto non esiste per tkey ma GIBS ha dato 'reale': lo servo solo se vicino."""
    if reale and _vicino(info, tkey, reale):
        try:
            with open(path_tile(info, reale, z, x, y), "rb") as f:
                return f.read()
        except OSError:
            pass
    return None


def ottieni_tile(info, tkey, d, z, x, y):
    """Ritorna (bytes|None, sorgente, permanente). None = vuoto/assente.
    sorgente 'agganciato:<istante>' = GIBS ha restituito un altro istante (bytes se entro un passo)."""
    path = path_tile(info, tkey, z, x, y)
    ttl_ok = ttl_tile(info, d, False)
    ttl_v = ttl_tile(info, d, True)
    c = leggi_cache(path, ttl_ok)
    if c:
        stat("tile_cache")
        return c[1], "cache", ttl_ok is None
    mv = leggi_cache_vuoto(path, ttl_v)
    if mv is not None:
        stat("tile_cache")
        if mv:
            return _agganciato(info, tkey, mv, z, x, y), "agganciato:" + mv, False
        return None, "cache", False

    # single-flight: una sola richiesta upstream per lo stesso tile
    with _inflight_lock:
        ev = _inflight.get(path)
        primo = ev is None
        if primo:
            ev = threading.Event()
            _inflight[path] = ev
    if not primo:
        ev.wait(60)
        c = leggi_cache(path, ttl_ok)
        if c:
            return c[1], "cache", ttl_ok is None
        mv = leggi_cache_vuoto(path, ttl_v)
        if mv:
            return _agganciato(info, tkey, mv, z, x, y), "agganciato:" + mv, False
        return None, "cache", False
    try:
        if info.get("fonte") == "eum" and d is not None:
            mt = parse_iso(eum_ultimo_remoto(info["remoto"], rete=False) or "")
            if mt and d > mt and (ora_utc() - d) < timedelta(hours=12):
                # istante non ancora pubblicato: EUMETView darebbe l'immagine precedente, che resterebbe in cache
                # per sempre sotto l'istante sbagliato. Vuoto breve (ttl_v), si riprova dopo.
                scrivi_file(path + ".vuoto", b"")
                return None, "rete", False
        url = url_upstream(info, tkey, z, x, y)
        # EUMETView risponde 502 per istanti non ancora pubblicati: niente retry, si considera "assente"
        eum_recente = info.get("fonte") == "eum" and d is not None and (ora_utc() - d) < timedelta(minutes=45)
        try:
            dati, ct, hdr = scarica(url, timeout=25, tentativi=1 if eum_recente else 2, richiedi_immagine=True)
            stat("tile_upstream")
            # GIBS "aggancia" un istante mancante all'immagine precedente: lo dice Layer-Time-Actual
            reale = chiave_tempo(info, hdr.get("Layer-Time-Actual")) if tkey else None
            if reale and reale != tkey:
                p_reale = path_tile(info, reale, z, x, y)
                if not os.path.exists(p_reale):
                    scrivi_file(p_reale, dati)
                scrivi_file(path + ".vuoto", reale.encode("utf-8"))
                return (dati if _vicino(info, tkey, reale) else None), "agganciato:" + reale, False
            scrivi_file(path, dati)
            try:
                os.remove(path + ".vuoto")
            except OSError:
                pass
            return dati, "rete", ttl_ok is None
        except Mancante as e:
            log.debug("tile mancante %s: %s", url, e)
            scrivi_file(path + ".vuoto", b"")
            return None, "rete", False
        except Exception as e:
            if eum_recente and isinstance(e, urllib.error.HTTPError) and e.code >= 500:
                scrivi_file(path + ".vuoto", b"")
                return None, "rete", False
            log.warning("tile errore %s %s/%s/%d/%d/%d: %s", info["id"], tkey, info["ext"], z, y, x, e)
            # errore di rete: se c'e' una copia scaduta, meglio quella che niente
            try:
                with open(path, "rb") as f:
                    return f.read(), "cache-scaduta", False
            except OSError:
                pass
            raise
    finally:
        with _inflight_lock:
            _inflight.pop(path, None)
        ev.set()


# ---------------------------------------------------------------- ultimo disponibile
# GIBS, se si chiede un istante non disponibile ma dentro un intervallo, restituisce l'immagine
# precedente e lo dichiara nell'header Layer-Time-Actual: ottieni_tile la marca "agganciato:<reale>".
_latest_cache = {}
_latest_lock = threading.Lock()
_latest_layer_locks = {}


def _sonda(info, tkey, d):
    """Ritorna l'istante reale disponibile (stringa) o None."""
    try:
        dati, sorg, _ = ottieni_tile(info, tkey, d, 0, 0, 0)
    except Exception as e:
        log.warning("sonda %s %s fallita: %s", info["id"], tkey, e)
        return None
    if sorg.startswith("agganciato:"):
        return sorg.split(":", 1)[1] or None
    if dati:
        return tkey
    return None


def ultimo_disponibile(lid):
    info = CAPS.info_layer(lid)
    if not info:
        return None
    if info.get("statico"):
        return {"layer": lid, "ultimo": None, "statico": True}
    with _latest_lock:
        c = _latest_cache.get(lid)
        if c and time.time() - c[0] < c[1]:
            return c[2]
        lk = _latest_layer_locks.setdefault(lid, threading.Lock())
    with lk:
        with _latest_lock:  # un altro thread potrebbe averlo appena calcolato
            c = _latest_cache.get(lid)
            if c and time.time() - c[0] < c[1]:
                return c[2]
        return _calcola_ultimo(lid, info)


# Layer EUMETView: l'ultimo istante si legge dal Capabilities del singolo layer (~9 KB, 0,4 s, aggiornato in tempo reale).
# Le sonde non bastano: EUMETView ha nearestValue=1 e risponde con l'immagine piu' vicina anche per istanti non ancora
# pubblicati (verificato l'11-10-26: rgb_dust IODC ultimo 17:45, richiesta 18:00 -> immagine delle 17:45; mosaico
# anello ultimo 15:00, richiesta 18:00 -> immagine delle 15:00). Per le orbite Metop gli istanti sono pure irregolari.
ORBITE_TTL = 600
_orbite_cache = {}  # remoto -> (t, maxTime)
_orbite_lock = threading.Lock()
_orbite_agg = {"in_corso": False}


def eum_ultimo_remoto(remoto, rete=True, ttl=ORBITE_TTL):
    with _orbite_lock:
        c = _orbite_cache.get(remoto)
    if c and (not rete or time.time() - c[0] < ttl):
        return c[1]
    if not rete:
        return None
    ws, nome = remoto.split(":", 1)
    url = "%s/%s/%s/ows?service=WMS&request=GetCapabilities&version=1.3.0" % (EUM_WMS.rsplit("/", 1)[0], ws, nome)
    mt = None
    try:
        dati, _, _ = scarica(url, timeout=20, tentativi=2)
        caps = parse_eum_caps(dati)
        info = caps.get(nome) or caps.get(remoto) or {}
        mt = info.get("maxTime")
    except Exception as e:
        log.warning("Capabilities del layer %s non letto: %s", remoto, e)
    with _orbite_lock:
        if mt:
            _orbite_cache[remoto] = (time.time(), mt)
        elif c:  # errore: tengo il vecchio valore e riprovo fra un minuto
            _orbite_cache[remoto] = (time.time() - ORBITE_TTL + 60, c[1])
    return mt or (c[1] if c else None)


def aggiorna_orbite_bg():
    """Rinfresca in background l'ultimo istante di tutti i layer a orbite (chiamato da /api/layers)."""
    with _orbite_lock:
        if _orbite_agg["in_corso"]:
            return
        scadute = [c["remoto"] for c in CURATI if c.get("orbite")
                   and time.time() - _orbite_cache.get(c["remoto"], (0, None))[0] >= ORBITE_TTL]
        if not scadute:
            return
        _orbite_agg["in_corso"] = True

    def lavoro():
        try:
            for r in scadute:
                eum_ultimo_remoto(r)
        finally:
            _orbite_agg["in_corso"] = False

    threading.Thread(target=lavoro, name="orbite-refresh", daemon=True).start()


def _calcola_ultimo(lid, info):
    ora = ora_utc()
    if info.get("fonte") == "eum":
        orb = bool(info.get("orbite"))
        mt = eum_ultimo_remoto(info["remoto"], ttl=ORBITE_TTL if orb else 110)
        if mt or orb:
            ris = {"layer": lid, "ultimo": mt or info.get("maxTime"), "tempoTipo": "datetime",
                   "passoMin": info.get("passoMin"), "maxTimeCapabilities": info.get("maxTime"), "sonde": 0,
                   "verificatoAlle": fmt_dt(ora), "nota": "ultimo istante dal Capabilities del singolo layer"}
            with _latest_lock:
                _latest_cache[lid] = (time.time(), (300 if orb else 110) if mt else 60, ris)
            return ris
        # Capabilities non raggiungibile: si prova con le sonde come prima
    sonde = 0
    trovato = None
    if info.get("tempoTipo") == "datetime":
        passo = info.get("passoMin") or 10
        passo_sonda = passo if (passo <= 60 and 1440 % passo == 0) else 10
        t = floor_passo(ora, passo_sonda)
        limite = ora - timedelta(hours=12)
        while t >= limite and sonde < 80:
            sonde += 1
            trovato = _sonda(info, fmt_dt(t), t)
            if trovato:
                break
            t -= timedelta(minutes=passo_sonda)
        ttl = 120
    else:
        g = ora.replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(12):
            sonde += 1
            d = g - timedelta(days=i)
            trovato = _sonda(info, fmt_d(d), d)
            if trovato:
                break
        ttl = 1800
    ris = {"layer": lid, "ultimo": trovato, "tempoTipo": info.get("tempoTipo"), "passoMin": info.get("passoMin"),
           "maxTimeCapabilities": info.get("maxTime"), "sonde": sonde, "verificatoAlle": fmt_dt(ora)}
    if trovato is None:
        ris["ultimo"] = info.get("maxTime")
        ris["nota"] = "nessun tile trovato con le sonde: uso maxTime del Capabilities"
        ttl = 60
    with _latest_lock:
        _latest_cache[lid] = (time.time(), ttl, ris)
    return ris


# ---------------------------------------------------------------- precipitazioni: valore reale e accumulo
# I prodotti H SAF di EUMETView sono anche coperture WCS (valori float veri in mm/h, banda 2 = qualità %).
# - /api/valore/{layer}?t=&lat=&lon=   -> WMS GetFeatureInfo (valore esatto nel punto)
# - /api/accumulo/{layer}/{da}/{a}/{z}/{y}/{x}  -> somma dei tasso*passo fra da e a (mm), PNG colorato
#   Le griglie di ogni istante vengono scaricate una volta (WCS GetCoverage ritagliato sul tassello) e
#   tenute in cache su disco: nell'animazione ogni fotogramma riusa quelle gia' scaricate.
EUM_OWS = "https://view.eumetsat.int/geoserver/ows"
COV_BBOX = {  # lon0, lat0, lon1, lat1 (EX_GeographicBoundingBox del Capabilities)
    "mtg_fd__h40b": (-70.0, -70.0, 70.0, 70.0),
    "msg_fes__h60b": (-77.0, -77.0, 77.0, 77.0),
    "msg_iodc__h63": (-35.0, -77.0, 125.0, 77.0),
}
CACHE_ACC = os.path.join(CACHE, "accumulo")
ACC_MAX_FOTOGRAMMI = 145
_acc_mem = {}           # chiave griglia -> array('f') (LRU semplice)
_acc_mem_lock = threading.Lock()
_acc_inflight = {}
_acc_inflight_lock = threading.Lock()
_valore_cache = {}
_ACC_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="acc")
_WCS_SEM = threading.BoundedSemaphore(2)
_wcs_pausa = [0.0]


def leggi_tiff(dati):
    """TIFF non compresso (a strisce o a tasselli), interleaved. -> (larghezza, altezza, bande, array).
    Solo stdlib: i GetCoverage di EUMETView sono float64 big-endian non compressi."""
    import array as _array
    bo = "<" if dati[:2] == b"II" else ">"
    off = struct.unpack(bo + "I", dati[4:8])[0]
    n = struct.unpack(bo + "H", dati[off:off + 2])[0]
    tsz = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 11: 4, 12: 8, 16: 8}
    fm = {1: "B", 3: "H", 4: "I", 16: "Q"}
    tag = {}
    for i in range(n):
        e = dati[off + 2 + i * 12: off + 14 + i * 12]
        t, ty, cnt = struct.unpack(bo + "HHI", e[:8])
        size = tsz.get(ty, 1) * cnt
        raw = e[8:12] if size <= 4 else dati[struct.unpack(bo + "I", e[8:12])[0]:][:size]
        tag[t] = struct.unpack(bo + fm[ty] * cnt, raw[:size]) if ty in fm else raw[:size]
    w, h = tag[256][0], tag[257][0]
    bps, spp = tag[258][0], tag.get(277, (1,))[0]
    comp = tag.get(259, (1,))[0]
    if comp not in (1, 8, 32946) or tag.get(317, (1,))[0] != 1:
        raise ValueError("TIFF con compressione/predittore non gestiti")
    if comp != 1:  # Deflate: si decomprime ogni blocco (tassello o striscia)
        offs = tag[324] if 322 in tag else tag[273]
        cnts = tag[325] if 322 in tag else tag[279]
        pezzi, nuovi_off, nuovi_cnt, pos = [], [], [], 0
        for o, c in zip(offs, cnts):
            b = zlib.decompress(dati[o:o + c])
            nuovi_off.append(pos)
            nuovi_cnt.append(len(b))
            pezzi.append(b)
            pos += len(b)
        dati = b"".join(pezzi)
        if 322 in tag:
            tag[324], tag[325] = nuovi_off, nuovi_cnt
        else:
            tag[273], tag[279] = nuovi_off, nuovi_cnt
    tc = {(3, 32): "f", (3, 64): "d", (1, 8): "B", (1, 16): "H", (2, 16): "h", (2, 32): "i", (1, 32): "I"}[
        (tag.get(339, (1,))[0], bps)]
    bpp = bps // 8 * spp
    out = _array.array(tc, bytes(w * h * bpp))
    swap = (bo == ">") != (sys.byteorder == "big")

    def blocco(buf):
        a = _array.array(tc, buf)
        if swap:
            a.byteswap()
        return a
    if 322 in tag:
        tw, tl = tag[322][0], tag[323][0]
        tx = (w + tw - 1) // tw
        for k, o in enumerate(tag[324]):
            a = blocco(dati[o:o + tw * tl * bpp])
            x0, y0 = (k % tx) * tw, (k // tx) * tl
            ncol = min(tw, w - x0)
            for r in range(min(tl, h - y0)):
                y = y0 + r
                out[(y * w + x0) * spp:(y * w + x0 + ncol) * spp] = a[r * tw * spp:(r * tw + ncol) * spp]
    else:
        rps = tag.get(278, (h,))[0]
        for k, (o, c) in enumerate(zip(tag[273], tag[279])):
            a = blocco(dati[o:o + c])
            y0 = k * rps
            nr = min(rps, h - y0)
            out[y0 * w * spp:(y0 + nr) * w * spp] = a[:nr * w * spp]
    return w, h, spp, out


def _lat_merc(z, yy):
    """latitudine (gradi) della coordinata y (in pixel di tassello, anche frazionaria) a zoom z"""
    n = 2 ** z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))


def _griglia_wcs(cov, tkey, z, x, y):
    """Valori (mm/h) del tassello 256x256 in Mercatore per un istante: array('f'), -1 = nessun dato.
    None = istante non disponibile. Cache su disco compressa + memoria."""
    import array as _array
    chiave = (cov, tkey, z, x, y)
    with _acc_mem_lock:
        g = _acc_mem.pop(chiave, None)
        if g is not None:
            _acc_mem[chiave] = g
            return g
    path = os.path.join(CACHE_ACC, cov, _RE_SAFE.sub("_", tkey), str(z), "%d_%d.f32z" % (x, y))
    g = None
    try:
        with open(path, "rb") as f:
            b = f.read()
        if b == b"ASSENTE":
            return None
        g = _array.array("f", zlib.decompress(b))
    except OSError:
        pass
    if g is None:
        with _acc_inflight_lock:
            ev = _acc_inflight.get(path)
            primo = ev is None
            if primo:
                ev = threading.Event()
                _acc_inflight[path] = ev
        if not primo:
            ev.wait(90)
            try:
                with open(path, "rb") as f:
                    b = f.read()
                return None if b == b"ASSENTE" else _array.array("f", zlib.decompress(b))
            except OSError:
                return None
        try:
            g = _scarica_griglia(cov, tkey, z, x, y, path)
        finally:
            with _acc_inflight_lock:
                _acc_inflight.pop(path, None)
            ev.set()
        if g is None or isinstance(g, str):
            return g
    with _acc_mem_lock:
        _acc_mem[chiave] = g
        while len(_acc_mem) > 160:
            _acc_mem.pop(next(iter(_acc_mem)))
    return g


def _scarica_griglia(cov, tkey, z, x, y, path):
    import array as _array
    n = 2 ** z
    lo0, lo1 = x / n * 360 - 180, (x + 1) / n * 360 - 180
    la1, la0 = _lat_merc(z, y), _lat_merc(z, y + 1)
    b = COV_BBOX.get(cov, (-80, -80, 80, 80))
    clo0, clo1, cla0, cla1 = max(lo0, b[0]), min(lo1, b[2]), max(la0, b[1]), min(la1, b[3])
    vuota = _array.array("f", [-1.0]) * 65536
    if clo0 >= clo1 or cla0 >= cla1:
        return vuota  # tassello fuori dalla copertura del satellite
    W = max(2, min(512, int(round(256 * (clo1 - clo0) / (lo1 - lo0)))))
    H = max(2, min(512, int(round(256 * (cla1 - cla0) / (la1 - la0)))))
    url = (EUM_OWS + "?service=WCS&version=2.0.1&request=GetCoverage&coverageId=" + cov
           + "&format=image/tiff&subset=time(%%22%s%%22)" % tkey.replace("Z", ".000Z")
           + "&subset=Lat(%.5f,%.5f)&subset=Long(%.5f,%.5f)&scalesize=i(%d),j(%d)" % (cla0, cla1, clo0, clo1, W, H)
           + "&geotiff:compression=DEFLATE")  # 50 KB invece di 1 MB per griglia (float64, 2 bande)
    d = parse_iso(tkey)
    recente = d is not None and (ora_utc() - d) < timedelta(hours=2)
    try:
        dati = ct = None
        for prova in range(4):
            # EUMETView limita le richieste WCS (risponde 503 se sono troppe): max 2 insieme + attesa crescente
            with _WCS_SEM:
                attesa = _wcs_pausa[0] - time.time()
                if attesa > 0:
                    time.sleep(min(attesa, 10))
                try:
                    dati, ct, _ = scarica(url, timeout=40, tentativi=1, attesa_pool=120)
                    break
                except urllib.error.HTTPError as e:
                    if e.code not in (429, 503) or prova == 3:
                        raise
                    _wcs_pausa[0] = time.time() + 2.0 * (prova + 1)
    except Mancante:
        if not recente:
            scrivi_file(path, b"ASSENTE")
        return None
    except Exception as e:
        if recente and isinstance(e, urllib.error.HTTPError) and e.code in (500, 502):
            return None  # istante non ancora pubblicato (EUMETView risponde 502)
        log.warning("WCS %s %s z%d/%d/%d: %s", cov, tkey, z, x, y, e)
        return "ERR"  # errore di rete: non e' un "dato assente", si riprova
    if "tiff" not in ct:
        if not recente:
            scrivi_file(path, b"ASSENTE")
        return None
    w, h, spp, a = leggi_tiff(dati)
    pioggia = a[0::spp]
    # ricampionamento in Mercatore: riga/colonna di origine per ogni pixel del tassello
    cols = []
    for px in range(256):
        lo = lo0 + (px + 0.5) / 256 * (lo1 - lo0)
        cols.append(-1 if lo < clo0 or lo > clo1 else min(w - 1, int((lo - clo0) / (clo1 - clo0) * w)))
    out = _array.array("f", vuota)
    for py in range(256):
        la = _lat_merc(z, y + (py + 0.5) / 256)
        if la < cla0 or la > cla1:
            continue
        r = min(h - 1, int((cla1 - la) / (cla1 - cla0) * h)) * w
        base = py * 256
        for px, c in enumerate(cols):
            if c >= 0:
                v = pioggia[r + c]
                out[base + px] = v if v >= 0 else -1.0
    scrivi_file(path, zlib.compress(out.tobytes(), 6))
    return out


def _istanti(da, a, passo):
    """istanti da 'da' ad 'a' (inclusi) allineati al passo"""
    t = floor_passo(da, passo)
    if t < da:
        t += timedelta(minutes=passo)
    out = []
    while t <= a and len(out) <= ACC_MAX_FOTOGRAMMI:
        out.append(t)
        t += timedelta(minutes=passo)
    return out


def accumulo_griglia(info, da, a, z, x, y):
    """-> (array mm | None, usati, totali)"""
    import array as _array
    passo = info.get("passoMin") or 10
    ist = _istanti(da, a, passo)
    if not ist or len(ist) > ACC_MAX_FOTOGRAMMI:
        raise ValueError("intervallo di accumulo non valido (max %d fotogrammi)" % ACC_MAX_FOTOGRAMMI)
    somma = None
    usati = 0
    k = passo / 60.0
    # griglie scaricate in parallelo (il pool upstream verso EUMETView resta limitato a 4 richieste)
    griglie = list(_ACC_POOL.map(lambda t: _griglia_wcs(info["wcs"], fmt_dt(t), z, x, y), ist))
    if any(isinstance(g, str) for g in griglie):
        raise ConnectionError("griglie non scaricate (EUMETView lento o irraggiungibile): riprovare")

    def piu(s, v):  # -1 = nessun dato: non sottrae, e un pixel resta "vuoto" solo se lo e' sempre
        return v if s < 0 else (s + v if v > 0 else s)
    for g in griglie:
        if g is None:
            continue
        usati += 1
        somma = _array.array("f", g) if somma is None else _array.array("f", map(piu, somma, g))
    if somma is None:
        return None, 0, len(ist)
    somma = _array.array("f", [v * k if v >= 0 else -1.0 for v in somma])
    return somma, usati, len(ist)


def _png_rgba(w, h, righe):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(righe, 6)) + chunk(b"IEND", b""))


def colora_accumulo(mm):
    import bisect
    voci = SCALE["accumulo"]["voci"]
    soglie = [v[0] for v in voci]
    colori = [bytes(int(c[i:i + 2], 16) for i in (1, 3, 5)) + b"\xdc" for _, _, c in voci]
    trasp = b"\x00\x00\x00\x00"
    righe = bytearray()
    for py in range(256):
        righe.append(0)
        for v in mm[py * 256:(py + 1) * 256]:
            if v < soglie[0]:
                righe += trasp
            else:
                righe += colori[bisect.bisect_right(soglie, v) - 1]
    return _png_rgba(256, 256, bytes(righe))


def valore_puntuale(info, tkey, lat, lon):
    """Valore reale nel punto (WMS GetFeatureInfo): {'valore','qualita','unita'} o None."""
    ck = (info["remoto"], tkey, round(lat, 3), round(lon, 3))
    c = _valore_cache.get(ck)
    if c and time.time() - c[0] < 3600:
        return c[1]
    dd = 0.002
    q = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": info["remoto"],
         "query_layers": info["remoto"], "crs": "EPSG:4326", "styles": "",
         "bbox": "%.5f,%.5f,%.5f,%.5f" % (lat - dd, lon - dd, lat + dd, lon + dd),
         "width": "3", "height": "3", "i": "1", "j": "1", "info_format": "application/json", "time": tkey,
         "feature_count": "1"}
    dati, _, _ = scarica(EUM_WMS + "?" + urllib.parse.urlencode(q), timeout=20, tentativi=1)
    feats = json.loads(dati.decode("utf-8")).get("features") or []
    ris = None
    if feats:
        p = feats[0].get("properties") or {}
        val = qual = None
        for k, v in p.items():
            if not isinstance(v, (int, float)):
                continue
            if "qual" in k.lower():
                qual = v
            elif val is None:
                val = v
        if val is not None and val > -1:
            ris = {"valore": round(float(val), 2), "qualita": qual, "unita": "mm/h"}
        elif val is not None:
            ris = {"valore": None, "nota": "fuori dalla vista del satellite"}
    if len(_valore_cache) > 2000:
        _valore_cache.clear()
    _valore_cache[ck] = (time.time(), ris)
    return ris


# ---------------------------------------------------------------- nativo: catalogo, ultimo istante, preparazione
NAT_SAT = [  # (sat nativo, prefisso nome, sigla satellite, chiave GEO_LON, copertura)
    ("goes19", "GOES-East", "GOES-19", "GOES-East", "Americhe e Atlantico"),
    ("goes18", "GOES-West", "GOES-18", "GOES-West", "Pacifico orientale, Alaska, Hawaii e costa ovest USA"),
    ("himawari9", "Himawari-9", "Himawari-9", "Himawari", "Giappone, Asia orientale, Australia e Pacifico occidentale"),
]
_NAT_COMUNE = ("Dati originali NOAA scaricati da AWS e ricostruiti qui pixel per pixel: ritardo reale 10-15 minuti. "
               "Il primo caricamento di una zona è lento (10-60 secondi per il primo tassello), poi è veloce. "
               "La cache occupa spazio su disco (cartella cache/nativo, pulizia automatica oltre 20 GB).")
NAT_BANDE = [  # (banda, nome, risKm, grigi, descrizione)
    ("truecolor", "colore vero 0,5 km", 0.5, False,
     "Colori reali alla risoluzione piena del canale rosso (0,5 km): coste, fiumi, nubi basse, fumo. Solo di giorno "
     "(di notte trasparente). Il verde è sintetico (gli strumenti non hanno un canale verde), corretto per la foschia."),
    ("vis", "visibile 0,5 km", 0.5, True,
     "Canale visibile rosso 0,64 µm a 0,5 km in scala di grigi: il massimo dettaglio delle nubi, solo di giorno."),
    ("ir", "infrarosso 2 km", 2.0, True,
     "Infrarosso 10,4 µm a 2 km, giorno e notte: chiaro = freddo/alto. Le palette e la sonda leggono la temperatura vera in °C."),
    ("geocolor", "GeoColor 0,5 km", 0.5, False,
     "Di giorno colore vero 0,5 km, di notte nubi dall'infrarosso su fondo blu scuro, sfumato sul terminatore. "
     "A differenza del GeoColor NASA non ci sono le luci delle città di notte."),
]


def nat_id(sat, banda):
    return "NAT_%s_%s" % (sat, banda)


def nat_attivo(sat):
    """True se il satellite e' servibile (GOES richiede h5py)."""
    if nativo is None:
        return False
    if sat.startswith("goes") and not H5PY_OK:
        return False
    return True


_nat_ultimo = {}          # (sat, banda) -> (t_calcolo, iso | None)
_nat_ultimo_lock = threading.Lock()
_nat_ultimo_locks = {}


def nat_ultimo(sat, banda, max_eta=120):
    """ultimo istante disponibile, cache 2 min; un lock per (sat, banda), mai globale."""
    k = (sat, banda)
    with _nat_ultimo_lock:
        c = _nat_ultimo.get(k)
        if c and time.time() - c[0] < max_eta:
            return c[1]
        lk = _nat_ultimo_locks.setdefault(k, threading.Lock())
    with lk:
        with _nat_ultimo_lock:
            c = _nat_ultimo.get(k)
            if c and time.time() - c[0] < max_eta:
                return c[1]
        try:
            v = nativo.ultimo_istante(sat, banda)
        except Exception as e:
            log.warning("nativo ultimo %s %s: %s", sat, banda, e)
            v = c[1] if c else None
        with _nat_ultimo_lock:
            _nat_ultimo[k] = (time.time(), v)
        return v


def _nat_ultimo_cache(sat, banda):
    with _nat_ultimo_lock:
        c = _nat_ultimo.get((sat, banda))
    return c[1] if c else None


def nat_aggiorna_ultimi():
    """thread di fondo: tiene caldi gli ultimi istanti (cosi' /api/layers risponde subito)."""
    while True:
        for sat, *_ in NAT_SAT:
            if not nat_attivo(sat):
                continue
            for banda, *_ in NAT_BANDE:
                try:
                    nat_ultimo(sat, banda, max_eta=150)
                except Exception:
                    pass
        time.sleep(180)


def nat_gruppo():
    """gruppo 'Risoluzione piena (originali)' per /api/layers (ricalcolato a ogni richiesta, e' leggero)."""
    if nativo is None:
        return None
    layers = []
    stima = floor_passo(ora_utc() - timedelta(minutes=30), 10)
    for sat, pref, sigla, geo, area in NAT_SAT:
        if not nat_attivo(sat):
            continue
        for banda, nome_b, km, grigi, descr in NAT_BANDE:
            mz = nativo.BANDE[banda]["maxZoom"]
            ult = _nat_ultimo_cache(sat, banda)
            mt = parse_iso(ult) if ult else stima
            lay = {
                "id": nat_id(sat, banda), "nome": "%s %s (originale)" % (pref, nome_b),
                "descr": "%s. Copertura: %s. %s" % (descr.rstrip("."), area, _NAT_COMUNE),
                "tms": "NATIVO", "ext": "png", "tempoTipo": "datetime", "passoMin": 10,
                "minTime": fmt_dt(mt - timedelta(days=7)), "maxTime": fmt_dt(mt), "defaultTime": fmt_dt(mt),
                "intervalli": [], "maxZoom": mz, "maxZoomNativo": mz, "legenda": None,
                "statico": False, "vettoriale": False, "fonte": "NOAA su AWS (file originali)",
                "titoloOriginale": "%s %s" % (sigla, traduzioni.BANDE_NATIVO.get(banda, banda)),
                "remoto": "%s/%s" % (sat, banda),
                "tipo": "base", "categoria": "geostazionario", "satellite": pref, "grigi": grigi,
                "risoluzioneKm": km, "geoLon": GEO_LON.get(geo), "gruppo": "nativo", "datiFermi": False,
                "nativo": {"sat": sat, "banda": banda, "satNome": sigla},
                "urlTile": "/api/nativo/%s/%s/{time}/{z}/{y}/{x}" % (sat, banda),
                "ultimoVerificato": bool(ult),
            }
            if banda == "ir":
                lay["legenda"] = {"colormap": "/api/nativo/colormap.xml"}
            layers.append(lay)
    if not layers:
        return None
    return {"id": "nativo", "nome": "Risoluzione piena (originali)", "layers": layers}


_NAT_ID_RE = re.compile(r"^NAT_([a-z0-9]+)_([a-z0-9]+)$")


def nat_da_id(lid):
    m = _NAT_ID_RE.match(lid or "")
    if not m or nativo is None:
        return None
    sat, banda = m.groups()
    if sat not in nativo.SATELLITI or banda not in nativo.BANDE or not nat_attivo(sat):
        return None
    return sat, banda


# preparazione animazioni: pool dedicato (2 lavori) per non intasare il pool interno di nativo
_nat_prep_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nat-prep")
_nat_prep_attivi = set()
_nat_prep_fatti = {}
_nat_prep_lock = threading.Lock()


def nat_prepara(sat, banda, istanti):
    """accoda nativo.prepara per ogni istante (in background). Ritorna quanti accodati."""
    n = 0
    for ist in istanti[:60]:
        d = parse_iso(ist)
        if d is None:
            continue
        k = (sat, banda, fmt_dt(floor_passo(d, 10)))
        with _nat_prep_lock:
            if k in _nat_prep_attivi or time.time() - _nat_prep_fatti.get(k, 0) < 3600:
                continue
            _nat_prep_attivi.add(k)
        n += 1

        def lavoro(k=k):
            t0 = time.time()
            try:
                nativo.prepara(k[0], k[1], k[2])
                if time.time() - t0 > 0.5:
                    log.info("nativo prepara %s %s %s in %.1f s", k[0], k[1], k[2], time.time() - t0)
            except FileNotFoundError:
                pass
            except Exception as e:
                log.warning("nativo prepara %s: %s", k, e)
            finally:
                with _nat_prep_lock:
                    _nat_prep_attivi.discard(k)
                    _nat_prep_fatti[k] = time.time()
        _nat_prep_pool.submit(lavoro)
    return n


# rendering tasselli: limite di concorrenza dedicato (non un lock globale: le altre API non aspettano mai)
NAT_SEM = threading.BoundedSemaphore(6)
NAT_PNG_VUOTO = None
_nat_inflight = {}
_nat_inflight_lock = threading.Lock()


def nat_tassello(sat, banda, ist, z, x, y):
    """(bytes | None, motivo). None = trasparente. Single-flight per lo stesso tassello."""
    k = (sat, banda, ist, z, x, y)
    with _nat_inflight_lock:
        ev = _nat_inflight.get(k)
        primo = ev is None
        if primo:
            ev = _nat_inflight[k] = threading.Event()
    if not primo:
        ev.wait(180)  # un altro thread lo sta calcolando: poi lo leggo dalla cache disco di nativo
    try:
        if not NAT_SEM.acquire(timeout=150):
            return None, "occupato"
        try:
            dati = nativo.tassello(sat, banda, ist, z, x, y)
        finally:
            NAT_SEM.release()
    except FileNotFoundError:
        return None, "non-disponibile"
    finally:
        if primo:
            with _nat_inflight_lock:
                _nat_inflight.pop(k, None)
            ev.set()
    if dati is None or dati == nativo.png_trasparente():
        return None, "fuori-disco"
    return dati, "ok"


# ---------------------------------------------------------------- eventi
CAT_IT = traduzioni.CATEGORIE_EVENTI  # unico posto: traduzioni.py

# layer suggerito per satellite geostazionario e tipo di evento
SUGG_GEO = {
    "MTG": {"nubi": "MTG_GeoColour", "fuoco": "MTG_FireTemp", "polvere": "MTG_Dust", "vulcano": "MSG_Cenere",
            "ir": "MTG_IR105"},
    "GOES-East": {"nubi": "GOES-East_ABI_GeoColor", "fuoco": "GOES-East_ABI_FireTemp", "polvere": "GOES-East_ABI_Dust",
                  "vulcano": "GOES-East_ABI_Air_Mass", "ir": "GOES-East_ABI_Band13_Clean_Infrared"},
    "GOES-West": {"nubi": "GOES-West_ABI_GeoColor", "fuoco": "GOES-West_ABI_FireTemp", "polvere": "GOES-West_ABI_Dust",
                  "vulcano": "GOES-West_ABI_Air_Mass", "ir": "GOES-West_ABI_Band13_Clean_Infrared"},
    "Himawari": {"nubi": "Himawari_AHI_Band13_Clean_Infrared", "fuoco": "VIIRS_NOAA20_Thermal_Anomalies_375m_All",
                 "polvere": "Himawari_AHI_Air_Mass", "vulcano": "Himawari_AHI_Air_Mass",
                 "ir": "Himawari_AHI_Band13_Clean_Infrared"},
}
GEO_CENTRI = [("MTG", 0.0), ("GOES-East", -75.2), ("GOES-West", -137.0), ("Himawari", 140.7)]


def layer_suggerito(cat, lat, lon):
    tipo = {"severeStorms": "nubi", "wildfires": "fuoco", "volcanoes": "vulcano", "dustHaze": "polvere",
            "floods": "nubi"}.get(cat, "nubi")
    disp = CAPS.costruisci()["perId"]
    if lat is None or lon is None or abs(lat) > 68:
        cand = ["VIIRS_NOAA20_Thermal_Anomalies_375m_All"] if tipo == "fuoco" else []
        cand += ["VIIRS_NOAA21_CorrectedReflectance_TrueColor", "VIIRS_NOAA20_CorrectedReflectance_TrueColor"]
    else:
        def dist(c):
            dl = abs(((lon - c + 180) % 360) - 180)
            return dl
        ordinati = sorted(GEO_CENTRI, key=lambda s: dist(s[1]))
        cand = [SUGG_GEO[s][tipo] for s, c in ordinati if dist(c) < 75]
        cand += [SUGG_GEO[s]["nubi"] for s, c in ordinati if dist(c) < 75]
        if cat == "seaLakeIce":
            cand = ["VIIRS_NOAA20_CorrectedReflectance_TrueColor"] + cand
    for c in cand:
        if c in disp:
            return c
    return None


def _centro(geom):
    t = geom.get("type")
    c = geom.get("coordinates")
    try:
        if t == "Point":
            return float(c[1]), float(c[0])
        if t == "Polygon":
            pts = c[0]
        elif t == "MultiPolygon":
            pts = c[0][0]
        elif t == "LineString":
            pts = c
        else:
            return None, None
        return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
    except Exception:
        return None, None


def gravita_eonet(cat, geom):
    mv = geom.get("magnitudeValue")
    unit = (geom.get("magnitudeUnit") or "").lower()
    try:
        mv = float(mv) if mv is not None else None
    except (TypeError, ValueError):
        mv = None
    if cat == "severeStorms":
        if mv is None:
            return 1
        kts = mv if "kt" in unit else mv / 1.852 if "km" in unit else mv
        return 3 if kts >= 96 else 2 if kts >= 64 else 1
    if cat == "wildfires":
        if mv is None:
            return 1
        acri = mv if "acre" in unit else mv * 2.471 if "ha" in unit else mv
        return 3 if acri >= 50000 else 2 if acri >= 5000 else 1
    if cat in ("volcanoes", "floods", "dustHaze", "earthquakes", "landslides", "tempExtremes"):
        return 2
    return 1


def eventi_eonet():
    dati, _, _ = scarica(EONET_URL, timeout=40)
    out = []
    for ev in json.loads(dati.decode("utf-8")).get("events", []):
        try:
            cats = ev.get("categories") or [{}]
            cat = cats[0].get("id", "altro")
            geoms = [g for g in (ev.get("geometry") or []) if g.get("coordinates") is not None]
            if not geoms:
                continue
            geoms.sort(key=lambda g: g.get("date") or "")
            ultimo = geoms[-1]
            lat, lon = _centro(ultimo)
            if lat is None:
                continue
            traj = []
            kts = []
            for g in geoms:
                la, lo = _centro(g)
                if la is not None:
                    traj.append([round(la, 4), round(lo, 4), norm_iso(g.get("date"))])
                    mv, un = g.get("magnitudeValue"), (g.get("magnitudeUnit") or "").lower()
                    try:
                        kts.append(float(mv) if "kt" in un else float(mv) / 1.852 if "km" in un else None)
                    except (TypeError, ValueError):
                        kts.append(None)
            intens = None
            if ultimo.get("magnitudeValue") is not None:
                intens = "%s %s" % (ultimo.get("magnitudeValue"), ultimo.get("magnitudeUnit") or "")
            fonti = ev.get("sources") or []
            out.append({
                "id": ev.get("id"), "fonte": "EONET", "titolo": ev.get("title"),
                "categoria": cat, "categoriaNome": CAT_IT.get(cat, cats[0].get("title", cat)),
                "gravita": gravita_eonet(cat, ultimo), "lat": round(lat, 4), "lon": round(lon, 4),
                "data": norm_iso(ultimo.get("date")), "intensita": intens.strip() if intens else None,
                "traiettoria": traj if len(traj) > 1 else None,
                "puntiKt": kts if cat == "severeStorms" else None,
                "url": (fonti[0].get("url") if fonti else None) or ev.get("link"),
                "urlEonet": ev.get("link"),
            })
        except Exception as e:
            log.warning("evento EONET scartato: %s", e)
    return out


def _traiettoria_gdacs(url_geom, anno_inizio, mese_inizio):
    dati, _, _ = scarica(url_geom, timeout=40)
    feats = json.loads(dati.decode("utf-8")).get("features", [])
    punti = []
    previsione_da = None
    extra = {"cono": None, "segmenti": []}
    for f in feats:
        p = f.get("properties", {})
        cls = p.get("Class") or ""
        try:
            if cls == "Poly_Cones":
                anello = (f.get("geometry") or {}).get("coordinates", [[]])[0]
                passo = max(1, len(anello) // 300)
                extra["cono"] = [[round(c[1], 3), round(c[0], 3)] for c in anello[::passo]]
            elif cls.startswith("Line_Line"):
                c = (f.get("geometry") or {}).get("coordinates") or []
                if len(c) >= 2:
                    extra["segmenti"].append({"da": [c[0][1], c[0][0]], "a": [c[-1][1], c[-1][0]],
                                              "cat": p.get("polygonlabel"), "previsione": bool(p.get("forecast"))})
        except Exception:
            pass
        if not cls.startswith("Point_Polygon_Point"):
            continue
        previsione_da = previsione_da or p.get("polygondate")
        m = re.match(r"(\d{2})/(\d{2}) (\d{2}):(\d{2})", p.get("polygonlabel") or "")
        la, lo = _centro(f.get("geometry") or {})
        if la is None or not m:
            continue
        gg, mm, hh, mi = map(int, m.groups())
        anno = anno_inizio + (1 if mm < mese_inizio else 0)
        try:
            t = datetime(anno, mm, gg, hh, mi, tzinfo=timezone.utc)
        except ValueError:
            continue
        punti.append((t, round(la, 3), round(lo, 3)))
    punti.sort()
    _gdacs_extra[url_geom] = extra
    return [[la, lo, fmt_dt(t)] for t, la, lo in punti], (norm_iso(previsione_da) if previsione_da else None)


_gdacs_extra = {}  # url geometria -> {cono, segmenti} (ultimo scaricato)


def eventi_gdacs():
    dati, _, _ = scarica(GDACS_TC_URL, timeout=40)
    feats = json.loads(dati.decode("utf-8")).get("features", [])
    correnti = [f for f in feats if str(f.get("properties", {}).get("iscurrent")).lower() == "true"]
    out = []

    def uno(f):
        p = f["properties"]
        lat, lon = _centro(f.get("geometry") or {})
        livello = (p.get("alertlevel") or "").lower()
        grav = {"green": 1, "orange": 2, "red": 3}.get(livello, 1)
        nome = (p.get("eventname") or "").rsplit("-", 1)[0].strip()
        da = parse_iso(p.get("fromdate")) or ora_utc()
        traj, prev = None, None
        try:
            g = (p.get("url") or {}).get("geometry")
            if g:
                traj, prev = _traiettoria_gdacs(g, da.year, da.month)
        except Exception as e:
            log.warning("traiettoria GDACS %s non disponibile: %s", nome, e)
        if traj:
            # posizione attuale = ultimo punto non di previsione
            oss = [pt for pt in traj if not prev or pt[2] <= prev]
            if oss:
                lat, lon = oss[-1][0], oss[-1][1]
        sev = p.get("severitydata") or {}
        gx = _gdacs_extra.get((p.get("url") or {}).get("geometry") or "") or {}
        return {
            "gdacsCono": gx.get("cono"), "gdacsSegmenti": gx.get("segmenti") or [],
            "ventoMaxKmh": sev.get("severity") if (sev.get("severityunit") or "").lower() == "km/h" else None,
            "id": "GDACS_TC_%s" % p.get("eventid"), "fonte": "GDACS",
            "titolo": "Ciclone tropicale %s" % (nome.title() or "?"), "nome": nome,
            "categoria": "severeStorms", "categoriaNome": CAT_IT["severeStorms"], "sottotipo": "ciclone",
            "gravita": grav, "allerta": p.get("alertlevel"),
            "lat": round(lat, 4) if lat is not None else None, "lon": round(lon, 4) if lon is not None else None,
            "data": norm_iso(p.get("todate")), "inizio": norm_iso(p.get("fromdate")),
            "intensita": sev.get("severitytext"), "paese": p.get("country"),
            "traiettoria": traj or None, "traiettoriaPrevistaDa": prev,
            "url": (p.get("url") or {}).get("report"),
        }

    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(uno, correnti[:12]):
            if r and r["lat"] is not None:
                out.append(r)
    return out


# ---------------------------------------------------------------- cicloni tropicali (NHC + JTWC + GDACS + EONET)
# Fonti verificate l'11-10-26 (www.nhc.noaa.gov NON risolve dal DNS di casa, quindi niente CurrentStorms.json):
#  - NHC/CPHC via ArcGIS NOAA mapservices.weather.noaa.gov (Atlantico, Pacifico est/centrale): punti di previsione
#    con vento/raffiche/pressione/categoria/movimento, traccia passata 6-oraria, cono, raggi 34/50/64 kt
#  - JTWC (www.metoc.navy.mil, file .tcw "JMV 3.0"): tutti i bacini, posizione, vento 1 min, raggi per quadrante,
#    previsione fino a 120 h, traccia passata con vento, pressione minima dalle note
#  - GDACS: cono e categorie dei segmenti; EONET: traccia con vento per gli altri sistemi
NHC_MS = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer"
NHC_BIN = {b: base for b, base in zip(
    ["AT1", "AT2", "AT3", "AT4", "AT5", "EP1", "EP2", "EP3", "EP4", "EP5", "CP1", "CP2", "CP3", "CP4", "CP5"],
    range(4, 4 + 26 * 15, 26))}
JTWC_RSS = "https://www.metoc.navy.mil/jtwc/rss/jtwc.rss"
JTWC_PROD = "https://www.metoc.navy.mil/jtwc/products/"
KT_KMH = 1.852
NM_KM = 1.852
BACINI = {"AL": "Atlantico settentrionale", "EP": "Pacifico nord-orientale", "CP": "Pacifico centrale",
          "WP": "Pacifico nord-occidentale", "IO": "Oceano Indiano settentrionale", "SH": "Emisfero australe"}
_DIR16 = ["nord", "nord-nordest", "nord-est", "est-nordest", "est", "est-sudest", "sud-est", "sud-sudest",
          "sud", "sud-sudovest", "sud-ovest", "ovest-sudovest", "ovest", "ovest-nordovest", "nord-ovest", "nord-nordovest"]
_TZ_NHC = {"UTC": 0, "GMT": 0, "AST": -4, "EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7,
           "PDT": -7, "PST": -8, "HST": -10, "HDT": -9, "CHST": 10}
_NON_TROP = {"EX": "extratropicale", "PT": "post-tropicale", "PTC": "post-tropicale", "LO": "bassa pressione residua",
             "DB": "disturbo", "WV": "onda tropicale", "SD": "depressione subtropicale", "SS": "tempesta subtropicale",
             "STD": "depressione subtropicale", "STS": "tempesta subtropicale", "RL": "bassa residua"}


def cat_saffir(kt):
    """categoria Saffir-Simpson dal vento sostenuto (kt): TD, TS, 1..5"""
    if kt is None:
        return None
    if kt < 34:
        return "TD"
    if kt < 64:
        return "TS"
    for lim, c in ((83, "1"), (96, "2"), (113, "3"), (137, "4")):
        if kt < lim:
            return c
    return "5"


def bacino_da_pos(lat, lon, testo=""):
    lon = ((lon + 540) % 360) - 180
    t = (testo or "").lower()
    if lat < 0:
        return "SH"
    if lon >= 100 or ("typhoon" in t and lon > 90):
        return "WP"
    if 40 <= lon < 100:
        return "IO"
    if lon <= -140 or lon >= 180:
        return "CP"
    if lon < -100 or (lon < -77 and lat < 14):
        return "EP"
    return "AL"


def nome_tipo(bacino, cat, kt, non_trop=None):
    """('Uragano', 'categoria 4') ecc., in italiano, per bacino"""
    if non_trop:
        return "Ciclone " + non_trop, None
    if cat == "TD":
        return "Depressione tropicale", None
    if cat == "TS":
        return "Tempesta tropicale", None
    if cat is None:
        return "Ciclone tropicale", None
    if bacino == "WP":
        return ("Super tifone" if (kt or 0) >= 130 else "Tifone"), "categoria %s" % cat
    if bacino in ("IO", "SH"):
        return ("Ciclone tropicale intenso" if cat in ("3", "4", "5") else "Ciclone tropicale"), "categoria %s" % cat
    return ("Uragano maggiore" if cat in ("3", "4", "5") else "Uragano"), "categoria %s" % cat


def _dir_nome(g):
    return _DIR16[int(((g % 360) + 11.25) // 22.5) % 16] if g is not None else None


def _moto(p1, p2):
    """(direzione gradi, km/h) fra due punti [lat, lon, iso]"""
    t1, t2 = parse_iso(p1[2]), parse_iso(p2[2])
    if not t1 or not t2 or t2 <= t1:
        return None, None
    la1, la2 = math.radians(p1[0]), math.radians(p2[0])
    dlo = math.radians(((p2[1] - p1[1] + 540) % 360) - 180)
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    brg = (math.degrees(math.atan2(y, x)) + 360) % 360
    d = 2 * 6371 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2
                                       + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2))
    return round(brg), round(d / ((t2 - t1).total_seconds() / 3600), 1)


def _norm_nome(s):
    s = (s or "").lower()
    for w in ("super typhoon", "typhoon", "hurricane", "tropical storm", "tropical depression", "tropical cyclone",
              "post-tropical cyclone", "post-tropical", "remnants of", "cyclone", "storm", "depression", "major",
              "subtropical", "potential"):
        s = s.replace(w, " ")
    s = re.sub(r"[^a-z\-]", " ", s)
    parole = [p for p in s.split() if len(p) > 2 and not re.match(r"^\d", p)]
    return parole[-1] if parole else ""


def _completa(c):
    """aggiunge campi derivati comuni (categoria, nomi, km/h, direzione)"""
    kt = c.get("ventoKt")
    nt = c.get("nonTropicale")
    c["categoria"] = cat_saffir(kt) if kt is not None else c.get("categoria")
    tipo, catn = nome_tipo(c["bacino"], c["categoria"], kt, nt)
    c["tipoNome"] = tipo
    c["categoriaNome"] = catn
    c["titolo"] = "%s %s" % (tipo, c["nome"].title() if c.get("nome") else "")
    c["bacinoNome"] = BACINI.get(c["bacino"], c["bacino"])
    c["emisfero"] = "S" if (c.get("lat") or 0) < 0 else "N"
    c["ventoKmh"] = round(kt * KT_KMH) if kt is not None else None
    c["raffKmh"] = round(c["raffiche"] * KT_KMH) if c.get("raffiche") is not None else None
    ti = parse_iso(c.get("istante") or "")
    c["oreDaUltimoDato"] = round((ora_utc() - ti).total_seconds() / 3600, 1) if ti else None
    c["direzioneNome"] = _dir_nome(c["direzione"]) if c.get("direzione") is not None else None
    # punti traccia: [lat, lon, iso, kt, categoria, (tipo NHC/JTWC)] -> categoria calcolata dal vento,
    # oppure 'NT' se il sistema in quel punto non e' tropicale (extra/post-tropicale, residuo)
    for p in (c.get("passato") or []) + (c.get("previsione") or []):
        while len(p) < 5:
            p.append(None)
        st = (p[5] if len(p) > 5 else "") or ""
        if st.upper() in _NON_TROP:
            p[4] = "NT"
        elif p[3] is not None:
            p[4] = cat_saffir(p[3])
        del p[5:]
    return c


def _q_nhc(layer, geom=False, extra=""):
    url = "%s/%d/query?where=1%%3D1&outFields=*&f=geojson&returnGeometry=%s%s" % (
        NHC_MS, layer, "true" if geom else "false", extra)
    dati, _, _ = scarica(url, timeout=30, tentativi=2)
    return json.loads(dati.decode("utf-8")).get("features") or []


def _t_nhc_adv(s):
    """'900 AM CST Sun Oct 11 2026' -> datetime UTC"""
    m = re.match(r"(\d{1,2})(\d{2})\s+(AM|PM)\s+([A-Z]+)\s+\w+\s+(\w+)\s+(\d+)\s+(\d{4})", (s or "").strip())
    if not m:
        return None
    hh, mi, ap, tz, mon, dd, yy = m.groups()
    hh = int(hh) % 12 + (12 if ap == "PM" else 0)
    try:
        loc = datetime.strptime("%s %s %s %d:%s" % (dd, mon[:3], yy, hh, mi), "%d %b %Y %H:%M")
    except ValueError:
        return None
    return (loc - timedelta(hours=_TZ_NHC.get(tz, 0))).replace(tzinfo=timezone.utc)


def ciclone_nhc(binn):
    base = NHC_BIN[binn]
    fc = _q_nhc(base + 2, geom=True)
    if not fc:
        return None
    fc.sort(key=lambda f: f["properties"].get("tau") or 0)
    p0 = fc[0]["properties"]
    adv = _t_nhc_adv(p0.get("advdate"))
    if adv is None:
        return None
    prev = []
    for f in fc:
        p = f["properties"]
        lo, la = f["geometry"]["coordinates"][:2]
        tau = p.get("tau") or 0
        if tau == 0:
            t = adv
        else:
            m = re.match(r"(\d{2})/(\d{2})(\d{2})", p.get("validtime") or "")
            if not m:
                continue
            gg, hh, mi = map(int, m.groups())
            mese, anno = adv.month, adv.year
            if gg < adv.day - 15:
                mese += 1
                if mese == 13:
                    mese, anno = 1, anno + 1
            t = datetime(anno, mese, gg, hh, mi, tzinfo=timezone.utc)
        kt = p.get("maxwind") if p.get("maxwind") not in (None, 9999) else None
        prev.append([round(la, 2), round(lo, 2), fmt_dt(t), kt, None, (p.get("stormtype") or "").upper()])
    passato = []
    try:
        for f in sorted(_q_nhc(base + 7, geom=True), key=lambda f: f["properties"].get("dtg") or 0):
            p = f["properties"]
            lo, la = f["geometry"]["coordinates"][:2]
            t = datetime.strptime(str(p.get("dtg")), "%Y%m%d%H").replace(tzinfo=timezone.utc)
            st = (p.get("stormtype") or "").upper()
            if st in ("DB", "LO", "WV") and not passato:
                continue  # fase di disturbo iniziale: niente
            passato.append([round(la, 2), round(lo, 2), fmt_dt(t), p.get("intensity"), None, st,
                            p.get("mslp") or None])
    except Exception as e:
        log.warning("NHC passato %s: %s", binn, e)
    cono = None
    try:
        cf = _q_nhc(base + 4, geom=True, extra="&maxAllowableOffset=0.05&geometryPrecision=2")
        if cf:
            g = cf[0]["geometry"]
            anello = g["coordinates"][0] if g["type"] == "Polygon" else max((pp[0] for pp in g["coordinates"]), key=len)
            cono = [[c[1], c[0]] for c in anello]
    except Exception as e:
        log.warning("NHC cono %s: %s", binn, e)
    raggi = {}
    try:
        for f in _q_nhc(base + 13):
            p = f["properties"]
            r = int(p.get("radii") or 0)
            if r in (34, 50, 64):
                raggi[str(r)] = {q: round((p.get(q) or 0) * NM_KM) for q in ("ne", "se", "sw", "nw")}
    except Exception as e:
        log.warning("NHC raggi %s: %s", binn, e)
    st0 = (p0.get("stormtype") or "").upper()
    bac = {"AT": "AL", "EP": "EP", "CP": "CP"}[binn[:2]]
    nome = re.sub(r"^(Major |Post-Tropical |Potential |Subtropical )?(Hurricane|Tropical Storm|Tropical Depression|"
                  r"Cyclone|Storm|Depression|Remnants Of)\s+", "", p0.get("stormname") or "", flags=re.I).strip()
    tcspd = p0.get("tcspd")
    c = {
        "id": "NHC_%s%02d%d" % (bac, p0.get("stormnum") or 0, adv.year), "fonte": "NHC", "nome": nome,
        "bacino": bac, "lat": prev[0][0], "lon": prev[0][1], "istante": prev[0][2],
        "ventoKt": prev[0][3], "raffiche": p0.get("gust") if p0.get("gust") not in (None, 9999) else None,
        "pressione": p0.get("mslp") if p0.get("mslp") not in (None, 9999) else None,
        "direzione": p0.get("tcdir") if p0.get("tcdir") not in (None, 9999) else None,
        # NHC: TCSPD in mph (come negli avvisi pubblici)
        "velocitaKmh": round(tcspd * 1.609344) if tcspd not in (None, 9999) else None,
        "nonTropicale": _NON_TROP.get(st0) if st0 not in ("HU", "MH", "TS", "TD", "TY", "STY") else None,
        "passato": [x[:6] for x in passato], "pressioniPassate": [x[6] for x in passato],
        "previsione": [list(x) for x in prev],
        "cono": cono, "conoFonte": "NHC" if cono else None,
        "raggi": raggi or None, "raggiFonte": "NHC" if raggi else None,
        "avviso": "avviso n. %s del %s UTC" % (p0.get("advisnum"), adv.strftime("%d-%m-%y %H:%M")),
        "aggiornato": fmt_dt(adv), "url": "https://www.nhc.noaa.gov/",
    }
    return _completa(c)


def cicloni_nhc():
    out = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        fut = {b: ex.submit(ciclone_nhc, b) for b in NHC_BIN}
        for b, f in fut.items():
            try:
                r = f.result()
                if r:
                    out.append(r)
            except Exception as e:
                log.warning("NHC %s: %s", b, e)
    return out


def _ll_jtwc(s):
    m = re.match(r"(\d{1,3})([NS])\s*(\d{1,4})([EW])", s)
    if not m:
        return None, None
    la = int(m.group(1)) / 10 * (1 if m.group(2) == "N" else -1)
    lo = int(m.group(3)) / 10 * (1 if m.group(4) == "E" else -1)
    return la, lo


def ciclone_jtwc(codice, testo):
    righe = testo.splitlines()
    hdr = next((r for r in righe if re.match(r"^\d{10}\s+\d{2}[A-Z]\s", r)), None)
    if not hdr:
        return None
    pz = hdr.split()
    t0 = datetime.strptime(pz[0], "%Y%m%d%H").replace(tzinfo=timezone.utc)
    sid, nome = pz[1], pz[2]
    try:
        direz, vel = int(pz[5]), int(pz[6])
    except (IndexError, ValueError):
        direz = vel = None
    prev, raggi = [], None
    for r in righe:
        m = re.match(r"^T(\d{3})\s+(\S+)\s+(\S+)\s+(\d{2,3})(.*)$", r)
        if not m:
            continue
        tau = int(m.group(1))
        la, lo = _ll_jtwc(m.group(2) + m.group(3))
        if la is None:
            continue
        kt = int(m.group(4))
        prev.append([la, lo, fmt_dt(t0 + timedelta(hours=tau)), kt, None])
        if tau == 0:
            raggi = {}
            for rk, q in re.findall(r"R0?(\d{2})\s+((?:\d{3}\s+[NS][EW]\s+QD\s*){4})", m.group(5)):
                v = re.findall(r"(\d{3})\s+([NS][EW])", q)
                raggi[str(int(rk))] = {k.lower(): round(int(n) * NM_KM) for n, k in v}
    stato = {}
    for r in righe:
        m = re.match(r"^\s*(\d{3})HR\s+(.*)$", r)
        if m:
            stato[int(m.group(1))] = m.group(2).strip()
    passato, visti = [], set()
    for r in righe:
        # righe "2726100306  81N1679E  20": numero, anno (2 cifre), MMGGHH, posizione, vento kt
        m = re.match(r"^\d{2}(\d{8})\s+(\d{1,3}[NS])\s*(\d{1,4}[EW])\s+(\d+)\s*$", r)
        if not m or m.group(1) in visti:
            continue
        visti.add(m.group(1))
        la, lo = _ll_jtwc(m.group(2) + m.group(3))
        try:
            t = datetime.strptime("20" + m.group(1), "%Y%m%d%H").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        passato.append([la, lo, fmt_dt(t), int(m.group(4)), None])
    # i primi punti a 15-20 kt sono il disturbo iniziale: si parte da 25 kt
    while len(passato) > 2 and (passato[0][3] or 0) < 25:
        passato.pop(0)
    pm = re.search(r"MINIMUM CENTRAL PRESSURE AT \d+Z IS\s+(\d{3,4})\s+MB", testo)
    pos = re.search(r"(\d{6})Z POSITION NEAR (\d+\.\d)([NS])\s+(\d+\.\d)([EW])", testo)
    subj = re.search(r"SUBJ:\s*(.*?)\s+\d{2}[A-Z]\s+\(", testo)
    tipo_s = (subj.group(1) if subj else "").upper()
    non_trop = None
    if "POST-TROPICAL" in tipo_s or "REMNANT" in tipo_s:
        non_trop = "post-tropicale"
    elif "EXTRATROPICAL" in tipo_s:
        non_trop = "extratropicale"
    elif "SUBTROPICAL" in tipo_s:
        non_trop = "subtropicale"
    raff = re.search(r"MAX SUSTAINED WINDS - (\d+) KT, GUSTS (\d+) KT", testo)
    lat, lon, ist = prev[0][:3] if prev else (passato[-1][:3] if passato else (None, None, None))
    if pos:
        # posizione aggiornata all'ora di emissione (3 h dopo il riferimento sinottico)
        g, la_s, ns, lo_s, ew = pos.groups()
        try:
            ti = t0.replace(day=int(g[:2]), hour=int(g[2:4]), minute=int(g[4:6]))
            lat = float(la_s) * (1 if ns == "N" else -1)
            lon = float(lo_s) * (1 if ew == "E" else -1)
            ist = fmt_dt(ti)
        except ValueError:
            pass
    for p in prev:
        h = int((parse_iso(p[2]) - t0).total_seconds() // 3600)
        if h in stato and "EXTRATROPICAL" in stato[h].upper() and "BECOMING" not in stato[h].upper():
            p.append("EX")
        elif h in stato and "POST-TROPICAL" in stato[h].upper():
            p.append("PT")
    bac = {"W": "WP", "E": "EP", "C": "CP", "A": "IO", "B": "IO", "S": "SH", "P": "SH", "L": "AL"}.get(sid[-1], None)
    if lat is None:
        return None
    c = {
        "id": "JTWC_%s%d" % (sid, t0.year), "fonte": "JTWC", "nome": nome.title(), "sigla": sid,
        "bacino": bac or bacino_da_pos(lat, lon), "lat": lat, "lon": lon, "istante": ist,
        "ventoKt": prev[0][3] if prev else (passato[-1][3] if passato else None),
        "raffiche": int(raff.group(2)) if raff else None,
        "pressione": int(pm.group(1)) if pm else None,
        "direzione": direz, "velocitaKmh": round(vel * KT_KMH) if vel is not None else None,
        "nonTropicale": non_trop,
        "passato": passato, "previsione": prev, "cono": None, "conoFonte": None,
        "raggi": raggi or None, "raggiFonte": "JTWC" if raggi else None,
        "avviso": "avviso JTWC n. %s, %s UTC" % (pz[3].lstrip("0") or "0", t0.strftime("%d-%m-%y %H:%M")),
        "aggiornato": fmt_dt(t0), "url": JTWC_PROD + codice + "web.txt",
        "notaVento": "vento medio su 1 minuto (JTWC)",
        "ultimoAvviso": "FINAL WARNING" in testo.upper(),
        "nota": "dissipato come ciclone tropicale significativo" if "DISSIPATED" in testo.upper() else None,
    }
    return _completa(c)


def cicloni_jtwc():
    dati, _, _ = scarica(JTWC_RSS, timeout=30)
    rss = dati.decode("utf-8", "replace")
    codici = []
    for m in re.finditer(r"products/([a-z]{2}(\d{2})\d{2})\.tcw", rss):
        if int(m.group(2)) >= 90:
            continue  # aree "invest" / allerta formazione: non ancora cicloni
        if m.group(1) not in codici:
            codici.append(m.group(1))
    out = []

    def uno(cod):
        d, _, _ = scarica(JTWC_PROD + cod + ".tcw", timeout=30)
        return ciclone_jtwc(cod, d.decode("utf-8", "replace"))
    with ThreadPoolExecutor(max_workers=3) as ex:
        for cod, f in [(c, ex.submit(uno, c)) for c in codici[:12]]:
            try:
                r = f.result()
                if r:
                    out.append(r)
            except Exception as e:
                log.warning("JTWC %s: %s", cod, e)
    return out


_cicloni = {"t": 0, "dati": None, "errori": []}
_cicloni_lock = threading.Lock()


def cicloni_attivi():
    """{'cicloni': [...], 'errori': [...], 'aggiornato': iso} (cache 10 min)"""
    with _cicloni_lock:
        if _cicloni["dati"] is not None and time.time() - _cicloni["t"] < EVENTI_TTL:
            return {"cicloni": _cicloni["dati"], "errori": _cicloni["errori"], "aggiornato": fmt_dt(
                datetime.fromtimestamp(_cicloni["t"], timezone.utc))}
        errori = []
        lista_n, lista_j = [], []
        with ThreadPoolExecutor(max_workers=2) as ex:
            fn, fj = ex.submit(cicloni_nhc), ex.submit(cicloni_jtwc)
            try:
                lista_n = fn.result()
            except Exception as e:
                errori.append("NHC: %s" % e)
            try:
                lista_j = fj.result()
            except Exception as e:
                errori.append("JTWC: %s" % e)
        # NHC ha precedenza (pressione, cono ufficiale); JTWC aggiunge i bacini che NHC non copre
        nomi = {_norm_nome(c["nome"]) for c in lista_n}
        tutti = lista_n + [c for c in lista_j if _norm_nome(c["nome"]) not in nomi]
        for c in lista_j:  # raggi JTWC se NHC non li ha
            k = _norm_nome(c["nome"])
            for n in lista_n:
                if _norm_nome(n["nome"]) == k:
                    n.setdefault("fonti", ["NHC"]).append("JTWC")
                    if not n.get("raggi") and c.get("raggi"):
                        n["raggi"], n["raggiFonte"] = c["raggi"], "JTWC"
        for e in errori:
            log.warning("cicloni: %s", e)
        if not tutti and _cicloni["dati"]:
            tutti = _cicloni["dati"]  # rete giu': tengo l'ultimo elenco
        _cicloni.update(t=time.time(), dati=tutti, errori=errori)
        return {"cicloni": tutti, "errori": errori, "aggiornato": fmt_dt(ora_utc())}


def ciclone_da_evento(ev):
    """scheda ciclone ricavata da un evento GDACS/EONET (quando NHC/JTWC non lo hanno)"""
    traj = ev.get("traiettoria") or []
    prev_da = ev.get("traiettoriaPrevistaDa")
    kts = ev.get("puntiKt") or []
    passato, prev = [], []
    for i, p in enumerate(traj):
        kt = kts[i] if i < len(kts) else None
        q = [p[0], p[1], p[2], round(kt) if kt is not None else None, None]
        (prev if prev_da and p[2] > prev_da else passato).append(q)
    # GDACS: categoria dei segmenti (HU/TS/TD...) sui punti di partenza
    for s in ev.get("gdacsSegmenti") or []:
        for q in passato + prev:
            if abs(q[0] - s["da"][0]) < 0.06 and abs(q[1] - s["da"][1]) < 0.06:
                q[4] = {"HU": "1", "TY": "1", "STY": "4", "TS": "TS", "TD": "TD", "MH": "3"}.get(
                    (s.get("cat") or "").upper(), q[4])
    ultimo = passato[-1] if passato else (traj[-1] if traj else [ev["lat"], ev["lon"], ev.get("data")])
    kt = passato[-1][3] if passato and passato[-1][3] is not None else None
    tit = ev.get("titolo") or ""
    intens = (ev.get("intensita") or "").lower()
    cat_testo = None
    if kt is None:
        if "tropical depression" in intens:
            cat_testo = "TD"
        elif "tropical storm" in intens:
            cat_testo = "TS"
        elif "hurricane" in intens or "typhoon" in intens:
            cat_testo = "1"
    c = {
        "id": "EV_" + str(ev.get("id")), "fonte": ev.get("fonte"), "nome": (ev.get("nome") or _norm_nome(tit)).title(),
        "bacino": bacino_da_pos(ev["lat"], ev["lon"], tit), "lat": ultimo[0], "lon": ultimo[1], "istante": ultimo[2],
        "ventoKt": kt, "categoria": cat_testo, "raffiche": None, "pressione": None,
        "direzione": None, "velocitaKmh": None, "nonTropicale": None,
        "passato": passato, "previsione": prev, "cono": ev.get("gdacsCono"), "conoFonte": "GDACS" if ev.get("gdacsCono") else None,
        "raggi": None, "raggiFonte": None, "avviso": None, "aggiornato": ev.get("data"), "url": ev.get("url"),
        "ventoMaxKmh": ev.get("ventoMaxKmh"),
    }
    if len(passato) >= 2:
        c["direzione"], c["velocitaKmh"] = _moto(passato[-2], passato[-1])
    # il categoria "testuale" vale solo se manca il vento
    _completa(c)
    if kt is None and cat_testo:
        c["categoria"] = cat_testo
        c["tipoNome"], c["categoriaNome"] = nome_tipo(c["bacino"], cat_testo, None)
        if cat_testo == "1":
            c["categoriaNome"] = None
        c["titolo"] = "%s %s" % (c["tipoNome"], c["nome"])
    return c


def unisci_cicloni(finali):
    """aggancia le schede ciclone agli eventi e aggiunge quelli che solo NHC/JTWC conoscono"""
    try:
        cic = cicloni_attivi()["cicloni"]
    except Exception as e:
        log.warning("cicloni non disponibili: %s", e)
        cic = []
    usati = set()
    for ev in finali:
        if ev.get("categoria") != "severeStorms":
            continue
        k = _norm_nome(ev.get("nome") or ev.get("titolo"))
        c = next((c for c in cic if k and _norm_nome(c["nome"]) == k), None)
        if c is None:
            # stesso sistema con nomi diversi: vicinanza (entro 3 gradi)
            c = next((c for c in cic if c["id"] not in usati and abs(c["lat"] - ev["lat"]) < 3
                      and abs(((c["lon"] - ev["lon"] + 540) % 360) - 180) < 3), None)
        if c is not None:
            usati.add(c["id"])
            c = dict(c)
            fonti = c.get("fonti") or [c["fonte"]]
            if ev.get("fonte") not in fonti:
                fonti.append(ev.get("fonte"))
            c["fonti"] = fonti
            if not c.get("cono") and ev.get("gdacsCono"):
                c["cono"], c["conoFonte"] = ev["gdacsCono"], "GDACS"
            ev["ciclone"] = c
        else:
            try:
                ev["ciclone"] = ciclone_da_evento(ev)
                ev["ciclone"]["fonti"] = [ev.get("fonte")]
            except Exception as e:
                log.warning("scheda ciclone %s: %s", ev.get("id"), e)
                continue
        c = ev["ciclone"]
        ev["lat"], ev["lon"] = c["lat"], c["lon"]
        ev["titolo"] = c["titolo"]
        ev["traiettoria"] = [p[:3] for p in c["passato"] + c["previsione"]] or ev.get("traiettoria")
        ev["traiettoriaPrevistaDa"] = c["passato"][-1][2] if c["passato"] and c["previsione"] else ev.get("traiettoriaPrevistaDa")
        cat = c.get("categoria")
        ev["gravita"] = max(ev.get("gravita") or 1, 3 if cat in ("3", "4", "5") else 2 if cat in ("1", "2") else 1)
        if c.get("ventoKmh"):
            ev["intensita"] = "%s, vento %d km/h" % (c["tipoNome"], c["ventoKmh"])
    for c in cic:
        if c["id"] in usati:
            continue
        c = dict(c, fonti=c.get("fonti") or [c["fonte"]])
        cat = c.get("categoria")
        finali.append({
            "id": c["id"], "fonte": c["fonte"], "titolo": c["titolo"], "nome": c["nome"],
            "categoria": "severeStorms", "categoriaNome": CAT_IT["severeStorms"], "sottotipo": "ciclone",
            "gravita": 3 if cat in ("3", "4", "5") else 2 if cat in ("1", "2") else 1,
            "lat": c["lat"], "lon": c["lon"], "data": c.get("aggiornato"),
            "intensita": "%s, vento %s km/h" % (c["tipoNome"], c.get("ventoKmh")),
            "traiettoria": [p[:3] for p in c["passato"] + c["previsione"]],
            "traiettoriaPrevistaDa": c["passato"][-1][2] if c["passato"] and c["previsione"] else None,
            "url": c.get("url"), "ciclone": c,
        })
    for ev in finali:  # campi interni non necessari al client
        ev.pop("gdacsSegmenti", None)
        ev.pop("gdacsCono", None)
    return finali


_eventi = {"t": 0, "dati": None}
_eventi_lock = threading.Lock()


def eventi():
    with _eventi_lock:
        if _eventi["dati"] is not None and time.time() - _eventi["t"] < EVENTI_TTL:
            return _eventi["dati"]
        path = os.path.join(CACHE, "eventi.json")
        lista_e, lista_g, errori = [], [], []
        with ThreadPoolExecutor(max_workers=2) as ex:
            fe = ex.submit(eventi_eonet)
            fg = ex.submit(eventi_gdacs)
            try:
                lista_e = fe.result()
            except Exception as e:
                errori.append("EONET: %s" % e)
            try:
                lista_g = fg.result()
            except Exception as e:
                errori.append("GDACS: %s" % e)
        for e in errori:
            log.warning("eventi: %s", e)
        if not lista_e and not lista_g:
            # rete giu': uso l'ultima copia salvata
            if _eventi["dati"] is not None:
                return _eventi["dati"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        # deduplica cicloni presenti in entrambe le fonti: tengo GDACS (allerta + previsione)
        nomi = {g["nome"].lower(): g for g in lista_g if g.get("nome")}
        finali = []
        for e in lista_e:
            if e["categoria"] == "severeStorms":
                parole = set(re.findall(r"[a-z\-]+", (e["titolo"] or "").lower()))
                dup = next((g for n, g in nomi.items() if n in parole), None)
                if dup:
                    dup["urlEonet"] = e.get("urlEonet")
                    if not dup.get("traiettoria") and e.get("traiettoria"):
                        dup["traiettoria"] = e["traiettoria"]
                    dup["gravita"] = max(dup["gravita"], e["gravita"])
                    continue
            finali.append(e)
        finali = lista_g + finali
        try:
            finali = unisci_cicloni(finali)
        except Exception:
            log.exception("unione dati cicloni fallita")
        for ev in finali:
            ev["layerSuggerito"] = layer_suggerito(ev["categoria"], ev.get("lat"), ev.get("lon"))
            traduzioni.traduci_evento(ev)  # titoli, paesi, intensita', categorie in italiano
        finali.sort(key=lambda x: (x["gravita"], x.get("data") or ""), reverse=True)
        _eventi["t"], _eventi["dati"] = time.time(), finali
        try:
            scrivi_file(path, json.dumps(finali, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass
        return finali


# ---------------------------------------------------------------- VIDEO MP4 (riserva con ffmpeg)
# Usato solo se il browser non sa codificare H.264 (WebCodecs): il browser invia i fotogrammi JPEG,
# il server li monta con ffmpeg e restituisce l'MP4. Nessuna installazione: se ffmpeg manca risponde 503.
import shutil
import subprocess
import uuid

CACHE_VIDEO = os.path.join(CACHE, "video")
VIDEO_MAX_FRAMES = 4000
VIDEO_MAX_BYTES = 6 * 1024 * 1024 * 1024
_video_lock = threading.Lock()


def ffmpeg_path():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in (r"C:\ffmpeg\bin\ffmpeg.exe", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe")):
        if os.path.isfile(c):
            return c
    return None


def video_pulisci(eta_s=6 * 3600):
    """cancella le sessioni video vecchie (abbandonate)"""
    if not os.path.isdir(CACHE_VIDEO):
        return
    ora = time.time()
    for d in os.listdir(CACHE_VIDEO):
        p = os.path.join(CACHE_VIDEO, d)
        try:
            if ora - os.path.getmtime(p) > eta_s:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def video_dir(vid):
    if not re.fullmatch(r"[0-9a-f]{32}", vid or ""):
        return None
    p = os.path.join(CACHE_VIDEO, vid)
    return p if os.path.isdir(p) else None


def video_monta(vid, fps, ripeti, pausa_s, crf):
    """monta i JPEG numerati con ffmpeg: ripetizioni e pausa sull'ultimo fotogramma via concat"""
    d = video_dir(vid)
    ff = ffmpeg_path()
    if not d or not ff:
        raise RuntimeError("ffmpeg non disponibile")
    fr = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
    if not fr:
        raise RuntimeError("nessun fotogramma ricevuto")
    # un fotogramma per voce (pausa = ultimo fotogramma ripetuto), numero esatto imposto con -frames:v
    dur = 1.0 / fps
    pausa_n = int(round(pausa_s * fps))
    seq = (fr + [fr[-1]] * pausa_n) * ripeti
    righe = ["ffconcat version 1.0"]
    for f in seq:
        righe.append("file '%s'" % f)
        righe.append("duration %.6f" % dur)
    righe.append("file '%s'" % seq[-1])  # l'ultima durata vale solo se segue un'altra voce
    with open(os.path.join(d, "lista.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(righe) + "\n")
    out = os.path.join(d, "video.mp4")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "lista.txt",
           "-vf", "fps=%g,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p" % fps,
           "-frames:v", str(len(seq)), "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-movflags", "+faststart", "video.mp4"]
    r = subprocess.run(cmd, cwd=d, capture_output=True, timeout=1800,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[-400:] or "ffmpeg non riuscito")
    return out


# ---------------------------------------------------------------- HTTP
_VULC_PROC = None


def _vulcani_glow(cart, pref):
    """PNG trasparente col solo bagliore della lava: differenza fra l'immagine 'lava' e i colori reali."""
    import numpy as np
    from PIL import Image
    lava = np.asarray(Image.open(os.path.join(cart, pref + "_lava.jpg")).convert("RGB"), np.float32)
    reale = np.asarray(Image.open(os.path.join(cart, pref + "_colori-reali.jpg")).convert("RGB"), np.float32)
    d = np.clip(lava - 0.93 * reale, 0, 255)
    m = d.max(axis=2)
    a = np.clip(m / 255.0 * 1.6, 0, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rgb = np.where(a[..., None] > 0.01, d / np.maximum(a[..., None], 0.01), 0)
    out = np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8)
    Image.fromarray(out, "RGBA").save(os.path.join(cart, pref + "_glow.png"), optimize=True)
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".ico": "image/x-icon",
        ".webp": "image/webp", ".woff2": "font/woff2", ".woff": "font/woff", ".wasm": "application/wasm",
        ".txt": "text/plain; charset=utf-8", ".map": "application/json"}


_static_cache = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MeteoSat/" + VERSIONE

    def log_message(self, fmt, *args):
        pass

    def _invia(self, codice, corpo, ctype, extra=None):
        try:
            self.send_response(codice)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "X-Tile-Vuoto, X-Cache, X-Tempo, X-Tempo-Reale")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(corpo)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, obj, codice=200, cache="no-cache"):
        corpo = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._invia(codice, corpo, "application/json; charset=utf-8", {"Cache-Control": cache})

    def _errore(self, codice, msg):
        self._json({"errore": msg}, codice)

    def do_OPTIONS(self):
        self._invia(204, b"", "text/plain", {"Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
                                            "Access-Control-Allow-Headers": "*"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        stat("richieste")
        try:
            parti = urllib.parse.urlsplit(self.path)
            percorso = parti.path
            if percorso.startswith("/api/"):
                self._api(percorso[5:], urllib.parse.parse_qs(parti.query))
            else:
                self._statico(percorso)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            stat("errori_interni")
            log.exception("errore su %s", self.path)
            try:
                self._errore(500, "errore interno del server")
            except Exception:
                pass

    # -------- static
    def _statico(self, percorso):
        rel = urllib.parse.unquote(percorso).lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        full = os.path.realpath(os.path.join(STATIC, rel))
        root = os.path.realpath(STATIC)
        if not (full == root or full.startswith(root + os.sep)):
            return self._errore(403, "percorso non consentito")
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            return self._errore(404, "file non trovato")
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        st = os.stat(full)
        extra = {"Cache-Control": "no-cache"}
        if rel.replace("\\", "/").startswith("data/"):
            extra["Cache-Control"] = "public, max-age=604800"  # dati di riferimento (coste, confini): cambiano di rado
        # compressione solo per i testi (html/js/css/json): le immagini sono gia' compresse
        gz = ext in (".html", ".js", ".css", ".json", ".svg", ".xml") and "gzip" in (self.headers.get("Accept-Encoding") or "")
        k = (full, st.st_mtime_ns, gz)
        corpo = _static_cache.get(k)
        if corpo is None:
            with open(full, "rb") as f:
                corpo = f.read()
            if gz:
                corpo = gzip.compress(corpo, 6)
            if len(_static_cache) > 64:
                _static_cache.clear()
            _static_cache[k] = corpo
        if gz:
            extra["Content-Encoding"] = "gzip"
            extra["Vary"] = "Accept-Encoding"
        self._invia(200, corpo, ctype, extra)

    # -------- vulcani: /api/vulcani  /api/vulcani/file/{Nome}/{file}  /api/vulcani/aggiorna
    def _vulcani(self, seg):
        import vulcani_elenco
        base = os.path.join(BASE, "Vulcani")
        if seg[:1] == ["file"] and len(seg) == 3:
            nome, f = seg[1], seg[2]
            if "/" in nome + f or "\\" in nome + f or ".." in nome + f:
                return self._errore(400, "nome non valido")
            full = os.path.join(base, f) if f.endswith(".mp4") and nome == "_video" else os.path.join(base, nome, f)
            if not os.path.isfile(full) and f.endswith("_glow.png"):
                _vulcani_glow(os.path.dirname(full), f[:-len("_glow.png")])
            if not os.path.isfile(full):
                return self._errore(404, "file non trovato")
            ctype = {".jpg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}.get(os.path.splitext(full)[1].lower())
            if not ctype:
                return self._errore(400, "tipo non valido")
            with open(full, "rb") as fh:
                corpo = fh.read()
            return self._invia(200, corpo, ctype, {"Cache-Control": "public, max-age=3600"})
        if seg == ["aggiorna"]:
            global _VULC_PROC
            if _VULC_PROC is not None and _VULC_PROC.poll() is None:
                return self._json({"avviato": False, "in_corso": True})
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            _VULC_PROC = subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable,
                                           os.path.join(BASE, "vulcano_rec.py"), "--no-telegram"],
                                          cwd=BASE, creationflags=0x08000000)
            return self._json({"avviato": True, "in_corso": True})
        try:
            with open(os.path.join(base, "stato.json"), encoding="utf-8") as f:
                stato = json.load(f)
        except (OSError, ValueError):
            stato = {}
        out = []
        for chiave, (lat, lon, lato, nome) in vulcani_elenco.VULCANI.items():
            v = stato.get(chiave) or {"scene": {}}
            scene = [dict(r, id=k) for k, r in v["scene"].items() if r.get("file") and not r.get("scartata")]
            scene.sort(key=lambda r: r["quando"])
            out.append({"chiave": chiave, "nome": nome, "lat": lat, "lon": lon, "latoKm": lato, "scene": scene,
                        "video": v.get("video") if v.get("video") and os.path.isfile(os.path.join(base, v["video"])) else None})
        return self._json({"vulcani": out, "inCorso": bool(_VULC_PROC is not None and _VULC_PROC.poll() is None)})

    # -------- api
    def _api(self, resto, qs):
        seg = [urllib.parse.unquote(s) for s in resto.split("/") if s != ""]
        if not seg:
            return self._errore(404, "endpoint sconosciuto")
        cmd = seg[0]
        if cmd == "vulcani":
            return self._vulcani(seg[1:])
        if cmd == "health":
            return self._health()
        if cmd == "layers":
            CAPS.assicura()
            cat = CAPS.costruisci()
            gruppi = list(cat["groups"])
            try:
                ng = nat_gruppo()
            except Exception:
                log.exception("gruppo nativo non costruito")
                ng = None
            if ng:
                pos = next((i + 1 for i, g in enumerate(gruppi) if g["id"] == "visibile"), 0)
                gruppi.insert(pos, ng)
            # layer a orbite: ultimo istante piu' fresco del Capabilities generale (che ha 6 ore di cache)
            aggiorna_orbite_bg()
            for i, g in enumerate(gruppi):
                if any(l.get("orbite") for l in g["layers"]):
                    nuovi = []
                    for l in g["layers"]:
                        mt = eum_ultimo_remoto(l["remoto"], rete=False) if l.get("orbite") else None
                        if mt and (parse_iso(mt) or ora_utc()) > (parse_iso(l.get("maxTime") or "") or datetime.min.replace(tzinfo=timezone.utc)):
                            l = dict(l, maxTime=mt, defaultTime=mt)
                        nuovi.append(l)
                    gruppi[i] = dict(g, layers=nuovi)
            return self._json({"groups": gruppi, "aggiornato": cat["aggiornato"],
                               "aggiornatoEumetsat": cat["aggiornatoEumetsat"], "avvisi": CAPS.avvisi},
                              cache="max-age=300")
        if cmd == "tile":
            return self._tile(seg[1:])
        if cmd == "events":
            CAPS.assicura()
            # traduzione anche qui: copre la copia di riserva su disco (eventi.json) salvata da versioni precedenti
            return self._json([traduzioni.traduci_evento(e) for e in eventi()], cache="max-age=120")
        if cmd == "latest":
            if len(seg) != 2:
                return self._errore(400, "uso: /api/latest/{layer}")
            nd = nat_da_id(seg[1])
            if nd:
                ult = nat_ultimo(*nd)
                return self._json({"layer": seg[1], "ultimo": ult, "tempoTipo": "datetime", "passoMin": 10,
                                   "fonte": "nativo", "verificatoAlle": fmt_dt(ora_utc())})
            CAPS.assicura()
            r = ultimo_disponibile(seg[1])
            if r is None:
                return self._errore(404, "layer sconosciuto: %s" % seg[1])
            return self._json(r)
        if cmd == "nativo":
            return self._nativo(seg[1:], qs)
        if cmd == "valore":
            return self._valore(seg[1:], qs)
        if cmd == "accumulo":
            return self._accumulo(seg[1:], qs)
        if cmd == "cicloni":
            return self._json(cicloni_attivi(), cache="max-age=120")
        if cmd == "legende":
            return self._legende(seg[1:])
        if cmd == "video":
            return self._video_get(seg[1:])
        return self._errore(404, "endpoint sconosciuto")

    # -------- legende: /api/legende/{layer} (voci + scala colori->valori) ; /api/legende = copertura del catalogo
    def _legende(self, seg):
        if not seg:
            CAPS.assicura()
            ids = [l["id"] for g in CAPS.costruisci()["groups"] for l in g["layers"]]
            try:
                ng = nat_gruppo()
                ids += [l["id"] for l in (ng or {}).get("layers", [])]
            except Exception:
                pass
            r = legende.riepilogo(ids)
            return self._json({"totale": len(r), "senzaLegenda": [x["id"] for x in r if not x["voci"]], "layers": r},
                              cache="max-age=600")
        if len(seg) != 1:
            return self._errore(400, "uso: /api/legende/{layer}")
        leg = legende.legenda(seg[0])
        if leg is None:
            return self._errore(404, "nessuna legenda per il layer %s" % seg[0])
        return self._json(leg, cache="max-age=3600")

    # -------- video MP4 di riserva (ffmpeg)
    def _video_get(self, seg):
        if seg == ["stato"]:
            return self._json({"ffmpeg": bool(ffmpeg_path())})
        if len(seg) == 2 and seg[1] == "file":
            d = video_dir(seg[0])
            out = d and os.path.join(d, "video.mp4")
            if not out or not os.path.isfile(out):
                return self._errore(404, "video non trovato")
            n = os.path.getsize(out)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(n))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    with open(out, "rb") as f:
                        while True:
                            b = f.read(1 << 20)
                            if not b:
                                break
                            self.wfile.write(b)
            finally:
                if self.command != "HEAD":
                    shutil.rmtree(d, ignore_errors=True)
            return None
        return self._errore(404, "endpoint video sconosciuto")

    def _youtube_post(self, seg, corpo):
        """POST /api/youtube/carica (corpo = file video; metadati nelle intestazioni X-*) -> {id}
        GET-like: POST /api/youtube/stato/{id}"""
        try:
            import yt_pubblica
        except Exception as e:
            return self._errore(503, "modulo YouTube non disponibile: %s" % e)
        if not yt_pubblica.disponibile():
            return self._errore(503, "questo PC non ha l'accesso YouTube (si pubblica dal PC principale)")
        if seg == ["carica"]:
            if len(corpo) < 1000:
                return self._errore(400, "file vuoto")
            h = self.headers
            titolo = urllib.parse.unquote(h.get("X-Titolo") or "Meteo Sat")
            desc = urllib.parse.unquote(h.get("X-Descrizione") or "")
            priv = h.get("X-Privacy") or "private"
            ext = ".webm" if (h.get("Content-Type") or "").endswith("webm") else ".mp4"
            tmpd = os.path.join(CACHE, "yt_tmp")
            os.makedirs(tmpd, exist_ok=True)
            percorso = os.path.join(tmpd, uuid.uuid4().hex + ext)
            with open(percorso, "wb") as f:
                f.write(corpo)
            jid = yt_pubblica.avvia(percorso, titolo, desc, priv, ["meteo", "satellite"])
            return self._json({"id": jid})
        if len(seg) == 2 and seg[0] == "stato":
            return self._json(yt_pubblica.LAVORI.get(seg[1]) or {"stato": "sconosciuto"})
        return self._errore(404, "endpoint YouTube sconosciuto")

    def do_POST(self):
        stat("richieste")
        try:
            parti = urllib.parse.urlsplit(self.path)
            seg = [urllib.parse.unquote(s) for s in parti.path.split("/") if s != ""]
            qs = urllib.parse.parse_qs(parti.query)
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 200 * 1024 * 1024:
                return self._errore(413, "corpo troppo grande")
            corpo = self.rfile.read(n) if n else b""
            if seg[:2] == ["api", "youtube"]:
                return self._youtube_post(seg[2:], corpo)
            if seg[:2] != ["api", "video"]:
                return self._errore(404, "endpoint sconosciuto")
            seg = seg[2:]
            if seg == ["inizio"]:
                if not ffmpeg_path():
                    return self._errore(503, "ffmpeg non installato su questo PC")
                video_pulisci()
                vid = uuid.uuid4().hex
                os.makedirs(os.path.join(CACHE_VIDEO, vid), exist_ok=True)
                return self._json({"id": vid})
            d = video_dir(seg[0]) if seg else None
            if not d:
                return self._errore(404, "sessione video non trovata")
            if len(seg) == 3 and seg[1] == "f" and seg[2].isdigit():
                i = int(seg[2])
                if i >= VIDEO_MAX_FRAMES or corpo[:2] != b"\xff\xd8":
                    return self._errore(400, "fotogramma non valido")
                with open(os.path.join(d, "%05d.jpg" % i), "wb") as f:
                    f.write(corpo)
                return self._json({"ok": True})
            if seg[1:] == ["fine"]:
                try:
                    fps = min(60.0, max(1.0, float((qs.get("fps") or ["10"])[0])))
                    ripeti = min(20, max(1, int((qs.get("ripeti") or ["1"])[0])))
                    pausa = min(10.0, max(0.0, float((qs.get("pausa") or ["0"])[0])))
                    crf = min(35, max(14, int((qs.get("crf") or ["20"])[0])))
                except ValueError:
                    return self._errore(400, "parametri non validi")
                with _video_lock:  # un montaggio alla volta: ffmpeg usa tutta la CPU
                    try:
                        out = video_monta(seg[0], fps, ripeti, pausa, crf)
                    except Exception as e:
                        log.warning("video ffmpeg non riuscito: %s", e)
                        return self._errore(500, "montaggio video non riuscito: %s" % e)
                return self._json({"ok": True, "byte": os.path.getsize(out)})
            if seg[1:] == ["annulla"]:
                shutil.rmtree(d, ignore_errors=True)
                return self._json({"ok": True})
            return self._errore(404, "endpoint video sconosciuto")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            stat("errori_interni")
            log.exception("errore POST su %s", self.path)
            try:
                self._errore(500, "errore interno del server")
            except Exception:
                pass

    # -------- valore reale nel punto: /api/valore/{layer}?t=ISO&lat=&lon=
    def _valore(self, seg, qs):
        CAPS.assicura()
        info = CAPS.info_layer(seg[0]) if len(seg) == 1 else None
        if not info or not info.get("wcs"):
            return self._errore(404, "layer senza valori reali")
        try:
            lat = float(qs["lat"][0])
            lon = ((float(qs["lon"][0]) + 540) % 360) - 180
            tkey, _ = normalizza_tempo(info, (qs.get("t") or ["latest"])[0])
        except (KeyError, ValueError, IndexError):
            return self._errore(400, "uso: /api/valore/{layer}?t=ISO&lat=..&lon=..")
        try:
            r = valore_puntuale(info, tkey, lat, lon)
        except Exception as e:
            return self._json({"valore": None, "errore": "EUMETView non risponde: %s" % e, "tempo": tkey})
        return self._json(dict(r or {"valore": None}, tempo=tkey, layer=info["id"]), cache="max-age=600")

    # -------- accumulo: /api/accumulo/{layer}/{da}/{a}/{z}/{y}/{x}   oppure /api/accumulo/{layer}/{da}/{a}?lat=&lon=
    def _accumulo(self, seg, qs):
        CAPS.assicura()
        info = CAPS.info_layer(seg[0]) if seg else None
        if not info or not info.get("wcs"):
            return self._vuoto(404, "layer-senza-accumulo", "no-store")
        try:
            da, _ = normalizza_tempo(info, seg[1])
            a, _ = normalizza_tempo(info, seg[2])
            dda, dta = parse_iso(da), parse_iso(a)
        except (ValueError, IndexError):
            return self._vuoto(400, "tempo-non-valido", "no-store")
        if dta < dda:
            dda, dta = dta, dda
        if len(seg) == 3 and "lat" in qs:
            # valore dell'accumulo in un punto: tassello al massimo zoom che lo contiene
            try:
                lat = max(-85.0, min(85.0, float(qs["lat"][0])))
                lon = ((float(qs["lon"][0]) + 540) % 360) - 180
            except (KeyError, ValueError):
                return self._errore(400, "lat/lon non validi")
            z = int(info.get("maxZoom", 6))
            n = 2 ** z
            fx = (lon + 180) / 360 * n
            fy = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
            x, y = int(fx) % n, min(n - 1, int(fy))
            try:
                mm, usati, tot = accumulo_griglia(info, dda, dta, z, x, y)
            except ValueError as e:
                return self._errore(400, str(e))
            except ConnectionError as e:
                return self._json({"mm": None, "errore": str(e)})
            v = None
            if mm is not None:
                v = mm[min(255, int((fy - y) * 256)) * 256 + min(255, int((fx - int(fx)) * 256))]
            return self._json({"mm": round(v, 1) if v is not None and v >= 0 else None, "fuoriDisco": v is not None and v < 0,
                               "da": da, "a": a, "fotogrammi": usati, "fotogrammiTotali": tot, "layer": info["id"]})
        if len(seg) != 6:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        try:
            z, y, x = int(seg[3]), int(seg[4]), int(seg[5].split(".")[0])
        except ValueError:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        n = 2 ** z if 0 <= z <= 22 else 0
        if not n or y < 0 or y >= n:
            return self._vuoto(200, "fuori-mappa", "max-age=86400")
        if z > info.get("maxZoom", 7):
            return self._vuoto(200, "oltre-zoom-nativo", "max-age=86400")
        x %= n
        try:
            mm, usati, tot = accumulo_griglia(info, dda, dta, z, x, y)
        except ValueError:
            return self._vuoto(400, "intervallo-troppo-lungo", "no-store")
        except ConnectionError:
            return self._vuoto(200, "riprova", "no-store")  # il client ritenta da solo
        if mm is None:
            return self._vuoto(200, "accumulo-non-pronto", "no-store")
        completo = usati == tot and dta < ora_utc() - timedelta(hours=2)
        self._invia(200, colora_accumulo(mm), "image/png",
                    {"X-Cache": "accumulo", "X-Fotogrammi": "%d/%d" % (usati, tot),
                     "Cache-Control": "public, max-age=86400" if completo else "max-age=120"})

    # -------- nativo: /api/nativo/{sat}/{banda}/{istante}/{z}/{y}/{x}  (+ colormap.xml, info, prepara)
    def _nativo(self, seg, qs):
        if nativo is None:
            if seg and seg[0] not in ("info", "colormap.xml"):
                return self._vuoto(200, "nativo-non-disponibile", "no-store")
            return self._errore(503, "modulo nativo non disponibile: %s" % NATIVO_ERR)
        if seg == ["colormap.xml"]:
            corpo = nativo.colormap_ir_xml().encode("utf-8")
            return self._invia(200, corpo, "application/xml; charset=utf-8", {"Cache-Control": "max-age=86400"})
        if seg == ["info"]:
            with _nat_prep_lock:
                prep = len(_nat_prep_attivi)
            return self._json(dict(nativo.info(), h5py=H5PY_OK, preparazioniInCorso=prep,
                                   satelliti_attivi=[s for s, *_ in NAT_SAT if nat_attivo(s)]))
        if len(seg) == 3 and seg[0] == "prepara":
            sat, banda = seg[1], seg[2]
            if not nat_da_id(nat_id(sat, banda)):
                return self._errore(404, "satellite o banda non validi")
            ist = [t for v in qs.get("t", []) for t in v.split(",") if t]
            n = nat_prepara(sat, banda, ist)
            with _nat_prep_lock:
                prep = len(_nat_prep_attivi)
            return self._json({"accodati": n, "inCorso": prep}, 202)
        if len(seg) != 6:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        sat, banda, t, zs, ys, xs = seg
        xs = xs.split(".")[0]
        if not nat_da_id(nat_id(sat, banda)):
            return self._vuoto(404, "layer-sconosciuto", "no-store")
        try:
            z, y, x = int(zs), int(ys), int(xs)
        except ValueError:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        n = 2 ** z if 0 <= z <= 22 else 0
        if not n or y < 0 or y >= n:
            return self._vuoto(200, "fuori-mappa", "max-age=86400")
        x %= n
        if z > nativo.BANDE[banda]["maxZoom"]:
            return self._vuoto(200, "oltre-zoom-nativo", "max-age=86400")
        if t in ("latest", "ultimo", "default"):
            t = nat_ultimo(sat, banda)
            if not t:
                return self._vuoto(200, "non-pronto", "no-store")
        d = parse_iso(t)
        if d is None:
            return self._vuoto(400, "tempo-non-valido", "no-store")
        d = floor_passo(d, 10)
        if d > ora_utc():
            return self._vuoto(200, "futuro", "no-store")
        ist = fmt_dt(d)
        t0 = time.time()
        try:
            dati, motivo = nat_tassello(sat, banda, ist, z, x, y)
        except Exception as e:
            log.warning("nativo tassello %s %s %s z%d/%d/%d: %s", sat, banda, ist, z, x, y, e)
            return self._vuoto(200, "riprova", "no-store")
        dt = time.time() - t0
        if dt > 5:
            log.info("nativo %s %s %s z%d/%d/%d in %.1f s", sat, banda, ist, z, x, y, dt)
        if dati is None:
            if motivo == "fuori-disco":
                return self._vuoto(200, "fuori-disco", "public, max-age=31536000, immutable")
            if motivo == "occupato":
                return self._vuoto(200, "riprova", "no-store")
            # istante non (ancora) presente sul bucket
            return self._vuoto(200, "non-pronto", "max-age=60")
        self._invia(200, dati, "image/png", {"X-Cache": "nativo", "X-Tempo": ist,
                                             "X-Durata": "%.2f" % dt,
                                             "Cache-Control": "public, max-age=31536000, immutable"})

    def _vuoto(self, codice=200, motivo="vuoto", cache="max-age=300"):
        stat("tile_vuoti")
        self._invia(codice, PNG_VUOTO, "image/png", {"X-Tile-Vuoto": "1", "X-Cache": motivo, "Cache-Control": cache})

    def _tile(self, seg):
        if len(seg) != 5:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        lid, t, zs, ys, xs = seg
        xs = xs.split(".")[0]
        try:
            z, y, x = int(zs), int(ys), int(xs)
        except ValueError:
            return self._vuoto(400, "richiesta-malformata", "no-store")
        CAPS.assicura()
        info = CAPS.info_layer(lid)
        if not info:
            return self._vuoto(404, "layer-sconosciuto", "no-store")
        if z < 0 or z > 22:
            return self._vuoto(400, "zoom-non-valido", "no-store")
        n = 2 ** z
        if y < 0 or y >= n:
            return self._vuoto(200, "fuori-mappa", "max-age=86400")
        x %= n
        if z > info.get("maxZoom", 8):
            return self._vuoto(200, "oltre-zoom-nativo", "max-age=86400")
        try:
            tkey, d = normalizza_tempo(info, t)
        except ValueError:
            return self._vuoto(400, "tempo-non-valido", "no-store")
        if d is not None and d > ora_utc() + timedelta(hours=1):
            return self._vuoto(200, "futuro", "no-store")
        try:
            dati, sorg, perm = ottieni_tile(info, tkey, d, z, x, y)
        except Exception:
            return self._vuoto(200, "errore-rete", "no-store")
        if dati is None:
            return self._vuoto(200, "assente-" + sorg, "max-age=300")
        extra_t = {}
        if sorg.startswith("agganciato:"):
            extra_t["X-Tempo-Reale"] = sorg.split(":", 1)[1]
            sorg, perm = "agganciato", False
        ctype = "image/jpeg" if info["ext"] in ("jpg", "jpeg") else "image/png"
        if dati[:3] == b"\xff\xd8\xff":
            ctype = "image/jpeg"
        elif dati[:4] == b"\x89PNG":
            ctype = "image/png"
        cache = "public, max-age=31536000, immutable" if perm else "max-age=600"
        h = {"X-Cache": sorg, "Cache-Control": cache, "X-Tempo": tkey or "statico"}
        h.update(extra_t)
        self._invia(200, dati, ctype, h)

    def _health(self):
        with _stats_lock:
            s = dict(STATS)
        ora = time.time()
        self._json({
            "ok": True, "versione": VERSIONE, "porta": PORTA,
            "uptimeSec": int(ora - s.pop("avvio")),
            "capabilities": {
                "pronto": CAPS.pronto.is_set(),
                "gibsLayer": len(CAPS.gibs), "gibsEtaMin": int((ora - CAPS.t_gibs) / 60) if CAPS.t_gibs else None,
                "eumetsatLayer": len(CAPS.eum), "eumetsatEtaMin": int((ora - CAPS.t_eum) / 60) if CAPS.t_eum else None,
                "avvisi": CAPS.avvisi,
            },
            "eventiInCache": len(_eventi["dati"] or []),
            "nativo": {"attivo": nativo is not None, "errore": NATIVO_ERR, "h5py": H5PY_OK,
                       "satelliti": [s for s, *_ in NAT_SAT if nat_attivo(s)]},
            "statistiche": s,
            "oraUtc": fmt_dt(ora_utc()),
        })


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # su Windows SO_REUSEADDR permetterebbe doppio bind
    request_queue_size = 64

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        log.exception("errore connessione da %s", client_address)


def main():
    log.info("Avvio METEO SAT v%s su http://%s:%d (pid %d)", VERSIONE, HOST, PORTA, os.getpid())
    try:
        httpd = Server((HOST, PORTA), Handler)
    except OSError as e:
        log.error("porta %d occupata o non disponibile: %s", PORTA, e)
        sys.exit(1)
    threading.Thread(target=CAPS.aggiorna, name="caps-init", daemon=True).start()
    if nativo is None:
        log.warning("modulo nativo non disponibile (%s): gruppo 'Risoluzione piena' disattivato", NATIVO_ERR)
    else:
        if not H5PY_OK:
            log.warning("h5py mancante: GOES nativo disattivato, resta Himawari (pip install h5py)")
        threading.Thread(target=nat_aggiorna_ultimi, name="nat-ultimi", daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Arresto server")
        httpd.server_close()


if __name__ == "__main__":
    main()
