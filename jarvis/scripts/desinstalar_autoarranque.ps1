# ============================================================================
#  desinstalar_autoarranque.ps1
#  Quita Jarvis del arranque automático y detiene sus procesos.
#  Ejecutar como administrador.
# ============================================================================

$ErrorActionPreference = "Continue"
$NombreTarea = "Jarvis"

$identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identidad)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: ejecuta este script como administrador." -ForegroundColor Red
    exit 1
}

$tarea = Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
if ($tarea) {
    Stop-ScheduledTask  -TaskName $NombreTarea -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
    Write-Host "Tarea programada eliminada." -ForegroundColor Green
} else {
    Write-Host "No había ninguna tarea programada llamada '$NombreTarea'."
}

Write-Host "Deteniendo procesos..."

# El servidor de Jarvis: solo matamos los python que corren uvicorn con server:app,
# para no llevarnos por delante otros scripts de Python que tengas abiertos.
Get-CimInstance Win32_Process -Filter "Name like '%python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*uvicorn*server:app*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "  Servidor detenido (PID $($_.ProcessId))"
    }

Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Write-Host "  Túnel de ngrok detenido (PID $($_.Id))"
}

Write-Host ""
Write-Host "Jarvis ya no arrancará solo." -ForegroundColor Green
Write-Host "Ollama se deja corriendo por si lo usas para otras cosas."
Write-Host "Si también quieres pararlo:  Stop-Process -Name ollama -Force"
