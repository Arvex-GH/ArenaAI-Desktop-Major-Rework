  /* ---------------------------------------------------------------------- */
  let activePopover = null;

  function positionPopover(popover, trigger) {
    if (!popover || !trigger) return;
    popover.style.visibility = 'hidden';
    popover.style.left = '8px';
    popover.style.top = '8px';
    const triggerRect = trigger.getBoundingClientRect();
    const width = popover.offsetWidth || 320;
    const height = popover.offsetHeight || 280;
    const margin = 8;
    const left = clamp(triggerRect.left, margin, window.innerWidth - width - margin);
    const below = window.innerHeight - triggerRect.bottom - margin;
    const above = triggerRect.top - margin;
    let top;
    if (below < Math.min(height, 280) && above > below) {
      top = Math.max(margin, triggerRect.top - height - 7);
    } else {
      top = triggerRect.bottom + 7;
      if (top + height > window.innerHeight - margin) top = Math.max(margin, window.innerHeight - height - margin);
    }
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.visibility = 'visible';
  }

  function closePopover(restoreFocus = true) {
    if (!activePopover) return;
    const reference = activePopover;
    activePopover = null;
    document.removeEventListener('pointerdown', reference.onOutside, true);
    document.removeEventListener('keydown', reference.onKey, true);
    reference.trigger?.setAttribute('aria-expanded', 'false');
    reference.node?.remove();
    try { reference.onClose?.(); } catch { /* noop */ }
    if (restoreFocus && reference.trigger?.isConnected) {
      try { reference.trigger.focus({ preventScroll: true }); } catch { reference.trigger.focus(); }
    }
  }

  function openPopover(trigger, { title, subtitle, content, wide = false, menu = false, initialFocus = null, onClose = null, role = 'dialog' }) {
    closePopover(false);
    if (!trigger || !els.popoverRoot) return null;
    const node = el('div', {
      class: `popover${wide ? ' popover-wide' : ''}${menu ? ' popover-menu' : ''}`,
      role,
      'aria-label': title || 'Menu',
      'data-popover': '1'
    });
    if (!menu) {
      const closeButton = el('button', {
        class: 'icon-button popover-close', type: 'button', 'aria-label': 'Close menu',
        onclick: () => closePopover()
      }, iconEl('x'));
      const head = el('div', { class: 'popover-head' },
        el('div', {}, el('p', { class: 'popover-title', text: title || 'Options' }), subtitle ? el('p', { class: 'popover-subtitle', text: subtitle }) : null),
        closeButton
      );
      node.appendChild(head);
    }
    if (content) append(node, content);
    els.popoverRoot.replaceChildren(node);
    trigger.setAttribute('aria-expanded', 'true');

    let outsideTarget = null;
    const onOutside = (event) => {
      if (!activePopover || activePopover.node !== node) return;
      if (node.contains(event.target) || trigger.contains(event.target) || event.target.closest?.('.modal-backdrop')) return;
      closePopover(false);
    };
    const onKey = (event) => {
      if (!activePopover || activePopover.node !== node) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closePopover();
        return;
      }
      const options = $$('[role="option"], [data-popover-action]', node).filter((item) => item.getAttribute('aria-disabled') !== 'true' && !item.disabled);
      if (!options.length) return;
      const index = options.indexOf(document.activeElement);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        const next = index < 0 || index >= options.length - 1 ? options[0] : options[index + 1];
        next.focus();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        const previous = index <= 0 ? options[options.length - 1] : options[index - 1];
        previous.focus();
      } else if (event.key === 'Home' && !event.target.matches('input, textarea')) {
        event.preventDefault();
        options[0].focus();
      } else if (event.key === 'End' && !event.target.matches('input, textarea')) {
        event.preventDefault();
        options[options.length - 1].focus();
      }
    };
    document.addEventListener('pointerdown', onOutside, true);
    document.addEventListener('keydown', onKey, true);
    activePopover = { node, trigger, onOutside, onKey, onClose };
    positionPopover(node, trigger);
    window.requestAnimationFrame(() => {
      if (!activePopover || activePopover.node !== node) return;
      const preferred = initialFocus && initialFocus.isConnected ? initialFocus : node.querySelector('[data-popover-autofocus]')
        || node.querySelector('[aria-selected="true"]')
        || node.querySelector('input')
        || node.querySelector('button, [tabindex="-1"]');
      if (preferred) {
        try { preferred.focus({ preventScroll: true }); } catch { preferred.focus(); }
      }
    });
    return node;
  }

  function popoverNote(text) {
    return el('p', { class: 'popover-note', text });
  }

  function popoverOption({ title, desc = '', icon = '', selected = false, disabled = false, danger = false, tag = '', meta = '', onClick = null }) {
    const button = el('button', {
      class: `popover-option${danger ? ' is-danger' : ''}`,
      type: 'button',
      role: 'option',
      'aria-selected': String(Boolean(selected)),
      'aria-disabled': disabled ? 'true' : false,
      disabled
    },
      icon ? el('span', { class: 'option-icon' }, iconEl(icon)) : null,
      el('span', { class: 'option-copy' },
        el('span', { class: 'option-title' }, el('span', { text: title }), tag ? el('span', { class: 'option-tag', text: tag }) : null),
        desc ? el('span', { class: 'option-desc', text: desc }) : null
      ),
      meta ? el('span', { class: 'model-option-meta', text: meta }) : null,
      selected ? el('span', { class: 'option-check' }, iconEl('check')) : null
    );
    if (onClick && !disabled) button.addEventListener('click', onClick);
    return button;
  }

  function openModePopover(trigger = els.modeButton) {
    if (!trigger) return;
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': 'Mode' });
    // Two levels, not four equal choices: Chat is the umbrella, and the shape
    // inside it (direct, battle, side by side) is the choice that follows.
    const groups = [
      { title: 'Chat', ids: ['direct', 'battle', 'side-by-side'] },
      { title: 'Agent', ids: ['agent'] }
    ];
    groups.forEach((group) => {
      list.appendChild(el('div', { class: 'popover-section', text: group.title, role: 'presentation' }));
      group.ids.forEach((id) => {
        const mode = MODES.find((item) => item.id === id);
        if (!mode) return;
        list.appendChild(popoverOption({
          title: mode.title,
          desc: mode.desc,
          icon: mode.icon,
          selected: state.mode === mode.id,
          onClick: () => {
            closePopover();
            void changeMode(mode.id);
          }
        }));
      });
    });
    openPopover(trigger, {
      title: 'Mode',
      menu: true,
      role: 'listbox',
      content: list
    });
  }

  function openModalityPopover(trigger = els.modalityButton) {
    if (!trigger || state.mode === 'agent') return;
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': 'Work type' });
    modalityOptionsForMode(state.mode).forEach((modality) => {
      list.appendChild(popoverOption({
        title: modality.label,
        desc: modality.desc,
        icon: modality.icon,
        selected: state.modality === modality.id,
        onClick: () => {
          closePopover();
          void changeModality(modality.id);
        }
      }));
    });
    openPopover(trigger, {
      title: 'Work type',
      menu: true,
      role: 'listbox',
      content: list
    });
  }

  function openPermissionModePopover(trigger = els.permissionButton) {
    if (!trigger) return;
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': 'Permissions' });
    PERMISSION_MODES.forEach((mode) => {
      list.appendChild(popoverOption({
        title: mode.label,
        desc: mode.desc,
        icon: mode.icon,
        selected: state.permissionMode === mode.id,
        danger: mode.danger,
        tag: mode.danger ? 'Dangerous' : '',
        onClick: () => {
          closePopover();
          void changePermissionMode(mode.id);
        }
      }));
    });
    openPopover(trigger, {
      title: 'Permissions',
      menu: true,
      role: 'listbox',
      content: list
    });
  }

  /* ------------------------------------------------------------------------ */
  /* Chat options: everything the composer used to spell out, as icons.        */
  /* ------------------------------------------------------------------------ */
  /* The toolbar shows two buttons — this menu and Attach — and nothing else.
     Mode, work type, permissions and folder are not decorations of the message
     box: each one changes what pressing Send means, so they sit in one panel a
     click away instead of competing with the text for attention. Every pill
     carries its own tooltip; the panel stays open while you change more than
     one thing. */
  const AGENT_BACKENDS = [
    { id: 'local', label: 'Local agent', icon: 'terminal',
      title: 'Local agent — works in the folder on this computer with your tools, under the permissions below.' },
    { id: 'native', label: 'Cloud agent', icon: 'cloud',
      title: 'Cloud agent — arena.ai\u2019s own agent: cloud workspace, web search and Arena connectors.' }
  ];

  function composerMenuPill(label, { icon = '', title = '', selected = false, danger = false, disabled = false, onClick = null } = {}) {
    const button = el('button', {
      class: `cm-pill${selected ? ' is-selected' : ''}${danger ? ' is-danger' : ''}`,
      type: 'button',
      title: title || label,
      'aria-pressed': String(Boolean(selected)),
      disabled: Boolean(disabled)
    },
      icon ? el('span', { class: 'cm-pill-icon' }, iconEl(icon)) : null,
      el('span', { class: 'cm-pill-label', text: label }),
      selected ? el('span', { class: 'cm-pill-check' }, iconEl('check')) : null
    );
    if (onClick && !disabled) button.addEventListener('click', onClick);
    return button;
  }

  function composerMenuLine(label, { icon = '', detail = '', title = '', disabled = false, danger = false, onClick = null } = {}) {
    const button = el('button', {
      class: `cm-line${danger ? ' is-danger' : ''}`, type: 'button',
      title: title || (detail ? `${label} — ${detail}` : label), disabled: Boolean(disabled)
    },
      icon ? el('span', { class: 'cm-line-icon' }, iconEl(icon)) : null,
      el('span', { class: 'cm-line-copy' },
        el('span', { class: 'cm-line-label', text: label }),
        detail ? el('span', { class: 'cm-line-detail', text: detail }) : null),
      iconEl('chevron-right')
    );
    if (onClick && !disabled) button.addEventListener('click', onClick);
    return button;
  }

  function openComposerMenu(trigger = els.composerMenuButton) {
    if (!trigger) return;
    const body = el('div', { class: 'composer-menu', role: 'group', 'aria-label': 'Chat options' });
    const popover = openPopover(trigger, {
      title: 'Chat options',
      subtitle: 'Mode, work type, permissions and folder for the next message.',
      content: body,
      wide: true
    });
    if (!popover) return;

    const refresh = () => {
      body.replaceChildren(...composerMenuSections(refresh));
      positionPopover(popover, trigger);
    };
    refresh();
    return popover;
  }

  function composerMenuSections(refresh) {
    const locked = chatLocked();
    const busy = state.generating;
    const blocked = locked || busy;
    const sections = [];

    const section = (title, children, note = '') => {
      const wrap = el('section', { class: 'cm-section' }, el('p', { class: 'cm-title', text: title }));
      const holder = el('div', { class: 'cm-body' });
      children.forEach((child) => child && holder.appendChild(child));
      wrap.appendChild(holder);
      if (note) wrap.appendChild(el('p', { class: 'cm-note', text: note }));
      sections.push(wrap);
      return wrap;
    };
    const pills = (items) => {
      const row = el('div', { class: 'cm-pills' });
      items.forEach((item) => item && row.appendChild(item));
      return row;
    };

    // Mode: the shape of the answer. Agent is a row of its own below, because
    // it is not a fourth equal choice — it has a backend to pick as well.
    section('Chat', [pills(MODES.filter((mode) => mode.id !== 'agent').map((mode) => composerMenuPill(mode.label, {
      icon: mode.icon,
      title: mode.desc,
      selected: state.mode === mode.id,
      disabled: blocked,
      // Await: the panel is rebuilt from the new state, not from the old one.
      onClick: async () => { await changeMode(mode.id); refresh(); }
    })))]);

    section('Agent', [pills(AGENT_BACKENDS.map((backend) => composerMenuPill(backend.label, {
      icon: backend.icon,
      title: backend.title,
      selected: state.mode === 'agent' && state.agentKind === backend.id,
      disabled: blocked,
      onClick: async () => {
        if (state.mode !== 'agent') await changeMode('agent');
        setAgentKind(backend.id);
        refresh();
      }
    })))], state.mode === 'agent'
      ? (state.agentKind === 'local'
        ? `Answering with the local agent${state.workspace ? ` in ${workspaceName()}` : ' — pick a folder first'}.`
        : 'Answering with arena.ai\u2019s cloud agent.')
      : 'Choosing one of these switches this chat to Agent Mode.');

    if (state.mode !== 'agent') {
      const options = modalityOptionsForMode(state.mode);
      if (options.length) {
        section('Work type', [pills(options.map((modality) => composerMenuPill(modality.label, {
          icon: modality.icon,
          title: modality.desc,
          selected: state.modality === modality.id,
          disabled: blocked,
          onClick: async () => { await changeModality(modality.id); refresh(); }
        })))]);
      }
    }

    section('Permissions', [pills(PERMISSION_MODES.map((mode) => composerMenuPill(mode.short || mode.label, {
      icon: mode.icon,
      title: `${mode.label} — ${mode.desc}`,
      selected: state.permissionMode === mode.id,
      danger: Boolean(mode.danger),
      disabled: busy || state.pendingChanges > 0,
      onClick: async () => { await changePermissionMode(mode.id); refresh(); }
    })))], state.permissionMode === 'bypassPermissions'
      ? 'Bypass is on: the agent runs commands and changes files without asking.'
      : 'What the agent may do without asking you first.');

    section('Folder', [
      composerMenuLine(state.workspace ? workspaceName() : 'No folder selected', {
        icon: 'folder',
        detail: state.workspace || 'The local agent and saved code work here',
        title: state.workspace || 'No folder selected yet',
        disabled: busy,
        onClick: async () => { closePopover(); if (state.nativeReady) await chooseNativeFolder(); else openSettings('workspace'); }
      }),
      state.workspace ? composerMenuLine('Open in Explorer', {
        icon: 'external', detail: '', disabled: busy,
        onClick: async () => { closePopover(false); await openWorkspaceFolder(); }
      }) : null
    ]);

    const syncOn = state.mode === 'agent'
      ? Boolean(state.directTools)
      : Boolean(state.directTools && state.workspaceChosen);
    const optionRows = [
      el('label', { class: 'cm-switch', title: 'Workspace Sync: let chats work in the folder selected on this computer' },
        el('span', { class: 'cm-switch-copy' },
          el('span', { class: 'cm-line-label', text: 'Workspace Sync' }),
          el('span', { class: 'cm-line-detail', text: syncOn ? 'On — chats may use the folder' : 'Off — chats stay on arena.ai' })),
        el('span', { class: 'switch' },
          el('input', {
            type: 'checkbox', class: 'switch-input', checked: syncOn, disabled: busy,
            'aria-label': 'Workspace Sync',
            onchange: (event) => { setWorkspaceSync(event.target.checked); refresh(); }
          }),
          el('span', { class: 'switch-track', 'aria-hidden': 'true' })))
    ];
    if (!els.compactButton?.hidden) {
      optionRows.push(composerMenuLine('Compact chat', {
        icon: 'compress', detail: 'Continue in a fresh session with a summary',
        disabled: busy || locked,
        onClick: () => { closePopover(); confirmCompact(); }
      }));
    }
    if (state.gateway && !els.gatewayChip?.hidden) {
      optionRows.push(composerMenuLine('Agent Gateway is on', {
        icon: 'globe', detail: 'Manage the tunnel', disabled: false,
        onClick: () => { closePopover(); openSettings('gateway'); }
      }));
    }
    section('Options', optionRows);

    if (locked) {
      sections.push(el('p', { class: 'cm-note cm-note-locked', text: 'This chat keeps the mode it started with. Start a new chat to change it.' }));
    }
    return sections;
  }

  function openWorkspacePopover(trigger = els.workspaceButton) {
    if (!trigger) return;
    const path = state.workspace;
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': 'Workspace' });
    list.appendChild(popoverOption({
      title: path ? workspaceName() : 'No folder selected',
      desc: path || 'Choose a folder for Local Agent and saved code',
      icon: 'folder',
      selected: true,
      onClick: () => {
        closePopover(false);
        setWorkspacePanelOpen(true);
      }
    }));
    list.appendChild(popoverOption({
      title: 'Change folder…',
      desc: state.nativeReady ? 'Pick a folder on this computer' : 'Enter the path in Settings',
      icon: 'folder',
      onClick: async () => {
        closePopover(false);
        if (!state.nativeReady) {
          openSettings('workspace');
          return;
        }
        await chooseNativeFolder();
      }
    }));
    if (path) {
      list.appendChild(popoverOption({
        title: 'Open in Explorer',
        icon: 'external',
        onClick: async () => { closePopover(false); await openWorkspaceFolder(); }
      }));
      list.appendChild(popoverOption({
        title: 'Show files',
        icon: 'panel-right',
        onClick: () => { closePopover(false); setWorkspacePanelOpen(true); }
      }));
    }
    if (state.workspaceChosen) {
      list.appendChild(popoverOption({
        title: 'Use default folder',
        desc: 'ArenaProjects\\workspace',
        icon: 'x',
        onClick: async () => {
          closePopover(false);
          try {
            const result = await API.clearWorkspace();
            state.workspace = normalizeWorkspace(result?.workspace ?? result?.path) || state.workspace;
            state.workspaceChosen = false;
            state.workspacePanelRoot = '';
            resetWorkspaceTree();
            updateComposerState();
            renderWorkspaceTreePanel();
            toast('Back to the default workspace folder.', 'success');
          } catch (error) {
            toast(errorMessage(error), 'error', 'Folder not changed');
          }
        }
      }));
    }
    openPopover(trigger, {
      title: 'Workspace',
      menu: true,
      role: 'listbox',
      content: list
    });
  }

  function openModelPicker(lane = 'a', trigger = null) {
    if (!allowsManualModels()) {
      const message = state.mode === 'agent'
        ? "In Agent, the model is not selected and does not appear: it is assigned automatically."
        : state.modality === 'video'
          ? "Video is only available in Battle; both models are selected by Arena."
          : "In Battle, both models are selected by Arena.";
      toast(message, 'info', "Manual selection is disabled");
      return;
    }
    const anchor = trigger || els.modelsNavBtn || els.modeButton;
    if (!anchor) return;
    const effectiveLane = lane === 'b' && state.mode === 'side-by-side' ? 'b' : 'a';
    const content = el('div');
    const searchWrap = el('div', { class: 'popover-search' }, iconEl('search'));
    const searchInput = el('input', { type: 'search', placeholder: "Find a model or laboratory", autocomplete: 'off', 'aria-label': "Search for a model" });
    searchWrap.appendChild(searchInput);
    content.appendChild(searchWrap);
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': `Model ${effectiveLane.toUpperCase()}` });
    content.appendChild(list);
    const footer = el('div', { class: 'popover-foot' },
      el('span', { class: 'modal-copy', style: { margin: '0', fontSize: '9px' }, text: `${modalityMeta(state.modality).label} · ${state.models.length} of models` }),
      (state.mode === 'direct' && state.modality === 'text' && effectiveLane === 'a')
        ? el('button', {
          class: 'button button-small button-quiet', type: 'button',
          onclick: () => { closePopover(); openFanoutPicker(anchor); }
        }, iconEl('layers'), el('span', { text: state.fanoutModels?.length >= 2 ? `Fan-out: ${state.fanoutModels.length}` : 'Ask several' }))
        : null,
      el('button', {
        class: 'icon-button tiny', type: 'button', 'aria-label': "Update the model list",
        onclick: async () => { await loadModels(state.modality, { force: true }); renderModelOptions(); }
      }, iconEl('refresh'))
    );
    content.appendChild(footer);

    const renderModelOptions = () => {
      clear(list);
      if (state.modelsLoading && !state.models.length) {
        list.appendChild(el('div', { class: 'popover-empty' }, el('span', { class: 'loading-block' }, el('span', { class: 'mini-spinner' }), el('span', { text: "Loading models…" }))));
        return;
      }
      if (state.modelsError) {
        list.appendChild(el('div', { class: 'popover-empty' }, el('span', { text: state.modelsError })));
        list.appendChild(el('div', { class: 'popover-foot' }, el('span'), el('button', {
          class: 'button button-small button-quiet', type: 'button',
          onclick: () => { void loadModels(state.modality, { force: true }).then(renderModelOptions); }
        }, iconEl('refresh'), el('span', { text: "Retry" }))));
        return;
      }
      const query = searchInput.value.trim().toLocaleLowerCase('en');
      ensureElo(renderModelOptions);
      const favs = modelPinSet(FAV_KEY);
      const hidden = modelPinSet(HIDDEN_KEY);
      const filtered = state.models.filter((model) => {
        if (hidden.has(String(model.id)) && !query) return false;
        if (!query) return true;
        return `${model.name} ${model.org} ${model.capabilities.join(' ')}`.toLocaleLowerCase('en').includes(query);
      }).sort((x, y) => (favs.has(String(y.id)) ? 1 : 0) - (favs.has(String(x.id)) ? 1 : 0));
      if (!filtered.length) {
        list.appendChild(el('div', { class: 'popover-empty', text: state.models.length ? "Models not found." : "The server did not return models for this type." }));
        return;
      }
      const selectedId = effectiveLane === 'b' ? state.modelBId : state.modelAId;
      const recentModels = (query ? [] : recentModelIds())
        .map((id) => filtered.find((model) => String(model.id) === id))
        .filter(Boolean)
        .slice(0, 4);
      const recentSet = new Set(recentModels.map((model) => String(model.id)));
      const sequence = recentModels.length
        ? [...recentModels, ...filtered.filter((model) => !recentSet.has(String(model.id)))]
        : filtered;
      sequence.forEach((model, index) => {
        if (recentModels.length && index === 0) list.appendChild(el('div', { class: 'popover-group-label', text: 'Recent' }));
        if (recentModels.length && index === recentModels.length) list.appendChild(el('div', { class: 'popover-group-label', text: 'All models' }));
        const conflicts = effectiveLane === 'b' && String(state.modelAId) === String(model.id);
        const capsText = (model.capabilities || []).join(' ').toLowerCase();
        const elo = eloForModel(model);
        const meta = [
          elo?.rank ? `Elo #${elo.rank}` : (model.rank ? `#${model.rank}` : ''),
          elo?.score ? `${elo.score}` : (model.arena_score ? `rating ${model.arena_score}` : ''),
          (capsText.includes('vision') || capsText.includes('image')) ? 'Vision' : '',
          (capsText.includes('pdf') || capsText.includes('document')) ? 'Docs' : '',
          /(mini|flash|lite|haiku|nano)/i.test(`${model.id || ''} ${model.name || ''}`) ? 'Light (saves daily balance)' : ''
        ].filter(Boolean).join(' · ');
        const option = popoverOption({
          title: model.name,
          desc: `${model.org || "Unknown organization"}${model.capabilities.length ? ` · ${model.capabilities.slice(0, 3).join(' · ')}` : ''}`,
          icon: '',
          selected: String(selectedId) === String(model.id),
          disabled: conflicts,
          tag: conflicts ? "in A" : '',
          meta,
          onClick: () => {
            if (selectModel(effectiveLane, model.id)) closePopover();
          }
        });
        const logoNode = modelIconNode(model);
        const iconWrap = logoNode
          ? el('span', { class: 'model-avatar has-logo' }, logoNode)
          : el('span', {
            class: 'model-avatar',
            style: { background: safeColor(model.color) || '#bdd3c1', color: '#1b231e' },
            text: initialLetters(model.name)
          });
        option.insertBefore(iconWrap, option.firstChild);
        const isFav = favs.has(String(model.id));
        const isHidden = hidden.has(String(model.id));
        const favBtn = el('span', { class: `model-pin${isFav ? ' is-fav' : ''}`, role: 'button', tabindex: '-1',
          title: isFav ? 'Unpin from top' : 'Pin to top', 'aria-label': isFav ? 'Unpin model' : 'Pin model' },
          el('span', { class: 'model-pin-glyph', text: isFav ? '★' : '☆' }));
        favBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleModelPin(FAV_KEY, model.id); renderModelOptions(); });
        option.appendChild(favBtn);
        const hideBtn = el('span', { class: 'model-pin', role: 'button', tabindex: '-1',
          title: isHidden ? 'Unhide model' : 'Hide model', 'aria-label': isHidden ? 'Unhide model' : 'Hide model' }, iconEl('x'));
        hideBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleModelPin(HIDDEN_KEY, model.id); renderModelOptions(); });
        option.appendChild(hideBtn);
        if (isHidden) option.classList.add('is-hidden-model');
        list.appendChild(option);
      });
      if (hidden.size && !query) {
        list.appendChild(el('div', { class: 'popover-empty', text: `${hidden.size} hidden model(s) — type to search to see and unhide them.` }));
      }
    };

    searchInput.addEventListener('input', renderModelOptions);
    renderModelOptions();

    openPopover(anchor, {
      title: `Select a model ${effectiveLane.toUpperCase()}`,
      subtitle: `${modeMeta(state.mode).label} · ${modalityMeta(state.modality).label} · only available in Arena`,
      content,
      wide: true,
      initialFocus: searchInput
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Modal infrastructure                                                  */
  /* ---------------------------------------------------------------------- */
  function openFanoutPicker(anchor) {
    const content = el('div');
    content.appendChild(el('p', { class: 'modal-copy', text: 'The same prompt goes to 2–4 models; you get every answer plus a short comparison. Each answer also stays as its own sub-chat.' }));
    const searchWrap = el('div', { class: 'popover-search' }, iconEl('search'));
    const searchInput = el('input', { type: 'search', placeholder: 'Find a model', autocomplete: 'off', 'aria-label': 'Search for a model' });
    searchWrap.appendChild(searchInput);
    content.appendChild(searchWrap);
    const list = el('div', { class: 'popover-list', role: 'listbox', 'aria-label': 'Fan-out models' });
    content.appendChild(list);
    const chosen = new Set((state.fanoutModels || []).map(String));
    if (!chosen.size) [...modelPinSet(FAV_KEY)].slice(0, 3).forEach((id) => chosen.add(id));
    const useBtn = el('button', { class: 'button button-small', type: 'button' }, el('span', { text: 'Use these' }));
    const clearBtn = el('button', { class: 'button button-small button-quiet', type: 'button' }, el('span', { text: 'Turn off' }));
    content.appendChild(el('div', { class: 'popover-foot' }, clearBtn, useBtn));
    const renderFanout = () => {
      clear(list);
      const q = searchInput.value.trim().toLocaleLowerCase('en');
      state.models.filter((m) => !q || `${m.name} ${m.org}`.toLocaleLowerCase('en').includes(q)).forEach((model) => {
        const on = chosen.has(String(model.id));
        list.appendChild(popoverOption({
          title: model.name, desc: model.org || '', icon: '', selected: on,
          onClick: () => {
            if (on) chosen.delete(String(model.id));
            else if (chosen.size >= 4) { toast('At most 4 models per fan-out.', 'warning'); return; }
            else chosen.add(String(model.id));
            renderFanout();
          }
        }));
      });
      useBtn.disabled = chosen.size < 2;
      const label = useBtn.querySelector('span');
      if (label) label.textContent = chosen.size >= 2 ? `Use ${chosen.size} models` : 'Use these';
    };
    searchInput.addEventListener('input', renderFanout);
    renderFanout();
    useBtn.addEventListener('click', () => {
      state.fanoutModels = [...chosen];
      if (state.fanoutModels.length && !state.modelAId) selectModel('a', state.fanoutModels[0]);
      closePopover();
      renderComposerContext();
      toast(`Fan-out on: the next prompt goes to ${state.fanoutModels.length} models.`, 'info', 'Ask several models');
      focusComposer();
    });
    clearBtn.addEventListener('click', () => {
      state.fanoutModels = [];
      closePopover();
      renderComposerContext();
    });
    openPopover(anchor || els.modeButton, { title: 'Ask several models', subtitle: 'Direct · Text', content, wide: true, initialFocus: searchInput });
  }

  let modalSequence = 0;

  function mountModal({ title, subtitle = '', size = '', content, actions = [], onMount, onClose }) {
    closePopover(false);
    if (state.activeModal) state.activeModal.close();
    const previousFocus = document.activeElement;
    const titleId = `modal-title-${++modalSequence}`;
    modalSequence += 1;
    const descriptionId = subtitle ? `modal-desc-${modalSequence}` : '';
    const backdrop = el('div', { class: 'modal-backdrop' });
    const modal = el('section', {
      class: `modal${size ? ` modal-${size}` : ''}`,
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': titleId,
      'aria-describedby': descriptionId || false
    });
    const closeButton = el('button', { class: 'icon-button', type: 'button', 'aria-label': "Close window" }, iconEl('x'));
    const head = el('header', { class: 'modal-head' },
      el('div', {}, el('h2', { id: titleId, text: title }), subtitle ? el('p', { id: descriptionId, text: subtitle }) : null),
      closeButton
    );
    const body = el('div', { class: 'modal-body' });
    if (content) append(body, content);
    const footer = el('footer', { class: 'modal-foot' });
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      document.removeEventListener('keydown', onKey);
      backdrop.remove();
      if (state.activeModal?.backdrop === backdrop) state.activeModal = null;
      try { onClose?.(); } catch { /* noop */ }
      if (previousFocus && previousFocus.isConnected) {
        try { previousFocus.focus({ preventScroll: true }); } catch { previousFocus.focus(); }
      }
    };
    const onKey = (event) => {
      if (!backdrop.isConnected) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      trapFocus(event, modal);
    };
    closeButton.addEventListener('click', close);

    actions.forEach((action) => {
      const button = el('button', {
        class: `button${action.variant === 'danger' ? ' button-danger' : action.variant === 'primary' ? '' : ' button-quiet'}`,
        type: 'button',
        'data-autofocus': action.autofocus ? '' : false,
        disabled: Boolean(action.disabled)
      }, action.icon ? iconEl(action.icon) : null, el('span', { text: action.label }));
      button.addEventListener('click', async () => {
        if (action.onClick) {
          button.disabled = true;
          let keepOpen = false;
          try {
            const result = await action.onClick({ close, modal, body, footer, button });
            keepOpen = result === false;
          } catch (error) {
            keepOpen = true;
            toast(errorMessage(error), 'error');
          }
          if (closed) return;
          if (!keepOpen) close();
          else button.disabled = false;
        } else {
          close();
        }
      });
      footer.appendChild(button);
    });
    if (actions.length) modal.appendChild(head); else modal.appendChild(head);
    modal.append(body);
    if (actions.length) modal.appendChild(footer);
    backdrop.appendChild(modal);
    backdrop.addEventListener('pointerdown', (event) => {
      if (event.target === backdrop) close();
    });
    els.modalRoot.replaceChildren(backdrop);
    document.addEventListener('keydown', onKey);
    state.activeModal = { close, backdrop, modal, body };
    window.requestAnimationFrame(() => {
      if (!backdrop.isConnected) return;
      const preferred = modal.querySelector('[data-autofocus]') || modal.querySelector('button, input, textarea, [tabindex]:not([tabindex="-1"])');
      if (preferred) {
        try { preferred.focus({ preventScroll: true }); } catch { preferred.focus(); }
      }
      onMount?.({ close, modal, body, footer });
    });
    return { close, modal, body, footer };
  }

  /* ---------------------------------------------------------------------- */
  /* Engine / native pane                                                  */
  /* ---------------------------------------------------------------------- */
  function syncDock(notify = false) {
    if (!els.app || !els.dockPane) return;
    // In the Windows app Arena is a separate native window, not a docked pane.
    const showDemoPane = state.demo && state.engineOpen;
    els.app.dataset.dock = showDemoPane ? 'open' : 'closed';
    els.app.style.setProperty('--dock-share', String(state.dockShare || 0.42));
    els.dockPane.hidden = !showDemoPane;
    if (state.engineOpen) {
      if (els.dockSubtitle) els.dockSubtitle.textContent = state.demo ? "Demo panel" : (state.desktop ? "Native application window" : "Panel opened via API");
      if (els.dockDescription) {
        els.dockDescription.textContent = state.demo
          ? "In demo mode, the native arena.ai page does not open. Only the reserved area layout is shown; the real panel appears through the existing backend."
          : "The arena.ai page is opened in a separate application panel. Login and challenge verification are performed manually. The interface does not bypass protection and does not save passwords or tokens.";
      }
      if (els.dockNote) els.dockNote.textContent = state.demo
        ? "Demo: no request to arena.ai is executed."
        : "The content of the panel is displayed by the backend application, not this interface.";
      if (els.dockTitle) els.dockTitle.textContent = state.demo ? "arena.ai Panel · demo" : "arena.ai Panel";
    }
    if (notify) announce(state.engineOpen ? "arena.ai Panel is open." : "arena.ai Panel is hidden.");
  }

  async function showEngine() {
    if (state.engineBusy) return;
    // Opening Arena while signed out is also a login attempt: poll until it
    // succeeds so a user who signs in from this button is not left disconnected.
    if (!state.authenticated && !state.loginPending) return startLogin();
    if (state.serverError) {
      toast("Local server is unavailable. Panel is not open.", 'error', "No connection");
      return;
    }
    state.engineBusy = true;
    try {
      const result = await API.engineShow();
      state.engineKnown = true;
      state.engineOpen = result?.open ?? result?.engine ?? result?.shown ?? true;
      const engine = normalizeEngine(result?.engine);
      if (engine.known) state.dockShare = clamp(engine.share, 0.24, 0.7);
      syncDock(true);
      toast(state.demo ? "Demo: panel area is open. Real page is not loaded." : "arena.ai page is open in a separate window.", 'success', "Panel");
      void loadStatus({ quiet: true });
    } catch (error) {
      toast(errorMessage(error), 'error', "Panel is not open");
    } finally {
      state.engineBusy = false;
    }
  }

  async function hideEngine() {
    if (state.engineBusy) return;
    if (state.serverError) {
      toast("Local server is unavailable. Cannot hide the native panel.", 'error');
      return;
    }
    state.engineBusy = true;
    try {
      const result = await API.engineHide();
      state.engineKnown = true;
      state.engineOpen = Boolean(result?.open ?? result?.engine ?? false);
      syncDock(true);
      toast("arena.ai Panel is hidden.", 'success');
    } catch (error) {
      toast(errorMessage(error), 'error', "The panel is not hidden");
    } finally {
      state.engineBusy = false;
    }
  }

  window.arenaPane = function arenaPane(open, share) {
    state.engineOpen = Boolean(open);
    state.engineKnown = true;
    const parsedShare = Number(share);
    const normalizedShare = parsedShare > 1 ? parsedShare / 100 : parsedShare;
    if (Number.isFinite(normalizedShare) && normalizedShare > 0) state.dockShare = clamp(normalizedShare, 0.24, 0.7);
    state.dockLayoutCalled = true;
    syncDock(false);
  };

  /* ---------------------------------------------------------------------- */
  /* Workspace                                                             */
  /* ---------------------------------------------------------------------- */
  function isAbsolutePath(path) {
    if (!path) return false;
    return /^[a-zA-Z]:[\\/]/.test(path) || /^\\\\[^\\]/.test(path) || /^\//.test(path);
  }

  async function applyWorkspacePath(path) {
    const clean = String(path || '').trim();
    if (!clean) throw new ApiError("Enter the full path to the working folder.");
    if (!isAbsolutePath(clean)) throw new ApiError("Full path is required, for example C:\\Projects\\Arena.");
    if (state.serverError) throw new ApiError("Local server is unavailable.");
    const result = await API.setWorkspace(clean);
    state.workspace = normalizeWorkspace(result?.workspace ?? result?.path) || clean;
    state.workspaceChosen = true;
    state.workspacePanelRoot = '';
    resetWorkspaceTree();
    updateComposerState();
    renderWorkspaceTreePanel();
    if (state.workspacePanelOpen) void loadWorkspaceTree('.', { force: true });
    return state.workspace;
  }

  /* ---------------------------------------------------------------------- */
  /* Run on this PC                                                         */
  /* ---------------------------------------------------------------------- */
  // Code mode runs projects in arena.ai's cloud sandbox; only the files come
  // here. This installs and starts the local copy and shows where it serves.
  const runLocal = { id: null, timer: null };

  function renderRunLocal(data) {
    const box = els.workspaceRunStatus;
    if (!box) return;
    clear(box);
    box.hidden = !data;
    if (!data) return;
    const url = (data.urls || [])[0] || '';
    const log = String(data.log || '').trim().split('\n').filter((line) => line.trim());
    const title = data.running
      ? (url ? `Running at ${url}` : 'Installing packages and starting…')
      : (url || data.stoppedByUser ? 'Stopped' : 'Stopped before it started serving');
    box.append(el('p', { class: 'workspace-run-title', text: title }));
    if (!url && log.length) box.append(el('pre', { class: 'workspace-run-log', text: log.slice(data.running ? -2 : -8).join('\n') }));
    const actions = el('div', { class: 'workspace-run-actions' });
    const add = (label, run) => {
      const b = el('button', { type: 'button', class: 'button button-quiet', text: label });
      b.addEventListener('click', run);
      actions.append(b);
    };
    if (data.running && url) add('Open', () => openPreview(url, 'Dev server'));
    if (data.running) add('Stop', () => { void stopRunLocal(); });
    else add('Close', () => renderRunLocal(null));
    box.append(actions);
  }

  async function pollRunLocal() {
    window.clearTimeout(runLocal.timer);
    if (!runLocal.id) return;
    try {
      const data = await API.runLocalState(runLocal.id);
      renderRunLocal(data);
      if (data.running) runLocal.timer = window.setTimeout(pollRunLocal, (data.urls || []).length ? 8000 : 1500);
      else runLocal.id = null;
    } catch {
      runLocal.id = null;
      renderRunLocal(null);
    }
  }

  async function startRunLocal() {
    const folder = state.workspacePanelRoot || state.workspace;
    if (!folder) { toast('Choose the project folder first.', 'warning', 'Run on this PC'); return; }
    if (runLocal.id) await stopRunLocal();
    els.workspaceRunButton?.setAttribute('aria-busy', 'true');
    try {
      const data = await API.runLocal(folder);
      runLocal.id = data.id;
      renderRunLocal(data);
      toast(`${data.cmd} in ${data.folder}`, 'info', 'Running on this PC');
      runLocal.timer = window.setTimeout(pollRunLocal, 1500);
    } catch (error) {
      toast(errorMessage(error), 'error', 'Could not run the project');
    } finally {
      els.workspaceRunButton?.removeAttribute('aria-busy');
    }
  }

  async function stopRunLocal() {
    const id = runLocal.id;
    if (!id) return;
    window.clearTimeout(runLocal.timer);
    runLocal.id = null;
    try { renderRunLocal({ ...(await API.stopRunLocal(id)), stoppedByUser: true }); } catch { renderRunLocal(null); }
  }

  async function chooseNativeFolder() {
    if (!state.nativeReady) {
      openSettings('workspace');
      toast("Native dialog pywebview is not available yet. Enter the full path manually.", 'info');
      return null;
    }
    if (!window.pywebview?.api?.choose_folder) {
      openSettings('workspace');
      toast("Method choose_folder is unavailable. Enter the path manually.", 'warning');
      return null;
    }
    try {
      const chosen = await window.pywebview.api.choose_folder();
      const path = typeof chosen === 'string' ? chosen : (chosen?.path ?? chosen?.folder ?? chosen?.directory ?? '');
      if (!path) {
        if (chosen?.error) toast(String(chosen.error), 'error', "No folder selected");
        return null;
      }
      const saved = await applyWorkspacePath(path);
      toast(saved, 'success', "Working folder saved");
      state.settingsRerender?.();
      return saved;
    } catch (error) {
      toast(errorMessage(error), 'error', "Working folder not changed");
      return null;
    }
  }

  async function openWorkspaceFolder(path = state.workspace) {
    if (state.demo) {
      toast("In the demo, the folder cannot be opened. Only the path is shown.", 'info', "Demo mode");
      return;
    }
    if (!path) {
      openSettings('workspace');
      return;
    }
    try {
      await API.openWorkspace(path);
      toast("Request to open folder sent to local server.", 'success');
    } catch (error) {
      toast(errorMessage(error), 'error', "Folder not opened");
    }
  }

  async function saveWorkspaceLane(lane) {
    if (!state.activeChatId) {
      toast("No active chat to save.", 'warning');
      return null;
    }
    if (state.demo) {
      const result = await API.saveWorkspace(state.activeChatId, lane);
      toast(`${result.path || "Demo path"}`, 'success', "Demo: save simulated");
      return result;
    }
    try {
      const result = await API.saveWorkspace(state.activeChatId, lane);
      toast(result?.path ? String(result.path) : "Server confirmed save.", 'success', "Saved to working folder");
      return result;
    } catch (error) {
      toast(errorMessage(error), 'error', "Server refused to save");
      return null;
    }
  }

  async function downloadWorkspaceZip() {
    if (state.demo) {
      toast("In demo, server ZIP export is not created. This is not a real working folder.", 'info', "Demo mode");
      return;
    }
    if (!state.workspace) {
      toast("First, select a working folder in the settings.", 'warning');
      openSettings('workspace');
      return;
    }
    try {
      const blob = await API.workspaceZip(state.workspace);
      downloadBlob(blob, `${baseName(state.workspace) || 'workspace'}.zip`);
      toast("ZIP archive of the working folder received from the local server.", 'success');
    } catch (error) {
      toast(errorMessage(error), 'error', "ZIP not created");
    }
  }

  async function saveMediaFromEvent(url, button) {
    if (!url) return;
    if (button) button.disabled = true;
    try {
      const result = await API.saveMedia(url);
      toast(result?.path ? String(result.path) : "Server saved media.", 'success', "Media saved");
    } catch (error) {
      toast(errorMessage(error), 'error', "Media not saved");
    } finally {
      if (button) button.disabled = false;
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Login, logout (no credentials are requested or stored)                 */
  /* ---------------------------------------------------------------------- */
  async function startLogin({ addAccount = false } = {}) {
    if (state.loginPending) return;
    if (state.serverError || state.networkOffline) {
      toast("Local server unavailable. Login impossible.", 'error', "No connection");
      return;
    }
    state.loginPending = true;
    updateConnectionUI();
    updateAccountUI();
    state.settingsRerender?.();
    const previousAccountId = addAccount ? state.currentAccountId : null;
    try {
      const loginResult = addAccount ? await API.addAccount() : await API.login();
      if (loginResult?.challenge) {
        state.challenge = { message: textValue(loginResult.challenge) || "Login requires manual verification in the arena.ai panel." };
        renderChallenge();
        toast("Login requires manual verification. Open the arena.ai panel; automatic bypass is not performed.", 'warning', 'Challenge');
      }
      toast("Log in in the opened Arena window. After confirmation, it will close automatically.", 'info', "Arena Login");
      let completed = false;
      for (let attempt = 0; attempt < 600; attempt += 1) {
        await sleep(1000);
        if (!state.loginPending) return;
        const result = await API.loginPoll();
        if (result?.cancelled || result?.error) {
          throw new ApiError(extractError(result, 0));
        }
        const done = result?.done === true || result?.authenticated === true || result?.status === 'done';
        if (done) { completed = true; break; }
      }
      if (!completed && !state.demo) await API.engineHide().catch(() => {});
      await loadStatus({ quiet: true });
      if (state.authenticated) {
        toast(state.email || "Login completed.", 'success', "Arena connected");
        await Promise.allSettled([loadChats({ silent: true }), loadModels(state.modality), loadPermissions({ quiet: true }), refreshWorkspacePanel({ force: true })]);
        announce("Login completed.");
      } else {
        toast(completed ? "Backend finished login, but status has not updated yet. Check the state." : "Login timeout expired. Please try again.", 'warning');
      }
    } catch (error) {
      toast(errorMessage(error), 'error', "Not signed in");
      if (addAccount && previousAccountId) {
        // The Arena window was switched to a guest for the new sign-in;
        // restore the previous account into it.
        await API.switchAccount(previousAccountId).catch(() => undefined);
      }
    } finally {
      state.loginPending = false;
      updateConnectionUI();
      updateAccountUI();
      updateComposerState();
      state.settingsRerender?.();
      void loadStatus({ quiet: true });
    }
  }

  // One account action at a time (switch, sign-out, cookie import). Its
  // current step shows in the account menu, the settings panel and the
  // sidebar, so the user sees the work instead of a button that does nothing.
  function setAccountTask(task) {
    state.accountTask = task;
    if (task) announce(task.error || task.step || '');
    updateAccountUI();
    activePopover?.node?.querySelector('.account-menu')?._rerender?.();
    state.settingsRerender?.();
  }

  function finishAccountTask(task, ms = 5000) {
    setAccountTask(task);
    window.setTimeout(() => { if (state.accountTask === task) setAccountTask(null); }, ms);
  }

  function accountTaskVisible() {
    return Boolean(activePopover?.node?.querySelector('.account-menu') || state.settingsRerender);
  }

  // Errors show next to the action when the menu or settings are open, else as a toast.
  function failAccountTask(kind, message, title, extra = {}) {
    if (accountTaskVisible()) finishAccountTask({ kind, error: message, ...extra }, 12000);
    else { setAccountTask(null); toast(message, 'error', title); }
  }

  function accountTaskLine(task) {
    const line = el('div', { class: `account-task${task?.error ? ' is-error' : task?.done ? ' is-done' : ''}`, role: task?.error ? 'alert' : 'status' });
    if (!task) { line.hidden = true; return line; }
    line.append(
      task.error ? iconEl('alert') : task.done ? iconEl('check') : el('span', { class: 'loading-spinner', 'aria-hidden': 'true' }),
      el('span', { text: task.error || task.step || '' }));
    return line;
  }

  function accountTaskRunning(kind = null) {
    const task = state.accountTask;
    return Boolean(task && !task.error && !task.done && (!kind || task.kind === kind));
  }

  async function loginWithCookie(cookie) {
    if (state.demo || state.loginPending || state.accountBusy || !cookie.trim()) return;
    state.loginPending = true;
    state.accountBusy = true;
    updateConnectionUI();
    updateComposerState();
    const step = (text) => setAccountTask({ kind: 'cookie', step: text });
    try {
      step('Checking the cookies with arena.ai…');
      const result = await API.loginCookie(cookie);
      if (!result?.ok) throw new ApiError(extractError(result, 0));
      step('Loading your account…');
      await loadStatus({ quiet: true });
      if (!state.authenticated) throw new ApiError("Session imported, but the server has not confirmed the account. Check cookies and connection.");
      step('Loading chats and models…');
      await Promise.allSettled([loadChats({ silent: true }), loadModels(state.modality), loadPermissions({ quiet: true }), refreshWorkspacePanel({ force: true })]);
      finishAccountTask({ kind: 'cookie', done: true, step: `Signed in as ${state.email || 'your arena.ai account'}` });
      if (!accountTaskVisible()) toast(state.email || "Login completed.", 'success', "Arena connected");
    } catch (error) {
      failAccountTask('cookie', errorMessage(error), "Login via cookies not performed");
    } finally {
      state.loginPending = false;
      state.accountBusy = false;
      updateConnectionUI();
      updateAccountUI();
      updateComposerState();
      state.settingsRerender?.();
    }
  }


  function accountInitials(account) {
    const source = String(account?.name || account?.email || 'A').trim();
    const parts = source.split(/[\s@._-]+/).filter(Boolean);
    return ((parts[0]?.[0] || 'A') + (parts[1]?.[0] || '')).toUpperCase();
  }

  function accountAvatarEl(account) {
    const node = el('span', { class: 'avatar account-menu-avatar', 'aria-hidden': 'true' });
    if (account?.expired) {
      node.classList.add('is-expired');
      node.textContent = '?';
      return node;
    }
    const url = account?.avatar_url ? safeUrl(account.avatar_url, { image: true }) : '';
    if (url) node.appendChild(el('img', { src: url, alt: '', referrerpolicy: 'no-referrer' }));
    else node.textContent = accountInitials(account);
    return node;
  }

  function resetForAccountChange() {
    state.chats = [];
    state.cloudChats = [];
    state.cloudChatsError = null;
    state.messages = [];
    state.chatTurns = {};
    state.activeChatId = null;
    state.activeChatTitle = '';
    state.artifacts = [];
    state.challenge = null;
    updateArtifactCount();
    renderWelcomeState();
    updateAllUI();
  }

  async function switchToAccount(account) {
    if (!account || state.accountBusy || state.loginPending) return false;
    const label = account.email || account.name || 'the account';
    if (anyGenerationRunning() || state.remoteBusy) {
      failAccountTask('switch', 'Finish or stop the running generations before switching accounts.', 'Account not switched', { accountId: account.id });
      return false;
    }
    state.accountBusy = true;
    const step = (text) => setAccountTask({ kind: 'switch', accountId: account.id, step: text });
    try {
      step(`Switching to ${label}…`);
      const result = await API.switchAccount(account.id);
      if (Array.isArray(result?.accounts)) state.accounts = result.accounts;
      step('Loading chats and models…');
      resetForAccountChange();
      await loadStatus({ quiet: true });
      await Promise.allSettled([loadChats({ silent: true }), loadArenaChats({ silent: true }), loadModels(state.modality, { quiet: true })]);
      setAccountTask(null);
      toast(label, 'success', 'Account switched');
      return true;
    } catch (error) {
      const payload = error?.payload;
      if (Array.isArray(payload?.accounts)) state.accounts = payload.accounts;
      await loadStatus({ quiet: true }).catch(() => undefined);
      failAccountTask('switch', errorMessage(error), payload?.expired ? 'Session expired — sign in again' : 'Account not switched', { accountId: account.id });
      return false;
    } finally {
      state.accountBusy = false;
      updateAccountUI();
    }
  }

  async function removeSavedAccount(account) {
    if (!account || state.accountBusy) return;
    state.accountBusy = true;
    try {
      const result = await API.removeAccount(account.id);
      if (Array.isArray(result?.accounts)) state.accounts = result.accounts;
      toast('The saved session for this account was removed from this app.', 'success', 'Account removed');
    } catch (error) {
      toast(errorMessage(error), 'error', 'Account not removed');
    } finally {
      state.accountBusy = false;
    }
  }

  function buildAccountMenu() {
    const content = el('div', { class: 'account-menu' });
    let addOpen = false;
    const render = () => {
      const hadFocus = content.contains(document.activeElement);
      clear(content);
      const accounts = Array.isArray(state.accounts) ? state.accounts : [];
      const task = state.accountTask;
      const busy = Boolean(state.accountBusy || state.loginPending || accountTaskRunning());
      const current = accounts.find((a) => a.current)
        || (state.authenticated ? { email: state.email, name: state.fullName, avatar_url: state.avatarUrl } : null);
      const action = (id, icon, label, run, { danger = false, expanded = null } = {}) => el('button', {
        class: `account-menu-action${danger ? ' is-danger' : ''}`, type: 'button', 'data-popover-action': id,
        disabled: busy, 'aria-expanded': expanded === null ? null : String(expanded), onclick: run
      }, iconEl(icon), el('span', { text: label }));

      // Who is signed in now: the one thing this menu must answer at a glance.
      const named = Boolean(current?.name && current.name !== current.email);
      content.appendChild(el('div', { class: 'account-menu-current' },
        current ? accountAvatarEl(current)
          : el('span', { class: 'avatar account-menu-avatar is-anonymous', 'aria-hidden': 'true' }, iconEl('user')),
        el('span', { class: 'account-menu-copy' },
          el('span', { class: 'account-menu-email', text: current ? (named ? current.name : (current.email || 'arena.ai account')) : 'Not signed in' }),
          el('span', { class: `account-menu-sub${current?.expired ? ' is-warn' : ''}`, text: !current ? 'Sign in to chat on arena.ai'
            : current.expired ? 'Session expired' : (named ? current.email : 'Signed in') }))));
      if (task && task.kind !== 'cookie') content.appendChild(accountTaskLine(task));
      if (current?.expired) {
        content.appendChild(action('account-reauth', 'login', 'Sign in again', () => { closePopover(false); void startLogin(); }));
      }

      const others = accounts.filter((a) => !a.current);
      if (others.length) {
        content.appendChild(el('p', { class: 'account-menu-label', text: 'Switch to' }));
        others.forEach((account) => {
          const label = account.email || account.name || 'arena.ai account';
          const switching = task?.kind === 'switch' && task.accountId === account.id && accountTaskRunning();
          const row = el('div', { class: `account-menu-row${account.expired ? ' is-expired' : ''}${switching ? ' is-busy' : ''}` },
            el('button', {
              class: 'account-menu-main', type: 'button', disabled: busy,
              'data-popover-action': account.expired ? 'account-expired' : 'account-switch', 'data-account-id': account.id || '',
              'aria-label': account.expired ? `Sign in again to ${label}` : `Switch to ${label}`,
              onclick: () => {
                if (account.expired) { closePopover(false); void startLogin({ addAccount: true }); return; }
                void switchToAccount(account).then((ok) => { if (ok) closePopover(false); });
              }
            },
              accountAvatarEl(account),
              el('span', { class: 'account-menu-copy' },
                el('span', { class: 'account-menu-email', text: label }),
                el('span', { class: 'account-menu-sub', text: switching ? 'Switching…'
                  : account.expired ? 'Session expired · sign in again'
                    : (account.name && account.name !== account.email ? account.name : 'Saved on this computer') })),
              switching ? el('span', { class: 'loading-spinner', 'aria-hidden': 'true' }) : null));
          if (!busy) {
            row.appendChild(el('button', {
              class: 'account-menu-remove', type: 'button', 'aria-label': `Forget ${label} on this computer`,
              title: 'Forget this account on this computer',
              onclick: (event) => { event.stopPropagation(); void removeSavedAccount(account).then(render); }
            }, iconEl('x')));
          }
          content.appendChild(row);
        });
      }

      content.appendChild(el('div', { class: 'account-menu-divider', role: 'separator' }));
      const methods = el('div', { class: 'account-menu-methods', hidden: !addOpen },
        action('account-add-window', 'login', 'Sign-in window', () => { closePopover(false); void startLogin({ addAccount: accounts.length > 0 }); }),
        action('account-add-cookies', 'copy', 'Paste cookies', () => {
          closePopover(false);
          openSettings('connection');
          window.setTimeout(() => {
            const field = document.querySelector('.cookie-field');
            if (field) { field.focus(); field.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
          }, 80);
        }));
      content.appendChild(action('account-add', current ? 'plus' : 'login', current ? 'Add another account' : 'Sign in', (event) => {
        addOpen = !addOpen;
        methods.hidden = !addOpen;
        event.currentTarget.setAttribute('aria-expanded', String(addOpen));
      }, { expanded: addOpen }));
      content.appendChild(methods);
      content.appendChild(el('div', { class: 'account-menu-divider', role: 'separator' }));
      content.appendChild(action('account-settings', 'settings', 'Settings', () => { closePopover(false); openSettings('connection'); }));
      content.appendChild(action('account-help', 'help', 'Help & shortcuts (F1)', () => { closePopover(false); document.querySelector('[data-action="help"]')?.click(); }));
      if (current) {
        content.appendChild(action('account-signout', 'logout', accountTaskRunning('signout') ? 'Signing out…' : 'Sign out', () => {
          void logoutAccount().then((ok) => { if (ok) closePopover(false); });
        }, { danger: true }));
      }
      if (hadFocus && !content.contains(document.activeElement)) {
        content.querySelector('[data-popover-action]:not([disabled])')?.focus();
      }
    };
    render();
    content._rerender = render;
    return content;
  }

  function openAccountMenu() {
    if (!els.accountButton) return;
    if (activePopover && activePopover.trigger === els.accountButton) { closePopover(); return; }
    // A finished step or old error is not news the next time the menu opens.
    if (state.accountTask && !accountTaskRunning()) state.accountTask = null;
    const content = buildAccountMenu();
    openPopover(els.accountButton, { title: 'Accounts', content, menu: true, role: 'menu' });
    void API.getAccounts?.().then((result) => {
      if (Array.isArray(result?.accounts)) {
        state.accounts = result.accounts;
        state.currentAccountId = result.current_id || state.currentAccountId;
        if (content.isConnected) content._rerender?.();
      }
    }).catch(() => undefined);
  }

  async function logoutAccount() {
    if (state.loginPending || state.accountBusy) return false;
    if (anyGenerationRunning() || state.remoteBusy) {
      failAccountTask('signout', 'Finish or stop the running generations before signing out.', 'Not signed out');
      return false;
    }
    state.accountBusy = true;
    const step = (text) => setAccountTask({ kind: 'signout', step: text });
    try {
      step('Signing out of arena.ai…');
      await API.logout();
      step("Clearing this computer's session…");
      state.authenticated = false;
      state.email = '';
      state.fullName = '';
      state.avatarUrl = '';
      state.modelAId = null;
      state.modelBId = null;
      state.chats = [];
      state.cloudChats = [];
      state.cloudChatsError = null;
      state.messages = [];
      state.chatTurns = {};
      state.activeChatId = null;
      state.activeChatTitle = '';
      state.artifacts = [];
      state.challenge = null;
      updateArtifactCount();
      renderWelcomeState();
      updateAllUI();
      await loadStatus({ quiet: true });
      setAccountTask(null);
      toast('The saved session on this computer was deleted.', 'success', 'Signed out');
      return true;
    } catch (error) {
      failAccountTask('signout', errorMessage(error), 'Logout not performed');
      return false;
    } finally {
      state.accountBusy = false;
      updateAccountUI();
      state.settingsRerender?.();
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Artifacts                                                             */
  /* ---------------------------------------------------------------------- */

  /* ---------------------------------------------------------------------- */
  /* Help                                                                  */
  /* ---------------------------------------------------------------------- */
  function shortcutRow(name, keys) {
    return el('div', { class: 'shortcut-row' },
      el('span', { class: 'shortcut-name', text: name }),
      el('span', { class: 'kbd' }, keys.map((key) => el('kbd', { text: key })))
    );
  }

  function openHelp() {
    const content = el('div');
    content.appendChild(el('div', { class: 'account-status-card' },
      accountAvatarNode(),
      el('div', { class: 'account-status-copy' },
        el('span', { class: 'account-status-name', text: state.authenticated ? (state.email || "Connected") : "Not signed in" }),
        el('span', { class: 'account-status-email', text: state.demo ? "Demo profile: data simulated" : (state.serverError ? "Local server is unavailable" : "Status received from local backend") })
      ),
      el('span', { class: 'status-line' }, el('span', { class: `status-dot ${state.authenticated ? 'is-online' : 'is-pending'}`, 'aria-hidden': 'true' }), el('span', { text: state.authenticated ? "login active" : "login required" }))
    ));

    content.appendChild(el('h3', { class: 'modal-section-title', text: "Keyboard" }));
    const shortcuts = el('div', { class: 'shortcut-list' },
      shortcutRow("Open this window", ['F1']),
      shortcutRow("Send message", ['Enter']),
      shortcutRow("New line", ['Shift', 'Enter']),
      shortcutRow("Search in loaded chats", ['Ctrl', 'K']),
      shortcutRow("Focus in input field", ['Ctrl', '/']),
      shortcutRow("Close menu or window", ['Esc'])
    );
    content.appendChild(shortcuts);

    content.appendChild(el('h3', { class: 'modal-section-title', text: "Demo mode" }));
    content.appendChild(el('div', { class: 'note-box is-demo' }, iconEl('flask'), el('span', {},
      el('strong', { text: "?demo=1 — local simulation only. " }),
      document.createTextNode("Models, streaming events, permissions, files, and media — are test. Requests to arena.ai are not executed. Without the demo parameter, all data and actions go through the real local Flask backend.")
    )));
    const demoButton = el('button', {
      class: 'button button-small button-quiet', type: 'button', style: { marginTop: '8px' },
      onclick: () => {
        if (state.demo) window.location.href = window.location.pathname;
        else window.location.href = `${window.location.pathname}?demo=1`;
      }
    }, iconEl('flask'), el('span', { text: state.demo ? "Exit demo" : "Open ?demo=1" }));
    content.appendChild(demoButton);

    content.appendChild(el('h3', { class: 'modal-section-title', text: "Connection status" }));
    content.appendChild(el('p', { class: 'modal-copy', text: state.demo
      ? "Demo mode. The real backend is not queried."
      : state.serverError
        ? `Local server unavailable: ${state.serverError}`
        : state.status?.session_error
          ? `Backend session error: ${textValue(state.status.session_error)}`
          : "Relative requests go to the local Flask backend. Login — in a separate window or, optionally, through cookies of your account."
    }));
    content.appendChild(el('p', { class: 'modal-copy', text: "Arena Desktop is an unofficial client and is not associated with Arena.ai. Attach files with +, by dragging them onto the message box, or by pasting. Battle votes are sent to Arena and reveal both models." }));

    mountModal({
      title: "Arena Desktop Help",
      subtitle: "Unofficial local client",
      size: 'medium',
      content,
      actions: [
        { label: "Settings", variant: 'quiet', onClick: () => { openSettings('connection'); return true; } },
        { label: "Got it", variant: 'primary', autofocus: true }
      ]
    });
  }

  /* ---------------------------------------------------------------------- */
  /* Settings                                                              */