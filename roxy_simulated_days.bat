@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_simulated_days.ps1" %*
set "roxySimulationExit=%errorlevel%"
if not "%roxySimulationExit%"=="0" pause
exit /b %roxySimulationExit%
