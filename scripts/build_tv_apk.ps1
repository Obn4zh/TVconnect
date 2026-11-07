# Builds tv-app\app\build\outputs\apk\release\app-release.apk (and runs unit tests)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\tv-app")
.\gradlew.bat testDebugUnitTest assembleRelease
Write-Host "Done: tv-app\app\build\outputs\apk\release\app-release.apk"
