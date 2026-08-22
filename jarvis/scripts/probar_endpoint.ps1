# ============================================================================
#  probar_endpoint.ps1
#  Recorre la cadena Alexa -> ngrok -> Jarvis salto por salto y te dice
#  exactamente en cuál se corta.
#
#     .\probar_endpoint.ps1
#
#  Úsalo cuando la consola de Alexa dé "No puedo conectarme con la Skill
#  solicitada" o cuando el JSON Output salga vacío.
# ============================================================================

$ErrorActionPreference = "Continue"

$RaizProyecto = Split-Path -Parent $PSScriptRoot

function Leer-Env {
    param([string]$Clave, [string]$PorDefecto = "")
    $rutaEnv = Join-Path $RaizProyecto ".env"
    if (-not (Test-Path $rutaEnv)) { return $PorDefecto }
    foreach ($linea in Get-Content $rutaEnv -Encoding UTF8) {
        $t = $linea.Trim()
        if ($t -eq "" -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $partes = $t.Split("=", 2)
        if ($partes[0].Trim() -eq $Clave) { return $partes[1].Trim().Trim('"').Trim("'") }
    }
    return $PorDefecto
}

$Puerto  = Leer-Env "JARVIS_PUERTO" "8000"
$SkillId = Leer-Env "ALEXA_SKILL_ID" ""
$dominio = Leer-Env "JARVIS_NGROK_URL" ""

if ($dominio) {
    if ($dominio -notmatch '^https?://') { $dominio = "https://$dominio" }
    $dominio = $dominio.TrimEnd('/')
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  PRUEBA DE LA CADENA COMPLETA" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Dominio configurado : $dominio"
Write-Host "  Puerto local        : $Puerto"
Write-Host ""

$culpable = $null

# ---------------------------------------------------------------------------
# SALTO 1 - El servidor de Jarvis en local
# ---------------------------------------------------------------------------
Write-Host "SALTO 1 - Servidor de Jarvis en localhost" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/jarvis" -UseBasicParsing -TimeoutSec 5
    Write-Host "  OK    responde: $($r.Content)" -ForegroundColor Green
} catch {
    Write-Host "  FALLO no responde en el puerto $Puerto" -ForegroundColor Red
    Write-Host ""
    Write-Host "  ARRÉGLALO ASÍ:" -ForegroundColor Yellow
    Write-Host "    cd $RaizProyecto"
    Write-Host "    py -m uvicorn server:app --port $Puerto"
    Write-Host ""
    Write-Host "  Sin esto, nada más va a funcionar. Para aquí."
    exit 1
}

# ---------------------------------------------------------------------------
# SALTO 2 - El proceso de ngrok
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "SALTO 2 - Proceso de ngrok" -ForegroundColor Cyan

$proc = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "  FALLO ngrok no está corriendo" -ForegroundColor Red
    Write-Host ""
    Write-Host "  ARRÉGLALO ASÍ (en otra ventana, y déjala abierta):" -ForegroundColor Yellow
    Write-Host "    ngrok http $Puerto --url=$dominio"
    Write-Host ""
    exit 1
}
Write-Host "  OK    corriendo (PID $($proc.Id))" -ForegroundColor Green

# ---------------------------------------------------------------------------
# SALTO 3 - El túnel registrado en ngrok
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "SALTO 3 - Túnel registrado" -ForegroundColor Cyan

try {
    $api = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 5
    if ($api.tunnels.Count -eq 0) {
        Write-Host "  FALLO ngrok corre pero no tiene ningún túnel activo" -ForegroundColor Red
        $culpable = "tunel"
    } else {
        foreach ($t in $api.tunnels) {
            Write-Host "  OK    $($t.public_url)  ->  $($t.config.addr)" -ForegroundColor Green
        }

        $urlPublica = $api.tunnels[0].public_url
        if ($dominio -and $urlPublica.TrimEnd('/') -ne $dominio) {
            Write-Host ""
            Write-Host "  AVISO: el túnel activo NO coincide con tu .env" -ForegroundColor Yellow
            Write-Host "     .env  : $dominio"
            Write-Host "     activo: $urlPublica"
            Write-Host "     El endpoint de Alexa debe apuntar al ACTIVO."
            $dominio = $urlPublica.TrimEnd('/')
        }
    }
} catch {
    Write-Host "  AVISO no pude leer el API de ngrok en el puerto 4040" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# SALTO 4 - GET público (¿llega desde internet?)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "SALTO 4 - GET público a $dominio/jarvis" -ForegroundColor Cyan

$getOk = $false
try {
    $r = Invoke-WebRequest -Uri "$dominio/jarvis" -UseBasicParsing -TimeoutSec 15
    $cuerpo = $r.Content

    if ($cuerpo -match '"status"\s*:\s*"ok"') {
        Write-Host "  OK    Jarvis responde desde internet" -ForegroundColor Green
        $getOk = $true
    }
    elseif ($cuerpo -match "<html") {
        Write-Host "  FALLO devolvió HTML en vez de JSON" -ForegroundColor Red
        Write-Host "        Probablemente la página intermedia de ngrok."
        $culpable = "interstitial"
    }
    else {
        Write-Host "  AVISO respuesta inesperada:" -ForegroundColor Yellow
        Write-Host "        $($cuerpo.Substring(0, [Math]::Min(160, $cuerpo.Length)))"
    }
} catch {
    Write-Host "  FALLO no se pudo alcanzar la URL pública" -ForegroundColor Red
    Write-Host "        $($_.Exception.Message)"
    $culpable = "url"
}

# ---------------------------------------------------------------------------
# SALTO 5 - POST como el de Alexa
# ---------------------------------------------------------------------------
# Con la verificación de firma activada, un POST sin la firma de Amazon DEBE
# devolver 400. Ese 400 es una BUENA noticia: demuestra que la petición
# atravesó el túnel y llegó hasta Jarvis, que fue quien la rechazó.
Write-Host ""
Write-Host "SALTO 5 - POST tipo Alexa (se espera un 400)" -ForegroundColor Cyan

$cuerpoPrueba = @{
    version = "1.0"
    session = @{
        new = $true
        sessionId = "amzn1.echo-api.session.prueba"
        application = @{ applicationId = $SkillId }
    }
    request = @{
        type = "LaunchRequest"
        requestId = "amzn1.echo-api.request.prueba"
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        locale = "es-ES"
    }
} | ConvertTo-Json -Depth 8

try {
    $r = Invoke-WebRequest -Uri "$dominio/jarvis" -Method Post `
            -ContentType "application/json" -Body $cuerpoPrueba `
            -UseBasicParsing -TimeoutSec 15

    Write-Host "  AVISO devolvió $($r.StatusCode) en lugar de 400." -ForegroundColor Yellow
    Write-Host "        ¿Tienes JARVIS_VERIFICAR_FIRMA=false? Ponlo en true."
}
catch {
    $codigo = $null
    if ($_.Exception.Response) { $codigo = [int]$_.Exception.Response.StatusCode }

    if ($codigo -eq 400) {
        Write-Host "  OK    devolvió 400: la petición LLEGO hasta Jarvis" -ForegroundColor Green
        Write-Host "        (rechazada por no traer la firma de Amazon, que es lo correcto)"
        $getOk = $true
    } elseif ($codigo) {
        Write-Host "  AVISO devolvió HTTP $codigo" -ForegroundColor Yellow
    } else {
        Write-Host "  FALLO no hubo respuesta: $($_.Exception.Message)" -ForegroundColor Red
        $culpable = "url"
    }
}

# ---------------------------------------------------------------------------
# VEREDICTO
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  VEREDICTO" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

if ($culpable -eq "url") {
    Write-Host "El túnel no es alcanzable desde internet." -ForegroundColor Red
    Write-Host ""
    Write-Host "Revisa por este orden:"
    Write-Host "  1. Que ngrok se arrancó con  --url=$dominio"
    Write-Host "  2. Que el dominio del dashboard de ngrok es exactamente ese"
    Write-Host "  3. Que no hay otra sesión de ngrok ocupando el dominio:"
    Write-Host "        Stop-Process -Name ngrok -Force"
}
elseif ($culpable -eq "interstitial") {
    Write-Host "ngrok devuelve su página intermedia." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Afecta a navegadores. Alexa manda POST con Accept: application/json"
    Write-Host "y normalmente la esquiva, así que prueba igualmente en la consola."
}
elseif ($getOk) {
    Write-Host "TU ENDPOINT FUNCIONA." -ForegroundColor Green
    Write-Host ""
    Write-Host "Endpoint que debe estar en la consola de Alexa:"
    Write-Host ""
    Write-Host "    $dominio/jarvis" -ForegroundColor White
    Write-Host ""
    Write-Host "Si Alexa sigue diciendo que no puede conectarse, el problema ya no"
    Write-Host "es de red sino de configuración de la skill. Comprueba:"
    Write-Host ""
    Write-Host "  1. Build > Endpoint tiene marcado HTTPS, no AWS Lambda ARN"
    Write-Host "  2. La URL de arriba está pegada en 'Default Region'"
    Write-Host "  3. El desplegable del certificado dice 'trusted certificate authority'"
    Write-Host "  4. Pulsaste 'Save Endpoints'"
    Write-Host "  5. Pulsaste 'Build Model' DESPUÉS de guardar el endpoint"
    Write-Host ""
    Write-Host "El paso 5 es el que más se olvida: cambiar el endpoint no surte"
    Write-Host "efecto hasta que reconstruyes el modelo."
}
else {
    Write-Host "Revisa los saltos marcados en rojo más arriba." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Consejo: deja abierto  http://127.0.0.1:4040  mientras pruebas en la" -ForegroundColor DarkGray
Write-Host "consola de Alexa. Si la petición no aparece ahí, nunca salió de Amazon." -ForegroundColor DarkGray
Write-Host ""
