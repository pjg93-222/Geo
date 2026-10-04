# Keeps the Terrier server on 127.0.0.1:8767 running. Start-TerrierServer.ps1 starts this script hidden and detached and
# records it in _build\terrier-supervisor-pid.json; it is not meant to be run by hand.
# When the recorded server exits without a stop request, it logs the exit to _build\terrier-supervisor.log, waits
# 5, 15, 30, then 60 s, and relaunches through Start-TerrierServer.ps1 -Relaunch (same checks, log rotation, identity
# check and _build\terrier-server-pid.json as any start). After 5 restarts in any 10 minutes it logs "giving up" and exits.
# Stop-TerrierServer.ps1 writes _build\terrier-supervisor.stop first; this script then exits without restarting anything.
$ErrorActionPreference = 'Stop'
$terrierRoot = $PSScriptRoot
$managePath = Join-Path $terrierRoot 'manage.py'
$startScript = Join-Path $terrierRoot 'Start-TerrierServer.ps1'
$logDirectory = Join-Path $terrierRoot '_build'
$record = Join-Path $logDirectory 'terrier-server-pid.json'
$watchRecord = Join-Path $logDirectory 'terrier-supervisor-pid.json'
$stopFlag = Join-Path $logDirectory 'terrier-supervisor.stop'
$supervisorLog = Join-Path $logDirectory 'terrier-supervisor.log'
$backoffSeconds = @(5, 15, 30, 60)
$restartLimit = 5
$restartWindow = [timespan]::FromMinutes(10)

function Write-SupervisorLog([string]$Text) {
    # Someone reading the log can briefly lock it; a line that still cannot be written is dropped rather than letting a
    # logging problem stop the supervisor.
    $line = (Get-Date -Format o) + ' supervisor ' + $PID + ': ' + $Text
    for ($try = 0; $try -lt 10; $try++) {
        try { Add-Content -LiteralPath $supervisorLog -Value $line; return } catch { Start-Sleep -Milliseconds 200 }
    }
}
function Test-StopRequested { return (Test-Path -LiteralPath $stopFlag) }
function Test-StillRecorded {
    # Another supervisor in the record means this one was replaced; it must not relaunch anything.
    try { return [int]((Get-Content -LiteralPath $watchRecord -Raw | ConvertFrom-Json).pid) -eq $PID } catch { return $false }
}
function Get-RecordedServer {
    # Same identity as Stop-TerrierServer.ps1. The handle is opened before the checks and kept, so the PID cannot be
    # reused by another process while it is checked, and the exit code stays readable after the server exits.
    if (!(Test-Path -LiteralPath $record)) { return $null }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$saved.pid) -ErrorAction Stop
        [void]$process.Handle
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        if ($saved.script -eq $managePath -and $owned -and $owned.Name -eq 'python.exe' -and $owned.CommandLine.Contains($managePath) -and $owned.CommandLine.Contains('--port 8767') -and
            [math]::Abs(($process.StartTime.ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1) { return $process }
    } catch { }
    return $null
}
function Test-RecordUnfinished {
    # A start that gave up waiting for readiness records only the process it launched (no launcher_pid).
    try { return !((Get-Content -LiteralPath $record -Raw | ConvertFrom-Json).launcher_pid) } catch { return $false }
}
function Invoke-Start {
    # Runs Start-TerrierServer.ps1 -Relaunch in this process; its output and warnings come back as one line of text, and
    # a failure is rethrown with whatever it said first.
    $said = New-Object 'System.Collections.Generic.List[string]'
    try { & $startScript -Relaunch 3>&1 | ForEach-Object { $said.Add(([string]$_).Trim()) } }
    catch { throw ((@($said) + $_.Exception.Message) -join ' / ') }
    return (@($said) -join ' / ')
}
function Wait-UnlessStopped([int]$Seconds) {
    $until = [datetime]::UtcNow.AddSeconds($Seconds)
    while ([datetime]::UtcNow -lt $until) {
        if (Test-StopRequested) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return !(Test-StopRequested)
}

try {
    $restarts = New-Object 'System.Collections.Generic.List[datetime]'
    $restartNumber = 0
    $server = Get-RecordedServer
    $unfinished = $false
    if ($server) { Write-SupervisorLog "watching server pid $($server.Id)" }
    else { Write-SupervisorLog 'started, but the recorded server is not running' }
    $lost = 'server not running: no recorded server'
    while ($true) {
        if ($server) {
            $tick = 0
            while (!$server.WaitForExit(1000)) {
                if (Test-StopRequested) { Write-SupervisorLog "stop requested; exiting and leaving server pid $($server.Id) to Stop-TerrierServer"; exit 0 }
                # An adopted server whose start timed out: once it listens, let Start-TerrierServer.ps1 finish its record.
                if ($unfinished -and (++$tick % 5) -eq 0 -and @(Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
                    try {
                        $said = Invoke-Start
                        $next = Get-RecordedServer
                        if ($next -and !(Test-RecordUnfinished)) {
                            $server = $next
                            $unfinished = $false
                            Write-SupervisorLog "record completed: watching server pid $($server.Id) ($said)"
                        }
                    } catch { }
                }
            }
            $exitCode = 'unknown'
            try { $exitCode = '0x{0:X8}' -f $server.ExitCode } catch { }
            $lost = "server exited unexpectedly: old pid $($server.Id), exit code $exitCode"
        }
        if (Test-StopRequested) { Write-SupervisorLog "stop requested ($lost); not restarting"; exit 0 }
        $now = [datetime]::UtcNow
        $recent = @($restarts | Where-Object { ($now - $_) -lt $restartWindow }).Count
        if ($recent -ge $restartLimit) {
            Write-SupervisorLog "$lost; giving up: $restartLimit restarts in the last 10 minutes already"
            exit 1
        }
        if (!(Test-StillRecorded)) { Write-SupervisorLog "$lost; another supervisor is recorded, so not restarting"; exit 0 }
        $restartNumber++
        $delay = $backoffSeconds[[math]::Min($recent, $backoffSeconds.Count - 1)]
        Write-SupervisorLog "$lost; restart $restartNumber in $delay s ($($recent + 1) of $restartLimit allowed in 10 minutes)"
        if (!(Wait-UnlessStopped $delay)) { Write-SupervisorLog "stop requested before restart $restartNumber; not restarting"; exit 0 }
        $restarts.Add([datetime]::UtcNow)
        try {
            $said = Invoke-Start
            $server = Get-RecordedServer
            if (!$server) { throw 'the server record does not name a running Terrier server after the launch' }
            $unfinished = Test-RecordUnfinished
            Write-SupervisorLog "restart $restartNumber done: server pid $($server.Id) ($said)"
        } catch {
            $problem = $_.Exception.Message
            # The launch may have failed only in its last steps (for example readiness timing out while Python is still
            # loading). A recorded server that is still running is adopted, not abandoned.
            $server = Get-RecordedServer
            if ($server) {
                $unfinished = Test-RecordUnfinished
                Write-SupervisorLog "restart $restartNumber reported: $problem; the recorded server pid $($server.Id) is running, so watching it"
            } else {
                Write-SupervisorLog "restart $restartNumber failed: $problem"
                $lost = "server not running: restart $restartNumber failed"
            }
        }
    }
} catch {
    Write-SupervisorLog ('stopped by an error: ' + $_.Exception.Message)
    exit 1
}
