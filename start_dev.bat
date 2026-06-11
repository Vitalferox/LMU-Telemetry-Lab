@echo off
title LMU Telemetry Lab - Dev
echo === Lancement LMU Telemetry Lab ===

start "LMU Backend (port 8000)" cmd /k "cd /d "%~dp0backend" && "%~dp0.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000"

start "LMU Frontend (port 5173)" cmd /k "cd /d "%~dp0frontend" && npm run dev"

timeout /t 5 /nobreak >nul
start http://localhost:5173
echo Backend: http://127.0.0.1:8000 - Frontend: http://localhost:5173
echo Fermer les deux fenetres CMD pour arreter l'application.
