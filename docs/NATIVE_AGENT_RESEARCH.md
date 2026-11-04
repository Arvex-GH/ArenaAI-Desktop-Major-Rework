# native Arena Agent Mode: dismantled protocol and integration plan

Date of review: 2026-11-01. Sources: JS- chanky production - arena.ai
(`1tul2cn6g9kjk.js`, `3b3qe3k896tjp.js`) and public documentation Trigger.dev AI Chat
(https://trigger.dev/docs/ai-chat/reference, /sessions, /server-chat).

## why "Agent" in Arena Desktop v4 - v4.2 wasn't a real Agent Mode

The local Agent application is its own tool cycle over a conventional one
evaluation: request goes to `/nextjs-api/stream/create-evaluation` with
`mode: "direct-battle"` and a specific text model automatically selected.
So on the arena side. ai such dialogue is literally a direct-chat with a name
Models (Argon, etc.) as seen by the user. It's not a signature bug or a race.
Statuses (race in v4.1 did exist and fixed separately): at Arena
Agent Mode is a completely separate circuit, which was not included in the application.

## how the real Arena Agent Mode works

The agent works on **Trigger.dev AI Chat** (durable sessions):

- `chat-agent` task (outdated folback - `agent-runner`);
- the session has two durability channels: `.in` (user messages) and `.out`
(SSE- chankov Stream AI SDK UI Message stream: text-delta, niger-delta,
tool-input-available, tool-output-available, data-*, etc.
- channel access - in-session JWT (public access token, PAT) with braces
`read:sessions:{triggerAddress}` + `write:sessions:{triggerAddress}`;
- in the production browser does not go to api.trigger.dev directly, but through a proxy on
Arena domain: `https://arena.ai/ai-proxy` (`AI_PROXY_PATH = "/ai-proxy"`).

### endpoints Arena (domain arena.ai)

1.  Establishment of the session and first communication:
`POST /nextjs-api/stream/create-chat`
Body: '{message:{id, role:"user", parts:[{type:"text",text}]}
recaptchaV3Token|recaptchaV2Token, cx:"programmatic"(?), clientPlatform:"web",
timezone, enabledConnectors?:[slug... ], modelId? , harnessId?}`.
Answer: '{id: sessionId, bootstrap:{sessionId, userId, inputId,
taskId:"chat-agent", triggerAddress, publicAccessToken, ttlMs, ...}}`.
reCAPTCHA v3 action on the site - `agentic_chat_submit` (evaluation - `chat_submit`);
At challenger, the site replicates v2 token from the box.
2.  Start/restart Trigger session and PAT:
`POST /api/chat/trigger-session` `{sessionId, timezone}` → `{publicAccessToken}`.
3.  PAT update: `POST /api/chat/trigger-token` `{sessionId}` in `{token}`.
4.  Session and transcript: `GET /api/chat/:id`, `GET /api/chat/:id/messages`.
5.  coding (productMode "coding"): `/api/coding-agent/sessions...` - we do not need it.

### Trigger date (via https://arena.ai/ai-proxy)

-subscription: `GET /realtime/v1/sessions/{triggerAddress}/out`,
Title `Authorization: Bearer <PAT>`, `Accept: text/event-stream`,
`Timeout-Seconds: 120` for renewal-`Last-Event-ID`.
The answers may include `X-Session-Settled: true` (the agent is flashing and there is no process) and `X-Stream-Version`.
Stream format: SSE. V1(`data` = JSON channel) or batch event.
(`event: batch`,`data = {records:[{seq_num, headers:[[k,v]], body}]}`)
`body` is JSON `{id, data: chunk}`.
Channel: `{"type":"trigger:turn-complete"}`; Fresh PAT may come.
In the recording head `public-access-token` or in the wallet itself. Other `trigger:*`
Businessmen are civil servants and missed it.
Model: `POST /realtime/v1/sessions/{triggerAddress}/in/append`
Title `Authorization: Bearer <PAT>`, `X-Part-Id: <uuid>`,
`x-trigger-source: sdk` fuselage
`{ "kind":"message","payload":{"message":UIMessage,"chatId":triggerAddress,
“trigger”: “submit-message”, “messageId”:id, “metadata:clientData}}`,
The answer is `{seq}`.
-Stop: the same appendix as the main body of `{"kind":"stop"}` (website sold separately)
`targetInputId` through its fetch fighter; Transportation without it).
-clientData website: `{ originhost: "arena.ai", time zone, arenavisitid? ,
PosthogFlags's What is enabledConnectors? , clientPlatform:"web"}。
401/403 Retransmission PAT through `trigger-token`; No session → `trigger-session`.

### what else exists but is not included in v1 integration

- HITL- tool confirmation (`submitHitlResponse`, parts condition)
`approval-requested/responded`: user responses go the same append c
The last assistant message filtered to tool parts.
- attachments (`uploads` to first message metadata, file-parts) and generated
Files/previews (`/api/chat/:id/preview`, `workspace.latest` - mainly coding).
- the field `cx` (CLIENT_INTERACTION_FIELD) is a coded behavioural block
Antibot telemetry (its codec: field layout, salt, control bits);
`198742` module in bundle. A live account scan (v4.3) revealed:
The synthesized value of `programmatic` the server rejects c
`HTTP 400 Message validation failed`.  With v4.3.1 the field is not sent, but created
Sessions go up the ladder of body options (no cx in, no connectors in the other
reCAPTCHA action) with the log of every attempt in `desktop.log`.
- `modelId` (Max Router in agent) and `harnessId` - according to user's requirements do not send:
Agent is always automatic.

## Arena Desktop integration plan (implemented in v4.3)

- `arena/native_agent.py`: pure constructors of bodies, parser SSE (v1+batch),
Maping chankov AI SDK in SSE- events of the existing interface.
- Backend: creating a session through the Arena window (cookies and reCAPTCHA v3/v2 are needed)
Same mechanism as evaluation; action redefined to `agentic_chat_submit`
append/ subscription - normal HTTPS requests for `arena.ai/ai-proxy` with PAT (+cookies),
PAT is not written in a file, only `sessionId`, `triggerAddress`, `lastEventId` are stored.
- Frontend: type of Agent «Arena Agent» stremit the answer directly in the application window;
The "local" is left for the folder tools. Connected account connectors
Transmitted to `enabledConnectors`.

## risks and what to check on a live account

1.  Exact behavior in the absence/infidelity of `cx` and v3-action.
2.  Recaptcha-challenger signal at `create-chat` (code/status) - repeat from v2.
3.  Does `/ai-proxy` require cookies in addition to PAT (we send both).
4.  Real names of agent data parts (sources, files) -v1 shows text,
Reflections, tools and media; Exotic parts may not show off.
5.  HITL- connector confirmations - phase 2.

## live v4.3 - v4.3.2 and go to page drive (v4.4.0)

Chronology on this Windows account:

- v4.3 (direct POST create-chat with `cx:"programmatic"`): HTTP 400
`Message validation failed`.
- v4.3.1: engine regression (undeclared variable `extraHeaders` b)
`_FETCH_JS` - requests did not leave at all; Fixed in v4.3.2, added
A test that actually runs JS engine in node.
- v4.3.2 (staircase of bodies without `cx`, two reCAPTCHA-action, X-Agent-Browser-
Attempt-Id: all options - same HTTP 400.

Chang by-line analysis (`3b3qe3k896tjp.js` dispatch handler)
AgentLanding, `0lfpyd7y-31vv.js` module 198742, `3-6xdh9q1c7dk.js` module
385144, `1tul2cn6g9kjk.js` modules 508293/926227 gave:

- our body matched the site field in the field (`message{id,role,parts}`),
`recaptchaV3Token` action `agentic_chat_submit`, `clientPlatform:"web"`,
`timezone`, `enabledConnectors`, heading attempted -id, other than `cx` and
UUIDv4 instead of v7. Site sends `cx` always and everywhere: create-evaluation,
retry, create-chat, `/api/coding-agent/sessions` and append metadata.
(our Direct without `cx` live passed - evaluation- endpoint softer; agent
create-chat - no.)
- `cx` codec: version 2 (legacy 1); Bit packet of signal buckets
(click, press, mouse movement, heartbeat, beating) with order
Fields and masks removed from salt (`deriveClientInteractionSalt`), plus
integrity bits - check the document environment. `"programmatic"` is not a blob,
A one of the values of the internal field `submitKind`
(`["enter","button","programmatic","unknown"]`).  Play codec and
We will not synthesize telemetry in principle.
- Bootstrap (Zod scheme `chatBootstrapSchema`, strict):
`{version:1, sessionId, userId, inputId, taskId:"chat-agent",
triggerAddress, publicAccessToken, ttlMs<=60000, feedbackType,
customFeedbackArm}` and the invariant `triggerAddress === sessionId`.
- the first message per session is delivered by the chat page itself (mechanism)
pending-input recovery with `landingInputId`; append's of transport too
Carry `cx` in metadata.

v4.4.0 - **page drive**: session creates and sends messages
This site interface in the engine window (`BrowserEngine.drive_agent_send`):
Navigation on `/agent[/<id>]`, printing in the real composer (textarea or
contenteditable, `execCommand('insertText')` + fallback
The real send button. The application only monitors the page responses
(fetch-observer on creation-chat /coding-sessions/append, without modification
Queries and urls, gets `{id, bootstrap}`, then the same as before: PAT
trigger-session/token and mirroring zxq4qxz-stream into our UI.
Human scans (v2/Cloudflare) while showing the site itself in the window
Arena.  Open questions for live inspection: composer selectors on
Real page (heuristics + diagnostics in `drive ...` log), behavior
When login-gay, the speed of delivery of the first message page.

## v4.5.0: return to the direct API (without entering the window) + original cx from the page

Page drive from v4.4.0 rejected (" same window mode, window ")
Simply hidden "and demanded to work" as with Direct "- without literal input
Prompta to the Arena window. The straight path is restored as the main:

- create: direct `POST /nextjs-api/stream/create-chat` through the engine
(UUIDv7 id, `agentic_chat_submit`, attempt-id title), stairs
Options with full log; Subsequent messages - a straight append to
`/ai-proxy` with `cx` in the move metadata.
- original `cx` without printing in the window: `ENGINE.native_preflight` performs in
Open page arena. ai probe, which through its own rantime
Turbopack (`globalThis.TURBOPACK.push` - the same chang loading mechanism:
Register the factory + push with `runtimeModuleIds` for execution
Module context and imports the client interaction module 385144,
Calling him a couple of `a(k())`. `k()` - a global photo taken by the website
Marks `submitKind:"programmatic"` honestly; integrity- bits and surroundings
Calculates the page code from its real state. We don't synthesize blob,
Not correct and not reuse between shipments (fresh for each).
- Preflight also checks reCAPTCHA v3 checker (token length in log) -
This closes the previously untested "silent token null" hypothesis.
- if the module is not loaded on the current page, the engine passes to /agent
(simple navigation, no input) and repeats the probe; If blob's not available,
Creation honestly goes without `cx` - the solution is behind the server.
- the trick with rantime is covered by a node test against a fake rantime with the same.
push semantics (transcription from public chanka).

Open live verification questions v4.5.0: will create-chat blog look like
programmatic;  Whether the agent ranner `cx` validates in the metadata append;
Stability id of module 385144 between depiles (diagnostics - trace probe in
`desktop.log`, lines `preflight:` and `native create [...]`.

## v4.5.1: end of batch mode, save session and continue cloud agent chats

Live v4.5.0 test confirmed the main thing (create with original `cx` accepted,
The native Agent opens inside the application, but revealed two defects:

1.  ** the move did not end on its own. ** site transport completes the title move
batch- records `trigger-control: turn-complete` (the body of the record is empty; ibid.)
The title `session-in-event-id` for resume; Chang, come on.
`type:"trigger:turn-complete"` is just a generic folback. Our client,
Checked only chank, so in batch mode the signal was lost and the cycle
Resubscribed to Stop. Fix: `is_turn_complete_record(record)`
Checks the whole record (headers + chank); Further dealt with
v1- envelope `{id, data:{...}}` without damage to real `data-*` chanks,
And as a spare terminator the pair is used "the subscription response will
`X-Session-Settled: true` and the whole cycle brought no records after already
Seen activity "(records are durable, cursor saved - no losses).
`X-Stream-Version`, increments and number of entries by cycle are added to the log.
2.  ** each request created a new chat. ** `native_generate` wrote chat only
After normal completion of the generator; Unfinished stream + Stop
(SSE cliff) means no saving, native coordinates
The following message was lost and created a new session. Fixes (in Russian) :
Shared `result`, the coordinates are published immediately after create,
`last_event_id` is updated on each record, `_save_chat` is executed
In `finally` without `yield` inside - the state is experiencing Stop/ shutdown.

Continuation of other chats: cloud agentic- records from `/api/history/unified`
They used to only open on the outside. Now `api_arena_chat_open` for
`agentic` imports chat locally: `agent_backend="arena"`,
`native={session_id, trigger_address=session_id, last_event_id=None,
needs_drain=True}`; messages are imported best-effort from `GET /api/chat/{id}`
(`history_sync.agentic_messages`, forms `parts` and `content`; read malfunction)
Does not block imports. Before the first append in the imported session
`_native_drain_cursor` passes `.out` to the current "head" cursor,
So that the old extradition will not be repeated in a new answer. In the list of cloud
agentic history - records are now linked to a local copy of the software
`eval_id == session_id`, as evaluation.

Not checked live (you need a user test): what batch signal comes
In the form described on his account; Real form of reply
`GET /api/chat/{session_id}` (import messages are tolerant, but the form is not
- confirmed; drain behavior at a large session history.