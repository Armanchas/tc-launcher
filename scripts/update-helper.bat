@echo off
rem Swap a running launcher for a downloaded one, then restart it.
rem
rem   update-helper.bat <pid-to-wait-for> <path-to-replace> <downloaded-file>
rem
rem Waits by PID, not by image name: the old launcher matched on IMAGENAME,
rem which is alarmingly broad.
rem
rem The waits use `ping`, NOT `timeout /t 1`: timeout needs a console handle,
rem and we are spawned DETACHED_PROCESS, so it exits instantly with "Input
rem redirection is not supported" and the loop would spin. The rem lines stay
rem outside the parenthesised blocks on purpose, since cmd parses comments for
rem block structure.
rem
rem The move is RETRIED, not attempted once. A PyInstaller onefile build is two
rem processes: the launcher.exe bootloader and the child it re-executes. Our
rem pid is the child, so when it exits the parent is often still alive clearing
rem its temp tree, and Windows holds an open handle to a running process image
rem -- so the first move can fail purely on timing. Antivirus scanning a fresh
rem download does the same thing.
setlocal EnableDelayedExpansion

set "PID=%~1"
set "OLD=%~f2"
set "NEW=%~f3"
set "TCDIR=%USERPROFILE%\.tclauncher"
if not exist "%TCDIR%" mkdir "%TCDIR%" 2>nul
set "LOG=%TCDIR%\update-helper.log"
set /a TRIES=0
set /a MTRIES=0

echo [%DATE% %TIME%] start pid=%PID% old="%OLD%" new="%NEW%">>"%LOG%"

:waitloop
tasklist /FI "PID eq %PID%" /NH 2>nul | find "%PID%" >nul
if not errorlevel 1 (
    set /a TRIES+=1
    if !TRIES! GEQ 120 goto giveup_wait
    ping -n 2 127.0.0.1 >nul
    goto waitloop
)

:moveloop
move /Y "%NEW%" "%OLD%" >nul 2>&1
if not errorlevel 1 goto swapped
set /a MTRIES+=1
if !MTRIES! GEQ 30 goto giveup_move
ping -n 2 127.0.0.1 >nul
goto moveloop

:swapped
echo [%DATE% %TIME%] swapped after !MTRIES! retr(y/ies); relaunching>>"%LOG%"
start "" "%OLD%"
exit /b 0

:giveup_wait
echo [%DATE% %TIME%] GAVE UP: pid %PID% still running after 120s>>"%LOG%"
exit /b 1

:giveup_move
echo [%DATE% %TIME%] GAVE UP: could not replace "%OLD%" after 30 tries; "%NEW%" left in place>>"%LOG%"
exit /b 1
