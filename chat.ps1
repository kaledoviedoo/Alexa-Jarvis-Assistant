# ============================================================================
#  chat.ps1  -  Abre el chat de Jarvis (Fase 6).
#
#     .\chat              abre el chat en este PC, ya vinculado
#     .\chat -Celular     (PowerShell como administrador) deja entrar al
#                         celular por Tailscale y te dice la direccion
#
#  El chat solo responde desde este PC o desde tu red de Tailscale. Por el
#  Funnel publico (lo que usa Alexa) no existe.
# ============================================================================
param([switch]$Celular)

$Raiz = $PSScriptRoot
$env_ = Join-Path $Raiz ".env"
$linea = Select-String -Path $env_ -Pattern '^JARVIS_TOKEN_LOCAL=' | Select-Object -First 1
if (-not $linea) {
    Write-Host "No encuentro JARVIS_TOKEN_LOCAL en el .env." -ForegroundColor Red
    exit 1
}
$token  = $linea.Line.Split('=', 2)[1].Trim().Trim('"').Trim("'")
$puerto = 8000
$lineaPuerto = Select-String -Path $env_ -Pattern '^JARVIS_PUERTO=' | Select-Object -First 1
if ($lineaPuerto) { $puerto = $lineaPuerto.Line.Split('=', 2)[1].Trim() }

if ($Celular) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Write-Host "Para abrirle la puerta al celular hace falta PowerShell como administrador." -ForegroundColor Yellow
        exit 1
    }
    $nombre = "Jarvis chat (Tailscale)"
    if (Get-NetFirewallRule -DisplayName $nombre -ErrorAction SilentlyContinue) {
        Write-Host "La regla del firewall ya existe." -ForegroundColor DarkGray
    } else {
        # Solo la red de Tailscale (100.64.0.0/10): ni el wifi de casa ni internet.
        New-NetFirewallRule -DisplayName $nombre -Direction Inbound -Protocol TCP `
            -LocalPort $puerto -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null
        Write-Host "Regla creada: el puerto $puerto acepta conexiones solo desde Tailscale." -ForegroundColor Green
    }
    $ip = (& tailscale ip -4 2>$null | Select-Object -First 1)
    Write-Host ""
    if ($ip) {
        Write-Host "  En el celular, con Tailscale abierto, entra a:" -ForegroundColor White
        Write-Host "     http://${ip}:$puerto/chat" -ForegroundColor Cyan
    } else {
        Write-Host "  No encontre la IP de Tailscale. Esta Tailscale abierto en el PC?" -ForegroundColor Yellow
    }
    Write-Host "  Luego, en el chat del PC, pulsa 'Vincular celular' y escribe el codigo."
    Write-Host ""
}

Start-Process "http://localhost:$puerto/chat?llave=$token"
