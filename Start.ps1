param([switch]$NoBrowser, [switch]$Quiet, [switch]$AppWindow)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$origin = 'http://127.0.0.1:8765'
$localDir = Join-Path $appRoot '.local'
$launcherLock = $null
$ownsLock = $false
$transcriptStarted = $false

function Test-AppReady {
    try {
        $health = Invoke-RestMethod "$origin/api/health" -TimeoutSec 3
        return $health.status -eq 'ok' -and $health.schema -eq 'cu-summary-v1'
    } catch { return $false }
}

try {
    Set-Location -LiteralPath $appRoot
    New-Item -ItemType Directory -Path $localDir -Force | Out-Null
    $launcherLock = New-Object System.Threading.Mutex($false, 'Local\CreditUnionGeographyLauncher8765')
    try { $ownsLock = $launcherLock.WaitOne(60000) }
    catch [System.Threading.AbandonedMutexException] { $ownsLock = $true }
    if (-not $ownsLock) { throw 'Another launch is still setting up. Wait a moment and open the shortcut again.' }
    Start-Transcript -LiteralPath (Join-Path $localDir 'launcher.log') -Append | Out-Null
    $transcriptStarted = $true

    if (-not (Test-AppReady)) {
        $occupied = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        if ($occupied) { throw 'Port 8765 is occupied by an unresponsive service. Use Stop Geography, then try again. No process was stopped automatically.' }
        $runtimePython = Join-Path $appRoot '..\..\work\.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $runtimePython)) {
            $runtimePython = Join-Path $appRoot '.venv\Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $runtimePython)) {
                $basePython = $env:CU_PYTHON
                if (-not $basePython) {
                    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
                    if (Test-Path -LiteralPath $bundledPython) { $basePython = $bundledPython }
                    else { $basePython = (Get-Command python.exe -ErrorAction Stop).Source }
                }
                & $basePython -m venv (Join-Path $appRoot '.venv')
                if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
                & $runtimePython -m pip install -r requirements.lock.txt
                if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
            }
        }
        $runtimePython = (Resolve-Path -LiteralPath $runtimePython).Path
        if (-not (Test-Path -LiteralPath (Join-Path $appRoot 'frontend\dist\index.html'))) {
            throw 'The built interface is missing. Run the frontend build before opening the app.'
        }
        & $runtimePython manage.py migrate --noinput
        if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
        $snapshotCount = & $runtimePython manage.py shell -c 'from server.research.models import Snapshot; print(Snapshot.objects.count())' --no-imports
        if ($LASTEXITCODE -ne 0) { throw 'Could not read the snapshot database.' }
        if ([int]$snapshotCount -eq 0) {
            & $runtimePython manage.py import_summary data/co-cd005-b0edb0f04d6c.zip
            if ($LASTEXITCODE -ne 0) { throw 'Summary import failed.' }
        }
        $managePath = Join-Path $appRoot 'manage.py'
        $server = Start-Process -FilePath $runtimePython -ArgumentList @(('"' + $managePath + '"'), 'serve') -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $localDir 'server.log') -RedirectStandardError (Join-Path $localDir 'server-error.log') -PassThru
        $server.Id | Set-Content -LiteralPath (Join-Path $localDir 'server.pid')
        $ready = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            if (Test-AppReady) { $ready = $true; break }
            $server.Refresh()
            if ($server.HasExited) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'The service did not become ready. See .local\server-error.log.' }
    }
    if (-not $NoBrowser) {
        $appBrowser = $null
        if ($AppWindow) {
            $browserCandidates = @((Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'), (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'))
            $appBrowser = $browserCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        }
        if ($appBrowser) { Start-Process -FilePath $appBrowser -ArgumentList @(("--app="+$origin),'--new-window') }
        else { Start-Process $origin }
    }
    Write-Host "Credit Union Geography is ready at $origin"
} catch {
    $message = $_.Exception.Message + [Environment]::NewLine + [Environment]::NewLine + 'Details: ' + (Join-Path $localDir 'launcher.log')
    if ($Quiet) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($message, 'Credit Union Geography', 'OK', 'Error') | Out-Null
    }
    Write-Error $message -ErrorAction Continue
    exit 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    if ($ownsLock) { $launcherLock.ReleaseMutex() }
    if ($launcherLock) { $launcherLock.Dispose() }
}
