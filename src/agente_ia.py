"""Orquestador conversacional con herramientas de agenda."""

import json
import os
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import AsyncOpenAI

from src import disponibilidad, memoria, tools

FECHA_ANCLAJE = date(2026, 10, 9)
SYSTEM_PROMPT = """
Eres Asistente Estilo, la recepcionista virtual de una barbería y peluquería.
Tu único objetivo es gestionar citas por WhatsApp. Habla en español, con tono
profesional, cercano y conciso; usa saltos de línea dobles y emojis moderados.
Nunca menciones detalles técnicos ni afirmes haber consultado algo sin usar la
herramienta correspondiente.

Sigue esta máquina de estados en orden:
1. Servicio: saluda y, al detectar interés en una cita, llama inmediatamente a
   obtener_servicios. Muestra nombre, precio y duración; espera una elección
   explícita.
2. Profesional: pregunta por una persona o por cualquier profesional
   disponible. Usa obtener_barberos para validar nombres e IDs; no inventes.
3. Fecha y disponibilidad: pregunta el día. La fecha de referencia es viernes
   9 de octubre de 2026: mañana es 2026-10-10 y el próximo lunes es 2026-10-12.
   Convierte fechas relativas a YYYY-MM-DD. Después llama inmediatamente a
   verificar_disponibilidad con la fecha, el barbero y el servicio elegidos.
   Nunca inventes horarios; muestra como máximo cuatro inicios devueltos. Si la
   lista está vacía, explica que ese día no hay huecos y pregunta si prefiere
   otra fecha o profesional.
4. Confirmación: tras elegir hora, resume servicio, profesional, fecha y hora,
   y pregunta si está correcto. Solo después de una confirmación explícita
   llama a crear_reserva. Si devuelve éxito, confirma la reserva e informa que
   recibirá un recordatorio un día antes.

Si crear_reserva devuelve error_type "horario_ocupado", discúlpate con empatía
y ofrece buscar inmediatamente otro hueco. No afirmes que una cita se creó
cuando la herramienta no confirmó el éxito. Para cancelar o reprogramar,
solicita el nombre o identificador de la cita. Para otros temas, redirige
amablemente a los servicios del local.
""".strip()

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "obtener_servicios",
            "description": "Lista los servicios disponibles en el local.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_barberos",
            "description": "Lista los profesionales activos del local.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verificar_disponibilidad",
            "description": "Consulta los inicios disponibles para una fecha.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fecha_str": {
                        "type": "string",
                        "description": "Fecha YYYY-MM-DD.",
                    },
                    "barbero_id": {"type": "integer"},
                    "servicio_id": {"type": "integer"},
                },
                "required": ["fecha_str", "barbero_id", "servicio_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_reserva",
            "description": (
                "Confirma una reserva SOLO tras el sí explícito del cliente "
                "al resumen completo de servicio, profesional, fecha y hora."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "barbero_id": {"type": "integer"},
                    "servicio_id": {"type": "integer"},
                    "fecha_str": {
                        "type": "string",
                        "description": "Fecha YYYY-MM-DD.",
                    },
                    "hora_inicio": {
                        "type": "string",
                        "description": "Hora HH:MM.",
                    },
                    "nombre_cliente": {"type": ["string", "null"]},
                },
                "required": [
                    "barbero_id",
                    "servicio_id",
                    "fecha_str",
                    "hora_inicio",
                    "nombre_cliente",
                ],
                "additionalProperties": False,
            },
        },
    },
]


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not configured.")
    return value


def _local_id() -> int:
    raw_value = _required_setting("BARBERBOT_LOCAL_ID")
    try:
        local_id = int(raw_value)
    except ValueError as exc:
        raise RuntimeError("BARBERBOT_LOCAL_ID must be a positive integer.") from exc
    if local_id <= 0:
        raise RuntimeError("BARBERBOT_LOCAL_ID must be a positive integer.")
    return local_id


def _business_timezone() -> ZoneInfo:
    timezone_name = os.getenv("BUSINESS_TIMEZONE", "Europe/Madrid")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"BUSINESS_TIMEZONE is not a valid IANA timezone: {timezone_name}"
        ) from exc


def _invoke_tool(name: str, arguments: dict[str, Any], whatsapp_id: str) -> Any:
    local_id = _local_id()
    if name == "obtener_servicios":
        return tools.obtener_servicios(local_id)
    if name == "obtener_barberos":
        return tools.obtener_barberos(local_id)
    if name == "verificar_disponibilidad":
        slots = disponibilidad.verificar_disponibilidad(
            fecha_str=arguments["fecha_str"],
            barbero_id=arguments["barbero_id"],
            servicio_id=arguments["servicio_id"],
        )
        return {"horas_disponibles": slots[:4]}
    if name == "crear_reserva":
        fecha = date.fromisoformat(arguments["fecha_str"])
        hora = time.fromisoformat(arguments["hora_inicio"])
        if hora.tzinfo is not None or hora.second or hora.microsecond:
            raise ValueError("hora_inicio must use HH:MM local time.")
        fecha_hora = datetime.combine(fecha, hora, tzinfo=_business_timezone())
        if fecha < FECHA_ANCLAJE or fecha_hora <= datetime.now(fecha_hora.tzinfo):
            raise ValueError("No se pueden reservar fechas u horas pasadas.")
        return tools.crear_reserva(
            local_id=local_id,
            whatsapp_id=whatsapp_id,
            barbero_id=arguments["barbero_id"],
            servicio_id=arguments["servicio_id"],
            fecha_hora=fecha_hora,
            nombre_cliente=arguments.get("nombre_cliente"),
        )
    raise ValueError(f"Unsupported tool requested by model: {name}")


async def procesar_mensaje_usuario(whatsapp_id: str, texto_usuario: str) -> str:
    """Run one WhatsApp turn, execute booking tools, and persist it."""
    if not isinstance(texto_usuario, str) or not texto_usuario.strip():
        raise ValueError("texto_usuario must be a non-empty string.")

    client = AsyncOpenAI(api_key=_required_setting("OPENAI_API_KEY"))
    try:
        history = memoria.obtener_historial_abierto(whatsapp_id, limite=15)
        memoria.guardar_mensaje(whatsapp_id, "user", texto_usuario)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *[
                {"role": item["rol"], "content": item["contenido"]}
                for item in history
            ],
            {"role": "user", "content": texto_usuario},
        ]

        for _ in range(6):
            response = await client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.2,
            )
            assistant_message = response.choices[0].message
            if not assistant_message.tool_calls:
                answer = assistant_message.content
                if not answer:
                    raise RuntimeError("OpenAI returned an empty assistant response.")
                memoria.guardar_mensaje(whatsapp_id, "assistant", answer)
                return answer

            messages.append(assistant_message.model_dump(exclude_none=True))
            for tool_call in assistant_message.tool_calls:
                tool_error = None
                try:
                    arguments = json.loads(tool_call.function.arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                    result = _invoke_tool(
                        tool_call.function.name,
                        arguments,
                        whatsapp_id,
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    result = {"status": "error", "error_type": "argumentos_invalidos"}
                    tool_error = str(exc)

                if (
                    tool_call.function.name == "crear_reserva"
                    and isinstance(result, dict)
                    and result.get("error_type") == "horario_ocupado"
                ):
                    answer = (
                        "Lo siento, ese horario acaba de ocuparse 🙏\n\n"
                        "¿Quieres que revise otros huecos disponibles para ese día?"
                    )
                    memoria.guardar_mensaje(whatsapp_id, "assistant", answer)
                    return answer

                tool_payload = {"result": result}
                if tool_error:
                    tool_payload["error"] = tool_error
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(
                            tool_payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                )

        raise RuntimeError("OpenAI exceeded the maximum number of tool-call rounds.")
    finally:
        await client.close()
