        function resetCowatchScroll() {
            const cwChips = document.getElementById('cowatch-chips-container');
            if (cwChips) cwChips.scrollTop = 0;
            const devChips = document.getElementById('cowatch-devices-chips-container');
            if (devChips) devChips.scrollTop = 0;
        }
        const workspaceCards = {
            operations: ['active-playback-card', 'card-activity'],
            'watch-lists': ['card-watch-lists'],
            trackers: ['card-server-config', 'card-ecosystem', 'card-multi-tracker', 'card-reconciliation'],
            automation: ['automation-rules-card', 'card-cowatch', 'card-arr-bridge'],
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
            if (name === 'diagnostics' && isAdmin && !document.getElementById('recovery-checks-list')?.dataset.loaded) refreshRecoveryChecks();
            if (name === 'automation') loadAutomationRules();
        }
        function openRecoveryAction(action) {
            if (action === 'compatibility') return;
            if (action === 'servers') return openSettingsModal('servers');
            if (action === 'trackers') return openSettingsModal('trackers');
            if (action === 'notifications') return openSettingsModal('notifications');
            if (action === 'queue') return openQueueRecovery();
            if (action === 'logs') return openLogsModal();
            if (action === 'webhook') return openWebhookDebuggerModal();
            openSettingsModal('servers');
        }
        async function refreshRecoveryChecks() {
            const list = document.getElementById('recovery-checks-list');
            const updated = document.getElementById('recovery-checks-updated');
            if (!list || !updated) return;
            list.replaceChildren(Object.assign(document.createElement('p'), {textContent: 'Loading checks…'}));
            try {
                const response = await fetch('/api/health/recovery');
                if (!response.ok) throw new Error(response.status === 401 ? 'Admin access is required to view recovery checks.' : 'Could not load recovery checks.');
                const data = await response.json();
                list.replaceChildren();
                (data.checks || []).forEach((check) => {
                    const item = document.createElement('article');
                    item.className = `recovery-check recovery-check-${check.status}`;
                    const heading = document.createElement('div');
                    heading.className = 'recovery-check-heading';
                    const title = document.createElement('strong');
                    title.textContent = check.label;
                    const badge = document.createElement('span');
                    badge.className = 'recovery-check-status';
                    badge.textContent = check.status.replaceAll('_', ' ');
                    heading.append(title, badge);
                    const summary = document.createElement('p');
                    summary.textContent = check.summary;
                    if (Array.isArray(check.compatibility_items) && check.compatibility_items.length) {
                        const formatList = document.createElement('ul');
                        formatList.className = 'recovery-capability-list';
                        check.compatibility_items.forEach((format) => {
                            const row = document.createElement('li');
                            const observed = format.observed_version === null ? 'not present' : `v${format.observed_version}`;
                            row.textContent = `${format.id.replaceAll('_', ' ')}: ${format.status.replaceAll('_', ' ')} (${observed}; current v${format.current_version}). ${format.action}`;
                            formatList.append(row);
                        });
                        item.append(heading, summary, formatList);
                    } else if (Array.isArray(check.findings) && check.findings.length) {
                        const findingsList = document.createElement('ul');
                        findingsList.className = 'recovery-capability-list';
                        check.findings.forEach((finding) => {
                            const row = document.createElement('li');
                            row.textContent = `${finding.severity.toUpperCase()}: ${finding.message}`;
                            findingsList.append(row);
                        });
                        item.append(heading, summary, findingsList);
                    } else {
                        item.append(heading, summary);
                    }
                    if (check.id === 'compatibility') {
                        const details = document.createElement('small');
                        const integrations = Object.entries(check.integrations || {}).map(([name, value]) => `${name}: ${value.version || 'unknown'} (${value.status}; ${value.compatibility})`);
                        const ruleset = check.ruleset || {};
                        details.textContent = `${check.application?.name} ${check.application?.version} · Python ${check.runtime?.python_version} (${check.runtime?.status}) · Integration versions are learned from explicit connection tests${integrations.length ? `: ${integrations.join('; ')}` : ''}. Ruleset v${ruleset.version}, reviewed ${ruleset.last_reviewed}.`;
                        item.append(details);
                    }
                    if (Array.isArray(check.effective_settings) && check.effective_settings.length) {
                        const details = document.createElement('details');
                        const detailsHeading = document.createElement('summary');
                        detailsHeading.textContent = 'Effective settings and sources';
                        const sourceList = document.createElement('ul');
                        sourceList.className = 'recovery-capability-list';
                        check.effective_settings.forEach((setting) => {
                            const row = document.createElement('li');
                            row.textContent = `${setting.name}: ${setting.value} (from ${setting.source})`;
                            sourceList.append(row);
                        });
                        details.append(detailsHeading, sourceList);
                        item.append(details);
                    }
                    if (Array.isArray(check.tracker_capabilities) && check.tracker_capabilities.length) {
                        const capabilityList = document.createElement('ul');
                        capabilityList.className = 'recovery-capability-list';
                        check.tracker_capabilities.forEach((tracker) => {
                            const capabilityLabels = {
                                realtime_playback: 'live playback', history: 'watched history', progress: 'progress',
                                ratings: 'ratings', watchlist: 'watch lists', collection: 'collection', search: 'search',
                            };
                            const capabilities = (tracker.capabilities || []).map((capability) => capabilityLabels[capability] || capability);
                            const detail = document.createElement('li');
                            detail.textContent = `${tracker.name}: ${capabilities.join(', ')} (${tracker.media_types.join(', ')})`;
                            capabilityList.append(detail);
                        });
                        item.append(capabilityList);
                    }
                    if (check.auth_rejections?.count) {
                        const rejectionList = document.createElement('ul');
                        rejectionList.className = 'recovery-capability-list';
                        Object.entries(check.auth_rejections.by_endpoint || {}).forEach(([endpoint, count]) => {
                            const detail = document.createElement('li');
                            detail.textContent = `${endpoint}: ${count} rejected request${count === 1 ? '' : 's'}`;
                            rejectionList.append(detail);
                        });
                        item.append(rejectionList);
                    }
                    const footer = document.createElement('div');
                    footer.className = 'recovery-check-footer';
                    const time = document.createElement('small');
                    time.textContent = `Checked ${new Date(check.last_checked).toLocaleString()}`;
                    footer.append(time);
                    if (Array.isArray(check.test_integrations) && check.test_integrations.length) {
                        const tests = document.createElement('div');
                        tests.className = 'recovery-check-tests';
                        check.test_integrations.forEach((integration) => {
                            const button = document.createElement('button');
                            button.type = 'button';
                            button.className = 'btn-sm';
                            const label = integration.startsWith('notification:') ? integration.split(':')[1] : integration;
                            button.textContent = `Test ${label.charAt(0).toUpperCase()}${label.slice(1)}`;
                            button.addEventListener('click', () => runRecoveryConnectionTest(integration, button));
                            tests.append(button);
                        });
                        item.append(tests);
                    }
                    const action = document.createElement('button');
                    action.type = 'button';
                    action.className = 'btn-sm';
                    action.textContent = check.id === 'compatibility' ? 'Version report' : (check.status === 'healthy' || check.status === 'optional' ? 'Open settings' : 'Review');
                    action.disabled = check.id === 'compatibility';
                    action.addEventListener('click', () => openRecoveryAction(check.next_action));
                    footer.append(action);
                    const testResult = document.createElement('p');
                    testResult.className = 'recovery-test-result';
                    testResult.setAttribute('aria-live', 'polite');
                    testResult.dataset.integration = check.id;
                    item.append(footer, testResult);
                    list.append(item);
                });
                list.dataset.loaded = 'true';
                updated.textContent = `Last checked ${new Date(data.checked_at).toLocaleString()}. Checks do not contact external services.`;
            } catch (error) {
                list.replaceChildren(Object.assign(document.createElement('p'), {textContent: error.message || 'Could not load recovery checks.'}));
                updated.textContent = 'Recovery checks unavailable.';
            }
        }
        async function runRecoveryConnectionTest(integration, button) {
            const result = button.closest('.recovery-check')?.querySelector('.recovery-test-result');
            button.disabled = true;
            button.textContent = 'Testing…';
            if (result) result.textContent = `Testing ${integration}…`;
            try {
                const response = await fetch('/api/health/recovery/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({integration})
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Connection test failed.');
                if (result) {
                    result.className = `recovery-test-result recovery-test-${data.status}`;
                    result.textContent = `${integration}: ${data.message} Checked ${new Date(data.checked_at).toLocaleString()}.`;
                }
            } catch (error) {
                if (result) {
                    result.className = 'recovery-test-result recovery-test-failed';
                    result.textContent = error.message || 'Connection test failed.';
                }
            } finally {
                button.disabled = false;
                const label = integration.startsWith('notification:') ? integration.split(':')[1] : integration;
                button.textContent = `Test ${label.charAt(0).toUpperCase()}${label.slice(1)}`;
            }
        }
        function openTrackerRecovery() {
            const text = document.getElementById('health-tracker-status')?.textContent || '';
            openSettingsModal('trackers', /refresh|expired/i.test(text) ? 'trakt' : null);
        }
        function openListenerRecovery() { openSettingsModal('servers'); }
        function openQueueRecovery() {
            switchWorkspace('operations');
            const status = document.getElementById('health-queue-status')?.textContent || '';
            const failedFilter = Array.from(document.querySelectorAll('.activity-filter-chip')).find((button) => button.getAttribute('onclick') === "setActivityFilter('status', 'failed', this)");
            const hasFailedEvents = (Array.isArray(allEvents) && allEvents.some((event) => {
                const result = String(event.result_status || '').toLowerCase();
                const deliveries = Object.values(event.tracker_delivery || {}).map((value) => String(value).toLowerCase());
                return result === 'error' || result === 'failed' || result === '429' || /^4\d\d$/.test(result) || /^5\d\d$/.test(result) || deliveries.includes('failed');
            })) || Boolean(document.querySelector('#events-tbody .activity-status-failed, #events-tbody .tracker-delivery-failed'));
            if (hasFailedEvents && failedFilter) {
                const allTypesFilter = Array.from(document.querySelectorAll('#card-activity .activity-filter-group[aria-label="Media type"] .activity-filter-chip')).find((button) => button.getAttribute('onclick') === "setActivityFilter('type', 'all', this)");
                if (allTypesFilter) setActivityFilter('type', 'all', allTypesFilter);
                const userFilter = document.getElementById('activity-user-filter');
                if (userFilter) userFilter.value = '';
                setActivityUser('');
                const search = document.getElementById('activity-search');
                if (search) search.value = '';
                setActivitySearch('');
                setActivityFilter('status', 'failed', failedFilter);
                document.querySelector('#events-tbody .activity-row')?.scrollIntoView({behavior:'smooth', block:'center'});
            } else if (/\d+ pending/i.test(status) && typeof retryQueue === 'function') {
                const retryButton = document.querySelector('#card-activity button[onclick="retryQueue()"]');
                if (retryButton) retryButton.focus();
                else document.getElementById('card-activity')?.scrollIntoView({behavior:'smooth', block:'start'});
            } else {
                document.getElementById('activity-search')?.focus();
            }
        }
        function updateSetupChecklist() {
            const checklist = document.getElementById('setup-checklist');
            if (!checklist) return;
            const serverCards = Array.from(document.querySelectorAll('#card-ecosystem .eco-card'));
            const serverReady = serverCards.some((card) => {
                const name = card.querySelector('.eco-card-name')?.textContent || '';
                const badge = card.querySelector('.eco-status-badge');
                return /plex|jellyfin|emby/i.test(name) && (badge?.classList.contains('is-connected') || badge?.classList.contains('is-available'));
            });
            const trackerReady = Array.from(document.querySelectorAll('#hub-trackers-grid .hub-tracker-item')).some((item) => {
                const status = item.querySelector(':scope > div:last-child > span:first-child')?.textContent || '';
                return /\b(active|ready)\b/i.test(status);
            });
            const successfulScrobbleActions = new Set(['scrobble', 'watched']);
            const activityReady = Array.isArray(allEvents) && allEvents.some((event) => {
                const status = String(event.result_status || '').toLowerCase();
                const action = String(event.action || '').toLowerCase();
                const succeeded = ['ok', 'success', '200', '201'].includes(status);
                const isScrobble = successfulScrobbleActions.has(action) || action.startsWith('mark_watched') || action.startsWith('scrobble_stop');
                return succeeded && isScrobble;
            });
            const states = {server:serverReady, tracker:trackerReady, activity:activityReady};
            let complete = 0;
            Object.entries(states).forEach(([step, done]) => {
                const item = checklist.querySelector(`[data-setup-step="${step}"]`);
                if (!item) return;
                item.classList.toggle('is-complete', done);
                item.querySelector('.setup-step-state').textContent = done ? '✓' : String(Object.keys(states).indexOf(step) + 1);
                if (done) complete += 1;
            });
            const progress = document.getElementById('setup-checklist-progress');
            if (progress) progress.textContent = `${complete} of 3 complete`;
            checklist.hidden = complete === 3;
        }
        const dashboardCommands = [
            { label: 'Manual Scrobble', detail: 'Search and mark media watched', run: () => openManualScrobbleModal() },
            { label: 'Retry Offline Queue', detail: 'Retry pending tracker deliveries', run: () => retryQueue() },
            { label: 'View Live Logs', detail: 'Open server diagnostics', run: () => openLogsModal() },
            { label: 'Switch Theme', detail: 'Choose a dashboard palette', run: () => openThemeModal() },
            { label: 'Reconcile Trakt and Media Server', detail: 'Scan library watch history', run: () => { switchWorkspace('trackers'); openReconcileModal(); } },
            { label: 'Add Media with Sonarr or Radarr', detail: 'Search the acquisition catalog', run: () => openAddArrModal() },
            { label: 'Webhook Inspector', detail: 'Inspect recent incoming events', run: () => openWebhookDebuggerModal() },
            ...Object.entries({ operations: 'Operations', 'watch-lists': 'Watch Lists', trackers: 'Trackers & Hub', automation: 'Automation', analytics: 'Analytics', diagnostics: 'System' }).map(([id, label]) => ({ label: `Go to ${label}`, detail: 'Switch dashboard workspace', run: () => switchWorkspace(id) }))
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
            updateSetupChecklist();
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
            const listeners = Array.from(document.querySelectorAll('#card-ecosystem .eco-card:not(.eco-card-disabled)')).filter((card) => /plex|jellyfin|emby/i.test(card.querySelector('.eco-card-name')?.textContent || '')).length;
            const listenerStatus = document.getElementById('health-listener-status');
            if (listenerStatus) listenerStatus.textContent = listeners ? `${listeners} active` : 'None active';
            updateSetupChecklist();
        }
        function initModalAccessibility() {
            const activeDialogs = new Map();
            const getDialog = (overlay) => overlay.querySelector('.modal-dialog, [role="dialog"]') || overlay;
            const focusableSelector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
            const syncOverlay = (overlay) => {
                const dialog = getDialog(overlay);
                const isOpen = getComputedStyle(overlay).display !== 'none';
                if (isOpen && !activeDialogs.has(overlay)) {
                    activeDialogs.set(overlay, document.activeElement);
                    dialog.setAttribute('role', 'dialog');
                    dialog.setAttribute('aria-modal', 'true');
                    if (!dialog.hasAttribute('tabindex')) dialog.tabIndex = -1;
                    if (!dialog.hasAttribute('aria-labelledby') && !dialog.hasAttribute('aria-label')) {
                        const heading = dialog.querySelector('h1, h2, h3, [data-dialog-title]');
                        if (heading) {
                            if (!heading.id) heading.id = `dialog-title-${overlays.indexOf(overlay)}`;
                            dialog.setAttribute('aria-labelledby', heading.id);
                        }
                    }
                    window.setTimeout(() => {
                        if (getComputedStyle(overlay).display === 'none') return;
                        const first = Array.from(dialog.querySelectorAll(focusableSelector)).find((el) => el.getClientRects().length && !el.closest('[hidden]'));
                        (first || dialog).focus({ preventScroll: true });
                    }, 0);
                } else if (!isOpen && activeDialogs.has(overlay)) {
                    const opener = activeDialogs.get(overlay);
                    activeDialogs.delete(overlay);
                    if (opener?.isConnected && typeof opener.focus === 'function') opener.focus({ preventScroll: true });
                }
            };
            const overlays = Array.from(document.querySelectorAll('.modal, #command-palette-modal'));
            overlays.forEach(syncOverlay);
            const observer = new MutationObserver((records) => records.forEach((record) => syncOverlay(record.target)));
            overlays.forEach((overlay) => observer.observe(overlay, { attributes: true, attributeFilter: ['style', 'hidden'] }));
            document.addEventListener('keydown', (event) => {
                if (event.key !== 'Tab') return;
                const overlay = overlays.filter((item) => getComputedStyle(item).display !== 'none').at(-1);
                if (!overlay) return;
                const dialog = getDialog(overlay);
                const focusable = Array.from(dialog.querySelectorAll(focusableSelector)).filter((el) => el.getClientRects().length && !el.closest('[hidden]'));
                if (!focusable.length) { event.preventDefault(); dialog.focus(); return; }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
            });
        }
        window.addEventListener('pageshow', resetCowatchScroll);
        document.addEventListener('DOMContentLoaded', () => {
            initModalAccessibility();
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
