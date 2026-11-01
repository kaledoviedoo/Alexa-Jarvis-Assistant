# ============================================================================
#  p.ps1  -  Mandale una orden a Jarvis sin pasar por Alexa.
#
#     .\p "que ventanas tengo abiertas"
#     .\p "como quedo lo ultimo"
#     .\p -Log "abre spotify"      # ademas, que herramientas uso
#
#  Lee la llave del .env cada vez, asi que funciona en cualquier consola
#  abierta en la carpeta de Jarvis, normal o de administrador.
# ============================================================================
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Comando,
    [switch]$Log
)

$Raiz = $PSScriptRoot
$linea = Select-String -Path (Join-Path $Raiz ".env") -Pattern '^JARVIS_TOKEN_LOCAL=' | Select-Object -First 1
if (-not $linea) {
    Write-Host "No encuentro JARVIS_TOKEN_LOCAL en el .env." -ForegroundColor Red
    exit 1
}
$token  = $linea.Line.Split('=', 2)[1].Trim().Trim('"').Trim("'")
$puerto = 8000
$lineaPuerto = Select-String -Path (Join-Path $Raiz ".env") -Pattern '^JARVIS_PUERTO=' | Select-Object -First 1
if ($lineaPuerto) { $puerto = $lineaPuerto.Line.Split('=', 2)[1].Trim() }

$cuerpo = [System.Text.Encoding]::UTF8.GetBytes((@{ comando = $Comando } | ConvertTo-Json))
try {
    $r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$puerto/probar" `
         -Headers @{ "X-Jarvis-Token" = $token } `
         -ContentType 'application/json; charset=utf-8' -Body $cuerpo -TimeoutSec 60
} catch {
    Write-Host "Jarvis no respondio: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Si acabas de reiniciarlo, espera unos segundos y repite." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "  $($r.respuesta)"
Write-Host ""
Write-Host "  ($($r.resuelto_por), $($r.milisegundos) ms)" -ForegroundColor DarkGray

if ($Log) {
    $archivo = Join-Path $env:USERPROFILE ".jarvis\jarvis.log"
    Write-Host ""
    Select-String -Path $archivo -Pattern "Ofrezco|Ejecutando herramienta|FRENO|Pregunta respondida" |
        Select-Object -Last 4 | ForEach-Object { Write-Host "  $($_.Line)" -ForegroundColor DarkCyan }
}
