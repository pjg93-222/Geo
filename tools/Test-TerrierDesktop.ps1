param([int]$TimeoutSeconds = 30)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$terrierRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$terrierOrigin = 'http://127.0.0.1:8767'
$terrierLauncher = Join-Path $terrierRoot 'Start-Terrier.ps1'
$terrierStop = Join-Path $terrierRoot 'Stop-test-demo.ps1'
$terrierRecord = Join-Path $terrierRoot '_build\test-demo-pid.json'
$terrierServer = Join-Path $terrierRoot 'frontend\scripts\test-demo-server.mjs'
$terrierReportDir = Join-Path $terrierRoot '_build\desktop-verification'
$terrierPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$terrierChecks = [Collections.Generic.List[object]]::new()
$terrierStub = $null
$terrierStubStarted = $null
$terrierStubPath = Join-Path $terrierReportDir ('port-stub-' + [guid]::NewGuid().ToString('N') + '.mjs')
$terrierShouldRestore = $false
$terrierFailure = $null
$terrierRunId = [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $terrierReportDir -Force | Out-Null

function Add-Check([string]$Name, [bool]$Passed, $Details) {
    $terrierChecks.Add([pscustomobject]@{name=$Name; passed=$Passed; details=$Details})
    if (-not $Passed) { throw ('Desktop verification failed: ' + $Name) }
    Write-Output ('PASS: ' + $Name)
}

function Get-Listener {
    @(Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue)
}

function Read-Health {
    try { Invoke-RestMethod -Uri ($terrierOrigin + '/_terrier/health') -TimeoutSec 3 }
    catch { return $null }
}

function Test-Health($Health) {
    return $null -ne $Health -and $Health.app -eq 'Terrier' -and $Health.mode -eq 'synthetic' -and $Health.version -eq 1
}

function Get-OwnedServer {
    if (-not (Test-Path -LiteralPath $terrierRecord)) { return $null }
    $saved = Get-Content -LiteralPath $terrierRecord -Raw | ConvertFrom-Json
    if ([IO.Path]::GetFullPath([string]$saved.script) -ne $terrierServer) { return $null }
    $running = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$saved.pid) -ErrorAction SilentlyContinue
    if (-not $running -or $running.Name -ne 'node.exe' -or -not $running.CommandLine.Contains($terrierServer)) { return $null }
    $difference = [math]::Abs((([datetime]$running.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds)
    if ($difference -ge 1) { return $null }
    return $running
}

function Invoke-HiddenScript([string]$Script, [string[]]$ExtraArguments, [string]$Label) {
    $outPath = Join-Path $terrierReportDir ($terrierRunId + '-' + $Label + '.out.log')
    $errPath = Join-Path $terrierReportDir ($terrierRunId + '-' + $Label + '.err.log')
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $Script + '"')) + $ExtraArguments
    $child = Start-Process -FilePath $terrierPowerShell -ArgumentList $arguments -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $outPath -RedirectStandardError $errPath
    # Windows PowerShell 5.1 needs a retained process handle to read ExitCode after exit.
    [void]$child.Handle
    if (-not $child.WaitForExit($TimeoutSeconds * 1000)) {
        # This handle belongs to the child created immediately above, never an unrelated port owner.
        $child.Kill()
        $child.WaitForExit()
        throw ('Timed out running ' + $Label + '; see ' + $errPath)
    }
    $exitCode = $child.ExitCode
    if ($null -eq $exitCode) { throw ('No exit code was available for ' + $Label) }
    return [pscustomobject]@{exitCode=[int]$exitCode; stdout=$outPath; stderr=$errPath}
}

function Wait-PortFree {
    $deadline = [datetime]::UtcNow.AddSeconds(8)
    while ((@(Get-Listener)).Count -gt 0 -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    return (@(Get-Listener)).Count -eq 0
}

function Stop-OwnedStub {
    if ($null -eq $terrierStub) { return $false }
    $running = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $terrierStub.Id) -ErrorAction SilentlyContinue
    if ($running -and $running.Name -eq 'node.exe' -and $running.CommandLine.Contains($terrierStubPath) -and [math]::Abs((([datetime]$running.CreationDate).ToUniversalTime() - $terrierStubStarted).TotalSeconds) -lt 1) {
        Stop-Process -Id $terrierStub.Id
        return $true
    }
    return $false
}

try {
    Add-Check 'launcher-files-exist' ((Test-Path -LiteralPath $terrierLauncher) -and (Test-Path -LiteralPath $terrierStop)) $terrierRoot

    $iconPath = Join-Path $terrierRoot 'assets\terrier.ico'
    $iconBytes = [IO.File]::ReadAllBytes($iconPath)
    Add-Check 'icon-header' ($iconBytes.Length -ge 6 -and [BitConverter]::ToUInt16($iconBytes,0) -eq 0 -and [BitConverter]::ToUInt16($iconBytes,2) -eq 1) $iconPath
    $entryCount = [BitConverter]::ToUInt16($iconBytes,4)
    Add-Check 'icon-directory' ($entryCount -gt 0 -and $iconBytes.Length -ge (6 + 16 * $entryCount)) @{entries=$entryCount; bytes=$iconBytes.Length}
    $iconSizes = @()
    for ($entry = 0; $entry -lt $entryCount; $entry++) {
        $offset = 6 + 16 * $entry
        $width = [int]$iconBytes[$offset]; if ($width -eq 0) { $width = 256 }
        $height = [int]$iconBytes[$offset+1]; if ($height -eq 0) { $height = 256 }
        $imageBytes = [BitConverter]::ToUInt32($iconBytes,$offset+8)
        $imageOffset = [BitConverter]::ToUInt32($iconBytes,$offset+12)
        Add-Check ('icon-entry-' + $entry) ($width -eq $height -and $imageBytes -gt 0 -and $imageOffset -ge (6 + 16 * $entryCount) -and ([long]$imageOffset + $imageBytes) -le $iconBytes.Length) @{width=$width; height=$height; bytes=$imageBytes}
        $iconSizes += $width
    }
    $requiredSizes = @(16,32,48,64,128,256)
    Add-Check 'icon-resolution-set' ((@($requiredSizes | Where-Object { $_ -notin $iconSizes })).Count -eq 0) @{expected=$requiredSizes; actual=$iconSizes}

    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktop 'Terrier.lnk'
    Add-Check 'desktop-shortcut-named-Terrier' (Test-Path -LiteralPath $shortcutPath) $shortcutPath
    $shortcutShell = New-Object -ComObject WScript.Shell
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    $launchArgument = '-File\s+"' + [regex]::Escape($terrierLauncher) + '"(?:\s|$)'
    Add-Check 'shortcut-target' ([IO.Path]::GetFullPath($shortcut.TargetPath) -eq $terrierPowerShell -and $shortcut.Arguments -match $launchArgument -and $shortcut.WorkingDirectory -eq $terrierRoot) @{target=$shortcut.TargetPath; arguments=$shortcut.Arguments; workingDirectory=$shortcut.WorkingDirectory}
    Add-Check 'shortcut-icon' ($shortcut.IconLocation -eq ($iconPath + ',0')) $shortcut.IconLocation
    Add-Check 'old-desktop-shortcut-retired' (-not (Test-Path -LiteralPath (Join-Path $desktop 'Credit Union Geography.lnk'))) $desktop

    $initialListeners = @(Get-Listener)
    $initialOwned = Get-OwnedServer
    Add-Check 'initial-port-ownership' ($initialListeners.Count -eq 0 -or ($null -ne $initialOwned -and (@($initialListeners | Where-Object {$_.OwningProcess -ne $initialOwned.ProcessId})).Count -eq 0)) @{listeners=@($initialListeners | Select-Object LocalAddress,OwningProcess); ownedProcess=if($initialOwned){$initialOwned.ProcessId}else{$null}}
    $terrierShouldRestore = $true
    $stopped = Invoke-HiddenScript $terrierStop @() 'stop-before-cold'
    Add-Check 'cold-start-port-free' ($stopped.exitCode -eq 0 -and (Wait-PortFree)) $stopped
    $cold = Invoke-HiddenScript $terrierLauncher @('-NoBrowser') 'cold-start'
    Add-Check 'cold-launch-success' ($cold.exitCode -eq 0) $cold
    $coldOwned = Get-OwnedServer
    $coldHealth = Read-Health
    Add-Check 'cold-health-and-process' ($null -ne $coldOwned -and (Test-Health $coldHealth) -and (@(Get-Listener | Where-Object {$_.OwningProcess -ne $coldOwned.ProcessId})).Count -eq 0) @{health=$coldHealth; pid=if($coldOwned){$coldOwned.ProcessId}else{$null}}
    $rootResponse = Invoke-WebRequest -Uri ($terrierOrigin + '/') -UseBasicParsing -TimeoutSec 5
    Add-Check 'static-page-title-exactly-Terrier' ($rootResponse.StatusCode -eq 200 -and $rootResponse.Content -match '<title>Terrier</title>') @{status=$rootResponse.StatusCode; title=([regex]::Match($rootResponse.Content,'<title>(.*?)</title>')).Groups[1].Value}

    $warm = Invoke-HiddenScript $terrierLauncher @('-NoBrowser') 'warm-start'
    $warmOwned = Get-OwnedServer
    Add-Check 'warm-launch-reuses-process' ($warm.exitCode -eq 0 -and $null -ne $warmOwned -and $warmOwned.ProcessId -eq $coldOwned.ProcessId) @{launch=$warm; coldPid=$coldOwned.ProcessId; warmPid=if($warmOwned){$warmOwned.ProcessId}else{$null}}
    $stopped = Invoke-HiddenScript $terrierStop @() 'stop-before-conflict'
    Add-Check 'conflict-test-port-free' ($stopped.exitCode -eq 0 -and (Wait-PortFree)) $stopped

    # The stub is deliberately different from Terrier and must survive a refused launch.
    "import {createServer} from 'node:http'; const server=createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({app:'Terrier desktop verification stub'}));});server.listen(8767,'127.0.0.1');server.on('error',e=>{console.error(e.message);process.exit(1);});" | Set-Content -LiteralPath $terrierStubPath -Encoding UTF8
    $nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
    $terrierStub = Start-Process -FilePath $nodeExe -ArgumentList @(('"' + $terrierStubPath + '"')) -WorkingDirectory $terrierRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $terrierReportDir ($terrierRunId + '-stub.out.log')) -RedirectStandardError (Join-Path $terrierReportDir ($terrierRunId + '-stub.err.log'))
    $terrierStubStarted = $terrierStub.StartTime.ToUniversalTime()
    $deadline = [datetime]::UtcNow.AddSeconds(8)
    do {
        $stubListeners = @(Get-Listener)
        if ($stubListeners.Count -gt 0) { break }
        $terrierStub.Refresh()
        if ($terrierStub.HasExited) { break }
        Start-Sleep -Milliseconds 150
    } while ([datetime]::UtcNow -lt $deadline)
    Add-Check 'owned-conflict-stub-ready' ($stubListeners.Count -gt 0 -and (@($stubListeners | Where-Object {$_.OwningProcess -ne $terrierStub.Id})).Count -eq 0) @{stubPid=$terrierStub.Id; listeners=@($stubListeners | Select-Object OwningProcess)}
    $conflict = Invoke-HiddenScript $terrierLauncher @('-NoBrowser') 'port-conflict'
    $terrierStub.Refresh()
    $afterConflict = @(Get-Listener)
    Add-Check 'unknown-port-refused' ($conflict.exitCode -eq 1) $conflict
    Add-Check 'unknown-port-owner-preserved' (-not $terrierStub.HasExited -and $afterConflict.Count -gt 0 -and (@($afterConflict | Where-Object {$_.OwningProcess -ne $terrierStub.Id})).Count -eq 0) @{stubPid=$terrierStub.Id; listeners=@($afterConflict | Select-Object OwningProcess)}
}
catch {
    $terrierFailure = $_.Exception.Message
    Write-Error $terrierFailure -ErrorAction Continue
}
finally {
    try {
        if (Stop-OwnedStub) { [void](Wait-PortFree) }
        if (Test-Path -LiteralPath $terrierStubPath) { Remove-Item -LiteralPath $terrierStubPath }
        if ($terrierShouldRestore) {
            $restoreListeners = @(Get-Listener)
            if ($restoreListeners.Count -eq 0 -or ($null -ne (Get-OwnedServer))) {
                $restored = Invoke-HiddenScript $terrierLauncher @('-NoBrowser') 'restore'
                Add-Check 'Terrier-restored' ($restored.exitCode -eq 0 -and (Test-Health (Read-Health)) -and $null -ne (Get-OwnedServer)) $restored
            } else {
                Add-Check 'Terrier-restored' $false 'Port8767 has another owner; left it untouched.'
            }
        }
    }
    catch {
        if (-not $terrierFailure) { $terrierFailure = $_.Exception.Message }
        Write-Error $_.Exception.Message -ErrorAction Continue
    }
    [pscustomobject]@{completedAt=[datetime]::UtcNow.ToString('o'); root=$terrierRoot; passed=($null -eq $terrierFailure); failure=$terrierFailure; checks=@($terrierChecks.ToArray())} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $terrierReportDir 'checks.json') -Encoding UTF8
}
if ($terrierFailure) { exit 1 }
Write-Output ('Desktop verification passed. Report: ' + (Join-Path $terrierReportDir 'checks.json'))
