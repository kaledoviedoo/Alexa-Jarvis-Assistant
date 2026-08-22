# ============================================================================
#  configurar_ngrok.ps1
#  Configuración inicial de ngrok para Jarvis. Se ejecuta UNA vez.
#
#     .\configurar_ngrok.ps1
#
#  Comprueba la instalación, configura el authtoken, registra tu dominio
#  estático en .env y verifica que el túnel funciona de extremo a extremo.
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

    if (-not (Test-Path $RutaEnv)) {
        $plantilla = Join-Path $RaizProyecto ".env.example"
        if (Test-Path $plantilla) { Copy-Item $plantilla $RutaEnv }
        else { New-Item -ItemType File -Path $RutaEnv | Out-Null }
    }

    $lineas = @(Get-Content $RutaEnv -Encoding UTF8)
    $encontrada = $false

    for ($i = 0; $i -lt $lineas.Count; $i++) {
        if ($lineas[$i] -match "^\s*$Clave\s*=") {
            $lineas[$i] = "$Clave=$Valor"
            $encontrada = $true
            break
        }
    }
    if (-not $encontrada) { $lineas += "$Clave=$Valor" }

    Set-Content -Path $RutaEnv -Value $lineas -Encoding UTF8
    Write-Host "  .env  ->  $Clave=$Valor" -ForegroundColor DarkGray
}

Titulo "CONFIGURACION DE NGROK PARA JARVIS"

# ---------------------------------------------------------------------------
# 1. ¿Está instalado?
# ---------------------------------------------------------------------------
Write-Host "PASO 1 - Comprobando la instalación" -ForegroundColor Cyan

$ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrok) {
    Write-Host "  No encuentro 'ngrok' en el PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Si lo instalaste como un .exe suelto, indícame dónde está."
    $rutaManual = Read-Host "  Ruta completa a ngrok.exe (Enter para cancelar)"

    if (-not $rutaManual) { Write-Host "Cancelado."; exit 1 }

    $rutaManual = $rutaManual.Trim('"')
    if (-not (Test-Path $rutaManual)) {
        Write-Host "  No existe ese archivo." -ForegroundColor Red
        exit 1
    }

    # Lo añadimos al PATH del usuario para que los scripts lo encuentren siempre.
    $carpeta = Split-Path -Parent $rutaManual
    $pathUsuario = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($pathUsuario -notlike "*$carpeta*") {
        [Environment]::SetEnvironmentVariable("Path", "$pathUsuario;$carpeta", "User")
        Write-Host "  Añadido al PATH: $carpeta" -ForegroundColor Green
        Write-Host "  Cierra y reabre PowerShell al terminar este script." -ForegroundColor Yellow
    }
    $env:Path += ";$carpeta"
    $ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
}

$version = & ngrok version 2>&1
Write-Host "  $version" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Verificar la firma digital
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 2 - Verificando la firma digital" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Windows Defender marca ngrok con frecuencia porque las herramientas"
Write-Host "  de túnel se usan a menudo en ataques reales. La forma correcta de"
Write-Host "  distinguir el ngrok legítimo de una copia manipulada es comprobar"
Write-Host "  quién firmó el ejecutable."
Write-Host ""

$rutaExe = $ngrok.Source
$firma = Get-AuthenticodeSignature -FilePath $rutaExe

Write-Host "  Archivo  : $rutaExe"
Write-Host "  Estado   : $($firma.Status)"
Write-Host "  Firmante : $($firma.SignerCertificate.Subject)"
Write-Host ""

$firmante = "$($firma.SignerCertificate.Subject)"

if ($firma.Status -ne "Valid") {
    Write-Host "  LA FIRMA NO ES VÁLIDA." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Un ngrok legítimo está firmado por ngrok LLC o ngrok, Inc. y su"
    Write-Host "  firma valida correctamente. Si la tuya no valida, el archivo puede"
    Write-Host "  estar manipulado o venir de una fuente falsa, y en ese caso la"
    Write-Host "  alerta de Defender sería correcta y conviene hacerle caso."
    Write-Host ""
    Write-Host "  Bórralo y descárgalo solo desde https://ngrok.com/download"
    Write-Host ""
    $seguir = Read-Host "  ¿Continuar de todas formas? (s/n)"
    if ($seguir -ne "s") { exit 1 }
}
elseif ($firmante -notmatch "ngrok") {
    Write-Host "  FIRMA VÁLIDA PERO EL FIRMANTE NO ES NGROK." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Que un archivo esté firmado no basta: importa quién lo firmó."
    Write-Host "  Bórralo y descárgalo desde el sitio oficial."
    Write-Host ""
    $seguir = Read-Host "  ¿Continuar de todas formas? (s/n)"
    if ($seguir -ne "s") { exit 1 }
}
else {
    Write-Host "  Firma válida y emitida por ngrok. Correcto." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# 3. Authtoken
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 3 - Authtoken" -ForegroundColor Cyan
Write-Host ""

$configNgrok = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$tieneToken = (Test-Path $configNgrok) -and ((Get-Content $configNgrok -Raw) -match "authtoken")

if ($tieneToken) {
    Write-Host "  Ya hay un authtoken configurado." -ForegroundColor Green
} else {
    Write-Host "  Cópialo de:  https://dashboard.ngrok.com/get-started/your-authtoken"
    Write-Host ""
    $token = Read-Host "  Pega aquí tu authtoken (Enter para saltar)"

    if ($token) {
        & ngrok config add-authtoken $token.Trim()
        Write-Host "  Authtoken guardado." -ForegroundColor Green
    } else {
        Write-Host "  Saltado. Sin authtoken, ngrok no arrancará." -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# 4. Dominio estático
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 4 - Dominio estático" -ForegroundColor Cyan
Write-Host ""
Write-Host "  El plan gratuito incluye UN dominio estático. Es imprescindible:"
Write-Host "  sin él, la URL cambia en cada arranque y tendrías que reconfigurar"
Write-Host "  la skill de Alexa cada vez que enciendes el PC."
Write-Host ""
Write-Host "  Reclámalo en:  https://dashboard.ngrok.com/domains"
Write-Host "  Tendrá esta forma:  https://algo-algo-1234.ngrok-free.app"
Write-Host ""

$dominio = Read-Host "  Pega aquí tu dominio estático completo"
$dominio = $dominio.Trim().TrimEnd("/")

if (-not $dominio) {
    Write-Host "  Sin dominio no puedo continuar." -ForegroundColor Red
    exit 1
}

if ($dominio -notmatch "^https://") {
    $dominio = "https://$dominio"
    Write-Host "  Le añadí https:// -> $dominio" -ForegroundColor DarkGray
}

Escribir-Env "JARVIS_NGROK_URL" $dominio
Escribir-Env "JARVIS_PUERTO" $Puerto

# ---------------------------------------------------------------------------
# 5. Prueba de extremo a extremo
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "PASO 5 - Prueba" -ForegroundColor Cyan
Write-Host ""

$servidorVivo = $false
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/jarvis" -UseBasicParsing -TimeoutSec 3 | Out-Null
    $servidorVivo = $true
} catch { }

if (-not $servidorVivo) {
    Write-Host "  El servidor de Jarvis no está corriendo. Arráncalo en otra ventana:" -ForegroundColor Yellow
    Write-Host "    py -m uvicorn server:app --port $Puerto" -ForegroundColor White
    Write-Host ""
    Read-Host "  Pulsa Enter cuando esté arrancado"
}

Write-Host "  Levantando ngrok..."

$procNgrok = Start-Process -FilePath "ngrok" `
    -ArgumentList "http", $Puerto, "--url=$dominio" `
    -WindowStyle Hidden -PassThru

Start-Sleep -Seconds 5

$ok = $false
try {
    $respuesta = Invoke-WebRequest -Uri "$dominio/jarvis" -UseBasicParsing -TimeoutSec 12
    $cuerpo = $respuesta.Content

    if ($cuerpo -match '"status"\s*:\s*"ok"') {
        $ok = $true
        Write-Host ""
        Write-Host "  El túnel funciona y llega hasta Jarvis." -ForegroundColor Green
    }
    elseif ($cuerpo -match "ngrok" -and $cuerpo -match "<html") {
        Write-Host ""
        Write-Host "  ngrok devolvió su página intermedia en lugar de Jarvis." -ForegroundColor Yellow
        Write-Host "  Las peticiones de Alexa son POST con Accept: application/json,"
        Write-Host "  así que normalmente la esquivan. Compruébalo en la pestaña Test"
        Write-Host "  de la consola de Alexa antes de darlo por bueno."
    }
    else {
        Write-Host "  Respuesta inesperada:" -ForegroundColor Yellow
        Write-Host "  $($cuerpo.Substring(0, [Math]::Min(200, $cuerpo.Length)))"
    }
} catch {
    Write-Host "  No pude alcanzar $dominio/jarvis" -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)"
    Write-Host ""
    Write-Host "  Revisa que el dominio esté bien escrito y que el authtoken sea válido."
}

if ($procNgrok -and -not $procNgrok.HasExited) {
    Stop-Process -Id $procNgrok.Id -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
Titulo "RESUMEN"

Write-Host "Endpoint para la consola de Alexa:" -ForegroundColor Green
Write-Host ""
Write-Host "    $dominio/jarvis" -ForegroundColor White
Write-Host ""
Write-Host "Pégalo en Build > Endpoint, elige la opción de certificado de"
Write-Host "'autoridad de certificación de confianza' y reconstruye el modelo."
Write-Host ""

if ($ok) {
    Write-Host "Siguiente paso: instalar el arranque automático." -ForegroundColor Cyan
    Write-Host "    .\instalar_autoarranque.ps1   (como administrador)" -ForegroundColor White
} else {
    Write-Host "Arregla primero el túnel antes de seguir." -ForegroundColor Yellow
}

Set-Content -Path (Join-Path $env:USERPROFILE ".jarvis\url_actual.txt") -Value "$dominio/jarvis"
Write-Host ""
