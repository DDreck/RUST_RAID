# C:\Scripts\Take-Screenshot.ps1
# Run in the active desktop session via Take-Screenshot-Interactive.ps1 (PsExec).
# Uses PrintScreen + Clipboard to capture all monitors, then crops to middle monitor.
$ErrorActionPreference = "Stop"
$outPath = "C:\Temp\rust_ss.png"

if (-not (Test-Path "C:\Temp")) { New-Item -ItemType Directory -Path "C:\Temp" | Out-Null }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Send PrintScreen — captures all monitors into clipboard
[System.Windows.Forms.SendKeys]::SendWait('{PRTSC}')
Start-Sleep -Milliseconds 500

$img = [System.Windows.Forms.Clipboard]::GetImage()
if (-not $img) { throw "Clipboard contained no image after PrintScreen." }

# Virtual screen origin (PrintScreen bitmap starts at virtual screen top-left)
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$vx = $vs.X
$vy = $vs.Y

# Middle monitor (index 1 sorted left-to-right by X)
$screens = [System.Windows.Forms.Screen]::AllScreens | Sort-Object { $_.Bounds.X }
$screen  = if ($screens.Count -ge 2) { $screens[1] } else { $screens[0] }
$b       = $screen.Bounds

# Crop to middle monitor (source rect offset by virtual screen origin)
$srcRect  = New-Object System.Drawing.Rectangle(($b.X - $vx), ($b.Y - $vy), $b.Width, $b.Height)
$dstRect  = New-Object System.Drawing.Rectangle(0, 0, $b.Width, $b.Height)
$cropped  = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
$gc       = [System.Drawing.Graphics]::FromImage($cropped)
$gc.DrawImage($img, $dstRect, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
$gc.Dispose()
$img.Dispose()

# Resize to max 1280 wide
$maxW  = 1280
$scale = [Math]::Min(1.0, $maxW / $b.Width)
$newW  = [int]($b.Width  * $scale)
$newH  = [int]($b.Height * $scale)

$scaled = New-Object System.Drawing.Bitmap($newW, $newH)
$gs     = [System.Drawing.Graphics]::FromImage($scaled)
$gs.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$gs.DrawImage($cropped, 0, 0, $newW, $newH)
$gs.Dispose()
$cropped.Dispose()

$scaled.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$scaled.Dispose()
exit 0
