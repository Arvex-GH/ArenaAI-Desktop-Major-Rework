"""
Convert arena.ai account history into Arena Desktop's local chat format.

The live Arena frontend exposes a unified account history and a full
evaluation-session resource through its own /api client.  Only the JSON shapes
are mirrored here; all network access stays in the authenticated app engine.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_WIRE_TO_UI_MODE = {
    "battle": "battle",
    "side-by-side": "side-by-side",
    "direct": "direct",
    "direct-battle": "direct",
}
_WIRE_TO_UI_MODALITY = {
    "auto": "text",
    "chat": "text",
    "webdev": "code",
    "search": "search",
    "image": "image",
    "video": "video",
    "audio": "text",
    "p2l": "text",
}


def ui_selection(mode: Any, modality: Any) -> Tuple[str, str]:
    ui_mode = _WIRE_TO_UI_MODE.get(str(mode or ""), "battle")
    ui_modality = _WIRE_TO_UI_MODALITY.get(str(modality or ""), "text")
    if ui_modality == "video" and ui_mode != "battle":
        ui_mode = "battle"
    return ui_mode, ui_modality


def is_injected_title(title: Any) -> bool:
    """True for titles Arena copied from the app's injected tool guide
    ("[Arena Desktop] The user is chatting ...", or "[System] You are Arena
    CLI ..." from older versions) instead of the user's words."""
    return str(title or "").strip().lower().startswith((
        "[arena desktop]", "[arena cli]", "[system]",
        "you are working as a coding agent inside arena desktop", "you are arena cli"))


def clean_title(title: Any, fallback: str = "Arena chat") -> str:
    text = " ".join(str(title or "").split())
    if is_injected_title(text):
        return "Arena Desktop chat"
    return text or fallback


def _iso_utc(value: Any) -> Optional[str]:
    """An Arena timestamp (ISO 8601 text or epoch milliseconds) in the form
    the app stores, so chat lists sort it with local times."""
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            stamp = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        elif isinstance(value, str) and value.strip():
            stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def last_activity(session: Any) -> Optional[str]:
    """When an Arena conversation last changed: the latest time on the
    session or its messages. Imported and refreshed chats keep this time,
    so opening an old chat does not move it to the top of the list."""
    if not isinstance(session, dict):
        return None
    container = _agentic_container(session)
    values: List[Any] = []
    for source in (session, container):
        values += [source.get(key) for key in ("updatedAt", "updated_at", "createdAt", "created_at")]
    messages = container.get("messages") if isinstance(container.get("messages"), list) else []
    for message in messages:
        if isinstance(message, dict):
            values += [message.get("updatedAt"), message.get("createdAt")]
    stamps = [stamp for stamp in map(_iso_utc, values) if stamp]
    return max(stamps) if stamps else None


def entry_summary(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    kind = str(entry.get("type") or "evaluation")
    if kind == "agentic":
        mode, modality = "agent", "text"
        model_a = model_b = None
    else:
        mode, modality = ui_selection(entry.get("mode"), entry.get("modality"))
        model_a, model_b = entry.get("modelAId"), entry.get("modelBId")
    return {
        "id": str(entry["id"]),
        "eval_id": str(entry["id"]),
        "type": kind,
        "title": clean_title(entry.get("title")),
        "updated_at": entry.get("updatedAt") or entry.get("createdAt") or "",
        "created_at": entry.get("createdAt") or "",
        "mode": mode,
        "modality": modality,
        "model_a": model_a,
        "model_b": model_b,
        "source": "arena",
    }


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return ""
    kind = str(part.get("type") or "")
    if kind in ("text", "search", "compaction"):
        return str(part.get("text") or "")
    if kind == "video":
        url = part.get("url")
        return f"[Video]({url})" if url else ""
    if kind in ("file", "image"):
        url = part.get("url") or part.get("data") or part.get("image")
        name = part.get("filename") or part.get("name") or ("Image" if kind == "image" else "File")
        return f"[{name}]({url})" if isinstance(url, str) and url.startswith(("http://", "https://")) else ""
    return ""


def _join_parts(parts: List[Any]) -> str:
    """Text of a message's parts.  Text separated by tool or step parts
    (a Code-mode agent narrating between sandbox calls) becomes separate
    paragraphs instead of one run-on line ("...setup.I will now read")."""
    pieces: List[str] = []
    gap = False
    for part in parts:
        text = _part_text(part)
        if text:
            if gap and pieces and not pieces[-1].endswith("\n\n"):
                text = ("\n" if pieces[-1].endswith("\n") else "\n\n") + text.lstrip(" ")
            pieces.append(text)
            gap = False
        elif isinstance(part, dict) and str(part.get("type") or "") not in ("reasoning", "thinking", ""):
            gap = True
    return "".join(pieces)


def message_text(raw: Any) -> str:
    """Plain text of one raw Arena message (parts or content)."""
    if not isinstance(raw, dict):
        return ""
    parts = raw.get("parts")
    if isinstance(parts, list):
        return _join_parts(parts).strip()
    return (_content_text(raw.get("content")) or "").strip()


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _join_parts(content)
    if isinstance(content, dict):
        return _part_text(content) or str(content.get("text") or content.get("content") or "")
    return ""


def _content_reasoning(content: Any, fallback: Any = "") -> str:
    pieces: List[str] = []
    if isinstance(fallback, str) and fallback:
        pieces.append(fallback)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "reasoning":
                pieces.append(str(part.get("text") or ""))
            elif part.get("type") == "thinking":
                pieces.append(str(part.get("thinking") or part.get("text") or ""))
    # Arena streams reasoning as raw deltas.  Preserve their spacing exactly;
    # inserting a newline between pieces is what produced one-word lines.
    return "".join(pieces)


def _attachment_links(message: Dict[str, Any]) -> List[str]:
    links: List[str] = []
    attachments = message.get("experimental_attachments") or message.get("attachments") or []
    if not isinstance(attachments, list):
        return links
    for item in attachments:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        name = str(item.get("name") or item.get("filename") or "Attachment")
        content_type = str(item.get("contentType") or item.get("content_type") or "")
        links.append(f"![{name}]({url})" if content_type.startswith("image/") else f"[{name}]({url})")
    return links


def _agentic_container(session: Any) -> Dict[str, Any]:
    """The agentic session payload may nest title/messages one level down."""
    if not isinstance(session, dict):
        return {}
    if isinstance(session.get("messages"), list) or session.get("title"):
        return session
    for key in ("session", "state", "data", "chat"):
        sub = session.get(key)
        if isinstance(sub, dict) and (isinstance(sub.get("messages"), list) or sub.get("title")):
            return sub
    return session


def agentic_title(session: Any) -> str:
    container = _agentic_container(session)
    title = container.get("title")
    if isinstance(session, dict) and not title:
        title = session.get("title")
    return str(title or "").strip()


def agentic_last_event_id(session: Any) -> Optional[str]:
    """The .out stream cursor from the session snapshot, when present.

    Arena's own resume reads ``lastEventId`` from ``GET /api/chat/{id}``
    (its reload returns {messages, pagination, lastEventId, ...}), so a
    continued session can subscribe exactly at the head instead of
    replaying or draining the whole backlog.
    """
    container = _agentic_container(session)
    for source in (container, session if isinstance(session, dict) else {}):
        for key in ("lastEventId", "last_event_id"):
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def agentic_messages(session: Any) -> List[Dict[str, Any]]:
    """Best-effort local messages for one cloud Arena Agent (agentic) session.

    The session endpoint returns AI SDK style messages: items carry either
    ``parts`` (typed part dicts) or ``content``. Anything unrecognised is
    skipped — importing the session coordinates matters more than a perfect
    transcript: continuation must work even if the payload shape drifts.
    """
    container = _agentic_container(session)
    raw_messages = container.get("messages")
    if not isinstance(raw_messages, list):
        return []
    indexed = list(enumerate(raw_messages))
    indexed.sort(key=lambda pair: (str((pair[1] or {}).get("createdAt") or "") if isinstance(pair[1], dict) else "", pair[0]))
    messages: List[Dict[str, Any]] = []
    for _index, raw in indexed:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        parts = raw.get("parts")
        if isinstance(parts, list):
            text = _join_parts(parts)
        else:
            text = _content_text(raw.get("content"))
        text = (text or "").strip()
        if not text:
            continue
        if role == "user":
            messages.append({"role": "user", "text": text})
        else:
            messages.append({"role": "assistant", "text": text, "text_b": "",
                             "names": {"a": "Agent", "b": None}})
    return messages


def session_to_local(session: Dict[str, Any],
                     model_name: Optional[Callable[[Optional[str]], Optional[str]]] = None) -> Dict[str, Any]:
    """Return title/mode/modality/models/messages for one evaluation session."""
    if not isinstance(session, dict):
        raise ValueError("Arena returned an invalid evaluation session")
    raw_messages = session.get("messages") or []
    if not isinstance(raw_messages, list):
        raw_messages = []
    indexed = list(enumerate(raw_messages))
    indexed.sort(key=lambda pair: (str((pair[1] or {}).get("createdAt") or ""), pair[0]))

    messages: List[Dict[str, Any]] = []
    current_assistant: Optional[Dict[str, Any]] = None
    current_parent: Optional[Tuple[str, ...]] = None
    model_ids: Dict[str, Optional[str]] = {"a": None, "b": None}
    inferred_modality = None

    def flush_assistant():
        nonlocal current_assistant, current_parent
        if current_assistant is not None:
            messages.append(current_assistant)
        current_assistant, current_parent = None, None

    for _index, raw in indexed:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").lower()
        text = _content_text(raw.get("content"))
        links = _attachment_links(raw)
        if links:
            text = (text + "\n\n" if text else "") + "\n\n".join(links)
        if role == "user":
            flush_assistant()
            messages.append({"role": "user", "text": text})
            continue
        if role != "assistant":
            continue
        lane = "b" if str(raw.get("participantPosition") or "a").lower() == "b" else "a"
        parents = raw.get("parentMessageIds") if isinstance(raw.get("parentMessageIds"), list) else []
        parent_key = tuple(str(item) for item in parents)
        if current_assistant is None or (parent_key and current_parent and parent_key != current_parent):
            flush_assistant()
            current_assistant = {"role": "assistant", "text": "", "text_b": "", "names": {"a": None, "b": None}}
            current_parent = parent_key or None
        assert current_assistant is not None
        field = "text_b" if lane == "b" else "text"
        if text:
            current_assistant[field] = (current_assistant[field] + "\n\n" + text) if current_assistant[field] else text
        reasoning = _content_reasoning(raw.get("content"), raw.get("reasoning"))
        if reasoning:
            r_field = "reasoning_b" if lane == "b" else "reasoning"
            current_assistant[r_field] = str(current_assistant.get(r_field) or "") + reasoning
        model_id = raw.get("modelId")
        if model_id and not model_ids[lane]:
            model_ids[lane] = str(model_id)
        if model_id and not current_assistant["names"].get(lane):
            current_assistant["names"][lane] = model_name(str(model_id)) if model_name else None
        if raw.get("failureReason"):
            current_assistant.setdefault("error", str(raw["failureReason"]))
    flush_assistant()

    for evaluation in session.get("maskedEvaluations") or []:
        if isinstance(evaluation, dict) and evaluation.get("modality"):
            inferred_modality = evaluation["modality"]
            break
    mode, modality = ui_selection(session.get("mode"), inferred_modality or session.get("modality"))
    return {
        "id": str(session.get("id") or ""),
        "eval_id": str(session.get("id") or ""),
        "title": clean_title(session.get("title")),
        "mode": mode,
        "modality": modality,
        "model_a": model_ids["a"],
        "model_b": model_ids["b"],
        "messages": messages,
        "updated_at": session.get("updatedAt") or "",
        "created_at": session.get("createdAt") or "",
        "source": "arena",
    }


def _message_signature(message: Dict[str, Any]) -> Tuple[str, str, str]:
    """Stable-enough identity for a local turn during a cloud refresh.

    Imported local messages do not retain Arena's remote IDs, so refresh
    compares role plus whitespace-normalised text.  Reasoning is excluded:
    Arena and the local stream may preserve it with slightly different
    framing while the visible turn is identical.
    """

    def clean(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    return (
        str(message.get("role") or ""),
        clean(message.get("text")),
        clean(message.get("text_b")),
    )


def _unwrap_injected(remote: List[Dict[str, Any]], local: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Arena stores what the app sent: injected memory or tool-guide text
    followed by the user's words. Map such a user turn back to the local turn
    that ends with the same words, and drop app-only turns (tool results,
    continue prompts) plus the reply to them, so a refresh never adds them
    to the chat as if the user had written them."""
    def norm(value: Any) -> str:
        return " ".join(str(value or "").split())

    local_users = {norm(m.get("text")): m.get("text") for m in local if m.get("role") == "user" and norm(m.get("text"))}
    out: List[Dict[str, Any]] = []
    skip_reply = False
    for message in remote:
        role = message.get("role")
        if role == "user":
            text = norm(message.get("text"))
            match = next((orig for words, orig in local_users.items() if text != words and text.endswith(words)), None)
            if match is not None:
                message = dict(message, text=match)
            elif is_injected_title(text):
                skip_reply = True
                continue
            skip_reply = False
        elif role == "assistant" and skip_reply:
            skip_reply = False
            continue
        out.append(message)
    return out


def merge_messages(local_messages: Any, remote_messages: Any) -> Tuple[List[Dict[str, Any]], int]:
    """Merge a fresh Arena transcript into an existing local one.

    The common case is that the cloud transcript simply continues the
    local one; shared turns then keep their local desktop-only details
    (tool calls, votes and saved media choices), gain any missing remote
    metadata, and the remote tail is appended.  When the transcripts do
    not form an exact prefix, keep every local entry and append only
    remote turns whose signatures are not already represented locally.
    The return value is ``(merged, added)``.
    """
    local = [dict(item) for item in local_messages if isinstance(item, dict)] if isinstance(local_messages, list) else []
    remote = [dict(item) for item in remote_messages if isinstance(item, dict)] if isinstance(remote_messages, list) else []
    remote = _unwrap_injected(remote, local)
    if not remote:
        return local, 0
    remote_sigs = [_message_signature(item) for item in remote]
    # Repair (v1.5.1): an older merge appended Arena's copies of replies the
    # chat already had, right after the local reply.  Drop such appended
    # copies so the turn-by-turn merge below can put each one in its place.
    remote_sig_set = set(remote_sigs)
    cleaned: List[Dict[str, Any]] = []
    for item in local:
        if (item.get("role") == "assistant" and cleaned and cleaned[-1].get("role") == "assistant"
                and _message_signature(item) in remote_sig_set):
            continue
        cleaned.append(item)
    local = cleaned
    local_sigs = [_message_signature(item) for item in local]
    aligned = _merge_aligned(local, remote)
    if aligned is not None:
        return aligned
    # Empty placeholder turns are not useful identity anchors.  They are
    # rare, but avoiding them prevents a blank assistant slot from being
    # treated as a real shared turn.
    if local and len(remote) >= len(local) and remote_sigs[:len(local)] == local_sigs:
        merged = []
        for local_message, remote_message in zip(local, remote):
            item = dict(local_message)
            for key, value in remote_message.items():
                if key == "names" and isinstance(value, dict) and isinstance(item.get("names"), dict):
                    names = dict(item["names"])
                    for lane, name in value.items():
                        if name and not names.get(lane):
                            names[lane] = name
                    item["names"] = names
                elif item.get(key) in (None, "", [], {}):
                    item[key] = value
            merged.append(item)
        merged.extend(remote[len(local):])
        return merged, len(remote) - len(local)
    seen = Counter(local_sigs)
    merged = local
    added = 0
    # Append only turns the chat does not have yet.  A remote reply to a
    # prompt that is already here is never appended on its own: that glued
    # several replies together under one prompt.
    new_turn = False
    for message, signature in zip(remote, remote_sigs):
        if message.get("role") == "user":
            new_turn = seen[signature] <= 0
            if not new_turn:
                seen[signature] -= 1
                continue
        elif not new_turn:
            continue
        if seen[signature] > 0:
            seen[signature] -= 1
            continue
        merged.append(message)
        added += 1
    return merged, added


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _merge_aligned(local: List[Dict[str, Any]], remote: List[Dict[str, Any]]):
    """Turn-by-turn merge when both transcripts have the same prompts in the
    same order.  A local reply that is empty or cut off (a dropped stream)
    takes Arena's finished text; desktop-only details (tool rows, votes,
    media) stay.  Returns (merged, added) or None when the shapes differ."""
    if not local or len(remote) < len(local):
        return None
    for mine, theirs in zip(local, remote):
        if mine.get("role") != theirs.get("role"):
            return None
        if mine.get("role") == "user" and _norm_text(mine.get("text")) != _norm_text(theirs.get("text")):
            return None
    merged: List[Dict[str, Any]] = []
    for mine, theirs in zip(local, remote):
        item = dict(mine)
        if mine.get("role") == "assistant":
            for field in ("text", "text_b"):
                have, got = _norm_text(mine.get(field)), _norm_text(theirs.get(field))
                # Only ever grow a reply: a longer local text (e.g. kept
                # after a continuation) is never cut back to Arena's copy.
                if got and got != have and (not have or len(got) >= len(have)
                                            and (got.startswith(have[:200]) or have[:120] in got)):
                    item[field] = theirs.get(field)
                    if field == "text":
                        item.pop("error", None)
        for key, value in theirs.items():
            if key == "names" and isinstance(value, dict) and isinstance(item.get("names"), dict):
                names = dict(item["names"])
                for lane, name in value.items():
                    if name and not names.get(lane):
                        names[lane] = name
                item["names"] = names
            elif item.get(key) in (None, "", [], {}):
                item[key] = value
        merged.append(item)
    merged.extend(remote[len(local):])
    return merged, len(remote) - len(local)
