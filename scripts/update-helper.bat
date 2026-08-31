@echo off
rem Swap a running launcher for a downloaded one, then restart it.
rem
rem   update-helper.bat <pid-to-wait-for> <path-to-replace> <downloaded-file>
rem
rem Waits by PID, not by image name: prospect-og's update.bat matched on
rem IMAGENAME, which also forced the new exe to be named launcher.exe.
setlocal EnableDelayedExpansion

set "PID=%~1"
set "OLD=%~f2"
set "NEW=%~f3"
set /a TRIES=0

rem The wait loop below uses `ping`, NOT `timeout /t 1`: timeout needs a
rem console handle, and we are spawned with DETACHED_PROCESS, so it fails
rem instantly with "Input redirection is not supported" and the loop would
rem spin at full speed. ping always works. The rem lines are kept out of the
rem parenthesised block on purpose -- cmd parses comments for block structure.

:waitloop
tasklist /FI "PID eq %PID%" /NH 2>nul | find "%PID%" >nul
if not errorlevel 1 (
    set /a TRIES+=1
    if !TRIES! GEQ 120 exit /b 1
    ping -n 2 127.0.0.1 >nul
    goto waitloop
)

move /Y "%NEW%" "%OLD%" >nul
if errorlevel 1 exit /b 1

start "" "%OLD%"
exit /b 0
