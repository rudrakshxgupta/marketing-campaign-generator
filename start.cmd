@echo off
rem  Start BrandlyAI.
rem
rem  Two servers, each in its own window, so closing this one does not take
rem  them down and you can read either log when something goes wrong.
rem
rem  Both are development servers: they live only as long as their window.
rem  Nothing here installs a service or survives a reboot, which is why the
rem  interface goes dead if the windows are closed -- the page still loads
rem  from the browser cache, and every API call behind it fails.

setlocal
cd /d "%~dp0"

rem  The Azure CLI supplies the login DefaultAzureCredential uses. Without it
rem  on PATH the text model cannot authenticate and campaigns fail at the
rem  brief-compilation step, several seconds in, which reads as a bug rather
rem  than a missing sign-in.
set "PATH=%PATH%;C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   No virtual environment found.
  echo   Run:  python -m venv .venv
  echo         .venv\Scripts\python -m pip install -r backend\requirements.txt
  echo         .venv\Scripts\python -m playwright install chromium
  echo.
  pause
  exit /b 1
)

echo Starting the API on http://127.0.0.1:8000 ...
start "BrandlyAI API" cmd /k "cd /d "%~dp0backend" && "%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000"

echo Starting the interface on http://localhost:5173 ...
start "BrandlyAI UI" cmd /k "cd /d "%~dp0frontend" && npm run dev"

rem  Vite needs a moment to bind before a browser pointed at it gets anything
rem  other than a refused connection.
timeout /t 6 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo   Both servers are running in their own windows.
echo   Close those windows to stop them.
echo.
