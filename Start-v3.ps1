# Start the v3 PREVIEW on 127.0.0.1:8766, detached, against V3\.local. It never touches 127.0.0.1:8765 (the live app).
# Agents always run in demo mode (CU_DEMO=1). -NoDemo is for Paul only.
param([int]$Port = 8766, [switch]$NoDemo)
$ErrorActionPreference = 'Stop'
$V3       = $PSScriptRoot
$BUILD    = Join-Path $V3 '_build'
$LOGS     = Join-Path $BUILD 'logs'
$localDir = Join-Path $V3 '.local'
$origin   = "http://127.0.0.1:$Port"
if ($Port -eq 8765) { throw 'Port 8765 belongs to the live app. Refusing to start there.' }
New-Item -ItemType Directory -Path $LOGS, $localDir -Force | Out-Null

function Test-V3Ready {
    try {
        $h = Invoke-RestMethod "$origin/api/health" -TimeoutSec 3
        return ($h.status -eq 'ok')
    } catch { return $false }
}

$mutex = New-Object System.Threading.Mutex($false, "Local\CreditUnionGeographyLauncher$Port")
$own = $false
try {
    try { $own = $mutex.WaitOne(60000) } catch [System.Threading.AbandonedMutexException] { $own = $true }
    if (-not $own) { throw 'Another v3 launch is in progress.' }
    if (Test-V3Ready) { Write-Host "v3 preview already running at $origin"; return }
    $occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($occupied) { throw "Port $Port is occupied by PID $($occupied.OwningProcess) and is not answering /api/health. Nothing was stopped." }

    $py = (Resolve-Path -LiteralPath (Join-Path $V3 '..\..\work\.venv\Scripts\python.exe')).Path
    if (-not (Test-Path -LiteralPath (Join-Path $V3 'frontend\dist\index.html'))) { throw 'frontend\dist is missing: run "npm run build" in V3\frontend.' }

    # environment for the child only (this process's copy is discarded when the script ends)
    $env:CU_DATA_DIR      = $localDir
    $env:CU_COOKIE_PREFIX = 'cu_v3_'
    $env:CU_DEMO          = $(if ($NoDemo) { '0' } else { '1' })
    $env:PYTHONPYCACHEPREFIX = Join-Path $localDir 'pycache'
    # numpy (score store) must not start a BLAS thread pool: with little free memory its import has crashed
    $env:OPENBLAS_NUM_THREADS = '1'; $env:OMP_NUM_THREADS = '1'

    # keep earlier logs: rotate instead of overwrite
    $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
    foreach ($n in "server_$Port.out.log", "server_$Port.err.log") {
        $p = Join-Path $LOGS $n
        if (Test-Path -LiteralPath $p) { Move-Item -LiteralPath $p -Destination (Join-Path $LOGS ("$stamp-$n")) }
    }
    $manage = Join-Path $V3 'manage.py'
    $proc = Start-Process -FilePath $py -ArgumentList @(('"' + $manage + '"'), 'serve', '--port', "$Port") -WorkingDirectory $V3 -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LOGS "server_$Port.out.log") -RedirectStandardError (Join-Path $LOGS "server_$Port.err.log") -PassThru
    $proc.Id | Set-Content -LiteralPath (Join-Path $localDir 'server.pid')

    $ready = $false
    for ($i = 0; $i -lt 80; $i++) {
        if (Test-V3Ready) { $ready = $true; break }
        $proc.Refresh()
        if ($proc.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) { throw "v3 preview did not become ready. See $LOGS\server_$Port.err.log" }

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1
    $info = [ordered]@{
        port = $Port; launcher_pid = $proc.Id; listener_pid = $listener.OwningProcess
        started_utc = (Get-Date).ToUniversalTime().ToString('o'); demo = (-not $NoDemo)
        data_dir = $localDir; cookie_prefix = 'cu_v3_'
        out_log = (Join-Path $LOGS "server_$Port.out.log"); err_log = (Join-Path $LOGS "server_$Port.err.log")
    }
    $info | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BUILD "pid_$Port.json") -Encoding utf8
    Write-Host "v3 preview ready at $origin (launcher PID $($proc.Id), listener PID $($listener.OwningProcess), demo=$(-not $NoDemo))"
} finally {
    if ($own) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
