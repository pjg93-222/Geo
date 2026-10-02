$ErrorActionPreference = 'Stop'
$terrierRoot = $PSScriptRoot
$origin = 'http://127.0.0.1:8767'
$managePath = Join-Path $terrierRoot 'manage.py'
$record = Join-Path $terrierRoot '_build\terrier-server-pid.json'
$launcherLock = New-Object System.Threading.Mutex($false, 'Local\TerrierLauncher8767')
$ownsLock = $false
function Test-TerrierReady {
    try {
        $health = Invoke-RestMethod "$origin/_terrier/health" -TimeoutSec 5
        return $health.app -eq 'Terrier' -and $health.mode -eq 'recorded' -and $health.version -eq 2 -and !$health.names_masked -and $health.people -gt 0
    } catch { return $false }
}
function Test-RecordedOwner($listeners) {
    if (!(Test-Path -LiteralPath $record)) { return $false }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        return $saved.script -eq $managePath -and $owned -and $owned.Name -eq 'python.exe' -and
            $owned.CommandLine.Contains($managePath) -and $owned.CommandLine.Contains('--port 8767') -and
            @($listeners.OwningProcess) -contains [int]$saved.pid -and
            [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1
    } catch { return $false }
}
function Stop-OwnedSyntheticPreview($listeners) {
    $oldRecord = Join-Path $terrierRoot '_build\test-demo-pid.json'
    $oldScript = Join-Path $terrierRoot 'frontend\scripts\test-demo-server.mjs'
    if (!(Test-Path -LiteralPath $oldRecord)) { return $false }
    try {
        $saved = Get-Content -LiteralPath $oldRecord -Raw | ConvertFrom-Json
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        if ($saved.script -ne $oldScript -or !$owned -or $owned.Name -ne 'node.exe' -or
            !$owned.CommandLine.Contains($oldScript) -or @($listeners.OwningProcess) -notcontains [int]$saved.pid -or
            [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -ge 1) { return $false }
        Stop-Process -Id $saved.pid
        Wait-Process -Id $saved.pid -Timeout 10 -ErrorAction SilentlyContinue
        return $true
    } catch { return $false }
}
try {
    try { $ownsLock = $launcherLock.WaitOne(15000) }
    catch [System.Threading.AbandonedMutexException] { $ownsLock = $true }
    if (!$ownsLock) { throw 'Terrier is already starting. Try again in a moment.' }
    if (!(Test-Path -LiteralPath (Join-Path $terrierRoot 'frontend\dist\index.html'))) { throw 'The Terrier build is missing. Run Install-TerrierDesktop.ps1 first.' }
    foreach ($name in 'scores.sqlite3','contacts.sqlite3','street.sqlite3','accounts.sqlite3') {
        if (!(Test-Path -LiteralPath (Join-Path $terrierRoot ('.local\'+$name)))) { throw "The stored dataset is missing $name. No generated records will be substituted." }
    }
    $runtimePython = Join-Path $terrierRoot '..\..\work\.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $runtimePython)) { $runtimePython = Join-Path $terrierRoot '.venv\Scripts\python.exe' }
    if (!(Test-Path -LiteralPath $runtimePython)) { throw 'The existing Python application runtime is unavailable.' }
    $runtimePython = (Resolve-Path -LiteralPath $runtimePython).Path
    $existing = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        if (Test-RecordedOwner $existing) {
            if (!(Test-TerrierReady)) { throw 'Terrier is running but its stored data is not ready. See _build\terrier-server.err.log.' }
            Write-Output "Terrier is ready at $origin (recorded dataset)"
            return
        }
        if (!(Stop-OwnedSyntheticPreview $existing)) { throw 'Port 8767 is used by another app. Terrier did not stop or change it.' }
    }
    $logDirectory = Join-Path $terrierRoot '_build'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $env:CU_DATA_DIR = Join-Path $terrierRoot '.local'
    $env:CU_COOKIE_PREFIX = 'cu_terrier_'
    $env:CU_DEMO = '0'
    $env:PYTHONPYCACHEPREFIX = Join-Path $terrierRoot '.local\pycache'
    $env:OPENBLAS_NUM_THREADS = '1'
    $env:OMP_NUM_THREADS = '1'
    $server = Start-Process -FilePath $runtimePython -ArgumentList @(('"'+$managePath+'"'),'serve','--port','8767') -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'terrier-server.out.log') -RedirectStandardError (Join-Path $logDirectory 'terrier-server.err.log')
    @{pid=$server.Id;script=$managePath;started=$server.StartTime.ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    $ready = $false
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        $server.Refresh()
        if ($server.HasExited) { break }
        if (Test-TerrierReady) { $ready=$true;break }
        Start-Sleep -Milliseconds 250
    }
    if (!$ready) { throw 'Terrier could not open its stored dataset. See _build\terrier-server.err.log.' }
    # Windows virtualenv launchers can delegate to a child Python process. Track the actual listener.
    $listener = @(Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction Stop)
    $listenerIds = @($listener.OwningProcess | Select-Object -Unique)
    if ($listenerIds.Count -ne 1) { throw 'Terrier listener identity is ambiguous.' }
    $serving = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerIds[0])"
    if (!$serving -or $serving.Name -ne 'python.exe' -or !$serving.CommandLine.Contains($managePath) -or
        !$serving.CommandLine.Contains('--port 8767') -or ($serving.ProcessId -ne $server.Id -and $serving.ParentProcessId -ne $server.Id)) {
        throw 'Terrier listener is not the Python process just started.'
    }
    @{pid=$serving.ProcessId;launcher_pid=$server.Id;script=$managePath;started=([datetime]$serving.CreationDate).ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    Write-Output "Terrier is ready at $origin (recorded dataset)"
} finally {
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    $launcherLock.Dispose()
}
