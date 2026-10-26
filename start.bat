@echo off
title K-Bridge
cd /d "%~dp0"
echo Pornire K-Bridge...
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo A aparut o eroare la lansarea aplicatiei K-Bridge.
    pause
)
