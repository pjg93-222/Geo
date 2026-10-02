$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$origin = 'http://127.0.0.1:8767'
$demoScript = Join-Path $demoRoot 'frontend\scripts\test-demo-server.mjs'
$record = Join-Path $demoRoot '_build\test-demo-pid.json'
$launcherLock = New-Object System.Threading.Mutex($false, 'Local\TerrierLauncher8767')
$ownsLock = $false
function Test-TerrierReady {
    try {
        $health = Invoke-RestMethod "$origin/_terrier/health" -TimeoutSec 2
        return $health.app -eq 'Terrier' -and $health.mode -eq 'synthetic' -and $health.version -eq 1
    } catch { return $false }
}
function Test-TerrierOwner($listeners) {
    if (!(Test-Path -LiteralPath $record)) { return $false }
    try {
        $saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
        $ownedProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
        return $saved.script -eq $demoScript -and $ownedProcess -and $ownedProcess.Name -eq 'node.exe' -and
            $ownedProcess.CommandLine.Contains($demoScript) -and
            @($listeners.OwningProcess) -contains [int]$saved.pid -and
            [math]::Abs((([datetime]$ownedProcess.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1
    } catch { return $false }
}
try {
    try { $ownsLock = $launcherLock.WaitOne(15000) }
    catch [System.Threading.AbandonedMutexException] { $ownsLock = $true }
    if (!$ownsLock) { throw 'Terrier is already starting. Try again in a moment.' }
    if (!(Test-Path -LiteralPath (Join-Path $demoRoot 'frontend\dist\index.html'))) { throw 'The Terrier build is missing. Run Install-TerrierDesktop.ps1 first.' }
    $existing = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        if (!(Test-TerrierOwner $existing)) { throw 'Port 8767 is used by another app. Terrier did not stop or change it.' }
        if (!(Test-TerrierReady)) { throw 'Terrier is running but is not ready. Run Stop-test-demo.ps1, then reopen Terrier.' }
        Write-Output "Terrier is ready at $origin"
        return
    }
    $nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
    $logDirectory = Join-Path $demoRoot '_build'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $demoProcess = Start-Process -FilePath $nodeExe -ArgumentList @(('"' + $demoScript + '"')) -WorkingDirectory (Join-Path $demoRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'test-demo.out.log') -RedirectStandardError (Join-Path $logDirectory 'test-demo.err.log')
    @{pid=$demoProcess.Id; script=$demoScript; started=$demoProcess.StartTime.ToUniversalTime().ToString('o'); url=$origin} | ConvertTo-Json | Set-Content -LiteralPath $record
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        $demoProcess.Refresh()
        if ($demoProcess.HasExited) { break }
        if (Test-TerrierReady) { $ready = $true; break }
        Start-Sleep -Milliseconds 200
    }
    if (!$ready) { throw 'Terrier did not become ready. See _build\test-demo.err.log.' }
    Write-Output "Terrier is ready at $origin"
} finally {
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    $launcherLock.Dispose()
}
