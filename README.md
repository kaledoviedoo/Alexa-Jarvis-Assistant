# Jarvis

Un asistente de voz que corre entero en mi PC y se maneja hablándole a un dispositivo echo dot.

Alexa pone el micrófono y el altavoz. Todo lo demás (entender la orden,
decidir qué hacer, abrir programas, leer la pantalla, escribir en WhatsApp,
buscar en mis notas) pasa en una RTX 3050 de 6 GB dentro de mi casa. Ninguna
frase que digo sale hacia un modelo en la nube.

```
Echo  ──▶  Amazon  ──▶  túnel  ──▶  FastAPI  ──▶  router  ──▶  herramientas
                                        │            │              (43)
                                        │            └── ¿no encaja?
                                        │                    ▼
                                        └────────────── Ollama (local)
```

## El problema que tuve

**Alexa corta a los ocho segundos.** No es una recomendación: si el servidor
no ha contestado, la sesión se cae con un error genérico y el usuario oye
"la skill solicitada no respondió correctamente".

Ocho segundos no dan para que un modelo de 7B lea la orden, decida, ejecute y
redacte. Así que el sistema está construido alrededor de esa restricción:

**1. Un router determinista se come el 90% de las órdenes.**
`nlu.py` son ~2.400 líneas de expresiones regulares ordenadas por
especificidad. "cierra spotify" no necesita un modelo de lenguaje: necesita
una regex y una llamada a `psutil`. Resuelve en **menos de 1 ms** y el modelo
ni se entera. El orden de los bloques importa enormemente y está documentado
patrón a patrón, porque un patrón genérico colocado demasiado arriba se come
las órdenes de tres bloques que van debajo.

**2. Lo que sí llega al modelo tiene presupuesto.**
`PRESUPUESTO_SEGUNDOS = 6.5`, y lo que gastó el router se resta del que le
queda al modelo. Se propaga por `threading.local()`.

**3. Lo que no cabe en el presupuesto no se pierde: se muda al fondo.**
Si el modelo no termina a tiempo, Alexa dice "lo estoy procesando" y el
trabajo sigue en un hilo. El resultado queda guardado y se recoge después con
"¿cómo quedó lo último?". Ninguna orden se cae por un *timeout*.

## Lo que se aprendió a base de romperlo

Cada uno de estos salió de leer registros de uso real, no de imaginar casos.

**El `async def` que congelaba el servidor.** Los endpoints eran `async` y
dentro llamaban a código bloqueante. Eso no bloquea "esa petición": bloquea el
bucle de eventos entero de uvicorn, así que una orden lenta congelaba todas
las demás. Una de cada tres peticiones moría. Con `asyncio.to_thread` bajó a
una de cada trece. La prueba de regresión mide **reloj de pared** —no la
duración de la petición rápida, que es justo el error que cometí las dos
primeras veces que intenté escribirla.

**MagicDNS haciendo inútil el "mantener caliente".** Había un ping cada 90
segundos para que el túnel no se durmiera. Nunca sirvió: MagicDNS resolvía el
nombre `.ts.net` a la IP interna del tailnet, así que el ping iba por dentro y
el camino público seguía frío. Ahora se resuelve por DNS-over-HTTPS y se
calienta con TLS crudo contra la IP pública, con SNI, que es exactamente el
camino que recorre Amazon.

**Un modelo de 3B llamando a herramientas por no callarse.** Del registro,
hablando yo solo sin dar ninguna orden: escribió texto en la ventana que
tuviera al frente, e intentó cerrar Alexa. Un modelo pequeño, cuando no
entiende, no se calla: llama a la que le suene. La respuesta es
`_por_que_no()` en `ollama_client.py`: las herramientas que **tocan** el
equipo exigen que la frase original contenga algo que de verdad las pida, y
que sea corta —las órdenes reales son cortas e imperativas; las dos frases que
dispararon acciones sin querer tenían 73 y 139 caracteres. Las que solo miran
(leer, listar, estado) pasan sin nada: equivocarse ahí no cuesta nada.

**Catorce funciones duplicadas en el router.** Python se queda con la última
definición sin decir nada. Dos de ellas tenían cuerpos distintos, así que la
versión que yo creía estar ejecutando llevaba tiempo muerta.

**PowerShell 5.1 y el UTF-8.** Los `.ps1` tienen que ir en UTF-8 **con BOM** y
sin un solo carácter no-ASCII dentro. Y al leer el log hay que pasarle
`-Encoding UTF8` explícito, o Python escribe en UTF-8, PowerShell lee en la
página de códigos del sistema, y en pantalla sale `Camino al tÃºnel`.

## De qué está hecho
|---|---|
| Pieza | Qué hace |
| `server.py` | FastAPI. Verifica la firma de Amazon, mide tiempos reales, responde en SSML. |
| `nlu.py` | El router determinista. Donde se resuelve casi todo. |
| `ollama_client.py` | *Function calling* con presupuesto, saneado de respuestas y el freno de mano. |
| `voz.py` | Cómo suena: vocativo al principio, y SSML para que "GitHub" no se lea "guitub". |
| `security.py` | Firma de Amazon, ventana de tiempo, ID de skill. |
| `modes.py` | Tres perfiles de modelo según lo que esté haciendo la GPU. |
| `tools/` | 22 módulos: archivos, pantalla, WhatsApp, correo, memoria semántica, catálogo de apps… |

**43 herramientas** expuestas al modelo, **150 pruebas de enrutado** y **195
de tolerancia al fraseo**, todas ejecutables sin tocar el equipo: las
herramientas reales se sustituyen por dobles que solo registran qué se llamó.

## Detalles que tienen su gracia

**Alexa transcribe fatal los nombres propios.** "Comet" llega como *cometa*,
*comer*, *covid*, *comed*, *cornet* y *comic*. Hay una tabla de alias por eso.
Sin ella, "cierra comet" buscaba un proceso llamado `cometa.exe` y contestaba
alegremente que no estaba abierto: peor que fallar, porque miente.

**Abrir una aplicación tiene seis intentos.** Ruta fijada a mano → catálogo de
lo instalado de verdad (rastreando menú Inicio, registro y Store) → protocolos
conocidos → lo más parecido del catálogo, razonando → **el buscador de
Windows** (tecla Windows, escribir, Enter, que es lo que harías tú) → y solo
entonces se rinde, proponiendo alternativas.

**Se pincha por función, no por nombre.** El botón de jugar pone JUGAR en
Epic, PLAY en Steam e INICIAR en otros. `INTENCIONES` en `tools/pantalla.py`
guarda las palabras candidatas de cada función ordenadas por fiabilidad, y se
pincha la primera que el OCR encuentre de verdad en pantalla. Si no encuentra
ninguna, no pincha: dice lo que sí está leyendo.

**Nada se borra.** "Elimina" mueve a `~/.jarvis/papelera`. El teclado es lista
blanca y no hay clics a coordenadas ciegas: solo se pincha donde el OCR ha
leído un texto de verdad.

## Montarlo

Hace falta Windows, [Ollama](https://ollama.com) y una cuenta de desarrollador
de Alexa (gratis).

```powershell
git clone <este-repo> jarvis
cd jarvis
Copy-Item .env.example .env        # y rellena ALEXA_SKILL_ID
Copy-Item contexto.ejemplo.md contexto.md
py -m pip install -r requirements.txt
ollama pull llama3.2:3b

.\scripts\configurar_tailscale.ps1   # o configurar_ngrok.ps1
.\scripts\instalar_autoarranque.ps1
.\reiniciar_jarvis.ps1
```

En la consola de Alexa, pega `alexa/interaction_model.json` en el editor JSON
y apunta el endpoint a tu túnel. Los detalles largos están en `LEEME.md`.

## Seguridad

Es un servidor con acceso a mis archivos, expuesto a internet para que Amazon
pueda alcanzarlo. Lo que lo sostiene:

- Verificación criptográfica de la firma de Amazon (`JARVIS_VERIFICAR_FIRMA=true`,
  y el arranque avisa a gritos si está apagada).
- Comprobación del ID de la skill: una petición bien firmada pero de otra
  skill se rechaza igual.
- Ventana de tiempo, contra reenvíos de una petición capturada.
- Escrituras limitadas a Escritorio, Descargas y Documentos. `resolver_ruta`
  rechaza `..`, letras de unidad y separadores codificados **antes** de tocar
  el disco, y comprueba igual en Windows que en Linux.
- Las órdenes sin vuelta atrás (apagar, borrar en lote) piden confirmación en
  un segundo turno.

`.env` nunca se sube: lleva el ID de la skill y el dominio del túnel, que
juntos son las llaves de la casa.

---

Proyecto personal. El asistente es local a propósito: el precio de que sea mío
es que tengo que resolver yo los ocho segundos.
