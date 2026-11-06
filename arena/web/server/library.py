# Arena Desktop web backend, part 4 of 11: library.
# Chat search, export and fork, workspace snapshots, Elo, scheduled
# automations, delete / archive on arena.ai, and votes.
#
# Not a standalone module: arena/web/app.py runs the parts in order
# inside its own namespace, so names from earlier parts are in scope.

# ---------- v5: chat search / export / fork, workspace snapshot, Elo, automations ----------

@app.route("/api/search")
def api_search():
    """Full-text search across locally saved chats (title search stays client-side)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    ql = q.lower()
    results = []
    for summary in _list_chats():
        if not isinstance(summary, dict):
            continue
        cid = summary.get("id") or summary.get("chat_id")
        if not cid:
            continue
        data = _load_chat(str(cid))
        if not data:
            continue
        for idx, m in enumerate(data.get("messages") or []):
            matched = False
            for Field_name in ("text", "text_b"):
                text = str(m.get(Field_name) or "")
                pos = text.lower().find(ql)
                if pos >= 0:
                    start = max(0, pos - 60)
                    results.append({
                        "chat_id": str(cid),
                        "title": data.get("title") or summary.get("title") or "Chat",
                        "msg_index": idx,
                        "role": m.get("role") or "assistant",
                        "snippet": (("..." if start else "")
                                    + text[start:pos + len(q) + 60].replace("\n", " ") + "...")})
                    matched = True
                    break
            if matched and len(results) >= 40:
                break
        if len(results) >= 40:
            break
    return jsonify(results[:40])


@app.route("/api/chats/<chat_id>/export.md")
def api_chat_export(chat_id):
    """The whole chat as one Markdown file (tool steps as a compact list)."""
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    lines = [f"# {data.get('title') or 'Chat'}", "",
             f"_Exported from Arena Desktop on {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    for m in data.get("messages") or []:
        role = m.get("role")
        if role == "notice":
            lines += ["", "---", "_Context compacted here_", ""]
            continue
        if role == "user":
            lines += ["## User", "", str(m.get("text") or ""), ""]
        elif role == "assistant":
            names = m.get("names") or {}
            for key, Field_name in (("a", "text"), ("b", "text_b")):
                text = str(m.get(Field_name) or "")
                if not text:
                    continue
                who = names.get(key) or ("Assistant" if key == "a" else "Model B")
                lines += [f"## {who}", "", text, ""]
            for t in m.get("tools") or []:
                lines.append(f"- `{t.get('tool')}` - {t.get('summary') or ''} ({t.get('status') or ''})")
            if m.get("tools"):
                lines.append("")
    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("title") or "chat")).strip("-")[:60] or "chat"
    return Response("\n".join(lines), mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})


@app.route("/api/chats/<chat_id>/fork", methods=["POST"])
def api_chat_fork(chat_id):
    """Copy the chat up to a message into a new chat with a fresh Arena session.

    The copied conversation rides into the new session as a pending digest
    (the Compact mechanism), so the model keeps the context that matters."""
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    body = request.get_json(silent=True) or {}
    msgs = list(data.get("messages") or [])
    try:
        idx = int(body.get("message_index"))
    except (TypeError, ValueError):
        idx = len(msgs) - 1
    idx = max(0, min(idx, len(msgs) - 1)) if msgs else -1
    copied = copy.deepcopy(msgs[:idx + 1]) if idx >= 0 else []
    new_id = generate_uuidv7()
    try:
        digest = _build_digest(dict(data, messages=copied)) if copied else ""
    except Exception:
        LOG.exception("fork digest failed")
        digest = ""
    _save_chat(new_id, title=(str(data.get("title") or "Chat") + " (fork)")[:80],
               messages=copied, mode=data.get("mode") or "direct",
               modality=data.get("modality") or "text",
               model_a=data.get("model_a"), model_b=data.get("model_b"),
               eval_id=None, native=None, memory_hash="", pending_digest=digest,
               parent_id=data.get("parent_id"))
    return jsonify({"ok": True, "chat_id": new_id})


@app.route("/api/workspace/snapshot", methods=["POST"])
def api_workspace_snapshot():
    """Concatenate workspace text files into one attachable context block."""
    ws = _chosen_ws()
    if not ws:
        return jsonify({"error": "Choose a workspace folder first.", "code": "no_workspace"}), 400
    root = Path(ws).resolve()
    body = request.get_json(silent=True) or {}
    wanted = [str(x) for x in (body.get("paths") or []) if str(x).strip()][:40]
    files: list = []
    for rel in wanted:
        try:
            target = (root / rel).resolve()
        except OSError:
            continue
        if root != target and root not in target.parents:
            continue
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for fp in sorted(target.rglob("*")):
                if len(files) >= 40:
                    break
                if fp.is_file() and ".git" not in fp.parts and ".arena-artifacts" not in fp.parts:
                    files.append(fp)
    parts = ["[Attached workspace files]"]
    total = included = 0
    for fp in files[:40]:
        try:
            raw = fp.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:2048]:
            continue
        text = raw.decode("utf-8", errors="replace")[:12000]
        parts.append(f"\n--- {fp.relative_to(root).as_posix()} ---\n{text}")
        total += len(text)
        included += 1
        if total >= 40000:
            break
    parts.append("\n[/Attached workspace files]")
    text = "\n".join(parts)
    return jsonify({"ok": True, "text": text[:44000], "chars": len(text), "files": included})


_elo_cache = {"t": 0.0, "models": {}}
ELO_TTL = 24 * 3600


def _elo_fetch():
    base = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"
    headers = {"User-Agent": f"Arena-Desktop/{APP_VERSION}"}
    r = requests.get(f"{base}/latest.json", headers=headers, timeout=8)
    r.raise_for_status()
    day = str((r.json() or {}).get("path") or "")
    models: dict = {}
    for board in ("text", "code"):
        try:
            rr = requests.get(f"{base}/{day}/{board}.json", headers=headers, timeout=8)
            if rr.status_code != 200:
                continue
            for m in (rr.json() or {}).get("models") or []:
                slug = str(m.get("model") or "").strip().lower()
                if slug and slug not in models:
                    models[slug] = {"rank": m.get("rank"), "score": m.get("score"), "board": board}
        except Exception:
            continue
    return models


@app.route("/api/elo")
def api_elo():
    """Daily Arena leaderboard snapshot (oolong-tea), cached 24h. Optional."""
    if load_config().get("elo_enabled") is False:
        return jsonify({"enabled": False, "models": {}})
    now = time.time()
    cache_file = COOKIE_FILE.parent / "elo_cache.json"
    if not _elo_cache["models"] and cache_file.exists():
        try:
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            _elo_cache.update(t=float(saved.get("t") or 0), models=saved.get("models") or {})
        except Exception:
            pass
    if _elo_cache["models"] and now - _elo_cache["t"] < ELO_TTL:
        return jsonify({"enabled": True, "models": _elo_cache["models"], "cached": True})
    try:
        models = _elo_fetch()
        if models:
            _elo_cache.update(t=now, models=models)
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"t": now, "models": models}), encoding="utf-8")
            except OSError:
                pass
    except Exception as e:
        LOG.info("elo fetch failed: %s", e)
    return jsonify({"enabled": True, "models": _elo_cache["models"]})


# ---------------- automations (scheduled agent runs) ----------------

_automation_running: set = set()
_automation_lock = threading.Lock()
_scheduler_started = False
_SCHEDULE_RE = re.compile(r"^(daily\s+([01]?\d|2[0-3]):[0-5]\d|hourly|every\s+\d{1,5}m)$")


def _automation_due(item, now=None) -> bool:
    if not isinstance(item, dict) or item.get("enabled", True) is False:
        return False
    now = now or datetime.now()
    sched = str(item.get("schedule") or "").strip().lower()
    try:
        last_ts = float(item.get("last_run") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    m = re.fullmatch(r"every\s+(\d{1,5})m", sched)
    if m:
        return time.time() - last_ts >= int(m.group(1)) * 60
    if sched == "hourly":
        return time.time() - last_ts >= 3600
    m = re.fullmatch(r"daily\s+([01]?\d|2[0-3]):([0-5]\d)", sched)
    if m:
        due = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return now >= due and last_ts < due.timestamp()
    return False


def _run_automation(item):
    """Run one automation now (on the caller's thread); returns the chat id."""
    chat_id = generate_uuidv7()
    prompt = str(item.get("prompt") or "").strip()
    title = ("Auto: " + str(item.get("name") or "Automation"))[:60]
    _tl.chat_id = chat_id
    _tl.stop_event = threading.Event()
    _tl.error = None
    _tl.perm_override = item.get("permission") or "plan"
    model_id = str(item.get("model") or "") or None
    conv = {"eval_id": None, "new": True, "mode": contracts.api_mode("direct"),
            "modality": contracts.api_modality("text"), "model_a": model_id,
            "model_b": None, "chat_id": chat_id, "auto_model": not model_id,
            "reasoning": {"a": "", "b": ""}, "lane_errors": {"a": [], "b": []},
            "attachments": None, "guide_sent": False, "mcp_guide_hash": None,
            "memory_hash": None, "title": title, "tools": [],
            "allow_delegate": True, "delegate_sent": False}
    prefix = ""
    if _memory_enabled():
        block, _h = _memory_block(_chosen_ws())
        prefix = block or ""
    history = [{"role": "user", "text": prompt}]
    out = {"a": "", "b": ""}
    final_error = None
    _generation_started(chat_id)
    try:
        _save_chat(chat_id, title=title, messages=list(history), mode="direct",
                   modality="text", model_a=model_id, model_b=None, eval_id=None)
        gen = _agent_run(get_client(), prefix + prompt, conv)
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                out = stop.value or out
                break
    except Exception as e:
        final_error = f"{type(e).__name__}: {e}"
        LOG.exception("automation run failed")
    finally:
        _generation_finished(chat_id)
        text = agent.strip_tool_calls(out.get("a") or "").strip()
        reply = {"role": "assistant", "text": text, "text_b": "",
                 "names": {"a": _model_name(model_id) or "Automation", "b": None}}
        if conv.get("tools"):
            reply["tools"] = conv["tools"]
        ckpts = {l: (conv.get(f"_checkpoint_{l}") or {}).get("id")
                 for l in ("a", "b") if (conv.get(f"_checkpoint_{l}") or {}).get("id")}
        if ckpts:
            reply["checkpoints"] = ckpts
        if final_error and not text:
            reply["error"] = final_error
        history.append(reply)
        try:
            _save_chat(chat_id, title=title, messages=history, mode="direct",
                       modality="text", model_a=model_id, model_b=None,
                       eval_id=conv.get("eval_id") if not conv.get("new") else None,
                       tools_guide=True,
                       delegate_guide=bool(conv.get("delegate_sent")))
        except Exception:
            LOG.exception("automation save failed")
        _notify("Arena Desktop", f"Automation '{str(item.get('name') or 'run')[:40]}' finished.", "done")
    return chat_id


def _automation_worker(item):
    try:
        _run_automation(item)
    except Exception:
        LOG.exception("automation worker failed")
    finally:
        with _automation_lock:
            _automation_running.discard(str(item.get("id") or ""))
        try:
            cfg = load_config()
            for it in cfg.get("automations") or []:
                if str(it.get("id")) == str(item.get("id")):
                    it["last_run"] = int(time.time())
            save_config(cfg)
        except Exception:
            LOG.exception("automation last_run save failed")


def _spawn_automation(item) -> bool:
    aid = str(item.get("id") or "")
    with _automation_lock:
        if aid in _automation_running:
            return False
        _automation_running.add(aid)
    threading.Thread(target=_automation_worker, args=(item,), daemon=True).start()
    return True


def _automation_tick():
    for item in load_config().get("automations") or []:
        if _automation_due(item):
            _spawn_automation(item)


def _start_automation_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        while True:
            time.sleep(30)
            try:
                _automation_tick()
            except Exception:
                LOG.exception("automation tick failed")

    threading.Thread(target=loop, daemon=True).start()


@app.route("/api/automations")
def api_automations_list():
    return jsonify({"items": load_config().get("automations") or []})


@app.route("/api/automations", methods=["POST"])
def api_automations_save():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    schedule = str(body.get("schedule") or "").strip().lower()
    if not name or not prompt:
        return jsonify({"error": "Name and prompt are required.", "code": "bad_request"}), 400
    if not _SCHEDULE_RE.match(schedule):
        return jsonify({"error": "Schedule must be 'daily HH:MM', 'hourly' or 'every Nm'.",
                        "code": "bad_request"}), 400
    permission = "plan"
    cfg = load_config()
    items = list(cfg.get("automations") or [])
    aid = str(body.get("id") or "") or generate_uuidv7()
    entry = {"id": aid, "name": name[:80], "prompt": prompt[:4000], "schedule": schedule,
             "enabled": body.get("enabled") is not False, "permission": permission,
             "model": str(body.get("model") or "") or None,
             "last_run": 0}
    for i, it in enumerate(items):
        if str(it.get("id")) == aid:
            entry["last_run"] = it.get("last_run") or 0
            items[i] = entry
            break
    else:
        items.append(entry)
    cfg["automations"] = items[:25]
    save_config(cfg)
    return jsonify({"ok": True, "item": entry})


@app.route("/api/automations/<aid>", methods=["DELETE"])
def api_automations_delete(aid):
    cfg = load_config()
    items = [it for it in (cfg.get("automations") or []) if str(it.get("id")) != str(aid)]
    cfg["automations"] = items
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/automations/<aid>/run", methods=["POST"])
def api_automations_run(aid):
    item = next((it for it in (load_config().get("automations") or [])
                 if str(it.get("id")) == str(aid)), None)
    if not item:
        return jsonify({"error": "Automation not found", "code": "gone"}), 404
    if not _spawn_automation(item):
        return jsonify({"error": "That automation is already running.", "code": "busy"}), 409
    return jsonify({"ok": True})


_start_automation_scheduler()


def _archive_agentic_on_arena(session_id: str) -> Optional[str]:
    """POST /api/chat/{id}/archive (Arena Agent sessions). Error or None."""
    path = contracts.PATHS["chat_archive"].format(
        session_id=urllib.parse.quote(str(session_id)))
    client = get_client()
    try:
        if _engine_on():
            if not ENGINE.ready():
                return ("arena.ai is waiting for a check" if ENGINE.challenge
                        else "arena.ai session is still loading")
            response = ENGINE.request(path, body={}, method="POST", timeout=35)
            status, detail = response.status_code, response.error
            if status in (200, 201, 204):
                ENGINE.sync_to(client)
                return None
        else:
            raw = client.session.post(contracts.ORIGIN + path, json={}, timeout=35)
            status, detail = raw.status_code, None
            if status in (200, 201, 204):
                client.sync_cookies()
                return None
    except requests.RequestException as e:
        return str(e)
    if status == 0:
        return f"Can't reach arena.ai: {detail}" if detail else "Can't reach arena.ai"
    if status in (401, 403):
        return "Arena session is not signed in or has expired"
    return f"arena.ai returned HTTP {status}"


def _archive_session_on_arena(session_id: str, agentic: bool):
    """Archive one Arena session. Returns (state, error).

    state is "archived", "failed" or "skipped" (no engine window for the
    evaluation Server Action). Mirrors arena.ai's own sidebar: evaluation
    sessions go through the archiveEvaluationSession Server Action,
    agentic sessions through POST /api/chat/{id}/archive.
    """
    session_id = str(session_id or "").strip()
    if not session_id:
        return "none", None
    if agentic:
        error = _archive_agentic_on_arena(session_id)
        return ("failed", error) if error else ("archived", None)
    if not _engine_on():
        return "skipped", "The arena.ai window is unavailable, so the chat could not be archived on Arena."
    try:
        result = ENGINE.archive_evaluation(session_id)
    except Exception as e:  # engine hiccup must not block the local delete
        return "failed", str(e)
    if result.get("ok"):
        return "archived", None
    detail = str(result.get("error") or "archive failed")
    if detail in ("no_window", "login_open", "probe_no_answer"):
        friendly = "The arena.ai window is not ready yet, so the chat could not be archived on Arena."
    elif "UnrecognizedAction" in detail or "not found on the server" in detail:
        friendly = "Arena changed its internals and refused the archive. The chat may still show on arena.ai."
    else:
        friendly = detail
    return "failed", friendly


def _chat_arena_session(chat: dict):
    """(session_id, agentic) for a stored chat, or (None, False)."""
    if not isinstance(chat, dict):
        return None, False
    native = chat.get("native") if isinstance(chat.get("native"), dict) else {}
    if chat.get("agent_backend") == "arena" or native.get("session_id"):
        session_id = native.get("session_id") or chat.get("eval_id")
        return (str(session_id), True) if session_id else (None, False)
    if chat.get("eval_id"):
        return str(chat["eval_id"]), False
    return None, False


def _delete_chat_everywhere(client, chat_id: str, chat: dict = None):
    """Delete a chat locally and archive it on Arena; returns (ok, message).

    Shared by the delete route's semantics and the delete_subagent tool.
    """
    chat = chat if isinstance(chat, dict) else (_load_chat(chat_id) or {})
    note = ""
    session_id, agentic = _chat_arena_session(chat)
    if session_id:
        if _signed_in_somewhere(client):
            _state, arena_error = _archive_session_on_arena(session_id, agentic)
            if arena_error:
                note = f" It could not be archived on Arena ({arena_error})."
        else:
            note = " It was not archived on Arena (not signed in)."
    if not _delete_chat(chat_id):
        return False, "That sub-agent chat no longer exists."
    _orphan_children(chat_id)
    return True, f"Sub-agent chat deleted.{note}"


@app.route("/api/chats/<chat_id>/delete", methods=["POST"])
def api_chat_delete(chat_id):
    """Delete locally and archive the same conversation on arena.ai."""
    if not _validate_chat_id(chat_id) or not (CHATS_DIR / chat_id).is_dir():
        return jsonify({"error": "Chat not found"}), 404
    chat = _load_chat(chat_id) or {}
    arena_state, arena_error = "none", None
    session_id, agentic = _chat_arena_session(chat)
    if session_id:
        if _signed_in_somewhere(get_client()):
            arena_state, arena_error = _archive_session_on_arena(session_id, agentic)
        else:
            arena_state, arena_error = ("skipped",
                                        "Not signed in, so the chat could not be archived on Arena.")
    if not _delete_chat(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    _orphan_children(chat_id)
    return jsonify({"ok": True, "arena": arena_state, "arena_error": arena_error})


@app.route("/api/arena-chats/<session_id>/archive", methods=["POST"])
def api_arena_chat_archive(session_id):
    """Archive a cloud (not yet imported) chat directly on arena.ai."""
    if not _validate_chat_id(session_id):
        return jsonify({"ok": False, "error": "Invalid chat ID"}), 400
    if not _signed_in_somewhere(get_client()):
        return jsonify({"ok": False, "error": "Not signed in", "code": "auth"}), 401
    data = request.get_json(silent=True) or {}
    agentic = str(data.get("type") or "").lower() == "agentic"
    state_name, error = _archive_session_on_arena(session_id, agentic)
    if state_name == "archived":
        return jsonify({"ok": True, "arena": "archived"})
    return jsonify({"ok": False, "arena": state_name,
                    "error": error or "Arena refused the archive."}), 502


def _evaluation_pair_message_ids(eval_id, pair_pos):
    """Assistant message ids (a, b) of one dual turn from the Arena session.

    Used for chats saved before message ids were stored locally: the
    session groups assistant messages by their parent user message.
    """
    session, error, _ = _arena_get_json(
        contracts.PATHS["evaluation"].format(
            eval_id=urllib.parse.quote(str(eval_id))), timeout=35)
    if error or not isinstance(session, dict):
        return None, None
    raw = [m for m in (session.get("messages") or [])
           if isinstance(m, dict) and str(m.get("role") or "").lower() == "assistant"]
    indexed = list(enumerate(raw))
    indexed.sort(key=lambda pair: (str(pair[1].get("createdAt") or ""), pair[0]))
    pairs: dict = {}
    order: list = []
    for _index, msg in indexed:
        parents = msg.get("parentMessageIds") if isinstance(msg.get("parentMessageIds"), list) else []
        key = str(parents[0]) if parents else f"single:{msg.get('id')}"
        if key not in pairs:
            pairs[key] = {}
            order.append(key)
        lane = "b" if str(msg.get("participantPosition") or "a").lower() == "b" else "a"
        if msg.get("id"):
            pairs[key][lane] = str(msg["id"])
    complete = [pairs[key] for key in order if pairs[key].get("a") and pairs[key].get("b")]
    if isinstance(pair_pos, int) and 0 <= pair_pos < len(complete):
        return complete[pair_pos]["a"], complete[pair_pos]["b"]
    if complete:
        return complete[-1]["a"], complete[-1]["b"]
    return None, None


_VOTE_VALUES = ("model_a", "model_b", "tie", "both_bad")


@app.route("/api/chats/<chat_id>/vote", methods=["POST"])
def api_chat_vote(chat_id):
    """Cast the Battle pairwise vote on Arena and reveal the two models.

    The vote itself is submitted by the arena.ai page (its own Server
    Action with a genuine reCAPTCHA token, like the website's vote bar);
    the app stores the choice and the revealed model names on the chat.
    """
    if not _validate_chat_id(chat_id):
        return jsonify({"error": "Invalid chat ID"}), 400
    data = request.get_json(silent=True) or {}
    value = data.get("value")
    if value not in _VOTE_VALUES:
        return jsonify({"error": "Unknown vote value", "code": "bad_request"}), 400
    chat = _load_chat(chat_id)
    if not chat:
        return jsonify({"error": "Chat not found"}), 404
    if chat.get("mode") not in ("battle", "side-by-side"):
        return jsonify({"error": "Voting is available only in Battle and Side by Side.",
                        "code": "unsupported_selection"}), 400
    eval_id = chat.get("eval_id")
    if not eval_id:
        return jsonify({"error": "This chat has no Arena session to vote on.",
                        "code": "arena_history"}), 422
    messages = [dict(m) if isinstance(m, dict) else m for m in (chat.get("messages") or [])]
    dual_indexes = [i for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get("role") == "assistant" and "text_b" in m]
    if not dual_indexes:
        return jsonify({"error": "This chat has no paired answers to vote on.",
                        "code": "bad_request"}), 422
    req_a, req_b = data.get("message_a_id"), data.get("message_b_id")
    target = None
    if req_a and req_b:
        for i in dual_indexes:
            ids = messages[i].get("arena_message_ids") or {}
            if ids.get("a") == req_a and ids.get("b") == req_b:
                target = i
                break
    pair_pos = data.get("pair_index")
    if target is None and isinstance(pair_pos, int) and 0 <= pair_pos < len(dual_indexes):
        target = dual_indexes[pair_pos]
    if target is None:
        target = dual_indexes[-1]
    msg = messages[target]
    if msg.get("vote"):
        return jsonify({"ok": False, "error": "This answer has already been voted on.",
                        "vote": msg.get("vote"), "names": msg.get("names")}), 409
    ids = msg.get("arena_message_ids") or {}
    a_id, b_id = ids.get("a") or req_a, ids.get("b") or req_b
    if not (a_id and b_id):
        a_id, b_id = _evaluation_pair_message_ids(eval_id, dual_indexes.index(target))
    if not (a_id and b_id):
        return jsonify({"error": "Could not identify the Arena messages for the vote.",
                        "code": "arena_history"}), 422
    if not _engine_on():
        return jsonify({"error": "Voting works in the desktop app: "
                        "the vote is submitted by the arena.ai page itself.",
                        "code": "engine_required"}), 503
    res = ENGINE.pairwise_vote({"evaluationSessionId": eval_id,
                                "messageAId": a_id, "messageBId": b_id, "value": value})
    result = (res or {}).get("result") or {}
    if not res or not res.get("ok") or not result.get("success"):
        detail = str(result.get("error") or (res or {}).get("error") or "unknown error")
        LOG.info("vote failed: %s (trace=%s)", detail[:200], (res or {}).get("trace"))
        if "UnrecognizedAction" in detail or "not found on the server" in detail:
            friendly = ("Arena has just updated its website, so voting needs a fresh connection. "
                        "Wait a few seconds and press the vote again.")
        elif detail in ("no_window", "login_open", "probe_no_answer") or detail.startswith("vote_fn_missing"):
            friendly = "The arena.ai window is not ready yet. Wait until it loads, then vote again."
        elif "captcha" in detail.lower() or "recaptcha" in detail.lower():
            friendly = "Arena asked for a human check before accepting the vote. Try again in a moment."
        else:
            short = " ".join(detail.split())[:160]
            friendly = f"Arena did not accept the vote: {short}"
        return jsonify({"error": friendly, "code": "arena_vote"}), 502
    model_a_id, model_b_id = result.get("modelAId"), result.get("modelBId")
    revealed = {str(item.get("id")): item.get("name")
                for item in (result.get("revealed") or [])
                if isinstance(item, dict) and item.get("id") and item.get("name")}
    names = {"a": revealed.get(str(model_a_id)) or _model_name(model_a_id),
             "b": revealed.get(str(model_b_id)) or _model_name(model_b_id)}
    msg["vote"] = value
    msg.setdefault("arena_message_ids", {"a": a_id, "b": b_id})
    if names["a"] or names["b"]:
        # One pair of models serves the whole session: reveal everywhere.
        for i in dual_indexes:
            current = messages[i].get("names") or {}
            messages[i]["names"] = {"a": current.get("a") or names["a"],
                                    "b": current.get("b") or names["b"]}
    _save_chat(chat_id, title=chat.get("title") or "Chat", messages=messages,
               mode=chat.get("mode"), modality=chat.get("modality"),
               model_a=chat.get("model_a") or model_a_id,
               model_b=chat.get("model_b") or model_b_id,
               eval_id=eval_id, source=chat.get("source"),
               tools_guide=chat.get("tools_guide"),
               agent_backend=chat.get("agent_backend"), native=chat.get("native"))
    LOG.info("vote: chat %s value=%s models=%s/%s", chat_id[:8], value, model_a_id, model_b_id)
    return jsonify({"ok": True, "vote": value, "names": messages[target].get("names"),
                    "model_a": model_a_id, "model_b": model_b_id})
