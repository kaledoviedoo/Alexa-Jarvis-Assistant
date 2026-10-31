# ============================================================================
#  vigilante.ps1
#  Comprueba que Jarvis siga vivo y lo levanta si no.
#
#     .\vigilante.ps1              comprueba una vez
#     .\vigilante.ps1 -Instalar    lo deja corriendo cada 5 minutos
#     .\vigilante.ps1 -Quitar      lo desinstala
#
#  Por que hace falta: la tarea de autoarranque solo se dispara al iniciar
#  sesion. Si el proceso se muere a media tarde, no hay nada que lo levante.
#  El 16 de septiembre paso exactamente eso y estuvo caido dos horas, con el
#  tunel publicado y todo, hasta que se reinicio a mano.
#
#  RestartCount en la tarea no cubre este caso: solo actua cuando falla la
#  TAREA, no cuando se cae el proceso que la tarea lanzo.
# ============================================================================

param(
    [switch]$Instalar,
    [switch]$Quitar,
    [int]$CadaMinutos = 5
)

$ErrorActionPreference = "Continue"
$RaizProyecto = Split-Path -Parent $PSScriptRoot
$NombreTarea  = "JarvisVigilante"

function Leer-Env {
    param([string]$Clave, [string]$PorDefecto = "")
    $rutaEnv = Join-Path $RaizProyecto ".env"
    if (-not (Test-Path $rutaEnv)) { return $PorDefecto }
    foreach ($linea in Get-Content $rutaEnv -Encoding UTF8) {
        if ($linea -match "^\s*$Clave\s*=\s*(.+?)\s*$") { return $Matches[1] }
    }
    return $PorDefecto
}

function Escribir-Registro {
    param([string]$Texto)
    $carpeta = Join-Path $env:USERPROFILE ".jarvis"
    if (-not (Test-Path $carpeta)) { New-Item -ItemType Directory -Force -Path $carpeta | Out-Null }
    $sello = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path (Join-Path $carpeta "vigilante.log") -Encoding UTF8 -Value "$sello  $Texto"
}

# ---------------------------------------------------------------------------
# Instalar / quitar
# ---------------------------------------------------------------------------
if ($Quitar) {
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Vigilante desinstalado." -ForegroundColor Green
    exit 0
}

if ($Instalar) {
    $identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identidad)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "Hace falta administrador para registrar la tarea. Elevando..." -ForegroundColor Yellow
        Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList `
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Instalar"
        exit 0
    }

    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false -ErrorAction SilentlyContinue

    $accion = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""

    # Repeticion indefinida desde el inicio de sesion. Empieza con dos minutos
    # de margen para no pelearse con el arranque normal de Jarvis.
    $disparador = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $disparador.Delay = "PT2M"
    $disparador.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $CadaMinutos) `
        -RepetitionDuration ([TimeSpan]::MaxValue)).Repetition

    $ajustes = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    $principalTarea = New-ScheduledTaskPrincipal `
        -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

    Register-ScheduledTask -TaskName $NombreTarea -Action $accion `
        -Trigger $disparador -Settings $ajustes -Principal $principalTarea `
        -Description "Comprueba cada $CadaMinutos min que Jarvis responde y lo reinicia si no." | Out-Null

    Write-Host ""
    Write-Host "Vigilante instalado: comprueba cada $CadaMinutos minutos." -ForegroundColor Green
    Write-Host "  Ver su estado  :  Get-ScheduledTask -TaskName $NombreTarea"
    Write-Host "  Su registro    :  $env:USERPROFILE\.jarvis\vigilante.log"
    Write-Host "  Probarlo ahora :  Start-ScheduledTask -TaskName $NombreTarea"
    Write-Host ""
    exit 0
}

# ---------------------------------------------------------------------------
# La comprobacion
# ---------------------------------------------------------------------------
$Puerto = Leer-Env "JARVIS_PUERTO" "8000"
$url    = "http://127.0.0.1:$Puerto/salud"

# Se pregunta por LOCALHOST y no por el tunel a proposito. Si el tunel esta
# caido pero Jarvis vive, reiniciar no arregla nada y ademas corta una sesion
# que podria estar en curso. Aqui solo nos importa si el proceso responde.
$vivo = $false
try {
    $r = Invoke-WebRequest -Uri $url -TimeoutSec 8 -UseBasicParsing
    $vivo = ($r.StatusCode -eq 200)
} catch {
    $vivo = $false
    $motivo = $_.Exception.Message
}

if ($vivo) {
    # En silencio: si escribiera una linea cada cinco minutos, el registro
    # seria inservible justo el dia que haga falta leerlo.
    exit 0
}

Escribir-Registro "Jarvis no responde en $url ($motivo). Reiniciando."

$reinicio = Join-Path $PSScriptRoot "reiniciar_jarvis.ps1"
if (-not (Test-Path $reinicio)) {
    Escribir-Registro "ERROR: no encuentro $reinicio"
    exit 1
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $reinicio *> $null

Start-Sleep -Seconds 12

try {
    $r = Invoke-WebRequest -Uri $url -TimeoutSec 8 -UseBasicParsing
    if ($r.StatusCode -eq 200) {
        Escribir-Registro "Levantado correctamente."
        exit 0
    }
} catch { }

Escribir-Registro "SIGUE SIN RESPONDER despues del reinicio. Hace falta mirarlo a mano."
exit 1
