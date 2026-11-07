# Builds the Windows desktop client and packs it for transfer to another PC:
#   desktop\dist\TVconnect\TVconnect.exe     (one-folder build, adb bundled in _internal\adb)
#   desktop\dist\TVconnect-win64.zip         (copy this to the other PC, unzip, run TVconnect.exe)
# Optional: $env:TVCONNECT_ADB_DIR = "C:\path\to\platform-tools" to choose which adb gets bundled.
# Native tools write progress to stderr, so failures are detected by exit code, not by stderr output.
Set-Location (Join-Path $PSScriptRoot "..\desktop")

function Invoke-Step([string]$what, [scriptblock]$cmd) {
    & $cmd 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path .venv)) { Invoke-Step "venv" { py -3 -m venv .venv } }
Invoke-Step "pip" { .\.venv\Scripts\python.exe -m pip install -q --upgrade pip }
Invoke-Step "pip install" { .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller }
Invoke-Step "PyInstaller" { .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --log-level WARN packaging\tvconnect.spec }

if (-not (Test-Path dist\TVconnect\_internal\adb\adb.exe)) { Write-Warning "adb was not bundled" }
$zip = "dist\TVconnect-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\TVconnect -DestinationPath $zip
Write-Host ("Done: desktop\{0} ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
