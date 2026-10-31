@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || goto failed

rem Windows one-click launcher. Use an installed supported 64-bit Python.
set "PY_CMD=python"

:got_python
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if exist "%VENV_PY%" goto have_venv

echo [1/3] Creating virtual environment...
%PY_CMD% -m venv ".venv"
if errorlevel 1 goto failed

:have_venv
if not exist "%VENV_PY%" goto failed
echo [2/3] Preparing packages (first launch may download several GB)...
"%VENV_PY%" "%CD%\setup_teaparty.py"
if errorlevel 1 goto failed

echo [3/3] Launching TEA PARTY...
echo Close this window or press Ctrl+C to stop the server.
"%VENV_PY%" "%CD%\main.py" --browser
set "EXIT_CODE=%ERRORLEVEL%"
popd
if "%EXIT_CODE%"=="0" exit /b 0
echo.
echo TEA PARTY exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:no_python
echo.
echo Python 3.11-3.13 64-bit was not found.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Reopen teaparty.bat after installation.
goto failed

:failed
echo.
echo Setup or launch failed. Read README.md for troubleshooting.
pause
popd
exit /b 1
