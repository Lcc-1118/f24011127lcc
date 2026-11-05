@echo off
setlocal
chcp 65001 >nul
set "HERE=%~dp0"
cd /d "%HERE%"

rem ---- locate a real python.exe (skip the Microsoft Store stub) ----
set "PYEXE="
for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do set "PYEXE=%%I"
if not defined PYEXE for /f "delims=" %%I in ('where py 2^>nul') do set "PYEXE=%%I"
if not defined PYEXE if exist "D:\应用\DSH Desktop\resources\office-runtime\primary-runtime\dependencies\python\python.exe" set "PYEXE=D:\应用\DSH Desktop\resources\office-runtime\primary-runtime\dependencies\python\python.exe"

if not defined PYEXE goto nopython

echo Using: "%PYEXE%"
echo.
"%PYEXE%" run.py %*
if errorlevel 1 goto failed
goto end

:nopython
echo.
echo [ERROR] No usable Python found.
echo Install Python 3.11+ from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:failed
echo.
echo [FAILED] If it says "No module named fastapi", install dependencies:
echo   "%PYEXE%" -m pip install -r requirements.txt
echo.
pause
goto end

:end
endlocal
