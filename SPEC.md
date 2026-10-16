# METEO SAT — webapp locale (porta 8952) — SPEC CONDIVISA

Obiettivo: sostituto moderno del vecchio software di ricezione satellitare (Roberto Fontana).
Utente: Fulvio, italiano. UI in ITALIANO. Date gg-mm-aa. Aspetto: professionale, stile centro meteo
(scuro, mappa a tutto schermo, pannelli laterali vetro, tipografia pulita), NON generico.
Nessuna antenna: tutti i dati da internet, gratis, senza chiavi API.

## Cartella: C:\Users\fulvio\Meteo
- server.py            (backend, stdlib Python 3.14, ThreadingHTTPServer, porta 8952) -> AGENTE BACKEND
- static/index.html, static/app.js, static/style.css (+ eventuali file) -> AGENTE FRONTEND
- cache/               cache disco tile/dati
Nessun npm/build: librerie JS solo da cdnjs/jsdelivr/unpkg con versione esatta, oppure copiate in static/vendor.
Map lib consigliata: Leaflet 1.9.4 (EPSG:3857). Anti-meridiano: gestire bene (Pacifico/Himawari).

## Dati VERIFICATI raggiungibili (gia' testati)
NASA GIBS WMTS (CORS ok, no key), EPSG:3857, "best":
  https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/{Layer}/default/{Time}/{TileMatrixSet}/{z}/{y}/{x}.{ext}
  Capabilities: .../best/wmts.cgi?SERVICE=WMTS&request=GetCapabilities  (gia' scaricato in %TEMP%/gibs_caps.xml, 5.8MB)
  TileMatrixSet e ext e formato Time (data o datetime ISO "2026-10-11T17:50:00Z") vanno LETTI dal Capabilities per ogni layer
  (il backend li espone gia' risolti in /api/layers). Layer time-step dei geostazionari: 10 min.
Layer chiave presenti:
  Geostazionari: GOES-East_ABI_GeoColor, GOES-East_ABI_Band13_Clean_Infrared, GOES-East_ABI_Air_Mass, GOES-East_ABI_Dust,
   GOES-East_ABI_FireTemp, GOES-East_ABI_Band2_Red_Visible_1km; idem GOES-West_*; Himawari_AHI_Air_Mass,
   Himawari_AHI_Band13_Clean_Infrared, Himawari_AHI_Band3_Red_Visible_1km (cercare anche Himawari GeoColor nel caps)
  Polari: MODIS_Terra/Aqua_CorrectedReflectance_TrueColor, VIIRS_SNPP/NOAA20/NOAA21_CorrectedReflectance_TrueColor,
   VIIRS_*_DayNightBand (luci notturne), fuochi VIIRS_*_Thermal_Anomalies_375m_Day/Night, MODIS_*_Thermal_Anomalies_All
  Aerosol/polveri: MODIS_Terra/Aqua_AOD_Deep_Blue_Combined, VIIRS_NOAA20_AOD_Deep_Blue_Land_Ocean, GOES-*_ABI_Dust,
   AIRS_L2_Dust_Score_Day/Night
  Inquinamento: TEMPO_L3_NO2_Vertical_Column_Troposphere (solo Nord America), OMI_SO2_*, MOPITT_CO_*, AIRS_Prata_SO2_Index_*,
   ricercare nel caps NO2 globale (OMI_NO2_*, TROPOMI) e CO/CH4
  Altro: SST (MODIS_*_L3_SST_Thermal_4km), nuvole/temperatura cima nuvole (AIRS/MODIS cloud top), precipitazioni IMERG,
   neve/ghiaccio marino, bordi/coste/etichette (reference layers: Coastlines, Reference_Labels, Reference_Features)
EONET v3 (eventi naturali aperti, GeoJSON): https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30  (e /events/geojson)
  categorie: severeStorms, wildfires, volcanoes, dustHaze, floods, seaLakeIce, ecc.
GDACS: https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=TC  (cicloni, traiettorie) — 200 ok
EUMETView WMS (Meteosat/MTG, Europa-Africa): https://view.eumetsat.int/geoserver/wms — 200 ok (layer da GetCapabilities)

## API del backend (contratto)
GET /api/layers      -> {groups:[{id,nome,layers:[{id,nome,descr,tms,ext,tempoTipo:"datetime|date",passoMin,
                         minTime,maxTime,maxZoom,tipo:"base|overlay",categoria,legenda?}]}]}  (gia' risolto dal Capabilities, cache 6h)
GET /api/tile/{layer}/{time}/{z}/{y}/{x}  -> proxy con cache disco (animazioni fluide), header CORS, 404 -> PNG trasparente 256x256
GET /api/events      -> eventi unificati EONET+GDACS: [{id,fonte,titolo,categoria,gravita,lat,lon,data,traiettoria?:[[lat,lon,t],..],url}] (cache 10min)
GET /api/latest/{layer} -> ultimo timestamp disponibile reale (probe tile) per layer geostazionari
GET /api/health
Static: / -> static/index.html

## Funzioni UI richieste (tutte quelle del vecchio programma + di piu')
1. Mappa a tutto schermo, zoom fino alla massima risoluzione nativa del layer (con indicatore "risoluzione nativa ~X km/px").
2. Selettore layer a gruppi: Visibile/GeoColor, Infrarosso, Vapore/Air Mass, Polveri, Incendi, Aerosol/Inquinamento (NO2,SO2,CO), Notte, Oceano (SST), Precipitazioni.
   Opacita' per layer, ordine, piu' layer sovrapposti, confronto affiancato/swipe (tendina) fra due layer o due date.
3. ANIMAZIONE: timeline con play/pausa, velocita', ultime 1h/3h/6h/12h/24h/48h/7g, intervallo personalizzato, loop, rimbalzo,
   precaricamento tile con barra progresso, frame a scatti, scrub col mouse, tasti rapidi (spazio, frecce).
   Esportazione: GIF/MP4 della vista corrente (client-side: canvas + gif.js/ffmpeg.wasm oppure backend) — almeno GIF; scheda "Salva immagine" PNG.
4. Eventi: pannello "Anomalie" con uragani/tifoni/cicloni, incendi, vulcani, polvere/foschia, alluvioni; click = vola sul luogo,
   attiva automaticamente il layer migliore e anima le ultime ore. Traiettorie cicloni disegnate. Filtri per categoria e gravita'.
5. PALETTE (cuore del vecchio software): per layer in scala di grigio (es. infrarosso Band13) applicare palette colore lato client
   (canvas/WebGL, LUT 256 valori): Grigi, IR classica "enhanced" (cime fredde colorate), Arcobaleno, Vapore acqueo, Fuoco/Termica,
   Inverso, + editor palette personalizzabile (stop colore, salva in localStorage), regolazione luminosita'/contrasto/gamma/soglia (min-max °C).
6. Sonda: passando il mouse mostra lat/lon e (se possibile) valore; coordinate cliccabili, ricerca luogo (Nominatim) opzionale.
7. Barra stato: data/ora UTC e locale Italia del frame, satellite sorgente, ultimo aggiornamento, indicatore caricamento.
8. Preferiti/viste salvate (localStorage), link condivisibile (stato nell'hash URL), schermo intero, tema scuro, responsive.
9. Auto-aggiornamento ogni 10 min in modalita' "live".
10. Guida rapida integrata in italiano (che cosa mostra ogni layer: es. "Air Mass", "Dust" rosa-magenta = polvere, ecc.).

## Qualita'
- Funziona offline di rete solo per cio' che e' in cache; errori di rete mai fatali, messaggi chiari in italiano.
- Nessun testo inglese visibile salvo nomi propri/satelliti.
- Prestazioni: tile in cache, precaricamento a pool limitato (6-8 richieste), niente flood verso NASA.
- Avvio: Avvia_Meteo.bat + icona desktop (pythonw, CREATE_NO_WINDOW, nessuna finestra nera).
