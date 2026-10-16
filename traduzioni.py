# -*- coding: utf-8 -*-
"""
METEO SAT - traduzioni italiane in UN SOLO POSTO (usato da server.py).

Qui stanno tutte le stringhe che arrivano IN INGLESE dalle fonti esterne:
  - titoli originali dei layer (NASA GIBS, EUMETSAT EUMETView)
  - categorie e titoli degli eventi (EONET, GDACS), paesi, intensita'
I nomi e le descrizioni curate dei layer sono gia' scritti in italiano nel catalogo (server.py, CURATI);
gli id dei layer NON si traducono mai (i link salvati devono continuare a funzionare).
Nomi propri di satelliti e strumenti restano: GOES, Himawari, Meteosat, Metop, VIIRS, MODIS, Sentinel, Landsat, TROPOMI...

Per aggiungere una traduzione: aggiungere la voce al dizionario giusto qui sotto, niente altro.
"""
import re

# ---------------------------------------------------------------- categorie eventi (EONET)
CATEGORIE_EVENTI = {
    "severeStorms": "Tempeste violente", "wildfires": "Incendi", "volcanoes": "Vulcani",
    "dustHaze": "Polvere e foschia", "floods": "Alluvioni", "seaLakeIce": "Ghiaccio marino e lacustre",
    "earthquakes": "Terremoti", "landslides": "Frane", "snow": "Neve", "drought": "Siccità",
    "tempExtremes": "Temperature estreme", "waterColor": "Colore dell'acqua", "manmade": "Origine umana",
}
# stesse categorie col titolo inglese di EONET (per i casi senza id)
CATEGORIE_EVENTI_EN = {
    "Severe Storms": "Tempeste violente", "Wildfires": "Incendi", "Volcanoes": "Vulcani",
    "Dust and Haze": "Polvere e foschia", "Floods": "Alluvioni", "Sea and Lake Ice": "Ghiaccio marino e lacustre",
    "Earthquakes": "Terremoti", "Landslides": "Frane", "Snow": "Neve", "Drought": "Siccità",
    "Temperature Extremes": "Temperature estreme", "Water Color": "Colore dell'acqua", "Manmade": "Origine umana",
}

# ---------------------------------------------------------------- tipi di evento nei titoli (inizio del titolo)
# ordine: dal piu' lungo al piu' corto (il primo che combacia vince)
TIPI_EVENTO = [
    ("Post-Tropical Cyclone", "Ciclone post-tropicale"),
    ("Potential Tropical Cyclone", "Potenziale ciclone tropicale"),
    ("Severe Tropical Cyclone", "Ciclone tropicale severo"),
    ("Very Intense Tropical Cyclone", "Ciclone tropicale molto intenso"),
    ("Intense Tropical Cyclone", "Ciclone tropicale intenso"),
    ("Severe Tropical Storm", "Tempesta tropicale severa"),
    ("Extratropical Cyclone", "Ciclone extratropicale"),
    ("Subtropical Depression", "Depressione subtropicale"),
    ("Subtropical Storm", "Tempesta subtropicale"),
    ("Tropical Depression", "Depressione tropicale"),
    ("Tropical Cyclone", "Ciclone tropicale"),
    ("Tropical Storm", "Tempesta tropicale"),
    ("Super Typhoon", "Super tifone"),
    ("Major Hurricane", "Uragano maggiore"),
    ("Hurricane", "Uragano"),
    ("Typhoon", "Tifone"),
    ("Cyclone", "Ciclone"),
    ("Storm", "Tempesta"),
    ("Prescribed Fire", "Fuoco prescritto"),
    ("Wildfires", "Incendi"),
    ("Wildfire", "Incendio"),
    ("Fire", "Incendio"),
    ("Dust Storm", "Tempesta di polvere"),
    ("Dust and Haze", "Polvere e foschia"),
    ("Dust", "Polvere"),
    ("Smoke", "Fumo"),
    ("Haze", "Foschia"),
    ("Flooding", "Alluvione"),
    ("Floods", "Alluvioni"),
    ("Flood", "Alluvione"),
    ("Landslide", "Frana"),
    ("Drought", "Siccità"),
    ("Heatwave", "Ondata di calore"),
    ("Heat Wave", "Ondata di calore"),
    ("Blizzard", "Bufera di neve"),
    ("Snowstorm", "Tempesta di neve"),
    ("Earthquake", "Terremoto"),
    ("Eruption", "Eruzione"),
]
# "Etna Volcano, Italy" -> "Vulcano Etna, Italia"
_RE_VULCANO = re.compile(r"^(.+?)\s+Volcano\b", re.I)
_RE_TIPI = [(re.compile(r"^%s\b" % re.escape(en), re.I), it) for en, it in TIPI_EVENTO]

# ---------------------------------------------------------------- paesi (GDACS "country", coda dei titoli EONET)
PAESI = {
    "United States": "Stati Uniti", "United States of America": "Stati Uniti", "USA": "Stati Uniti",
    "Mexico": "Messico", "Canada": "Canada", "Cuba": "Cuba", "Bahamas": "Bahamas", "The Bahamas": "Bahamas",
    "Haiti": "Haiti", "Dominican Republic": "Repubblica Dominicana", "Jamaica": "Giamaica",
    "Puerto Rico": "Porto Rico", "Guatemala": "Guatemala", "Honduras": "Honduras", "Nicaragua": "Nicaragua",
    "El Salvador": "El Salvador", "Costa Rica": "Costa Rica", "Panama": "Panamá", "Belize": "Belize",
    "Bermuda": "Bermuda", "Cayman Islands": "Isole Cayman", "Turks and Caicos Islands": "Isole Turks e Caicos",
    "Virgin Islands": "Isole Vergini", "British Virgin Islands": "Isole Vergini britanniche",
    "Antigua and Barbuda": "Antigua e Barbuda", "Saint Lucia": "Santa Lucia", "Barbados": "Barbados",
    "Trinidad and Tobago": "Trinidad e Tobago", "Guadeloupe": "Guadalupa", "Martinique": "Martinica",
    "Saint Kitts and Nevis": "Saint Kitts e Nevis", "Grenada": "Grenada", "Dominica": "Dominica",
    "Venezuela": "Venezuela", "Colombia": "Colombia", "Ecuador": "Ecuador", "Peru": "Perù", "Chile": "Cile",
    "Argentina": "Argentina", "Brazil": "Brasile", "Bolivia": "Bolivia", "Paraguay": "Paraguay", "Uruguay": "Uruguay",
    "Japan": "Giappone", "China": "Cina", "Taiwan": "Taiwan", "Hong Kong": "Hong Kong", "Macao": "Macao",
    "Philippines": "Filippine", "Viet Nam": "Vietnam", "Vietnam": "Vietnam", "Laos": "Laos",
    "Lao People's Democratic Republic": "Laos", "Cambodia": "Cambogia", "Thailand": "Thailandia",
    "Myanmar": "Myanmar", "Bangladesh": "Bangladesh", "India": "India", "Sri Lanka": "Sri Lanka",
    "Pakistan": "Pakistan", "Oman": "Oman", "Yemen": "Yemen", "Somalia": "Somalia", "Saudi Arabia": "Arabia Saudita",
    "United Arab Emirates": "Emirati Arabi Uniti", "Iran": "Iran", "Malaysia": "Malesia", "Indonesia": "Indonesia",
    "Singapore": "Singapore", "Brunei": "Brunei", "Timor-Leste": "Timor Est", "East Timor": "Timor Est",
    "Korea": "Corea", "South Korea": "Corea del Sud", "Republic of Korea": "Corea del Sud", "Korea, Republic of": "Corea del Sud",
    "North Korea": "Corea del Nord", "Democratic People's Republic of Korea": "Corea del Nord",
    "Russia": "Russia", "Russian Federation": "Russia", "Mongolia": "Mongolia",
    "Australia": "Australia", "New Zealand": "Nuova Zelanda", "Papua New Guinea": "Papua Nuova Guinea",
    "Fiji": "Figi", "Vanuatu": "Vanuatu", "Tonga": "Tonga", "Samoa": "Samoa", "American Samoa": "Samoa americane",
    "New Caledonia": "Nuova Caledonia", "French Polynesia": "Polinesia francese", "Solomon Islands": "Isole Salomone",
    "Guam": "Guam", "Northern Mariana Islands": "Isole Marianne Settentrionali", "Marshall Islands": "Isole Marshall",
    "Micronesia": "Micronesia", "Federated States of Micronesia": "Micronesia", "Palau": "Palau", "Kiribati": "Kiribati",
    "Tuvalu": "Tuvalu", "Niue": "Niue", "Cook Islands": "Isole Cook", "Wallis and Futuna": "Wallis e Futuna",
    "Madagascar": "Madagascar", "Mozambique": "Mozambico", "Mauritius": "Mauritius", "Reunion": "Riunione",
    "Réunion": "Riunione", "Comoros": "Comore", "Mayotte": "Mayotte", "Seychelles": "Seychelles",
    "South Africa": "Sudafrica", "Tanzania": "Tanzania", "Kenya": "Kenya", "Malawi": "Malawi", "Zimbabwe": "Zimbabwe",
    "Cape Verde": "Capo Verde", "Cabo Verde": "Capo Verde", "Senegal": "Senegal", "Morocco": "Marocco",
    "Algeria": "Algeria", "Tunisia": "Tunisia", "Libya": "Libia", "Egypt": "Egitto", "Sudan": "Sudan",
    "Italy": "Italia", "Greece": "Grecia", "Spain": "Spagna", "France": "Francia", "Portugal": "Portogallo",
    "Turkey": "Turchia", "Türkiye": "Turchia", "Iceland": "Islanda", "Ireland": "Irlanda",
    "United Kingdom": "Regno Unito", "Germany": "Germania", "Norway": "Norvegia", "Greenland": "Groenlandia",
    "Antarctica": "Antartide", "Croatia": "Croazia", "Albania": "Albania", "Cyprus": "Cipro", "Malta": "Malta",
}
# Stati USA e province con nome italiano diverso (gli altri restano uguali)
LUOGHI = {"Hawaii": "Hawaii", "New Mexico": "Nuovo Messico", "North Carolina": "Carolina del Nord",
          "South Carolina": "Carolina del Sud", "North Dakota": "Dakota del Nord", "South Dakota": "Dakota del Sud",
          "Pennsylvania": "Pennsylvania", "Virginia": "Virginia", "West Virginia": "Virginia Occidentale",
          "Louisiana": "Louisiana", "Florida": "Florida", "California": "California", "British Columbia": "Columbia Britannica",
          "Northwest Territories": "Territori del Nord-Ovest", "Quebec": "Québec"}


def traduci_paese(s):
    """'Japan, Philippines' -> 'Giappone, Filippine'. Paesi sconosciuti restano com'erano (nomi propri)."""
    if not s:
        return s
    parti = [p.strip() for p in re.split(r"\s*[,;]\s*", s) if p.strip()]
    return ", ".join(PAESI.get(p, LUOGHI.get(p, p)) for p in parti)


def traduci_titolo_evento(t):
    """'Wildfire Hatch Grade, Walla Walla, Washington' -> 'Incendio Hatch Grade, Walla Walla, Washington'
    'Hurricane Simon' -> 'Uragano Simon'; 'Etna Volcano, Italy' -> 'Vulcano Etna, Italia'."""
    if not t:
        return t
    t = t.strip()
    pezzi = [p.strip() for p in t.split(",")]
    testa = pezzi[0]
    m = _RE_VULCANO.match(testa)
    if m:
        testa = "Vulcano " + m.group(1) + testa[m.end():]
    else:
        for rx, it in _RE_TIPI:
            m = rx.match(testa)
            if m:
                resto = testa[m.end():].strip()
                testa = (it + " " + resto).strip()
                if it in ("Incendio", "Fuoco prescritto"):  # "Wildfire Bunker Fire" -> "Incendio Bunker"
                    testa = re.sub(r"\s+Fire$", "", testa, flags=re.I)
                break
    # coda: luoghi/paesi (solo quelli con nome italiano diverso)
    coda = [PAESI.get(p, LUOGHI.get(p, p)) for p in pezzi[1:]]
    return ", ".join([testa] + coda)


def numero_it(v, dec=0):
    """1234.5 -> '1.234,5' (formato italiano)."""
    s = ("{:,.%df}" % dec).format(v)
    return s.replace(",", " ").replace(".", ",").replace(" ", ".")


_RE_ACRI = re.compile(r"^\s*([\d.]+)\s*acres?\s*$", re.I)
_RE_HA = re.compile(r"^\s*([\d.]+)\s*(?:ha|hectares?)\s*$", re.I)
_RE_NM2 = re.compile(r"^\s*([\d.]+)\s*NM\^?2\s*$", re.I)
_RE_KTS = re.compile(r"^\s*([\d.]+)\s*kts?\s*$", re.I)
_RE_GDACS = re.compile(r"^(.*?)\s*\(maximum wind speed of\s*([\d.]+)\s*km/h\)\s*$", re.I)


def _tipo(testo):
    for rx, it in _RE_TIPI:
        if rx.match(testo.strip()):
            return it
    return testo


def traduci_intensita(s):
    """Intensita' degli eventi in italiano, con unita' metriche accanto a quelle originali."""
    if not s:
        return s
    m = _RE_ACRI.match(s)
    if m:
        v = float(m.group(1))
        return "%s acri (circa %s ettari)" % (numero_it(v), numero_it(v * 0.404686))
    m = _RE_HA.match(s)
    if m:
        return "%s ettari" % numero_it(float(m.group(1)))
    m = _RE_NM2.match(s)
    if m:
        v = float(m.group(1))
        return "%s miglia nautiche quadrate (circa %s km²)" % (numero_it(v), numero_it(v * 3.4299))
    m = _RE_KTS.match(s)
    if m:
        v = float(m.group(1))
        return "%s nodi (circa %s km/h)" % (numero_it(v), numero_it(v * 1.852))
    m = _RE_GDACS.match(s)
    if m:
        return "%s (vento massimo %s km/h)" % (_tipo(m.group(1)), numero_it(float(m.group(2))))
    return s


def traduci_evento(ev):
    """Traduce in posto i campi testuali di un evento di /api/events."""
    if ev.get("titolo"):
        ev["titolo"] = traduci_titolo_evento(ev["titolo"])
    if ev.get("paese"):
        ev["paese"] = traduci_paese(ev["paese"])
    if ev.get("intensita"):
        ev["intensita"] = traduci_intensita(ev["intensita"])
    cn = ev.get("categoriaNome")
    if ev.get("categoria") in CATEGORIE_EVENTI:
        ev["categoriaNome"] = CATEGORIE_EVENTI[ev["categoria"]]
    elif cn in CATEGORIE_EVENTI_EN:
        ev["categoriaNome"] = CATEGORIE_EVENTI_EN[cn]
    return ev


# ---------------------------------------------------------------- titoli originali dei layer
# EUMETView: "Prodotto - Satellite - Posizione" -> "Prodotto Satellite (Posizione)"
PRODOTTI_EUM = {
    "Geo Colour RGB": "GeoColor RGB",
    "True Colour RGB": "Colori reali RGB",
    "European HRV RGB": "Composito HRV ad alta risoluzione Europa",
    "Airmass RGB": "Masse d'aria RGB",
    "Tropical Airmass RGB": "Masse d'aria tropicali RGB",
    "LI Accumulated Flash Area": "Area dei lampi accumulata (Lightning Imager)",
    "Rapidly Developing Thunderstorms": "Temporali in rapido sviluppo (RDT)",
    "Convection RGB": "Convezione RGB",
    "Cloud Type RGB": "Tipo di nubi RGB",
    "Cloud Phase RGB": "Fase delle nubi RGB",
    "Fog / Low Clouds RGB": "Nebbia / nubi basse RGB",
    "Cloud Top Height": "Altezza della cima delle nubi",
    "Dust RGB": "Polveri RGB",
    "Volcanic Ash RGB": "Cenere vulcanica RGB",
    "Fire Temperature RGB": "Temperatura incendi RGB",
    "Fire Radiative Power (FRP)": "Potenza radiativa dei fuochi (FRP)",
    "Snow RGB": "Neve RGB",
    "Blended FCI / LEO MW precipitation": "Precipitazione combinata FCI + microonde da satelliti polari",
    "Blended SEVIRI / LEO MW precipitation and morphologic information":
        "Precipitazione combinata SEVIRI + microonde da satelliti polari, con informazione morfologica",
    "Precipitation rate at ground by GEO/IR supported by LEO/MW":
        "Intensità di precipitazione al suolo da infrarosso geostazionario + microonde polari",
    "AVHRR RGB Natural Colour + Fog 6 orbits accumulated": "AVHRR colori naturali + nebbia RGB, 6 orbite accumulate",
    "AVHRR RGB Cloud Daily Accumulated": "AVHRR nubi RGB, accumulo giornaliero",
    "AVHRR IR10.8 6 orbits accumulated": "AVHRR infrarosso 10,8 µm, 6 orbite accumulate",
    "Geo Ring Dust RGB": "Polveri RGB, mosaico dell'anello geostazionario",
    "Geo Ring Airmass RGB": "Masse d'aria RGB, mosaico dell'anello geostazionario",
    "Geo Ring Ash RGB": "Cenere RGB, mosaico dell'anello geostazionario",
    "Geo Ring Natural Colour RGB": "Colori naturali RGB, mosaico dell'anello geostazionario",
    "Orbital Track": "Traccia orbitale",
}
SATELLITI_EUM = {
    "MSG": "Meteosat MSG", "MTG": "Meteosat MTG", "MTG-I": "Meteosat MTG", "Multimission": "multimissione",
    "Metop B": "Metop-B", "Metop C": "Metop-C", "Metop A": "Metop-A", "Metop": "Metop",
}
POSIZIONI_EUM = {"0 degree": "0°", "Indian Ocean": "Oceano Indiano"}
# canali: "FCI HRFI VIS0.6 μm Image", "High Rate SEVIRI IR10.8 μm Image"
_RE_CANALE = re.compile(r"^(?P<pre>.*?)\s*(?P<tipo>VIS|IR|WV|NIR)(?P<a>\d+)\.(?P<b>\d+)\s*[μµ]m\s*Image$")
NOMI_CANALE = {"VIS": "visibile", "IR": "infrarosso", "WV": "vapore acqueo", "NIR": "vicino infrarosso"}
_RE_TRACCIA = re.compile(r"^METOP ([ABC]) ORBITAL TRACK$", re.I)

# gruppo "Risoluzione piena": bande ricostruite dai file originali (nativo.py)
BANDE_NATIVO = {"truecolor": "colore vero", "vis": "visibile", "ir": "infrarosso", "geocolor": "GeoColor"}

# NASA GIBS: "Prodotto (termine, termine, satellite)"
PRODOTTI_GIBS = {
    "Geo Color": "GeoColor", "Red Visible": "Visibile rosso", "Corrected Reflectance": "Riflettanza corretta",
    "Clean Infrared": "Infrarosso pulito", "Cloud Top Temperature": "Temperatura della cima delle nubi",
    "Air Mass": "Masse d'aria", "Water Vapor Infrared": "Vapore acqueo infrarosso", "Dust Score": "Indice polveri",
    "Dust": "Polveri", "Fire Temperature": "Temperatura incendi",
    "Thermal Anomalies and Fires": "Anomalie termiche e incendi", "Reflectance": "Riflettanza",
    "Vegetation Disturbance Status": "Stato di disturbo della vegetazione",
    "Land Surface Reflectance": "Riflettanza della superficie terrestre",
    "Nitrogen Dioxide": "Biossido di azoto", "Sulfur Dioxide": "Biossido di zolfo",
    "Carbon Monoxide": "Monossido di carbonio", "Methane": "Metano",
    "Merged Aerosol Optical Depth": "Spessore ottico degli aerosol combinato",
    "Deep Blue Aerosol Optical Depth": "Spessore ottico degli aerosol Deep Blue",
    "Day/Night Band": "Banda giorno/notte", "At Sensor Radiance": "Radianza al sensore",
    "Black Marble": "Black Marble, luci notturne", "Sea Surface Temperature": "Temperatura superficiale del mare",
    "Sea Surface Temperature Anomalies": "Anomalie della temperatura superficiale del mare",
    "Precipitation Rate": "Intensità di precipitazione",
    "Precipitation Rate, 30-minute avg.": "Intensità di precipitazione, media su 30 minuti",
    "Snow Cover": "Copertura nevosa", "Sea Ice Concentration": "Concentrazione del ghiaccio marino",
    "Blue Marble Shaded Relief and Bathymetry": "Blue Marble con rilievo ombreggiato e batimetria",
    "Blue Marble": "Blue Marble, Terra senza nubi", "Land Water Map": "Mappa di terre e acque",
    "Coastlines": "Linee di costa", "Reference Features": "Confini e strade",
    "Reference Labels": "Nomi di luoghi", "Graticule": "Reticolo geografico",
}
TERMINI_GIBS = {
    "Day": "giorno", "Night": "notte", "All": "giorno e notte", "True Color": "colori reali",
    "Tropospheric Column": "colonna troposferica", "Vertical Column Troposphere": "colonna verticale troposferica",
    "Subdaily": "infragiornaliero", "Total Vertical Column": "colonna verticale totale",
    "Planetary Boundary Layer": "strato limite planetario", "Land and Ocean": "terra e mare",
    "Day/Night Band": "banda giorno/notte", "Nadir BRDF-Adjusted": "corretta BRDF al nadir",
    "Color Index": "indice di colore", "Normalized Difference Snow Index": "indice NDSI della neve",
    "Aqua+Terra": "Aqua + Terra", "Prata": "metodo Prata", "Deep Blue": "Deep Blue",
}
_RE_GIBS = re.compile(r"^(?P<prod>[^()]+?)\s*\((?P<termini>.*)\)\s*$")
_RE_BANDE = re.compile(r"^(Bands?)\s+(.+)$", re.I)
_RE_UM = re.compile(r"^(\d+)\.(\d+)\s*µm$")
_RE_METRI = re.compile(r"^(\d+)m$")


def _termine_gibs(t):
    t = t.strip()
    if t in TERMINI_GIBS:
        return TERMINI_GIBS[t]
    m = _RE_BANDE.match(t)
    if m:
        return ("bande " if m.group(1).lower() == "bands" else "banda ") + m.group(2)
    m = _RE_UM.match(t)
    if m:
        return "%s,%s µm" % (m.group(1), m.group(2))
    m = _RE_METRI.match(t)
    if m:
        return "%s m" % m.group(1)
    return t  # nomi propri (satellite, strumento, livello L2/L3...)


def traduci_titolo_layer(t):
    """Titolo originale (inglese) del layer -> italiano.
    'Dust RGB - MSG - 0 degree' -> 'Polveri RGB Meteosat MSG (0°)'."""
    if not t:
        return t
    t = re.sub(r"\s+", " ", t.strip())
    m = _RE_TRACCIA.match(t)
    if m:
        return "Traccia orbitale Metop-%s" % m.group(1).upper()
    if " - " in t:
        parti = [p.strip() for p in t.split(" - ")]
        prod = parti[0]
        mc = _RE_CANALE.match(prod)
        if mc:
            pre = mc.group("pre").replace("High Rate", "alta frequenza").strip()
            prod = "Immagine %s %s,%s µm%s" % (NOMI_CANALE[mc.group("tipo")], mc.group("a"), mc.group("b"),
                                                " (%s)" % pre if pre else "")
        else:
            prod = PRODOTTI_EUM.get(prod, prod)
        sat = SATELLITI_EUM.get(parti[1], parti[1]) if len(parti) > 1 else ""
        pos = POSIZIONI_EUM.get(parti[2], parti[2]) if len(parti) > 2 else ""
        sep = ", " if "," in prod else " "
        return (prod + (sep + sat if sat else "")).strip() + (" (%s)" % pos if pos else "")
    if t in PRODOTTI_EUM:
        return PRODOTTI_EUM[t]
    m = _RE_GIBS.match(t)
    if m:
        prod = m.group("prod").strip()
        termini = [x for x in (s.strip() for s in m.group("termini").split(",")) if x]
        return "%s (%s)" % (PRODOTTI_GIBS.get(prod, prod), ", ".join(_termine_gibs(x) for x in termini))
    return PRODOTTI_GIBS.get(t, t)
