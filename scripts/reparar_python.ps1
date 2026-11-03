# ============================================================================
#  reparar_python.ps1  -  Vuelve a poner a punto Jarvis tras cambiar Python.
#
#     .\scripts\reparar_python.ps1
#
#  Los paquetes de Python viven DENTRO de cada instalacion de Python. Si
#  desinstalas Python o instalas otra version, Jarvis se queda sin ellos y el
#  servidor no arranca ("No module named uvicorn"). Este script los vuelve a
#  instalar todos en el Python que tengas ahora.
#
#  No toca tu .env, tus rutinas, tu historial ni los modelos de voz: eso vive
#  en la carpeta de Jarvis y en %USERPROFILE%\.jarvis, no dentro de Python.
# ============================================================================
$ErrorActionPreference = "Continue"
$Raiz = Split-Path $PSScriptRoot -Parent
Set-Location $Raiz

function Titulo($t) { Write-Host ""; Write-Host $t -ForegroundColor White }
function Bien($t)   { Write-Host "   $t" -ForegroundColor Green }
function Mal($t)    { Write-Host "   $t" -ForegroundColor Red }
function Nota($t)   { Write-Host "   $t" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  REPARAR JARVIS TRAS CAMBIAR PYTHON" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# ---- 1. Que Python hay ----------------------------------------------------
Titulo "1. Buscando Python..."
$version = & py -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
if (-not $version) {
    Mal "No encuentro el comando 'py'. Reinstala Python marcando 'py launcher' y 'Add to PATH'."
    exit 1
}
$exe = & py -c "import sys; print(sys.executable)"
Bien "Python $version en $exe"
$partes = $version.Split('.')
if ([int]$partes[0] -lt 3 -or ([int]$partes[0] -eq 3 -and [int]$partes[1] -lt 10)) {
    Mal "Jarvis necesita Python 3.10 o mas nuevo."
    exit 1
}

# ---- 2. pip ----------------------------------------------------------------
Titulo "2. Poniendo pip al dia..."
& py -m ensurepip --upgrade 2>$null | Out-Null
& py -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -eq 0) { Bien "listo" } else { Mal "pip dio un error, sigo igual" }

# ---- 3. Nucleo: sin esto no arranca ----------------------------------------
Titulo "3. Paquetes del nucleo (requirements.txt)..."
& py -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -eq 0) { Bien "listo" } else { Mal "Algo fallo. Repite a mano:  py -m pip install -r requirements.txt"; exit 1 }

# ---- 4. Extras: vision, correo, memoria, voz ------------------------------
Titulo "4. Extras: pantalla, Outlook, memoria y voz (requirements-extras.txt)..."
Nota "Si alguno falla, Jarvis arranca igual y solo dice que no puede con esa orden."
$extras = Get-Content requirements-extras.txt | Where-Object { $_ -match '^\s*[A-Za-z]' }
foreach ($linea in $extras) {
    $paquete = ($linea -split '\s*;')[0].Trim()
    if ($linea -match 'sys_platform\s*==\s*"win32"' -or $linea -notmatch 'sys_platform') {
        & py -m pip install --quiet "$paquete" 2>$null
        if ($LASTEXITCODE -eq 0) { Bien "$paquete" } else { Mal "$paquete (opcional, sigo)" }
    }
}
& py -m pip install --quiet pyyaml 2>$null

# pywin32 necesita registrar sus DLL para que funcione el correo de Outlook.
& py -c "import win32com" 2>$null
if ($LASTEXITCODE -ne 0) {
    & py -m pywin32_postinstall -install 2>$null | Out-Null
}

# ---- 5. Comprobar que todo importa ----------------------------------------
Titulo "5. Comprobando..."
$prueba = @'
import importlib
nucleo = ["fastapi", "uvicorn", "cryptography", "psutil", "ollama", "pyautogui", "pyperclip",
          "PIL", "pygetwindow", "docx", "openpyxl"]
extras = {"numpy": "memoria rapida", "pytesseract": "leer la pantalla", "mss": "capturas",
          "win32com": "Outlook", "pypdf": "leer PDF", "yaml": "rutinas.yaml",
          "faster_whisper": "voz: oir", "sounddevice": "voz: microfono",
          "keyboard": "voz: atajo Ctrl+Alt+J", "piper": "voz: hablar"}
faltan = []
for m in nucleo:
    try:
        importlib.import_module(m)
    except Exception as e:
        faltan.append(m)
print("NUCLEO", "OK" if not faltan else "FALTA " + ", ".join(faltan))
for m, para in extras.items():
    try:
        importlib.import_module(m)
        print("OK   ", para)
    except Exception:
        print("NO   ", para)
'@
$salida = $prueba | & py -
foreach ($l in $salida) {
    if ($l -like "NUCLEO OK*") { Bien "El nucleo esta completo." }
    elseif ($l -like "NUCLEO*") { Mal $l }
    elseif ($l -like "OK*") { Bien $l.Substring(5) }
    else { Nota ("falta " + $l.Substring(5) + " (opcional)") }
}
if ($salida -notcontains "NUCLEO OK") { exit 1 }

# ---- 6. Arrancar Jarvis -----------------------------------------------------
Titulo "6. Reiniciando Jarvis..."
Nota "Acepta el aviso de Windows si pide permisos de administrador."
& (Join-Path $Raiz "reiniciar_jarvis.ps1")
