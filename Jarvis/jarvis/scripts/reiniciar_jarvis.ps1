# ============================================================================
#  reiniciar_jarvis.ps1
#  Reinicia el servidor de Jarvis y demuestra que arranco con el codigo actual.
#
#     .\reiniciar_jarvis.ps1
#
#  Por que este script es mas complicado de lo que parece que deberia:
#
#  1) Matar por linea de comandos no sirve. Si el proceso viejo se lanzo de
#     otra forma, el filtro no lo encuentra, sigue agarrado al 8000, el nuevo
#     falla al enlazar con el error 10048 y se apaga en silencio. Crees que
#     actualizaste y Alexa sigue hablando con el codigo de hace horas.
#     Por eso matamos a QUIEN TENGA EL PUERTO.
#
#  2) Matar el proceso tampoco basta. La tarea de autoarranque se registro con
#     -RunLevel Highest, o sea que Jarvis corre ELEVADO. Una consola normal no
#     puede matar un proceso elevado: Stop-Process devuelve "Acceso denegado".
#     Si ese error va con -ErrorAction SilentlyContinue, el script dice
#     "matando PID 2352" y no mata nada.
#     Por eso aqui: primero paramos la TAREA (eso si funciona sin elevar), y
#     si aun asi resiste, el script se relanza como administrador solo.
# ============================================================================

$ErrorActionPreference = "Continue"

$RaizProyecto = Split-Path -Parent $PSScriptRoot
$LogJarvis    = Join-Path $env:USERPROFILE ".jarvis\jarvis.log"
$CarpetaLogs  = Join-Path $env:USERPROFILE ".jarvis\logs"
$NombreTarea  = "Jarvis"

function Soy-Administrador {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

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

function Quien-Tiene-El-Puerto {
    param([int]$Puerto)
    $encontrados = @()
    try {
        $encontrados += (Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction Stop).OwningProcess
    } catch {
        $salida = netstat -ano | Select-String ":$Puerto\s+.*LISTENING"
        foreach ($l in $salida) {
            $campos = ($l.ToString() -split "\s+") | Where-Object { $_ -ne "" }
            if ($campos.Length -ge 5) { $encontrados += [int]$campos[-1] }
        }
    }
    return @($encontrados | Sort-Object -Unique | Where-Object { $_ -gt 0 })
}

function Esperar-Puerto-Libre {
    param([int]$Puerto, [int]$Intentos = 15)
    for ($i = 0; $i -lt $Intentos; $i++) {
        Start-Sleep -Milliseconds 600
        if (-not (Quien-Tiene-El-Puerto -Puerto $Puerto)) { return $true }
    }
    return $false
}

$Puerto = [int](Leer-Env "JARVIS_PUERTO" "8000")

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  REINICIANDO JARVIS" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
$elevado = Soy-Administrador
if ($elevado) { Write-Host "  (consola de administrador)" -ForegroundColor DarkGray }
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Parar la tarea programada
# ---------------------------------------------------------------------------
# Este es el paso que faltaba. La tarea corre elevada, asi que el proceso es
# intocable desde una consola normal, pero PARAR LA TAREA si esta permitido
# porque la tarea es tuya. El Programador de tareas mata el proceso por ti.
Write-Host "1. Parando la tarea programada..." -ForegroundColor Cyan

$tarea = Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
if ($tarea) {
    if ($tarea.State -eq "Running") {
        Stop-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
        Write-Host "   tarea detenida" -ForegroundColor DarkGray
        Start-Sleep -Seconds 2
    } else {
        Write-Host "   la tarea no estaba corriendo (estado: $($tarea.State))" -ForegroundColor DarkGray
    }
} else {
    Write-Host "   no hay tarea programada, seguimos" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 2. Liberar el puerto
# ---------------------------------------------------------------------------
Write-Host "2. Liberando el puerto $Puerto..." -ForegroundColor Cyan

$ocupantes = Quien-Tiene-El-Puerto -Puerto $Puerto
$accesoDenegado = $false

if (-not $ocupantes) {
    Write-Host "   ya estaba libre" -ForegroundColor Green
} else {
    foreach ($idProc in $ocupantes) {
        $proc = Get-Process -Id $idProc -ErrorAction SilentlyContinue
        $nombre = if ($proc) { $proc.ProcessName } else { "desconocido" }
        Write-Host "   PID $idProc ($nombre)" -ForegroundColor Yellow

        # Sin SilentlyContinue: queremos VER el "Acceso denegado" si ocurre.
        # Callarlo fue justo lo que hizo que el script anterior mintiera.
        try {
            Stop-Process -Id $idProc -Force -ErrorAction Stop
            Write-Host "     muerto" -ForegroundColor DarkGray
        } catch {
            Write-Host "     no pude: $($_.Exception.Message)" -ForegroundColor Red
            # Segundo intento con taskkill, que a veces llega donde Stop-Process no.
            $salida = & taskkill /F /T /PID $idProc 2>&1
            Write-Host "     taskkill: $salida" -ForegroundColor DarkGray
            if ($salida -match "denegado|denied|Acceso") { $accesoDenegado = $true }
        }
    }
}

# Por si quedo algun python de Jarvis sin el puerto (un arranque a medias).
Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*uvicorn*server:app*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$libre = Esperar-Puerto-Libre -Puerto $Puerto

# ---------------------------------------------------------------------------
# 2b. Si resistio, relanzarnos como administrador
# ---------------------------------------------------------------------------
if (-not $libre -and -not $elevado) {
    Write-Host ""
    Write-Host "   El proceso corre ELEVADO y esta consola no lo es." -ForegroundColor Yellow
    Write-Host "   Me relanzo como administrador. Acepta el aviso de Windows." -ForegroundColor Yellow
    Write-Host ""

    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"" `
        -Verb RunAs
    exit 0
}

if (-not $libre) {
    Write-Host ""
    Write-Host "   EL PUERTO $Puerto SIGUE OCUPADO incluso como administrador" -ForegroundColor Red
    Write-Host "   Mira quien es exactamente:"
    Write-Host "     Get-NetTCPConnection -LocalPort $Puerto -State Listen |"
    Write-Host "       ForEach-Object { Get-Process -Id `$_.OwningProcess } | Select Id, ProcessName, Path"
    Write-Host "   Si no es Jarvis, cambia JARVIS_PUERTO en el .env y en el tunel."
    exit 1
}

Write-Host "   puerto libre" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 3. Borrar la cache de Python
# ---------------------------------------------------------------------------
Write-Host "3. Limpiando la cache de Python..." -ForegroundColor Cyan
Get-ChildItem -Path $RaizProyecto -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host "   hecho" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# 4. Arrancar
# ---------------------------------------------------------------------------
Write-Host "4. Arrancando..." -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $CarpetaLogs | Out-Null
$LogErr = Join-Path $CarpetaLogs "servidor.err.log"

# Vaciamos el log de errores para que lo que leamos luego sea de ESTE arranque
# y no un error de hace tres horas.
Set-Content -Path $LogErr -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

if ($tarea) {
    Start-ScheduledTask -TaskName $NombreTarea
    Write-Host "   lanzado desde la tarea programada" -ForegroundColor DarkGray
} else {
    Start-Process -FilePath "py" `
        -ArgumentList "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", $Puerto `
        -WorkingDirectory $RaizProyecto -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $CarpetaLogs "servidor.log") `
        -RedirectStandardError  $LogErr
    Write-Host "   lanzado directamente" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 5. Esperar a que responda
# ---------------------------------------------------------------------------
Write-Host "5. Esperando a que responda..." -ForegroundColor Cyan

$vivo = $false
for ($i = 0; $i -lt 25; $i++) {
    Start-Sleep -Seconds 2
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Puerto/jarvis" -UseBasicParsing -TimeoutSec 2 | Out-Null
        $vivo = $true
        break
    } catch { }
}

if (-not $vivo) {
    Write-Host "   NO responde en el puerto $Puerto" -ForegroundColor Red
    if (Test-Path $LogErr) {
        Write-Host ""
        Write-Host "   Ultimas lineas del error:" -ForegroundColor Yellow
        Get-Content $LogErr -Tail 15 -Encoding UTF8 | ForEach-Object { Write-Host "     $_" -ForegroundColor DarkGray }
    }
    exit 1
}

Write-Host "   responde" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 6. Comprobar que es el codigo NUEVO
# ---------------------------------------------------------------------------
# Que algo conteste en el 8000 no prueba nada: el que contesta puede ser el
# proceso viejo que nunca murio. El sello lo zanja.
$SELLO_ESPERADO = "2026-08-22-gpu-libre-29"

Write-Host ""
Write-Host "6. Comprobando la version que esta corriendo..." -ForegroundColor Cyan
Write-Host ""

$ok = $false
try {
    $salud = Invoke-RestMethod -Uri "http://127.0.0.1:$Puerto/salud" -TimeoutSec 8

    $sello = $salud.sello
    if (-not $sello) { $sello = "(sin sello: es codigo viejo)" }

    Write-Host "   Sello              : $sello"
    Write-Host "   Sesion continua    : $($salud.sesion_continua)"
    Write-Host "   Modo actual        : $($salud.modo)"
    Write-Host "   Modelo             : $($salud.perfil.modelo)"
    Write-Host "   Ollama conectado   : $($salud.ollama.conectado)"

    # Si Comet sale "NO ENCONTRADO", "abre comet" fallaria con el sonido de
    # error de Windows. Mejor verlo aqui que descubrirlo hablandole a Alexa.
    $rutaComet = $salud.rutas.comet
    if ($rutaComet) {
        Write-Host "   Comet              : $rutaComet" -ForegroundColor Green
    } else {
        Write-Host "   Comet              : NO ENCONTRADO (usaria el navegador por defecto)" -ForegroundColor Yellow
    }

    if ($salud.gpu.disponible) {
        $libreGb = [math]::Round($salud.gpu.vram_libre_mb / 1024, 1)
        $totalGb = [math]::Round($salud.gpu.vram_total_mb / 1024, 1)
        Write-Host "   GPU                : $libreGb de $totalGb GB libres"
    }

    $ok = ($sello -eq $SELLO_ESPERADO) -and ($salud.sesion_continua -eq $true)
} catch {
    Write-Host "   No pude leer /salud: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host ""
if ($ok) {
    Write-Host "   CODIGO ACTUAL EN MARCHA. La sesion continua esta activa." -ForegroundColor Green
    Write-Host ""
    Write-Host "   Prueba ahora:" -ForegroundColor Cyan
    Write-Host "     'Alexa, abre mi asistente'"
    Write-Host "     espera el saludo LARGO (te dira que le hables seguido)"
    Write-Host "     'como esta el cpu'  ...  'que hora es'  ...  'pausa'"
} else {
    Write-Host "   NO ES EL CODIGO QUE ESPERABA" -ForegroundColor Red
    Write-Host "   Esperaba el sello: $SELLO_ESPERADO"
    Write-Host "   Lo mas probable: otro python arrancado desde otra carpeta."
    Write-Host "   Miralos todos con:"
    Write-Host "     Get-CimInstance Win32_Process -Filter `"Name like '%python%'`" | Select ProcessId, CommandLine | Format-List"
}

# ---------------------------------------------------------------------------
# 6b. Calentar el tunel publico
# ---------------------------------------------------------------------------
# Este paso es el que evita el "La Skill solicitada no respondio correctamente"
# justo despues de reiniciar. El tunel sigue apuntando al proceso que acabamos
# de matar; hasta que alguien lo obliga a rehacer la conexion, las primeras
# peticiones de Amazon se pierden por el camino y NUNCA llegan al servidor
# (en el registro no aparece ningun LaunchRequest, solo el SessionEndedRequest
# con INVALID_RESPONSE que manda Alexa al rendirse).
#
# Golpearlo nosotros aqui cuesta unos segundos y te ahorra hablarle tres veces.
$TunelUrl = Leer-Env "JARVIS_TUNEL_URL" (Leer-Env "JARVIS_NGROK_URL" "")

if ($TunelUrl) {
    Write-Host ""
    Write-Host "6b. Calentando el camino publico del tunel..." -ForegroundColor Cyan

    # Ojo con la tentacion de hacer esto con Invoke-WebRequest a la URL: con
    # Tailscale corriendo, MagicDNS resuelve el nombre .ts.net a la IP interna
    # del tailnet. Esa peticion sale del equipo y vuelve a entrar sin tocar el
    # Funnel: mide algo que a Amazon no le sirve. El script de Python resuelve
    # por DNS publico y conecta contra la entrada real de Tailscale.
    Push-Location $RaizProyecto
    & py "scripts\calentar_tunel.py" 6 2>&1 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
    $calentado = ($LASTEXITCODE -eq 0)
    Pop-Location

    if ($calentado) {
        Write-Host "   tunel caliente" -ForegroundColor Green
    } else {
        Write-Host "   el camino publico sigue lento o caido" -ForegroundColor Yellow
        Write-Host "   Comprueba que el Funnel siga publicado:"
        Write-Host "     tailscale funnel status"
        Write-Host "   Si no aparece nada, vuelve a publicarlo:"
        Write-Host "     tailscale funnel --bg --https=443 localhost:$Puerto"
    }
} else {
    Write-Host ""
    Write-Host "6b. No hay JARVIS_TUNEL_URL en el .env, me salto el calentado." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 7. Ultimas lineas del arranque
# ---------------------------------------------------------------------------
if (Test-Path $LogJarvis) {
    Write-Host ""
    Write-Host "7. Ultimas lineas del arranque:" -ForegroundColor Cyan
    Write-Host ""
    # -Encoding UTF8 no es opcional: Python escribe el log en UTF-8 y
    # PowerShell 5.1 lo lee en la pagina de codigos del sistema si no se le
    # dice. Sin esto salia "Camino al tAonel" y "MenAo Inicio", que parece un
    # fallo del arranque cuando en realidad solo es la consola leyendo mal.
    Get-Content $LogJarvis -Tail 18 -Encoding UTF8 | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
}

Write-Host ""
