        function resetCowatchScroll() {
            const cwChips = document.getElementById('cowatch-chips-container');
            if (cwChips) cwChips.scrollTop = 0;
            const devChips = document.getElementById('cowatch-devices-chips-container');
            if (devChips) devChips.scrollTop = 0;
        }
        const workspaceCards = {
            operations: ['active-playback-card', 'card-activity'],
            trackers: ['card-server-config', 'card-ecosystem', 'card-multi-tracker', 'card-reconciliation'],
            automation: ['card-cowatch', 'card-arr-bridge'],
            analytics: ['card-analytics'],
            diagnostics: ['card-backup']
        };
        function switchWorkspace(name, moveFocus = false) {
            if (!workspaceCards[name]) return;
            document.querySelectorAll('.workspace-tab').forEach((tab) => {
                const selected = tab.id === `tab-${name}`;
                tab.setAttribute('aria-selected', String(selected));
                tab.tabIndex = selected ? 0 : -1;
                if (selected && moveFocus) tab.focus();
            });
            document.querySelectorAll('.workspace-panel').forEach((panel) => {
                panel.hidden = panel.id !== `view-${name}`;
            });
            try { localStorage.setItem('omniscrobble_workspace', name); } catch (e) {}
        }
        const dashboardCommands = [
            { label: 'Manual Scrobble', detail: 'Search and mark media watched', run: () => openManualScrobbleModal() },
            { label: 'Retry Offline Queue', detail: 'Retry pending tracker deliveries', run: () => retryQueue() },
            { label: 'View Live Logs', detail: 'Open server diagnostics', run: () => openLogsModal() },
            { label: 'Switch Theme', detail: 'Choose a dashboard palette', run: () => openThemeModal() },
            { label: 'Reconcile Trakt and Media Server', detail: 'Scan library watch history', run: () => { switchWorkspace('trackers'); openReconcileModal(); } },
            { label: 'Add Media with Sonarr or Radarr', detail: 'Search the acquisition catalog', run: () => openAddArrModal() },
            { label: 'Webhook Inspector', detail: 'Inspect recent incoming events', run: () => openWebhookDebuggerModal() },
            ...Object.entries({ operations: 'Operations', trackers: 'Trackers & Hub', automation: 'Automation', analytics: 'Analytics', diagnostics: 'System' }).map(([id, label]) => ({ label: `Go to ${label}`, detail: 'Switch dashboard workspace', run: () => switchWorkspace(id) }))
        ];
        let commandPaletteMatches = [];
        let commandPaletteIndex = 0;
        function openCommandPalette() {
            const modal = document.getElementById('command-palette-modal');
            if (!modal) return;
            modal.style.display = 'flex';
            const input = document.getElementById('command-palette-search');
            input.value = '';
            renderCommandPalette('');
            window.setTimeout(() => input.focus(), 20);
        }
        function closeCommandPalette() {
            const modal = document.getElementById('command-palette-modal');
            if (modal) modal.style.display = 'none';
        }
        function toggleCommandPalette() {
            const modal = document.getElementById('command-palette-modal');
            if (!modal) return;
            if (modal.style.display === 'flex') closeCommandPalette();
            else openCommandPalette();
        }
        function renderCommandPalette(query) {
            const q = String(query || '').trim().toLowerCase();
            commandPaletteMatches = dashboardCommands.map((command) => {
                const haystack = `${command.label} ${command.detail}`.toLowerCase();
                const tokens = q.split(/\s+/).filter(Boolean);
                const matched = tokens.every((token) => haystack.includes(token));
                const score = q ? (command.label.toLowerCase().startsWith(q) ? 0 : command.label.toLowerCase().includes(q) ? 1 : 2) : 0;
                return { ...command, matched, score };
            }).filter((command) => command.matched).sort((a, b) => a.score - b.score || a.label.localeCompare(b.label));
            commandPaletteIndex = 0;
            const list = document.getElementById('command-palette-results');
            list.replaceChildren();
            if (!commandPaletteMatches.length) {
                const empty = document.createElement('div');
                empty.textContent = 'No matching commands';
                empty.style.cssText = 'padding:14px;color:var(--text-muted);font-size:13px;';
                list.appendChild(empty);
                return;
            }
            commandPaletteMatches.forEach((command, index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.setAttribute('role', 'option');
                button.setAttribute('aria-selected', String(index === commandPaletteIndex));
                button.className = 'command-palette-option';
                button.style.cssText = `display:flex;flex-direction:column;align-items:flex-start;gap:3px;width:100%;padding:11px 12px;border:1px solid ${index === commandPaletteIndex ? 'var(--accent-color)' : 'transparent'};border-radius:7px;background:${index === commandPaletteIndex ? 'var(--bg-subtle)' : 'transparent'};color:var(--text-main);text-align:left;cursor:pointer;`;
                const title = document.createElement('span');
                title.textContent = command.label;
                title.style.fontWeight = '600';
                const detail = document.createElement('span');
                detail.textContent = command.detail;
                detail.style.cssText = 'font-size:11px;color:var(--text-muted);';
                button.append(title, detail);
                button.addEventListener('mouseenter', () => highlightCommandPalette(index));
                button.addEventListener('click', () => runCommandPaletteCommand(index));
                list.appendChild(button);
            });
        }
        function highlightCommandPalette(index) {
            commandPaletteIndex = index;
            document.querySelectorAll('#command-palette-results [role="option"]').forEach((option, optionIndex) => {
                const selected = optionIndex === index;
                option.setAttribute('aria-selected', String(selected));
                option.style.borderColor = selected ? 'var(--accent-color)' : 'transparent';
                option.style.background = selected ? 'var(--bg-subtle)' : 'transparent';
            });
        }
        function runCommandPaletteCommand(index = commandPaletteIndex) {
            const command = commandPaletteMatches[index];
            if (!command) return;
            closeCommandPalette();
            command.run();
        }
        function handleCommandPaletteKeydown(event) {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                const delta = event.key === 'ArrowDown' ? 1 : -1;
                if (commandPaletteMatches.length) highlightCommandPalette((commandPaletteIndex + delta + commandPaletteMatches.length) % commandPaletteMatches.length);
            } else if (event.key === 'Enter') {
                event.preventDefault();
                runCommandPaletteCommand();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closeCommandPalette();
            }
        }
        function initWorkspaces() {
            const logsDialog = document.querySelector('#logs-modal > .modal-dialog');
            const inspectorDialog = document.querySelector('#webhook-debugger-modal > .modal-dialog');
            if (logsDialog) document.getElementById('diagnostics-pane-logs')?.appendChild(logsDialog);
            if (inspectorDialog) document.getElementById('diagnostics-pane-inspector')?.appendChild(inspectorDialog);
            Object.entries(workspaceCards).forEach(([workspace, ids]) => {
                const panel = document.getElementById(`view-${workspace}`);
                ids.forEach((id) => {
                    const card = document.getElementById(id);
                    if (panel && card) panel.appendChild(card);
                });
            });
            document.getElementById('workspace-nav')?.addEventListener('keydown', (event) => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                const tabs = Array.from(document.querySelectorAll('.workspace-tab'));
                const current = tabs.indexOf(document.activeElement);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
                event.preventDefault();
                switchWorkspace(tabs[next].id.replace('tab-', ''), true);
            });
            let saved = 'operations';
            try { saved = localStorage.getItem('omniscrobble_workspace') || saved; } catch (e) {}
            switchWorkspace(saved);
            const serverCard = document.getElementById('card-server-config');
            if (serverCard) {
                const labels = Array.from(serverCard.querySelectorAll('.info-item'));
                const tracker = labels.find((item) => item.textContent.includes('Trakt Connection'));
                const queue = serverCard.textContent.match(/Queue:\s*\d+\s*pending/i);
                const tokenStatus = tracker?.textContent.match(/Healthy|Token Expired\s*\/\s*Refresh Needed|Not Linked/i);
                const trackerStatus = document.getElementById('health-tracker-status');
                if (trackerStatus && tokenStatus) trackerStatus.textContent = tokenStatus[0].replace('Token Expired / Refresh Needed', 'Refresh needed');
                const queueStatus = document.getElementById('health-queue-status');
                if (queueStatus && queue) queueStatus.textContent = queue[0].replace(/^Queue:\s*/i, '');
            }
            const listeners = document.querySelectorAll('#card-ecosystem .eco-card:not(.eco-card-disabled)').length;
            const listenerStatus = document.getElementById('health-listener-status');
            if (listenerStatus) listenerStatus.textContent = listeners ? `${listeners} active` : 'None active';
        }
        window.addEventListener('pageshow', resetCowatchScroll);
        document.addEventListener('DOMContentLoaded', () => {
            initWorkspaces();
            resetCowatchScroll();
            fetchEvents();
            updateDynamicSubtitle();
        });

        // PWA Service Worker Registration
        if (!isDemo && 'serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').then((reg) => {
                    reg.update().catch(() => {});
                }).catch(() => {});
            });
        }
