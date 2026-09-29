@echo off
rem ---------------------------------------------------------------------
rem  SINRO launcher.  Open the app with one command.
rem
rem    sinro         pull the latest, check the environment, then serve
rem    sinro check   check the environment only
rem    sinro folder  open the project folder in Explorer
rem
rem  Japanese text is printed by the Python tools, not by this file,
rem  so that it does not depend on the console code page.
rem ---------------------------------------------------------------------
setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Cannot enter %REPO% & exit /b 1)

if /i "%~1"=="folder" (
  start "" "%CD%"
  popd & endlocal & exit /b 0
)

echo [SINRO] %CD%
echo [1/3] git pull
git pull --rebase origin main
if errorlevel 1 (
  echo.
  echo git pull failed. Fix it before you start editing.
  popd & endlocal & exit /b 1
)

echo [2/3] tools\doctor.py
python tools\doctor.py
if errorlevel 1 (
  echo.
  echo doctor reported a problem. Fix it before you start editing.
  popd & endlocal & exit /b 1
)

if /i "%~1"=="check" (
  popd & endlocal & exit /b 0
)

echo [3/3] tools\serve.py
python tools\serve.py
popd
endlocal
