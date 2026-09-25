<#
.SYNOPSIS
  Richtet den Catawiki-Scan auf dem Heimrechner ein (Windows-Aufgabenplanung).

.DESCRIPTION
  Prod ist bei Catawiki per IP gesperrt. Diese Aufgabe startet alle
  -IntervalMinutes Minuten backend\app\tools\catawiki_home_scan.py. Ein Lauf
  fragt Prod nur, ob ein Scan faellig ist (Button "Catawiki jetzt scannen" in
  der Watchlist oder Zeitplan in den Einstellungen), und endet sonst sofort.

  Legt beim ersten Aufruf %USERPROFILE%\.lego-arbitrage\home-scan.env an. Dort
  LEGO_REMOTE_SCAN_TOKEN auf denselben Wert setzen wie in der App unter
  Einstellungen > Catawiki > Heimrechner-Token.

  Keine Adminrechte noetig; die Aufgabe laeuft als angemeldeter Benutzer.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-catawiki-home-scan.ps1 -ApiUrl https://example.de/lego
  powershell -ExecutionPolicy Bypass -File scripts\install-catawiki-home-scan.ps1 -Uninstall
#>
param(
    [int]$IntervalMinutes = 10,
    # Adresse der App wie im Browser, mit Basispfad, z. B. https://example.de/lego
    [string]$ApiUrl,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "LEGO Arbitrage Catawiki-Scan"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Aufgabe '$TaskName' entfernt."
    return
}

$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo "backend"
$pythonw = Join-Path $backend ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "Nicht gefunden: $pythonw. Zuerst die Backend-venv anlegen (backend\.venv)."
}

$configDir = Join-Path $env:USERPROFILE ".lego-arbitrage"
$configFile = Join-Path $configDir "home-scan.env"
if (-not (Test-Path $configFile)) {
    if (-not $ApiUrl) { throw "-ApiUrl fehlt (Adresse der App wie im Browser, mit /lego)." }
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    $token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    @(
        "# Catawiki-Heimrechner-Scan. Token identisch in der App eintragen:",
        "# Einstellungen > Catawiki > Heimrechner-Token",
        "LEGO_API_URL=$ApiUrl",
        "LEGO_REMOTE_SCAN_TOKEN=$token"
    ) | Set-Content -Path $configFile -Encoding utf8
    Write-Host "Konfiguration angelegt: $configFile"
    Write-Host "Token fuer die App (Einstellungen > Catawiki > Heimrechner-Token):"
    Write-Host "  $token"
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m app.tools.catawiki_home_scan" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Fragt Prod alle $IntervalMinutes Minuten nach einem Catawiki-Scan und fuehrt ihn aus." -Force | Out-Null

Write-Host "Aufgabe '$TaskName' eingerichtet (alle $IntervalMinutes Minuten)."
Write-Host "Log: $(Join-Path $configDir 'home-scan.log')"
Write-Host "Test von Hand: cd `"$backend`"; .\.venv\Scripts\python.exe -m app.tools.catawiki_home_scan --force --dry-run"
