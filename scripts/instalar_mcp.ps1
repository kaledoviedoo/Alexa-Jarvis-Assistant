# ============================================================================
#  instalar_mcp.ps1  -  Herramientas externas por MCP (Fase 7.3). Todo gratis.
#
#     .\scripts\instalar_mcp.ps1
#
#  Instala:
#    - mcp-server-fetch        para leer paginas web enteras
#    - mcp-youtube-transcript  para leer los subtitulos de un video de YouTube
#    - github-mcp-server       el oficial de GitHub, que Jarvis usa en modo
#                              SOLO LECTURA (no puede crear ni borrar nada)
#
#  Ninguno queda corriendo: Jarvis los arranca cuando una orden los necesita
#  y los apaga a los 10 minutos sin uso.
# ============================================================================
$ErrorActionPreference = "Continue"
$Raiz = Split-Path $PSScriptRoot -Parent
$Carpeta = Join-Path $env:USERPROFILE ".jarvis\mcp"
New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  HERRAMIENTAS EXTERNAS (MCP) PARA JARVIS" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

$i = 1
foreach ($p in @("mcp-server-fetch", "mcp-youtube-transcript")) {
    Write-Host ""
    Write-Host "$i. Instalando $p..." -ForegroundColor White
    & py -m pip install --quiet --upgrade $p
    if ($LASTEXITCODE -eq 0) { Write-Host "   listo" -ForegroundColor Green }
    else { Write-Host "   FALLO. A mano:  py -m pip install $p" -ForegroundColor Red }
    $i++
}

Write-Host ""
Write-Host "$i. Bajando el servidor oficial de GitHub..." -ForegroundColor White
try {
    $ultima = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/github/github-mcp-server/releases/latest"
    $zip = $ultima.assets | Where-Object { $_.name -match "Windows_x86_64\.zip$" } | Select-Object -First 1
    if (-not $zip) { throw "no encontre la version para Windows en $($ultima.tag_name)" }
    $destino = Join-Path $env:TEMP $zip.name
    Invoke-WebRequest -UseBasicParsing $zip.browser_download_url -OutFile $destino
    Expand-Archive -Force $destino -DestinationPath $Carpeta
    Remove-Item $destino -ErrorAction SilentlyContinue
    if (Test-Path (Join-Path $Carpeta "github-mcp-server.exe")) {
        Write-Host "   listo ($($ultima.tag_name))" -ForegroundColor Green
    } else { throw "el zip no traia github-mcp-server.exe" }
} catch {
    Write-Host "   No se pudo: $_" -ForegroundColor Red
    Write-Host "   Bajalo a mano de https://github.com/github/github-mcp-server/releases" -ForegroundColor DarkGray
    Write-Host "   y deja github-mcp-server.exe en $Carpeta" -ForegroundColor DarkGray
}

$envArchivo = Join-Path $Raiz ".env"
$tieneToken = (Test-Path $envArchivo) -and (Select-String -Path $envArchivo -Pattern "^JARVIS_GITHUB_TOKEN=\S+" -Quiet)
Write-Host ""
if ($tieneToken) {
    Write-Host "El token de GitHub ya esta en el .env." -ForegroundColor Green
} else {
    Write-Host "FALTA UN PASO para GitHub (las otras dos ya funcionan):" -ForegroundColor Yellow
    Write-Host "  1. Entra a https://github.com/settings/personal-access-tokens/new" -ForegroundColor White
    Write-Host "  2. Token 'fine-grained', caducidad 90 dias, 'Only select repositories' o 'All repositories'." -ForegroundColor White
    Write-Host "  3. Permisos de repositorio en SOLO LECTURA (Read-only): Actions, Contents, Issues," -ForegroundColor White
    Write-Host "     Metadata y Pull requests. Nada de escritura." -ForegroundColor White
    Write-Host "  4. Copia el token y anadelo al .env, con tu repositorio de siempre:" -ForegroundColor White
    Write-Host "       JARVIS_GITHUB_TOKEN=github_pat_..." -ForegroundColor Cyan
    Write-Host "       JARVIS_GITHUB_REPO=tu-usuario/tu-repositorio" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "Ahora reinicia Jarvis:  .\reiniciar_jarvis.ps1" -ForegroundColor Cyan
Write-Host "Y prueba en el chat: 'resume este video <enlace de YouTube>'." -ForegroundColor Cyan
Write-Host ""
