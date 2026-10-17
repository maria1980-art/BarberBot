"""Hourly worker for WhatsApp appointment reminders."""

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler
from psycopg2.extras import RealDictCursor

logger = logging.getLogger("barberbot.reminders")
CLAIM_TIMEOUT = timedelta(minutes=30)
REMINDER_WINDOW = timedelta(hours=24)


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not configured.")
    return value


def _business_timezone() -> ZoneInfo:
    timezone_name = os.getenv("BUSINESS_TIMEZONE", "Europe/Madrid")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"BUSINESS_TIMEZONE is not a valid IANA timezone: {timezone_name}"
        ) from exc


def _claim_upcoming_reminders() -> list[dict[str, Any]]:
    now = datetime.now(_business_timezone())
    cutoff = now + REMINDER_WINDOW
    claim_expiration = now - CLAIM_TIMEOUT
    connection = psycopg2.connect(_required_setting("DATABASE_URL"))
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    WITH citas_candidatas AS (
                        SELECT c.id
                        FROM citas AS c
                        WHERE c.estado = 'confirmada'
                          AND c.fecha_hora > %s
                          AND c.fecha_hora <= %s
                          AND c.recordatorio_enviado_at IS NULL
                          AND (
                              c.recordatorio_procesando_at IS NULL
                              OR c.recordatorio_procesando_at < %s
                          )
                        ORDER BY c.fecha_hora, c.id
                        FOR UPDATE OF c SKIP LOCKED
                    ),
                    citas_reclamadas AS (
                        UPDATE citas AS c
                        SET recordatorio_procesando_at = %s
                        FROM citas_candidatas AS pendientes
                        WHERE c.id = pendientes.id
                        RETURNING c.id, c.local_id, c.cliente_id, c.barbero_id,
                                  c.servicio_id, c.fecha_hora,
                                  c.recordatorio_procesando_at AS claimed_at
                    )
                    SELECT reservadas.id AS cita_id,
                           clientes.whatsapp_id,
                           COALESCE(NULLIF(clientes.nombre, ''), 'Hola') AS nombre_cliente,
                           servicios.nombre AS nombre_servicio,
                           barberos.nombre AS nombre_barbero,
                           reservadas.fecha_hora AT TIME ZONE %s AS fecha_hora_local,
                           reservadas.claimed_at
                    FROM citas_reclamadas AS reservadas
                    JOIN clientes ON clientes.id = reservadas.cliente_id
                    JOIN servicios ON servicios.id = reservadas.servicio_id
                                  AND servicios.local_id = reservadas.local_id
                    JOIN barberos ON barberos.id = reservadas.barbero_id
                                 AND barberos.local_id = reservadas.local_id
                    ORDER BY reservadas.fecha_hora, reservadas.id
                    """,
                    (
                        now,
                        cutoff,
                        claim_expiration,
                        now,
                        _business_timezone().key,
                    ),
                )
                return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _send_reminder(reminder: dict[str, Any]) -> None:
    api_token = _required_setting("WHATSAPP_API_TOKEN")
    phone_number_id = _required_setting("WHATSAPP_PHONE_NUMBER_ID")
    graph_version = _required_setting("WHATSAPP_GRAPH_API_VERSION")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+", graph_version):
        raise RuntimeError("WHATSAPP_GRAPH_API_VERSION must look like vNN.N.")
    template_name = _required_setting("WHATSAPP_REMINDER_TEMPLATE_NAME")
    template_language = _required_setting("WHATSAPP_REMINDER_TEMPLATE_LANGUAGE")

    appointment_time = reminder["fecha_hora_local"].strftime("%d/%m a las %H:%M")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": reminder["whatsapp_id"],
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": reminder["nombre_cliente"]},
                        {"type": "text", "text": reminder["nombre_servicio"]},
                        {"type": "text", "text": appointment_time},
                        {"type": "text", "text": reminder["nombre_barbero"]},
                    ],
                }
            ],
        },
    }
    url = (
        f"https://graph.facebook.com/{graph_version}/"
        f"{phone_number_id}/messages"
    )
    with httpx.Client(timeout=20.0) as client:
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_token}"},
            json=payload,
        )
        response.raise_for_status()


def _mark_reminder_sent(cita_id: int, claimed_at: datetime) -> None:
    connection = psycopg2.connect(_required_setting("DATABASE_URL"))
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE citas
                    SET recordatorio_enviado_at = NOW(),
                        recordatorio_procesando_at = NULL
                    WHERE id = %s
                      AND recordatorio_enviado_at IS NULL
                      AND recordatorio_procesando_at = %s
                    """,
                    (cita_id, claimed_at),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"Could not mark reminder as sent for appointment {cita_id}."
                    )
    finally:
        connection.close()


def _release_reminder_claim(cita_id: int, claimed_at: datetime) -> None:
    connection = psycopg2.connect(_required_setting("DATABASE_URL"))
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE citas
                    SET recordatorio_procesando_at = NULL
                    WHERE id = %s
                      AND recordatorio_enviado_at IS NULL
                      AND recordatorio_procesando_at = %s
                    """,
                    (cita_id, claimed_at),
                )
    finally:
        connection.close()


def enviar_recordatorios_pendientes() -> int:
    """Claim and send due reminders; return the number successfully sent."""
    reminders = _claim_upcoming_reminders()
    sent_count = 0
    for reminder in reminders:
        cita_id = reminder["cita_id"]
        try:
            _send_reminder(reminder)
            _mark_reminder_sent(cita_id, reminder["claimed_at"])
            sent_count += 1
            logger.info("Reminder sent for appointment id=%s.", cita_id)
        except (httpx.HTTPError, psycopg2.Error, RuntimeError):
            logger.exception("Reminder delivery failed for appointment id=%s.", cita_id)
            _release_reminder_claim(cita_id, reminder["claimed_at"])
    return sent_count


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    timezone = _business_timezone()
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        enviar_recordatorios_pendientes,
        trigger="interval",
        hours=1,
        id="whatsapp_appointment_reminders",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        next_run_time=datetime.now(timezone),
    )
    logger.info("Reminder worker started; timezone=%s.", timezone.key)
    scheduler.start()


if __name__ == "__main__":
    main()
