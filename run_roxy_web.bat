@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python virtual environment was not found: .venv
    echo Create the environment and install server\requirements.txt first.
    exit /b 1
)

"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Local Web dependencies are not installed.
    echo Run: .venv\Scripts\python.exe -m pip install -r server\requirements.txt
    exit /b 1
)

echo RoxyPlan Local Web V0.1
echo Local: http://127.0.0.1:8000
echo LAN: use this computer's LAN IPv4 address followed by :8000
echo Example format: http://YOUR_LAN_IPV4:8000
echo.

"%PYTHON_EXE%" -m uvicorn server.main:app --host 0.0.0.0 --port 8000
