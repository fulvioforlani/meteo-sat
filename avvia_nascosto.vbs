' METEO SAT - avvio senza finestre nere.
' Se il server (porta 8952) non risponde lo avvia con pythonw (nascosto), poi apre il browser.
' Argomento opzionale: /nobrowser  -> avvia solo il server.
Option Explicit
Dim sh, fso, cartella, py, url, i, apri

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
cartella = fso.GetParentFolderName(WScript.ScriptFullName)
url = "http://127.0.0.1:8952/"

apri = True
If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "/nobrowser" Then apri = False
End If

Function Attivo()
  Dim h
  On Error Resume Next
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1000, 1000, 2000, 2000
  h.Open "GET", url & "api/health", False
  h.Send
  Attivo = (Err.Number = 0)
  If Attivo Then Attivo = (h.Status = 200)
  Err.Clear
  On Error GoTo 0
End Function

If Not Attivo() Then
  py = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Python\bin\pythonw.exe"
  If Not fso.FileExists(py) Then py = "pythonw.exe"
  sh.CurrentDirectory = cartella
  ' 0 = finestra nascosta, False = non aspettare
  sh.Run """" & py & """ """ & cartella & "\server.py""", 0, False
  For i = 1 To 60
    WScript.Sleep 250
    If Attivo() Then Exit For
  Next
End If

If apri Then sh.Run url
