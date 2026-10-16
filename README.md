# Meteo Sat

Webapp per vedere i satelliti meteo direttamente da internet (nessuna antenna).
Dati: NASA GIBS (GOES, Himawari, VIIRS/MODIS, incendi, NO2, SO2, CO, aerosol), EUMETSAT WMS, EONET e GDACS (uragani, incendi).
Funzioni: animazioni, palette e fusione dei livelli, confronto a tendina, cicloni, legende dei colori, export PNG/GIF/MP4. Tutto in italiano.

## Avvio
Serve Python 3 con `requests`, `numpy`, `Pillow` (dove richiesti dai moduli).

    python server.py

poi aprire http://127.0.0.1:8952/ . Su Windows: `Avvia_Meteo.bat` (avvio senza finestre).

Dettagli tecnici in `SPEC.md`. Le credenziali EUMETSAT (opzionali) vanno in `eumetsat.key`, non incluso nel repository.
