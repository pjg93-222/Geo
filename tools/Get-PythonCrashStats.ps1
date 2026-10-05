# Read-only report of python.exe crashes: Windows Application log, provider 'Application Error', event Id 1000.
# Needs no elevation and changes nothing (no registry, service, antivirus or dump settings; no files are written).
# Prints the crashes per day, per (python path, exception code, faulting module+offset) with first and last time, the
# count of the dominant signature 0xc000070a at ntdll.dll+0xc87a4 (STATUS_THREADPOOL_HANDLE_EXCEPTION), and whether the
# live Terrier server crashed: the faulting process ids are matched against the pids the supervisor recorded in
# _build\terrier-supervisor.log ("watching server pid N", and the rotated terrier-supervisor.1.log when it exists).
# -Days N (default 14) reports the last N days; -Since <datetime> overrides it. -Json prints one JSON object instead.
param(
    [int]$Days = 14,
    [Nullable[datetime]]$Since = $null,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
$terrierRoot = Split-Path -Parent $PSScriptRoot
$supervisorLog = Join-Path $terrierRoot '_build\terrier-supervisor.log'
$dominantCode = '0xc000070a'
$dominantModule = 'ntdll.dll'
$dominantOffset = '0xc87a4'

$start = if ($null -ne $Since) { [datetime]$Since } else { (Get-Date).AddDays(-$Days) }

function Get-Field([string]$Message, [string]$Label) {
    # The message has one "Label: value" per line; the label text is English, so a missing field reads as unknown. The
    # application and module name lines carry ", version: ..." after the name, which is cut off here.
    $match = [regex]::Match($Message, '(?m)^\s*' + [regex]::Escape($Label) + ':\s*([^\r\n]*?)\s*$')
    if ($match.Success -and $match.Groups[1].Value) { return ($match.Groups[1].Value -split ',\s*version:')[0] }
    return $null
}
function ConvertTo-ShortHex([string]$Text) {
    # 0x00000000000C87A4 -> 0xc87a4; anything that is not hex (or is missing) is returned as it is.
    if ($Text -match '^0x([0-9a-fA-F]+)$') {
        $digits = $Matches[1].TrimStart('0').ToLowerInvariant()
        return '0x' + $(if ($digits) { $digits } else { '0' })
    }
    return $Text
}

$events = @()
try {
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'Application Error'; Id = 1000; StartTime = $start } -ErrorAction Stop)
} catch {
    # "No events were found" is a normal answer; anything else (for example a missing log) is worth showing.
    if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
}

$crashes = foreach ($event in $events) {
    $message = [string]$event.Message
    $application = Get-Field $message 'Faulting application name'
    if ($application -notlike 'python*.exe') { continue }
    $processText = Get-Field $message 'Faulting process id'
    $processId = $null
    if ($processText -match '^0x([0-9a-fA-F]+)$') { $processId = [Convert]::ToInt64($Matches[1], 16) }
    $path = Get-Field $message 'Faulting application path'
    $module = Get-Field $message 'Faulting module name'
    $offset = ConvertTo-ShortHex (Get-Field $message 'Fault offset')
    $code = ConvertTo-ShortHex (Get-Field $message 'Exception code')
    [pscustomobject]@{
        Time = $event.TimeCreated
        Day = $event.TimeCreated.ToString('yyyy-MM-dd')
        Path = if ($path) { $path } else { '(unknown path)' }
        Code = if ($code) { $code } else { '(unknown code)' }
        Module = if ($module) { $module } else { '(unknown module)' }
        Offset = if ($offset) { $offset } else { '(unknown offset)' }
        ProcessId = $processId
    }
}
$crashes = @($crashes)

$perDay = @($crashes | Group-Object Day | Sort-Object Name | ForEach-Object { [pscustomobject]@{ Day = $_.Name; Count = $_.Count } })
$signatures = @($crashes | Group-Object Path, Code, Module, Offset | ForEach-Object {
    $first = $_.Group[0]
    $times = $_.Group | Sort-Object Time
    [pscustomobject]@{
        Path = $first.Path
        Code = $first.Code
        Signature = $first.Module + '+' + $first.Offset
        Count = $_.Count
        First = $times[0].Time
        Last = $times[-1].Time
    }
} | Sort-Object Count -Descending)

$dominant = @($crashes | Where-Object { $_.Code -eq $dominantCode -and $_.Module -ieq $dominantModule -and $_.Offset -eq $dominantOffset })
$dominantShare = if ($crashes.Count) { [math]::Round(100.0 * $dominant.Count / $crashes.Count, 1) } else { $null }

# The live server: every pid the supervisor logged as watched, with the time it began to watch it. A crash counts only if
# it is not older than that line, so a recycled pid number from before the server started is not blamed on it.
$watched = @()
$logFiles = @($supervisorLog, [IO.Path]::ChangeExtension($supervisorLog, '.1.log')) | Where-Object { Test-Path -LiteralPath $_ }
foreach ($file in $logFiles) {
    foreach ($line in (Get-Content -LiteralPath $file)) {
        if ($line -match '^(\S+)\s+supervisor\s+\d+:\s+watching server pid\s+(\d+)\s*$') {
            $watched += [pscustomobject]@{ ProcessId = [int64]$Matches[2]; Since = [DateTimeOffset]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture) }
        }
    }
}
$serverReport = foreach ($server in ($watched | Sort-Object Since)) {
    $hits = @($crashes | Where-Object { $_.ProcessId -eq $server.ProcessId -and [DateTimeOffset]$_.Time -ge $server.Since })
    [pscustomobject]@{
        ServerPid = $server.ProcessId
        WatchedSince = $server.Since.ToString('o')
        Crashed = ($hits.Count -gt 0)
        CrashTimes = @($hits | Sort-Object Time | ForEach-Object { $_.Time.ToString('o') })
    }
}
$serverReport = @($serverReport)
$serverCrashed = @($serverReport | Where-Object Crashed).Count -gt 0

if ($Json) {
    [pscustomobject]@{
        Since = $start.ToString('o')
        Total = $crashes.Count
        PerDay = $perDay
        Signatures = $signatures
        DominantSignature = [pscustomobject]@{ Code = $dominantCode; Signature = $dominantModule + '+' + $dominantOffset; Count = $dominant.Count; SharePercent = $dominantShare }
        LiveServer = [pscustomobject]@{ SupervisorLogFound = ($logFiles.Count -gt 0); WatchedServers = $serverReport; Crashed = $serverCrashed }
    } | ConvertTo-Json -Depth 6
    return
}

Write-Output ('python.exe crashes (Application Error, Id 1000) since ' + $start.ToString('yyyy-MM-dd HH:mm') + ': ' + $crashes.Count)
Write-Output ''
Write-Output 'Per day:'
if ($perDay.Count) { $perDay | Format-Table Day, Count -AutoSize | Out-String -Width 200 | ForEach-Object { $_.Trim() } | Write-Output } else { Write-Output '  none' }
Write-Output 'Per python path, exception code and faulting module+offset:'
if ($signatures.Count) {
    $signatures | Format-Table Count, Code, Signature, @{ Label = 'First'; Expression = { $_.First.ToString('yyyy-MM-dd HH:mm') } }, @{ Label = 'Last'; Expression = { $_.Last.ToString('yyyy-MM-dd HH:mm') } }, Path -AutoSize | Out-String -Width 250 | ForEach-Object { $_.Trim() } | Write-Output
} else { Write-Output '  none' }
$shareText = if ($null -ne $dominantShare) { ' (' + $dominantShare + '% of all)' } else { '' }
Write-Output ('Dominant signature ' + $dominantCode + ' at ' + $dominantModule + '+' + $dominantOffset + ': ' + $dominant.Count + $shareText)
Write-Output ''
if (!$logFiles.Count) { Write-Output 'Live Terrier server: no supervisor log at _build\terrier-supervisor.log, so its pids are unknown.' }
elseif (!$serverReport.Count) { Write-Output 'Live Terrier server: the supervisor log has no "watching server pid" lines.' }
else {
    Write-Output ('Live Terrier server crashed in this window: ' + $(if ($serverCrashed) { 'YES' } else { 'no' }))
    foreach ($server in $serverReport) {
        Write-Output ('  server pid ' + $server.ServerPid + ' (watched since ' + ([DateTimeOffset]::Parse($server.WatchedSince)).ToString('yyyy-MM-dd HH:mm') + '): ' + $(if ($server.Crashed) { 'crashed at ' + ($server.CrashTimes -join ', ') } else { 'no crash event' }))
    }
}
