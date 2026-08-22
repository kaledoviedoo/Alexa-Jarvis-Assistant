# Jarvis 2.0

Asistente local para Windows controlado por voz con Alexa.
FastAPI + Ollama + túnel de ngrok, perfilado para una RTX 3050 de 6 GB.

El manual completo está publicado como artefacto. Este archivo es solo el arranque rápido.

---

## Arranque rápido

```powershell
# 1. Dependencias
py -m pip install -r requirements.txt

# 2. Modelos
ollama pull llama3.2:3b
ollama pull qwen2.5:7b-instruct-q4_K_M

# 3. Configuración
copy .env.example .env
notepad .env          # pon aquí tu ALEXA_SKILL_ID

# 4. Túnel de ngrok (authtoken + dominio estático + prueba)
.\scripts\configurar_ngrok.ps1

# 5. Comprobar que todo está en su sitio
.\scripts\diagnostico.ps1

# 6. Arrancar
py -m uvicorn server:app --reload --port 8000
```

Prueba sin Alexa:

```powershell
irm http://localhost:8000/probar -Method Post -ContentType "application/json" `
  -Body '{"comando": "crea un archivo llamado prueba punto py con el codigo print hola"}'
```

Arranque automático al iniciar sesión (PowerShell **como administrador**):

```powershell
cd scripts
.\instalar_autoarranque.ps1
```

---

## Estructura

```
jarvis/
├── server.py              Endpoints de FastAPI y formato de Alexa
├── config.py              Todos los ajustes viven aquí
├── nlu.py                 Router de comandos por regex  ← el 90% de las órdenes
├── ollama_client.py       Function calling con presupuesto de tiempo
├── modes.py               Modos normal / dedicado / gaming y control de VRAM
├── security.py            Verificación de la firma de Amazon
├── tareas.py              Resultados de tareas en segundo plano
├── tools/
│   ├── archivos.py        Crear, leer, editar, mover, buscar (con sandbox)
│   ├── sistema.py         CPU, RAM, GPU, procesos, apps, energía
│   ├── navegador.py       Comet y búsquedas web
│   └── entrada.py         Teclado y ratón (lista blanca)
├── scripts/
│   ├── configurar_ngrok.ps1        Authtoken, dominio estático y prueba del túnel
│   ├── iniciar_jarvis.ps1          Arranca Ollama + servidor + ngrok
│   ├── instalar_autoarranque.ps1   Registra la tarea programada
│   ├── desinstalar_autoarranque.ps1
│   └── diagnostico.ps1             Revisa todo y dice qué falta
├── alexa/
│   └── interaction_model.json      Pegar en el JSON Editor de la consola
├── test_router.py         56 frases dictadas → comando correcto
└── test_alexa.py          Peticiones reales de Alexa contra el servidor
```

---

## Las tres decisiones de diseño que importan

**1. Router determinista antes que el modelo.**
Alexa corta a los ~8 s; un modelo de 7B en frío tarda 20-40 s. Las órdenes frecuentes se
resuelven con expresiones regulares en menos de 1 ms y nunca llegan a fallar por timeout.
Solo lo verdaderamente abierto pasa a Ollama.

**2. Presupuesto de tiempo con ejecución en segundo plano.**
Si el modelo no termina en 6,5 s, Jarvis responde "lo estoy procesando" pero la tarea sigue
corriendo. El resultado se recupera diciendo "cómo quedó lo último". Ninguna orden se pierde.

**3. Sandbox real, no confianza.**
Jarvis solo escribe en Escritorio, Descargas, Documentos y `~\Jarvis`. Los intentos de escapar
(`..\..\`, rutas absolutas, letras de unidad, codificación URL) se rechazan antes de tocar el
disco. Nada se borra: "elimina X" lo mueve a `~\.jarvis\papelera`. El teclado funciona por lista
blanca y no existe ninguna función que haga clic en coordenadas arbitrarias.

---

## Pruebas

```powershell
py test_router.py    # 56/56 — enrutado de frases dictadas
py test_alexa.py     # formato de respuesta de Alexa válido
```

---

## Diagnóstico

| Dónde mirar | Qué te dice |
|---|---|
| `.\scripts\diagnostico.ps1` | Requisitos, dependencias, servicios, estado interno |
| `http://localhost:8000/salud` | Ollama, modelos, VRAM libre, modo, seguridad |
| `~\.jarvis\jarvis.log` | La línea `Slots:` muestra qué entendió Alexa exactamente |
| `~\.jarvis\logs\arranque.log` | Por qué no arrancó algo al encender el PC |
| `http://127.0.0.1:4040` | Panel de ngrok: peticiones que llegan en tiempo real |

---

## Nota sobre ngrok y Windows Defender

Defender marca el ejecutable de ngrok con frecuencia, porque las herramientas de túnel
se usan a menudo en ataques reales para sacar datos de una red. El propio equipo de ngrok
reconoce el problema y reenvía sus binarios a Microsoft cada vez que vuelven a ser marcados.

La comprobación que de verdad distingue el ngrok legítimo de una copia manipulada es la
firma digital, no la alerta del antivirus:

```powershell
Get-AuthenticodeSignature (Get-Command ngrok).Source
```

El resultado debe decir `Valid` y el firmante debe contener `ngrok`. `configurar_ngrok.ps1`
y `diagnostico.ps1` hacen esta comprobación automáticamente.

Si la firma no valida o el firmante no es ngrok, no añadas ninguna exclusión: descarga el
archivo de nuevo únicamente desde https://ngrok.com/download. Si la firma sí valida y aun
así Defender lo bloquea, la exclusión correcta es la de ese archivo concreto, ejecutada
como administrador, y nunca desactivar la protección en tiempo real ni excluir carpetas
enteras:

```powershell
Add-MpPreference -ExclusionPath "C:\ruta\exacta\a\ngrok.exe"
```

Para revertirla más adelante:

```powershell
Remove-MpPreference -ExclusionPath "C:\ruta\exacta\a\ngrok.exe"
```
