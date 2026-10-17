"""Webhook de Meta y despacho asíncrono de respuestas de WhatsApp."""

import hashlib
import hmac
import json
import logging
import os
import re
from typing import Any

import httpx
import psycopg2
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAIError

from src.agente_ia import procesar_mensaje_usuario

logger = logging.getLogger(__name__)
app = FastAPI(title="BarberBot WhatsApp API")


@app.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


def _required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not configured.")
    return value


async def _enviar_mensaje_whatsapp(whatsapp_id: str, texto: str) -> None:
    api_token = _required_setting("WHATSAPP_API_TOKEN")
    phone_number_id = _required_setting("WHATSAPP_PHONE_NUMBER_ID")
    graph_version = _required_setting("WHATSAPP_GRAPH_API_VERSION")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+", graph_version):
        raise RuntimeError("WHATSAPP_GRAPH_API_VERSION must look like vNN.N.")
    url = (
        f"https://graph.facebook.com/{graph_version}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": whatsapp_id,
        "type": "text",
        "text": {"preview_url": False, "body": texto[:4096]},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_token}"},
            json=payload,
        )
        response.raise_for_status()


async def _procesar_y_responder(whatsapp_id: str, texto_usuario: str) -> None:
    try:
        respuesta = await procesar_mensaje_usuario(whatsapp_id, texto_usuario)
    except (OpenAIError, psycopg2.Error, RuntimeError, ValueError):
        logger.exception("Could not process an incoming WhatsApp message.")
        respuesta = (
            "Lo siento, ahora mismo no puedo revisar la agenda 🙏\n\n"
            "¿Podrías intentarlo de nuevo dentro de unos minutos?"
        )

    try:
        await _enviar_mensaje_whatsapp(whatsapp_id, respuesta)
    except (httpx.HTTPError, RuntimeError):
        logger.exception("Could not send the WhatsApp response.")


def _extract_incoming_texts(payload: dict[str, Any]) -> list[tuple[str, str]]:
    incoming: list[tuple[str, str]] = []
    seen_message_ids: set[str] = set()
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return incoming

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata", {})
            if not isinstance(metadata, dict):
                continue
            configured_phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
            if not configured_phone_id:
                raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID is not configured.")
            if metadata.get("phone_number_id") != configured_phone_id:
                logger.warning("Ignoring a webhook for an unconfigured phone number.")
                continue

            messages = value.get("messages", [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue
                sender = message.get("from")
                message_id = message.get("id")
                text_content = message.get("text")
                body = text_content.get("body") if isinstance(text_content, dict) else None
                if not all(
                    isinstance(item, str) and item
                    for item in (sender, message_id, body)
                ):
                    logger.warning("Ignoring a malformed text message in webhook.")
                    continue
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                incoming.append((sender, body))
    return incoming


@app.get("/webhook", response_class=PlainTextResponse)
async def verificar_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    if mode != "subscribe" or challenge is None:
        raise HTTPException(status_code=400, detail="Invalid webhook verification request.")
    expected_token = _required_setting("WHATSAPP_VERIFY_TOKEN")
    if verify_token is None or not hmac.compare_digest(verify_token, expected_token):
        raise HTTPException(status_code=403, detail="Webhook verification failed.")
    return PlainTextResponse(challenge, status_code=200)


@app.post("/webhook")
async def recibir_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    raw_body = await request.body()
    app_secret = _required_setting("WHATSAPP_APP_SECRET").encode("utf-8")
    received_signature = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = "sha256=" + hmac.new(
        app_secret,
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received_signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

    for whatsapp_id, texto_usuario in _extract_incoming_texts(payload):
        background_tasks.add_task(
            _procesar_y_responder,
            whatsapp_id,
            texto_usuario,
        )
    return {"status": "accepted"}
