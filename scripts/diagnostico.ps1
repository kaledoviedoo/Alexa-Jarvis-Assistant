# ============================================================================
#  diagnostico.ps1
#  Revisa que todas las piezas de Jarvis estén en su sitio.
#  Ejecuta esto PRIMERO cuando algo no funcione.
# ============================================================================

$RaizProyecto = Split-Path -Parent $PSScriptRoot
$fallos = 0

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

function Comprobar {
    param([string]$Nombre, [scriptblock]$Prueba, [string]$Arreglo)
    Write-Host -NoNewline ("  {0,-34}" -f $Nombre)
    try {
        $resultado = & $Prueba
        if ($resultado) {
            Write-Host "OK  $resultado" -ForegroundColor Green
            return $true
        }
        Write-Host "FALLO" -ForegroundColor Red
        if ($Arreglo) { Write-Host "        -> $Arreglo" -ForegroundColor Yellow }
        $script:fallos++
        return $false
    } catch {
        Write-Host "FALLO  $($_.Exception.Message)" -ForegroundColor Red
        if ($Arreglo) { Write-Host "        -> $Arreglo" -ForegroundColor Yellow }
        $script:fallos++
        return $false
    }
}

Write-Host ""
Write-Host "=================================================================="
Write-Host "  DIAGNOSTICO DE JARVIS"
Write-Host "=================================================================="
Write-Host ""

Write-Host "REQUISITOS" -ForegroundColor Cyan

Comprobar "Python (launcher py)" {
    $v = & py --version 2>&1
    if ($LASTEXITCODE -eq 0) { $v } else { $null }
} "Instala Python desde python.org y marca 'Add to PATH'." | Out-Null

Comprobar "Ollama instalado" {
    $v = & ollama --version 2>&1
    if ($LASTEXITCODE -eq 0) { "$v" } else { $null }
} "Instala Ollama desde ollama.com/download" | Out-Null

Comprobar "ngrok instalado" {
    $v = & ngrok version 2>&1
    if ($LASTEXITCODE -eq 0) { "$v" } else { $null }
} "Descárgalo de https://ngrok.com/download y añádelo al PATH" | Out-Null

Comprobar "ngrok firmado por ngrok" {
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    $f = Get-AuthenticodeSignature -FilePath $cmd.Source
    if ($f.Status -eq "Valid" -and "$($f.SignerCertificate.Subject)" -match "ngrok") {
        "firma válida"
    } else {
        $null
    }
} "Firma no verificada. Vuelve a descargarlo SOLO desde https://ngrok.com/download" | Out-Null

Comprobar "authtoken de ngrok" {
    $cfg = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
    if ((Test-Path $cfg) -and ((Get-Content $cfg -Raw) -match "authtoken")) { "configurado" }
} "ngrok config add-authtoken TU_TOKEN" | Out-Null

Comprobar "nvidia-smi (RTX 3050)" {
    $v = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) { "$v" } else { $null }
} "Actualiza los drivers NVIDIA. Sin esto no se puede leer la VRAM." | Out-Null

Write-Host ""
Write-Host "ARCHIVOS DEL PROYECTO" -ForegroundColor Cyan

Comprobar "server.py" { if (Test-Path (Join-Path $RaizProyecto "server.py")) { "presente" } } | Out-Null
Comprobar "config.py" { if (Test-Path (Join-Path $RaizProyecto "config.py")) { "presente" } } | Out-Null
Comprobar "archivo .env" {
    if (Test-Path (Join-Path $RaizProyecto ".env")) { "presente" }
} "Copia .env.example a .env y pon ahí tu ALEXA_SKILL_ID." | Out-Null

Write-Host ""
Write-Host "DEPENDENCIAS DE PYTHON" -ForegroundColor Cyan

foreach ($paquete in @("fastapi", "uvicorn", "psutil", "ollama", "cryptography", "pyautogui", "docx", "openpyxl")) {
    $nombreImport = $paquete
    Comprobar "  $paquete" {
        & py -c "import $nombreImport" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { "instalado" } else { $null }
    } "py -m pip install -r requirements.txt" | Out-Null
}

Write-Host ""
Write-Host "SERVICIOS EN EJECUCION" -ForegroundColor Cyan

Comprobar "API de Ollama (11434)" {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { "responde" }
} "Arranca Ollama:  ollama serve" | Out-Null

$servidorOk = Comprobar "Servidor Jarvis (8000)" {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/jarvis" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { "responde" }
} "Arranca el servidor:  py -m uvicorn server:app --port 8000"

$tunelOk = Comprobar "Túnel de ngrok" {
    if (-not (Get-Process -Name "ngrok" -ErrorAction SilentlyContinue)) { return $null }
    try {
        $api = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 3
        if ($api.tunnels.Count -gt 0) { $api.tunnels[0].public_url } else { $null }
    } catch { "corriendo (API 4040 no responde)" }
} "Arranca el túnel:  ngrok http 8000 --url=TU_DOMINIO"

Comprobar "Tarea programada" {
    $t = Get-ScheduledTask -TaskName "Jarvis" -ErrorAction SilentlyContinue
    if ($t) { $t.State }
} "Instálala:  .\instalar_autoarranque.ps1  (como administrador)" | Out-Null

# El más importante de todos: comprueba la cadena COMPLETA tal y como la ve
# Alexa, desde internet hasta Jarvis. Si esto pasa, el endpoint es válido.
$dominio = Leer-Env "JARVIS_NGROK_URL" ""
if ($dominio) {
    if ($dominio -notmatch '^https?://') { $dominio = "https://$dominio" }
    $dominio = $dominio.TrimEnd('/')
}
Comprobar "Endpoint público accesible" {
    if (-not $dominio) { return $null }
    $r = Invoke-WebRequest -Uri "$dominio/jarvis" -UseBasicParsing -TimeoutSec 12
    if ($r.Content -match '"status"\s*:\s*"ok"') {
        "$dominio/jarvis"
    } elseif ($r.Content -match "<html") {
        $null   # página intermedia de ngrok u otra cosa
    } else {
        $null
    }
} "Revisa JARVIS_NGROK_URL en .env y que ngrok esté corriendo con --url=ese dominio" | Out-Null

Write-Host ""
Write-Host "MODELOS DE OLLAMA" -ForegroundColor Cyan
try {
    $modelos = & ollama list 2>&1
    Write-Host $modelos
} catch {
    Write-Host "  No pude listar los modelos." -ForegroundColor Red
}

if ($servidorOk) {
    Write-Host ""
    Write-Host "ESTADO INTERNO DE JARVIS" -ForegroundColor Cyan
    try {
        $salud = Invoke-RestMethod -Uri "http://127.0.0.1:8000/salud" -TimeoutSec 5
        Write-Host "  Modo actual        : $($salud.modo)"
        Write-Host "  Modelo             : $($salud.perfil.modelo)"
        Write-Host "  Ollama conectado   : $($salud.ollama.conectado)"
        Write-Host "  Verificar firma    : $($salud.seguridad.verificar_firma)"
        Write-Host "  Skill ID puesto    : $($salud.seguridad.skill_id_configurado)"
        Write-Host "  Escritorio         : $($salud.rutas.escritorio)"
        Write-Host "  Comet detectado    : $($salud.rutas.comet)"
        if ($salud.gpu.disponible) {
            $libre = [math]::Round($salud.gpu.vram_libre_mb / 1024, 1)
            $total = [math]::Round($salud.gpu.vram_total_mb / 1024, 1)
            Write-Host "  GPU                : $($salud.gpu.nombre)  $libre de $total GB libres"
        }
    } catch {
        Write-Host "  No pude leer /salud: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=================================================================="
if ($fallos -eq 0) {
    Write-Host "  TODO EN ORDEN" -ForegroundColor Green
} else {
    Write-Host "  $fallos COMPROBACIONES FALLARON (mira las flechas amarillas)" -ForegroundColor Yellow
}
Write-Host "=================================================================="
Write-Host ""
