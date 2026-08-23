# ============================================================================
#  configurar_tailscale.ps1
#  Cambia el tunel de ngrok a Tailscale Funnel.
#
#     .\configurar_tailscale.ps1
#
#  Por que Tailscale en lugar de ngrok:
#    - No tiene pagina intersticial. Nunca. Amazon recibe JSON limpio.
#    - Instalador firmado por Tailscale Inc: Defender no lo cuestiona.
#    - URL fija gratis, con certificado real de Let's Encrypt.
#    - 'funnel --bg' persiste entre reinicios sin tarea programada.
#    - Sin limite mensual de peticiones.
# ============================================================================

$ErrorActionPreference = "Continue"

$RaizProyecto = Split-Path -Parent $PSScriptRoot
$RutaEnv      = Join-Path $RaizProyecto ".env"
$Puerto       = "8000"

function Titulo {
    param([string]$Texto)
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Cyan
    Write-Host "  $Texto" -ForegroundColor Cyan
    Write-Host "==================================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Escribir-Env {
    param([string]$Clave, [string]$Valor)
    if (-not (Test-Path $RutaEnv)) { New-Item -ItemType File -Path $RutaEnv | Out-Null }

    $lineas = @(Get-Content $RutaEnv -Encoding UTF8)
    $encontrada = $false
    for ($i = 0; $i -lt $lineas.Count; $i++) {
        if ($lineas[$i] -match "^\s*$Clave\s*=") {
            $lineas[$i] = "$Clave=$Valor"; $encontrada = $true; break
        }
    }
    if (-not $encontrada) { $lineas += "$Clave=$Valor" }
    Set-Content -Path $RutaEnv -Value $lineas -Encoding UTF8
    Write-Host "  .env  ->  $Clave=$Valor" -ForegroundColor DarkGray
}

Titulo "CAMBIO A TAILSCALE FUNNEL"

# ---------------------------------------------------------------------------
# 1. Instalacion
# ---------------------------------------------------------------------------
Write-Host "PASO 1 - Tailscale instalado?" -ForegroundColor Cyan

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Write-Host "  No esta instalado." -ForegroundColor Yellow
    Write-Host ""
    $r = Read-Host "  Lo instalo con winget? (s/n)"
    if ($r -eq "s") {
        winget install --id tailscale.tailscale --accept-source-agreements --accept-package-agreements
        Write-Host ""
        Write-Host "  Cierra y reabre PowerShell, y vuelve a ejecutar este script." -ForegroundColor Yellow
    } else {
        Write-Host "  Descargalo de https://tailscale.com/download/windows"
    }
    exit 0
}

Write-Host "  $(& tailscale version 2>&1 | Select-Object -First 1)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Sesion iniciada
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 2 - Sesion" -ForegroundColor Cyan

$estado = & tailscale status 2>&1 | Out-String
if ($estado -match "Logged out|NeedsLogin") {
    Write-Host "  Hay que iniciar sesion. Se abrira el navegador." -ForegroundColor Yellow
    & tailscale up
    Start-Sleep -Seconds 3
} else {
    Write-Host "  Sesion iniciada." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. Activar Funnel
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 3 - Activando Funnel en el puerto $Puerto" -ForegroundColor Cyan
Write-Host ""
Write-Host "  La primera vez, Tailscale abrira el navegador para que autorices" -ForegroundColor DarkGray
Write-Host "  Funnel en tu tailnet. Acepta y vuelve aqui." -ForegroundColor DarkGray
Write-Host ""

# --bg deja la configuracion guardada en el nodo: sobrevive a los reinicios
# sin necesidad de que ningun proceso quede abierto.
& tailscale funnel --bg --https=443 "localhost:$Puerto" 2>&1 | Out-String | Write-Host

Start-Sleep -Seconds 3

# ---------------------------------------------------------------------------
# 4. Leer la URL publica
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 4 - URL publica" -ForegroundColor Cyan

$estadoFunnel = & tailscale funnel status 2>&1 | Out-String
Write-Host $estadoFunnel

$url = ""
if ($estadoFunnel -match "(https://[a-zA-Z0-9\-\.]+\.ts\.net)") {
    $url = $Matches[1].TrimEnd("/")
    Write-Host "  Detectada: $url" -ForegroundColor Green
} else {
    Write-Host "  No pude leerla automaticamente." -ForegroundColor Yellow
    $url = (Read-Host "  Pegala tu (https://algo.algo.ts.net)").Trim().TrimEnd("/")
}

if (-not $url) { Write-Host "Sin URL no puedo continuar." -ForegroundColor Red; exit 1 }

Escribir-Env "JARVIS_TUNEL_PROVIDER" "tailscale"
Escribir-Env "JARVIS_TUNEL_URL" $url
Escribir-Env "JARVIS_PUERTO" $Puerto

# ---------------------------------------------------------------------------
# 5. Prueba
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 5 - Prueba desde internet" -ForegroundColor Cyan
Write-Host ""

$servidorVivo = $false
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/jarvis" -UseBasicParsing -TimeoutSec 3 | Out-Null
    $servidorVivo = $true
} catch { }

if (-not $servidorVivo) {
    Write-Host "  El servidor no esta corriendo. Arrancalo en otra ventana:" -ForegroundColor Yellow
    Write-Host "    py -m uvicorn server:app --port $Puerto" -ForegroundColor White
    Read-Host "  Pulsa Enter cuando este listo"
}

$ok = $false
try {
    $r = Invoke-WebRequest -Uri "$url/jarvis" -UseBasicParsing -TimeoutSec 20
    if ("$($r.Content)" -match '"status"\s*:\s*"ok"') {
        $ok = $true
        Write-Host "  JSON limpio, sin pagina intermedia. Esto es lo que veia falta." -ForegroundColor Green
        Write-Host "  $($r.Content)" -ForegroundColor DarkGray
    } else {
        Write-Host "  Respuesta inesperada:" -ForegroundColor Yellow
        Write-Host "  $("$($r.Content)".Substring(0, [Math]::Min(300, "$($r.Content)".Length)))"
    }
} catch {
    Write-Host "  No pude alcanzar $url/jarvis" -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)"
    Write-Host ""
    Write-Host "  Si dice que Funnel no esta habilitado, entra al enlace que"
    Write-Host "  imprimio Tailscale arriba y autorizalo en tu tailnet."
}

# ---------------------------------------------------------------------------
Titulo "RESUMEN"

Write-Host "Endpoint nuevo para la consola de Alexa:" -ForegroundColor Green
Write-Host ""
Write-Host "    $url/jarvis" -ForegroundColor White
Write-Host ""
Write-Host "Pasos en la consola:"
Write-Host "  1. Build > Endpoint > HTTPS"
Write-Host "  2. Default Region: pega la URL de arriba"
Write-Host "  3. Certificado: 'trusted certificate authority'"
Write-Host "  4. Save Endpoints"
Write-Host ""
Write-Host "Ya puedes cerrar ngrok, no hace falta:" -ForegroundColor DarkGray
Write-Host "    Stop-Process -Name ngrok -Force" -ForegroundColor DarkGray
Write-Host ""

if ($ok) {
    Write-Host "El Funnel queda activo entre reinicios por si solo (--bg)." -ForegroundColor Cyan
}

Set-Content -Path (Join-Path $env:USERPROFILE ".jarvis\url_actual.txt") -Value "$url/jarvis"
Write-Host ""
