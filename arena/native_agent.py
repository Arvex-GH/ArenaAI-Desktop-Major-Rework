"""Native Arena Agent Mode protocol helpers (Trigger.dev AI Chat via Arena's proxy).

The wire protocol was reconstructed from arena.ai frontend bundles and the
public Trigger.dev AI Chat documentation; see docs/NATIVE_AGENT_RESEARCH.md.

This module is transport-free: it only builds request bodies/URLs/headers and
parses/maps stream records.  arena.web.app performs the actual HTTP through
the engine window (session creation, needs cookies + reCAPTCHA) and plain
HTTPS requests against the /ai-proxy data plane (PAT bearer auth).
"""

import json
import logging
import os
import time
import urllib.parse
import uuid

LOG = logging.getLogger(__name__)

_KNOWN_STRUCTURAL = {
    "start", "start-step", "finish", "finish-step", "text-start", "text-end",
    "reasoning-start", "reasoning-end", "tool-input-start", "tool-input-delta",
    "tool-input-end", "message-metadata", "raw",
}
_SEEN_UNKNOWN: set = set()


def _note_unknown(chunk) -> None:
    """Log the shape (kind + keys only, never values) of stream chunks this
    client does not understand yet, once per kind per process. Upstream
    media-choice/reveal flows will show up here first."""
    try:
        kind = str((chunk or {}).get("type") or "")
        if not kind or kind in _KNOWN_STRUCTURAL or kind in _SEEN_UNKNOWN:
            return
        _SEEN_UNKNOWN.add(kind)
        keys = sorted(str(k) for k in (chunk or {}).keys())
        LOG.info("native: unknown chunk kind=%s keys=%s", kind, ",".join(keys))
    except Exception:
        pass

TURN_COMPLETE = "trigger:turn-complete"
DEFAULT_TIMEZONE = "Europe/Moscow"
RECAPTCHA_V3_ACTION = "agentic_chat_submit"


def new_uuid() -> str:
    """UUIDv7 (time-ordered): the website generates ids with generateSafeUUIDv7."""
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    raw = bytearray(16)
    raw[0:6] = ms.to_bytes(6, "big")
    raw[6] = 0x70 | (rand[0] & 0x0F)
    raw[7] = rand[1]
    raw[8] = 0x80 | (rand[2] & 0x3F)
    raw[9:16] = rand[3:10]
    return str(uuid.UUID(bytes=bytes(raw)))


def _tz(timezone) -> str:
    return timezone if isinstance(timezone, str) and timezone else DEFAULT_TIMEZONE


def user_message(text: str, message_id=None, files=None) -> dict:
    parts = []
    for item in files or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        media = item.get("mediaType") or item.get("mimeType") or item.get("contentType")
        if not (isinstance(url, str) and url and isinstance(media, str) and media):
            continue
        part = {"type": "file", "url": url, "mediaType": media}
        name = item.get("filename") or item.get("name")
        if isinstance(name, str) and name:
            part["filename"] = name
        parts.append(part)
    parts.append({"type": "text", "text": text})
    return {
        "id": message_id or new_uuid(),
        "role": "user",
        "parts": parts,
    }


def client_data(timezone=None, connectors=None, cx=None) -> dict:
    data = {
        "originHost": "arena.ai",
        "timezone": _tz(timezone),
        "clientPlatform": "web",
    }
    if connectors:
        data["enabledConnectors"] = list(connectors)
    if cx:
        # The website's transport attaches a fresh client-interaction blob
        # to every turn's metadata; ours comes from ENGINE.native_preflight.
        data["cx"] = cx
    return data


def build_create_body(text: str, timezone=None, connectors=None, message_id=None,
                      v2_token=None, cx=None, files=None) -> dict:
    """Body for POST /nextjs-api/stream/create-chat.

    ``recaptchaV3Token: None`` is a placeholder the engine page fetch fills
    in (same convention as evaluation creation).  ``cx`` must be a genuine
    client-interaction blob computed by the page's own module
    (ENGINE.native_preflight); it is omitted when the page cannot provide
    one, and the server then decides whether to accept the submission.
    Synthesized values are rejected by Arena's validation and must never
    be fabricated here.
    """
    body = {
        "message": user_message(text, message_id, files=files),
        "clientPlatform": "web",
        "timezone": _tz(timezone),
    }
    if cx:
        body["cx"] = cx
    if v2_token:
        body["recaptchaV2Token"] = v2_token
        body["recaptchaV3Token"] = None
    else:
        body["recaptchaV3Token"] = None
    if connectors:
        body["enabledConnectors"] = list(connectors)
    return body


def build_append_body(text: str, trigger_address: str, timezone=None,
                      connectors=None, message_id=None, cx=None, files=None) -> dict:
    message = user_message(text, message_id, files=files)
    return {
        "kind": "message",
        "payload": {
            "message": message,
            "chatId": trigger_address,
            "trigger": "submit-message",
            "messageId": message["id"],
            "metadata": client_data(timezone, connectors, cx=cx),
        },
    }


STOP_BODY = {"kind": "stop"}


def out_url(origin: str, trigger_address: str) -> str:
    return (f"{origin.rstrip('/')}/ai-proxy/realtime/v1/sessions/"
            f"{urllib.parse.quote(str(trigger_address), safe='')}/out")


def in_url(origin: str, trigger_address: str) -> str:
    return (f"{origin.rstrip('/')}/ai-proxy/realtime/v1/sessions/"
            f"{urllib.parse.quote(str(trigger_address), safe='')}/in/append")


def stream_headers(pat: str, last_event_id=None) -> dict:
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "text/event-stream",
        "Timeout-Seconds": "120",
    }
    if last_event_id:
        headers["Last-Event-ID"] = str(last_event_id)
    return headers


def append_headers(pat: str) -> dict:
    return {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
        "x-trigger-source": "sdk",
        "X-Part-Id": new_uuid(),
    }


def parse_bootstrap(payload) -> dict:
    """Normalize the create-chat response into session coordinates."""
    if not isinstance(payload, dict):
        return {"session_id": None, "trigger_address": None, "pat": None, "task_id": None}
    boot = payload.get("bootstrap") if isinstance(payload.get("bootstrap"), dict) else {}
    return {
        "session_id": payload.get("id") or boot.get("sessionId"),
        "trigger_address": boot.get("triggerAddress"),
        "pat": boot.get("publicAccessToken"),
        "task_id": boot.get("taskId"),
    }


def _json_or_none(text):
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def records_from_event(event_type, event_id, data_text) -> list:
    """One SSE dispatch -> stream records {id, chunk, headers}.

    Handles both wire shapes the official client accepts: v1 events whose
    data is the chunk JSON itself, and ``batch`` events carrying
    ``{records:[{seq_num, headers:[[k, v]...], body}]}`` where body is a JSON
    string ``{id, data: chunk}``.
    """
    if event_type == "batch":
        payload = _json_or_none(data_text)
        if not isinstance(payload, dict):
            return []
        records = []
        for rec in payload.get("records") or []:
            if not isinstance(rec, dict):
                continue
            headers_list = rec.get("headers") or []
            if headers_list and headers_list[0] and headers_list[0][0] == "":
                continue  # keepalive/control frame in the official client
            parsed = _json_or_none(rec.get("body"))
            chunk = None
            if isinstance(parsed, dict):
                # Batch bodies are envelopes {id, data: chunk}; unwrap only
                # when the body has no chunk "type" of its own so a raw
                # data-* chunk (which carries its own "data") survives.
                chunk = parsed.get("data") if "data" in parsed and "type" not in parsed else parsed
            headers = {}
            for pair in headers_list:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    headers[str(pair[0]).lower()] = pair[1]
            seq = rec.get("seq_num")
            records.append({
                "id": str(seq) if seq is not None else event_id,
                "chunk": chunk,
                "headers": headers,
            })
        return records
    chunk = _json_or_none(data_text)
    if isinstance(chunk, dict) and "chunk" in chunk and isinstance(chunk.get("chunk"), dict):
        return [{"id": event_id, "chunk": chunk["chunk"], "headers": {}}]
    if isinstance(chunk, dict) and "type" not in chunk and isinstance(chunk.get("data"), dict):
        # v1 envelope shape {id, data:{...chunk...}}; real data-* chunks
        # carry their own "type" and are never unwrapped.
        chunk = chunk["data"]
    return [{"id": event_id, "chunk": chunk, "headers": {}}]


def iter_sse_records(lines):
    """Yield stream records from decoded SSE text lines."""
    event_type = None
    event_id = None
    data_parts: list = []

    def dispatch():
        if not data_parts and event_type is None and event_id is None:
            return []
        return records_from_event(event_type, event_id, "\n".join(data_parts))

    for raw_line in lines:
        line = raw_line.rstrip("\r\n") if isinstance(raw_line, str) else ""
        if line == "":
            yield from dispatch()
            event_type, event_id, data_parts = None, None, []
            continue
        if line.startswith(":"):
            continue
        Field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if Field == "event":
            event_type = value
        elif Field == "id":
            event_id = value
        elif Field == "data":
            data_parts.append(value)
    yield from dispatch()


def is_turn_complete(chunk) -> bool:
    return isinstance(chunk, dict) and chunk.get("type") == TURN_COMPLETE


TURN_COMPLETE_CONTROL = "turn-complete"


def is_turn_complete_record(record) -> bool:
    """End of turn the way the website's own transport detects it.

    A batch (v2) record signals the end of the turn through the record
    HEADER ``trigger-control: turn-complete`` and its body is empty, so a
    client that only inspects the chunk ``type`` never sees it: it keeps
    re-subscribing to a quiet stream until the user presses Stop (the
    v4.5.0 bug). The non-batch fallback is a chunk with
    ``type == "trigger:turn-complete"``. Check the whole record.
    """
    if not isinstance(record, dict):
        return False
    headers = record.get("headers") or {}
    if isinstance(headers, dict) and str(headers.get("trigger-control") or "").lower() == TURN_COMPLETE_CONTROL:
        return True
    return is_turn_complete(record.get("chunk"))


def is_control(chunk) -> bool:
    return isinstance(chunk, dict) and str(chunk.get("type") or "").startswith("trigger:")


def refreshed_pat(record):
    headers = record.get("headers") or {}
    pat = headers.get("public-access-token")
    if isinstance(pat, str) and pat:
        return pat
    chunk = record.get("chunk")
    if isinstance(chunk, dict) and chunk.get("publicAccessToken"):
        return chunk["publicAccessToken"]
    return None


def _short(value, limit=4000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            value = str(value)
    return value[:limit]


def map_chunk(chunk, tool_names: dict) -> list:
    """Map one AI SDK UIMessageChunk to this app's chat SSE events (lane 'a')."""
    if not isinstance(chunk, dict):
        return []
    kind = chunk.get("type")
    if kind == "text-delta":
        delta = chunk.get("delta") or chunk.get("textDelta") or ""
        return [{"type": "text", "model": "a", "text": str(delta)}] if delta else []
    if kind == "reasoning-delta":
        delta = chunk.get("delta") or chunk.get("textDelta") or ""
        return [{"type": "reasoning", "model": "a", "text": str(delta)}] if delta else []
    if kind == "tool-input-available":
        call_id = str(chunk.get("toolCallId") or "")
        name = str(chunk.get("toolName") or "tool")
        if call_id:
            tool_names[call_id] = name
        return [{
            "type": "tool_call", "id": call_id, "tool": name, "name": name,
            "model": "a", "title": name, "summary": name, "rel": name,
            "path": _short(chunk.get("input")), "inside": False, "status": "running",
        }]
    if kind in ("tool-output-available", "tool-output-error"):
        call_id = str(chunk.get("toolCallId") or "")
        name = tool_names.get(call_id, "tool")
        ok = kind == "tool-output-available"
        output = chunk.get("output") if ok else (
            chunk.get("errorText") or chunk.get("error") or "")
        return [{
            "type": "tool_result", "id": call_id, "tool": name, "name": name,
            "model": "a", "rel": name, "ok": ok,
            "status": "ok" if ok else "error", "output": _short(output),
        }]
    if kind == "tool-approval-request":
        call_id = str(chunk.get("toolCallId") or "")
        name = tool_names.get(call_id, "tool")
        return [{
            "type": "notice",
            "text": (f"The agent asked to confirm an action ({name}). "
                     "Confirming inside the app is not supported yet — "
                     "this step needs confirmation on arena.ai."),
        }]
    if kind == "file":
        url = chunk.get("url") or chunk.get("data")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            media_type = str(chunk.get("mediaType") or chunk.get("mimeType") or "")
            media_kind = ("image" if media_type.startswith("image/")
                          else "video" if media_type.startswith("video/")
                          else "audio" if media_type.startswith("audio/") else "file")
            return [{
                "type": "media", "model": "a", "kind": media_kind, "url": url,
                "title": str(chunk.get("filename") or "Agent file"),
            }]
        return []
    if kind == "error":
        return [{
            "type": "error", "code": "model", "model": "a", "retry": True,
            "message": _short(chunk.get("errorText") or chunk.get("error") or "Agent error", 500),
        }]
    if isinstance(kind, str) and kind.startswith("data-"):
        data = chunk.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                lower = url.lower()
                tail = lower.split("?")[0]
                media_kind = ("image" if tail.endswith(
                    (".png", ".jpg", ".jpeg", ".webp", ".gif"))
                    else "audio" if tail.endswith(
                    (".mp3", ".wav", ".ogg", ".opus", ".m4a", ".flac"))
                    else "video" if tail.endswith((".mp4", ".webm", ".mov"))
                    else "file")
                return [{
                    "type": "media", "model": "a", "kind": media_kind, "url": url,
                    "title": str(data.get("title") or data.get("filename") or "Agent file"),
                }]
        _note_unknown(chunk)
        return []
    _note_unknown(chunk)
    return []
