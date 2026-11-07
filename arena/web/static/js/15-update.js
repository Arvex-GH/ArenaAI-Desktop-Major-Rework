  /* ---------------------------------------------------------------------- */
  /* Updates: one dialog that checks, downloads and installs in place.       */
  /* ---------------------------------------------------------------------- */

  function updateSeenKey() { return 'arenaDesktop.updateSeen.v2'; }

  function updateDismissed(version) {
    try { return window.localStorage.getItem(updateSeenKey()) === String(version); } catch { return false; }
  }

  function rememberUpdate(version) {
    try { window.localStorage.setItem(updateSeenKey(), String(version || '')); } catch { /* storage unavailable */ }
  }

  function releaseDateLabel(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    try {
      return new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short' }).format(date);
    } catch { return ''; }
  }

  function bytesLabel(value) {
    const size = Number(value) || 0;
    if (!size) return '';
    if (size > 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
    return `${Math.round(size / (1024 * 1024))} MB`;
  }

  function plainNotes(text) {
    return String(text || '')
      .replace(/\r/g, '')
      .replace(/^#{1,6}\s*/gm, '')
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/^\s*[-*]\s+/gm, '· ')
      .trim();
  }

  function openUpdateDialog(info) {
    const data = info || state.updateInfo || {};
    if (!data || !data.latest) return;
    if (state.activeModal) state.activeModal.close();

    const canInstall = Boolean(data.can_install);
    let job = null;
    let pollTimer = null;
    let busy = false;
    // What the bar has already shown. A percentage that drops is read as a
    // restart, and the only honest reason to restart is a new download — so the
    // bar is clamped here and reset only where a download actually begins.
    let shownPct = 0;
    // Polls are 450 ms apart and each one is an HTTP round trip: without this,
    // an older answer can land after a newer one and walk the bar backwards.
    let polling = false;

    const backdrop = el('div', { class: 'modal-backdrop' });
    const modal = el('section', {
      class: 'modal modal-update', role: 'dialog', 'aria-modal': 'true',
      'aria-labelledby': 'update-title'
    });

    const closeButton = el('button', { class: 'icon-button', type: 'button', 'aria-label': 'Close update window' }, iconEl('x'));
    const head = el('header', { class: 'modal-head' },
      el('div', {},
        el('p', { class: 'eyebrow', text: 'Arena Desktop' }),
        el('h2', { id: 'update-title', text: `Version ${data.latest} is available` })
      ),
      closeButton
    );

    const body = el('div', { class: 'modal-body update-body' });
    const versionRow = el('div', { class: 'update-versions' },
      el('span', { class: 'update-version is-current', text: data.current ? `You have ${data.current}` : 'Installed build' }),
      el('span', { class: 'update-arrow', 'aria-hidden': 'true', text: '→' }),
      el('span', { class: 'update-version is-next', text: data.latest })
    );
    body.appendChild(versionRow);

    const notesText = plainNotes(data.notes);
    const notes = el('div', { class: 'update-notes' },
      notesText ? el('pre', { class: 'update-notes-text', text: notesText }) : el('p', { class: 'update-notes-empty', text: 'This release does not include a description.' })
    );
    body.appendChild(notes);

    const progress = el('div', { class: 'update-progress', hidden: true },
      el('div', { class: 'update-progress-head' },
        el('span', { class: 'update-progress-label', text: 'Preparing' }),
        el('span', { class: 'update-progress-pct', text: '0%' })
      ),
      el('div', { class: 'update-progress-track' }, el('div', { class: 'update-progress-fill' }))
    );
    body.appendChild(progress);

    const note = el('p', { class: 'update-note' });
    body.appendChild(note);

    const error = el('p', { class: 'update-error', hidden: true });
    body.appendChild(error);

    const foot = el('footer', { class: 'modal-foot update-foot' });
    const meta = el('span', { class: 'update-meta' });
    const later = el('button', { class: 'button button-quiet', type: 'button' }, el('span', { text: 'Later' }));
    const primary = el('button', { class: 'button', type: 'button', 'data-autofocus': '' });
    foot.append(meta, el('span', { class: 'update-foot-spacer' }), later, primary);

    modal.append(head, body, foot);
    backdrop.appendChild(modal);
    els.modalRoot.replaceChildren(backdrop);

    const published = releaseDateLabel(data.published_at);
    const sizeLabel = bytesLabel(data.installer?.size);
    meta.textContent = [published ? `Released ${published}` : '', sizeLabel].filter(Boolean).join(' · ');

    function setPrimary(label, onClick) {
      primary.replaceChildren(el('span', { text: label }));
      primary.onclick = onClick;
      primary.disabled = false;
    }

    function setProgress(visible, label, pct) {
      progress.hidden = !visible;
      if (!visible) return;
      const value = Math.max(shownPct, Math.min(100, Number(pct) || 0));
      shownPct = value;
      progress.querySelector('.update-progress-label').textContent = label;
      progress.querySelector('.update-progress-pct').textContent = `${Math.round(value)}%`;
      progress.querySelector('.update-progress-fill').style.width = `${Math.max(2, value)}%`;
    }

    function resetProgress() {
      shownPct = 0;
    }

    function showError(message) {
      error.hidden = !message;
      error.textContent = message || '';
    }

    function stopPolling() {
      if (pollTimer) { window.clearInterval(pollTimer); pollTimer = null; }
    }

    function close() {
      stopPolling();
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
      if (state.activeModal?.backdrop === backdrop) state.activeModal = null;
    }

    function onKey(event) {
      if (event.key === 'Escape' && !busy) { event.preventDefault(); close(); }
    }

    function markDismissed() {
      rememberUpdate(data.latest);
      close();
      showUpdateBadge(data);
    }

    function fail(message) {
      busy = false;
      setProgress(false);
      showError(message || 'Something went wrong.');
      setPrimary(canInstall ? 'Try again' : 'Open the release page', startOrOpen);
      later.disabled = false;
    }

    async function applyUpdate() {
      busy = true;
      showError('');
      setProgress(true, 'Installing', 100);
      primary.disabled = true;
      later.disabled = true;
      note.textContent = 'Arena Desktop closes and starts again in a moment. Your chats and settings stay where they are.';
      try {
        const result = await API.updateInstall();
        if (!result?.ok) throw new Error(result?.error || 'The update could not be started.');
        setProgress(true, 'Restarting Arena Desktop', 100);
        stopPolling();
      } catch (err) {
        fail(errorMessage(err));
      }
    }

    function pollOnce() {
      if (polling) return;
      polling = true;
      API.updateProgress().then((result) => {
        job = result?.job || null;
        if (!job) return;
        if (job.phase === 'downloading') {
          const received = Number(job.received) || 0;
          const total = Number(job.total) || 0;
          const pct = total ? (received / total) * 100 : Number(job.pct) || 0;
          setProgress(true, total ? `Downloading ${bytesLabel(received)} of ${bytesLabel(total)}` : 'Downloading', pct);
        } else if (job.phase === 'ready') {
          stopPolling();
          busy = false;
          setProgress(true, 'Download complete', 100);
          note.textContent = 'The app will close, replace itself and start again. Chats and settings stay as they are.';
          later.disabled = false;
          setPrimary('Restart and update', applyUpdate);
        } else if (job.phase === 'error') {
          stopPolling();
          fail(job.error || 'The download failed.');
        }
      }).catch(() => { /* keep the last known state */ })
        .finally(() => { polling = false; });
    }

    async function startDownload() {
      busy = true;
      showError('');
      primary.disabled = true;
      later.disabled = true;
      note.textContent = 'Downloading the new build from the official release page.';
      resetProgress();
      setProgress(true, 'Starting the download', 1);
      try {
        // force: the six-hour check cache decided this dialog was worth opening,
        // but the download must be decided by the feed as it is right now.
        const result = await API.updateDownload(true);
        if (!result?.ok) throw new Error(result?.error || 'The download could not start.');
        busy = false;
        later.disabled = false;
        setPrimary('Cancel download', () => {
          API.updateCancel().catch(() => {});
          stopPolling();
          resetIdle();
        });
        pollOnce();
        pollTimer = window.setInterval(pollOnce, 450);
      } catch (err) {
        const message = errorMessage(err);
        // The feed can only answer this when the build really is current. That
        // is not a failure to retry, it is the answer: say so, and take the
        // stale "an update is available" down with it.
        if (/already on the newest|newest version|no update/i.test(message)) {
          busy = false;
          setProgress(false);
          resetProgress();
          note.textContent = 'This build is the newest release on the feed. Nothing to install.';
          showError('');
          API.updateCheck(true)
            .then((fresh) => { if (fresh) state.updateInfo = fresh; })
            .catch(() => { /* the note above stands on its own */ });
          setPrimary('Close', () => close());
          later.disabled = true;
          return;
        }
        fail(message);
      }
    }

    function resetIdle() {
      busy = false;
      resetProgress();
      setProgress(false);
      note.textContent = canInstall
        ? 'The update downloads here, then Arena Desktop closes, replaces itself and starts again. Nothing else is touched.'
        : 'This copy cannot replace itself. Download the new build from GitHub and run it over the old one.';
      setPrimary(canInstall ? 'Download and install' : 'Open release page', startOrOpen);
      later.disabled = false;
    }

    function startOrOpen() {
      if (!canInstall) {
        if (data.url) window.open(data.url, '_blank', 'noopener');
        else toast('No download link in this release.', 'warning', 'Updates');
        return;
      }
      startDownload();
    }

    // One handler for the primary button, and only one: setPrimary() owns it and
    // is called on every state change. A second listener here meant every click
    // ran twice — "Restart and update" started the update *and* began the
    // download again, which is what walked the bar back to 1% and left the app
    // quitting while a second job was rewriting the same file.
    later.addEventListener('click', markDismissed);
    closeButton.addEventListener('click', () => { if (!busy) markDismissed(); });
    backdrop.addEventListener('pointerdown', (event) => { if (event.target === backdrop && !busy) markDismissed(); });
    document.addEventListener('keydown', onKey);

    resetIdle();
    state.activeModal = { close, backdrop, modal, body };
    window.requestAnimationFrame(() => {
      try { primary.focus({ preventScroll: true }); } catch { /* noop */ }
    });
  }

  /* A quiet chip that reopens the dialog after "Later". */
  function showUpdateBadge(info) {
    const existing = document.querySelector('.update-island');
    if (existing) existing.remove();
    if (!info?.latest) return;
    const island = el('button', { class: 'update-island', type: 'button', title: `Arena Desktop ${info.latest} is available` },
      el('span', { class: 'island-dot' }),
      el('span', { text: 'Update ready' }),
      el('span', { class: 'island-ver', text: info.latest }),
      el('span', { class: 'island-close', 'aria-hidden': 'true', text: '×' })
    );
    island.addEventListener('click', (event) => {
      if (event.target.closest('.island-close')) { island.remove(); return; }
      island.remove();
      openUpdateDialog(info);
    });
    document.body.appendChild(island);
  }

  function maybeNotifyUpdate(info) {
    if (!info?.update || !info?.latest) return;
    if (state.demo && new URLSearchParams(window.location.search).get('update') !== '1') return;
    if (updateDismissed(info.latest)) { showUpdateBadge(info); return; }
    openUpdateDialog(info);
  }
