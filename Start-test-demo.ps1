$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$nodeExe = (Get-Command node.exe).Source
$demoScript = Join-Path $demoRoot 'frontend\scripts\test-demo-server.mjs'
$existing = Get-NetTCPConnection -LocalPort 8767 -State Listen -ErrorAction SilentlyContinue
if ($existing) { Write-Output 'Port 8767 already has a listener; no additional process started.'; exit }
$logDirectory = Join-Path $demoRoot '_build'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$demoProcess = Start-Process -FilePath $nodeExe -ArgumentList @(('"' + $demoScript + '"')) -WorkingDirectory (Join-Path $demoRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $demoRoot '_build\test-demo.out.log') -RedirectStandardError (Join-Path $demoRoot '_build\test-demo.err.log')
@{pid=$demoProcess.Id; script=$demoScript; started=$demoProcess.StartTime.ToUniversalTime().ToString('o'); url='http://127.0.0.1:8767'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $demoRoot '_build\test-demo-pid.json')
Write-Output 'Test-dummy preview starting at http://127.0.0.1:8767'
