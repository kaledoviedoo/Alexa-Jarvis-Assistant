# ============================================================================
#  revisar_tunel.ps1
#  Dice EXACTAMENTE que hay al otro lado de tu URL publica.
#
#     .\revisar_tunel.ps1
#
#  Distingue entre: servidor caido, agente de ngrok caido, dominio equivocado,
#  pagina intersticial y respuesta correcta de Jarvis. Cada caso tiene una
#  causa distinta y un arreglo distinto.
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
        $p = $t.Split("=", 2)
        if ($p[0].Trim() -eq $Clave) { return $p[1].Trim().Trim('"').Trim("'") }
    }
    return $PorDefecto
}

$Puerto  = Leer-Env "JARVIS_PUERTO" "8000"
$dominio = Leer-Env "JARVIS_NGROK_URL" ""
if ($dominio) {
    if ($dominio -notmatch '^https?://') { $dominio = "https://$dominio" }
    $dominio = $dominio.TrimEnd('/')
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  QUE HAY AL OTRO LADO DE TU URL" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# A. Procesos locales
# ---------------------------------------------------------------------------
Write-Host "A. PROCESOS LOCALES" -ForegroundColor Cyan

$pNgrok = Get-Process -Name "ngrok" -ErrorAction SilentlyContinue
if ($pNgrok) {
    foreach ($p in $pNgrok) {
        $vivo = (Get-Date) - $p.StartTime
        Write-Host ("   ngrok    PID {0}, lleva {1:hh\:mm\:ss} corriendo" -f $p.Id, $vivo) -ForegroundColor Green
    }
    if ($pNgrok.Count -gt 1) {
        Write-Host "   AVISO: hay $($pNgrok.Count) procesos de ngrok. El plan gratuito solo" -ForegroundColor Yellow
        Write-Host "          permite una sesion; los de mas pueden estar peleandose el dominio." -ForegroundColor Yellow
        Write-Host "          Arreglo:  Stop-Process -Name ngrok -Force   y arranca uno solo." -ForegroundColor Yellow
    }
} else {
    Write-Host "   ngrok    NO ESTA CORRIENDO" -ForegroundColor Red
}

$pUvicorn = Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*uvicorn*" }
if ($pUvicorn) {
    Write-Host "   servidor PID $($pUvicorn.ProcessId)" -ForegroundColor Green
} else {
    Write-Host "   servidor NO ESTA CORRIENDO" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# B. Tuneles registrados en el agente
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "B. TUNELES REGISTRADOS EN EL AGENTE" -ForegroundColor Cyan
try {
    $api = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 4
    if ($api.tunnels.Count -eq 0) {
        Write-Host "   Ninguno. El agente corre pero no expone nada." -ForegroundColor Red
    }
    foreach ($t in $api.tunnels) {
        Write-Host "   $($t.public_url)  ->  $($t.config.addr)" -ForegroundColor Green
    }
} catch {
    Write-Host "   El API local de ngrok (4040) no responde." -ForegroundColor Red
    Write-Host "   Suele significar que el agente no esta arrancado."
}

# ---------------------------------------------------------------------------
# C. Respuesta cruda de la URL publica
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "C. RESPUESTA CRUDA DE $dominio/jarvis" -ForegroundColor Cyan
Write-Host ""

$codigo   = $null
$cuerpo   = ""
$cabeceras = @{}

try {
    $r = Invoke-WebRequest -Uri "$dominio/jarvis" -UseBasicParsing -TimeoutSec 20
    $codigo = [int]$r.StatusCode
    $cuerpo = "$($r.Content)"
    foreach ($k in $r.Headers.Keys) { $cabeceras[$k] = $r.Headers[$k] }
}
catch {
    if ($_.Exception.Response) {
        $resp = $_.Exception.Response
        try { $codigo = [int]$resp.StatusCode } catch { }

        # PowerShell 7 deja el cuerpo aqui; 5.1 obliga a leer el stream.
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $cuerpo = "$($_.ErrorDetails.Message)"
        } else {
            try {
                $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $cuerpo = $sr.ReadToEnd()
                $sr.Close()
            } catch { }
        }

        try {
            foreach ($k in $resp.Headers.AllKeys) { $cabeceras[$k] = $resp.Headers[$k] }
        } catch {
            try { foreach ($k in $resp.Headers.Keys) { $cabeceras[$k] = $resp.Headers[$k] } } catch { }
        }
    } else {
        Write-Host "   SIN RESPUESTA: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "   Codigo HTTP : $codigo"

foreach ($clave in @("ngrok-error-code", "Server", "Content-Type")) {
    foreach ($k in $cabeceras.Keys) {
        if ("$k".ToLower() -eq $clave.ToLower()) {
            Write-Host "   $clave : $($cabeceras[$k])"
        }
    }
}

Write-Host ""
Write-Host "   --- primeros 400 caracteres del cuerpo ---" -ForegroundColor DarkGray
$recorte = if ($cuerpo.Length -gt 400) { $cuerpo.Substring(0, 400) } else { $cuerpo }
Write-Host $recorte
Write-Host "   --- fin ---" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# D. Veredicto
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  VEREDICTO" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

$textoErrNgrok = ($cuerpo -match "ERR_NGROK_(\d+)")
$codigoNgrok = if ($textoErrNgrok) { $Matches[1] } else { "" }

if ($cuerpo -match '"status"\s*:\s*"ok"') {
    Write-Host "TODO CORRECTO: Jarvis responde desde internet." -ForegroundColor Green
    Write-Host "Si Alexa sigue fallando, el problema no es la red."
}
elseif ($codigoNgrok -eq "3200" -or $cuerpo -match "endpoint.*offline|not found.*ngrok|tunnel not found") {
    Write-Host "EL AGENTE DE NGROK NO ESTA SIRVIENDO ESTE DOMINIO." -ForegroundColor Red
    Write-Host ""
    Write-Host "El dominio existe en tu cuenta pero no hay ningun agente conectado a el."
    Write-Host "Es lo que pasa si cerraste la ventana de ngrok o si el proceso murio."
    Write-Host ""
    Write-Host "ARREGLO: abre una ventana y dejala abierta:"
    Write-Host "   ngrok http $Puerto --url=$dominio" -ForegroundColor White
}
elseif ($codigoNgrok -eq "8012" -or $cuerpo -match "8012") {
    Write-Host "NGROK ESTA VIVO PERO NO ALCANZA TU SERVIDOR LOCAL." -ForegroundColor Red
    Write-Host ""
    Write-Host "El tunel funciona; lo que no responde es uvicorn en el puerto $Puerto."
    Write-Host ""
    Write-Host "ARREGLO:"
    Write-Host "   cd $RaizProyecto" -ForegroundColor White
    Write-Host "   py -m uvicorn server:app --port $Puerto" -ForegroundColor White
}
elseif ($codigo -eq 404 -and $cuerpo -match '"detail"\s*:\s*"Not Found"') {
    Write-Host "LLEGA A JARVIS PERO LA RUTA NO EXISTE." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "El 404 lo genera FastAPI, no ngrok: la cadena funciona entera."
    Write-Host "Si esto sale en /jarvis, tienes un server.py antiguo corriendo."
    Write-Host ""
    Write-Host "ARREGLO: reinicia uvicorn para que cargue el server.py actual."
}
elseif ($codigo -eq 404) {
    Write-Host "404 SIN IDENTIFICAR." -ForegroundColor Red
    Write-Host "Mira el cuerpo de arriba: si menciona ngrok, el agente no sirve"
    Write-Host "este dominio. Si es JSON de FastAPI, es un server.py viejo."
}
elseif ($cuerpo -match "<html" -and $cuerpo -match "ngrok") {
    Write-Host "PAGINA INTERSTICIAL DE NGROK (solo afecta a navegadores)." -ForegroundColor Yellow
    Write-Host "No bloquea a Alexa, que manda POST. No es el problema."
}
else {
    Write-Host "RESPUESTA NO RECONOCIDA. Copia el bloque del cuerpo de arriba." -ForegroundColor Yellow
}

Write-Host ""
