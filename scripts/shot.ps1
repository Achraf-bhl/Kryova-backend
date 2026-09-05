<#
.SYNOPSIS
    Screenshot the CATIA window — the picture `catia_capture_view` cannot take.

.DESCRIPTION
    CLAUDE.md requires two pictures after any CATIA test, because they fail to show
    different things. `catia_capture_view` renders the *viewport* through the product's
    own tool, which is what shows a pad that went the wrong way. This takes the whole
    application window: the spec tree, a modal dialog, a greyed command, an error box, a
    floating toolbar.

    That second one is not decoration. D11 — a floating toolbar read as an open modal
    dialog, which refused every interactive command on a working seat — was invisible in
    every viewport render and obvious the moment somebody looked at the window.

    Captures with PrintWindow against the window handle rather than grabbing the screen
    region, so a window that is partly covered, or behind the terminal, still comes out
    whole. Nothing is brought to the foreground: the bridge's whole discipline is that an
    engineer working in another application is not interrupted, and a screenshot tool
    that raises CATIA would break it for the sake of a picture.

.PARAMETER Out
    Where to write the PNG. Defaults to a timestamped file in the current directory.

.PARAMETER Process
    Process to capture. Defaults to CNEXT — the CATIA V5 executable, which is *not*
    called CATIA.exe, a mistake that once cost a verification session its Tier 4.

.PARAMETER FullScreen
    Capture the whole desktop instead of one window. Use when what matters is a dialog
    that is not owned by the main frame.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\shot.ps1 -Out docs\run\catia-01.png
#>
[CmdletBinding()]
param(
    [string]$Out = "",
    [string]$Process = "CNEXT",
    [switch]$FullScreen
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

if (-not $Out) {
    $Out = Join-Path (Get-Location) ("shot-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".png")
}
$directory = Split-Path -Parent $Out
if ($directory -and -not (Test-Path $directory)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$signature = @'
using System;
using System.Runtime.InteropServices;
public class ShotNative {
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
'@
if (-not ("ShotNative" -as [type])) { Add-Type -TypeDefinition $signature }

function Save-FullScreen([string]$path) {
    $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
}

if ($FullScreen) {
    Save-FullScreen $Out
    Write-Output "[shot] full screen -> $Out"
    exit 0
}

$target = Get-Process -Name $Process -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1

if (-not $target) {
    # Say which of the two it is. "No screenshot" reads the same for a seat that is
    # not running and a seat that is running headless, and the remedies differ.
    $any = Get-Process -Name $Process -ErrorAction SilentlyContinue
    if ($any) {
        Write-Output "[shot] $Process is running but has no main window; capturing the screen"
    } else {
        Write-Output "[shot] $Process is not running; capturing the screen instead"
    }
    Save-FullScreen $Out
    Write-Output "[shot] full screen -> $Out"
    exit 0
}

$rect = New-Object ShotNative+RECT
[void][ShotNative]::GetWindowRect($target.MainWindowHandle, [ref]$rect)
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top

if ($width -le 0 -or $height -le 0) {
    Write-Output "[shot] $Process window has no extent (minimised?); capturing the screen"
    Save-FullScreen $Out
    Write-Output "[shot] full screen -> $Out"
    exit 0
}

$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$hdc = $graphics.GetHdc()
# nFlags 2 = PW_RENDERFULLCONTENT: without it a hardware-composited viewport comes
# back black, which looks exactly like a part that failed to build.
$ok = [ShotNative]::PrintWindow($target.MainWindowHandle, $hdc, 2)
$graphics.ReleaseHdc($hdc)

if (-not $ok) {
    Write-Output "[shot] PrintWindow refused; falling back to the screen"
    $graphics.Dispose(); $bitmap.Dispose()
    Save-FullScreen $Out
} else {
    $bitmap.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
}

Write-Output "[shot] $Process ($width x $height) -> $Out"
