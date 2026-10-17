"""Persistencia cronológica del historial de WhatsApp."""

import os
import re
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


def _normalizar_whatsapp_id(whatsapp_id: str) -> str:
    if not isinstance(whatsapp_id, str):
        raise TypeError("whatsapp_id must be a string.")
    normalized = re.sub(r"[\s()+.-]", "", whatsapp_id)
    if not re.fullmatch(r"[1-9][0-9]{7,14}", normalized):
        raise ValueError(
            "whatsapp_id must be an international phone number in E.164 format."
        )
    return normalized


def guardar_mensaje(whatsapp_id: str, rol: str, contenido: str) -> None:
    """Store one user or assistant message for a WhatsApp conversation."""
    telefono = _normalizar_whatsapp_id(whatsapp_id)
    if rol not in {"user", "assistant"}:
        raise ValueError("rol must be either 'user' or 'assistant'.")
    if not isinstance(contenido, str) or not contenido.strip():
        raise ValueError("contenido must be a non-empty string.")

    connection = psycopg2.connect(_database_url())
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO historial_conversaciones
                        (whatsapp_id, rol, contenido)
                    VALUES (%s, %s, %s)
                    """,
                    (telefono, rol, contenido),
                )
    finally:
        connection.close()


def obtener_historial_abierto(
    whatsapp_id: str,
    limite: int = 15,
) -> list[dict[str, Any]]:
    """Return the latest messages in chronological order (oldest first)."""
    telefono = _normalizar_whatsapp_id(whatsapp_id)
    if isinstance(limite, bool) or not isinstance(limite, int) or limite <= 0:
        raise ValueError("limite must be a positive integer.")

    connection = psycopg2.connect(_database_url())
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT rol, contenido, created_at
                    FROM (
                        SELECT id, rol, contenido, created_at
                        FROM historial_conversaciones
                        WHERE whatsapp_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                    ) AS mensajes_recientes
                    ORDER BY created_at ASC, id ASC
                    """,
                    (telefono, limite),
                )
                return [dict(mensaje) for mensaje in cursor.fetchall()]
    finally:
        connection.close()
