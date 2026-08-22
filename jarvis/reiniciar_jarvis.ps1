# ============================================================================
#  reiniciar_jarvis.ps1  (atajo)
#
#  El script de verdad esta en scripts\reiniciar_jarvis.ps1. Este archivo
#  existe solo porque escribir  .\reiniciar_jarvis.ps1  desde la raiz es lo
#  primero que sale a la cabeza, y fallar ahi no ensena nada: solo molesta.
#
#  Pasa los argumentos tal cual, asi que  .\reiniciar_jarvis.ps1 -Verbose
#  funciona igual que llamar al de scripts.
# ============================================================================

& (Join-Path $PSScriptRoot "scripts\reiniciar_jarvis.ps1") @args
exit $LASTEXITCODE
