# ============================================================================
#  instalar_voz.ps1  -  Voz sin Alexa (Fase 6.2). Todo gratis y local.
#
#     .\scripts\instalar_voz.ps1
#
#  Instala:
#    - faster-whisper   para entender lo que dices (corre en el procesador)
#    - sounddevice      para grabar del microfono
#    - keyboard         para el atajo de teclado (Ctrl+Alt+J)
#    - piper-tts        para contestarte con una voz natural en espanol
#  Y descarga una vez:
#    - el modelo de Whisper (small, ~460 MB)
#    - la voz de Piper (es_MX-claude-high, ~60 MB)
#
#  Todo queda en %USERPROFILE%\.jarvis\voz. Si Piper no instalara, Jarvis
#  habla con la voz de Windows, que ya viene en el equipo.
# ============================================================================
$ErrorActionPreference = "Continue"
$Raiz = Split-Path $PSScriptRoot -Parent
$Carpeta = Join-Path $env:USERPROFILE ".jarvis\voz"
New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null

function Leer-Env($nombre, $defecto) {
    $linea = Select-String -Path (Join-Path $Raiz ".env") -Pattern "^$nombre=" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($linea) { return $linea.Line.Split('=', 2)[1].Trim().Trim('"') }
    return $defecto
}
$Modelo = Leer-Env "JARVIS_WHISPER_MODELO" "small"
$Voz    = Leer-Env "JARVIS_VOZ_PIPER" "es_MX-claude-high"

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  VOZ LOCAL PARA JARVIS" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

$paquetes = @("faster-whisper", "sounddevice", "keyboard", "piper-tts")
$i = 1
foreach ($p in $paquetes) {
    Write-Host ""
    Write-Host "$i. Instalando $p..." -ForegroundColor White
    & py -m pip install --quiet --upgrade $p
    if ($LASTEXITCODE -eq 0) { Write-Host "   listo" -ForegroundColor Green }
    else { Write-Host "   FALLO. A mano:  py -m pip install $p" -ForegroundColor Red }
    $i++
}

Write-Host ""
Write-Host "$i. Bajando la voz de Piper ($Voz)..." -ForegroundColor White
& py -m piper.download_voices --download-dir $Carpeta $Voz
if (Test-Path (Join-Path $Carpeta "$Voz.onnx")) { Write-Host "   lista" -ForegroundColor Green }
else { Write-Host "   No se pudo. Jarvis usara la voz de Windows." -ForegroundColor Yellow }
$i++

Write-Host ""
Write-Host "$i. Bajando el modelo de Whisper ($Modelo). La primera vez tarda unos minutos..." -ForegroundColor White
$destino = Join-Path $Carpeta "whisper"
& py -c "from faster_whisper import WhisperModel; WhisperModel('$Modelo', device='cpu', compute_type='int8', download_root=r'$destino'); print('   listo')"
if ($LASTEXITCODE -ne 0) { Write-Host "   FALLO al bajar Whisper. Revisa la conexion y repite." -ForegroundColor Red }

Write-Host ""
Write-Host "Ahora reinicia Jarvis:  .\reiniciar_jarvis.ps1" -ForegroundColor Cyan
Write-Host "Y prueba: pulsa Ctrl+Alt+J, espera el pitido, habla y haz una pausa." -ForegroundColor Cyan
Write-Host ""
Write-Host "Si no te oye: Configuracion de Windows > Privacidad > Microfono >" -ForegroundColor DarkGray
Write-Host "'Permitir que las aplicaciones de escritorio accedan al microfono'." -ForegroundColor DarkGray
Write-Host ""
