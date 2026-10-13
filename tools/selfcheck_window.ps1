
# Capture the running FormPilot main window (PrintWindow works even if occluded).
# Usage: powershell -NoProfile -File tools/selfcheck_window.ps1 [out.png]
param([string]$Out = "D:\Code\FormPilot\data\selfcheck\10_live_main.png")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public struct RECT { public int Left, Top, Right, Bottom; }
public class WinCap {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
}
"@
$p = Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "FormPilot*" } | Select-Object -First 1
if (-not $p) { Write-Output "no FormPilot window found"; exit 1 }
$rect = New-Object RECT
[WinCap]::GetWindowRect($p.MainWindowHandle, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[WinCap]::PrintWindow($p.MainWindowHandle, $hdc, 2) | Out-Null
$g.ReleaseHdc($hdc); $g.Dispose()
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
Write-Output ("saved: " + $Out + "  " + $w + "x" + $h)
