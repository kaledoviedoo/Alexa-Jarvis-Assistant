# ============================================================================
#  idioma_ocr.ps1
#  Anade el espanol a Tesseract.
#
#     .\idioma_ocr.ps1
#
#  Por que hace falta: winget instala Tesseract con el ingles y nada mas. El
#  instalador grafico de UB-Mannheim si deja marcar idiomas, pero winget lo
#  ejecuta en silencio con las opciones por defecto, asi que esa pantalla no
#  llega a aparecer.
#
#  Sin el espanol, el OCR se atraganta con las tildes: donde la pantalla pone
#  una vocal acentuada, escupe simbolos raros o se salta la palabra entera.
#
#  Lo unico que falta es un archivo de datos. No hay que reinstalar nada.
# ============================================================================

$ErrorActionPreference = "Continue"

# Usamos tessdata_fast y no el normal a proposito. Pesa 1,5 MB en vez de 15 y
# reconoce mas rapido, a cambio de un pelin menos de precision. Para leer una
# pantalla de ordenador (texto limpio, nitido, generado por el sistema) la
# diferencia no se nota, y aqui el reloj de Alexa manda.
$UrlIdioma = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/spa.traineddata"

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  ANADIENDO EL ESPANOL A TESSERACT" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Localizar Tesseract
# ---------------------------------------------------------------------------
$rutas = @(
    "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
    "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe",
    "$env:LOCALAPPDATA\Tesseract-OCR\tesseract.exe"
)

$tesseract = ""
foreach ($r in $rutas) { if (Test-Path $r) { $tesseract = $r; break } }
if (-not $tesseract) {
    $enPath = Get-Command tesseract.exe -ErrorAction SilentlyContinue
    if ($enPath) { $tesseract = $enPath.Source }
}

if (-not $tesseract) {
    Write-Host "No encuentro Tesseract. Instalalo primero:" -ForegroundColor Red
    Write-Host "  winget install UB-Mannheim.TesseractOCR"
    exit 1
}

Write-Host "Tesseract: $tesseract" -ForegroundColor DarkGray

$carpetaDatos = Join-Path (Split-Path -Parent $tesseract) "tessdata"
if (-not (Test-Path $carpetaDatos)) {
    New-Item -ItemType Directory -Force -Path $carpetaDatos | Out-Null
}
Write-Host "Datos    : $carpetaDatos" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# 2. Ya lo tiene?
# ---------------------------------------------------------------------------
$idiomas = & $tesseract --list-langs 2>&1 | Out-String
if ($idiomas -match "\bspa\b") {
    Write-Host "El espanol YA estaba instalado. No hay nada que hacer." -ForegroundColor Green
    Write-Host ""
    exit 0
}

Write-Host "Falta el espanol. Descargando (1,5 MB)..." -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 3. Descargar
# ---------------------------------------------------------------------------
# A temporal primero. Si escribieramos directo sobre tessdata y la descarga se
# cortara a medias, quedaria un archivo roto que Tesseract intentaria cargar,
# y el error de eso es mucho mas confuso que "no esta el idioma".
$temporal = Join-Path $env:TEMP "spa.traineddata"

try {
    # Sin la barra de progreso la descarga va bastante mas rapida.
    $progresoAnterior = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $UrlIdioma -OutFile $temporal -UseBasicParsing -TimeoutSec 120
    $ProgressPreference = $progresoAnterior
} catch {
    Write-Host "No pude descargarlo: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Hazlo a mano:" -ForegroundColor Yellow
    Write-Host "  1. Abre $UrlIdioma"
    Write-Host "  2. Guarda el archivo en $carpetaDatos"
    exit 1
}

$tamano = (Get-Item $temporal).Length
if ($tamano -lt 500000) {
    Write-Host "El archivo bajo incompleto ($tamano bytes). Reintenta." -ForegroundColor Red
    Remove-Item $temporal -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "Descargado: $([math]::Round($tamano / 1MB, 1)) MB" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 4. Copiarlo donde va (puede pedir permisos)
# ---------------------------------------------------------------------------
$destino = Join-Path $carpetaDatos "spa.traineddata"

try {
    Copy-Item $temporal $destino -Force -ErrorAction Stop
    Write-Host "Copiado a tessdata" -ForegroundColor Green
} catch {
    # tessdata suele vivir en Archivos de programa, y ahi no se escribe sin
    # permisos de administrador. En vez de fallar, nos elevamos solo para la
    # copia: es un comando y se acaba.
    Write-Host "Necesito permisos para escribir ahi. Acepta el aviso de Windows." -ForegroundColor Yellow

    $orden = "Copy-Item '$temporal' '$destino' -Force"
    Start-Process -FilePath "powershell.exe" -Verb RunAs -Wait `
        -ArgumentList "-NoProfile", "-WindowStyle", "Hidden", "-Command", $orden

    if (-not (Test-Path $destino)) {
        Write-Host "No se pudo copiar. Hazlo a mano:" -ForegroundColor Red
        Write-Host "  copia $temporal"
        Write-Host "  a     $carpetaDatos"
        exit 1
    }
    Write-Host "Copiado a tessdata" -ForegroundColor Green
}

Remove-Item $temporal -Force -ErrorAction SilentlyContinue

# ---------------------------------------------------------------------------
# 5. Comprobar de verdad
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Comprobando..." -ForegroundColor Cyan

$idiomas = & $tesseract --list-langs 2>&1 | Out-String
if ($idiomas -match "\bspa\b") {
    Write-Host "  El espanol ya esta disponible." -ForegroundColor Green
    Write-Host ""
    Write-Host "Reinicia Jarvis para que lo use:" -ForegroundColor Cyan
    Write-Host "  .\reiniciar_jarvis.ps1"
} else {
    Write-Host "  El archivo esta copiado pero Tesseract sigue sin verlo." -ForegroundColor Yellow
    Write-Host "  Comprueba si la variable TESSDATA_PREFIX apunta a otra carpeta:"
    Write-Host "    `$env:TESSDATA_PREFIX"
    Write-Host "  Idiomas que ve ahora mismo:"
    Write-Host "    $($idiomas.Trim())"
}

Write-Host ""
