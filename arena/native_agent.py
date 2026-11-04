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
    "tool-input-end", "message-metadata", "raw", "data-image-battle-reveal",
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


HITL_TOOL_NAMES = {"ask_user", "add_voice", "propose_plan", "generate_image"}
AWAITING_USER_RESPONSE = "awaiting_user_response"
_TOOL_DISPLAY_NAMES = {
    "ask_user": "Question",
    "generate_image": "Generate image",
    "add_voice": "Choose voice",
    "propose_plan": "Plan",
}


def normalize_tool_name(name) -> str:
    """Arena streams its own built-in capabilities as ``arena__<tool>``.

    The desktop UI and transcript use the short capability name, which is
    also the name Arena expects back inside a HITL tool-result envelope.
    """
    value = str(name or "tool")
    if value.startswith("tool-"):
        value = value[5:]
    if value.startswith("arena__"):
        value = value[7:]
    return value or "tool"


def tool_display_name(name) -> str:
    short = normalize_tool_name(name)
    return _TOOL_DISPLAY_NAMES.get(short, short)


def user_message_parts(parts, message_id=None) -> dict:
    return {
        "id": message_id or new_uuid(),
        "role": "user",
        "parts": list(parts or []),
    }


def build_append_parts_body(parts, trigger_address: str, timezone=None,
                            connectors=None, message_id=None, cx=None) -> dict:
    message = user_message_parts(parts, message_id)
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


def build_append_body(text: str, trigger_address: str, timezone=None,
                      connectors=None, message_id=None, cx=None, files=None) -> dict:
    message = user_message(text, message_id, files=files)
    return build_append_parts_body(
        message["parts"], trigger_address, timezone=timezone,
        connectors=connectors, message_id=message["id"], cx=cx)


def tool_results_part(results) -> dict:
    """The exact user-message part Arena's web client appends for HITL."""
    clean = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        clean.append({
            "requestNodeId": item.get("requestNodeId") or item.get("request_node_id"),
            "tool": normalize_tool_name(item.get("tool")),
            "toolCallId": str(item.get("toolCallId") or item.get("tool_call_id") or ""),
            "output": item.get("output") if isinstance(item.get("output"), dict) else {},
        })
    return {"type": "data-tool-results", "data": clean}


def build_hitl_append_body(results, trigger_address: str, timezone=None,
                           connectors=None, message_id=None, cx=None) -> dict:
    return build_append_parts_body(
        [tool_results_part(results)], trigger_address, timezone=timezone,
        connectors=connectors, message_id=message_id, cx=cx)


def output_status(output):
    if isinstance(output, dict):
        return str(output.get("status") or "")
    if isinstance(output, str):
        parsed = _json_or_none(output)
        if isinstance(parsed, dict):
            return str(parsed.get("status") or "")
    return ""


def message_ids_from_chunk(chunk):
    """Return ``(message_id, node_id)`` visible in one stream chunk."""
    if not isinstance(chunk, dict):
        return None, None
    message_id = chunk.get("messageId")
    metadata = chunk.get("messageMetadata")
    node_id = None
    if isinstance(metadata, dict):
        node_id = metadata.get("nodeId") or metadata.get("node_id")
    return (str(message_id) if message_id else None,
            str(node_id) if node_id else None)


def ask_user_questions(tool_input):
    """Normalise Arena's structured ``ask_user`` input for the desktop UI."""
    if isinstance(tool_input, str):
        tool_input = _json_or_none(tool_input)
    if not isinstance(tool_input, dict):
        return []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        # Be tolerant of a single-question shorthand if Arena ever sends it.
        if tool_input.get("question"):
            questions = [tool_input]
        else:
            return []
    result = []
    for index, question in enumerate(questions[:6]):
        if not isinstance(question, dict):
            continue
        text = str(question.get("question") or "").strip()
        if not text:
            continue
        options = []
        for option_index, option in enumerate((question.get("options") or [])[:6]):
            if isinstance(option, str):
                option = {"label": option}
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            if not label:
                continue
            options.append({
                "id": str(option.get("id") or f"option_{option_index + 1}"),
                "label": label,
                "description": str(option.get("description") or "").strip(),
            })
        result.append({
            "id": str(question.get("id") or f"question_{index + 1}"),
            "question": text,
            "options": options,
            "allowCustomResponse": question.get("allowCustomResponse") is not False,
        })
    return result


def _candidate_list(value):
    candidates = []
    if not isinstance(value, list):
        return candidates
    for fallback_index, item in enumerate(value[:16]):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", fallback_index))
        except (TypeError, ValueError):
            index = fallback_index
        candidate = {"index": index}
        if isinstance(item.get("hash"), str) and item["hash"]:
            candidate["hash"] = item["hash"]
        for key in ("url", "mediaType", "filename", "title"):
            if isinstance(item.get(key), str) and item[key]:
                candidate[key] = item[key]
        candidates.append(candidate)
    return candidates


def _attribution_list(value):
    attribution = []
    if not isinstance(value, list):
        return attribution
    for fallback_index, item in enumerate(value[:16]):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", fallback_index))
        except (TypeError, ValueError):
            index = fallback_index
        clean = {"index": index}
        for key in ("model_name", "organization"):
            if isinstance(item.get(key), str) and item[key]:
                clean[key] = item[key]
        if item.get("is_private") is not None:
            clean["is_private"] = bool(item.get("is_private"))
        attribution.append(clean)
    return attribution


def image_choice_from_output(output, tool_input=None):
    """Normalised pending/completed state for ``generate_image`` output."""
    if isinstance(output, str):
        output = _json_or_none(output)
    if not isinstance(output, dict):
        return None
    if isinstance(tool_input, str):
        tool_input = _json_or_none(tool_input)
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    candidates = _candidate_list(output.get("candidates"))
    if not candidates:
        return None
    selected = output.get("selected_index")
    try:
        selected_index = int(selected) if selected is not None else None
    except (TypeError, ValueError):
        selected_index = None
    return {
        "kind": "image",
        "status": str(output.get("status") or ("completed" if selected_index is not None else "")),
        "prompt": str(tool_input.get("prompt") or output.get("prompt") or ""),
        "file_path": str(output.get("file_path") or tool_input.get("file_path") or ""),
        "candidates": candidates,
        "selected_index": selected_index,
        "selection_method": str(output.get("selection_method") or ""),
        "attribution": _attribution_list(output.get("attribution")),
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
        name = normalize_tool_name(chunk.get("toolName"))
        if call_id:
            tool_names[call_id] = name
        title = tool_display_name(name)
        tool_input = chunk.get("input")
        detail = _short(tool_input)
        if name == "ask_user":
            questions = ask_user_questions(tool_input)
            if questions:
                detail = questions[0]["question"]
        elif name == "generate_image" and isinstance(tool_input, dict):
            detail = str(tool_input.get("prompt") or tool_input.get("file_path") or detail)
        return [{
            "type": "tool_call", "id": call_id, "tool": name, "name": title,
            "model": "a", "title": title, "summary": title, "rel": title,
            "path": detail, "inside": False, "status": "running",
        }]
    if kind in ("tool-output-available", "tool-output-error"):
        call_id = str(chunk.get("toolCallId") or "")
        name = normalize_tool_name(tool_names.get(call_id, "tool"))
        ok = kind == "tool-output-available"
        output = chunk.get("output") if ok else (
            chunk.get("errorText") or chunk.get("error") or "")
        if ok and output_status(output) == AWAITING_USER_RESPONSE:
            return [{
                "type": "hitl_wait", "id": call_id, "tool_call_id": call_id,
                "tool": name, "name": tool_display_name(name), "model": "a",
                "output": output if isinstance(output, dict) else _json_or_none(output) or {},
            }]
        events = [{
            "type": "tool_result", "id": call_id, "tool": name,
            "name": tool_display_name(name),
            "model": "a", "rel": tool_display_name(name), "ok": ok,
            "status": "ok" if ok else "error", "output": _short(output),
        }]
        if ok and name == "generate_image":
            choice = image_choice_from_output(output)
            if choice:
                events.append({
                    "type": "media_choice_done", "id": call_id,
                    "tool_call_id": call_id, "tool": name, "model": "a",
                    "media_choice": choice,
                })
        return events
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
        if kind == "data-image-battle-reveal" and isinstance(data, dict):
            return [{
                "type": "media_reveal", "model": "a",
                "tool_call_id": str(data.get("toolCallId") or data.get("tool_call_id") or ""),
                "request_node_id": str(data.get("requestNodeId") or data.get("request_node_id") or ""),
                "attribution": _attribution_list(data.get("attribution")),
            }]
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
