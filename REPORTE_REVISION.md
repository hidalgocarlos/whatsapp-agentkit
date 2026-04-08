# Reporte de Revision de Codigo — AgentKit (Imporusa)
**Fecha:** 2026-04-07  
**Archivos revisados:** 15  
**Autor de la revision:** Claude Code

---

## CRITICOS (Arreglar YA)

### 1. LLAVES API REALES EXPUESTAS en `.env`
**Archivo:** `.env` (lineas 10, 18, 38, 44, 47-48)  
**Severidad:** CRITICA  
Todas tus API keys reales estan en el archivo `.env`:
- `ANTHROPIC_API_KEY=sk-ant-api03-WLZu...`
- `WHAPI_TOKEN=UIj8JU8cc...`
- `EMAIL_PASSWORD=anyxxnax...`
- `TAVILY_API_KEY=tvly-dev-3ZRp...`
- `NOTION_TOKEN=ntn_lv876...`

**Riesgo:** Si este repo se sube a GitHub (publico o privado filtrado), cualquiera puede usar tus cuentas y generar cargos.  
**Accion:** Rotar TODAS las keys inmediatamente. Asegurarte de tener un `.gitignore` que excluya `.env`.

---

### 2. Puerto desalineado entre Dockerfile y docker-compose.yml
**Archivos:** `Dockerfile` (linea 6-7) vs `docker-compose.yml` (linea 5)  
**Severidad:** CRITICA — la app no funciona en Docker  

| Componente | Puerto |
|---|---|
| Dockerfile `EXPOSE` | 8080 |
| Dockerfile `CMD --port` | 8080 |
| docker-compose `ports` | 8000:8000 |
| .env `PORT` | 8000 |

El contenedor escucha en **8080** pero docker-compose mapea al **8000**. El trafico nunca llega a la app.

---

### 3. `RESEND_API_KEY` no esta en `.env` — los emails NUNCA se envian
**Archivo:** `agent/tools.py` (linea 449) y `.env`  
**Severidad:** CRITICA  
`enviar_cotizacion_email()` busca `RESEND_API_KEY` en las variables de entorno, pero `.env` no la define. La funcion siempre retorna `False`.  
Ademas, las variables SMTP (`EMAIL_PASSWORD`, `SMTP_HOST`, etc.) estan en `.env` pero el codigo ya no usa SMTP — usa Resend API via httpx. Esas variables SMTP son codigo muerto.

---

### 4. `await session.delete(msg)` — TypeError en `limpiar_historial`
**Archivo:** `agent/memory.py` (linea 100)  
**Severidad:** CRITICA  
`AsyncSession.delete()` de SQLAlchemy es un metodo **sincrono** (no es coroutine). Usar `await` sobre el retorna `None`, lo que causa `TypeError: object NoneType can't be used in 'await' expression`.  
Esta funcion falla cada vez que un usuario escribe "limpiar" en `test_local.py`.

---

### 5. Race condition en mensajes concurrentes
**Archivo:** `agent/main.py` (lineas 72-171)  
**Severidad:** ALTA  
`procesar_mensaje()` se ejecuta como `background_task`. Si un cliente envia 2 mensajes rapido:
1. Ambos llaman `obtener_historial()` al mismo tiempo
2. Ninguno ha guardado el mensaje anterior todavia
3. Claude ve el mismo historial para ambos, causando respuestas duplicadas o desordenadas

Se necesita un lock por numero de telefono o procesamiento secuencial por conversacion.

---

## BUGS FUNCIONALES

### 6. `fecha_legible` muestra meses en ingles
**Archivo:** `agent/tools.py` (linea 235)  
`datetime.now().strftime("%d de %B de %Y")` produce `"07 de April de 2026"` en vez de `"07 de Abril de 2026"`.  
`%B` usa el locale del sistema (generalmente ingles).

### 7. `log_error()` recibe nombre de funcion en vez de telefono
**Archivo:** `agent/main.py` (lineas 150, 177) vs `agent/session_logger.py` (linea 91)  
`log_error(telefono, error)` espera un telefono, pero main.py pasa `"procesar_mensaje"` y `"webhook"`. Los logs quedan con info erronea.

### 8. `RESEND_FROM` usa sandbox de Resend
**Archivo:** `agent/tools.py` (linea 454)  
El default `"Imporusa <onboarding@resend.dev>"` es el email sandbox de Resend que SOLO entrega al email del dueno de la cuenta. Emails a clientes reales seran rechazados.

### 9. `datetime.utcnow()` esta deprecado
**Archivo:** `agent/memory.py` (lineas 41, 57)  
Deprecado desde Python 3.12. Debe usarse `datetime.now(datetime.UTC)`.

---

## RECOMENDACIONES DE MEJORA

### 10. No hay `.gitignore` en el proyecto
No se encontro `.gitignore`. Esto significa que `.env`, `*.db`, `__pycache__/`, y `logs/` podrian subirse a Git accidentalmente.

### 11. `prompts.yaml` se lee del disco en CADA mensaje
**Archivo:** `agent/brain.py` (lineas 101-108, 111-114)  
`cargar_system_prompt()`, `obtener_mensaje_error()` y `obtener_mensaje_fallback()` leen el archivo YAML del disco por cada mensaje entrante. En produccion con trafico, esto es I/O innecesario. Deberia cachearse como se hace con `_knowledge_cache`.

### 12. Sin timeout en `httpx.AsyncClient` de whapi.py
**Archivo:** `agent/providers/whapi.py` (linea 82)  
`httpx.AsyncClient()` sin `timeout` puede colgar indefinidamente si Whapi no responde.

### 13. Dependencia `aiosmtplib` importada pero no usada
**Archivo:** `agent/tools.py` (linea 18)  
Se importa `aiosmtplib` pero el email se envia via Resend API. Import muerto.

### 14. Dependencia `tavily-python` en requirements.txt pero no usada
**Archivo:** `requirements.txt` (linea 12)  
La busqueda Tavily se hace con httpx directo (tools.py linea 511). El paquete `tavily-python` nunca se importa.

### 15. `log_inicio_sesion` importada pero nunca usada en main.py
**Archivo:** `agent/main.py` (linea 22)  
Import muerto. Solo se usa en `test_local.py`.

### 16. Sin validacion del comando ENVIAR_COTIZACION
**Archivo:** `agent/main.py` (lineas 86-96)  
El parseo se hace con `split("|")`. Si Claude genera un formato malformado o el contenido incluye `|`, el parseo se rompe silenciosamente. Deberia haber validacion robusta.

### 17. Sin limite de longitud en respuestas de WhatsApp
No hay chunking de mensajes largos. Si Claude genera una respuesta muy larga, WhatsApp podria truncarla o rechazarla.

### 18. Knowledge cache nunca se refresca
**Archivo:** `agent/brain.py`  
`cargar_knowledge()` se llama una vez al arrancar. Si los archivos en `/knowledge` cambian, el cache queda obsoleto hasta reiniciar el servidor.

### 19. `docker-compose.yml` usa `version` deprecado
**Archivo:** `docker-compose.yml` (linea 1)  
`version: "3.8"` esta deprecado en Docker Compose v2+. Se puede eliminar.

### 20. Carpeta `logs/` no esta en la estructura Docker
**Archivo:** `docker-compose.yml`  
La carpeta `logs/` se crea en tiempo de ejecucion (`session_logger.py`) pero no se monta como volumen en Docker. Los logs se pierden al reiniciar el contenedor.

---

## RESUMEN

| Categoria | Cantidad |
|---|---|
| Criticos (arreglar YA) | 5 |
| Bugs funcionales | 4 |
| Recomendaciones | 11 |
| **Total hallazgos** | **20** |

### Prioridad de accion:
1. Rotar TODAS las API keys expuestas y agregar `.gitignore`
2. Alinear puertos Dockerfile ↔ docker-compose
3. Agregar `RESEND_API_KEY` al `.env` (o los emails no funcionan)
4. Corregir `await session.delete()` en memory.py
5. Implementar lock por telefono para evitar race conditions
