@echo off
rem METEO SAT - avvia il server nascosto (pythonw) e apre http://127.0.0.1:8952/
rem Per l'icona sul desktop conviene puntare direttamente a: wscript.exe "avvia_nascosto.vbs"
start "" wscript.exe "%~dp0avvia_nascosto.vbs" %*
exit /b 0
