param([switch]$NoBrowser, [switch]$Quiet)
$ErrorActionPreference = 'Stop'
$terrierRoot = $PSScriptRoot
$logDirectory = Join-Path $terrierRoot '_build'
try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    & (Join-Path $terrierRoot 'Start-test-demo.ps1')
    if (!$NoBrowser) {
        $candidates = @(
            (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
            (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
            (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
        )
        $browser = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (!$browser) { throw 'Install Google Chrome or Microsoft Edge to open Terrier.' }
        $browserProfile = Join-Path $env:LOCALAPPDATA 'Terrier\Browser'
        # The interactive app window is visible; its server remains hidden.
        Start-Process -FilePath $browser -ArgumentList @('--app=http://127.0.0.1:8767/', '--new-window', '--no-first-run', '--no-default-browser-check', ('--user-data-dir="' + $browserProfile + '"')) | Out-Null
    }
} catch {
    $message = $_.Exception.Message
    Add-Content -LiteralPath (Join-Path $logDirectory 'terrier-launcher.log') -Value ((Get-Date -Format o) + ' ' + $message)
    if ($Quiet -and !$NoBrowser) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($message, 'Terrier', 'OK', 'Error') | Out-Null
    }
    Write-Error $message -ErrorAction Continue
    exit 1
}
