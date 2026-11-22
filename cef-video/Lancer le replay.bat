@echo off
chcp 65001 >nul
title Replay escrime
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py serveur_replay.py %*
) else (
    python serveur_replay.py %*
)

echo.
echo Le serveur s'est arrete.
pause
