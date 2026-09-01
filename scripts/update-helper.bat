@echo off
rem Swap a running launcher for a downloaded one, then restart it.
rem
rem   update-helper.bat <pid-for-the-log> <path-to-replace> <downloaded-file>
rem
rem There is deliberately NO process-watching here. The previous version ran
rem `tasklist ... | find "%PID%"`, and `find` with no filename argument reads
rem STDIN -- which this helper does not have. It hung forever on a console read,
rem in a visible window titled "find <pid>", and the swap never happened.
rem
rem The move itself is the readiness test, and a better one: Windows holds an
rem open handle to a running process image, so `move` FAILS while the launcher
rem is alive and SUCCEEDS the moment it is not. That is the exact condition we
rem care about, with no process enumeration and nothing that can read stdin.
rem It also covers the onefile bootloader parent outliving its child, and an
rem antivirus scanner briefly holding the fresh download.
rem
rem The sleep uses `ping`, NOT `timeout /t 1`: timeout needs a console handle.
rem The rem lines stay outside the blocks, since cmd parses comments for block
rem structure.
setlocal EnableDelayedExpansion

set "PID=%~1"
set "OLD=%~f2"
set "NEW=%~f3"
set "TCDIR=%USERPROFILE%\.tclauncher"
if not exist "%TCDIR%" mkdir "%TCDIR%" 2>nul
set "LOG=%TCDIR%\update-helper.log"
set /a TRIES=0

echo [%DATE% %TIME%] start pid=%PID% old="%OLD%" new="%NEW%">>"%LOG%"

if not exist "%NEW%" (
    echo [%DATE% %TIME%] GAVE UP: "%NEW%" is missing>>"%LOG%"
    exit /b 1
)

:moveloop
move /Y "%NEW%" "%OLD%" >nul 2>&1
if not errorlevel 1 goto swapped
set /a TRIES+=1
if !TRIES! GEQ 60 goto giveup
ping -n 2 127.0.0.1 >nul 2>&1 <nul
goto moveloop

:swapped
echo [%DATE% %TIME%] swapped after !TRIES! retries; relaunching>>"%LOG%"
start "" "%OLD%"
exit /b 0

:giveup
echo [%DATE% %TIME%] GAVE UP: "%OLD%" still locked after !TRIES! tries; "%NEW%" left in place>>"%LOG%"
exit /b 1
