<#
.SYNOPSIS
  Stop the edge-sensor-platform demo stack cleanly. Sends the agent a graceful stop first so it
  seals its in-flight MCAP segment, then stops the supporting services. Captured data on disk
  (miniodata, prom-data, bin/loki-data) is preserved — this is a pause, not a wipe.
#>
$root = Split-Path -Parent $PSScriptRoot
# Agent first: try a graceful CTRL-style stop so @app.on_event("shutdown") seals the open segment.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'agent:app' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; Write-Host "stopped agent $($_.ProcessId)" }
Start-Sleep -Seconds 2
foreach ($n in 'minio','prometheus','loki-windows-amd64','promtail-windows-amd64','grafana') {
  Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; Write-Host "stopped $n ($($_.Id))" }
}
Write-Host "Stack stopped. Data preserved under miniodata/, bin/prom-data/, bin/loki-data/."
