# ============================================================================
#  instalar_autoarranque.ps1
#  Registra Jarvis en el Programador de tareas de Windows.
#
#  EJECUTAR COMO ADMINISTRADOR:
#     Clic derecho en PowerShell > "Ejecutar como administrador"
#     cd C:\ruta\a\jarvis\scripts
#     .\instalar_autoarranque.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

# --- Comprobar permisos de administrador ---
$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: hay que ejecutar este script como administrador." -ForegroundColor Red
    Write-Host "Cierra esta ventana, abre PowerShell con clic derecho > 'Ejecutar como administrador' y vuelve a intentarlo."
    exit 1
}

$NombreTarea   = "Jarvis"
$RaizProyecto  = Split-Path -Parent $PSScriptRoot
$ScriptArranque = Join-Path $PSScriptRoot "iniciar_jarvis.ps1"

if (-not (Test-Path $ScriptArranque)) {
    Write-Host "ERROR: no encuentro iniciar_jarvis.ps1 en $PSScriptRoot" -ForegroundColor Red
    exit 1
}

Write-Host "Instalando la tarea programada '$NombreTarea'..." -ForegroundColor Cyan
Write-Host "  Proyecto: $RaizProyecto"

# Si ya existe, la quitamos para reinstalarla limpia.
$existente = Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
if ($existente) {
    Write-Host "  Ya existía una tarea con ese nombre, la reemplazo."
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
}

# - WindowStyle Hidden y -NonInteractive: nada de ventanas negras al arrancar.
$accion = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptArranque`"" `
    -WorkingDirectory $RaizProyecto

# Al iniciar sesión (no al arrancar Windows): Jarvis necesita una sesión de
# escritorio activa para controlar teclado, mouse y abrir aplicaciones.
$disparador = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# 30 segundos de margen para que el escritorio termine de cargar.
$disparador.Delay = "PT30S"

$ajustes = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

# Nivel más alto: hace falta para powercfg (cambio de plan de energía).
$principalTarea = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $NombreTarea `
    -Action $accion `
    -Trigger $disparador `
    -Settings $ajustes `
    -Principal $principalTarea `
    -Description "Arranca el asistente Jarvis (Ollama + FastAPI + túnel de ngrok) al iniciar sesión." | Out-Null

Write-Host ""
Write-Host "Listo. Jarvis arrancará solo al iniciar sesión." -ForegroundColor Green
Write-Host ""
Write-Host "Comandos útiles:"
Write-Host "  Probarla ahora sin reiniciar :  Start-ScheduledTask -TaskName Jarvis"
Write-Host "  Ver su estado                :  Get-ScheduledTask -TaskName Jarvis"
Write-Host "  Desinstalarla                :  .\desinstalar_autoarranque.ps1"
Write-Host "  Ver los registros            :  Get-Content `"$env:USERPROFILE\.jarvis\logs\arranque.log`" -Tail 30"
Write-Host ""

$respuesta = Read-Host "¿Quieres arrancar Jarvis ahora mismo para probar? (s/n)"
if ($respuesta -eq "s") {
    Start-ScheduledTask -TaskName $NombreTarea
    Write-Host "Arrancando... dale unos 30 segundos y abre http://localhost:8000/salud" -ForegroundColor Cyan
}
