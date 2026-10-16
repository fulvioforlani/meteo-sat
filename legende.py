# -*- coding: utf-8 -*-
"""
legende.py - LEGENDE A COLORI di tutti i layer del catalogo (unico posto da mantenere).

Ogni layer del catalogo (/api/layers) e' associato a un MODELLO di legenda (LAYER_MODELLO + REGOLE).
Tre tipi:
  A  scala di valori con unita' reali (temperatura, mm/h, NO2, AOD...) o classi ufficiali con colori esatti.
     I colori e i valori vengono dalle fonti ufficiali:
       - colormap XML di NASA GIBS (https://gibs.earthdata.nasa.gov/colormaps/v1.3/<nome>.xml), scaricate
         e tenute in cache/colormaps (7 giorni; se la rete manca si usa la copia su disco);
       - legende EUMETView (GetLegendGraphic: JSON per FRP, barre colore campionate per fulmini e altezza nubi);
       - scale campionate gia' usate dal server (H SAF H40B/H60B/H63, accumulo) passate con registra_scale();
       - codifica nativa del server (nativo.py: grigio 1..255 = +60..-110 °C).
     "scala.entries" = [[r,g,b,da,a], ...] (None = aperto) per leggere il valore dal colore del pixel.
  B  composizioni RGB senza scala numerica: voci a categorie scritte in italiano dalle guide ufficiali
     (EUMETSAT RGB Quick Guides, CIRA/NOAA, NASA Worldview). Le voci incerte hanno certo=False
     ("indicativo" nell'interfaccia).
  C  colori reali / visibile / sfondi / riferimenti: legenda "come leggerla" con esempi tipici.

API usata dal server:
  registra_scale(SCALE)        scale campionate del server (h40b, h60b, imerg, accumulo)
  legenda(layer_id)            -> dict pronto per JSON (o None se il layer non ha modello)
  modello_di(layer_id)         -> chiave del modello o None
  riepilogo(ids)               -> [{id, modello, tipo, voci, scala}] per il controllo di copertura

Formato voce: {"c": "#RRGGBB", "nome": str, "def": str, "da": num|None, "a": num|None,
               "certo": bool, "alt": ["#RRGGBB", ...]}   (alt = altri colori tipici della stessa voce)
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("meteo.legende")

QUI = os.path.dirname(os.path.abspath(__file__))
CACHE_CM = os.path.join(QUI, "cache", "colormaps")
GIBS_CM = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/%s.xml"
CM_TTL = 7 * 86400
EUM_LEG = ("https://view.eumetsat.int/geoserver/ows?service=WMS&version=1.3.0&request=GetLegendGraphic"
           "&format=image%%2Fpng&width=640&height=80&layer=%s")

# soglie di distanza colore (Delta E 1976 in spazio Lab) oltre le quali il colore "non e' in legenda"
SOGLIA = {"A": 14, "B": 30, "C": 34}

_SCALE_SERVER = {}


def registra_scale(scale):
    _SCALE_SERVER.clear()
    _SCALE_SERVER.update(scale or {})


def V(c, nome, d, da=None, a=None, certo=True, alt=None, gruppo=None):
    v = {"c": c, "nome": nome, "def": d}
    if da is not None or a is not None:
        v["da"], v["a"] = da, a
    if not certo:
        v["certo"] = False
    if alt:
        v["alt"] = list(alt)
    if gruppo is not None:
        v["gruppo"] = gruppo  # voce valida per il gruppo della scala con questo indice (es. 1 = neve in IMERG)
    return v


# =====================================================================================================
# Fasce di significato riutilizzate (definizioni in italiano semplice)
# =====================================================================================================
def _fasce_ir():
    return [
        V(None, "Cime estremamente fredde", "Sommità di temporali violenti o di cicloni che sfondano la tropopausa: "
          "le nubi più alte e attive che esistano.", None, -70),
        V(None, "Nubi alte molto fredde", "Cumulonembi maturi e incudini temporalesche, nubi spesse dei fronti: "
          "possibili piogge intense e grandine.", -70, -55),
        V(None, "Nubi alte", "Cirri spessi e nubi alte dei sistemi frontali, circa 8-11 km di quota.", -55, -40),
        V(None, "Nubi medie", "Altostrati e altocumuli (circa 3-7 km) oppure nubi alte sottili, che sembrano più calde "
          "perché lasciano passare il calore da sotto.", -40, -20),
        V(None, "Nubi medio-basse", "Nubi tra 1 e 4 km circa, o superfici molto fredde (neve, ghiacci polari).", -20, 0),
        V(None, "Nubi basse o suolo freddo", "Strati e cumuli bassi, nebbie, oppure mare e terra freddi "
          "(di notte o alle alte latitudini).", 0, 15),
        V(None, "Superficie temperata", "Mare o terreno senza nubi alle medie latitudini.", 15, 30),
        V(None, "Superficie calda", "Superficie che emette molto calore: suolo scaldato dal sole o mare caldo.", 30, None),
    ]


IR_NOTA = ("La temperatura di brillanza è quella che il satellite \"vede\": sulle nubi spesse coincide con la "
           "temperatura della cima (più fredda = più alta), sulle nubi sottili è più calda del reale. "
           "Le quote indicate sono tipiche delle medie latitudini.")


# =====================================================================================================
# MODELLI
# =====================================================================================================
M = {}

# ------------------------------------------------------------------ INFRAROSSO (tipo A)
M["ir_gibs"] = dict(
    tipo="A", titolo="Temperatura di brillanza 10,3 µm", unita="°C", dec=0,
    fonte="NASA GIBS, colormap ufficiale Clean_Longwave_Infrared_Window_Band", url=GIBS_CM % "Clean_Longwave_Infrared_Window_Band",
    scala={"gibs": "Clean_Longwave_Infrared_Window_Band"}, voci=_fasce_ir(), nota=IR_NOTA)
M["ir_nativo"] = dict(
    tipo="A", titolo="Temperatura di brillanza 10,3-10,4 µm (file originali)", unita="°C", dec=0,
    fonte="Codifica del server (nativo.py): grigio 1..255 = +60..-110 °C, dai file originali NOAA/JMA",
    scala={"nativo_ir": True}, voci=_fasce_ir(),
    nota=IR_NOTA + " In scala di grigi: chiaro = freddo. Con una palette attiva i colori cambiano ma il valore resta giusto.")
M["ir_metop"] = dict(
    tipo="A", titolo="Temperatura di brillanza 10,8 µm (AVHRR)", unita="°C", dec=0,
    fonte="EUMETView: scala di grigi fissa 220-310 K dichiarata nel Capabilities (bianco = 220 K)",
    scala={"grigio": [220 - 273.15, 310 - 273.15]}, voci=[v for v in _fasce_ir() if v["a"] is None or v["a"] > -60],
    nota=IR_NOTA + " EUMETSAT dichiara l'intervallo 220-310 K (-53..+37 °C): sotto i -53 °C tutto è bianco, sopra i "
         "+37 °C tutto è nero. Rampa lineare presunta: valore indicativo ±2-3 °C.")
M["ir_eum"] = dict(
    tipo="A", titolo="Infrarosso termico (scala di grigi)", unita="", dec=0, qualitativa=True,
    fonte="EUMETView (MTG FCI 10,5 µm / MSG SEVIRI 10,8 µm): scala di grigi, chiaro = freddo",
    voci=[
        V("#FFFFFF", "Bianco brillante", "Cime molto fredde: temporali, incudini, nubi alte e spesse dei fronti.",
          alt=["#F0F0F0", "#E6E6E6"]),
        V("#C8C8C8", "Grigio chiaro", "Nubi alte più sottili o nubi medie.", alt=["#B4B4B4", "#D2D2D2"]),
        V("#8C8C8C", "Grigio medio", "Nubi basse e medie, o terreno/mare freddi.", alt=["#787878", "#A0A0A0"]),
        V("#505050", "Grigio scuro", "Superficie senza nubi, mare o terra temperati; nubi molto basse e calde.",
          alt=["#3C3C3C", "#646464"]),
        V("#1E1E1E", "Nero", "Superficie molto calda (nessuna nube): suolo o mare.", alt=["#0A0A0A", "#282828"]),
    ],
    nota="EUMETView non pubblica la corrispondenza grigio-temperatura per questo layer: la lettura è solo "
         "qualitativa (più chiaro = più freddo = più alto). Per i gradi veri usa GOES/Himawari o i file originali.")
M["ir039"] = dict(
    tipo="A", titolo="Infrarosso 3,9 µm (scala di grigi)", unita="", dec=0, qualitativa=True,
    fonte="EUMETView MSG SEVIRI IR3.9: scala di grigi, chiaro = freddo",
    voci=[
        V("#FFFFFF", "Bianco", "Nubi alte e fredde (di notte); di giorno anche nubi di ghiaccio.", alt=["#EBEBEB"]),
        V("#A0A0A0", "Grigio", "Nubi medie/basse. Di NOTTE nebbie e strati bassi risultano più chiari "
          "dell'ambiente: è il canale classico per scoprirli.", alt=["#B4B4B4", "#8C8C8C"]),
        V("#505050", "Grigio scuro", "Superficie temperata senza nubi.", alt=["#646464", "#3C3C3C"]),
        V("#0A0A0A", "Nero / punti neri", "Superfici molto calde e INCENDI attivi (punti scuri isolati): a 3,9 µm il "
          "fuoco brilla molto più che negli altri canali. Di giorno anche il riflesso del sole.", alt=["#1E1E1E"]),
    ],
    nota="A 3,9 µm di giorno si somma la luce solare riflessa: le nubi d'acqua sembrano più calde (scure). "
         "Lettura qualitativa, scala non pubblicata.")
M["wv"] = dict(
    tipo="A", titolo="Vapore acqueo 6,2 µm (scala di grigi)", unita="", dec=0, qualitativa=True,
    fonte="EUMETView MSG SEVIRI WV6.2: scala di grigi, chiaro = freddo/umido in quota",
    voci=[
        V("#FFFFFF", "Bianco", "Nubi alte e spesse (temporali, fronti).", alt=["#F0F0F0"]),
        V("#BEBEBE", "Grigio chiaro", "Aria umida nella media-alta troposfera (5-10 km).", alt=["#AAAAAA", "#D2D2D2"]),
        V("#787878", "Grigio medio", "Umidità moderata in quota.", alt=["#8C8C8C", "#646464"]),
        V("#323232", "Scuro / nero", "Aria SECCA in quota che scende dall'alto (subsidenza, intrusioni secche dietro "
          "i fronti, gocce fredde): spesso cielo sereno sotto.", alt=["#1E1E1E", "#464646"]),
    ],
    nota="Il canale 6,2 µm non vede il suolo: mostra solo l'umidità tra circa 5 e 10 km. Lettura qualitativa.")
M["modis_ctt"] = dict(
    tipo="A", titolo="Temperatura della cima delle nubi (MODIS)", unita="°C", dec=0,
    fonte="NASA GIBS, colormap MODIS_Cloud_Top_Temp (in kelvin, convertiti in °C)", url=GIBS_CM % "MODIS_Cloud_Top_Temp",
    scala={"gibs": "MODIS_Cloud_Top_Temp", "add": -273.15}, voci=_fasce_ir(),
    nota="Stima del prodotto MODIS (non la temperatura di brillanza): dove non ci sono nubi è trasparente.")

# ------------------------------------------------------------------ VAPORE / AEROSOL / INQUINANTI (tipo A, GIBS)
M["modis_wv"] = dict(
    tipo="A", titolo="Vapore acqueo in colonna (acqua precipitabile)", unita="cm", dec=1,
    fonte="NASA GIBS, colormap MODIS_Water_Vapor", url=GIBS_CM % "MODIS_Water_Vapor",
    scala={"gibs": "MODIS_Water_Vapor"},
    voci=[
        V(None, "Aria molto secca", "Meno di 0,5 cm d'acqua in tutta la colonna: regioni polari, alta montagna, deserti in inverno.", None, 0.5),
        V(None, "Aria secca", "Tipica delle masse d'aria fredde o continentali.", 0.5, 1.5),
        V(None, "Umidità moderata", "Valori normali alle medie latitudini.", 1.5, 3),
        V(None, "Aria umida", "Masse d'aria calde e umide (estate, aria subtropicale).", 3, 5),
        V(None, "Aria molto umida", "Tropici e flussi di umidità intensi (\"fiumi atmosferici\"): carburante per piogge forti.", 5, None),
    ],
    nota="Centimetri d'acqua che si otterrebbero condensando tutto il vapore della colonna d'aria.")
M["airs_dust"] = dict(
    tipo="A", titolo="Indice di polvere AIRS", unita="", dec=0,
    fonte="NASA GIBS, colormap AIRS_Dust_Score", url=GIBS_CM % "AIRS_Dust_Score",
    scala={"gibs": "AIRS_Dust_Score"},
    voci=[
        V(None, "Polvere debole", "Presenza di polvere minerale appena rilevabile.", None, 420, certo=False),
        V(None, "Polvere moderata", "Strato di polvere evidente, tipico delle uscite sahariane.", 420, 460, certo=False),
        V(None, "Polvere forte", "Nube di polvere spessa.", 460, 500, certo=False),
        V(None, "Polvere molto forte", "Tempesta di polvere/sabbia.", 500, None, certo=False),
    ],
    nota="Indice adimensionale: più alto = più polvere; sotto 400 il layer è trasparente. Le fasce sono indicative.")
M["no2_omi"] = dict(
    tipo="A", titolo="NO2 troposferico (colonna)", unita="×10¹⁵ molecole/cm²", dec=1,
    fonte="NASA GIBS, colormap OMI_Nitrogen_Dioxide_Tropo_Column", url=GIBS_CM % "OMI_Nitrogen_Dioxide_Tropo_Column",
    scala={"gibs": "OMI_Nitrogen_Dioxide_Tropo_Column", "mult": 1e-15},
    voci=[
        V(None, "Aria pulita", "Valori di fondo: oceani, montagne, zone rurali lontane dal traffico.", None, 1),
        V(None, "Basso", "Campagne abitate e piccoli centri.", 1, 3),
        V(None, "Moderato", "Periferie, grandi strade, zone industriali minori.", 3, 6),
        V(None, "Elevato", "Grandi città e distretti industriali.", 6, 10),
        V(None, "Molto elevato", "Metropoli, grandi centrali e aree come la Pianura Padana in inverno.", 10, 20),
        V(None, "Estremo", "Le zone più inquinate del pianeta (oltre 2×10¹⁶).", 20, None),
    ],
    nota="È la quantità di NO2 in tutta la colonna d'aria bassa, non la concentrazione al suolo. Il NO2 viene "
         "soprattutto da traffico, riscaldamento e centrali. Fasce indicative.")
M["no2_tempo"] = dict(
    tipo="A", titolo="NO2 troposferico orario (TEMPO)", unita="×10¹⁵ molecole/cm²", dec=1,
    fonte="NASA GIBS, colormap TEMPO_NO2_Vertical_Column_Troposphere", url=GIBS_CM % "TEMPO_NO2_Vertical_Column_Troposphere",
    scala={"gibs": "TEMPO_NO2_Vertical_Column_Troposphere", "mult": 1e-15},
    voci=M["no2_omi"]["voci"][:5] + [V(None, "Estremo", "Le zone più inquinate (oltre 2×10¹⁶).", 20, None)],
    nota=M["no2_omi"]["nota"])
M["so2_tropomi"] = dict(
    tipo="A", titolo="SO2 (colonna)", unita="DU", dec=1,
    fonte="NASA GIBS, colormap OMI_SO2_Lower_Troposphere (usata per TROPOMI)", url=GIBS_CM % "OMI_SO2_Lower_Troposphere",
    scala={"gibs": "OMI_SO2_Lower_Troposphere"},
    voci=[
        V(None, "Nessun SO2 rilevabile", "Rumore di fondo dello strumento: nessuna sorgente significativa.", None, 2),
        V(None, "Debole", "Degassamento vulcanico o grandi impianti industriali.", 2, 5),
        V(None, "Moderato", "Pennacchio vulcanico o emissioni industriali forti.", 5, 15),
        V(None, "Forte", "Nube di un'eruzione.", 15, 32),
        V(None, "Molto forte", "Eruzione importante: SO2 in grande quantità.", 32, None),
    ],
    nota="DU = unità Dobson (1 DU = strato di 0,01 mm di gas puro al suolo). Fasce indicative.")
M["so2_omps"] = dict(
    tipo="A", titolo="SO2 nello strato limite (vicino al suolo)", unita="DU", dec=2,
    fonte="NASA GIBS, colormap OMPS_SO2_Planetary_Boundary_Layer", url=GIBS_CM % "OMPS_SO2_Planetary_Boundary_Layer",
    scala={"gibs": "OMPS_SO2_Planetary_Boundary_Layer"},
    voci=[
        V(None, "Fondo", "Nessuna sorgente rilevabile.", None, 1.5),
        V(None, "Debole", "Emissioni industriali o urbane deboli.", 1.5, 2.5),
        V(None, "Moderato", "Centrali a carbone, raffinerie, fonderie.", 2.5, 3.5),
        V(None, "Elevato", "Grandi sorgenti industriali o vulcani in degassamento.", 3.5, 4.5),
        V(None, "Molto elevato", "Sorgenti molto forti o eruzioni.", 4.5, None),
    ],
    nota="DU = unità Dobson. Valori piccoli e rumorosi: guardare le macchie ricorrenti, non il singolo pixel. Fasce indicative.")
M["so2_airs"] = dict(
    tipo="A", titolo="Indice SO2 AIRS (Prata)", unita="DU", dec=1,
    fonte="NASA GIBS, colormap AIRS_Prata_SO2_Index", url=GIBS_CM % "AIRS_Prata_SO2_Index",
    scala={"gibs": "AIRS_Prata_SO2_Index"},
    voci=[
        V(None, "Nessun segnale", "Nessun SO2 in quota rilevabile.", None, 2),
        V(None, "Debole", "Possibile SO2 vulcanico in quota.", 2, 10, certo=False),
        V(None, "Moderato", "Nube vulcanica con SO2.", 10, 30, certo=False),
        V(None, "Forte", "Nube vulcanica densa di SO2.", 30, None, certo=False),
    ],
    nota="Indice per le nubi vulcaniche in media-alta troposfera; non vede bene l'SO2 vicino al suolo.")
M["co_airs"] = dict(
    tipo="A", titolo="Monossido di carbonio a 500 hPa (~5,5 km)", unita="ppbv", dec=0,
    fonte="NASA GIBS, colormap AIRS_Carbon_Monoxide_Volume_Mixing_Ratio", url=GIBS_CM % "AIRS_Carbon_Monoxide_Volume_Mixing_Ratio",
    scala={"gibs": "AIRS_Carbon_Monoxide_Volume_Mixing_Ratio"},
    voci=[
        V(None, "Basso", "Aria pulita, tipica dell'emisfero sud e degli oceani.", None, 80),
        V(None, "Fondo normale", "Valori di fondo dell'emisfero nord.", 80, 120),
        V(None, "Elevato", "Inquinamento o fumo trasportato da lontano.", 120, 200),
        V(None, "Molto elevato", "Pennacchi di grandi incendi o forte inquinamento asiatico.", 200, None),
    ],
    nota="ppbv = parti per miliardo in volume. Il CO viene da combustioni (incendi, traffico, industria). Fasce indicative.")
M["co_mls"] = dict(
    tipo="A", titolo="Monossido di carbonio a 215 hPa (~11 km)", unita="ppbv", dec=0,
    fonte="NASA GIBS, colormap MLS_CO_215hPa", url=GIBS_CM % "MLS_CO_215hPa",
    scala={"gibs": "MLS_CO_215hPa"},
    voci=[
        V(None, "Basso", "Aria pulita in alta troposfera.", None, 60),
        V(None, "Fondo", "Valori normali in quota.", 60, 100),
        V(None, "Elevato", "Fumo o inquinamento sollevato in quota da temporali o monsoni.", 100, 150),
        V(None, "Molto elevato", "Pennacchi di grandi incendi portati in alta quota.", 150, None),
    ],
    nota="Dati molto grossolani (~165 km). Fasce indicative.")
M["ch4_airs"] = dict(
    tipo="A", titolo="Metano a 400 hPa (~7 km)", unita="ppbv", dec=0,
    fonte="NASA GIBS, colormap AIRS_Methane_Volume_Mixing_Ratio", url=GIBS_CM % "AIRS_Methane_Volume_Mixing_Ratio",
    scala={"gibs": "AIRS_Methane_Volume_Mixing_Ratio"},
    voci=[
        V(None, "Basso", "Sotto la media attuale.", None, 1800, certo=False),
        V(None, "Nella media", "Valori di fondo attuali in media troposfera.", 1800, 1880, certo=False),
        V(None, "Sopra la media", "Aria arricchita da sorgenti (zone umide, allevamenti, gas e petrolio, risaie).", 1880, 1950, certo=False),
        V(None, "Alto", "Accumuli di metano trasportati in quota.", 1950, None, certo=False),
    ],
    nota="ppbv = parti per miliardo. Il metano è un gas serra; in quota le differenze sono piccole. Fasce indicative.")
M["aod"] = dict(
    tipo="A", titolo="Spessore ottico degli aerosol (AOD)", unita="", dec=2,
    fonte="NASA GIBS, colormap MODIS_VIIRS_AOD", url=GIBS_CM % "MODIS_VIIRS_AOD",
    scala={"gibs": "MODIS_VIIRS_AOD"},
    voci=[
        V(None, "Aria limpidissima", "Pochissime particelle: visibilità ottima.", None, 0.1),
        V(None, "Aria limpida", "Leggera foschia di fondo.", 0.1, 0.3),
        V(None, "Foschia moderata", "Smog, sale marino o polvere leggera.", 0.3, 0.6),
        V(None, "Aria torbida", "Polvere sahariana, fumo di incendi o smog denso.", 0.6, 1.0),
        V(None, "Molto torbida", "Nube densa di polvere o fumo: sole velato.", 1.0, 2.0),
        V(None, "Estrema", "Tempesta di sabbia o fumo di grandi incendi.", 2.0, None),
    ],
    nota="Numero senza unità: quanto la luce del sole viene attenuata dalle particelle nell'aria. "
         "Dove ci sono nubi non viene calcolato (trasparente).")

# ------------------------------------------------------------------ NUBI E TEMPORALI (tipo A, EUMETView)
M["li_afa"] = dict(
    tipo="A", titolo="Fulmini: lampi accumulati", unita="lampi in 5 min", dec=0,
    fonte="EUMETView, legenda ufficiale Accumulated Flash Area (MTG Lightning Imager)", url=EUM_LEG % "mtg_fd%3Ali_afa",
    scala={"barra": "li_afa", "punti": [[0, 1], [257, 10], [487, 20]]},
    voci=[
        V(None, "Attività isolata", "Qualche lampo: temporale debole o all'inizio.", None, 2),
        V(None, "Attività moderata", "Temporale attivo.", 2, 5),
        V(None, "Attività intensa", "Temporale forte con fulmini frequenti.", 5, 10),
        V(None, "Attività molto intensa", "Nucleo temporalesco molto attivo: possibili grandine e raffiche.", 10, 20),
        V(None, "Attività estrema", "20 lampi o più in 5 minuti nello stesso punto: sistema temporalesco violento.", 20, None),
    ],
    nota="Numero di lampi visti dallo spazio nello stesso pixel in 5 minuti (include i lampi tra nube e nube). "
         "Fasce indicative.")
M["cth"] = dict(
    tipo="A", titolo="Altezza della cima delle nubi", unita="m", dec=0,
    fonte="EUMETView, legenda ufficiale Cloud Top Height (MSG)", url=EUM_LEG % "msg_fes%3Acth",
    scala={"barra": "cth", "punti": [[0, 320], [487, 16000]]},
    voci=[
        V(None, "Nubi basse", "Strati, nebbie alte, cumuli bassi: sotto i 2 km.", None, 2000),
        V(None, "Nubi medie", "Altocumuli e altostrati, tra 2 e 6 km.", 2000, 6000),
        V(None, "Nubi alte", "Cirri e nubi alte dei fronti, tra 6 e 10 km.", 6000, 10000),
        V(None, "Nubi molto alte", "Cumulonembi e incudini temporalesche, tra 10 e 13 km.", 10000, 13000),
        V(None, "Cime estreme", "Oltre 13 km: i temporali più potenti, tipici dei tropici.", 13000, None),
    ],
    nota="Stima dell'altezza della nube più alta, ricavata dal satellite e da dati meteo di supporto.")
M["frp"] = dict(
    tipo="A", titolo="Potenza radiativa del fuoco (FRP)", unita="MW", dec=0,
    fonte="EUMETView, legenda ufficiale FRP (stile mtg_fd:frp_default, colori per MW)", url=EUM_LEG % "mtg_fd%3Afrp",
    scala={"frp": True},
    voci=[
        V(None, "Fuoco debole", "Piccolo incendio, fuoco agricolo o torcia di gas: meno di 10 MW.", None, 10),
        V(None, "Fuoco moderato", "Incendio attivo di media grandezza.", 10, 50),
        V(None, "Fuoco intenso", "Incendio esteso e vigoroso.", 50, 150),
        V(None, "Fuoco molto intenso", "Grande incendio boschivo o fronte di fiamma molto attivo.", 150, None),
    ],
    nota="I quadratini segnano i pixel con fuoco attivo; colore (e dimensione) crescono con la potenza in megawatt. "
         "Fasce indicative.")

# ------------------------------------------------------------------ PRECIPITAZIONI (tipo A)
def _fasce_pioggia():
    return [
        V(None, "Pioggia debole", "Pioviggine o pioggia leggera.", None, 1),
        V(None, "Pioggia moderata", "Pioggia continua, ombrello necessario.", 1, 4),
        V(None, "Pioggia forte", "Rovesci intensi.", 4, 10),
        V(None, "Molto forte", "Temporale o rovescio violento: possibili allagamenti locali.", 10, 30),
        V(None, "Nubifragio", "Pioggia torrenziale: rischio alluvioni lampo.", 30, 50),
        V(None, "Eccezionale", "Oltre 50 mm/h: intensità rarissima.", 50, None),
    ]


PIOGGIA_NOTA = ("Intensità istantanea stimata da satellite: non è un radar, le piogge deboli e quelle sui rilievi "
                "possono sfuggire o essere sovrastimate.")
M["h40b"] = dict(
    tipo="A", titolo="Intensità di pioggia (H SAF H40B)", unita="mm/h", dec=1,
    fonte="EUMETView, legenda ufficiale H40B (GetLegendGraphic JSON)", url=EUM_LEG % "mtg_fd%3Ah40b",
    scala={"server": "h40b"}, voci=_fasce_pioggia(), nota=PIOGGIA_NOTA)
M["h60b"] = dict(
    tipo="A", titolo="Intensità di pioggia (H SAF H60B/H63)", unita="mm/h", dec=1,
    fonte="EUMETView, colori campionati dalla legenda ufficiale (stile msg_h60b_h63)", url=EUM_LEG % "msg_fes%3Ah60b",
    scala={"server": "h60b"}, voci=_fasce_pioggia(), nota=PIOGGIA_NOTA)
M["imerg"] = dict(
    tipo="A", titolo="Intensità di pioggia e neve (IMERG)", unita="mm/h", dec=1,
    fonte="NASA GIBS, colormap GPM_Precipitation_Rate (pioggia e neve)", url=GIBS_CM % "GPM_Precipitation_Rate",
    scala={"gibs": "GPM_Precipitation_Rate"},
    voci=_fasce_pioggia() + [V("#4F73C4", "Neve (azzurro → viola)", "Precipitazione stimata come neve: stessa scala "
                                "in mm/h d'acqua fusa, colori dall'azzurro chiaro (debole) al viola scuro (intensa).",
                                alt=["#92EEF2", "#5F9ED7", "#4A37DB", "#770BD1"], gruppo=1)],
    nota="Stima globale combinata (microonde + infrarosso). La neve ha una scala di colori a parte (azzurro → viola): "
         "il riquadro sotto il cursore indica se il pixel è pioggia o neve. " + PIOGGIA_NOTA)
M["accumulo"] = dict(
    tipo="A", titolo="Pioggia caduta nell'intervallo (accumulo)", unita="mm", dec=1,
    fonte="Calcolato dal server sommando i valori reali (WCS) delle immagini dell'animazione",
    scala={"server": "accumulo"},
    voci=[
        V(None, "Poca pioggia", "Qualche goccia o pioggia debole.", None, 2),
        V(None, "Pioggia moderata", "Terreno bagnato, pozzanghere.", 2, 10),
        V(None, "Pioggia abbondante", "Accumulo importante.", 10, 30),
        V(None, "Pioggia molto abbondante", "Possibili allagamenti e piene di torrenti.", 30, 75),
        V(None, "Accumulo eccezionale", "Rischio alluvione.", 75, None),
    ],
    nota="Millimetri caduti dal primo fotogramma dell'animazione all'istante mostrato (1 mm = 1 litro al m²).")

# ------------------------------------------------------------------ NOTTE (tipo A)
M["dnb"] = dict(
    tipo="A", titolo="Banda giorno/notte VIIRS (contrasto quasi costante)", unita="livello 0-255", dec=0,
    fonte="NASA GIBS, colormap VIIRS_SNPP_DayNightBand_ENCC", url=GIBS_CM % "VIIRS_SNPP_DayNightBand_ENCC",
    scala={"gibs": "VIIRS_SNPP_DayNightBand_ENCC"},
    voci=[
        V(None, "Buio", "Nessuna luce: mare o terra senza luna e senza luci.", None, 30),
        V(None, "Luce debole", "Nubi o neve illuminate dalla luna, luci sparse, bagliore dell'aria.", 30, 110),
        V(None, "Luce media", "Nubi illuminate dalla luna, paesi e strade.", 110, 200),
        V(None, "Luce intensa", "Città, incendi, pescherecci con lampade, torce di gas, aurore.", 200, None),
    ],
    nota="Immagine notturna \"come la vede l'occhio\": la luminosità è rinormalizzata (contrasto quasi costante), "
         "quindi il livello non è una misura assoluta.")
M["dnb_rad"] = dict(
    tipo="A", titolo="Radianza notturna VIIRS", unita="nW/(cm²·sr)", dec=1,
    fonte="NASA GIBS, colormap VIIRS_DayNightBand_At_Sensor_Radiance", url=GIBS_CM % "VIIRS_DayNightBand_At_Sensor_Radiance",
    scala={"gibs": "VIIRS_DayNightBand_At_Sensor_Radiance"},
    voci=[
        V(None, "Buio", "Nessuna luce rilevabile.", None, 0.5),
        V(None, "Luce debole", "Nubi illuminate dalla luna, piccoli paesi.", 0.5, 5),
        V(None, "Luci urbane", "Centri abitati e strade illuminate.", 5, 20),
        V(None, "Luce molto intensa", "Grandi città, torce di gas, incendi.", 20, None),
    ],
    nota="Radianza misurata dal sensore (non corretta): valore reale. Fasce indicative.")

# ------------------------------------------------------------------ OCEANO (tipo A)
M["sst"] = dict(
    tipo="A", titolo="Temperatura superficiale del mare", unita="°C", dec=1,
    fonte="NASA GIBS, colormap GHRSST/MODIS/VIIRS Sea_Surface_Temperature", url=GIBS_CM % "GHRSST_Sea_Surface_Temperature",
    scala={"gibs": "GHRSST_Sea_Surface_Temperature"},
    voci=[
        V(None, "Acque polari", "Mari ghiacciati o quasi.", None, 5),
        V(None, "Acque fredde", "Mari subpolari, correnti fredde.", 5, 12),
        V(None, "Acque fresche", "Es. Mediterraneo in inverno (13-15 °C), Atlantico del nord.", 12, 18),
        V(None, "Acque temperate", "Mari delle medie latitudini in estate.", 18, 24),
        V(None, "Acque calde", "Mari subtropicali, Mediterraneo in piena estate.", 24, 26.5),
        V(None, "Calde: soglia dei cicloni", "Dai 26,5 °C in su l'oceano può alimentare cicloni tropicali.", 26.5, 29),
        V(None, "Molto calde", "Mari tropicali più caldi: grande energia per i temporali.", 29, None),
    ],
    nota="Temperatura dei primi millimetri/centimetri d'acqua.")
M["sst_l2"] = dict(M["sst"], fonte="NASA GIBS, colormap MODIS/VIIRS Sea_Surface_Temperature",
                   url=GIBS_CM % "MODIS_Sea_Surface_Temperature", scala={"gibs": "MODIS_Sea_Surface_Temperature"},
                   nota="Misura di un singolo passaggio: dove ci sono nubi non c'è dato (trasparente).")
M["sst_l2_viirs"] = dict(M["sst_l2"], url=GIBS_CM % "VIIRS_Sea_Surface_Temperature",
                         scala={"gibs": "VIIRS_Sea_Surface_Temperature"})
M["sst_anom"] = dict(
    tipo="A", titolo="Anomalia della temperatura del mare", unita="°C", dec=1,
    fonte="NASA GIBS, colormap GHRSST_Sea_Surface_Temperature_Anomalies", url=GIBS_CM % "GHRSST_Sea_Surface_Temperature_Anomalies",
    scala={"gibs": "GHRSST_Sea_Surface_Temperature_Anomalies"},
    voci=[
        V(None, "Molto più fredda", "Oltre 2 °C sotto la media del periodo.", None, -2),
        V(None, "Più fredda", "Da 1 a 2 °C sotto la media.", -2, -1),
        V(None, "Poco più fredda", "Leggermente sotto la media.", -1, -0.3),
        V(None, "Nella norma", "Vicina alla media climatica.", -0.3, 0.3),
        V(None, "Poco più calda", "Leggermente sopra la media.", 0.3, 1),
        V(None, "Più calda", "Da 1 a 2 °C sopra la media.", 1, 2),
        V(None, "Molto più calda", "Oltre 2 °C sopra la media: possibile ondata di calore marina.", 2, None),
    ],
    nota="Differenza rispetto alla media climatica dello stesso giorno dell'anno.")

# ------------------------------------------------------------------ NEVE E GHIACCIO (tipo A)
M["ndsi"] = dict(
    tipo="A", titolo="Indice di neve (NDSI)", unita="NDSI ×100", dec=0,
    fonte="NASA GIBS, colormap MODIS_NDSI_Snow_Cover", url=GIBS_CM % "MODIS_NDSI_Snow_Cover",
    scala={"gibs": "MODIS_NDSI_Snow_Cover"},
    voci=[
        V(None, "Neve scarsa o dubbia", "Indice basso: neve sporca, sparsa o mescolata a vegetazione.", None, 20),
        V(None, "Neve parziale", "Copertura discontinua.", 20, 50),
        V(None, "Neve estesa", "Suolo in gran parte innevato.", 50, 80),
        V(None, "Neve piena", "Manto nevoso continuo e pulito.", 80, None),
    ],
    nota="Indice di neve normalizzato da 0 a 100 (non è esattamente la percentuale di copertura). "
         "Nubi, notte e terra senza neve sono trasparenti.")
M["ghiaccio_mare"] = dict(
    tipo="A", titolo="Concentrazione del ghiaccio marino", unita="%", dec=0,
    fonte="NASA GIBS, colormap GHRSST_Sea_Ice_Concentration", url=GIBS_CM % "GHRSST_Sea_Ice_Concentration",
    scala={"gibs": "GHRSST_Sea_Ice_Concentration"},
    voci=[
        V(None, "Mare libero o ghiaccio sparso", "Sotto il 15% (il limite usato per il \"bordo del ghiaccio\").", None, 15),
        V(None, "Ghiaccio rado", "Banchi sparsi, navigabile con cautela.", 15, 40),
        V(None, "Ghiaccio medio", "Copertura a metà.", 40, 70),
        V(None, "Ghiaccio compatto", "Banchisa fitta.", 70, 90),
        V(None, "Banchisa continua", "Mare quasi completamente ghiacciato.", 90, None),
    ],
    nota="Percentuale di superficie del mare coperta da ghiaccio.")

# ------------------------------------------------------------------ CLASSI UFFICIALI (tipo A, colori esatti)
M["opera_dist"] = dict(
    tipo="A", titolo="Disturbo della vegetazione (OPERA DIST-ALERT)", unita="", dec=0, classi=True,
    fonte="NASA GIBS, colormap OPERA_Vegetation_Disturbance_Status (classi ufficiali)", url=GIBS_CM % "OPERA_Vegetation_Disturbance_Status",
    voci=[
        V("#121212", "Nessun disturbo", "Vegetazione invariata rispetto al riferimento."),
        V("#005555", "Prima rilevazione, perdita < 50%", "Primo segnale di perdita di vegetazione, lieve: va confermato dai passaggi successivi."),
        V("#897F4E", "Provvisorio, perdita < 50%", "Perdita lieve vista più volte, non ancora confermata."),
        V("#DEE043", "Confermato, perdita < 50%", "Perdita lieve confermata e ancora in corso."),
        V("#008888", "Prima rilevazione, perdita ≥ 50%", "Primo segnale di perdita forte (es. incendio, taglio, frana)."),
        V("#E48727", "Provvisorio, perdita ≥ 50%", "Perdita forte vista più volte, non ancora confermata."),
        V("#E01B07", "Confermato, perdita ≥ 50%", "Perdita forte confermata e in corso: tipico di un incendio recente."),
        V("#777777", "Concluso, perdita < 50%", "Disturbo lieve confermato e terminato."),
        V("#DDDDDD", "Concluso, perdita ≥ 50%", "Disturbo forte confermato e terminato."),
    ],
    nota="Percentuale = quanta copertura vegetale è stata persa nel pixel di 30 m. I colori sono quelli ufficiali.")

# ------------------------------------------------------------------ PUNTI FUOCO (tipo B: simboli)
M["fuochi_viirs"] = dict(
    tipo="B", titolo="Fuochi attivi VIIRS (punti)", unita="",
    fonte="NASA FIRMS/GIBS: colore dei punti rilevato sui tasselli WMS (11-10-26)",
    voci=[
        V("#F02828", "Fuoco attivo", "Pixel di 375 m dove il satellite ha visto un'anomalia di calore: incendio, fuoco "
          "agricolo, torcia di gas o grande impianto industriale.", alt=["#EE2727", "#D81818", "#B80808"]),
        V("#7F0000", "Bordo del punto", "Contorno sfumato del simbolo (stesso fuoco).", alt=["#780808", "#980808"]),
    ],
    nota="Un punto non dice quanto è grande l'incendio: vedi il layer FRP o i falsi colori fuoco/fumo. "
         "Il passaggio notturno è il più affidabile (niente riflessi del sole).")
M["fuochi_modis"] = dict(
    tipo="B", titolo="Fuochi attivi MODIS (punti)", unita="",
    fonte="NASA FIRMS/GIBS: colore dei punti rilevato sui tasselli WMS (11-10-26)",
    voci=[
        V("#EC6210", "Fuoco attivo", "Pixel di 1 km con anomalia di calore (incendio, fuoco agricolo, torcia di gas).",
          alt=["#E86808", "#E85808", "#D84808"]),
        V("#7F0000", "Bordo del punto", "Contorno sfumato del simbolo (stesso fuoco).", alt=["#AA5500"]),
    ],
    nota="MODIS (Terra e Aqua): pixel più grandi di VIIRS, vede meno i fuochi piccoli.")
M["rdt"] = dict(
    tipo="B", titolo="Temporali in rapido sviluppo (RDT)", unita="",
    fonte="EUMETView, stile ufficiale msg_fes:rdt (contorno rosso, etichetta = BTmin)",
    voci=[
        V("#FF0000", "Cella temporalesca", "Contorno rosso: nube convettiva individuata e seguita dal software NWC SAF. "
          "Il numero al centro è la temperatura minima della cima (°C, arrotondata): più è bassa, più il temporale è profondo."),
        V("#FFFFFF", "Alone dell'etichetta", "Sfondo bianco del numero."),
    ],
    nota="Il numero compare solo sulle celle abbastanza grandi alla scala della mappa.", certo_nota=False)
M["tracce_metop"] = dict(
    tipo="B", titolo="Impronta delle orbite Metop-B", unita="",
    fonte="EUMETView, stile ufficiale metop_avhrr_orbital_tracks (bordo e riempimento #EE6723 al 50%)",
    voci=[
        V("#EE6723", "Striscia osservata", "Area vista in una delle ultime orbite: dentro il contorno il mosaico è fresco.",
          alt=["#F2A276", "#F7B48E"]),
    ],
    nota="Serve a capire quali parti del mosaico Metop sono recenti e dove passano le giunture fra le orbite.")

# ------------------------------------------------------------------ COLORI REALI / VISIBILE (tipo C)
def _voci_colori_reali(alta=False):
    v = [
        V("#F4F6F8", "Bianco brillante", "Nubi spesse oppure neve e ghiaccio. La neve sta ferma, segue i rilievi e lascia "
          "le valli scure; le nubi si muovono e hanno bordi sfumati.", alt=["#FFFFFF", "#E6EAEE"]),
        V("#C3CBD3", "Grigio-azzurro chiaro", "Nubi sottili, veli di cirri o foschia: la superficie si intravede.",
          alt=["#B0BAC4", "#D2D8DE"]),
        V("#8E8474", "Grigio-bruno velato", "Fumo di incendi (spesso in pennacchi che partono da un punto) o smog: velo "
          "che sfuma i colori sotto.", alt=["#80786A", "#9C9282"], certo=False),
        V("#CDB98F", "Beige-giallastro velato", "Possibile polvere in sospensione sopra mare o terra.",
          alt=["#C2AA78", "#D8C8A0"], certo=False),
        V("#0D1B2E", "Blu molto scuro / nero", "Mare e laghi profondi.", alt=["#0A1424", "#142640", "#081018"]),
        V("#3C7C86", "Turchese / verde acqua", "Acque basse, torbide o fioriture di alghe; sedimenti dei fiumi.",
          alt=["#4A9094", "#2E6670"], certo=False),
        V("#3E5A2C", "Verde scuro", "Foreste e vegetazione fitta.", alt=["#324A24", "#4C6A34"]),
        V("#7E8656", "Verde-oliva", "Campi, pascoli e vegetazione rada.", alt=["#8A8C5C", "#6E7A4A"]),
        V("#A68D66", "Marrone-ocra", "Terreno scoperto: suolo secco, campi arati, rocce.", alt=["#9A8060", "#B49A72"]),
        V("#D8BE92", "Sabbia chiara", "Terreno molto chiaro: sabbia, rocce chiare.", alt=["#E2C8A0", "#CCAE80"]),
        V("#3A3027", "Bruno molto scuro", "Zone molto scure: ombre di nubi e rilievi, boschi fitti, tetti scuri in città; "
          "se la macchia è estesa e ha bordi netti può essere un'area bruciata di recente o una colata lavica.",
          alt=["#2E2620", "#463A30"], certo=False),
        V("#8E8E8E", "Grigio neutro", "Città, strade, cave e rocce nude (si riconoscono bene solo ad alta risoluzione).",
          alt=["#7E7E7E", "#A0A0A0", "#6E6F68", "#B0AFA7", "#BCBBB3", "#C8C7BF"], certo=False),
    ]
    if alta:
        v.append(V("#000000", "Nero / vuoto", "Nessun dato quel giorno (la striscia del satellite non è passata) o nubi "
                   "mascherate: torna indietro di qualche giorno."))
    else:
        v.append(V("#000000", "Nero", "Notte o nessun dato."))
    return v


C_NOTA = ("Immagine a colori come la vedrebbe l'occhio dallo spazio. Bianco = nubi o neve; i colori del suolo dipendono "
          "da stagione e luce. Il riquadro indica il colore tipico più vicino: è una lettura indicativa.")
M["colori_reali"] = dict(tipo="C", titolo="Colori reali: come leggerli", fonte="Interpretazione standard (NASA Worldview, EUMETSAT True Colour RGB)",
                         voci=_voci_colori_reali(), nota=C_NOTA)
M["colori_reali_hls"] = dict(tipo="C", titolo="Colori reali 30 m: come leggerli",
                             fonte="Interpretazione standard (NASA HLS/Worldview)", voci=_voci_colori_reali(alta=True),
                             nota="A 30 m si riconoscono città, campi e cicatrici di incendi. " + C_NOTA)
M["visibile_grigi"] = dict(
    tipo="C", titolo="Visibile in scala di grigi: come leggerlo",
    fonte="Interpretazione standard del canale visibile 0,6 µm (EUMETSAT, NOAA)",
    voci=[
        V("#FFFFFF", "Bianco brillante", "Nubi spesse (temporali, strati densi) o neve fresca: riflettono molta luce.",
          alt=["#F0F0F0", "#E6E6E6"]),
        V("#C8C8C8", "Grigio chiaro", "Nubi medie o nebbie e strati bassi (lisci e uniformi).", alt=["#B4B4B4", "#D2D2D2"]),
        V("#8C8C8C", "Grigio", "Nubi sottili, cirri o terreno grigio.", alt=["#A0A0A0", "#787878"]),
        V("#505050", "Grigio scuro", "Terreno e vegetazione senza nubi.", alt=["#3C3C3C", "#646464"]),
        V("#141414", "Nero", "Mare senza nubi, oppure notte (nessuna luce solare).", alt=["#000000", "#282828"]),
    ],
    nota="Il visibile misura la luce del sole riflessa: funziona solo di giorno. Le nubi spesse sono le più bianche, "
         "ma il visibile non distingue nubi alte da basse (per quello serve l'infrarosso).")
M["black_marble"] = dict(
    tipo="C", titolo="Luci delle città (Black Marble)", fonte="NASA Black Marble 2016 (composito senza nubi)",
    voci=[
        V("#FFE9A8", "Giallo-bianco brillante", "Centri delle grandi città: luce artificiale intensa.", alt=["#FFFFFF", "#FFF2C8"]),
        V("#E0A040", "Arancio", "Città medie, periferie, autostrade illuminate.", alt=["#C8862E", "#F0B860"]),
        V("#6A4A20", "Arancio scuro", "Paesi e piccoli centri.", alt=["#503818", "#806030"]),
        V("#0A1430", "Blu notte", "Mare e zone disabitate senza luci.", alt=["#05081A", "#101C40"]),
        V("#1E2A3A", "Grigio-blu scuro", "Terre emerse di notte senza luci.", alt=["#18202C"], certo=False),
    ],
    nota="Immagine fissa (2016): non mostra la situazione di stanotte.")
M["blue_marble"] = dict(
    tipo="C", titolo="Blue Marble: come leggerlo", fonte="NASA Blue Marble (composito senza nubi)",
    voci=[
        V("#2E4A22", "Verde scuro", "Foreste.", alt=["#243C1A", "#385A2A"]),
        V("#8C7A50", "Marrone", "Steppe, savane secche, rilievi.", alt=["#7A6A44", "#9C8A5C"]),
        V("#D6C094", "Sabbia", "Terreno color sabbia.", alt=["#E0CCA4", "#C8B080"]),
        V("#F2F4F6", "Bianco", "Ghiacci e nevi perenni.", alt=["#FFFFFF", "#E2E8EE"]),
        V("#0A2350", "Blu scuro", "Oceani profondi (fondali illustrati nella versione con batimetria).", alt=["#06183A", "#123070"]),
        V("#3C8CB4", "Azzurro", "Mari bassi e piattaforma continentale.", alt=["#4AA0C4", "#2E78A0"], certo=False),
    ],
    nota="Sfondo fisso senza nubi: non è un'immagine del giorno.")
M["osm_terra_mare"] = dict(
    tipo="C", titolo="Terre e mari (OSM)", fonte="NASA GIBS OSM_Land_Water_Map: colori rilevati sui tasselli (11-10-26)",
    voci=[
        V("#4B4B4B", "Grigio scuro", "Terre emerse."),
        V("#808080", "Grigio medio", "Mari, laghi e grandi fiumi."),
    ],
    nota="Sfondo neutro per far risaltare i prodotti colorati sovrapposti.")
M["coste"] = dict(
    tipo="C", titolo="Linee di costa", fonte="NASA GIBS Coastlines_15m (OpenStreetMap): colori rilevati sui tasselli",
    voci=[
        V("#001111", "Linea scura", "Linea di costa (bordo interno).", alt=["#081818", "#182828"]),
        V("#B8C8C8", "Linea chiara", "Alone chiaro della costa, per vederla anche su sfondi scuri.", alt=["#A8B8B8", "#C8D4D4"]),
    ])
M["confini"] = dict(
    tipo="C", titolo="Confini e strade", fonte="NASA GIBS Reference_Features_15m",
    voci=[
        V("#FFFFFF", "Linee chiare", "Confini di stato e di regione, strade principali (compaiono avvicinando la mappa).",
          alt=["#E0E0E0", "#C8C8C8"], certo=False),
    ])
M["etichette"] = dict(
    tipo="C", titolo="Nomi di luoghi", fonte="NASA GIBS Reference_Labels_15m",
    voci=[
        V("#FFFFFF", "Testo chiaro", "Nomi di città, stati e luoghi (compaiono avvicinando la mappa).", alt=["#E8E8E8"], certo=False),
        V("#000000", "Contorno scuro", "Bordo del testo, per leggerlo su qualsiasi sfondo.", alt=["#202020"], certo=False),
    ])
M["reticolo"] = dict(
    tipo="C", titolo="Reticolo lat/lon", fonte="NASA GIBS Graticule_15m: colori rilevati sui tasselli",
    voci=[
        V("#839494", "Linee grigie", "Meridiani e paralleli.", alt=["#889898", "#486868", "#2E3E3E"]),
        V("#AEAEAE", "Numeri", "Valori di latitudine e longitudine.", alt=["#D1D1D1"]),
    ])

# =====================================================================================================
# Composizioni RGB (tipo B) - definite in legende_rgb (guide ufficiali EUMETSAT/CIRA/NASA)
# =====================================================================================================
from legende_rgb import MODELLI_RGB  # noqa: E402

M.update(MODELLI_RGB)

# =====================================================================================================
# Layer -> modello
# =====================================================================================================
LAYER_MODELLO = {
    # visibile / colori reali
    "MTG_GeoColour": "geocolour_mtg", "GOES-East_ABI_GeoColor": "geocolor", "GOES-West_ABI_GeoColor": "geocolor",
    "MTG_TrueColour": "colori_reali", "MTG_VIS06": "visibile_grigi", "MSG_HRV_Europa": "eview",
    "GOES-East_ABI_Band2_Red_Visible_1km": "visibile_grigi", "GOES-West_ABI_Band2_Red_Visible_1km": "visibile_grigi",
    "Himawari_AHI_Band3_Red_Visible_1km": "visibile_grigi",
    "VIIRS_NOAA21_CorrectedReflectance_TrueColor": "colori_reali", "VIIRS_NOAA20_CorrectedReflectance_TrueColor": "colori_reali",
    "VIIRS_SNPP_CorrectedReflectance_TrueColor": "colori_reali", "MODIS_Terra_CorrectedReflectance_TrueColor": "colori_reali",
    "MODIS_Aqua_CorrectedReflectance_TrueColor": "colori_reali",
    # infrarosso
    "MTG_IR105": "ir_eum", "MSG_IR108": "ir_eum", "MSG_IR039": "ir039",
    "GOES-East_ABI_Band13_Clean_Infrared": "ir_gibs", "GOES-West_ABI_Band13_Clean_Infrared": "ir_gibs",
    "Himawari_AHI_Band13_Clean_Infrared": "ir_gibs", "MODIS_Terra_Cloud_Top_Temp_Day": "modis_ctt",
    # vapore
    "MSG_WV062": "wv", "MSG_AirMass": "airmass", "MSG_TropicalAirmass": "airmass_tropicale",
    "GOES-East_ABI_Air_Mass": "airmass", "GOES-West_ABI_Air_Mass": "airmass", "Himawari_AHI_Air_Mass": "airmass",
    "MODIS_Terra_Water_Vapor_5km_Day": "modis_wv",
    # nubi e temporali
    "MTG_Fulmini": "li_afa", "MSG_RDT": "rdt", "MSG_Convection": "convezione", "MTG_CloudType": "tipo_nubi",
    "MTG_CloudPhase": "fase_nubi", "MTG_Nebbia": "nebbia", "MSG_IODC_Nebbia": "nebbia", "MSG_AltezzaNubi": "cth",
    # polveri
    "MTG_Dust": "polveri", "MSG_Dust": "polveri", "MSG_IODC_Dust": "polveri", "GeoRing_Dust": "polveri",
    "MSG_Cenere": "cenere", "GOES-East_ABI_Dust": "polveri", "GOES-West_ABI_Dust": "polveri",
    "AIRS_L2_Dust_Score_Day": "airs_dust", "AIRS_L2_Dust_Score_Night": "airs_dust",
    # Metop
    "MetopB_AVHRR_ColoriNebbia": "metop_colori_nebbia", "MetopC_AVHRR_ColoriNebbia": "metop_colori_nebbia",
    "MetopB_AVHRR_Nubi": "avhrr_124", "MetopC_AVHRR_Nubi": "avhrr_124",
    "MetopB_AVHRR_IR108": "ir_metop", "MetopC_AVHRR_IR108": "ir_metop", "MetopB_Tracce": "tracce_metop",
    # incendi
    "MTG_FireTemp": "temp_fuoco", "MTG_FRP": "frp", "GOES-East_ABI_FireTemp": "temp_fuoco", "GOES-West_ABI_FireTemp": "temp_fuoco",
    "VIIRS_NOAA20_Thermal_Anomalies_375m_All": "fuochi_viirs", "VIIRS_NOAA21_Thermal_Anomalies_375m_All": "fuochi_viirs",
    "VIIRS_SNPP_Thermal_Anomalies_375m_All": "fuochi_viirs", "MODIS_Combined_Thermal_Anomalies_All": "fuochi_modis",
    # alta risoluzione
    "HLS_S30_Nadir_BRDF_Adjusted_Reflectance": "colori_reali_hls", "HLS_L30_Nadir_BRDF_Adjusted_Reflectance": "colori_reali_hls",
    "OPERA_L3_DIST-ALERT-HLS_Color_Index": "opera_dist",
    "VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1": "fuoco_fumo", "VIIRS_NOAA21_CorrectedReflectance_BandsM11-I2-I1": "fuoco_fumo",
    "VIIRS_SNPP_CorrectedReflectance_BandsM11-I2-I1": "fuoco_fumo",
    "VIIRS_NOAA21_SurfaceReflectance_BandsI1-I2-I1": "viirs_i1i2i1", "VIIRS_SNPP_SurfaceReflectance_BandsI1-I2-I1": "viirs_i1i2i1",
    "MODIS_Terra_CorrectedReflectance_Bands721": "fuoco_fumo", "MODIS_Aqua_CorrectedReflectance_Bands721": "fuoco_fumo",
    "VIIRS_NOAA20_Thermal_Anomalies_375m_Day": "fuochi_viirs", "VIIRS_NOAA20_Thermal_Anomalies_375m_Night": "fuochi_viirs",
    "VIIRS_NOAA21_Thermal_Anomalies_375m_Day": "fuochi_viirs", "VIIRS_NOAA21_Thermal_Anomalies_375m_Night": "fuochi_viirs",
    # inquinamento
    "TROPOMI_L2_Nitrogen_Dioxide_Tropospheric_Column": "no2_omi", "OMI_Nitrogen_Dioxide_Tropo_Column": "no2_omi",
    "TEMPO_L3_NO2_Vertical_Column_Troposphere": "no2_tempo", "TROPOMI_L2_Sulfur_Dioxide_Total_Vertical_Column": "so2_tropomi",
    "OMPS_SO2_Planetary_Boundary_Layer": "so2_omps", "AIRS_Prata_SO2_Index_Day": "so2_airs",
    "AIRS_L2_Carbon_Monoxide_500hPa_Volume_Mixing_Ratio_Day": "co_airs", "MLS_CO_215hPa_Day": "co_mls",
    "AIRS_L2_Methane_400hPa_Volume_Mixing_Ratio_Day": "ch4_airs",
    "MODIS_Terra_AOD_Deep_Blue_Combined": "aod", "MODIS_Aqua_AOD_Deep_Blue_Combined": "aod",
    "VIIRS_NOAA20_AOD_Deep_Blue_Land_Ocean": "aod", "VIIRS_NOAA21_AOD_Deep_Blue_Land_Ocean": "aod",
    # notte
    "VIIRS_NOAA20_DayNightBand": "dnb", "VIIRS_NOAA21_DayNightBand": "dnb", "VIIRS_SNPP_DayNightBand": "dnb",
    "VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance": "dnb_rad", "VIIRS_Black_Marble": "black_marble",
    # oceano
    "GHRSST_L4_MUR_Sea_Surface_Temperature": "sst", "GHRSST_L4_MUR_Sea_Surface_Temperature_Anomalies": "sst_anom",
    "VIIRS_SNPP_L2_Sea_Surface_Temp_Night": "sst_l2_viirs", "MODIS_Aqua_L2_Sea_Surface_Temp_Day": "sst_l2",
    # precipitazioni
    "MTG_Precipitazioni": "h40b", "MSG_Precipitazioni": "h60b", "MSG_IODC_Precipitazioni": "h60b",
    "IMERG_Precipitation_Rate_30min": "imerg", "IMERG_Precipitation_Rate": "imerg",
    "_accumulo": "accumulo",  # pseudo-layer: legenda dell'accumulo calcolato dal server (modalita' Accumulo)
    # neve
    "MTG_Neve": "neve_rgb", "VIIRS_NOAA20_NDSI_Snow_Cover": "ndsi", "MODIS_Terra_NDSI_Snow_Cover": "ndsi",
    "GHRSST_L4_MUR_Sea_Ice_Concentration": "ghiaccio_mare",
    # sfondi e riferimenti
    "BlueMarble_ShadedRelief_Bathymetry": "blue_marble", "BlueMarble_NextGeneration": "blue_marble",
    "OSM_Land_Water_Map": "osm_terra_mare", "Coastlines_15m": "coste", "Reference_Features_15m": "confini",
    "Reference_Labels_15m": "etichette", "Graticule_15m": "reticolo",
}
# layer non elencati (es. risoluzione piena, layer GIBS aggiunti in futuro): regole sul nome
REGOLE = [
    (r"^NAT_.*_ir$", "ir_nativo"), (r"^NAT_.*_vis$", "visibile_grigi"), (r"^NAT_.*_truecolor$", "colori_reali"),
    (r"^NAT_.*_geocolor$", "geocolor_nativo"),
    (r"GeoColou?r", "geocolor"), (r"Air_?Mass", "airmass"), (r"_Dust$", "polveri"), (r"FireTemp", "temp_fuoco"),
    (r"Band13|Clean_Infrared", "ir_gibs"), (r"Thermal_Anomalies", "fuochi_viirs"), (r"TrueColor", "colori_reali"),
    (r"Bands721|BandsM11", "fuoco_fumo"), (r"AOD", "aod"), (r"Sea_Surface_Temp", "sst_l2"),
]


def modello_di(lid):
    k = LAYER_MODELLO.get(lid)
    if k:
        return k
    for rx, k in REGOLE:
        if re.search(rx, lid or ""):
            return k
    return None


# =====================================================================================================
# Costruzione delle scale (colori -> valori)
# =====================================================================================================
_lock = threading.Lock()
_cache = {}   # modello -> (istante, legenda)


def _scarica(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "MeteoSat/1.0 (legende)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _xml_gibs(nome):
    os.makedirs(CACHE_CM, exist_ok=True)
    p = os.path.join(CACHE_CM, nome + ".xml")
    eta = time.time() - os.path.getmtime(p) if os.path.exists(p) else None
    if eta is None or eta > CM_TTL:
        try:
            dati = _scarica(GIBS_CM % nome)
            ET.fromstring(dati)  # valida
            tmp = p + ".tmp"
            with open(tmp, "wb") as f:
                f.write(dati)
            os.replace(tmp, p)
        except Exception as e:
            log.warning("colormap %s non scaricata: %s (uso la copia su disco se c'e')", nome, e)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return f.read()


_RE_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?INF", re.I)


def _intervallo(s):
    if s is None:
        return None
    nums = _RE_NUM.findall(s)
    if not nums:
        return None

    def f(x):
        return None if "INF" in x.upper() else float(x)
    a = f(nums[0])
    b = f(nums[1]) if len(nums) > 1 else a
    return a, b


def _scala_gibs(nome, mult=1.0, add=0.0):
    dati = _xml_gibs(nome)
    if not dati:
        return None
    root = ET.fromstring(dati)
    entries, gruppi = [], []
    units = None
    for cm in root.iter("ColorMap"):
        titolo = cm.get("title") or ""
        es = cm.findall(".//ColorMapEntry")
        if not es or all(e.get("transparent") == "true" for e in es):
            continue
        gi = len(gruppi)
        gruppi.append(titolo)
        units = units or cm.get("units")
        for e in es:
            if e.get("transparent") == "true" or e.get("nodata") == "true":
                continue
            iv = _intervallo(e.get("value") or e.get("sourceValue"))
            if iv is None:
                continue
            r, g, b = (int(x) for x in e.get("rgb").split(","))
            lo, hi = iv

            def cv(x):
                return None if x is None else x * mult + add
            lo, hi = cv(lo), cv(hi)
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
            entries.append([r, g, b, _arr(lo), _arr(hi), gi])
    if not entries:
        return None
    sc = {"entries": entries}
    if len(gruppi) > 1:
        sc["gruppi"] = [_trad_gruppo(g) for g in gruppi]
    else:
        for e in entries:
            e.pop()
    return sc


def _trad_gruppo(t):
    return {"Rain Rate": "pioggia", "Snow Rate": "neve"}.get(t, t)


def _arr(x):
    if x is None:
        return None
    return float("%.6g" % x)


def _barra_eum(nome):
    """Barra colori ufficiale EUMETView (immagine 640x80) -> lista di colori, una per colonna (x=148..635).
    Decodifica PNG con la sola libreria standard (zlib); cache su disco in JSON."""
    os.makedirs(CACHE_CM, exist_ok=True)
    p = os.path.join(CACHE_CM, "eum_%s.json" % nome)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    layer = {"li_afa": "mtg_fd%3Ali_afa", "cth": "msg_fes%3Acth"}[nome]
    try:
        cols = _png_riga(_scarica(EUM_LEG % layer), 20)[148:636]
    except Exception as e:
        log.warning("legenda EUMETView %s non leggibile: %s", nome, e)
        return None
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cols, f)
    return cols


def _png_riga(dati, y):
    """Decodifica minimale PNG (8 bit, colore RGB/RGBA o palette) e restituisce la riga y come [[r,g,b],...]."""
    import struct
    import zlib
    assert dati[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, plte = 8, b"", None
    w = h = ct = bd = None
    while pos < len(dati):
        n, t = struct.unpack(">I4s", dati[pos:pos + 8])
        c = dati[pos + 8:pos + 8 + n]
        if t == b"IHDR":
            w, h, bd, ct = struct.unpack(">IIBB", c[:10])
        elif t == b"PLTE":
            plte = [list(c[i:i + 3]) for i in range(0, len(c), 3)]
        elif t == b"IDAT":
            idat += c
        pos += 12 + n
    if bd != 8:
        raise ValueError("profondita' PNG non gestita: %s" % bd)
    bpp = {2: 3, 6: 4, 3: 1, 0: 1, 4: 2}[ct]
    raw = zlib.decompress(idat)
    stride = w * bpp
    prev = bytearray(stride)
    for row in range(h):
        f = raw[row * (stride + 1)]
        cur = bytearray(raw[row * (stride + 1) + 1:(row + 1) * (stride + 1)])
        for i in range(stride):
            a = cur[i - bpp] if i >= bpp else 0
            b = prev[i]
            cc = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                cur[i] = (cur[i] + a) & 255
            elif f == 2:
                cur[i] = (cur[i] + b) & 255
            elif f == 3:
                cur[i] = (cur[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - cc), abs(a - cc), abs(a + b - 2 * cc)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else cc)
                cur[i] = (cur[i] + pr) & 255
        if row == y:
            if ct == 3:
                return [plte[cur[x]] for x in range(w)]
            if ct in (0, 4):
                return [[cur[x * bpp]] * 3 for x in range(w)]
            return [list(cur[x * bpp:x * bpp + 3]) for x in range(w)]
        prev = cur
    raise ValueError("riga fuori immagine")


def _scala_barra(nome, punti):
    cols = _barra_eum(nome)
    if not cols:
        return None
    n = len(cols)

    def val(x):
        for (x0, v0), (x1, v1) in zip(punti, punti[1:]):
            if x <= x1:
                return v0 + (v1 - v0) * (x - x0) / (x1 - x0)
        return punti[-1][1]
    entries, ultimo = [], None
    for i, c in enumerate(cols):
        lo, hi = val(i - 0.5 if i else 0), val(i + 0.5)
        if i == n - 1:
            hi = None
        if ultimo and ultimo[:3] == list(c[:3]):
            ultimo[4] = _arr(hi) if hi is not None else None
            continue
        ultimo = [c[0], c[1], c[2], _arr(lo), _arr(hi)]
        entries.append(ultimo)
    return {"entries": entries}


def _scala_frp():
    # GetLegendGraphic JSON di EUMETView, stile mtg_fd:frp_default: Categorize(FRP, colore per ogni MW intero 0..255)
    os.makedirs(CACHE_CM, exist_ok=True)
    p = os.path.join(CACHE_CM, "eum_frp.json")
    cols = None
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                cols = json.load(f)
        except Exception:
            cols = None
    if not cols:
        try:
            d = _scarica("https://view.eumetsat.int/geoserver/ows?service=WMS&version=1.3.0&request=GetLegendGraphic"
                         "&format=application/json&layer=mtg_fd:frp").decode("utf-8", "replace")
            m = re.search(r"Categorize\(FRP,([^\]]*)\)", d)
            parti = [x.strip("' ") for x in m.group(1).split(",")]
            cols = [[c, float(s) if s else None] for c, s in zip(parti[0::2], parti[1::2] + [""])]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cols, f)
        except Exception as e:
            log.warning("legenda FRP non leggibile: %s", e)
            return None
    entries, prec = [], 0.0
    for c, soglia in cols[1:]:  # il primo colore (#FFFFFF) vale per FRP < 0
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        if entries and entries[-1][:3] == [r, g, b]:
            entries[-1][4] = soglia
        else:
            entries.append([r, g, b, prec, soglia])
        prec = soglia if soglia is not None else prec
    return {"entries": entries}


def _scala_server(nome):
    sc = _SCALE_SERVER.get(nome)
    if not sc:
        return None
    entries = []
    for lo, hi, c in sc.get("voci", []):
        entries.append([int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), lo, hi])
    out = {"entries": entries, "classi": True}
    if sc.get("continua"):
        out["classi"] = False
    return out


def _scala_nativo_ir():
    # nativo.py: v = 1..255 -> T = 60 - (v - 1) * 170 / 254 (°C), grigio v,v,v
    passo = 170 / 254
    return {"entries": [[v, v, v, _arr(60 - (v - 1) * passo - passo / 2), _arr(60 - (v - 1) * passo + passo / 2)]
                        for v in range(1, 256)]}


def _scala_grigio(t_bianco, t_nero):
    """rampa lineare di grigi: 255 (bianco) = t_bianco, 0 (nero) = t_nero; gli estremi sono aperti."""
    d = abs(t_bianco - t_nero) / 255 / 2
    ent = []
    for v in range(256):
        t = t_nero + (t_bianco - t_nero) * v / 255
        ent.append([v, v, v, _arr(min(t - d, t + d)), _arr(max(t - d, t + d))])
    ent[0][4] = None      # nero: "da +37 °C in su"
    ent[-1][3] = None     # bianco: "fino a -53 °C"
    return {"entries": ent}


def _costruisci_scala(spec):
    if not spec:
        return None
    if "gibs" in spec:
        return _scala_gibs(spec["gibs"], spec.get("mult", 1.0), spec.get("add", 0.0))
    if "barra" in spec:
        return _scala_barra(spec["barra"], spec["punti"])
    if spec.get("frp"):
        return _scala_frp()
    if "server" in spec:
        return _scala_server(spec["server"])
    if spec.get("nativo_ir"):
        return _scala_nativo_ir()
    if "grigio" in spec:
        a, b = spec["grigio"]
        return _scala_grigio(a, b)
    return None


def _hex(r, g, b):
    return "#%02X%02X%02X" % (int(r), int(g), int(b))


def _colore_a(sc, v):
    """colore della scala per il valore v (la voce che lo contiene, o la piu' vicina)."""
    best, bd = None, None
    for e in sc["entries"]:
        if len(e) > 5 and e[5] != 0:
            continue  # solo il primo gruppo (pioggia) per i colori delle fasce
        lo, hi = e[3], e[4]
        if (lo is None or v >= lo) and (hi is None or v < hi or (lo == hi and v == lo)):
            return _hex(*e[:3])
        m = lo if hi is None else hi if lo is None else (lo + hi) / 2
        d = abs(m - v)
        if bd is None or d < bd:
            best, bd = e, d
    return _hex(*best[:3]) if best else None


def _colore_fascia(sc, da, a):
    """colore dello swatch di una fascia [da, a): la voce mediana fra quelle che cadono nella fascia."""
    dentro = []
    for e in sc["entries"]:
        if len(e) > 5 and e[5] != 0:
            continue
        lo, hi = e[3], e[4]
        m = lo if hi is None else hi if lo is None else (lo + hi) / 2
        if m is None:
            continue
        if (da is None or m >= da) and (a is None or m < a):
            dentro.append((m, e))
    if dentro:
        dentro.sort(key=lambda x: x[0])
        e = dentro[len(dentro) // 2][1]
        return _hex(*e[:3])
    v = a if da is None else da if a is None else (da + a) / 2
    return _colore_a(sc, v)


def _costruisci(chiave):
    mod = M[chiave]
    leg = {k: v for k, v in mod.items() if k not in ("scala", "voci", "certo_nota")}
    leg["modello"] = chiave
    leg["soglia"] = mod.get("soglia", SOGLIA.get(mod["tipo"], 30))
    sc = _costruisci_scala(mod.get("scala"))
    voci = [dict(v) for v in mod.get("voci", [])]
    if sc:
        for v in voci:
            if not v.get("c") and ("da" in v):
                v["c"] = _colore_fascia(sc, v.get("da"), v.get("a"))
        leg["scala"] = sc
    elif mod.get("scala"):
        leg["scalaMancante"] = True
    for v in voci:
        if not v.get("c"):
            v["c"] = "#808080"
    leg["voci"] = voci
    return leg


def legenda(lid):
    chiave = modello_di(lid)
    if not chiave or chiave not in M:
        return None
    with _lock:
        hit = _cache.get(chiave)
        if hit and time.time() - hit[0] < 6 * 3600 and not hit[1].get("scalaMancante"):
            leg = hit[1]
        else:
            leg = None
    if leg is None:
        leg = _costruisci(chiave)
        with _lock:
            _cache[chiave] = (time.time(), leg)
    return dict(leg, layer=lid)


def riepilogo(ids):
    out = []
    for lid in ids:
        k = modello_di(lid)
        leg = legenda(lid) if k else None
        out.append({"id": lid, "modello": k, "tipo": leg and leg["tipo"], "voci": len(leg["voci"]) if leg else 0,
                    "scala": bool(leg and leg.get("scala")),
                    "valori": len(leg["scala"]["entries"]) if leg and leg.get("scala") else 0,
                    "incerte": sum(1 for v in leg["voci"] if v.get("certo") is False) if leg else 0})
    return out
