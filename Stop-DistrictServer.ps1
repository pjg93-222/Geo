# Stops one Terrier district server (Oregon or Connecticut) and its supervisor. A copy of Stop-TerrierServer.ps1 (Colorado, 8767),
# which stays untouched: only the district, port and file names differ.
# usage: Stop-DistrictServer.ps1 -District or-05|ct-01 -Port 8775|8777
param(
    [Parameter(Mandatory)][ValidateSet('or-05', 'ct-01')][string]$District,
    [Parameter(Mandatory)][ValidateRange(1024, 65535)][int]$Port
)
$ErrorActionPreference = 'Stop'
if ($Port -eq 8767) { throw 'port 8767 is live Colorado: its own scripts stop it, never this one' }
$record = Join-Path $PSScriptRoot "_build\district-$District-server-pid.json"
$managePath = Join-Path $PSScriptRoot 'manage.py'
$watchRecord = Join-Path $PSScriptRoot "_build\district-$District-supervisor-pid.json"
$watchScript = Join-Path $PSScriptRoot 'Watch-DistrictServer.ps1'
$stopFlag = Join-Path $PSScriptRoot "_build\district-$District-supervisor.stop"
$launcherLock = New-Object System.Threading.Mutex($false, "Local\TerrierLauncher$Port")
$ownsLock = $false
# Order matters, so a deliberate stop is never undone:
# 1. The stop flag tells any supervisor not to restart the server (it checks every second) and makes a relaunch that
#    reaches Start-DistrictServer.ps1 after this point refuse to launch. The flag stays until the next supervised start.
# 2. The launcher lock waits out a launch already under way, so the record read below names the last server started.
# 3. The recorded supervisor is awaited and, only if it is still the same process after 40 s, stopped.
# 4. The recorded server is stopped exactly as before. Problems in steps 1-3 are warnings: they never prevent step 4.
function Set-StopFlag {
    try { if (Test-Path -LiteralPath (Join-Path $PSScriptRoot '_build')) { Set-Content -LiteralPath $stopFlag -Value (Get-Date -Format o) } }
    catch { Write-Warning ('Could not write the supervisor stop flag: ' + $_.Exception.Message) }
}
function Get-RecordedSupervisor($saved) {
    $watching = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$saved.pid)"
    if ($watching -and [string]::Equals([string]$saved.script, $watchScript, [StringComparison]::OrdinalIgnoreCase) -and $watching.Name -eq 'powershell.exe' -and
        ([string]$watching.CommandLine).IndexOf($watchScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        [math]::Abs((([datetime]$watching.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1) { return $watching }
    return $null
}
try {
    Set-StopFlag
    try { $ownsLock = $launcherLock.WaitOne(120000) }
    catch [System.Threading.AbandonedMutexException] { $ownsLock = $true }
    if (!$ownsLock) { Write-Warning "A $District start is still running after 120 s; stopping without waiting for it." }
    # A supervised start that held the lock removes the flag before it starts its supervisor; set it again for that one.
    Set-StopFlag
    try {
        if (Test-Path -LiteralPath $watchRecord) {
            $watch = Get-Content -LiteralPath $watchRecord -Raw | ConvertFrom-Json
            if ($watch -and (Get-RecordedSupervisor $watch)) {
                Wait-Process -Id ([int]$watch.pid) -Timeout 40 -ErrorAction SilentlyContinue
                if (Get-RecordedSupervisor $watch) {
                    Stop-Process -Id ([int]$watch.pid) -ErrorAction SilentlyContinue
                    Wait-Process -Id ([int]$watch.pid) -Timeout 10 -ErrorAction SilentlyContinue
                }
                Write-Output "Terrier $District supervisor stopped."
            }
        }
    } catch { Write-Warning ("Could not check the $District supervisor (the stop flag still keeps it from restarting the server): " + $_.Exception.Message) }
    if (!(Test-Path -LiteralPath $record)) { Write-Output "No $District process record." }
    else {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        if ($saved.script -eq $managePath -and $owned -and $owned.Name -eq 'python.exe' -and $owned.CommandLine.Contains($managePath) -and $owned.CommandLine.Contains("--port $Port") -and [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1) {
            Stop-Process -Id $saved.pid
            Wait-Process -Id $saved.pid -Timeout 10 -ErrorAction SilentlyContinue
            Write-Output "Terrier $District server stopped."
        } else { Write-Output 'Recorded process is absent or identity changed; nothing stopped.' }
    }
} finally {
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    $launcherLock.Dispose()
}
