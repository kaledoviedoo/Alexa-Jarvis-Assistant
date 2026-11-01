# Jarvis

![estado](https://img.shields.io/badge/estado-en%20uso%20diario-success)
[![pruebas](https://github.com/kaledoviedoo/Alexa-Jarvis-Assistant/actions/workflows/pruebas.yml/badge.svg)](https://github.com/kaledoviedoo/Alexa-Jarvis-Assistant/actions/workflows/pruebas.yml)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![fastapi](https://img.shields.io/badge/FastAPI-0.110-009688)
![ollama](https://img.shields.io/badge/Ollama-local-black)
![alexa](https://img.shields.io/badge/Alexa-Custom%20Skill-00CAFF)
![licencia](https://img.shields.io/badge/uso-personal-lightgrey)

Asistente de voz que corre entero en mi PC y se maneja hablándole a un Echo. Alexa
pone el micrófono; entender la orden, decidir y ejecutar pasa en una RTX 3050 dentro
de mi casa, sin que ninguna frase salga hacia un modelo en la nube.

## Demo

```
Echo ──▶ Amazon ──▶ túnel ──▶ FastAPI ──▶ router regex ──▶ herramientas (29)
                                  │            │
                                  │            ├── ¿no encaja? ──▶ capa semántica
                                  │            │                        │
                                  │            └────────────────────────┴──▶ Ollama
                                  │                                          (local)
                                  └── > 6.5 s ──▶ hilo de fondo + "lo estoy procesando"
```

| Dices | Pasa |
|---|---|
| `abre spotify y busca tame impala` | Abre Spotify y escribe en SU buscador, no en Google |
| `manda un mensaje a familia que llego en 10` | Escribe en WhatsApp Web y confirma el chat antes de enviar |
| `selecciona todos los pdf` → `archívalos en la bóveda` | Los reparte por Obsidian razonando carpeta por carpeta |
| `qué archivo tiene más memoria` | Los archivos que más ocupan (no el uso de RAM) |
| `juega valorant` | Abre el lanzador y le da a JUGAR leyendo la pantalla |

## Stack

| Capa | Herramienta |
|---|---|
| Voz | Alexa Custom Skill (SSML, es-MX) |
| Backend | Python 3.10+, FastAPI, uvicorn |
| Modelos | Ollama local: `llama3.2:3b`, `qwen2.5:7b`, `nomic-embed-text` |
| Visión | Tesseract OCR + mss |
| Automatización | pyautogui, pygetwindow, psutil, pywin32 |
| Red | Tailscale Funnel (o ngrok) |
| Datos | JSON en disco, sin base de datos |

## Features

- **Router determinista** de ~2.200 líneas de regex que resuelve el 90% de las órdenes en menos de 1 ms, sin tocar el modelo.
- **Capa semántica** entre el router y el modelo: si no dices la palabra exacta, traduce por significado (~30 ms) en vez de gastar segundos de LLM.
- **Presupuesto de tiempo**: lo que no cabe en los 8 s de Alexa se muda a un hilo y se recoge después con "cómo quedó lo último".
- **Tareas que sobreviven** (SQLite en `~/.jarvis/jarvis.db`): un reinicio no pierde nada; lo que termina sin que nadie espere salta como aviso de Windows y se cuenta al abrir la siguiente sesión.
- **Chat local** en `/chat` (`.\chat`): sin el límite de 8 s de Alexa, con cada paso en vivo y confirmaciones con botón. Solo desde el PC o tu red de Tailscale, nunca por el Funnel; el celular se vincula con un código de 6 cifras.
- **Rutinas** en `~/.jarvis/rutinas.yaml`: buenos días, Descargas, disco lleno, modo juego y memoria al día, más recordatorios por voz ("todos los lunes a las 8 recuérdame…"). Nunca envían ni hacen nada irreversible.
- **Memoria semántica** de la bóveda de Obsidian y del propio código (troceado por definiciones vía AST).
- **Visión**: lee la pantalla y pulsa por función, no por nombre (JUGAR en Epic, PLAY en Steam).
- **Integraciones**: WhatsApp Web, Outlook (COM local), Teams, Obsidian, Epic/Steam.
- **Freno de acciones**: las herramientas que tocan el equipo exigen que la frase original de verdad las pidiera.
- **Seguridad**: firma de Amazon verificada, escrituras confinadas a tres carpetas, nada se borra de verdad, confirmación en dos turnos para lo irreversible.
- **458 pruebas** de enrutado, fraseo, sandbox de archivos y formato Alexa, ejecutables sin tocar el equipo y en cada push.

## Getting Started

Requisitos: Windows 10/11, Python 3.10+, [Ollama](https://ollama.com) y una cuenta
de desarrollador de Alexa (gratis).

```powershell
git clone https://github.com/kaledoviedoo/Alexa-Jarvis-Assistant.git jarvis
cd jarvis

py -m pip install -r requirements.txt          # núcleo
py -m pip install -r requirements-extras.txt   # correo, visión, memoria

ollama pull llama3.2:3b
ollama pull nomic-embed-text

Copy-Item .env.example .env
Copy-Item contexto.ejemplo.md contexto.md
```

Rellena `ALEXA_SKILL_ID` en el `.env`, y luego:

```powershell
.\scripts\configurar_tailscale.ps1
.\scripts\instalar_autoarranque.ps1
.\scripts\vigilante.ps1 -Instalar
.\reiniciar_jarvis.ps1
```

Los extras son opcionales: sin ellos Jarvis arranca igual y solo dice que no
puede con esas órdenes. El vigilante comprueba cada 5 minutos que el servidor
responda y lo levanta si se cayó.

En la consola de Alexa: pega `alexa/interaction_model.json` en el editor JSON, dale
a *Build Model* y apunta el endpoint a tu túnel.

## Uso

```
Alexa, abre mi asistente
```

La sesión se queda abierta: puedes encadenar órdenes sin repetir la invocación, y
cerrarla con "pausa".

```
cómo está el cpu
entra a descargas
selecciona los 3 primeros
archívalos en la bóveda
qué archivo maneja whatsapp
```

Y contra el servidor directamente:

```powershell
curl -H "X-Jarvis-Token: <JARVIS_TOKEN_LOCAL>" http://localhost:8000/salud   # sello, modo, GPU, Ollama
.\scripts\diagnostico.ps1            # revisa túnel, puertos y dependencias
py test_router.py                    # 188 casos de enrutado
py test_archivos.py                  # el sandbox y la selección, sobre disco
```

## Estructura

```
jarvis/
├── server.py              FastAPI: firma, tiempos, SSML
├── nlu.py                 Router determinista (el 90% de las órdenes)
├── ollama_client.py       Function calling, presupuesto y freno de acciones
├── tareas.py              Tareas de fondo en SQLite, con sus pasos
├── rutinas.py             Planificador, rutinas de serie y recordatorios por voz
├── avisos.py              Notificaciones de Windows, sin instalar nada
├── chat.py, chat.html     El chat local: pasos en vivo y botones de confirmar
├── security.py            Verificación de la firma de Amazon
├── modes.py               Perfiles de modelo según la GPU
├── voz.py                 Vocativo y pronunciación (SSML)
├── config.py              .env, rutas y límites de seguridad
├── tools/                 23 módulos de capacidades
│   ├── archivos.py        Crear, leer, mover (sandbox de rutas)
│   ├── seleccion.py       Coger varios archivos por voz
│   ├── memoria.py         Vectores de la bóveda y del código
│   ├── intencion.py       Capa semántica sobre el router
│   ├── pantalla.py        OCR y clic por función
│   ├── whatsapp.py        WhatsApp Web con confirmación
│   ├── archivar.py        Archivado razonado en Obsidian
│   └── ...
├── alexa/                 Modelo de interacción de la skill
├── scripts/               13 scripts de instalación, diagnóstico y vigilancia
├── .github/workflows/     Las pruebas en cada push
└── test_*.py              458 pruebas
```

## Contacto

Kaled Oviedo — Ingeniería de Sistemas

- Instagram: [@kaledoviedoo](https://instagram.com/kaledoviedoo)
- GitHub: [kaledoviedoo](https://github.com/kaledoviedoo)
