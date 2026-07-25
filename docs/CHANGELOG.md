# MixCreator PRO - CHANGELOG

## v4.3.0-winlive-stable

- FASE 4D.3 completata
- Nuovo parser WinLive
- Normalizzazione WL5SYNCT completa
- Allineamento timestamp tra righe
- Eliminazione righe solo temporali
- Safe Write transazionale
- Nuova classificazione Esito WinLive
- Report Problemi dettagliato
- Ottimizzazione prestazioni (9 minuti -> 6 secondi)

## v1.2.01 (In sviluppo)

-   Aggiunta firma "Created by Dino S." (prevista)
-   Pannello Anteprima (in sviluppo)

## v1.1 Stable

-   GUI completa
-   Worker con thread separato
-   Motore audio FFmpeg
-   Annullamento elaborazione
-   Barra di avanzamento
-   Log integrato
-   Salvataggio impostazioni

## v2.3C (Build)

-   Aggiunta waveform interattiva al Clip Editor con ricerca click-to-seek
-   Selezione IN/OUT evidenziata nella waveform
-   Caricamento della waveform asincrono senza bloccare l'interfaccia
-   Introdotta cache della waveform per tempi di caricamento più rapidi

## v2.3A (Build)

-   Migrazione del Clip Editor da pygame a python-vlc
-   Risolto bug: l'anteprima della clip (`Ascolta clip`) ora riparte sempre dal punto IN anche dopo terminazione automatica
