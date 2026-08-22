# ============================================================================
#  instalar_extras.ps1
#  Instala lo que hace falta para el correo, la pantalla y Teams.
#
#     .\instalar_extras.ps1
#
#  Se separa del instalador principal a proposito: son capacidades opcionales
#  y pesadas. Si algo de esto falta, Jarvis sigue funcionando igual y solo te
#  dice que no puede con esa orden concreta, en vez de no arrancar.
# ============================================================================

$ErrorActionPreference = "Continue"
$RaizProyecto = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  EXTRAS DE JARVIS: correo, pantalla y Teams" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Paquetes de Python
# ---------------------------------------------------------------------------
Write-Host "1. Paquetes de Python..." -ForegroundColor Cyan
Write-Host ""

$paquetes = @(
    @{ nombre = "pywin32";     modulo = "win32com";    para = "leer Outlook sin nube ni permisos" },
    @{ nombre = "mss";         modulo = "mss";         para = "capturar la pantalla rapido" },
    @{ nombre = "pytesseract"; modulo = "pytesseract"; para = "leer el texto de la pantalla" }
)

foreach ($p in $paquetes) {
    & py -c "import $($p.modulo)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ya estaba: $($p.nombre)" -ForegroundColor DarkGray
        continue
    }

    Write-Host "   instalando $($p.nombre) ($($p.para))..." -ForegroundColor Yellow
    & py -m pip install --quiet --upgrade $p.nombre

    & py -c "import $($p.modulo)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "     listo" -ForegroundColor Green
    } else {
        Write-Host "     FALLO. Pruebalo a mano:  py -m pip install $($p.nombre)" -ForegroundColor Red
    }
}

# pywin32 necesita un paso extra que casi nadie recuerda: registrar sus DLLs.
# Sin esto, importar win32com funciona pero Dispatch("Outlook.Application")
# falla con un error de COM que no dice nada util.
Write-Host ""
Write-Host "   registrando pywin32..." -ForegroundColor DarkGray
& py -m pywin32_postinstall -install 2>$null | Out-Null

# ---------------------------------------------------------------------------
# 2. Tesseract (el motor de OCR)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "2. Tesseract, el motor que lee la pantalla..." -ForegroundColor Cyan

$rutasTesseract = @(
    "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
    "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe",
    "$env:LOCALAPPDATA\Tesseract-OCR\tesseract.exe"
)

$tesseract = ""
foreach ($ruta in $rutasTesseract) {
    if (Test-Path $ruta) { $tesseract = $ruta; break }
}
if (-not $tesseract) {
    $enPath = Get-Command tesseract.exe -ErrorAction SilentlyContinue
    if ($enPath) { $tesseract = $enPath.Source }
}

if ($tesseract) {
    Write-Host "   encontrado: $tesseract" -ForegroundColor Green

    # Comprobamos el idioma espanol. Sin el, el OCR lee los acentos como
    # simbolos raros y las frases salen destrozadas.
    $idiomas = & $tesseract --list-langs 2>&1 | Out-String
    if ($idiomas -match "spa") {
        Write-Host "   idioma espanol: instalado" -ForegroundColor Green
    } else {
        Write-Host "   idioma espanol: NO esta" -ForegroundColor Yellow
        Write-Host "   Sin el, los acentos se leen mal. Vuelve a pasar el instalador"
        Write-Host "   de Tesseract y marca Spanish en 'Additional language data'."
    }
} else {
    Write-Host "   NO esta instalado" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Descargalo de:" -ForegroundColor White
    Write-Host "     https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor White
    Write-Host ""
    Write-Host "   Durante la instalacion, despliega 'Additional language data'"
    Write-Host "   y marca Spanish. Luego vuelve a correr este script."
    Write-Host ""
    Write-Host "   O con winget, si lo tienes:" -ForegroundColor DarkGray
    Write-Host "     winget install UB-Mannheim.TesseractOCR" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 3. Modelo de vision
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "3. Modelo de vision (solo para 'describe la pantalla')..." -ForegroundColor Cyan

$modelos = & ollama list 2>$null | Out-String
if ($modelos -match "llava") {
    Write-Host "   ya lo tienes" -ForegroundColor Green
} else {
    Write-Host "   no esta. Son unos 4 GB de descarga." -ForegroundColor Yellow
    Write-Host "   Es OPCIONAL: sin el, todo lo demas funciona igual y solo"
    Write-Host "   'describe la pantalla' te dira que falta el modelo."
    Write-Host ""
    $respuesta = Read-Host "   Descargarlo ahora? (s/N)"
    if ($respuesta -match "^[sSyY]") {
        & ollama pull llava:7b
    } else {
        Write-Host "   Saltado. Cuando quieras:  ollama pull llava:7b" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# 4. Outlook y Teams
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "4. Aplicaciones..." -ForegroundColor Cyan

$outlook = Get-Command outlook.exe -ErrorAction SilentlyContinue
if (-not $outlook) {
    $rutasOutlook = @(
        "$env:ProgramFiles\Microsoft Office\root\Office16\OUTLOOK.EXE",
        "${env:ProgramFiles(x86)}\Microsoft Office\root\Office16\OUTLOOK.EXE"
    )
    foreach ($r in $rutasOutlook) { if (Test-Path $r) { $outlook = $r; break } }
}

if ($outlook) {
    Write-Host "   Outlook de escritorio: instalado" -ForegroundColor Green
    Write-Host "   (dejalo abierto; leer el correo con Outlook cerrado es mas lento)" -ForegroundColor DarkGray
} else {
    Write-Host "   Outlook de escritorio: NO encontrado" -ForegroundColor Yellow
    Write-Host "   Las ordenes de correo no funcionaran. Ojo: la version web de"
    Write-Host "   Outlook no vale, hace falta la aplicacion de escritorio."
}

if (Test-Path "$env:LOCALAPPDATA\Microsoft\Teams" -PathType Container) {
    Write-Host "   Teams: instalado" -ForegroundColor Green
} else {
    $teamsNuevo = Get-AppxPackage -Name "MSTeams" -ErrorAction SilentlyContinue
    if ($teamsNuevo) {
        Write-Host "   Teams: instalado (version nueva)" -ForegroundColor Green
    } else {
        Write-Host "   Teams: no lo encuentro, pero los enlaces msteams: pueden funcionar igual" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# 5. Guardar la ruta de Tesseract en el .env
# ---------------------------------------------------------------------------
if ($tesseract) {
    $rutaEnv = Join-Path $RaizProyecto ".env"
    $contenido = if (Test-Path $rutaEnv) { Get-Content $rutaEnv -Raw -Encoding UTF8 } else { "" }

    if ($contenido -notmatch "JARVIS_TESSERACT_EXE\s*=") {
        Write-Host ""
        Write-Host "5. Guardando la ruta de Tesseract en el .env..." -ForegroundColor Cyan
        Add-Content -Path $rutaEnv -Encoding UTF8 -Value @"

# Ruta de Tesseract, detectada por instalar_extras.ps1
JARVIS_TESSERACT_EXE=$tesseract
"@
        Write-Host "   guardada" -ForegroundColor Green
    }
}

# ---------------------------------------------------------------------------
# 6. Prueba de verdad
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "6. Probando de verdad, no solo comprobando que importe..." -ForegroundColor Cyan
Write-Host ""

Push-Location $RaizProyecto
& py -c @"
import sys
sys.path.insert(0, '.')

from tools import correo, pantalla

print('   CORREO  :', correo.correos_sin_leer()[:88])
print('   PANTALLA:', pantalla.leer_pantalla(maximo_lineas=2)[:88])
"@
Pop-Location

Write-Host ""
Write-Host "Si arriba ves respuestas reales, ya funciona." -ForegroundColor Green
Write-Host "Reinicia Jarvis para que cargue lo nuevo:" -ForegroundColor Cyan
Write-Host "  .\reiniciar_jarvis.ps1"
Write-Host ""
