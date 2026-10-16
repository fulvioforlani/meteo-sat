# -*- coding: utf-8 -*-
"""
legende_rgb.py - legende a CATEGORIE delle composizioni RGB (tipo B), in italiano.

Fonti (verificate l'11-10-26):
  EUMeTrain / EUMETSAT RGB Quick Guides  https://eumetrain.org/sites/default/files/2025-06/
      airmass_QG.pdf, dust_QG.pdf, Ash_QG.pdf, night_micro_QG.pdf, severe_storm_QG.pdf, snow_QG.pdf,
      natural_colour_QG.pdf, HRVclouds_QG.pdf ; 2021-05/CloudTypeRGB.pdf ; 2023-01/CloudPhaseRGB.pdf ;
      2021-05/FireTemperatureRGB.pdf  (colori campionati dai riquadri della legenda delle guide)
  CIRA GeoColor Quick Guide 2025         https://rammb2.cira.colostate.edu/wp-content/uploads/2020/01/QuickGuide_CIRA_GeoColor_2025update.pdf
  EUMETSAT MTG GeoColour                 https://user.eumetsat.int/catalogue/EO:EUM:DAT:0913
  EUMeTrain AVHRR Cloud RGB              https://resources.eumetrain.org/data/4/461/print_5.htm
  NASA Worldview (descrizioni layer)     https://worldview.earthdata.nasa.gov/config/metadata/layers/...
  EUMETView Capabilities (abstract dei layer) https://view.eumetsat.int/geoserver/ows

"alt" = altri colori tipici della stessa voce, rilevati sui tasselli reali EUMETView/GIBS (11-10-26):
servono alla lettura del colore sotto il cursore. certo=False = significato o colore non dichiarato da una
fonte ufficiale (l'interfaccia lo mostra come "indicativo").
"""


def V(c, nome, d, certo=True, alt=None, nomatch=False):
    v = {"c": c.upper(), "nome": nome, "def": d}
    if nomatch:
        v["nomatch"] = True  # voce solo descrittiva: non usata per riconoscere il colore sotto il cursore
    if not certo:
        v["certo"] = False
    if alt:
        v["alt"] = [a.upper() for a in alt]
    return v


QG = "https://eumetrain.org/sites/default/files/2025-06/"

# ------------------------------------------------------------------ GeoColor
GEOCOLOR = dict(
    tipo="B", titolo="GeoColor (giorno colori naturali, notte infrarosso)",
    fonte="CIRA GeoColor Quick Guide (2025)", url="https://rammb2.cira.colostate.edu/wp-content/uploads/2020/01/QuickGuide_CIRA_GeoColor_2025update.pdf",
    voci=[
        V("#FFFFFF", "Nubi spesse (giorno)", "Nubi alte e spesse, come i temporali: bianco brillante.", alt=["#F0F0F0", "#E2E6EA"]),
        V("#C8CDD3", "Nubi sottili (giorno)", "Nubi più sottili o velature: grigio chiaro, la superficie si intravede.",
          certo=False, alt=["#B4BAC2", "#D6DADE"]),
        V("#9AA6B4", "Fumo (giorno)", "Fumo di incendi: grigio-bluastro, spesso in pennacchi.", alt=["#8E98A4"]),
        V("#C8A77A", "Polvere sollevata (giorno)", "Polvere o sabbia sollevata dal vento: marrone chiaro."),
        V("#3E6B35", "Vegetazione (giorno)", "Terreno coperto di piante: varie tonalità di verde.", alt=["#4A6E3A", "#355A2C", "#5A7444"]),
        V("#A07D55", "Suolo scoperto (giorno)", "Suolo secco, roccia o terreno arido: marrone.", alt=["#B49268", "#8C6E4C", "#C8A880"]),
        V("#16304E", "Mare (giorno)", "Mare sereno di giorno: blu scuro.", certo=False, alt=["#0E2440", "#1C3A5C"]),
        V("#7CC3EA", "Nubi basse e nebbia (notte)", "Nebbia e nubi basse di goccioline: azzurro chiaro.", alt=["#6AAED8", "#8ED0F0"]),
        V("#C9C9C9", "Nubi medie e alte (notte)", "Nubi viste dall'infrarosso: dal grigio (medie, cirri) al bianco (alte e fredde).",
          alt=["#B0B4BA", "#E6E6E6", "#9A9EA6"]),
        V("#F0B93A", "Luci delle città (notte)", "Luci urbane da un archivio FISSO VIIRS (non mostra blackout o incendi reali).",
          alt=["#FFD27A", "#C98A3A", "#E0A040"]),
        V("#2B2140", "Terra senza nubi (notte)", "Terreno sereno di notte: viola scuro.", alt=["#231A36", "#352A4C"]),
        V("#1F3F75", "Mare senza nubi (notte)", "Mare sereno di notte: blu, più chiaro dove l'acqua è più fredda.",
          alt=["#173464", "#284C86", "#0E1E40"]),
    ],
    nota="Di notte le nubi vengono dall'infrarosso e sono semitrasparenti: nubi basse spesse possono sembrare "
         "trasparenti. All'alba e al tramonto le due parti si fondono. Le luci delle città sono uno sfondo fisso.")
GEOCOLOUR_MTG = dict(
    tipo="B", titolo="GeoColour Meteosat MTG (giorno colori reali, notte infrarosso)",
    fonte="EUMETSAT, prodotto MTG FCI GeoColour (catalogo EO:EUM:DAT:0913)", url="https://user.eumetsat.int/catalogue/EO:EUM:DAT:0913",
    voci=[
        V("#FFFFFF", "Nubi spesse (giorno)", "Di giorno le nubi spesse appaiono bianche, come in una foto.", certo=False,
          alt=["#EEF0F2", "#E0E4E8"]),
        V("#C3CBD3", "Nubi sottili (giorno)", "Velature e nubi sottili: grigio-azzurro, la superficie si intravede.", certo=False,
          alt=["#B0BAC4"]),
        V("#4F7A3A", "Vegetazione (giorno)", "Terreno con piante: verde (colore corretto con l'indice di vegetazione).",
          certo=False, alt=["#5A7E44", "#45682F"]),
        V("#B08A5E", "Suolo scoperto (giorno)", "Terreno senza vegetazione: roccia, suolo secco, sabbia.", certo=False, alt=["#C49C6C", "#9C7A52", "#D8B88C"]),
        V("#1B3A66", "Mare (giorno)", "Mare sereno: blu scuro.", certo=False, alt=["#12305A", "#0C2246"]),
        V("#DCDCDC", "Nubi di ghiaccio (notte)", "Nubi alte e fredde: biancastre.", alt=["#5A5A5E", "#6A6A6E", "#787878", "#C8C8CC", "#F0F0F0", "#B4B6BC"]),
        V("#7FB6E0", "Nubi d'acqua basse (notte)", "Nebbia e nubi basse di goccioline: bluastre.", alt=["#6C9CC8", "#8EC4E8", "#5A88B8"]),
        V("#F0B93A", "Luci delle città (notte)", "Luci urbane da un'immagine NASA fissa (Black Marble), non in tempo reale.",
          certo=False, alt=["#FFD27A", "#C98A3A"]),
        V("#0D1020", "Terra e mare senza nubi (notte)", "Sfondo notturno scuro (Black Marble).", certo=False,
          alt=["#060A16", "#141A2C", "#0A1428"]),
    ],
    nota="Di notte le nubi d'acqua sono bluastre e quelle di ghiaccio biancastre, semitrasparenti sopra uno sfondo "
         "fisso: le nubi basse spesse possono sembrare trasparenti. EUMETSAT non pubblica una tabella colori: "
         "la parte diurna è descritta per analogia con i colori reali.")
GEOCOLOR_NATIVO = dict(
    tipo="B", titolo="GeoColor dai file originali (giorno colori reali, notte infrarosso)",
    fonte="Ricetta del server (nativo.py): di giorno colore vero, di notte nubi dall'IR 10,3 µm (205-285 K) su fondo blu scuro",
    voci=[
        V("#FFFFFF", "Nubi spesse (giorno)", "Nubi spesse: bianco brillante.", alt=["#F0F2F4", "#E2E6EA"]),
        V("#C3CBD3", "Nubi sottili (giorno)", "Velature e nubi sottili.", alt=["#B0BAC4"]),
        V("#3E5A2C", "Vegetazione (giorno)", "Foreste e campi: verde.", alt=["#4C6A34", "#7E8656"]),
        V("#A68D66", "Suolo scoperto (giorno)", "Terreno secco o roccioso: marrone-ocra.", alt=["#D8BE92", "#8C7454"]),
        V("#0D1B2E", "Mare (giorno)", "Mare sereno: blu molto scuro.", alt=["#0A1424", "#142640"]),
        V("#EBEFFF", "Nubi alte e fredde (notte)", "Cime sotto i -60 °C circa: bianco-azzurro.", alt=["#D2D8F0", "#F4F6FF"]),
        V("#8890B0", "Nubi medie (notte)", "Cime tra circa -40 e -10 °C: grigio-azzurro.", alt=["#6A7496", "#A4ACC8"]),
        V("#2E3A60", "Nubi basse o calde (notte)", "Nubi basse, poco più fredde del suolo: si distinguono appena dal fondo.",
          alt=["#1E2A4C", "#3C4870"]),
        V("#08102A", "Sereno (notte)", "Cielo sereno di notte: fondo blu scuro fisso (niente luci delle città).",
          alt=["#0A1430", "#060C22"]),
    ],
    nota="Di notte la luminosità delle nubi dipende solo dalla temperatura (più bianco = più freddo e alto): "
         "nebbie e strati bassi quasi non si vedono. Niente luci delle città.")

# ------------------------------------------------------------------ E-View (HRV + IR)
EVIEW = dict(
    tipo="B", titolo="E-View: HRV ad alta risoluzione + infrarosso",
    fonte="EUMETView (abstract msg_fes:rgb_eview) + EUMeTrain HRV Cloud RGB Quick Guide (stessa ricetta)", url=QG + "HRVclouds_QG.pdf",
    voci=[
        V("#C7CAD8", "Nubi alte spesse", "Nubi alte e spesse (es. cumulonembi): bianco-grigio brillante, con ombre.",
          alt=["#E6E8F0", "#B4B8C8", "#FFFFFF"]),
        V("#6870B4", "Nubi alte sottili", "Velature alte sottili (cirri): bluastre.", alt=["#5860A8", "#8088C0"]),
        V("#DDE67F", "Nebbia, nubi basse/medie, neve", "Nebbia, nubi basse o medie e terreno innevato: gialle.",
          alt=["#C8D070", "#EEF09A", "#B4BC60"]),
        V("#353939", "Terra senza neve", "Terreno sereno: grigio con toni bluastri o giallastri.", alt=["#888868", "#787858", "#686858", "#989868", "#4A4C40", "#2A2E34"]),
        V("#2B2F86", "Mare", "Mare senza ghiaccio: blu scuro.", alt=["#20246E", "#343A96"]),
    ],
    nota="Solo di giorno (usa il canale visibile HRV). Nebbia, nubi basse e neve hanno colori simili. "
         "I colori vengono dalla guida dell'HRV Cloud RGB, ricetta uguale: tonalità indicative.")

# ------------------------------------------------------------------ Masse d'aria
AIRMASS = dict(
    tipo="B", titolo="Masse d'aria RGB (Airmass)",
    fonte="EUMETSAT/EUMeTrain Airmass RGB Quick Guide", url=QG + "airmass_QG.pdf",
    voci=[
        V("#3E2F8F", "Aria fredda polare", "Massa d'aria fredda e ricca di ozono, di origine polare: blu-viola.",
          alt=["#4A3C9C", "#342880", "#5848A8", "#2A2470"]),
        V("#257A3D", "Aria calda e umida", "Aria calda, povera di ozono, con molta umidità in quota: verde.",
          alt=["#2E8A48", "#1E6A34", "#3C8C4C"]),
        V("#3A4018", "Aria calda poco umida in quota", "Aria calda con poca umidità negli strati alti: verde oliva scuro.",
          alt=["#4A5020", "#30361A"]),
        V("#861518", "Aria secca discendente (corrente a getto)", "Aria secca che scende dalla stratosfera: indica correnti a "
          "getto, anomalie di vorticità e possibile ciclogenesi. Rosso scuro.", alt=["#9C2020", "#701414", "#A83A2A", "#7A2A3A"]),
        V("#FFFFFF", "Nubi alte e spesse", "Nubi alte e spesse a più strati: bianco.", alt=["#F0EEE8", "#E4E0D8"]),
        V("#6F6E2A", "Nubi medie", "Nubi di media quota, di ghiaccio o acqua: ocra-oliva.", alt=["#8A8434", "#9C9A50", "#5E5C24"]),
        V("#413716", "Nubi basse", "Le nubi basse non hanno un colore proprio: si vede solo la forma (bluastre in aria "
          "polare, verdastre in aria tropicale).", alt=["#4C4020"], nomatch=True),
    ],
    nota="Giorno e notte. Verso il bordo del disco l'aria tropicale diventa blu e non è più interpretabile; anche "
         "l'aria molto calda e secca dei deserti appare rossastra senza discesa di aria stratosferica.")
AIRMASS_TROPICALE = dict(
    tipo="B", titolo="Masse d'aria tropicali RGB",
    fonte="EUMETView (abstract msg_fes:rgb_tropicalairmass); colori per analogia con l'Airmass RGB", url=QG + "airmass_QG.pdf",
    voci=[
        V("#F2F2F2", "Nubi alte molto fredde", "Cumulonembi e nubi alte molto fredde: qui hanno sfumature sui top invece "
          "del bianco pieno.", certo=False, alt=["#FFFFFF", "#E0E0D8"]),
        V("#FFF3A0", "Torri che sfondano la tropopausa", "Torri temporalesche più fredde in cima alle nubi alte (scopo del "
          "prodotto); colore esatto non documentato.", certo=False, alt=["#FFE880", "#F8F0C0"]),
        V("#2E8040", "Aria calda e umida", "Aria calda e umida in quota (per analogia con l'Airmass).", certo=False,
          alt=["#257A3D", "#3C8C4C"]),
        V("#3E2F8F", "Aria più fredda", "Aria fredda e ricca di ozono (per analogia con l'Airmass).", certo=False,
          alt=["#480848", "#380838", "#580848", "#380848", "#280838", "#4A3C9C", "#342880"]),
        V("#861518", "Aria secca in quota", "Aria secca discendente (per analogia con l'Airmass).", certo=False,
          alt=["#380808", "#480808", "#280808", "#580838", "#380828", "#481808", "#9C2020", "#701414"]),
        V("#6F6E2A", "Nubi medie", "Nubi di media quota (per analogia con l'Airmass).", certo=False, alt=["#8A8434"]),
    ],
    nota="Variante dell'Airmass per i tropici: il canale verde ha una scala diversa per mostrare meglio le cime più "
         "fredde dei temporali. EUMETSAT non pubblica una tabella colori: tutte le voci sono indicative.")

# ------------------------------------------------------------------ Polveri / cenere
POLVERI = dict(
    tipo="B", titolo="Polveri RGB (Dust)",
    fonte="EUMETSAT/EUMeTrain Dust RGB Quick Guide", url=QG + "dust_QG.pdf",
    voci=[
        V("#EF4D8F", "Polvere (o cenere)", "Nube di polvere in sospensione: da rosa a magenta-viola. La cenere vulcanica tende "
          "più al rosso.", alt=["#E040A0", "#D84898", "#F060B0", "#C83C8C", "#E878B8"]),
        V("#231F20", "Cirri sottili", "Velature di ghiaccio sottili senza nubi sotto: nero o blu scuro.", alt=["#101018", "#1A1A30", "#28203C"]),
        V("#821618", "Nubi di ghiaccio spesse", "Nubi alte, spesse e fredde (es. temporali, fronti): rosso scuro.",
          alt=["#480808", "#783808", "#983808", "#6C1010", "#962020", "#5A0C14", "#A0303A"]),
        V("#A59F30", "Nubi medie", "Nubi di media quota: ocra-marrone se spesse, verde oliva se sottili.",
          alt=["#784808", "#985808", "#B87808", "#C88808", "#708F63", "#6B8D41", "#886808", "#B8A840", "#8C8428", "#C0B060"]),
        V("#0EA94B", "Cirri sottili su suolo caldo", "Velature sottili sopra i deserti: verde.", alt=["#20A050", "#3CA060"]),
        V("#ADDFE9", "Suolo caldo, aria secca", "Suolo caldo con aria secca: azzurro-ciano di giorno (il colore cambia molto con la temperatura del suolo).",
          alt=["#08B8F8", "#08C8F8", "#08D8F8", "#08E8F8", "#C0E8F0", "#98D0E0", "#B0C8E8"]),
        V("#3272B9", "Superficie serena (blu)", "Mare o terra senza nubi: blu acceso, più intenso dove l'aria vicino al suolo è umida.",
          alt=["#0878F8", "#0868F8", "#0858F8", "#0848F8", "#2868F8", "#4878F8", "#6878F8", "#1A76E1", "#0888F8", "#3C6EC0", "#2858A0", "#4878C8", "#4060B0"]),
        V("#B99BC9", "Aria secca nei bassi strati", "Superficie serena con aria secca: blu-rosato o lilla (tipico dei deserti di notte).",
          alt=["#81A6FF", "#C8C6FC", "#A8A0F0", "#A890C8", "#C8A8D8", "#9C88C0"]),
    ],
    nota="Giorno e notte (solo infrarosso). Non dice altezza né concentrazione della polvere; quella sottile o "
         "bassa sul mare si vede male e sotto le nubi non si vede. I colori del sereno cambiano molto tra giorno e notte.")
CENERE = dict(
    tipo="B", titolo="Cenere vulcanica RGB (Ash)",
    fonte="EUMETSAT/EUMeTrain Ash RGB Quick Guide", url=QG + "Ash_QG.pdf",
    voci=[
        V("#CF2026", "Cenere vulcanica", "Nube di cenere: rossa, più intensa con più cenere.", alt=["#E03030", "#B81C24"]),
        V("#F0EA43", "Cenere mista a SO2", "Cenere e gas insieme: tonalità di giallo.", alt=["#E8D840"]),
        V("#79CAB2", "Pennacchio di SO2", "Anidride solforosa vulcanica: verde brillante, più intenso con più gas.", alt=["#60C8A0"]),
        V("#8D5E20", "Ghiaccio spesso o cenere densa", "Nubi di ghiaccio spesse oppure cenere molto densa: marrone.",
          alt=["#806901", "#5A4701", "#2E3D01", "#887808", "#7A5020", "#A07030"]),
        V("#102E52", "Nubi di ghiaccio sottili", "Velature di ghiaccio sottili: blu scuro.", alt=["#0C2040", "#1C3C64"]),
        V("#8A9A7A", "Nubi d'acqua", "Nubi di goccioline: grigio-beige.", alt=["#A7CD3F", "#99E265", "#84DC75", "#9CA888", "#788868"]),
        V("#6DC2ED", "Terra o mare senza nubi", "Superficie serena: blu sul mare, rosa o ciano brillante sui deserti caldi di giorno.",
          alt=["#4DFEFF", "#62FFFF", "#58F8F8", "#79ACCE", "#68A8D8", "#80B8E8", "#A0B0E0", "#C0A8D8", "#5AA8D8"]),
    ],
    nota="Da usare in caso di eruzione, giorno e notte. La cenere spessa si confonde con le nubi di ghiaccio spesse; "
         "verso il bordo del disco le nubi d'acqua diventano verdi come l'SO2.")

# ------------------------------------------------------------------ Nebbia (Night Microphysics)
_NEBBIA_VOCI = [
    V("#8EC6D6", "Nebbia o nubi basse", "Nebbia o strato basso spesso a goccioline piccole: acquamarina / verde-azzurro "
      "chiaro (verdastro se più fredda).", alt=["#A1B356", "#8ECED6", "#9ED4AE", "#A0D8C8", "#7CC0C8", "#B0E0D0"]),
    V("#6367AF", "Sereno, mare o aria umida", "Mare (o terra) senza nubi: blu acceso, più intenso dove l'aria bassa è umida.", alt=["#310CEE", "#0805D1", "#0808F8", "#2808F8", "#0808C8", "#0808A8", "#874CFE", "#6544F0", "#2701E2", "#5A5CA8", "#6E70B8", "#5058A0"]),
    V("#8D70B2", "Sereno, aria secca o suolo caldo", "Terra senza nubi: rosa-viola; lilla chiaro sui deserti caldi, rosso-cremisi sugli altipiani freddi.", alt=["#5A43D5", "#B57CE7", "#7848F8", "#9858F8", "#811CE6", "#AD1E4A", "#983452", "#9C78B8", "#A080C0", "#8060A8"]),
    V("#9E7376", "Nubi medie spesse", "Nubi spesse di media quota: beige-marrone.", alt=["#A88080", "#8C6468"]),
    V("#7F131B", "Nubi di ghiaccio spesse", "Nubi alte e spesse: rosso-bruno (rosso-arancio se più fredde, con puntini "
      "gialli o verdi di rumore sulle cime freddissime).", alt=["#A22521", "#C04020", "#E07030", "#901C1C", "#6C1018"]),
    V("#29398A", "Cirri sottili", "Velature di ghiaccio sottili: blu scuro.", alt=["#2D014D", "#0D0121", "#080888", "#202C78", "#323C94", "#1C2464"]),
    V("#60489D", "Cirri molto sottili", "Velature sottilissime: viola-magenta, secondo il suolo sotto.", alt=["#824492", "#6C50A8", "#583C90"]),
    V("#C51783", "Zona illuminata dal sole (non valido)", "Con la luce del sole le nubi diventano magenta: qui il "
      "prodotto non è affidabile (usarlo solo di notte).", alt=["#D02090", "#B81478", "#E040A0"]),
]
NEBBIA = dict(
    tipo="B", titolo="Nebbia / nubi basse RGB (Night Microphysics)",
    fonte="EUMETSAT/EUMeTrain Night Microphysics RGB Quick Guide", url=QG + "night_micro_QG.pdf",
    voci=_NEBBIA_VOCI,
    nota="Solo di NOTTE: con il sole tutte le nubi diventano magenta e la nebbia può sparire. Non distingue la nebbia "
         "dallo strato basso; i cirri sopra la nebbia la nascondono.")

# ------------------------------------------------------------------ Convezione / nubi
CONVEZIONE = dict(
    tipo="B", titolo="Convezione RGB (temporali intensi)",
    fonte="EUMETSAT/EUMeTrain Severe Storms (Convection) RGB Quick Guide", url=QG + "severe_storm_QG.pdf",
    voci=[
        V("#F6F298", "Cime con cristalli piccoli", "Nube di ghiaccio spessa con cristalli piccoli o cima molto fredda: giallo. "
          "Spesso indica correnti ascendenti forti (temporale intenso).", alt=["#FFF070", "#EEE080", "#FFFFB0"]),
        V("#E93D4D", "Cime con cristalli grandi", "Nube di ghiaccio spessa con cristalli grandi o cima non freddissima: rosso.",
          alt=["#D03040", "#F05060", "#C02838"]),
        V("#9C52A0", "Ghiaccio sottile, cristalli grandi", "Velatura di ghiaccio sottile: rosa-viola.", alt=["#8C4890", "#A860A8"]),
        V("#D98792", "Ghiaccio sottile, cristalli piccoli", "Velatura di ghiaccio sottile: malva.", alt=["#C87888", "#E898A0"]),
        V("#CDCFCA", "Nubi d'acqua sopraffusa", "Nubi d'acqua sotto zero: grigiastre (verde-giallo se molto fredde).",
          alt=["#B8C0B0", "#D8DCC8"]),
        V("#63449B", "Niente nubi medie o alte", "Zone senza nubi medie o alte: blu o magenta.", alt=["#6818B8", "#4818B8", "#5818B8", "#4838F8", "#5848F8", "#6838E8", "#3818B8", "#7818B8", "#3838F8", "#4828E8", "#4A3890", "#7848A0", "#3C3080"]),
    ],
    nota="Solo di giorno. Il giallo indica cristalli piccoli o cime molto fredde ma NON sempre un temporale forte "
         "(anche nubi da incendi, polvere, nubi orografiche). Superficie e nubi basse si vedono appena.")
TIPO_NUBI = dict(
    tipo="B", titolo="Tipo di nubi RGB (Cloud Type)",
    fonte="EUMeTrain Cloud Type RGB Quick Guide", url="https://eumetrain.org/sites/default/files/2021-05/CloudTypeRGB.pdf",
    voci=[
        V("#C50C00", "Cirri sottili", "Velature di ghiaccio sottili: rosso (più scuro sul mare).", alt=["#A00800", "#D82010"]),
        V("#FFFF3D", "Nubi di ghiaccio spesse", "Nubi spesse a più strati con ghiaccio in cima: giallo.", alt=["#F0F040", "#FFF060"]),
        V("#FFFFFF", "Nubi d'acqua sopraffusa", "Nubi d'acqua sotto zero gradi: bianco.", alt=["#F0F0F0"]),
        V("#8EFCEB", "Nubi d'acqua basse/medie", "Nubi di goccioline basse o medie: azzurro-turchese.", alt=["#80E8E0", "#A0FFF0"]),
        V("#46AC70", "Nubi a fase mista", "Nubi basse o medie con acqua e ghiaccio insieme: verde.", alt=["#3C9C60", "#58B880"]),
        V("#CC3E89", "Nubi d'acqua alte e sottili", "Nubi d'acqua alte e sottili: rosa-magenta (più arancio se c'è ghiaccio).",
          alt=["#B83080", "#D85090"]),
        V("#5FFF12", "Neve e ghiaccio al suolo", "Neve o ghiaccio a terra o in mare: verde brillante.", alt=["#50E010"]),
        V("#453699", "Terra", "Terreno senza nubi: blu-viola.", alt=["#2868C8", "#2878D8", "#2888E8", "#2858A8", "#3C2C88", "#5040A8"]),
        V("#100820", "Mare / notte", "Mare senza nubi (molto scuro); di notte il prodotto è vuoto.", certo=False, alt=["#000000", "#1A1030"]),
    ],
    nota="Solo di giorno. Le classi alte/basse sono grossolane; la neve somiglia alle nubi basse o medie di ghiaccio.")
FASE_NUBI = dict(
    tipo="B", titolo="Fase delle nubi RGB (Cloud Phase)",
    fonte="EUMeTrain Cloud Phase RGB Quick Guide", url="https://eumetrain.org/sites/default/files/2023-01/CloudPhaseRGB.pdf",
    voci=[
        V("#486AE1", "Ghiaccio spesso, cristalli grandi", "Nubi di ghiaccio spesse con cristalli grandi: blu.", alt=["#3C5CD0", "#5878E8"]),
        V("#74E0F9", "Ghiaccio spesso, cristalli piccoli", "Nubi di ghiaccio spesse con cristalli piccoli: azzurro-ciano.",
          alt=["#60D0F0", "#88F0FF"]),
        V("#799171", "Ghiaccio sottile", "Velature di ghiaccio sottili: verde-grigio.", alt=["#688060", "#88A080"]),
        V("#FFE9BD", "Acqua spessa, gocce piccole", "Nubi d'acqua spesse a goccioline piccole: crema-bianco.", alt=["#FFF0D0", "#F0D8A8"]),
        V("#FFACCD", "Acqua spessa, gocce più grandi", "Nubi d'acqua spesse a gocce più grandi: rosa.", alt=["#F098B8", "#FFC0D8"]),
        V("#8D46A2", "Gocce enormi o fase mista", "Nubi d'acqua con gocce molto grandi o nubi miste spesse: viola.", alt=["#7C3C90"]),
        V("#786346", "Nubi d'acqua sottili sul mare", "Nubi d'acqua sottili sopra il mare: marrone-oliva.", alt=["#685838"]),
        V("#572A20", "Terra con vegetazione", "Terreno senza neve con piante: marrone scuro.", alt=["#4A2418", "#683828"]),
        V("#FFE328", "Terreno arido", "Terreno privo di vegetazione: giallo.", alt=["#F0D030", "#E8C838"]),
        V("#31319F", "Neve o ghiaccio marino", "Neve al suolo o ghiaccio sul mare: blu scuro.", alt=["#282890"]),
        V("#000000", "Mare", "Mare senza ghiaccio: nero.", alt=["#080808", "#101018"]),
    ],
    nota="Solo di giorno. Non dà la temperatura (abbinarlo all'infrarosso); neve e nubi di ghiaccio a cristalli grandi "
         "hanno colori simili (la neve di solito più scura).")
NEVE_RGB = dict(
    tipo="B", titolo="Neve RGB (Snow / Day Snow-Fog)",
    fonte="EUMETSAT/EUMeTrain Snow RGB Quick Guide", url=QG + "snow_QG.pdf",
    voci=[
        V("#F2644B", "Neve al suolo", "Terreno innevato: rosso-arancio, più brillante sui grandi campi di neve in montagna.",
          alt=["#E05040", "#FF7858", "#D04838"]),
        V("#C8E9F0", "Nebbia / nubi d'acqua (gocce piccole)", "Nebbia o nubi basse spesse a goccioline piccole: quasi bianche, "
          "a volte verdine o azzurrine.", alt=["#E0F4F8", "#B8E0E8", "#FFFFFF"]),
        V("#DFEFDC", "Nubi d'acqua a gocce grandi", "Nubi d'acqua spesse a gocce grandi o nubi miste: verde-giallo chiaro.",
          alt=["#D0E8C8"]),
        V("#C6A057", "Ghiaccio, cristalli piccoli", "Nubi di ghiaccio spesse con cristalli piccoli: arancio chiaro.", alt=["#D8B068"]),
        V("#CD7D39", "Ghiaccio, cristalli grandi", "Nubi di ghiaccio spesse con cristalli grandi: arancio più scuro.", alt=["#B86C30"]),
        V("#77765F", "Ghiaccio sottile sul mare", "Velatura di ghiaccio sottile sopra il mare: grigio-oliva.", alt=["#686850"]),
        V("#8EAD70", "Terra senza neve", "Terreno sereno senza neve: verde-grigio.", alt=["#7C9C60", "#A0B880", "#6C8C58"]),
        V("#701E2E", "Ghiaccio marino senza neve", "Ghiaccio sul mare non coperto di neve: rosso-bruno scuro."),
        V("#2A1B50", "Mari e laghi", "Acqua libera: blu-viola molto scuro.", alt=["#201440", "#342060", "#100820"]),
    ],
    nota="Solo di giorno; non applicabile alle alte latitudini d'inverno né sui deserti sabbiosi.")

# ------------------------------------------------------------------ Metop AVHRR
METOP_COLORI_NEBBIA = dict(
    tipo="B", titolo="Colori naturali (giorno) + nebbia (notte) Metop AVHRR",
    fonte="EUMETView (abstract eps:m01_rgb_natural_fog) + EUMeTrain Natural Colour e Night Microphysics Quick Guide",
    url=QG + "natural_colour_QG.pdf",
    voci=[
        V("#EBECEC", "Nubi d'acqua (giorno)", "Nebbia o nubi basse di goccioline: bianco-grigio.", alt=["#485858", "#586868", "#687878", "#788888", "#889898", "#FFFFFF", "#D8DADC"]),
        V("#C0CDDF", "Nubi miste o velo di cirri (giorno)", "Nubi con acqua e ghiaccio, o con un velo di cirri: grigio-azzurro chiaro.",
          alt=["#B0C0D4", "#D0DAE8"]),
        V("#6DC8BF", "Nubi di ghiaccio / neve (giorno)", "Nubi alte e spesse a cristalli grandi, oppure neve e ghiaccio al suolo: "
          "ciano-turchese (si distinguono dalla forma).", alt=["#65C2CF", "#50B8C0", "#80D8E0"]),
        V("#738650", "Vegetazione (giorno)", "Terreno coperto da vegetazione verde: verde.", alt=["#608040", "#80905C"]),
        V("#B19172", "Suolo nudo (giorno)", "Sabbia, roccia, suolo nudo o vegetazione secca: marrone chiaro.",
          alt=["#736D67", "#C0A080", "#A08060"]),
        V("#126C7B", "Ghiaccio marino senza neve (giorno)", "Ghiaccio sul mare: blu-verde scuro."),
        V("#071E2F", "Mari e laghi (giorno)", "Acqua libera: blu quasi nero.", alt=["#000000", "#0A1420"]),
    ] + [dict(v, nome=v["nome"] + " (notte)") for v in _NEBBIA_VOCI if not v["nome"].startswith("Zona illuminata")],
    nota="Mosaico delle ultime 6 orbite: la parte di giorno è a colori naturali (EUMETSAT Natural Colour), quella di "
         "notte è l'RGB nebbia. Le strisce hanno orari diversi; neve e nubi di ghiaccio hanno colori simili.")
AVHRR_124 = dict(
    tipo="B", titolo="Nubi RGB AVHRR (canali 1-2-4)",
    fonte="EUMeTrain, modulo AVHRR Cloud RGB (cap. V)", url="https://resources.eumetrain.org/data/4/461/print_5.htm",
    voci=[
        V("#FFFFFF", "Nubi alte spesse e fredde", "Nubi alte, spesse e fredde, come il cuore dei temporali: bianco.", alt=["#F0F0F8"]),
        V("#8FA6E0", "Cirri sottili", "Nubi alte sottili, come le incudini semitrasparenti: bluastre.", alt=["#8B779B", "#7A737F", "#786888", "#A0B4E8", "#7890D0"]),
        V("#E6DC78", "Nubi medie/basse spesse o neve", "Nubi spesse di media e bassa quota, e terreno innevato: giallastre "
          "(neve e nubi basse si somigliano).", alt=["#E8D870", "#F0E8A0", "#D8CC70"]),
        V("#3B6FB0", "Mare e laghi", "Acque libere: blu medio (più chiaro col riflesso del sole).", alt=["#281848", "#381848", "#482848", "#3F234F", "#544673", "#280838", "#2C5C9C", "#4880C0", "#1C3C70"]),
        V("#4CC24A", "Vegetazione calda", "Zone con vegetazione e calde: verde brillante.", alt=["#40B040", "#58C858"]),
        V("#4F9F8A", "Vegetazione fredda (montagna)", "Vegetazione in zone fredde: verde-bluastro.", alt=["#48907C"]),
        V("#8C8C8C", "Suolo nudo", "Terreno senza piante: grigiastro (toni blu se freddo).", alt=["#7C8494", "#A0A0A0"]),
        V("#B8C06A", "Terreno arido", "Terreno privo di vegetazione: verde-giallastro.", alt=["#C8C878", "#A8B060"]),
    ],
    nota="Soprattutto diurno (due canali solari): la parte notturna resta vuota. D'inverno neve e nebbia si confondono.")

# ------------------------------------------------------------------ Incendi
TEMP_FUOCO = dict(
    tipo="B", titolo="Temperatura incendi RGB (Fire Temperature)",
    fonte="EUMeTrain / CIRA Fire Temperature RGB Quick Guide", url="https://eumetrain.org/sites/default/files/2021-05/FireTemperatureRGB.pdf",
    voci=[
        V("#FF2828", "Fuoco poco caldo / punto caldo", "Incendio piccolo o poco caldo, visibile solo a 3,8 µm: rosso.",
          alt=["#E02020", "#FF4030"]),
        V("#FFC000", "Fuoco molto caldo", "Incendio più intenso: arancione.", alt=["#FFA000", "#F0B000"]),
        V("#FFFF00", "Fuoco caldo", "Incendio caldo, visto anche a 2,2 µm: giallo.", alt=["#F0F040"]),
        V("#FFFFFF", "Fuoco estremamente intenso", "Incendio molto intenso, visto da tutti e tre i canali: bianco."),
        V("#781313", "Aree bruciate (giorno)", "Cicatrici di incendio: rosso scuro-marrone.", alt=["#601010"]),
        V("#A03971", "Superficie terrestre (giorno)", "Terreno di giorno: dal viola-magenta al blu-viola, secondo tipo e "
          "temperatura (le zone aride molto calde possono sembrare rossastre).", alt=["#8C3068", "#B04880", "#6C3878", "#C04060"]),
        V("#000000", "Acqua, neve, ghiaccio, notte", "Acqua, neve, ghiaccio e sfondo notturno: nero.", alt=["#080810", "#100810"]),
        V("#397AD2", "Nubi d'acqua (giorno)", "Nubi di goccioline di giorno: blu.", alt=["#3068C0", "#4888E0"]),
        V("#006363", "Nubi di ghiaccio (giorno)", "Nubi di ghiaccio di giorno: verde-azzurro scuro.", alt=["#005050", "#107878"]),
    ],
    nota="Giorno e notte. Gli incendi si vedono solo senza nubi e il fumo di solito no. Di giorno zone aride molto "
         "calde possono apparire rossastre (falsi allarmi).")
_FALSI_COLORI = [
    V("#33CC33", "Vegetazione", "Anche poca vegetazione appare verde brillante.", alt=["#28A828", "#48D848", "#209020", "#60B040"]),
    V("#C9A19A", "Suolo nudo", "Terreno nudo o roccioso: tinta rosata.", alt=["#D8B0A8", "#B89088", "#E0C0B0"]),
    V("#B0201A", "Aree bruciate", "Zone colpite da incendio: rosso scuro o brillante.", alt=["#902018", "#C83020", "#803020"]),
    V("#FF8C1E", "Fuoco attivo", "Fronti di fiamma attivi: arancio-rosso vivo (il fumo diventa quasi trasparente).",
      certo=False, alt=["#FF6A00", "#FFA040"]),
    V("#0A0A1E", "Acqua", "Acqua libera, anche di alluvione: molto scura.", alt=["#000010", "#101028", "#000000"]),
    V("#1D3C8A", "Acqua con sedimenti", "Acqua torbida con sedimenti: blu scuro.", alt=["#2848A0"]),
    V("#40E0D0", "Neve e ghiaccio", "Neve e ghiaccio: azzurro vivo o turchese brillante.", alt=["#30C8D8", "#60F0E8"]),
    V("#FFFFFF", "Nubi d'acqua", "Nubi basse e calde di goccioline: bianche.", alt=["#F0F0F0", "#E0E4E8"]),
    V("#7FD6D6", "Nubi di ghiaccio alte", "Nubi alte e fredde di cristalli: turchesi o bluastre.", alt=["#90E0E8", "#70C0D0"]),
]
FUOCO_FUMO = dict(
    tipo="B", titolo="Fuoco e fumo (falsi colori SWIR-NIR-rosso)",
    fonte="NASA Worldview, descrizioni Corrected Reflectance Bands 7-2-1 (MODIS) e M11-I2-I1 (VIIRS)",
    url="https://worldview.earthdata.nasa.gov/config/metadata/layers/modis/terra/MODIS_Terra_CorrectedReflectance_Bands721.html",
    voci=_FALSI_COLORI,
    nota="Solo di giorno. Rosso = infrarosso a onda corta, verde = vicino infrarosso, blu = rosso visibile. "
         "Il rosso delle aree bruciate varia con la vegetazione e con quanto è completo l'incendio.")
VIIRS_I1I2I1 = dict(
    tipo="B", titolo="Banda I VIIRS (falsi colori rosso + vicino infrarosso)",
    fonte="NASA Worldview, descrizione Land Surface Reflectance (Bands I1-I2-I1)",
    url="https://worldview.earthdata.nasa.gov/config/metadata/layers/viirs/snpp/VIIRS_SNPP_SurfaceReflectance_BandsI1-I2-I1.html",
    voci=[
        V("#33CC33", "Vegetazione", "Anche poca vegetazione appare verde brillante.", alt=["#78A878", "#88B888", "#689868", "#68A868", "#28A828", "#48D848", "#60B040"]),
        V("#0A0A14", "Acqua", "Acqua libera sul terreno: molto scura.", alt=["#000000", "#101020"]),
        V("#D98CB3", "Acqua con sedimenti", "Acqua torbida con sedimenti: rosa."),
        V("#B38CB3", "Suolo nudo, aree bruciate, città", "Terreno senza piante: tonalità rosa-viola (più scure dove è bruciato "
          "o costruito).", certo=False, alt=["#A07CA0", "#806080", "#C8A0C0"]),
        V("#FFFFFF", "Nubi e neve", "Probabilmente bianche o chiare; la fonte non lo descrive.", certo=False, alt=["#E8E8E8"]),
    ],
    nota="Riflettanza della superficie (corretta per l'atmosfera), giornaliera, solo di giorno.")

MODELLI_RGB = {
    "geocolor": GEOCOLOR, "geocolour_mtg": GEOCOLOUR_MTG, "geocolor_nativo": GEOCOLOR_NATIVO, "eview": EVIEW,
    "airmass": AIRMASS, "airmass_tropicale": AIRMASS_TROPICALE, "polveri": POLVERI, "cenere": CENERE,
    "nebbia": NEBBIA, "convezione": CONVEZIONE, "tipo_nubi": TIPO_NUBI, "fase_nubi": FASE_NUBI, "neve_rgb": NEVE_RGB,
    "metop_colori_nebbia": METOP_COLORI_NEBBIA, "avhrr_124": AVHRR_124, "temp_fuoco": TEMP_FUOCO,
    "fuoco_fumo": FUOCO_FUMO, "viirs_i1i2i1": VIIRS_I1I2I1,
}
