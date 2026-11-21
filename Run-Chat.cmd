@echo off
setlocal
cd /d "%~dp0"

if not defined DRAYL_BRAIN (
  if exist "%~dp0school-pack.zip" (
    set "DRAYL_BRAIN=%~dp0school-pack.zip"
  ) else if exist "%~dp0school-pack\manifest.json" (
    set "DRAYL_BRAIN=%~dp0school-pack"
  )
)

if not defined DRAYL_BRAIN (
  echo No school-pack.zip found next to this file.
  echo Double-click Build-School-Pack.cmd first, or set DRAYL_BRAIN to your pack folder.
  echo.
  pause
  goto :eof
)

cd chatbox

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 -m drayl_chat --brain "%DRAYL_BRAIN%" %*
    goto :eof
  )
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python -m drayl_chat --brain "%DRAYL_BRAIN%" %*
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
