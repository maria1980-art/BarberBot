# BarberBot: configuración realizada y estado

Versión preparada para compartir en un repositorio. Los nombres, rutas y
dominios de ejemplo son ficticios o marcadores; no se incluyen credenciales ni
valores del archivo `.env`.

Para desplegar desde cero, consulta
[MANUAL_PUESTA_EN_MARCHA_PUBLICO.md](./MANUAL_PUESTA_EN_MARCHA_PUBLICO.md).

## Resumen del proyecto

BarberBot es un agente de reservas para una barbería/peluquería. Recibe mensajes
de WhatsApp mediante Meta WhatsApp Cloud API, consulta un modelo de OpenAI,
accede a PostgreSQL para consultar disponibilidad y guardar citas, y responde al
cliente por WhatsApp.

Componentes principales:

- `main.py`: API FastAPI, healthcheck y endpoints de verificación y recepción
  del webhook.
- `src/agente_ia.py`: instrucciones conversacionales, herramientas y
  orquestación del turno.
- `src/tools.py`: consultas de servicios y profesionales y creación de reservas.
- `src/disponibilidad.py`: cálculo de huecos de agenda.
- `src/memoria.py`: persistencia del historial conversacional.
- `worker.py`: envío periódico de recordatorios.
- `init_db.sql`: esquema y seed de demostración.
- `migrations/001_recordatorios.sql`: columnas e índice de recordatorios.
- `Dockerfile` y `docker-compose.yml`: construcción y ejecución de servicios.

## Arquitectura del mensaje

1. Meta envía un evento a `POST /webhook`.
2. La API valida la firma HMAC usando `WHATSAPP_APP_SECRET`.
3. Se extraen mensajes de texto. Eventos cuyo `metadata.phone_number_id` no
   coincide con `WHATSAPP_PHONE_NUMBER_ID` se ignoran.
4. FastAPI acepta el webhook y programa el procesamiento en segundo plano.
5. El agente recupera historial, guarda el mensaje y llama al modelo configurado
   con `OPENAI_API_KEY` y `OPENAI_MODEL`.
6. El modelo puede consultar servicios y profesionales, revisar disponibilidad y
   crear una reserva mediante herramientas.
7. La respuesta se envía por WhatsApp Cloud API.

`GET /health` devuelve `{"status":"ok"}`. Solo comprueba que la API está viva;
no prueba las conexiones a Meta, OpenAI o PostgreSQL ni el flujo completo.

## Configuración documentada

Se creó un `.env` local a partir de `.env.example`. El archivo real no se incluye
en esta copia. El `.gitignore` excluye `.env` y variantes locales.

Variables principales:

| Variable | Propósito |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Credenciales y nombre de PostgreSQL. |
| `DATABASE_URL` | Conexión a PostgreSQL; dentro de Compose el host es `postgres_db`. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Acceso y modelo del agente. |
| `BARBERBOT_LOCAL_ID` | ID del local de la agenda. |
| `BUSINESS_TIMEZONE` | Zona horaria IANA del negocio. |
| `WHATSAPP_VERIFY_TOKEN` | Secreto elegido para verificar el callback de Meta. |
| `WHATSAPP_APP_SECRET` | App Secret usado para comprobar la firma de webhooks. |
| `WHATSAPP_API_TOKEN` | Token de acceso para enviar mensajes con Cloud API. |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número emisor de Cloud API. |
| `WHATSAPP_GRAPH_API_VERSION` | Versión de Graph API con formato `vNN.N`. |
| `WHATSAPP_REMINDER_TEMPLATE_NAME` | Nombre de plantilla aprobada de recordatorio. |
| `WHATSAPP_REMINDER_TEMPLATE_LANGUAGE` | Idioma aprobado de la plantilla. |
| `LOG_LEVEL` | Nivel de logging del worker. |

No confundir el Phone Number ID con el número telefónico visible ni con el WABA
ID. El código usa el Phone Number ID y no requiere una variable separada para el
WABA ID.

## Infraestructura comprobada

Se validó `docker compose config --quiet` y se levantaron los servicios
`postgres_db`, `web_api` y `cron_worker`. PostgreSQL y la API aparecieron
saludables. La API respondió en `http://localhost:8000/health` con:

```json
{"status":"ok"}
```

También se verificó que la API era alcanzable mediante el túnel HTTPS temporal.
Para recrear la API después de cambiar su entorno:

```powershell
Set-Location 'C:\ruta\al\proyecto\barberbot_project'
docker compose up -d --force-recreate web_api
```

La ruta anterior es ficticia: ejecútese desde la carpeta real que contiene
`docker-compose.yml`.

## Configuración Meta/ngrok comprobada

Se creó una aplicación de demostración en Meta for Developers, se añadió el
caso de uso WhatsApp, se verificó y guardó el callback y se suscribió el campo
`messages`.

Durante las pruebas, el dominio del túnel debe tratarse como temporal. Ejemplo
ficticio de callback:

```text
https://<dominio-publico-del-tunel>/webhook
```

Se verificó la suscripción de la aplicación a la WABA mediante Graph API; el
resultado incluyó la app de demostración. No se guardan aquí IDs, tokens ni la
URL real del túnel.

Meta entregó una plantilla de demostración al teléfono de prueba, y una prueba
manual del webhook produjo `POST /webhook` con HTTP 200. Una muestra del panel
también produjo el aviso `Ignoring a webhook for an unconfigured phone number`
cuando el ID de la muestra no coincidió con el Phone Number ID configurado. Un
HTTP 200 confirma la recepción HTTP, no el procesamiento ni el envío de una
respuesta.

## Validaciones realizadas

- `python -m compileall` terminó sin errores de sintaxis.
- `docker compose config --quiet` terminó correctamente.
- `GET /health` respondió correctamente localmente y a través del túnel.
- Los servicios de Docker llegaron a estar activos y saludables.
- Meta verificó el callback; el campo `messages` quedó suscrito.
- La app apareció en la consulta de aplicaciones suscritas a la WABA.
- Se observó un mensaje entrante que alcanzó la llamada al modelo.

No se encontró una suite de pruebas automatizadas en el proyecto.

## Bloqueo actual

La llamada al modelo falló con `429 insufficient_quota` y
`credit_balance_exhausted`: la cuenta/proyecto de API asociado a la clave no
tenía cuota disponible. La compra de crédito no se realizó. Por tanto, no se
completó una respuesta del agente de extremo a extremo.

ChatGPT y la API de OpenAI tienen facturación separada. La app no debe activar
recargas automáticas ni incurrir en gastos sin autorización. Mientras la API no
tenga cuota o no se implemente otra opción de modelo, el agente no podrá
generar respuestas.

La API captura el error de OpenAI e intenta enviar un mensaje alternativo. Ese
envío depende también de que el token de acceso y el Phone Number ID de Meta
sean válidos; no se confirmó su éxito de extremo a extremo.

## Pendiente antes de considerar el flujo operativo

1. Resolver la cuota de OpenAI mediante una opción de coste aceptable o
   implementar y validar otro proveedor/modelo.
2. Confirmar que `WHATSAPP_API_TOKEN` es válido y corresponde a la app/número
   configurado.
3. Confirmar que `WHATSAPP_PHONE_NUMBER_ID` corresponde al número remitente.
4. Enviar un mensaje nuevo y verificar tanto la respuesta del modelo como la
   respuesta saliente de WhatsApp.
5. Probar reservas, concurrencia de horarios y recordatorios.
6. Preparar un endpoint estable, observabilidad, políticas y requisitos de Meta
   antes de producción.

La verificación comercial, el registro de un número real y una plantilla
aprobada no se completaron. La verificación comercial es opcional para pruebas
básicas con el número de prueba; no soluciona problemas de cuota, IDs o tokens.

## Limitaciones conocidas del código

- Solo se procesan mensajes entrantes de tipo texto.
- El prompt menciona cancelación y reprogramación, pero no hay herramientas que
  realicen esas operaciones.
- La fecha de anclaje conversacional es estática en `src/agente_ia.py` y debe
  hacerse dinámica antes de operar en fechas futuras.
- No hay una suite automatizada que cubra webhooks, reservas o worker.

## Diagnóstico rápido

Ejecutar desde la carpeta del proyecto:

```powershell
docker compose ps
docker compose logs --since 10m --no-color web_api
docker compose logs -f web_api
```

- `GET /health 200 OK`: healthcheck, no indica un mensaje entrante.
- `POST /webhook 200 OK`: se aceptó la petición HTTP, pero aún se debe comprobar
  procesamiento, filtros y envío.
- `Ignoring a webhook for an unconfigured phone number`: revisar
  `WHATSAPP_PHONE_NUMBER_ID`.
- `429 insufficient_quota`: revisar cuota de la API de OpenAI.
- `Could not send the WhatsApp response`: revisar el error de Cloud API, el
  token de acceso y el Phone Number ID. No publicar el token.
- Sin `POST`: revisar ngrok, URL callback, suscripción `messages`, suscripción a
  WABA y destinatarios/números de prueba.
