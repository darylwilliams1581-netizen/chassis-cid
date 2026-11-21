@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 seed\scripts\school_pack.py textbooks --trusted --queries 20 --output school-pack.zip
    if %ERRORLEVEL%==0 (
      echo.
      echo Done. school-pack.zip is ready. Double-click Run-Chat.cmd to boot the chat box from it.
    )
    goto :eof
  )
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python seed\scripts\school_pack.py textbooks --trusted --queries 20 --output school-pack.zip
    if %ERRORLEVEL%==0 (
      echo.
      echo Done. school-pack.zip is ready. Double-click Run-Chat.cmd to boot the chat box from it.
    )
    goto :eof
  )
)

echo.
echo Building a school pack needs Python 3.10 or newer, and it is not installed
echo (or not on the PATH) on this computer.
echo.
echo Download it from https://www.python.org/downloads/
echo Tick "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
