"""Consulta de disponibilidad de profesionales para un servicio y una fecha."""

import os
from datetime import date, datetime, time, timedelta
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


def _parse_fecha(fecha_str: str) -> date:
    if not isinstance(fecha_str, str):
        raise TypeError("fecha_str must be a string in YYYY-MM-DD format.")
    try:
        parsed = date.fromisoformat(fecha_str)
    except ValueError as exc:
        raise ValueError("fecha_str must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != fecha_str:
        raise ValueError("fecha_str must use YYYY-MM-DD format.")
    return parsed


def verificar_disponibilidad(
    fecha_str: str,
    barbero_id: int,
    servicio_id: int,
) -> list[str]:
    """Return available start times, in the database session's local timezone.

    Candidate start times are evaluated in 30-minute increments from opening.
    Every candidate must fit the complete service duration within business
    hours and must not overlap any pending or confirmed appointment.
    """
    fecha = _parse_fecha(fecha_str)
    if isinstance(barbero_id, bool) or not isinstance(barbero_id, int) or barbero_id <= 0:
        raise ValueError("barbero_id must be a positive integer.")
    if isinstance(servicio_id, bool) or not isinstance(servicio_id, int) or servicio_id <= 0:
        raise ValueError("servicio_id must be a positive integer.")

    connection = psycopg2.connect(_database_url())
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('TimeZone', %s, true)",
                    (os.getenv("BUSINESS_TIMEZONE", "Europe/Madrid"),),
                )
                cursor.execute(
                    """
                    SELECT l.horario_apertura, l.horario_cierre,
                           s.duracion_minutos
                    FROM barberos AS b
                    JOIN locales AS l ON l.id = b.local_id
                    JOIN servicios AS s ON s.local_id = b.local_id
                    WHERE b.id = %s
                      AND b.activo = TRUE
                      AND s.id = %s
                      AND s.duracion_minutos > 0
                    """,
                    (barbero_id, servicio_id),
                )
                agenda = cursor.fetchone()
                if agenda is None:
                    return []

                apertura = agenda["horario_apertura"]
                cierre = agenda["horario_cierre"]
                duracion = timedelta(minutes=agenda["duracion_minutos"])
                apertura_local = datetime.combine(fecha, apertura)
                cierre_local = datetime.combine(fecha, cierre)
                if cierre_local <= apertura_local:
                    return []

                cursor.execute(
                    """
                    SELECT
                        fecha_hora AT TIME ZONE current_setting('TIMEZONE')
                            AS inicio_local,
                        fecha_fin AT TIME ZONE current_setting('TIMEZONE')
                            AS fin_local
                    FROM citas
                    WHERE barbero_id = %s
                      AND estado IN ('pendiente', 'confirmada')
                      AND fecha_hora < (
                          (%s::date + %s::time)
                          AT TIME ZONE current_setting('TIMEZONE')
                      )
                      AND fecha_fin > (
                          (%s::date + %s::time)
                          AT TIME ZONE current_setting('TIMEZONE')
                      )
                    ORDER BY fecha_hora
                    """,
                    (barbero_id, fecha, cierre, fecha, apertura),
                )
                citas = cursor.fetchall()

        slots: list[str] = []
        inicio_slot = apertura_local
        paso = timedelta(minutes=30)
        while inicio_slot + duracion <= cierre_local:
            fin_slot = inicio_slot + duracion
            ocupado = any(
                inicio_slot < cita["fin_local"]
                and fin_slot > cita["inicio_local"]
                for cita in citas
            )
            if not ocupado:
                slots.append(inicio_slot.strftime("%H:%M"))
            inicio_slot += paso
        return slots
    finally:
        connection.close()
