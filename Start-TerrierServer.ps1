# -NoSupervisor starts or reuses the server only. Without it, a hidden Watch-TerrierServer.ps1 is also started (unless the
# recorded one is still running) to relaunch the server after an unexpected exit. It relaunches through this script with
# -Relaunch (which implies -NoSupervisor), so every launch makes the same checks, rotates the same logs and writes the
# same record. -Relaunch also refuses to launch while a stop is under way and waits up to 90 s for readiness, not ~15 s.
param([switch]$NoSupervisor, [switch]$Relaunch)
if ($Relaunch) { $NoSupervisor = [switch]$true }
$ErrorActionPreference = 'Stop'
$terrierRoot = $PSScriptRoot
$origin = 'http://127.0.0.1:8767'
$managePath = Join-Path $terrierRoot 'manage.py'
$record = Join-Path $terrierRoot '_build\terrier-server-pid.json'
$watchScript = Join-Path $terrierRoot 'Watch-TerrierServer.ps1'
$watchRecord = Join-Path $terrierRoot '_build\terrier-supervisor-pid.json'
$stopFlag = Join-Path $terrierRoot '_build\terrier-supervisor.stop'
$launcherLock = New-Object System.Threading.Mutex($false, 'Local\TerrierLauncher8767')
$ownsLock = $false
function Test-SupervisorRunning {
    if (!(Test-Path -LiteralPath $watchRecord)) { return $false }
    try {
        $saved = Get-Content -LiteralPath $watchRecord -Raw | ConvertFrom-Json
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$saved.pid)"
        return $owned -and [string]::Equals([string]$saved.script, $watchScript, [StringComparison]::OrdinalIgnoreCase) -and $owned.Name -eq 'powershell.exe' -and
            ([string]$owned.CommandLine).IndexOf($watchScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1
    } catch { return $false }
}
function Start-TerrierSupervisor {
    if (Test-SupervisorRunning) { return }
    # A stop flag left by Stop-TerrierServer.ps1 would make the new supervisor exit at once.
    if (Test-Path -LiteralPath $stopFlag) { Remove-Item -LiteralPath $stopFlag }
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Detached like the server: its own hidden console, so it outlives the shell or shortcut that started it.
    $watch = Start-Process -FilePath $powerShell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',('"'+$watchScript+'"')) -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru
    try {
        @{pid=$watch.Id;script=$watchScript;started=$watch.StartTime.ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $watchRecord
    } catch {
        # Stop-TerrierServer.ps1 can only stop a recorded supervisor, so an unrecorded one must not keep running.
        Stop-Process -Id $watch.Id -ErrorAction SilentlyContinue
        throw
    }
}
function Start-SupervisorOrWarn {
    # The server is already running and recorded; a missing supervisor must not turn that into a failed start.
    try { Start-TerrierSupervisor }
    catch { Write-Warning ('Terrier is running without its supervisor, so it will not be restarted after a crash: ' + $_.Exception.Message) }
}
function Move-TerrierLog([string]$logDirectory, [string]$name) {
    # Keep the previous run's output: terrier-server.err.log becomes terrier-server.err.<local time>.log. The newest ten
    # such copies are kept (a crash loop is at most five restarts, so the first crash's log survives it); the current file
    # is never deleted. A log another program holds open (a viewer tailing it, antivirus) cannot be renamed, so it is
    # copied instead; if even that fails, the start goes on with a warning.
    try {
        $current = Join-Path $logDirectory ($name + '.log')
        $item = Get-Item -LiteralPath $current -ErrorAction SilentlyContinue
        if ($item -and $item.Length -gt 0) {
            $target = Join-Path $logDirectory ($name + '.' + (Get-Date -Format 'yyyyMMddTHHmmss') + '.log')
            if (Test-Path -LiteralPath $target) { $target = Join-Path $logDirectory ($name + '.' + (Get-Date -Format 'yyyyMMddTHHmmssfff') + '.log') }
            try { Move-Item -LiteralPath $current -Destination $target }
            catch {
                Copy-Item -LiteralPath $current -Destination $target
                Write-Warning ($name + '.log is in use; copied it to ' + (Split-Path -Leaf $target) + ' instead of renaming it.')
            }
        }
        $rotated = '^' + [regex]::Escape($name) + '\.\d{8}T\d{6}(\d{3})?\.log$'
        Get-ChildItem -LiteralPath $logDirectory -File | Where-Object { $_.Name -match $rotated } | Sort-Object Name -Descending |
            Select-Object -Skip 10 | ForEach-Object { Remove-Item -LiteralPath $_.FullName -ErrorAction SilentlyContinue }
    } catch { Write-Warning ('Could not keep the previous ' + $name + '.log: ' + $_.Exception.Message) }
}
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
function Complete-InterruptedRecord($listeners) {
    # A start that gave up waiting for readiness leaves the record naming the process it launched (no launcher_pid). When
    # that process is still the recorded one and its own child Python is now the only listener, finish the record exactly
    # as a completed start would, so the server counts as Terrier's again.
    if (!(Test-Path -LiteralPath $record)) { return }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        if ($saved.launcher_pid -or $saved.script -ne $managePath) { return }
        $launcher = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$saved.pid)"
        if (!$launcher -or $launcher.Name -ne 'python.exe' -or !$launcher.CommandLine.Contains($managePath) -or !$launcher.CommandLine.Contains('--port 8767') -or
            [math]::Abs((([datetime]$launcher.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -ge 1) { return }
        $listenerIds = @($listeners.OwningProcess | Select-Object -Unique)
        if ($listenerIds.Count -ne 1) { return }
        $serving = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerIds[0])"
        if (!$serving -or $serving.Name -ne 'python.exe' -or !$serving.CommandLine.Contains($managePath) -or !$serving.CommandLine.Contains('--port 8767') -or
            $serving.ParentProcessId -ne $launcher.ProcessId -or [datetime]$serving.CreationDate -lt [datetime]$launcher.CreationDate) { return }
        @{pid=$serving.ProcessId;launcher_pid=$launcher.ProcessId;script=$managePath;started=([datetime]$serving.CreationDate).ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    } catch { }
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
    # Stop-TerrierServer.ps1 writes the flag before it takes this lock, so a relaunch that gets here during a stop gives up.
    if ($Relaunch -and (Test-Path -LiteralPath $stopFlag)) { throw 'Terrier is being stopped; not relaunching it.' }
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
        if (!(Test-RecordedOwner $existing)) { Complete-InterruptedRecord $existing }
        if (Test-RecordedOwner $existing) {
            if (!(Test-TerrierReady)) { throw 'Terrier is running but its stored data is not ready. See _build\terrier-server.err.log.' }
            if (!$NoSupervisor) { Start-SupervisorOrWarn }
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
    Move-TerrierLog $logDirectory 'terrier-server.out'
    Move-TerrierLog $logDirectory 'terrier-server.err'
    $server = Start-Process -FilePath $runtimePython -ArgumentList @(('"'+$managePath+'"'),'serve','--port','8767') -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'terrier-server.out.log') -RedirectStandardError (Join-Path $logDirectory 'terrier-server.err.log')
    @{pid=$server.Id;script=$managePath;started=$server.StartTime.ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    $ready = $false
    $readyBy = [datetime]::UtcNow.AddSeconds(90)
    for ($attempt=0; $attempt -lt 60 -or ($Relaunch -and [datetime]::UtcNow -lt $readyBy); $attempt++) {
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
    if (!$NoSupervisor) { Start-SupervisorOrWarn }
    Write-Output "Terrier is ready at $origin (recorded dataset)"
} finally {
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    $launcherLock.Dispose()
}
