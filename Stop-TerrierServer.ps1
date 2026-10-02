$ErrorActionPreference = 'Stop'
$record = Join-Path $PSScriptRoot '_build\terrier-server-pid.json'
$managePath = Join-Path $PSScriptRoot 'manage.py'
if (!(Test-Path -LiteralPath $record)) { Write-Output 'No Terrier process record.'; exit }
$saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($saved.script -eq $managePath -and $owned -and $owned.Name -eq 'python.exe' -and $owned.CommandLine.Contains($managePath) -and $owned.CommandLine.Contains('--port 8767') -and [math]::Abs((([datetime]$owned.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1) {
    Stop-Process -Id $saved.pid
    Wait-Process -Id $saved.pid -Timeout 10 -ErrorAction SilentlyContinue
    Write-Output 'Terrier server stopped.'
} else { Write-Output 'Recorded process is absent or identity changed; nothing stopped.' }
