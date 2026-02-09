# restart.ps1 - Kill and restart the fin web server
# Usage: powershell -File restart.ps1

$Port = 8000
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Fin = Join-Path $ProjectDir ".venv\Scripts\fin.exe"

# Find and kill whatever is on port 8000
$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $connections) {
    $procId = $conn.OwningProcess
    if ($procId -and $procId -ne 0) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "Killing $($proc.ProcessName) (PID $procId) on port $Port"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

# Wait for port to release
$retries = 0
while ($retries -lt 5) {
    $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $still) { break }
    Start-Sleep -Milliseconds 500
    $retries++
}

# Start server: LAN-accessible, no TLS (local network)
Write-Host "Starting fin web --host 0.0.0.0 --no-tls ..."
Start-Process -FilePath $Fin -ArgumentList "web", "--host", "0.0.0.0", "--no-tls" -WorkingDirectory $ProjectDir -WindowStyle Hidden

# Verify
Start-Sleep -Seconds 3
try {
    $r = Invoke-WebRequest -Uri "http://localhost:${Port}/api/categories" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
        Write-Host "OK - http://localhost:${Port}" -ForegroundColor Green
    }
} catch {
    Write-Host "Still starting - try http://localhost:${Port} in a moment" -ForegroundColor Yellow
}
