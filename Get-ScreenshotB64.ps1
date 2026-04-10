# C:\Scripts\Get-ScreenshotB64.ps1
# Reads C:\Temp\rust_ss.png and outputs it as base64 to stdout.
# Run after Take-Screenshot-Interactive.ps1 has saved the file.
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::ASCII
[Convert]::ToBase64String([System.IO.File]::ReadAllBytes("C:\Temp\rust_ss.png"))
