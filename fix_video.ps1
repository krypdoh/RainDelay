# Fix rain_native.mp4 performance issue
# This script creates an optimized native-resolution video

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "RainDelay Video Optimizer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

cd "$PSScriptRoot\assets"

# Check if rain_native.mp4 exists
if (Test-Path "rain_native.mp4") {
    Write-Host "Found rain_native.mp4 (this is causing poor performance)" -ForegroundColor Yellow
    Write-Host "Renaming to rain_native_original.mp4.bak..." -ForegroundColor Yellow
    Move-Item "rain_native.mp4" "rain_native_original.mp4.bak" -Force
    Write-Host "✓ Renamed" -ForegroundColor Green
    Write-Host ""
}

# Check for ffmpeg
Write-Host "Checking for ffmpeg..." -ForegroundColor Yellow
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Host "✗ ffmpeg not found in PATH" -ForegroundColor Red
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Yellow
    Write-Host "  1. Install ffmpeg: winget install ffmpeg" -ForegroundColor White
    Write-Host "  2. Or just use rain.mp4 (app will auto-select it now)" -ForegroundColor White
    Write-Host ""
    Write-Host "App will now use rain.mp4 which should work smoothly!" -ForegroundColor Green
    pause
    exit 0
}

Write-Host "✓ ffmpeg found" -ForegroundColor Green
Write-Host ""

# Select source video
$source = $null
if (Test-Path "rain_native_original.mp4.bak") {
    $source = "rain_native_original.mp4.bak"
} elseif (Test-Path "rain.mp4") {
    $source = "rain.mp4"
} else {
    Write-Host "✗ No source video found!" -ForegroundColor Red
    exit 1
}

Write-Host "Creating optimized rain_native.mp4 from $source..." -ForegroundColor Yellow
Write-Host "Target: 2560x1600, 30fps, ~600kbps (smooth playback)" -ForegroundColor Cyan
Write-Host ""

# Create optimized version with SQUARE PIXELS (SAR 1:1)
$ffmpegArgs = @(
    "-i", $source,
    "-vf", "scale=2560:1600:flags=bicubic,setsar=1:1",  # Force square pixels!
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "28",
    "-b:v", "600k",
    "-maxrate", "800k",
    "-bufsize", "1600k",
    "-pix_fmt", "yuv420p",
    "-r", "30",
    "-an",  # No audio (overlay has separate audio)
    "-y",   # Overwrite
    "rain_native.mp4"
)

Write-Host "Running: ffmpeg $($ffmpegArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ""

& ffmpeg @ffmpegArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "✓ SUCCESS!" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Optimized video created:" -ForegroundColor Green
    $file = Get-Item "rain_native.mp4"
    Write-Host "  Size: $([math]::Round($file.Length / 1MB, 2)) MB" -ForegroundColor White
    Write-Host "  Resolution: 2560x1600" -ForegroundColor White
    Write-Host "  Bitrate: ~600 Kbps (optimized for smooth playback)" -ForegroundColor White
    Write-Host ""
    Write-Host "This should now run smoothly on your laptop!" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "✗ ffmpeg encoding failed" -ForegroundColor Red
    Write-Host "App will use rain.mp4 instead" -ForegroundColor Yellow
}

Write-Host ""
pause
