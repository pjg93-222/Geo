$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.local\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No launcher process is recorded.'; exit }
$serverProcessId = [int](Get-Content -LiteralPath $pidFile)
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
if ($processInfo -and $processInfo.CommandLine.Contains((Join-Path $PSScriptRoot 'manage.py')) -and $processInfo.CommandLine -match 'manage.py.+serve' -and $processInfo.Name -match '^python') {
    Stop-Process -Id $serverProcessId
    Remove-Item -LiteralPath $pidFile
    Write-Host 'Credit Union Geography stopped.'
} else { Write-Host 'The recorded process is no longer the application server; nothing was stopped.' }
