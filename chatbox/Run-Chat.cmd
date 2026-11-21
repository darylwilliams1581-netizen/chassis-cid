@echo off
setlocal
cd /d "%~dp0"
REM Optional: set DRAYL_BRAIN=C:\path\to\school-pack-folder
REM or run: Run-Chat.cmd --brain C:\path\to\school-pack-folder

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 -m drayl_chat %*
    goto :eof
  )
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python -m drayl_chat %*
    goto :eof
  )
)

echo.
echo D-RAYL Chat Box needs Python 3.10 or newer, and it is not installed
echo (or not on the PATH) on this computer.
echo.
echo Download it from https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
