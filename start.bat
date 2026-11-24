@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found.
  echo Install Python 3.11 or newer from https://www.python.org/downloads/
  echo On the installer, check "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create the virtual environment.
    pause
    exit /b 1
  )
)

echo Installing Python packages. The first run can take a few minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :pipfail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :pipfail

if not exist ".env" (
  echo.
  echo First-time database setup.
  echo PostgreSQL must already be installed and running.
  echo If asked, type the password you set for the postgres user.
  echo.
  ".venv\Scripts\python.exe" setup_database.py
  if errorlevel 1 (
    echo.
    echo Database setup did not finish. Read the message above, then run start.bat again.
    pause
    exit /b 1
  )
)

echo.
echo Server: http://127.0.0.1:8000
echo.
echo Demo password for every account: Demo@123
echo   Student   student1@erp.test
echo   Teacher   teacher1@erp.test
echo   Admin     admin@erp.test
echo.
echo Leave this window open. Close it, or press Ctrl+C, to stop the server.
echo.
".venv\Scripts\python.exe" -m uvicorn --app-dir backend main:app --host 127.0.0.1 --port 8000
echo.
echo The server stopped.
pause
exit /b 0

:pipfail
echo.
echo Package install failed. Check that you are online, then run start.bat again.
pause
exit /b 1
