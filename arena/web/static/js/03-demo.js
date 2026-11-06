  /* Demo backend (only with ?demo=1). Every event here is simulated locally. */
  /* ---------------------------------------------------------------------- */
  function demoArtwork(title, hue = 145, subline = 'Arena Desktop · demo') {
    const safeTitle = String(title).replace(/[<>&"]/g, '');
    const safeSub = String(subline).replace(/[<>&"]/g, '');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500"><defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 34% 13%)"/><stop offset="1" stop-color="hsl(${hue + 36} 24% 7%)"/></linearGradient><radialGradient id="glow" cx=".68" cy=".2" r=".8"><stop offset="0" stop-color="hsl(${hue} 54% 66%)" stop-opacity=".42"/><stop offset="1" stop-color="hsl(${hue} 54% 54%)" stop-opacity="0"/></radialGradient></defs><rect width="800" height="500" fill="url(#bg)"/><rect width="800" height="500" fill="url(#glow)"/><circle cx="640" cy="240" r="128" fill="none" stroke="hsl(${hue} 42% 68%)" stroke-opacity=".27"/><circle cx="640" cy="240" r="84" fill="none" stroke="hsl(${hue} 42% 68%)" stroke-opacity=".42"/><circle cx="640" cy="240" r="18" fill="hsl(${hue} 54% 73%)" fill-opacity=".9"/><path d="M75 384h400M75 358h286M75 332h322" stroke="hsl(${hue} 22% 50%)" stroke-opacity=".45" stroke-width="2" stroke-linecap="round"/><text x="75" y="174" fill="#edf2ed" font-family="Segoe UI, Arial, sans-serif" font-size="34" font-weight="600">${safeTitle}</text><text x="75" y="214" fill="hsl(${hue} 25% 69%)" font-family="Segoe UI, Arial, sans-serif" font-size="17">${safeSub}</text><rect x="75" y="284" width="122" height="29" rx="6" fill="hsl(${hue} 30% 44%)" fill-opacity=".28"/><text x="91" y="304" fill="hsl(${hue} 42% 82%)" font-family="Segoe UI, Arial, sans-serif" font-size="13">simulated media</text></svg>`;
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  }

  function demoModel(id, name, org, color, rank, score, capabilities = []) {
    return { id, name, org, org_display: org, color, rank, arena_score: score, capabilities };
  }

  const demoModels = {
    text: [
      demoModel('atlas-3', 'Atlas 3', 'Nordic Labs', '#9fc7f4', 1, 1385, ["Text", "Reasoning", "200K context"]),
      demoModel('sable-r1', 'Sable R1', 'Sable AI', '#bda7eb', 2, 1364, ["Text", "Reasoning", "Code"]),
      demoModel('lumen-2-pro', 'Lumen 2 Pro', 'Tessera', '#eac28f', 3, 1320, ["Text", "Long context"]),
      demoModel('kestrel-max', 'Kestrel Max', 'Monolith', '#98d6b1', 4, 1298, ["Text", "Fast"]),
      demoModel('northstar-mini', 'Northstar Mini', 'Fjord', '#a6d9d6', 5, 1240, ["Text", "Economical"]),
      demoModel('meridian-2', 'Meridian 2', 'Arcadia', '#e1adad', 6, 1198, ["Text", "Instructions"])
    ],
    code: [
      demoModel('forge-1-coder', 'Forge 1 Coder', 'Monolith', '#97d9b8', 1, 1392, ["Code", "Tools"]),
      demoModel('atlas-3', 'Atlas 3', 'Nordic Labs', '#9fc7f4', 2, 1360, ["Code", "Reasoning"]),
      demoModel('kestrel-code', 'Kestrel Code', 'Sable AI', '#c1b1ec', 3, 1318, ["Code", "Fast"]),
      demoModel('lumen-dev', 'Lumen Dev', 'Tessera', '#ebc48f', 4, 1272, ["Code", "Files"])
    ],
    search: [
      demoModel('compass-search', 'Compass Search', 'Arcadia', '#a9c7ef', 1, 1330, ["Search", "Sources"]),
      demoModel('atlas-search', 'Atlas Search', 'Nordic Labs', '#9fc7f4', 2, 1296, ["Search", "Reasoning"]),
      demoModel('scout-mini', 'Scout Mini', 'Fjord', '#a6d9d6', 3, 1215, ["Search", "Economical"])
    ],
    image: [
      demoModel('prism-canvas', 'Prism Canvas', 'Tessera', '#e5b48e', 1, 1310, ["Images"]),
      demoModel('aurora-image', 'Aurora Image', 'Sable AI', '#bfa8e9', 2, 1278, ["Images"]),
      demoModel('lumen-sketch', 'Lumen Sketch', 'Nordic Labs', '#a7d8c2', 3, 1224, ["Images"])
    ],
    video: [
      demoModel('reelforge', 'ReelForge', 'Monolith', '#e3b58f', 1, 1200, ["Video · preview"]),
      demoModel('motionlite', 'MotionLite', 'Arcadia', '#a9c7ef', 2, 1168, ["Video · preview"])
    ]
  };

  const demoStore = {
    authenticated: true,
    email: 'demo@local.invalid',
    engineOpen: false,
    offline: false,
    loading: false,
    sessionError: null,
    permissionMode: 'acceptEdits',
    workspace: 'C:\\ArenaWorkspace\\foto-studio',
    mode: 'direct',
    modality: 'text',
    chats: [],
    messages: {},
    cloudChats: [],
    cloudMessages: {},
    connectors: [],
    pendingPermissions: [],
    mediaChoices: [],
    loginAt: 0,
    pendingAdd: false,
    activeChatId: null,
    activeStream: null,
    streams: new Set(),
    currentAccountId: 'demo-acc-1',
    accounts: [
      { id: 'demo-acc-1', email: 'demo@local.invalid', name: 'Demo User', avatar_url: null, expired: false },
      { id: 'demo-acc-2', email: 'second@local.invalid', name: 'Second User', avatar_url: null, expired: false },
      { id: 'demo-acc-3', email: 'old@local.invalid', name: 'Old User', avatar_url: null, expired: true }
    ],
    serial: 0,
    settings: {}
  };

  (function seedDemoChats() {
    const now = Date.now();
    const landingId = 'demo-chat-landing';
    const testsId = 'demo-chat-tests';
    const searchId = 'demo-chat-search';
    const imageId = 'demo-chat-image';
    demoStore.messages[landingId] = [
      {
        role: 'user', created_at: new Date(now - 2.4 * 3600e3).toISOString(),
        content: "Put together a landing structure for a small photo studio. We need a calm visual language, no noise, and an adaptive grid. Suggest two directions."
      },
      {
        role: 'assistant', lane: 'a', model_id: 'atlas-3', model_name: 'Atlas 3', created_at: new Date(now - 2.3 * 3600e3).toISOString(),
        content: "### direction A - \"quiet gallery\"\n\nMain idea: photos are an interface. Minimum text, lots of air and one clear scenario.\n\n1.  ** first screen :** series of 3 frames, studio name and one button.\n2.  ** work :** grid 2 x 2 with different proportions.\n3.  ** about studio :** short manifesto and contact.\n4.  ** palette :** warm graphite, paper, one low accent.\n\n```css\n.hero {\n  display: grid;\n  grid-template-columns: minmax(0, 1.2fr) minmax(220px, .8fr);\n  gap: clamp(1rem, 4vw, 4rem);\n}\n\n@media (max-width: 760px) {\n  .hero { grid-template-columns: 1fr; }\n}\n```\n\nI do not promise a ready site before launch: first it is necessary to agree on the structure and photos.",
        reasoning: "Comparing two calm approaches. For a photo studio, the rhythm of the grid and the quality of the shots are more important than decorative effects.",
        sources: []
      },
      {
        role: 'assistant', lane: 'b', model_id: 'sable-r1', model_name: 'Sable R1', created_at: new Date(now - 2.3 * 3600e3).toISOString(),
        content: "### Direction B — \"Magazine Story\"\n\nRestrained editorial grid, large typography, and thoughtful captions for the works.\n\n- Top row: studio name, city, contact.\n- Large headline-story instead of an advertising slogan.\n- Asymmetrical pairs of photos.\n- The mobile version maintains the feeling of a magazine rather than turning into cards.\n\n**Next step:** choose one hero scenario and select 6–9 photos for the first screen.",
        sources: []
      }
    ];
    demoStore.messages[testsId] = [
      { role: 'user', created_at: new Date(now - 26 * 3600e3).toISOString(), content: "Check the approach to tests for the Flask endpoint. Which cases should be added?" },
      { role: 'assistant', lane: 'a', model_id: null, model_name: 'Agent', created_at: new Date(now - 25.7 * 3600e3).toISOString(), content: "Add checks for a successful JSON response, an incorrect request body, an empty set, and a backend failure. Do not mix the network client and the Flask test client. In the demo, this is an illustrative example, not an actual run." }
    ];
    demoStore.messages[searchId] = [
      { role: 'user', created_at: new Date(now - 3.2 * 86400e3).toISOString(), content: "Collect sources on WebView2 and pywebview — only for illustrating demo mode." },
      { role: 'assistant', lane: 'a', model_id: 'compass-search', model_name: 'Compass Search', created_at: new Date(now - 3.1 * 86400e3).toISOString(), content: "Below are **simulated demo-mode sources**. These are not the results of actual search.", sources: [
        { title: "Demonstration source on WebView2", url: 'https://example.com/demo/webview2', snippet: "Example of a source card." },
        { title: "Demonstration source on pywebview", url: 'https://example.com/demo/pywebview', snippet: "Example of a source card." }
      ] }
    ];
    demoStore.messages[imageId] = [
      { role: 'user', created_at: new Date(now - 4.5 * 86400e3).toISOString(), content: "Come up with three calm covers for a podcast." },
      { role: 'assistant', lane: 'a', model_id: 'prism-canvas', model_name: 'Prism Canvas', created_at: new Date(now - 4.4 * 86400e3).toISOString(), content: "Here is a sample demo cover. Real image generation was not performed.", media: [
        { kind: 'image', url: demoArtwork("Quiet Echo", 155, "demo image · not generated"), title: "Quiet Echo · demo cover" }
      ] }
    ];
    const secondId = 'demo-chat-second-account';
    demoStore.messages[secondId] = [
      { role: 'user', created_at: new Date(now - 1.2 * 3600e3).toISOString(), content: "A chat that belongs to the second demo account." },
      { role: 'assistant', lane: 'a', model_id: 'atlas-3', model_name: 'Atlas 3', created_at: new Date(now - 1.1 * 3600e3).toISOString(), content: "This dialogue is only listed while the second demo account is current." }
    ];
    demoStore.chats = [
      { id: secondId, title: "Second account notes", updated_at: new Date(now - 1.1 * 3600e3).toISOString(), mode: 'direct', modality: 'text', account: 'demo-acc-2' },
      { id: landingId, title: "Landing page for a photo studio", updated_at: new Date(now - 2.3 * 3600e3).toISOString(), mode: 'side-by-side', modality: 'text' },
      { id: testsId, title: "Check approach to tests", updated_at: new Date(now - 25.7 * 3600e3).toISOString(), mode: 'agent', modality: 'text' },
      { id: searchId, title: "Sources: WebView2 and pywebview", updated_at: new Date(now - 3.1 * 86400e3).toISOString(), mode: 'direct', modality: 'search' },
      { id: imageId, title: "Cover options", updated_at: new Date(now - 4.4 * 86400e3).toISOString(), mode: 'direct', modality: 'image' }
    ];
    const cloudEvalId = 'demo-cloud-evaluation';
    const cloudAgentId = 'demo-cloud-agent';
    demoStore.cloudMessages[cloudEvalId] = [
      { role: 'user', created_at: new Date(now - 5.2 * 86400e3).toISOString(), content: "This dialogue seems to have been created on the arena.ai website." },
      {
        role: 'assistant', lane: 'a', model_id: 'atlas-3', model_name: 'Atlas 3',
        created_at: new Date(now - 5.1 * 86400e3).toISOString(),
        reasoning: "In the demo, I check that the reasoning remains a normal paragraph and that each word is not moved to a separate line.",
        content: "When imported, this demonstration dialogue will also appear in the local history of Arena Desktop. The actual request to arena.ai was not performed."
      }
    ];
    demoStore.cloudChats = [
      {
        id: cloudEvalId, eval_id: cloudEvalId, type: 'evaluation', title: "Dialogue from the arena.ai website · demo",
        updated_at: new Date(now - 5.1 * 86400e3).toISOString(), created_at: new Date(now - 5.2 * 86400e3).toISOString(),
        mode: 'direct', modality: 'text', message_count: 2, local_chat_id: null, source: 'arena'
      },
      {
        id: cloudAgentId, eval_id: cloudAgentId, type: 'agentic', title: 'Arena Agent · demo',
        updated_at: new Date(now - 6.4 * 86400e3).toISOString(), created_at: new Date(now - 6.5 * 86400e3).toISOString(),
        mode: 'agent', modality: 'text', message_count: 4, local_chat_id: null, source: 'arena'
      }
    ];
    demoStore.connectors = [{
      id: 'filesystem', name: 'filesystem', preset_id: 'filesystem', source: 'official', transport: 'stdio',
      enabled: true, running: false, status: 'stopped',
      description: "Demo entry of the official MCP Filesystem. The real process does not run in preview.",
      command_display: 'npx -y @modelcontextprotocol/server-filesystem C:\\ArenaWorkspace\\foto-studio',
      tools: [], tools_count: 0, error: null
    }];
    demoStore.arenaConnectors = [
      { slug: 'github', name: 'GitHub', description: 'Work with repos, issues, and pull requests.', auth_status: 'connected' },
      { slug: 'google-drive', name: 'Drive', description: 'Find and manage files in Google Drive.', auth_status: 'connected' },
      { slug: 'google-docs', name: 'Docs', description: 'Create, read, and edit Google Docs.', auth_status: 'disconnected' },
      { slug: 'notion', name: 'Notion', description: 'Look up your documents.', auth_status: 'disconnected' },
      { slug: 'gmail', name: 'Gmail', description: 'Connect your email account.', auth_status: 'disconnected' },
      { slug: 'linear', name: 'Linear', description: 'Track issues, projects, and cycles.', auth_status: 'disconnected' },
      { slug: 'google-calendar', name: 'Calendar', description: 'Connect your Google Calendar.', auth_status: 'disconnected' },
      { slug: 'google-slides', name: 'Slides', description: 'Read and edit slides in your presentations.', auth_status: 'disconnected' },
      { slug: 'google-sheets', name: 'Sheets', description: 'Read, edit, and format your spreadsheets.', auth_status: 'disconnected' },
      { slug: 'dropbox', name: 'Dropbox', description: 'Browse, read, and organize your Dropbox files.', auth_status: 'disconnected' },
      { slug: 'vercel', name: 'Vercel', description: 'Manage deployments and projects.', auth_status: 'disconnected' },
      { slug: 'supabase', name: 'Supabase', description: 'Manage your Postgres database and auth.', auth_status: 'disconnected' }
    ].map((item) => ({ ...item, logo_url: null, connected: item.auth_status === 'connected' }));
    demoStore.arenaConnectorPending = {};
    demoStore.activeChatId = null;
  })();

  const demoRouter = {
    ...demoModel('demo-max-router', "Max Router (demo)", "Demo", '#a9c4ae', 0, null, ["Router"]),
    provider: 'boss-bandit', user_selectable: true, is_router: true, fixed: true
  };

  function demoFindModel(id) {
    if (!id) return null;
    if (String(id) === String(demoRouter.id)) return demoRouter;
    for (const list of Object.values(demoModels)) {
      const found = list.find((model) => String(model.id) === String(id));
      if (found) return found;
    }
    return null;
  }

  function demoDefaultModels(modality) {
    return demoModels[modality] || demoModels.text;
  }

  function demoValidateSelection(mode, modality) {
    if (modality === 'video' && mode !== 'battle') throw new ApiError('Video is available only in Battle Mode; Arena selects both models.', 400);
    if (modality === 'auto' && mode !== 'battle') throw new ApiError('Auto is available only in Battle Mode.', 400);
    if (mode === 'agent' && modality !== 'text') throw new ApiError('Agent Mode is text-only; its model is assigned automatically.', 400);
  }

  const demoAgentIdentity = { id: 'agent', name: 'Agent', org: "Automatic model selection", org_display: "Automatic model selection", color: '#a9c4ae', capabilities: [] };

  const demoWorkspaceTreeData = {
    '.': [
      { name: 'src', path: 'src', is_dir: true, size: 0 },
      { name: 'tests', path: 'tests', is_dir: true, size: 0 },
      { name: 'README.md', path: 'README.md', is_dir: false, size: 421 },
      { name: 'page.html', path: 'page.html', is_dir: false, size: 512 },
      { name: 'notes.txt', path: 'notes.txt', is_dir: false, size: 188 }
    ],
    src: [
      { name: 'app.py', path: 'src/app.py', is_dir: false, size: 1320 },
      { name: 'normalize.py', path: 'src/normalize.py', is_dir: false, size: 640 }
    ],
    tests: [
      { name: 'test_normalize.py', path: 'tests/test_normalize.py', is_dir: false, size: 512 }
    ]
  };

  const demoWorkspaceFileData = {
    'README.md': "# Photo Studio (demo)\n\nThis is a demonstration file in the browser. The real local folder was not read.",
    'page.html': "<!doctype html>\n<title>Demo page</title>\n<style>body{font-family:system-ui;background:#101418;color:#e8f0e9;padding:24px}h1{color:#9fd8ae}</style>\n<h1>Demo HTML preview</h1>\n<p>Rendered in a sandboxed frame.</p>",
    'notes.txt': "Demo notes: check the light, the grid, and the mobile version. No actions were performed on the computer.",
    'src/app.py': "# Demo file\n\ndef main() -> None:\n    print(\"Arena Desktop demo\")\n",
    'src/normalize.py': 'def normalize_name(value: str) -> str:\n    return " ".join(value.strip().split()).casefold()\n',
    'tests/test_normalize.py': "def test_demo() -> None:\n    assert True  # Demo file was not run\n"
  };

  function demoClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function demoConnectorPresets() {
    const ws = demoStore.workspace;
    const installed = new Set(demoStore.connectors.map((server) => server.preset_id || server.name));
    const presets = [
      { id: 'filesystem', name: 'Filesystem', vendor: 'Model Context Protocol', description: "Files inside the selected working folder.", requirements: 'Node.js (npx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem', Fields: [{ key: 'path', label: "Access folder", type: 'text', default: ws }], command: `npx -y @modelcontextprotocol/server-filesystem ${ws}` },
      { id: 'git', name: 'Git', vendor: 'Model Context Protocol', description: "History, status, diff, and branches of the local repository.", requirements: 'uv (uvx), Git', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/git', Fields: [{ key: 'path', label: "Repository folder", type: 'text', default: ws }], command: `uvx mcp-server-git --repository ${ws}` },
      { id: 'fetch', name: 'Fetch', vendor: 'Model Context Protocol', description: "Loading web pages as text/Markdown.", requirements: 'uv (uvx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/fetch', Fields: [], command: 'uvx mcp-server-fetch' },
      { id: 'memory', name: 'Memory', vendor: 'Model Context Protocol', description: "Agent's local long-term memory.", requirements: 'Node.js (npx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/memory', Fields: [], command: 'npx -y @modelcontextprotocol/server-memory' },
      { id: 'time', name: 'Time', vendor: 'Model Context Protocol', description: "Current time and time zones.", requirements: 'uv (uvx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/time', Fields: [], command: 'uvx mcp-server-time' },
      { id: 'sequential_thinking', name: 'Sequential Thinking', vendor: 'Model Context Protocol', description: "Structured step-by-step reasoning.", requirements: 'Node.js (npx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking', Fields: [], command: 'npx -y @modelcontextprotocol/server-sequential-thinking' },
      { id: 'everything', name: "Everything (MCP demo)", vendor: 'Model Context Protocol', description: "Official test MCP server.", requirements: 'Node.js (npx)', docs: 'https://github.com/modelcontextprotocol/servers/tree/main/src/everything', Fields: [], command: 'npx -y @modelcontextprotocol/server-everything' },
      { id: 'github', name: 'GitHub', vendor: 'GitHub', description: "Official GitHub MCP: repositories, issues, pull requests, and code.", requirements: 'Docker; GitHub Personal Access Token', docs: 'https://github.com/github/github-mcp-server', Fields: [{ key: 'token', label: 'GitHub Personal Access Token', type: 'password', required: false, help: "No token is required in the demo." }, { key: 'read_only', label: "Read-only (recommended)", type: 'checkbox', default: true }], command: 'docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN -e GITHUB_READ_ONLY=1 ghcr.io/github/github-mcp-server' },
      { id: 'playwright', name: 'Playwright', vendor: 'Microsoft', description: "Official browser MCP Microsoft.", requirements: 'Node.js (npx)', docs: 'https://github.com/microsoft/playwright-mcp', Fields: [], command: 'npx -y @playwright/mcp@latest' }
    ];
    return presets.map((preset) => ({ ...preset, installed: installed.has(preset.id) }));
  }

  function demoConnectorSummary(server) {
    const tools = Array.isArray(server.tools) ? server.tools : [];
    return { ...demoClone(server), id: server.name, tools, tools_count: tools.length };
  }

  function demoConnectorsPayload() {
    return {
      ok: true,
      demo: true,
      config_path: 'C:\\Users\\Demo\\.arena-personal\\mcp.json',
      config_error: null,
      servers: demoStore.connectors.map(demoConnectorSummary),
      presets: demoConnectorPresets(),
      transports: { stdio: true, http: false }
    };
  }

  function demoToolsFor(server) {
    const byPreset = {
      filesystem: ['list_allowed_directories', 'list_directory', 'read_text_file'],
      git: ['git_status', 'git_log', 'git_diff_unstaged'],
      fetch: ['fetch'],
      memory: ['search_nodes', 'create_entities'],
      time: ['get_current_time', 'convert_time'],
      sequential_thinking: ['sequentialthinking'],
      everything: ['echo', 'add', 'get-sum'],
      github: ['search_repositories', 'get_me', 'list_issues'],
      playwright: ['browser_navigate', 'browser_snapshot']
    };
    return (byPreset[server.preset_id] || ['demo_echo']).map((name) => ({ name, description: "Demo tool", annotations: {} }));
  }

  function demoConnectServer(server) {
    server.running = true;
    server.status = 'connected';
    server.error = null;
    server.tools = demoToolsFor(server);
    return demoConnectorSummary(server);
  }
