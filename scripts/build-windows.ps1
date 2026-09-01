# Build the Windows launcher: PyInstaller onefile -> dist\launcher.exe
#
# --noupx is deliberate. PyInstaller uses UPX automatically if it finds it on
# PATH, and UPX-packed binaries are one of the strongest antivirus heuristics
# going. Passing it explicitly makes the output the same whatever the build
# machine has installed. It does not fix Defender false positives on its own --
# see docs/windows-release-checklist.md -- but it removes one clear trigger.
#
# onefile, and named launcher.exe, on purpose:
#   - one file is trivially swappable by update-helper.bat, symmetric with the
#     AppImage, so one helper design covers both platforms;
#   - the name keeps prospect-og's auto-updater usable as a migration lever
#     (its update.bat relaunches by the old image name), and updater.pick_asset()
#     matches the release asset by exact equality with "launcher.exe" -- any
#     other name breaks Windows self-update silently.
#
# Mirrors scripts/build-appimage.sh; read that first when changing anything here.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$root = (Get-Location).Path

# NOT `pip install -e`: setuptools installs this project through a PEP 660
# import hook (__editable___..._finder.py), which PyInstaller's analysis does
# not follow, so the frozen exe can end up without the tclauncher package.
# build-appimage.sh installs non-editable for the same reason.
python -m venv .venv-win
if ($LASTEXITCODE -ne 0) { throw "Could not create .venv-win." }
.\.venv-win\Scripts\pip.exe install -q ".[dev]" pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }

$version = .\.venv-win\Scripts\python.exe -c "from tclauncher.version import APP_VERSION; print(APP_VERSION)"
Write-Host "Building TCLauncher $version for Windows"

.\.venv-win\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; refusing to build." }

# PyInstaller takes a SCRIPT PATH -- there is no `-m module` option. And
# tclauncher/__main__.py cannot be passed directly either: it uses
# package-relative imports, which break when PyInstaller runs a file as the
# top-level __main__ ("attempted relative import with no known parent
# package"). build-appimage.sh already generates this exact wrapper; mirror it.
# Written as ASCII so no BOM reaches the tokenizer or PyInstaller's analysis.
New-Item -ItemType Directory -Force -Path build | Out-Null
@'
from tclauncher.__main__ import main

if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding ascii build\entrypoint.py

# Stale output would make the Test-Path check below pass after a failed build.
New-Item -ItemType Directory -Force -Path dist | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue dist\launcher.exe

# --add-data / --icon sources are absolute: the spec is written to build\ via
# --specpath, and build-appimage.sh likewise passes "$(pwd)/..." rather than a
# path relative to the invocation directory. Data destinations stay relative --
# they are positions inside the bundle.
#   update-helper.bat -> bundle root, where platforms.helper_script() looks
#                        (os.path.join(sys._MEIPASS, "update-helper.bat")).
#   assets            -> tclauncher\assets, where __main__ reads icon.ico from
#                        os.path.dirname(__file__); parity with the AppImage.
.\.venv-win\Scripts\pyinstaller.exe `
    --onefile `
    --noconsole `
    --noupx `
    --name launcher `
    --icon "$root\icon.ico" `
    --add-data "$root\scripts\update-helper.bat;." `
    --add-data "$root\tclauncher\assets;tclauncher\assets" `
    --distpath dist `
    --workpath build\pyinstaller `
    --specpath build `
    --clean --noconfirm `
    build\entrypoint.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

if (-not (Test-Path "dist\launcher.exe")) { throw "Build produced no exe." }
Write-Host "Built dist\launcher.exe ($version)"
