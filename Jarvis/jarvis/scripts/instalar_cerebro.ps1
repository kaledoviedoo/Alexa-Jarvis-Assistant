# ============================================================================
#  instalar_cerebro.ps1
#  Lo que hace falta para la memoria semantica y el archivado automatico.
#
#     .\instalar_cerebro.ps1
#
#  Son tres cosas y ninguna es obligatoria: sin ellas Jarvis arranca igual y
#  solo te dice que no puede con esas ordenes concretas.
# ============================================================================

$ErrorActionPreference = "Continue"
$RaizProyecto = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  SEGUNDO CEREBRO: memoria semantica y archivado" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host ""

# ---------------------------------------------------------------------------
# 1. El modelo de vectores
# ---------------------------------------------------------------------------
Write-Host "1. Modelo de vectores (nomic-embed-text)..." -ForegroundColor Cyan
Write-Host ""
Write-Host "   Es lo que convierte cada nota en numeros para poder buscar por" -ForegroundColor DarkGray
Write-Host "   significado. Son 270 MB, mucho menos que un modelo de chat." -ForegroundColor DarkGray
Write-Host ""

$modelos = & ollama list 2>$null | Out-String
if ($modelos -match "nomic-embed-text") {
    Write-Host "   ya lo tienes" -ForegroundColor Green
} else {
    Write-Host "   descargando..." -ForegroundColor Yellow
    & ollama pull nomic-embed-text

    $modelos = & ollama list 2>$null | Out-String
    if ($modelos -match "nomic-embed-text") {
        Write-Host "   listo" -ForegroundColor Green
    } else {
        Write-Host "   FALLO. Pruebalo a mano:  ollama pull nomic-embed-text" -ForegroundColor Red
    }
}

# ---------------------------------------------------------------------------
# 2. Paquetes de Python
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "2. Paquetes de Python..." -ForegroundColor Cyan
Write-Host ""

$paquetes = @(
    @{ nombre = "numpy"; modulo = "numpy"; para = "comparar miles de vectores rapido" },
    @{ nombre = "pypdf"; modulo = "pypdf"; para = "leer el contenido de los PDF al archivarlos" }
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
        Write-Host "     FALLO. A mano:  py -m pip install $($p.nombre)" -ForegroundColor Red
    }
}

# ---------------------------------------------------------------------------
# 3. La boveda y sus convenciones
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "3. Tu boveda de Obsidian..." -ForegroundColor Cyan
Write-Host ""

Push-Location $RaizProyecto
& py -c @"
import sys
sys.path.insert(0, '.')

from tools import convenciones, obsidian

vault = obsidian.vault()
if vault is None:
    print('   NO encuentro la boveda.')
    print('   Ponla en el .env:  JARVIS_OBSIDIAN_VAULT=C:\\ruta\\a\\tu\\boveda')
    raise SystemExit(0)

print(f'   Boveda: {vault}')

reglas = convenciones.leer_reglas()
if reglas:
    print(f'   CLAUDE.md: {len(reglas)} caracteres leidos')
else:
    print('   CLAUDE.md: NO esta en la raiz de la boveda.')
    print('   Sin el, Jarvis usa unas convenciones por defecto que pueden no')
    print('   coincidir con como tienes organizadas tus carpetas.')

carpetas = convenciones.carpetas_reales()
if carpetas:
    print('   Carpetas reconocidas:')
    for tipo, ruta in sorted(carpetas.items()):
        print(f'     {tipo:12} -> {ruta.name}')
else:
    print('   No reconoci ninguna carpeta de las del CLAUDE.md.')
"@
Pop-Location

# ---------------------------------------------------------------------------
# 4. Indexar por primera vez
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "4. Primera indexacion..." -ForegroundColor Cyan
Write-Host ""
Write-Host "   Esto tarda: hay que pasar cada nota por el modelo. Una boveda de" -ForegroundColor DarkGray
Write-Host "   doscientas notas son unos dos minutos. Solo pasa la primera vez;" -ForegroundColor DarkGray
Write-Host "   despues solo se reindexa lo que cambies." -ForegroundColor DarkGray
Write-Host ""

$respuesta = Read-Host "   Indexar ahora? (S/n)"
if ($respuesta -notmatch "^[nN]") {
    Push-Location $RaizProyecto
    & py -c @"
import sys
sys.path.insert(0, '.')
from tools import memoria
print('  ', memoria.indexar())
"@
    Pop-Location
} else {
    Write-Host "   Saltado. Por voz: 'indexa la boveda'" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Reinicia Jarvis para que cargue lo nuevo:" -ForegroundColor Cyan
Write-Host "  .\reiniciar_jarvis.ps1"
Write-Host ""
