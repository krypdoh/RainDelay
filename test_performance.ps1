# Performance diagnostic capture script
# Run this on both machines and compare the output

$logFile = "$env:APPDATA\RainDelay\raindelay.log"
$outputFile = "performance_diagnostics_$(Get-Date -Format 'yyyy-MM-dd_HHmmss').txt"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "RainDelay Performance Diagnostics" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Capture system info
Write-Host "Capturing system information..." -ForegroundColor Yellow
$sysInfo = @"
========== MACHINE INFORMATION ==========
Computer Name: $env:COMPUTERNAME
Windows Version: $(Get-WmiObject Win32_OperatingSystem | Select-Object -ExpandProperty Caption)
OS Build: $(Get-WmiObject Win32_OperatingSystem | Select-Object -ExpandProperty BuildNumber)
CPU: $(Get-WmiObject Win32_Processor | Select-Object -ExpandProperty Name)
RAM: $([math]::Round((Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)) GB
GPU: $(Get-WmiObject Win32_VideoController | Select-Object -ExpandProperty Name)
Display: $(Get-WmiObject Win32_VideoController | Select-Object -ExpandProperty CurrentHorizontalResolution) x $(Get-WmiObject Win32_VideoController | Select-Object -ExpandProperty CurrentVerticalResolution)
==========================================

"@

$sysInfo | Out-File $outputFile -Encoding UTF8

# Clear old log
Remove-Item $logFile -ErrorAction SilentlyContinue

Write-Host "Starting RainDelay (will auto-show overlay in 1.5 seconds)..." -ForegroundColor Yellow
Write-Host "Press Ctrl+Alt+R to toggle overlay on/off" -ForegroundColor Green
Write-Host "Testing for 20 seconds..." -ForegroundColor Green
Write-Host ""

# Run the app (it will auto-close after test duration via timers if you added them back)
python main.py

Write-Host ""
Write-Host "Extracting diagnostics from log..." -ForegroundColor Yellow

# Extract relevant log entries
Start-Sleep -Seconds 1
Get-Content $logFile | Select-String -Pattern "DIAG|PERF|Hardware" | Out-File $outputFile -Append -Encoding UTF8

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Diagnostics saved to:" -ForegroundColor Green
Write-Host "  $outputFile" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Send this file for comparison with other machine" -ForegroundColor Yellow
