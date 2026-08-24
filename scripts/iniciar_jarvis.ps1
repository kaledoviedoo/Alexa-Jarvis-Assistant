# ============================================================================
#  iniciar_jarvis.ps1
#  Arranca Ollama, el servidor de Jarvis y el túnel de ngrok.
#  Es el script que ejecuta la tarea programada al iniciar sesión.
# ============================================================================

$ErrorActionPreference = "Continue"

$RaizProyecto = Split-Path -Parent $PSScriptRoot
$CarpetaLogs  = Join-Path $env:USERPROFILE ".jarvis\logs"
New-Item -ItemType Directory -Force -Path $CarpetaLogs | Out-Null

$LogArranque = Join-Path $CarpetaLogs "arranque.log"

function Escribir-Log {
    param([string]$Mensaje)
    $linea = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Mensaje"
    Write-Host $linea
    Add-Content -Path $LogArranque -Value $linea
}

# ---------------------------------------------------------------------------
# Leer .env  (mismo formato que lee config.py)
# ---------------------------------------------------------------------------
function Leer-Env {
    param([string]$Clave, [string]$PorDefecto = "")

    $rutaEnv = Join-Path $RaizProyecto ".env"
    if (-not (Test-Path $rutaEnv)) { return $PorDefecto }

    foreach ($linea in Get-Content $rutaEnv -Encoding UTF8) {
        $t = $linea.Trim()
        if ($t -eq "" -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }

        $partes = $t.Split("=", 2)
        if ($partes[0].Trim() -eq $Clave) {
            return $partes[1].Trim().Trim('"').Trim("'")
        }
    }
    return $PorDefecto
}

$Proveedor    = (Leer-Env "JARVIS_TUNEL_PROVIDER" "ngrok").ToLower()
$DominioNgrok = Leer-Env "JARVIS_NGROK_URL" ""
$Puerto       = Leer-Env "JARVIS_PUERTO" "8000"

# Normalizamos el dominio: en .env se puede haber escrito sin esquema
# ("mi-dominio.ngrok-free.dev") o con barra final. ngrok necesita la URL
# completa en --url, así que la reconstruimos aquí en vez de fallar.
if ($DominioNgrok) {
    if ($DominioNgrok -notmatch '^https?://') { $DominioNgrok = "https://$DominioNgrok" }
    $DominioNgrok = $DominioNgrok.TrimEnd('/')
}

Escribir-Log "===== Arrancando Jarvis ====="
Escribir-Log "Proyecto : $RaizProyecto"
Escribir-Log "Puerto   : $Puerto"
Escribir-Log "Dominio  : $DominioNgrok"

# ---------------------------------------------------------------------------
# 1. Esperar a que haya red
#    Al iniciar sesión, la tarea puede dispararse antes de que la tarjeta de
#    red termine de conectar. Sin esto, ngrok falla nada más arrancar.
# ---------------------------------------------------------------------------
$intentos = 0
while ($intentos -lt 30) {
    if (Test-Connection -ComputerName 1.1.1.1 -Count 1 -Quiet -ErrorAction SilentlyContinue) {
        Escribir-Log "Red disponible."
        break
    }
    Start-Sleep -Seconds 2
    $intentos++
}
if ($intentos -ge 30) { Escribir-Log "AVISO: sigo sin red, continúo de todos modos." }

# ---------------------------------------------------------------------------
# 2. Ollama
# ---------------------------------------------------------------------------
if (-not (Get-Process -Name "ollama" -ErrorAction SilentlyContinue)) {
    Escribir-Log "Iniciando Ollama..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $CarpetaLogs "ollama.log") `
        -RedirectStandardError  (Join-Path $CarpetaLogs "ollama.err.log")
    Start-Sleep -Seconds 5
} else {
    Escribir-Log "Ollama ya estaba corriendo."
}

$intentos = 0
while ($intentos -lt 20) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
        Escribir-Log "Ollama responde correctamente."
        break
    } catch {
        Start-Sleep -Seconds 2
        $intentos++
    }
}

# ---------------------------------------------------------------------------
# 3. Servidor de Jarvis (Uvicorn)
# ---------------------------------------------------------------------------
Escribir-Log "Iniciando el servidor de Jarvis..."

Start-Process -FilePath "py" `
    -ArgumentList "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", $Puerto `
    -WorkingDirectory $RaizProyecto `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $CarpetaLogs "servidor.log") `
    -RedirectStandardError  (Join-Path $CarpetaLogs "servidor.err.log")

$servidorListo = $false
$intentos = 0
while ($intentos -lt 25) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/jarvis" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $servidorListo = $true
        Escribir-Log "Servidor de Jarvis activo en el puerto $Puerto."
        break
    } catch {
        Start-Sleep -Seconds 2
        $intentos++
    }
}

if (-not $servidorListo) {
    Escribir-Log "ERROR: el servidor no respondió. Revisa $CarpetaLogs\servidor.err.log"
}

# ---------------------------------------------------------------------------
# 4. Túnel público
# ---------------------------------------------------------------------------
if ($Proveedor -eq "tailscale") {
    # Funnel guarda su configuración en el propio nodo con --bg, así que
    # normalmente ya está activo tras reiniciar. Solo lo re-armamos si falta.
    Escribir-Log "Comprobando el Funnel de Tailscale..."
    try {
        $estadoFunnel = & tailscale funnel status 2>&1 | Out-String

        if ($estadoFunnel -match "(https://[a-zA-Z0-9\-\.]+\.ts\.net)") {
            Escribir-Log "Funnel ya activo en $($Matches[1])"
        } else {
            Escribir-Log "Funnel caído, reactivando..."
            & tailscale funnel --bg --https=443 "localhost:$Puerto" 2>&1 | Out-String | ForEach-Object {
                Escribir-Log $_.Trim()
            }
        }
    } catch {
        Escribir-Log "ERROR con Tailscale: $($_.Exception.Message)"
    }

    Escribir-Log "===== Jarvis listo ====="
    return
}

# ---- ngrok ----
# Se usa el dominio estático de la cuenta (--url), no un túnel efímero,
# para que la URL del endpoint de Alexa no cambie nunca.
if (Get-Process -Name "ngrok" -ErrorAction SilentlyContinue) {
    Escribir-Log "ngrok ya estaba corriendo."
} elseif (-not $DominioNgrok) {
    Escribir-Log "ERROR: falta JARVIS_NGROK_URL en .env. Ejecuta configurar_ngrok.ps1"
} else {
    Escribir-Log "Iniciando ngrok en $DominioNgrok ..."

    Start-Process -FilePath "ngrok" `
        -ArgumentList "http", $Puerto, "--url=$DominioNgrok", "--log=stdout" `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $CarpetaLogs "tunel.log") `
        -RedirectStandardError  (Join-Path $CarpetaLogs "tunel.err.log")

    # Comprobamos contra el API local de ngrok (puerto 4040) que el túnel
    # quedó realmente levantado, en vez de dar por hecho que arrancó bien.
    $tunelOk = $false
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 2
        try {
            $api = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
            if ($api.tunnels.Count -gt 0) {
                $tunelOk = $true
                Escribir-Log "Túnel activo: $($api.tunnels[0].public_url)"
                break
            }
        } catch {
            # El API de ngrok tarda un par de segundos en levantar.
        }
    }

    if (-not $tunelOk) {
        Escribir-Log "ERROR: ngrok no levantó el túnel. Revisa $CarpetaLogs\tunel.log"
    }
}

Escribir-Log "===== Jarvis listo ====="
