@echo off
rem ---------------------------------------------------------------------
rem  Register the "sinro" command on this PC.  Run once per machine.
rem
rem    tools\install-command.cmd
rem
rem  It writes tiny forwarders into %LOCALAPPDATA%\Microsoft\WindowsApps,
rem  which is already on PATH, so no PATH editing and no admin rights.
rem  To undo, just delete the two .cmd files it reports.
rem ---------------------------------------------------------------------
setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "BIN=%LOCALAPPDATA%\Microsoft\WindowsApps"

if not exist "%REPO%\tools\sinro.cmd" (
  echo Cannot find "%REPO%\tools\sinro.cmd".
  exit /b 1
)
if not exist "%BIN%" mkdir "%BIN%"

rem Both spellings, so either one works.
for %%N in (sinro shinro) do (
  > "%BIN%\%%N.cmd" echo @echo off
  >>"%BIN%\%%N.cmd" echo call "%REPO%\tools\sinro.cmd" %%*
  echo installed: %BIN%\%%N.cmd
)

echo.
echo Done. Open a new terminal and type:  sinro
echo   sinro          pull, check, then open the app in a browser
echo   sinro check    check the environment only
echo   sinro folder   open the project folder
endlocal
