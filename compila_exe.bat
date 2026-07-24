@echo off
cd /d C:\MixCreatorPro

echo.
echo Eliminazione vecchie compilazioni...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Verifica file FFmpeg...
if not exist ffmpeg\ffmpeg.exe (
    echo ERRORE: manca C:\MixCreatorPro\ffmpeg\ffmpeg.exe
    pause
    exit /b 1
)
if not exist ffmpeg\ffprobe.exe (
    echo ERRORE: manca C:\MixCreatorPro\ffmpeg\ffprobe.exe
    pause
    exit /b 1
)

echo.
echo Compilazione one-file...
py -3.12 -m PyInstaller --clean --noconfirm MixCreatorPRO.spec

echo.
if exist dist\MixCreatorPRO.exe (
    echo COMPILAZIONE COMPLETATA
    echo File creato: C:\MixCreatorPro\dist\MixCreatorPRO.exe
) else (
    echo ERRORE: EXE non creato
)

echo.
pause
