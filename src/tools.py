"""Database operations used by the conversational booking agent."""

import os
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg2
from psycopg2.errors import ExclusionViolation
from psycopg2.extras import RealDictCursor


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


def obtener_servicios(local_id: int) -> list[dict[str, Any]]:
    """Return valid services for one local, without exposing other tenants."""
    connection = psycopg2.connect(_database_url())
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, local_id, nombre, precio, duracion_minutos
                    FROM servicios
                    WHERE local_id = %s
                      AND duracion_minutos > 0
                    ORDER BY nombre
                    """,
                    (local_id,),
                )
                return [
                    {
                        **dict(service),
                        "precio": str(service["precio"]),
                    }
                    for service in cursor.fetchall()
                ]
    finally:
        connection.close()


def obtener_barberos(local_id: int) -> list[dict[str, Any]]:
    """Return active professionals for one local."""
    connection = psycopg2.connect(_database_url())
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, local_id, nombre
                    FROM barberos
                    WHERE local_id = %s
                      AND activo = TRUE
                    ORDER BY nombre
                    """,
                    (local_id,),
                )
                return [dict(barbero) for barbero in cursor.fetchall()]
    finally:
        connection.close()


def _parse_aware_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "fecha_hora must be a valid ISO 8601 datetime."
            ) from exc
    else:
        raise TypeError("fecha_hora must be a string or datetime.")

    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(
            "fecha_hora must include a timezone, for example +02:00 or Z."
        )
    return result


def _normalize_whatsapp_id(whatsapp_id: str) -> str:
    normalized = re.sub(r"[\s()+.-]", "", whatsapp_id)
    if not re.fullmatch(r"[1-9][0-9]{7,14}", normalized):
        raise ValueError(
            "whatsapp_id must be an international phone number in E.164 format."
        )
    return normalized


def crear_reserva(
    *,
    local_id: int,
    whatsapp_id: str,
    barbero_id: int,
    servicio_id: int,
    fecha_hora: str | datetime,
    nombre_cliente: str | None = None,
) -> dict[str, Any]:
    """Create a booking atomically, deriving its end from the service duration.

    `fecha_hora` must include a timezone. `local_id` is explicit to preserve
    tenant isolation in the multi-local SaaS schema.
    """
    inicio = _parse_aware_datetime(fecha_hora)
    telefono = _normalize_whatsapp_id(whatsapp_id)
    database_url = _database_url()
    connection = psycopg2.connect(database_url)

    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT s.duracion_minutos, s.precio
                    FROM servicios AS s
                    JOIN barberos AS b
                      ON b.local_id = s.local_id
                    WHERE s.id = %s
                      AND s.local_id = %s
                      AND b.id = %s
                      AND b.activo = TRUE
                      AND s.duracion_minutos > 0
                    FOR SHARE OF s, b
                    """,
                    (servicio_id, local_id, barbero_id),
                )
                servicio = cursor.fetchone()
                if servicio is None:
                    return {
                        "status": "error",
                        "error_type": "servicio_no_disponible",
                    }

                duracion_minutos = servicio["duracion_minutos"]
                fin = inicio + timedelta(minutes=duracion_minutos)

                cursor.execute(
                    """
                    INSERT INTO clientes (whatsapp_id, nombre)
                    VALUES (%s, %s)
                    ON CONFLICT (whatsapp_id)
                    DO UPDATE SET nombre = COALESCE(
                        clientes.nombre,
                        EXCLUDED.nombre
                    )
                    RETURNING id
                    """,
                    (telefono, nombre_cliente),
                )
                cliente = cursor.fetchone()

                cursor.execute(
                    """
                    INSERT INTO citas (
                        local_id,
                        barbero_id,
                        cliente_id,
                        servicio_id,
                        fecha_hora,
                        fecha_fin,
                        estado
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'confirmada')
                    RETURNING id
                    """,
                    (
                        local_id,
                        barbero_id,
                        cliente["id"],
                        servicio_id,
                        inicio,
                        fin,
                    ),
                )
                cita = cursor.fetchone()

        return {
            "status": "ok",
            "cita_id": cita["id"],
            "fecha_hora": inicio.isoformat(),
            "fecha_fin": fin.isoformat(),
            "duracion_minutos": duracion_minutos,
            "precio": str(Decimal(servicio["precio"])),
        }
    except ExclusionViolation as exc:
        connection.rollback()
        if exc.diag.constraint_name != "citas_sin_solapamiento":
            raise
        return {
            "status": "error",
            "error_type": "horario_ocupado",
        }
    finally:
        connection.close()
