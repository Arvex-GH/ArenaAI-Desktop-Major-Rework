"""Verify the built onefile EXE really embeds the fixed code.

Parses the PyInstaller CArchive + PYZ with the stdlib only and byte-searches
the decompressed modules / data files for the markers that matter.
"""
import marshal
import struct
import sys
import zlib

MAGIC = b"MEI\014\013\012\013\016"
COOKIE_LEN = 24 + 64  # magic(8) + 4 * uint32 + pylib name(64)


def read_carchive(blob: bytes):
    pos = blob.rfind(MAGIC)
    assert pos != -1, "PyInstaller cookie not found"
    _, pkg_len, toc_off, toc_len, _pyvers = struct.unpack("!8sIIII", blob[pos:pos + 24])
    pkg_start = len(blob) - pkg_len
    toc = blob[pkg_start + toc_off: pkg_start + toc_off + toc_len]
    entries = {}
    p = 0
    while p < len(toc):
        (entry_len, off, comp_len, _uncomp_len, comp_flag, type_code) = struct.unpack("!iIIIBB", toc[p:p + 18])
        name = toc[p + 18:p + entry_len].split(b"\0")[0].decode("utf-8", "replace")
        raw = blob[pkg_start + off: pkg_start + off + comp_len]
        data = zlib.decompress(raw) if comp_flag else raw
        entries[name.replace("\\", "/")] = data
        p += entry_len
    return entries


def read_pyz(pyz: bytes):
    assert pyz[:4] == b"PYZ\0", "bad PYZ magic"
    (toc_off,) = struct.unpack("!I", pyz[8:12])
    toc = marshal.loads(pyz[toc_off:])
    out = {}
    failed = []
    for name, (typ, off, length) in toc:
        raw = pyz[off:off + length]
        if not raw:
            out[name] = b""
            continue
        try:
            out[name] = zlib.decompress(raw)
        except zlib.error as e:
            failed.append((name, typ, off, length, str(e), raw[:8].hex()))
    print(f"PYZ: {len(toc)} entries, {len(failed)} failed to decompress")
    for row in failed[:5]:
        print("  FAILED:", row)
    return out


def main(path: str) -> int:
    blob = open(path, "rb").read()
    entries = read_carchive(blob)
    pyz_name = next(n for n in entries if n.endswith(".pyz"))
    modules = read_pyz(entries[pyz_name])

    checks = []

    engine = modules["arena.engine"]
    checks.append(("engine: fetch signature incl. cx args",
                   b"(function (rid, path, method, body, v3key, action, extraHeaders, wantCx, cxModuleId)" in engine))
    checks.append(("engine: broken 6-param signature absent",
                   b"(function (rid, path, method, body, v3key, action) {" not in engine))
    checks.append(("engine: native preflight present",
                   b"native_preflight" in engine and b"__arenaCx" in engine))
    checks.append(("engine: pairwise vote via Server Action",
                   b"pairwise_vote" in engine and b"pairwise_feedback" in engine
                   and b"createPairwiseFeedback" in engine))

    app = modules["arena.web.app"]
    checks.append(("app: APP_VERSION 5.1.0", b"5.1.0" in app))
    checks.append(("app: native create direct with genuine-cx ladder",
                   b"genuine-cx+connectors" in app and b"_native_drive" not in app))
    checks.append(("app: turn-complete record detection + drain resume",
                   b"is_turn_complete_record" in app and b"needs_drain" in app
                   and b"_native_drain_cursor" in app))
    checks.append(("app: server-side snapshot cursor + idle-tolerant stream",
                   b"_native_refresh_cursor" in app and b"_iter_records_idle" in app))
    checks.append(("app: delete archives on Arena + cloud archive route",
                   b"/archive" in app and b"_archive_session_on_arena" in app
                   and b"arena_connectors_off" in app))
    checks.append(("engine: archiveEvaluationSession probe",
                   b"archiveEvaluationSession" in engine and b"archive_evaluation" in engine))
    checks.append(("app: uploads endpoint + attachment rules",
                   b"/api/uploads" in app and b"_validate_upload" in app
                   and b"generate-agent-upload-url" not in app and b"storage_agent_upload_url" in app))
    checks.append(("engine: vote action rediscovery + stale retry",
                   b"discoverActionId" in engine and b"UnrecognizedAction" in engine
                   and b"ratelimit-modality" in engine))
    checks.append(("app: battle vote endpoint + id resolver",
                   b"_evaluation_pair_message_ids" in app and b"/vote" in app
                   and b"arena_message_ids" in app))

    history_sync = modules["arena.history_sync"]
    checks.append(("history_sync: snapshot lastEventId cursor",
                   b"agentic_last_event_id" in history_sync))

    native = modules["arena.native_agent"]
    checks.append(("native_agent: batch trigger-control detection",
                   b"is_turn_complete_record" in native and b"trigger-control" in native))

    index = entries.get("arena/web/static/index.html", b"")
    checks.append(("index.html: assets v=5.1.0", b"?v=5.1.0" in index))
    checks.append(("index.html: Arena dropdown chips + resize handles, no segmented bars",
                   b'id="modeButton"' in index and b'id="modalityButton"' in index
                   and b'id="sidebarResize"' in index and b'id="workspaceResize"' in index
                   and b'id="workspaceSplitResize"' in index
                   and b'id="modeBar"' not in index and b'id="modalityBar"' not in index
                   and b'id="attachButton"' in index and b'id="fileInput"' in index
                   and b'id="workspaceSyncToggle"' in index
                   and b'id="welcomeComposerSlot"' in index and b'quick-card' not in index
                   and b'id="connectionPill"' not in index and b'id="artifactsButton"' not in index))
    checks.append(("index.html: no tiny composer/footer captions or Ctrl N chip",
                   b'id="composerHint"' not in index and b'class="status-bar"' not in index
                   and b"Ctrl N" not in index and b'id="connectorsNavBtn"' not in index))
    checks.append(("index.html: Arena legal links under profile",
                   b"terms-of-use" in index and b"privacy-policy" in index
                   and b"cookie-policy" in index))

    app_js = entries.get("arena/web/static/app.js", b"")
    checks.append(("app.js: demo app_version 5.1.0", b"app_version: '5.1.0'" in app_js))
    checks.append(("app.js: cloud chat open passes title",
                   b"openArenaChat(id, type, title)" in app_js))
    checks.append(("app.js: Arena dropdowns + vote + chat lock",
                   b"openModePopover" in app_js and b"openModalityPopover" in app_js
                   and b"modalityOptionsForMode" in app_js and b"renderVoteBar" in app_js
                   and b"chatLocked" in app_js))
    checks.append(("app.js: persistent resize + real icon helpers + local MCP Add",
                   b"setupLayoutResize" in app_js and b"arenaDesktop.layout.v1" in app_js
                   and b"ORG_LOGOS" in app_js and b"connectorLogoNode" in app_js
                   and b"accountAvatarNode" in app_js and b"openAddConnectorModal('local')" in app_js))
    checks.append(("app.js: no composer agent switch",
                   b"agent-kind-btn" not in app_js))
    checks.append(("app.js: composer refresh syncs selection controls and models nav",
                   b"renderModeBars();" in app_js and b"renderModelsNavCount();" in app_js))
    checks.append(("app.js: attachments + minimalist menus + 4-tab settings",
                   b"uploadAttachment" in app_js and b"attachment-chip" in app_js
                   and b"Drop files to attach" not in app_js and b"dropOverlay" in app_js
                   and b"Use default folder" in app_js and b"settings-tab-diagnostics" not in app_js
                   and b"openArtifacts" not in app_js and b"connectionPill" not in app_js))
    checks.append(("app.js: v4.9 centered composer, connector switches, fixed resize vars",
                   b"welcomeComposerSlot" in app_js and b"setArenaConnectorEnabled" in app_js
                   and b"--sidebar-w" in app_js and b"--workspace-panel-w" in app_js
                   and b"archiveArenaChat" in app_js))
    checks.append(("app.js: workspace tree root row",
                   b"workspace-tree-root" in app_js))
    checks.append(("app: multi-account store + switch/add routes",
                   b"/api/accounts/switch" in app and b"/api/accounts/add" in app
                   and b"_register_account" in app and b"generating_chat_ids" in app
                   and b"_chat_stop_event" in app))
    checks.append(("engine: local-only session clear for account switching",
                   b"clear_session_cookies" in engine))
    agent_mod = modules.get("arena.agent", b"")
    checks.append(("app+agent: lean tool context (artifact offload + repeat guard)",
                   b".arena-artifacts" in agent_mod and b"trim_output" in agent_mod
                   and b"sig_counts" in app and b"mcp_guide_hash" in app))
    checks.append(("app: compact + cross-chat memory",
                   b"/compact" in app and b"pending_digest" in app
                   and b"MEMORY.md" in app and b"_build_digest" in app
                   and b"Persistent memory" in agent_mod))
    checks.append(("app: sub-agents + updates + settings backup + diagnostics",
                   b"delegate_task" in app and b"_run_subagent" in app
                   and b"/api/update-check" in app and b"/api/settings/export" in app
                   and b"/api/diagnostics" in app and b"delegate_guide" in agent_mod))
    checks.append(("app: v5 agent core (fan-out, checkpoints, automations, watchdogs)",
                   b"_fanout_run" in app and b"/api/checkpoints/" in app
                   and b"/api/automations" in app and b"STREAM_IDLE_TIMEOUT" in app
                   and b"read_files" in agent_mod and b"run_shell_command" in agent_mod))
    checks.append(("app: v5 chats (search, export.md, fork, snapshot, elo, beta)",
                   b"/api/search" in app and b"/export.md" in app and b"/fork" in app
                   and b"/api/workspace/snapshot" in app and b"/api/elo" in app
                   and b"beta_updates" in app))
    checks.append(("app.js: v5 UI (fan-out, pins, automations, undo, jump pill)",
                   b"fanoutModels" in app_js and b"arenaDesktop.modelFavs" in app_js
                   and b"renderAutomationsPanel" in app_js and b"Undo file changes" in app_js
                   and b"initJumpPill" in app_js))
    checks.append(("app: plan approval + checklist + AGENTS.md (v4.20)",
                   b"propose_plan" in app and b"set_todos" in app and b"AGENTS.md" in app
                   and b"propose_plan" in agent_mod))
    checks.append(("app: model questions to the user (ask_user)",
                   b"ask_user" in app and b"/api/questions" in app and b"ask_user" in agent_mod))
    checks.append(("app: Arena Agent file bridge (Workspace Sync downloads)",
                   b"_native_file_bridge" in app and b"synced_files" in app))
    checks.append(("app.js: Workspace Sync switch + settings in accounts menu, no old Tools pill",
                   b"workspaceSyncToggle" in app_js and b"setWorkspaceSync" in app_js and b"visibleLaneText" in app_js
                   and b"account-settings" in app_js and b"tools-toggle" not in app_js))
    checks.append(("app.js: accounts menu + per-chat generations + status marks",
                   b"openAccountMenu" in app_js and b"switchToAccount" in app_js
                   and b"syncGenerationState" in app_js and b"run-dot" in app_js
                   and b"done-dot" in app_js and b"unreadDone" in app_js))

    style = entries.get("arena/web/static/style.css", b"")
    checks.append(("style.css: Arena Reskin + vote bar",
                   b"Arena-style Reskin" in style and b".vote-bar" in style))
    checks.append(("style.css: v4.7 dropdowns, icons and resize handles",
                   b".popover-menu" in style and b".mode-chip" in style
                   and b".modality-chip" in style and b".model-org-logo" in style
                   and b".resize-handle" in style and b".workspace-card" in style
                   and b".attachment-chip" in style and b".attach-button" in style))
    static_text = (index + app_js + style).decode("utf-8", "ignore")
    checks.append(("static UI: English only, no Cyrillic or old product name",
                   not any("\u0400" <= ch <= "\u04ff" for ch in static_text)
                   and b"Arena Personal" not in index + app_js + style))

    agent_m = modules["arena.agent"]
    checks.append(("v5.1: auto-continue + soft anti-loop + delete_subagent",
                   b"_turn_auto" in app and b"auto_retry" in app
                   and b"blocked: repeated call" in app and b"delete_subagent" in app
                   and b"delete_subagent" in agent_m))
    checks.append(("v5.1 UI: back-to-bottom + add account cookies + no rate-limit lock",
                   b"scrollBottomBtn" in app_js and b"Add an account with cookies" in app_js
                   and b"auto_continue" in app_js
                   and b"Arena asked to wait before sending again" not in app_js))
    models_m = modules["arena.models"]
    checks.append(("catalog: signed-in fetch + force refresh (new models appear)",
                   b"_catalog_fetch_json" in app and b"model catalog fetch failed" in app
                   and b"fetch_json" in models_m))
    checks.append(("parallel: fan-out pairs + live fork anchor + automations edit",
                   b"_run_children_pair" in app and b"msg_index" in app
                   and b"Edit automation" in app_js and b"Save changes" in app_js
                   and b"force ? '&force=1'" in app_js))

    ok = True
    for label, passed in checks:
        print(("PASS " if passed else "FAIL ") + label)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
