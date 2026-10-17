# Manual para poner en marcha BarberBot

Manual de despliegue local y pruebas del agente de reservas de WhatsApp en
Windows. Los nombres, dominios y rutas de ejemplo de este documento son
ficticios o marcadores. No contiene credenciales.

> **Bloqueo conocido de la instalación documentada:** OpenAI devolvió
> `429 insufficient_quota` porque el proyecto de API no tenía cuota disponible.
> No se compró crédito. Antes de esperar respuestas del agente se debe resolver
> ese bloqueo, aceptando conscientemente cualquier coste o implementando otra
> opción de modelo.

## 1. Requisitos

- Windows y PowerShell.
- Docker Desktop con motor Linux en ejecución.
- Docker Compose.
- Una app de Meta for Developers con el caso de uso WhatsApp.
- WABA (WhatsApp Business Account) y un número de prueba o registrado.
- Un teléfono móvil autorizado como destinatario de prueba.
- Credenciales válidas de WhatsApp Cloud API.
- Clave de API OpenAI con cuota disponible, o un proveedor/modelo alternativo
  implementado.
- ngrok para pruebas locales con URL HTTPS pública.
- Plantilla de mensaje aprobada si se van a probar recordatorios o mensajes
  iniciados por la empresa fuera de la ventana de servicio.

La suscripción de ChatGPT no incluye saldo de la API. No activar pagos ni
recargas automáticas sin revisar coste y dar consentimiento.

## 2. Componentes

- `web_api`: FastAPI/Uvicorn; recibe `/webhook`, valida firma, orquesta el agente
  y responde por WhatsApp.
- `src/agente_ia.py`: lógica conversacional, llamada a OpenAI y ejecución de
  herramientas.
- `src/tools.py`: consultas de datos y alta de citas.
- `src/disponibilidad.py`: cálculo de huecos disponibles.
- `src/memoria.py`: historial de conversación en PostgreSQL.
- `postgres_db`: almacén de agenda, clientes, citas e historial.
- `cron_worker`: worker que busca citas próximas y envía recordatorios cada hora.
- ngrok: túnel temporal entre Meta y el puerto local 8000.

## 3. Preparar variables de entorno

La raíz del proyecto contiene `.env.example`. Si no existe `.env`, cópialo como
`.env` y complétalo localmente. No publiques `.env`, no lo pegues en chats ni
incluyas capturas con sus valores.

Ruta genérica de ejemplo:

```text
C:\ruta\al\proyecto\barberbot_project
```

### Base de datos

- `POSTGRES_USER`: usuario inicial de PostgreSQL.
- `POSTGRES_PASSWORD`: contraseña robusta.
- `POSTGRES_DB`: base de datos a crear.
- `DATABASE_URL`: URL de conexión. Dentro de Compose el hostname es
  `postgres_db`; formato orientativo:
  `postgresql://USUARIO:CONTRASEÑA@postgres_db:5432/BASE`.
- Si la contraseña tiene caracteres reservados de URL, codifícalos correctamente.

### Agente

- `OPENAI_API_KEY`: clave API con cuota habilitada.
- `OPENAI_MODEL`: modelo soportado por la API; el ejemplo del proyecto usa
  `gpt-4o-mini`.
- `BARBERBOT_LOCAL_ID`: ID entero positivo del local que atenderá el agente.
- `BUSINESS_TIMEZONE`: zona IANA, normalmente `Europe/Madrid` para un negocio en
  España.

`init_db.sql` inserta un local y un conjunto de datos de demostración al
inicializar un volumen PostgreSQL vacío. El ID generado no debe suponerse
constante si hay datos previos o se reutiliza el volumen.

Para consultar los locales tras inicializar la base:

```powershell
docker compose exec postgres_db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id, nombre FROM locales ORDER BY id;"'
```

Usa el ID correcto como `BARBERBOT_LOCAL_ID`.

### Meta y WhatsApp

- `WHATSAPP_VERIFY_TOKEN`: secreto elegido por el operador y copiado igual en la
  configuración webhook de Meta. Se usa para verificar el challenge inicial.
- `WHATSAPP_APP_SECRET`: App Secret de la misma app Meta; valida la firma HMAC de
  los webhooks.
- `WHATSAPP_API_TOKEN`: token de acceso que autoriza los envíos a WhatsApp Cloud
  API. Puede caducar si es temporal.
- `WHATSAPP_PHONE_NUMBER_ID`: identificador del número remitente en Cloud API.
- `WHATSAPP_GRAPH_API_VERSION`: versión Graph API válida en formato `vNN.N`.
- `WHATSAPP_REMINDER_TEMPLATE_NAME`: nombre exacto de la plantilla aprobada.
- `WHATSAPP_REMINDER_TEMPLATE_LANGUAGE`: idioma/código aprobado de la plantilla.
- `LOG_LEVEL`: nivel de logs del worker; `INFO` es un valor razonable.

No confundir los identificadores y secretos:

| Dato | Dónde se configura |
| --- | --- |
| Verify Token | `WHATSAPP_VERIFY_TOKEN` |
| App Secret | `WHATSAPP_APP_SECRET` |
| Access Token | `WHATSAPP_API_TOKEN` |
| Phone Number ID | `WHATSAPP_PHONE_NUMBER_ID` |
| WABA ID | Se usa al administrar/suscribir la cuenta en Graph; no reemplaza al Phone Number ID |
| Número visible con prefijo internacional | Dirección telefónica; no es ID ni token |

## 4. Iniciar PostgreSQL, API y worker

Abre PowerShell y cambia a la raíz real del proyecto. Ejemplo de uso:

```powershell
Set-Location 'C:\ruta\al\proyecto\barberbot_project'
docker info
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Si `docker info` no logra conectar al motor, inicia Docker Desktop y espera a
que esté listo. Si Compose dice `no configuration file provided`, estás fuera
de la carpeta del proyecto: vuelve a ejecutar `Set-Location` con la ruta real.

Confirma que:

- `postgres_db` está `Up` y `healthy`.
- `web_api` está `Up` y `healthy`.
- `cron_worker` está `Up`.

Los scripts de inicialización se ejecutan automáticamente solo cuando
PostgreSQL crea un volumen vacío. No borres el volumen para forzar la
inicialización si necesitas conservar datos.

Comprobar la API:

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/health'
```

Resultado esperado:

```json
{"status":"ok"}
```

Este healthcheck no comprueba las credenciales de OpenAI/Meta ni el flujo de
mensajes.

## 5. Configurar la aplicación Meta

Los textos exactos de menú pueden variar:

1. Abre la aplicación de Meta for Developers.
2. Añade/configura el caso de uso **Conectar en WhatsApp**.
3. En **Paso 1: Pruébalo / API Setup**, elige un número de prueba.
4. Añade el móvil operador como destinatario permitido y completa su verificación.
5. Copia el **Phone Number ID** del remitente de prueba a
   `WHATSAPP_PHONE_NUMBER_ID`. No copies el número visible `+...` ni el WABA ID.
6. Genera un **Access Token** para la app/número y guárdalo en
   `WHATSAPP_API_TOKEN`. Si es temporal, registra cuándo caduca de forma segura
   y rótalo cuando sea necesario.
7. Obtén el App Secret de la app y guárdalo en `WHATSAPP_APP_SECRET`.
8. En **Paso 2: Configuración de producción → Configurar webhooks**, proporciona
   la URL pública HTTPS del túnel terminada en `/webhook` y el mismo Verify
   Token que está en `.env`.
9. Pulsa **Verificar y guardar**.
10. En **Campos del webhook**, activa la suscripción `messages`.
11. Comprueba también que la app está suscrita a la WABA. Graph API Explorer
    permite consultar `GET /WABA_ID/subscribed_apps`. Para crear la suscripción
    se puede usar `POST` a la misma ruta con los permisos y acceso necesarios.
12. Durante pruebas en modo desarrollo, utiliza el número de prueba y el
    destinatario autorizado. No es necesario iniciar la verificación comercial
    solo para verificar el callback básico.

No publiques tokens ni enlaces de Graph que puedan llevar tokens en la query.

## 6. Exponer la API para Meta con ngrok

En otra ventana PowerShell ejecuta:

```powershell
ngrok http 8000
```

Deja la ventana abierta y copia el dominio HTTPS que aparezca en `Forwarding`.
Ejemplo ficticio:

```text
https://<dominio-publico-del-tunel>
```

La Callback URL completa debe quedar así:

```text
https://<dominio-publico-del-tunel>/webhook
```

Puedes verificar el alcance al health endpoint con la URL actual:

```powershell
Invoke-RestMethod -Uri 'https://<dominio-publico-del-tunel>/health' -Headers @{'ngrok-skip-browser-warning'='true'}
```

El resultado esperado es `status: ok`. Si ngrok se reinicia, verifica su
dominio actual y actualiza Meta si cambió. El dominio de túnel es temporal y no
es apropiado como endpoint productivo permanente.

## 7. Cargar cambios de configuración

Compose lee `.env` al crear el contenedor. Después de cambiar credenciales, IDs
o configuración:

```powershell
Set-Location 'C:\ruta\al\proyecto\barberbot_project'
docker compose up -d --force-recreate web_api
```

Si se modificaron variables que usa el worker:

```powershell
docker compose up -d --force-recreate web_api cron_worker
```

Comprueba después `docker compose ps`.

## 8. Probar el webhook y el agente

### Verificación inicial del webhook

Meta debe aceptar **Verificar y guardar**. Esto confirma el challenge y Verify
Token, no un mensaje real.

### Evento simulado desde Meta

1. Ve a la suscripción `messages` y abre **Probar**.
2. Elige un ejemplo `Incoming Message`.
3. Pulsa **Enviar al servidor**.
4. Revisa ngrok y `docker compose logs -f web_api`.

Esperar `POST /webhook 200 OK` indica que FastAPI aceptó una solicitud de muestra.
El evento simulado puede usar un Phone Number ID ficticio y ser ignorado por el
filtro del código; no es una prueba completa del agente.

### Envío de demostración Meta → móvil

En **Paso 1: Pruébalo**, selecciona el número de prueba, selecciona el teléfono
autorizado como destinatario y pulsa **Enviar mensaje**. Esto envía una plantilla
demostrativa de Meta a tu móvil. No es una respuesta de BarberBot ni prueba
webhooks entrantes.

### Mensaje entrante móvil → agente

1. Mantén ngrok online.
2. En PowerShell, desde la raíz del proyecto:
   ```powershell
   docker compose logs -f web_api
   ```
3. Desde el teléfono añadido y verificado como destinatario, escribe un mensaje
   de texto nuevo **al número de prueba de Meta**.
4. Busca un `POST /webhook` nuevo en logs de Uvicorn y ngrok.
5. Comprueba las líneas siguientes y el chat de WhatsApp para validar la
   respuesta.

`Ctrl+C` detiene la observación de logs, no los contenedores. La ventana de ngrok
debe permanecer abierta.

## 9. Interpretar errores

### Solo aparecen `GET /health`

Son comprobaciones automáticas de Docker, no mensajes. Si no aparece un `POST`,
Meta no está enviando el evento al callback actual. Revisar ngrok, callback,
suscripción `messages`, suscripción WABA y destinatario autorizado.

### `POST /webhook` con 401

La firma no coincide. Revisar que `WHATSAPP_APP_SECRET` pertenezca a la app Meta
que está enviando el evento.

### `Ignoring a webhook for an unconfigured phone number`

El `metadata.phone_number_id` recibido no coincide con
`WHATSAPP_PHONE_NUMBER_ID`. Copiar el Phone Number ID del número que originó el
evento, actualizar `.env`, recrear `web_api` y probar con un mensaje nuevo.

### `429 insufficient_quota` / `credit_balance_exhausted`

El proyecto/organización asociado a `OPENAI_API_KEY` no tiene cuota disponible.
Consultar Billing en OpenAI y elegir una solución de coste aceptable. ChatGPT y
la API son facturaciones distintas. No habilitar auto-recarga ni comprar crédito
sin consentimiento.

### `Could not send the WhatsApp response`

El envío saliente falló. Revisar el detalle de Graph API en los logs y confirmar
token de acceso vigente, Phone Number ID, versión de Graph API, destinatario y
restricciones del modo de prueba. No compartir el token al pedir ayuda.

### Interpretar HTTP 200 correctamente

`POST /webhook 200 OK` significa que el endpoint recibió y aceptó la petición
HTTP. No garantiza que el evento coincida con el Phone Number ID, que el modelo
genere texto o que Cloud API acepte el mensaje saliente.

## 10. Activar recordatorios

`cron_worker` revisa aproximadamente cada hora las citas confirmadas de las
siguientes 24 horas y usa una plantilla de WhatsApp. Configura el nombre exacto
de una plantilla aprobada en `WHATSAPP_REMINDER_TEMPLATE_NAME` y su idioma en
`WHATSAPP_REMINDER_TEMPLATE_LANGUAGE`.

El cuerpo debe admitir los parámetros que usa `worker.py`, en este orden:

1. Nombre del cliente.
2. Servicio.
3. Fecha y hora.
4. Profesional.

Consulta los logs del worker:

```powershell
docker compose logs -f cron_worker
```

Esta plantilla es distinta de la respuesta de texto del agente dentro de la
ventana de servicio.

## 11. Flujo de reservas previsto

1. El cliente solicita una cita.
2. El agente consulta servicios y espera elección explícita.
3. Valida profesionales y obtiene la preferencia.
4. Consulta los huecos disponibles para fecha, servicio y profesional.
5. El cliente selecciona hora.
6. El agente resume los detalles y solicita confirmación explícita.
7. Tras confirmación, crea la cita si el horario sigue libre.
8. El worker programa su recordatorio.

La base de datos impide solapamientos activos. El agente solo procesa texto.
Aunque el prompt menciona cancelación y reprogramación, actualmente no hay
herramientas implementadas para realizarlas.

## 12. Lista antes de producción

- Sustituir ngrok de desarrollo por infraestructura y URL HTTPS estables.
- Proteger secretos con un gestor de secretos adecuado y rotar los expuestos.
- Establecer copias de seguridad y monitorización de base de datos y servicios.
- Revisar permisos, estado Live, límites y requisitos de Meta para el negocio.
- Confirmar número real, destinatarios, plantillas y cumplimiento aplicable.
- Establecer límites de gasto de OpenAI o implementar una alternativa y probarla.
- Cambiar la fecha de anclaje estática del prompt por lógica dinámica.
- Implementar pruebas automatizadas de firma, filtros, reserva y envío.
- Implementar cancelación/reprogramación o evitar ofrecer esas funciones.
- Probar el flujo completo de mensajes, reservas y recordatorios antes de atender
  clientes reales.
