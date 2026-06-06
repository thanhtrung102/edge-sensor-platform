<#
.SYNOPSIS
  Bring up the full edge-sensor-platform demo stack natively (no Docker) on Windows and wait
  until all six services are healthy. Run from anywhere; paths are resolved from the repo root.

  Services: MinIO (:9000/:9001) · Edge agent (:8000) · Prometheus (:9090) · Loki (:3100)
            · Promtail (:9080) · Grafana (:3000)

  Usage:  powershell -ExecutionPolicy Bypass -File scripts\demo-up.ps1
  Stop:   powershell -ExecutionPolicy Bypass -File scripts\demo-down.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path logs, miniodata, bin\loki-data | Out-Null

$py = ".\.venv\Scripts\python.exe"
$env:S3_ENDPOINT = "http://127.0.0.1:9000"; $env:S3_BUCKET = "edge-data"
$env:S3_ACCESS_KEY = "minioadmin"; $env:S3_SECRET_KEY = "minioadmin"
$env:DEVICE_ID = "edge-001"; $env:SITE = "hanoi-lab"; $env:CAPTURE_FPS = "2"
$env:BUFFER_DIR = "./buf"; $env:RECORDING_FORMAT = "mcap"; $env:SEGMENT_SECONDS = "60"
$env:CAMERA_SOURCE = "synthetic"
$env:MINIO_ROOT_USER = "minioadmin"; $env:MINIO_ROOT_PASSWORD = "minioadmin"

function Start-Svc($name, $exe, $argline) {
  Start-Process -FilePath $exe -ArgumentList $argline -WindowStyle Hidden `
    -RedirectStandardOutput "logs\$name.log" -RedirectStandardError "logs\$name.err.log"
  Write-Host "  started $name"
}
function Wait-Http($name, $url, $tries = 30) {
  for ($i = 0; $i -lt $tries; $i++) {
    try { if ((Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) { Write-Host "  OK   $name"; return } } catch {}
    Start-Sleep -Seconds 2
  }
  Write-Host "  WARN $name not healthy after $($tries*2)s (check logs\$name.err.log)"
}

$prom = (Get-ChildItem bin\prom\prometheus-*\prometheus.exe | Select-Object -First 1).FullName
$graf = (Get-ChildItem bin\grafana\grafana-* -Directory | Select-Object -First 1).FullName

Write-Host "Starting stack..."
Start-Svc "minio"      "bin\minio.exe"                "server miniodata --address :9000 --console-address :9001"
Wait-Http "minio"      "http://localhost:9000/minio/health/live"
& $py -c "import boto3;from botocore.config import Config;c=boto3.client('s3',endpoint_url='http://127.0.0.1:9000',aws_access_key_id='minioadmin',aws_secret_access_key='minioadmin',region_name='us-east-1',config=Config(retries={'max_attempts':1}));import botocore;[c.create_bucket(Bucket='edge-data') if 'edge-data' not in [b['Name'] for b in c.list_buckets()['Buckets']] else None];print('  bucket edge-data ready')"

Start-Svc "prometheus" $prom                          "--config.file=observability\prometheus.local.yml --storage.tsdb.path=bin\prom-data"
Start-Svc "loki"       "bin\loki-windows-amd64.exe"   "-config.file=observability\loki.local.yml"
Wait-Http "loki"       "http://localhost:3100/ready"
Start-Svc "promtail"   "bin\promtail-windows-amd64.exe" "-config.file=observability\promtail.local.yml"

$env:GF_PATHS_PROVISIONING = "$root\observability\grafana\provisioning-local"
$env:GF_SECURITY_ADMIN_PASSWORD = "admin"
Start-Svc "grafana"    "$graf\bin\grafana.exe"        "server --homepath `"$graf`""

# Agent last (needs MinIO); stdout -> logs\agent.log so Promtail tails it.
Start-Process -FilePath $py -ArgumentList "-u -m uvicorn agent:app --app-dir agent --host 127.0.0.1 --port 8000" `
  -WindowStyle Hidden -RedirectStandardOutput "logs\agent.log" -RedirectStandardError "logs\agent.err.log"
Write-Host "  started agent"

Wait-Http "agent"      "http://localhost:8000/healthz"
Wait-Http "prometheus" "http://localhost:9090/-/healthy"
Wait-Http "grafana"    "http://localhost:3000/api/health"

Write-Host ""
Write-Host "Demo stack up. Open:"
Write-Host "  Grafana    http://localhost:3000   (admin/admin) - Edge Fleet dashboard"
Write-Host "  Agent      http://localhost:8000/healthz | /metrics"
Write-Host "  MinIO      http://localhost:9001   (minioadmin/minioadmin)"
Write-Host "  Prometheus http://localhost:9090/alerts"
Write-Host "Run analytics:  cd pipeline; ..\.venv\Scripts\python extract.py; ..\.venv\Scripts\dbt build --profiles-dir ."
