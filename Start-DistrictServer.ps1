# Starts or reuses ONE Terrier district server (Oregon or Connecticut) on 127.0.0.1:<Port>, hidden and detached. It is a copy of
# Start-TerrierServer.ps1 (Colorado, 8767), which stays untouched: only the district, port, environment and file names differ, plus
# the three notes marked "district copy" below. co-05 and port 8767 are refused (Colorado has its own scripts).
# usage: Start-DistrictServer.ps1 -District or-05|ct-01 -Port 8775|8777 [-NoSupervisor]
# -NoSupervisor starts or reuses the server only. Without it, a hidden Watch-DistrictServer.ps1 is also started (unless the
# recorded one is still running) to relaunch the server after an unexpected exit. It relaunches through this script with
# -Relaunch (which implies -NoSupervisor), so every launch makes the same checks, rotates the same logs and writes the
# same record. -Relaunch also refuses to launch while a stop is under way. Every start waits up to 180 s for readiness.
# A port that already has a listener is never started a second time: the recorded server is reused, any other listener is an error.
param(
    [Parameter(Mandatory)][ValidateSet('or-05', 'ct-01')][string]$District,
    [Parameter(Mandatory)][ValidateRange(1024, 65535)][int]$Port,
    [switch]$NoSupervisor, [switch]$Relaunch
)
if ($Relaunch) { $NoSupervisor = [switch]$true }
$ErrorActionPreference = 'Stop'
if ($Port -eq 8767) { throw 'port 8767 is live Colorado: its own scripts start it, never this one' }
$terrierRoot = $PSScriptRoot
$origin = "http://127.0.0.1:$Port"
$managePath = Join-Path $terrierRoot 'manage.py'
$dataDirectory = Join-Path $terrierRoot ".local\$District"
$record = Join-Path $terrierRoot "_build\district-$District-server-pid.json"
$watchScript = Join-Path $terrierRoot 'Watch-DistrictServer.ps1'
$watchRecord = Join-Path $terrierRoot "_build\district-$District-supervisor-pid.json"
$stopFlag = Join-Path $terrierRoot "_build\district-$District-supervisor.stop"
$launcherLock = New-Object System.Threading.Mutex($false, "Local\TerrierLauncher$Port")
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
    # A stop flag left by Stop-DistrictServer.ps1 would make the new supervisor exit at once.
    if (Test-Path -LiteralPath $stopFlag) { Remove-Item -LiteralPath $stopFlag }
    $powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Detached like the server: its own hidden console, so it outlives the shell or shortcut that started it.
    $watch = Start-Process -FilePath $powerShell -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',('"'+$watchScript+'"'),'-District',$District,'-Port',"$Port") -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru
    try {
        @{pid=$watch.Id;script=$watchScript;started=$watch.StartTime.ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $watchRecord
    } catch {
        # Stop-DistrictServer.ps1 can only stop a recorded supervisor, so an unrecorded one must not keep running.
        Stop-Process -Id $watch.Id -ErrorAction SilentlyContinue
        throw
    }
}
function Start-SupervisorOrWarn {
    # The server is already running and recorded; a missing supervisor must not turn that into a failed start.
    try { Start-TerrierSupervisor }
    catch { Write-Warning ("The $District server is running without its supervisor, so it will not be restarted after a crash: " + $_.Exception.Message) }
}
function Move-TerrierLog([string]$logDirectory, [string]$name) {
    # Keep the previous run's output: district-or-05-server.err.log becomes district-or-05-server.err.<local time>.log. The newest ten
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
    # district copy 1 of 3: readiness is GET /api/v3/meta answering 200, the check these two districts were started and verified with.
    # Colorado's /_terrier/health also needs the contacts store and a verified-source flag that these two stores are not known to carry.
    try { return (Invoke-WebRequest -Uri "$origin/api/v3/meta" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { return $false }
}
function Test-RecordedOwner($listeners) {
    if (!(Test-Path -LiteralPath $record)) { return $false }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        return $saved.script -eq $managePath -and $owned -and $owned.Name -eq 'python.exe' -and
            $owned.CommandLine.Contains($managePath) -and $owned.CommandLine.Contains("--port $Port") -and
            @($listeners.OwningProcess) -contains [int]$saved.pid -and
            [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1
    } catch { return $false }
}
function Complete-InterruptedRecord($listeners) {
    # A start that gave up waiting for readiness leaves the record naming the process it launched (no launcher_pid). When
    # that process is still the recorded one and its own child Python is now the only listener, finish the record exactly
    # as a completed start would, so the server counts as this district's again.
    if (!(Test-Path -LiteralPath $record)) { return }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        if ($saved.launcher_pid -or $saved.script -ne $managePath) { return }
        $launcher = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$saved.pid)"
        if (!$launcher -or $launcher.Name -ne 'python.exe' -or !$launcher.CommandLine.Contains($managePath) -or !$launcher.CommandLine.Contains("--port $Port") -or
            [math]::Abs((([datetime]$launcher.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -ge 1) { return }
        $listenerIds = @($listeners.OwningProcess | Select-Object -Unique)
        if ($listenerIds.Count -ne 1) { return }
        $serving = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerIds[0])"
        if (!$serving -or $serving.Name -ne 'python.exe' -or !$serving.CommandLine.Contains($managePath) -or !$serving.CommandLine.Contains("--port $Port") -or
            $serving.ParentProcessId -ne $launcher.ProcessId -or [datetime]$serving.CreationDate -lt [datetime]$launcher.CreationDate) { return }
        @{pid=$serving.ProcessId;launcher_pid=$launcher.ProcessId;script=$managePath;started=([datetime]$serving.CreationDate).ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    } catch { }
}
try {
    try { $ownsLock = $launcherLock.WaitOne(15000) }
    catch [System.Threading.AbandonedMutexException] { $ownsLock = $true }
    if (!$ownsLock) { throw "The $District server is already starting. Try again in a moment." }
    # Stop-DistrictServer.ps1 writes the flag before it takes this lock, so a relaunch that gets here during a stop gives up.
    if ($Relaunch -and (Test-Path -LiteralPath $stopFlag)) { throw "The $District server is being stopped; not relaunching it." }
    if (!(Test-Path -LiteralPath (Join-Path $terrierRoot 'frontend\dist\index.html'))) { throw 'The Terrier build is missing. Run Install-TerrierDesktop.ps1 first.' }
    foreach ($name in 'scores.sqlite3','contacts.sqlite3','street.sqlite3','accounts.sqlite3') {
        if (!(Test-Path -LiteralPath (Join-Path $dataDirectory $name))) { throw "The stored dataset is missing $name for $District. No generated records will be substituted." }
    }
    $runtimePython = Join-Path $terrierRoot '..\..\work\.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $runtimePython)) { $runtimePython = Join-Path $terrierRoot '.venv\Scripts\python.exe' }
    if (!(Test-Path -LiteralPath $runtimePython)) { throw 'The existing Python application runtime is unavailable.' }
    $runtimePython = (Resolve-Path -LiteralPath $runtimePython).Path
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        if (!(Test-RecordedOwner $existing)) { Complete-InterruptedRecord $existing }
        if (Test-RecordedOwner $existing) {
            if (!(Test-TerrierReady)) { throw "The $District server is running but did not answer /api/v3/meta. See _build\district-$District-server.err.log." }
            if (!$NoSupervisor) { Start-SupervisorOrWarn }
            Write-Output "Terrier $District is ready at $origin"
            return
        }
        throw "Port $Port is used by another app. The $District server did not stop or change it."
    }
    $logDirectory = Join-Path $terrierRoot '_build'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    # district copy 2 of 3: the environment is exactly what the earlier by-hand starts of these servers had, CU_DISTRICT and nothing else, so
    # settings.py gives this district its own data folder and its own cookie names (or05_ / ct01_). Colorado's variables (CU_DATA_DIR,
    # CU_COOKIE_PREFIX, CU_DEMO, PYTHONPYCACHEPREFIX, the thread caps) are NOT set, and any that were inherited are cleared so
    # they cannot point this server at another district's data or cookies.
    foreach ($name in 'CU_DATA_DIR','CU_COOKIE_PREFIX','CU_DEMO','PYTHONPYCACHEPREFIX','OPENBLAS_NUM_THREADS','OMP_NUM_THREADS') { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    $env:CU_DISTRICT = $District
    Move-TerrierLog $logDirectory "district-$District-server.out"
    Move-TerrierLog $logDirectory "district-$District-server.err"
    $server = Start-Process -FilePath $runtimePython -ArgumentList @(('"'+$managePath+'"'),'serve','--port',"$Port") -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory "district-$District-server.out.log") -RedirectStandardError (Join-Path $logDirectory "district-$District-server.err.log")
    @{pid=$server.Id;script=$managePath;started=$server.StartTime.ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    $ready = $false
    $readyBy = [datetime]::UtcNow.AddSeconds(180)
    # district copy 3 of 3: every start (not only -Relaunch) waits up to 180 s, polling once a second. While a district server warms its
    # stores (about 2 to 3 minutes) it answers /api/v3/meta only after about 85 s (Oregon, 2026-10-09), so Colorado's 15 s first-start
    # window and 90 s relaunch window would be too tight and would leave an unfinished record.
    while ([datetime]::UtcNow -lt $readyBy) {
        $server.Refresh()
        if ($server.HasExited) { break }
        if (Test-TerrierReady) { $ready=$true;break }
        Start-Sleep -Seconds 1
    }
    if (!$ready) { throw ("The $District server did not answer /api/v3/meta" + $(if ($server.HasExited) { ", it exited with code $($server.ExitCode) (3221227274 means start once more)" } else { '' }) + ". See _build\district-$District-server.err.log.") }
    # Windows virtualenv launchers can delegate to a child Python process. Track the actual listener.
    $listener = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    $listenerIds = @($listener.OwningProcess | Select-Object -Unique)
    if ($listenerIds.Count -ne 1) { throw "The $District listener identity is ambiguous." }
    $serving = Get-CimInstance Win32_Process -Filter "ProcessId=$($listenerIds[0])"
    if (!$serving -or $serving.Name -ne 'python.exe' -or !$serving.CommandLine.Contains($managePath) -or
        !$serving.CommandLine.Contains("--port $Port") -or ($serving.ProcessId -ne $server.Id -and $serving.ParentProcessId -ne $server.Id)) {
        throw "The $District listener is not the Python process just started."
    }
    @{pid=$serving.ProcessId;launcher_pid=$server.Id;script=$managePath;started=([datetime]$serving.CreationDate).ToUniversalTime().ToString('o');url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    if (!$NoSupervisor) { Start-SupervisorOrWarn }
    Write-Output "Terrier $District is ready at $origin"
} catch {
    # Nobody sees this script's console when it is started detached, so a failed manual start is also written down (the supervisor logs its own relaunch failures).
    if (!$Relaunch) { try { Add-Content -LiteralPath (Join-Path $terrierRoot "_build\district-$District-launcher.log") -Value ((Get-Date -Format o) + ' ' + $_.Exception.Message) } catch { } }
    throw
} finally {
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    $launcherLock.Dispose()
}
