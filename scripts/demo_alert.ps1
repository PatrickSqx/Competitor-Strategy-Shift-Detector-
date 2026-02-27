param(
  [string]$BaseUrl = "http://localhost:8000"
)

Write-Host "Health check..."
Invoke-RestMethod -Method Get -Uri "$BaseUrl/healthz" | ConvertTo-Json -Depth 6

Write-Host ""
Write-Host "Run 1 (baseline)..."
Invoke-RestMethod -Method Post -Uri "$BaseUrl/run-once" -ContentType "application/json" -Body '{"scenario":"current"}' | ConvertTo-Json -Depth 8

Write-Host ""
Write-Host "Run 2 (shock)..."
Invoke-RestMethod -Method Post -Uri "$BaseUrl/run-once" -ContentType "application/json" -Body '{"scenario":"shock"}' | ConvertTo-Json -Depth 8

Write-Host ""
Write-Host "Run 3 (shock)..."
Invoke-RestMethod -Method Post -Uri "$BaseUrl/run-once" -ContentType "application/json" -Body '{"scenario":"shock"}' | ConvertTo-Json -Depth 8

Write-Host ""
Write-Host "Run 4 (shock, triggers persistence learning)..."
Invoke-RestMethod -Method Post -Uri "$BaseUrl/run-once" -ContentType "application/json" -Body '{"scenario":"shock"}' | ConvertTo-Json -Depth 8

Write-Host ""
Write-Host "Latest signals..."
Invoke-RestMethod -Method Get -Uri "$BaseUrl/signals/latest?limit=10" | ConvertTo-Json -Depth 8
