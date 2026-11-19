# Keithley 3706A Thermoelement-Monitor

GUI zum Auslesen von Thermoelementen an einem Keithley 3706A (System Switch/Multimeter) per USB/VISA.

## Start
- **`start.bat`** (Doppelklick) – startet das Programm, installiert fehlende Pakete beim ersten Start.
- **`build_exe.bat`** – erzeugt eine eigenständige Version in `dist\Keithley3706A-Monitor\`
  (kein Python nötig, nur VISA). Zum Weitergeben den ganzen Ordner kopieren.

Voraussetzungen: Python ≥ 3.11 und eine VISA-Implementierung (NI-VISA oder Keysight IO Libraries).

## Funktionen

**Anzeige**
- Kanaltabelle: Messwert, Einheit, Änderungsrate (K/min), Min/Max/Mittel, Status (offen/Alarm)
- Live-Plot mit Kanalauswahl, Zeitfenster, Hover-Werten, zwei Messcursorn (Δt, ΔT, Rate),
  Rechteck-Zoom; lange Messungen werden für die Anzeige ausgedünnt, gespeichert wird voll aufgelöst
- Kachelansicht mit großen Werten für Fernablesung (eigenes Fenster, Vollbild mit F11)
- Vergleichsdateien: ältere CSV-Aufnahmen gestrichelt einblenden (Originalzeit oder ausgerichtet)
- Hell / Dunkel / Hoher Kontrast / Auto (Windows-Einstellung); verschiebbare Panels, Anordnung wird gemerkt

**Messung**
- Thermoelement-Typ global und je Kanal (mehrere DMM-Konfigurationen am Gerät)
- Kalibrier-Offset je Kanal, Vergleichsstelle intern/simuliert/extern, NPLC, Intervall
- Pause, Einzelmessung, Statusanzeige (verbunden / Scan läuft / pausiert / veraltet / Fehler),
  Herzschlag mit Alter des letzten Scans und Scandauer, automatischer Wiederaufbau der Verbindung
- Simulationsmodus (Ressource `SIMULATION`) zum Testen ohne Gerät

**Alarme**
- Ober-/Untergrenze je Kanal mit Hysterese, optional Fühlerbruch als Alarm
- Rote Tabellenzeile und Kachel, Alarmbanner, Warnton, Windows-Benachrichtigung
- E-Mail über SMTP (STARTTLS/SSL), Sendepause je Kanal; Passwort per Windows-DPAPI verschlüsselt

**Aufnahme & Reproduzierbarkeit**
- CSV in wählbaren Ordner (wird gemerkt); jede Zeile sofort geschrieben; optional neue Datei je Tag
- Spalte „Ereignis“: Marker mit Kommentar (Taste M), Alarme, Pausen, Konfigurationsänderungen
- Metadaten im Dateikopf (Gerät, Karte, Typen, Offsets, Vergleichsstelle, Notiz, Rechner/Benutzer)
  und zusätzlich als JSON-Datei neben der CSV
- Aufnahme planen: Start sofort/Uhrzeit/Temperaturschwelle, Stopp manuell/Dauer/Uhrzeit/Schwelle
- Profile (JSON) speichern/laden; Messskript exportieren, das die Messung ohne GUI wiederholt
- Export: Verlauf als CSV, Plot als PNG/SVG/PDF, Ereignisprotokoll als TXT

## Tastenkürzel
| Taste | Aktion | Taste | Aktion |
|---|---|---|---|
| Leertaste | Pause / Weiter | Strg+R | Aufnahme starten/beenden |
| E | Einzelmessung | Strg+K | Verbinden/Trennen |
| M | Marker setzen | Strg+E | Einstellungen |
| C | Messcursor | Strg+Shift+K | Kanal-Einstellungen |
| Z | Rechteck-Zoom | Strg+S / Strg+O | Profil speichern / laden |
| L | Live folgen | Strg+P | Plot exportieren |
| T | Kachelansicht | F11 | Vollbild |
| Esc | Alarmbanner quittieren | F1 | Tastenkürzel anzeigen |

## Headless (ohne Oberfläche)
```
py -3.11 -m keithley3706a.headless Messprofil.json --dauer 3600 --ordner D:\Daten
```
Das Profil wird in der GUI unter *Datei → Profil speichern* erzeugt. *Datei → Messskript exportieren*
erzeugt ein eigenständiges Python-Skript mit eingebettetem Profil.

## Aktuelle Hardware
- Keithley 3706A, Slot 1: Modell 3724 (Dual 1x30 FET-Multiplexer)
- Thermoelemente an Kanälen 1001–1009 (Typ K angenommen, interne Vergleichsstelle)

Einstellungen werden in der Windows-Registry gespeichert (`HKCU\Software\JLU-Physik`).

## CSV-Format
Standard „Excel (DE)“: Semikolon, Dezimalkomma, UTF-8 mit BOM. Kopfzeilen mit `#` enthalten die
Metadaten. Leere Zellen = offenes Thermoelement. Letzte Spalte „Ereignis“.

## Messprinzip (TSP)
Pro Zyklus ein Scan über alle Kanäle (`scan.execute`), Ergebnis über ein einziges
`printbuffer(...)` (Kanal, Wert, Einheit). Je Thermoelement-Typ wird eine DMM-Konfiguration
`pyTCconfig_<Typ>` angelegt und den Kanälen zugewiesen. Es wird **kein** `reset()` ausgeführt.
Offsets werden in der Software addiert.

## Projektstruktur
```
main.py, start.bat, build_exe.bat
keithley3706a/
  instrument.py     TSP-Treiber + Simulator        acquisition.py  Messthread
  recorder.py       CSV-Aufnahme (Tageswechsel)    csv_loader.py   CSV einlesen (Vergleich)
  analysis.py       Statistik, Änderungsrate       alarms.py       Grenzwerte mit Hysterese
  notify.py         Ton, E-Mail                    secret.py       DPAPI-Verschlüsselung
  scheduler.py      geplante Aufnahme              profile.py      Profile, Skript-Export
  headless.py       Messung ohne GUI               channel_props.py Typ/Offset/Alarm je Kanal
  main_window.py    Hauptfenster                   tile_window.py  Kachelansicht
  settings*.py, dialogs.py, theme.py, icons.py, history.py
tests/              pytest
```
