# Keithley 3706A Thermoelement-Monitor

GUI zum Auslesen von Thermoelementen an einem Keithley 3706A (System Switch/Multimeter) per USB/VISA.

## Funktionen
- Tabelle aller konfigurierten Kanäle mit Messwert, Einheit und Status (offenes Thermoelement wird erkannt)
- Live-Plot aller Temperaturen; Kanäle per Häkchen ein-/ausblendbar; Zeitfenster wählbar; Maus-Hover zeigt Werte
- Kanäle benennbar (Doppelklick auf „Bezeichnung“), Namen werden in Plot und CSV übernommen
- CSV-Aufnahme in einen wählbaren Ordner (wird gemerkt); jede Zeile wird sofort geschrieben
- Export des gesamten Verlaufs im Speicher als CSV
- Helles / dunkles Design oder automatisch nach Windows-Einstellung
- Automatischer Wiederaufbau der Verbindung bei USB-Unterbrechung
- Simulationsmodus (Ressource `SIMULATION`) zum Testen ohne Gerät

## Start
Doppelklick auf **`start.bat`**. Fehlende Python-Pakete werden beim ersten Start automatisch installiert.

Voraussetzungen: Python ≥ 3.11 und eine VISA-Implementierung (NI-VISA oder Keysight IO Libraries)
mit USB-Treiber für das 3706A.

## Aktuelle Hardware
- Keithley 3706A, Slot 1: Modell 3724 (Dual 1x30 FET-Multiplexer)
- Thermoelemente an Kanälen 1001–1009 (Typ K angenommen, interne Vergleichsstelle)

Alle Messparameter (Kanäle, Thermoelement-Typ, Vergleichsstelle, Einheit, Integrationszeit, Intervall)
sind unter **Einstellungen** änderbar und werden gespeichert (Windows-Registry, `HKCU\Software\JLU-Physik`).

## CSV-Format
Standard ist „Excel (DE)“: Semikolon als Trenner, Dezimalkomma, UTF-8 mit BOM. Kopfzeilen mit `#`
enthalten Gerät, Karte, Thermoelement-Typ, Vergleichsstelle usw. Leere Zellen = offenes Thermoelement.

## Messprinzip (TSP)
Pro Messzyklus wird ein Scan über alle Kanäle ausgeführt (`scan.execute`) und das Ergebnis mit
einem einzigen `printbuffer(...)` abgeholt (Kanal, Wert, Einheit). Die Konfiguration wird
als DMM-Konfiguration `pyTCconfig` angelegt und den Kanälen zugewiesen. Es wird **kein** `reset()`
ausgeführt, andere gespeicherte Gerätekonfigurationen bleiben erhalten.

## Projektstruktur
```
main.py                      Startpunkt
start.bat                    Starter für Windows
keithley3706a/instrument.py  TSP-Treiber + Simulator
keithley3706a/acquisition.py Messthread
keithley3706a/recorder.py    CSV-Aufnahme
keithley3706a/history.py     Verlaufsspeicher für den Plot
keithley3706a/main_window.py Hauptfenster
keithley3706a/theme.py       Hell-/Dunkel-Design
tests/                       Unit-Tests (pytest)
```
