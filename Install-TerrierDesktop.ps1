param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$terrierRoot = $PSScriptRoot
if (!$SkipBuild) {
    & npm.cmd --prefix (Join-Path $terrierRoot 'frontend') run build
    if ($LASTEXITCODE -ne 0) { throw 'Terrier build failed.' }
    & node.exe (Join-Path $terrierRoot 'frontend\scripts\compress-demo.mjs')
    if ($LASTEXITCODE -ne 0) { throw 'Terrier build compression failed.' }
}
if (!(Test-Path -LiteralPath (Join-Path $terrierRoot 'frontend\dist\index.html'))) { throw 'Build Terrier before installing its shortcut.' }

# Convert the lossless approved logo into native Windows icon sizes.
Add-Type -AssemblyName System.Drawing
$assetDirectory = Join-Path $terrierRoot 'assets'
New-Item -ItemType Directory -Path $assetDirectory -Force | Out-Null
$iconPath = Join-Path $assetDirectory 'terrier.ico'
$source = [System.Drawing.Image]::FromFile((Join-Path $terrierRoot 'frontend\public\brand\terrier-icon-hq.png'))
$sizes = @(16,24,32,48,64,128,256)
$images = [System.Collections.Generic.List[byte[]]]::new()
try {
    foreach ($size in $sizes) {
        $bitmap = [System.Drawing.Bitmap]::new($size,$size)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $memory = [System.IO.MemoryStream]::new()
        try {
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($source,0,0,$size,$size)
            $bitmap.Save($memory,[System.Drawing.Imaging.ImageFormat]::Png)
            $images.Add($memory.ToArray())
        } finally { $memory.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
    }
} finally { $source.Dispose() }
$stream = [System.IO.File]::Create($iconPath)
$writer = [System.IO.BinaryWriter]::new($stream)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16*$sizes.Count
    for ($i=0; $i -lt $sizes.Count; $i++) {
        $dimension = if ($sizes[$i] -eq 256) { 0 } else { $sizes[$i] }
        $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
        $writer.Write([byte]0); $writer.Write([byte]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length); $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($bytes in $images) { $writer.Write([byte[]]$bytes) }
} finally { $writer.Dispose(); $stream.Dispose() }

$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shortcutPath = Join-Path $desktop 'Terrier.lnk'
$legacyPath = Join-Path $desktop 'Credit Union Geography.lnk'
$backupDirectory = Join-Path $terrierRoot ('_build\desktop-backup\'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
if (Test-Path -LiteralPath $shortcutPath) { Copy-Item -LiteralPath $shortcutPath -Destination (Join-Path $backupDirectory 'Terrier.lnk') }
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $terrierRoot 'Start-Terrier.ps1')+'" -Quiet'
$shortcut.WorkingDirectory = $terrierRoot
$shortcut.IconLocation = $iconPath+',0'
$shortcut.Description = 'Terrier'
$shortcut.WindowStyle = 7
$shortcut.Save()
if (Test-Path -LiteralPath $legacyPath) {
    # Archive only the known predecessor shortcut, never another application's link.
    $legacy = $shortcutShell.CreateShortcut($legacyPath)
    if ($legacy.Arguments -match '[\\/]cu-geography[\\/]Start\.ps1' -and $legacy.Description -eq 'Open the local map, source records and contact registry.') {
        $resolvedSource = [System.IO.Path]::GetFullPath($legacyPath)
        $resolvedDestination = [System.IO.Path]::GetFullPath((Join-Path $backupDirectory 'Credit Union Geography.lnk'))
        if ($resolvedSource -ne [System.IO.Path]::GetFullPath((Join-Path $desktop 'Credit Union Geography.lnk')) -or !$resolvedDestination.StartsWith([System.IO.Path]::GetFullPath($terrierRoot)+[System.IO.Path]::DirectorySeparatorChar)) { throw 'Shortcut backup path validation failed.' }
        Move-Item -LiteralPath $resolvedSource -Destination $resolvedDestination
    }
}
Write-Output "Installed Terrier: $shortcutPath"
Write-Output "Previous shortcuts are backed up in $backupDirectory"
