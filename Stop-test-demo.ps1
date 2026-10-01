$ErrorActionPreference = 'Stop'
$record = Join-Path $PSScriptRoot '_build\test-demo-pid.json'
if (!(Test-Path -LiteralPath $record)) { Write-Output 'No test-demo process record.'; exit }
$saved = Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($saved.pid)"
if ($process -and $process.Name -eq 'node.exe' -and $process.CommandLine.Contains($saved.script) -and [math]::Abs((([datetime]$process.CreationDate).ToUniversalTime() - ([datetime]$saved.started).ToUniversalTime()).TotalSeconds) -lt 1) {
  Stop-Process -Id $saved.pid
  Write-Output 'Test-dummy preview stopped.'
} else { Write-Output 'Recorded process is absent or identity changed; nothing stopped.' }
