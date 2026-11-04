"""Unit tests for the native Arena Agent protocol helpers."""
import json

from arena import native_agent as na


def test_create_body_shape_and_recaptcha_modes():
    body = na.build_create_body("hello", "Europe/Moscow", ["github"], message_id="m1")
    assert body["message"] == {"id": "m1", "role": "user",
                               "parts": [{"type": "text", "text": "hello"}]}
    assert body["recaptchaV3Token"] is None and "recaptchaV2Token" not in body
    assert body["clientPlatform"] == "web" and body["timezone"] == "Europe/Moscow"
    assert body["enabledConnectors"] == ["github"] and "cx" not in body
    assert na.build_create_body("x", cx="blob")["cx"] == "blob"

    body2 = na.build_create_body("hi", None, None, v2_token="tok")
    assert body2["recaptchaV2Token"] == "tok" and body2["recaptchaV3Token"] is None
    assert "enabledConnectors" not in body2


def test_append_body_and_urls():
    body = na.build_append_body("next", "addr-1", "Europe/Moscow", ["notion"], message_id="m2")
    assert body["kind"] == "message"
    payload = body["payload"]
    assert payload["chatId"] == "addr-1" and payload["trigger"] == "submit-message"
    assert payload["messageId"] == "m2"
    assert payload["message"]["parts"][0]["text"] == "next"
    assert payload["metadata"]["enabledConnectors"] == ["notion"]
    assert payload["metadata"]["clientPlatform"] == "web"
    assert na.out_url("https://arena.ai", "addr 1").endswith(
        "/ai-proxy/realtime/v1/sessions/addr%201/out")
    assert na.in_url("https://arena.ai/", "a").endswith("/sessions/a/in/append")
    headers = na.append_headers("pat")
    assert headers["Authorization"] == "Bearer pat" and headers["X-Part-Id"]
    assert na.STOP_BODY == {"kind": "stop"}


def test_parse_bootstrap():
    boot = na.parse_bootstrap({"id": "s1", "bootstrap": {
        "sessionId": "s1", "triggerAddress": "t1", "publicAccessToken": "p1",
        "taskId": "chat-agent"}})
    assert boot == {"session_id": "s1", "trigger_address": "t1", "pat": "p1",
                    "task_id": "chat-agent"}
    assert na.parse_bootstrap(None)["session_id"] is None
    assert na.parse_bootstrap({"id": "s2"})["trigger_address"] is None


def test_parse_bootstrap_real_shape():
    # The website's chatBootstrapSchema: triggerAddress === sessionId, ttl <= 60s.
    sid = "11111111-1111-7111-8111-111111111111"
    boot = na.parse_bootstrap({"id": sid, "bootstrap": {
        "version": 1, "sessionId": sid, "userId": "u", "inputId": "i",
        "taskId": "chat-agent", "triggerAddress": sid,
        "publicAccessToken": "pat", "ttlMs": 60000,
        "feedbackType": "check_in", "customFeedbackArm": None}})
    assert boot["session_id"] == sid and boot["trigger_address"] == sid
    assert boot["pat"] == "pat"


def test_new_uuid_is_v7():
    a, b = na.new_uuid(), na.new_uuid()
    assert a != b
    assert a[14] == "7" and a[19] in "89ab"  # version + variant nibbles
    assert na.user_message("x")["id"][14] == "7"


def test_iter_sse_records_v1_and_batch():
    lines = [
        "id: 10",
        'data: {"type": "text-delta", "delta": "Hi"}',
        "",
        "event: batch",
        'data: {"records": ['
        '{"seq_num": 11, "headers": [["public-access-token", "pat-new"]], '
        '"body": "{\\"id\\": \\"r1\\", \\"data\\": {\\"type\\": \\"trigger:turn-complete\\"}}"}, '
        '{"seq_num": 12, "headers": [["", ""]], "body": "{}"}]}',
        "",
    ]
    records = list(na.iter_sse_records(lines))
    assert len(records) == 2  # keepalive batch record skipped
    assert records[0]["id"] == "10" and records[0]["chunk"]["type"] == "text-delta"
    assert records[1]["id"] == "11"
    assert na.is_turn_complete(records[1]["chunk"])
    assert na.refreshed_pat(records[1]) == "pat-new"
    assert na.is_control({"type": "trigger:upgrade-required"})
    assert not na.is_control({"type": "text-delta"})


def test_map_chunk_text_tools_media_errors():
    names = {}
    assert na.map_chunk({"type": "text-delta", "delta": "a"}, names) == [
        {"type": "text", "model": "a", "text": "a"}]
    assert na.map_chunk({"type": "reasoning-delta", "delta": "r"}, names)[0]["type"] == "reasoning"

    call = na.map_chunk({"type": "tool-input-available", "toolCallId": "c1",
                         "toolName": "web_search", "input": {"q": "x"}}, names)[0]
    assert call["type"] == "tool_call" and call["id"] == "c1" and call["tool"] == "web_search"
    assert names == {"c1": "web_search"}

    result = na.map_chunk({"type": "tool-output-available", "toolCallId": "c1",
                           "output": {"hits": 2}}, names)[0]
    assert result["ok"] is True and result["tool"] == "web_search"
    assert json.loads(result["output"]) == {"hits": 2}

    err = na.map_chunk({"type": "tool-output-error", "toolCallId": "c1",
                        "errorText": "boom"}, names)[0]
    assert err["ok"] is False and err["output"] == "boom"

    media = na.map_chunk({"type": "file", "url": "https://cdn.test/x.png",
                          "mediaType": "image/png"}, names)[0]
    assert media == {"type": "media", "model": "a", "kind": "image",
                     "url": "https://cdn.test/x.png", "title": "Agent file"}

    assert na.map_chunk({"type": "error", "errorText": "bad"}, names)[0]["message"] == "bad"
    approval = na.map_chunk({"type": "tool-approval-request", "toolCallId": "c1"}, names)[0]
    assert approval["type"] == "notice" and "web_search" in approval["text"]
    assert na.map_chunk({"type": "start"}, names) == []
    assert na.map_chunk("nonsense", names) == []


def test_turn_complete_record_checks_headers_and_chunk():
    # Batch (v2) end of turn: the signal lives in the record headers and
    # the body/chunk is empty. Checking only the chunk type made turns
    # hang until Stop (the v4.5.0 live bug).
    assert na.is_turn_complete_record(
        {"id": "1", "chunk": None, "headers": {"trigger-control": "turn-complete"}})
    assert na.is_turn_complete_record(
        {"id": "2", "chunk": {"type": "trigger:turn-complete"}, "headers": {}})
    assert not na.is_turn_complete_record(
        {"id": "3", "chunk": {"type": "text-delta", "delta": "x"}, "headers": {}})
    assert not na.is_turn_complete_record(
        {"id": "4", "chunk": None, "headers": {"x-trigger-msgtype": "chunk"}})
    assert not na.is_turn_complete_record(None)


def test_v1_envelope_unwrapped_but_data_chunks_kept():
    recs = na.records_from_event(None, "7", '{"id":"7","data":{"type":"text-delta","delta":"а"}}')
    assert recs[0]["chunk"]["type"] == "text-delta"
    assert recs[0]["chunk"]["delta"] == "а"
    # A real data-* chunk carries its own "data" and must survive intact.
    recs = na.records_from_event(None, "8", '{"type":"data-note","data":{"a":1}}')
    assert recs[0]["chunk"]["type"] == "data-note"
    assert recs[0]["chunk"]["data"] == {"a": 1}


def test_batch_records_headers_and_control_record():
    payload = json.dumps({"records": [
        {"seq_num": 5, "headers": [["x-trigger-msgtype", "chunk"]],
         "body": json.dumps({"id": "5", "data": {"type": "text-delta", "delta": "Привет"}})},
        {"seq_num": 6, "headers": [["trigger-control", "turn-complete"],
                                   ["session-in-event-id", "9"]], "body": ""},
    ]})
    recs = na.records_from_event("batch", None, payload)
    assert recs[0]["id"] == "5" and recs[0]["chunk"]["delta"] == "Привет"
    assert recs[1]["id"] == "6"
    assert recs[1]["headers"]["trigger-control"] == "turn-complete"
    assert na.is_turn_complete_record(recs[1])
    assert not na.is_turn_complete_record(recs[0])


def test_agentic_import_messages_from_parts_and_content():
    from arena import history_sync
    session = {"title": "Агентный чат", "messages": [
        {"role": "user", "parts": [{"type": "text", "text": "Вопрос"}],
         "createdAt": "2026-01-01T00:00:00Z"},
        {"role": "assistant", "parts": [
            {"type": "step-start"},
            {"type": "reasoning", "text": "Скрыто"},
            {"type": "text", "text": "Ответ"},
        ], "createdAt": "2026-01-01T00:00:01Z"},
        {"role": "user", "content": "Ещё", "createdAt": "2026-01-01T00:00:02Z"},
        {"role": "tool", "parts": [{"type": "text", "text": "шум"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "  "}]},
        "junk",
    ]}
    assert history_sync.agentic_title(session) == "Агентный чат"
    messages = history_sync.agentic_messages(session)
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "Вопрос"), ("assistant", "Ответ"), ("user", "Ещё")]
    assert messages[1]["names"] == {"a": "Agent", "b": None}
    # Nested container + garbage tolerance.
    nested = {"session": {"title": "Вложено", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "Внутри"}]}]}}
    assert history_sync.agentic_title(nested) == "Вложено"
    assert history_sync.agentic_messages(nested)[0]["text"] == "Внутри"
    assert history_sync.agentic_messages(None) == []
    assert history_sync.agentic_messages({"messages": "no"}) == []
    # Resume cursor from the session snapshot (the website resumes this way).
    assert history_sync.agentic_last_event_id({"lastEventId": "42"}) == "42"
    assert history_sync.agentic_last_event_id({"lastEventId": 7}) == "7"
    assert history_sync.agentic_last_event_id({"session": {"messages": [], "lastEventId": "9"}}) == "9"
    assert history_sync.agentic_last_event_id({"messages": []}) is None
    assert history_sync.agentic_last_event_id(None) is None


def test_native_audio_files_map_to_audio_kind():
    from arena import native_agent
    out = native_agent.map_chunk(
        {"type": "file", "url": "https://cdn.example.com/voice.mp3",
         "mediaType": "audio/mpeg", "filename": "voice.mp3"}, {})
    assert out and out[0]["type"] == "media" and out[0]["kind"] == "audio"
    out = native_agent.map_chunk(
        {"type": "data-media", "data": {"url": "https://cdn.example.com/a.wav"}}, {})
    assert out and out[0]["kind"] == "audio"
    # Unknown chunks are tolerated and yield nothing.
    assert native_agent.map_chunk({"type": "mystery-kind", "x": 1}, {}) == []


def test_native_hitl_tool_names_outputs_and_append_body():
    names = {}
    call = na.map_chunk({
        "type": "tool-input-available", "toolCallId": "q1",
        "toolName": "arena__ask_user",
        "input": {"questions": [{
            "id": "color", "question": "Which color?",
            "options": [{"id": "red", "label": "Red", "description": "Warm"},
                        {"id": "blue", "label": "Blue"}],
            "allowCustomResponse": False,
        }]},
    }, names)[0]
    assert call["tool"] == "ask_user" and call["name"] == "Question"
    assert call["path"] == "Which color?"
    assert names == {"q1": "ask_user"}
    waiting = na.map_chunk({
        "type": "tool-output-available", "toolCallId": "q1",
        "output": {"status": "awaiting_user_response"},
    }, names)[0]
    assert waiting["type"] == "hitl_wait" and waiting["tool"] == "ask_user"
    questions = na.ask_user_questions({
        "questions": [{
            "id": "color", "question": "Which color?",
            "options": [{"id": "red", "label": "Red", "description": "Warm"}],
            "allowCustomResponse": False,
        }]
    })
    assert questions[0]["options"][0] == {
        "id": "red", "label": "Red", "description": "Warm"}
    assert questions[0]["allowCustomResponse"] is False

    body = na.build_hitl_append_body([
        {"requestNodeId": "node-1", "tool": "arena__ask_user",
         "toolCallId": "q1", "output": {"skipped": False, "answers": [
             {"questionId": "color", "selectedOptionId": "red",
              "customResponse": None}]}},
    ], "addr-1", "Europe/Moscow", message_id="m-hit")
    payload = body["payload"]
    assert payload["chatId"] == "addr-1" and payload["messageId"] == "m-hit"
    assert payload["message"] == {
        "id": "m-hit", "role": "user",
        "parts": [{"type": "data-tool-results", "data": [{
            "requestNodeId": "node-1", "tool": "ask_user", "toolCallId": "q1",
            "output": {"skipped": False, "answers": [
                {"questionId": "color", "selectedOptionId": "red",
                 "customResponse": None}]},
        }]}],
    }


def test_native_message_ids_from_stream_chunks():
    assert na.message_ids_from_chunk({
        "type": "start", "messageId": "msg-1",
        "messageMetadata": {"nodeId": "node-1"},
    }) == ("msg-1", "node-1")
    assert na.message_ids_from_chunk({
        "type": "message-metadata",
        "messageMetadata": {"nodeId": "node-2"},
    }) == (None, "node-2")
    assert na.message_ids_from_chunk(None) == (None, None)


def test_native_generate_image_choice_and_reveal_mapping():
    names = {}
    call = na.map_chunk({
        "type": "tool-input-available", "toolCallId": "img1",
        "toolName": "arena__generate_image",
        "input": {"file_path": "poster.png", "prompt": "A calm poster",
                  "offer_options": True},
    }, names)[0]
    assert call["tool"] == "generate_image" and call["name"] == "Generate image"
    assert call["path"] == "A calm poster"
    output = {
        "status": "awaiting_user_response",
        "candidates": [{"index": 0, "hash": "a" * 43},
                       {"index": 1, "hash": "b" * 43}],
    }
    waiting = na.map_chunk({
        "type": "tool-output-available", "toolCallId": "img1", "output": output,
    }, names)[0]
    assert waiting["type"] == "hitl_wait" and waiting["output"] == output
    choice = na.image_choice_from_output(output, {
        "file_path": "poster.png", "prompt": "A calm poster"})
    assert choice["status"] == "awaiting_user_response"
    assert choice["selected_index"] is None
    assert [item["hash"] for item in choice["candidates"]] == ["a" * 43, "b" * 43]

    completed = {
        "status": "completed", "candidates": output["candidates"],
        "selected_index": 1, "file_path": "poster.png",
        "selection_method": "user",
        "attribution": [
            {"index": 0, "model_name": "Model One", "organization": "Org A"},
            {"index": 1, "model_name": "Model Two", "organization": "Org B"},
        ],
    }
    events = na.map_chunk({
        "type": "tool-output-available", "toolCallId": "img1", "output": completed,
    }, names)
    assert [event["type"] for event in events] == ["tool_result", "media_choice_done"]
    assert events[1]["media_choice"]["selected_index"] == 1
    assert events[1]["media_choice"]["attribution"][1]["model_name"] == "Model Two"

    reveal = na.map_chunk({
        "type": "data-image-battle-reveal",
        "data": {"requestNodeId": "node-9", "toolCallId": "img1",
                 "attribution": [{"index": 0, "model_name": "Model One"}]},
    }, names)[0]
    assert reveal["type"] == "media_reveal"
    assert reveal["request_node_id"] == "node-9"
    assert reveal["attribution"] == [{"index": 0, "model_name": "Model One"}]


def test_history_refresh_merge_preserves_local_turn_details():
    from arena import history_sync

    local = [
        {"role": "user", "text": "First"},
        {"role": "assistant", "text": "Answer", "text_b": "",
         "tools": [{"tool": "generate_image"}], "names": {"a": None, "b": None}},
    ]
    remote = [
        {"role": "user", "text": "First"},
        {"role": "assistant", "text": "Answer", "text_b": "",
         "names": {"a": "Arena Model", "b": None}},
        {"role": "user", "text": "Continued on the website"},
        {"role": "assistant", "text": "New answer", "text_b": ""},
    ]
    merged, added = history_sync.merge_messages(local, remote)
    assert added == 2 and len(merged) == 4
    assert merged[1]["tools"] == [{"tool": "generate_image"}]
    assert merged[1]["names"]["a"] == "Arena Model"

    # A locally-enriched copy whose text no longer forms an exact prefix
    # still receives only the missing remote turns, without duplicates.
    local2 = local + [{"role": "assistant", "text": "Local detail", "text_b": ""}]
    merged2, added2 = history_sync.merge_messages(local2, remote)
    assert added2 == 2
    assert [m["text"] for m in merged2].count("Answer") == 1
    assert merged2[-1]["text"] == "New answer"
