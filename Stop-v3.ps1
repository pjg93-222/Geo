# Stop ONLY the v3 preview process this copy started (the PIDs recorded in _build\pid_<port>.json).
# Each PID is stopped only if it is a python process whose command line holds THIS copy's manage.py and "serve".
# Anything that owns port 8765 is never stopped.
param([int]$Port = 8766)
$ErrorActionPreference = 'Stop'
$V3 = $PSScriptRoot
if ($Port -eq 8765) { throw 'Port 8765 belongs to the live app. Refusing.' }
$infoFile = Join-Path $V3 "_build\pid_$Port.json"
if (-not (Test-Path -LiteralPath $infoFile)) { Write-Host "No v3 preview is recorded for port $Port."; return }
$info = Get-Content -LiteralPath $infoFile -Raw | ConvertFrom-Json
$live = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess })
$manage = Join-Path $V3 'manage.py'
$stopped = @()
# child (listener) first, then the venv launcher that started it
foreach ($procId in @($info.listener_pid, $info.launcher_pid) | Select-Object -Unique) {
    if ($live -contains $procId) { Write-Host "PID $procId owns 8765; not touching it."; continue }
    $pi = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if (-not $pi) { Write-Host "PID $procId has already exited (the venv launcher ends when its child stops)."; continue }
    if ($pi.Name -match '^python' -and $pi.CommandLine.Contains($manage) -and $pi.CommandLine -match 'manage\.py"?\s+serve') {
        Stop-Process -Id $procId -Force
        $stopped += $procId
    } else { Write-Host "PID $procId is running but is not the v3 server (different command line); left alone." }
}
if ($stopped.Count) {
    Remove-Item -LiteralPath $infoFile, (Join-Path $V3 '.local\server.pid') -ErrorAction SilentlyContinue
    Write-Host "v3 preview stopped (PIDs $($stopped -join ', '))."
}
