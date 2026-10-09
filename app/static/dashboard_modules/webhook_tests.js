        function openTestWebhookModal() {
            if (!isAdmin && !isDemo) { openUnlockModal(); return; }
            const modal = document.getElementById('test-modal');
            if (modal) modal.style.display = 'flex';
        }

        function closeTestWebhookModal() {
            const modal = document.getElementById('test-modal');
            if (modal) modal.style.display = 'none';
        }

        function toggleTestMediaFields(type) {
            const showRow = document.getElementById('test-show-row');
            const sCol = document.getElementById('test-season-col');
            const eCol = document.getElementById('test-episode-col');
            if (type === 'movie') {
                if (showRow) showRow.style.display = 'none';
                if (sCol) sCol.style.display = 'none';
                if (eCol) eCol.style.display = 'none';
            } else {
                if (showRow) showRow.style.display = 'block';
                if (sCol) sCol.style.display = 'block';
                if (eCol) eCol.style.display = 'block';
            }
        }

        async function submitTestWebhook() {
            const btn = document.getElementById('test-submit-btn');
            const resBox = document.getElementById('test-result-box');
            const eventVal = document.getElementById('test-event-select').value;
            const typeVal = document.getElementById('test-type-select').value;
            const showTitle = document.getElementById('test-show-input').value.trim();
            const titleVal = document.getElementById('test-title-input').value.trim();
            const seasonVal = parseInt(document.getElementById('test-season-input').value) || 1;
            const episodeVal = parseInt(document.getElementById('test-episode-input').value) || 1;
            const execTrakt = document.getElementById('test-execute-trakt').checked;

            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Simulating...';
            }
            if (resBox) resBox.style.display = 'none';

            try {
                if (isDemo) {
                    if (resBox) {
                        resBox.style.display = 'block';
                        resBox.innerHTML = '<span class="u-color-10b981">✓ Simulated webhook passed in Demo mode.</span>';
                    }
                    if (btn) { btn.disabled = false; btn.textContent = '▶ Run Simulation'; }
                    return;
                }
                const res = await fetch('/api/test/webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        event: eventVal,
                        media_type: typeVal,
                        title: titleVal || (typeVal === 'movie' ? 'Synthetic Movie' : 'Synthetic Episode'),
                        show_title: typeVal === 'episode' ? showTitle : null,
                        season: seasonVal,
                        episode: episodeVal,
                        execute_trakt: execTrakt
                    })
                });
                const data = await res.json();
                if (btn) { btn.disabled = false; btn.textContent = '▶ Run Simulation'; }
                if (resBox) {
                    resBox.style.display = 'block';
                    const cw = data.cowatch || {};
                        resBox.innerHTML = `<div>Status: <strong class="u-color-10b981">${escapeHtml(data.status)}</strong></div>` +
                        `<div>Parsed: ${escapeHtml(data.parsed.title)} (${escapeHtml(data.parsed.media_type)})</div>` +
                        `<div>Co-Watch: <span class="webhook-test-eligibility ${cw.eligible ? 'is-eligible' : 'is-ineligible'}">${cw.eligible ? 'Eligible' : 'Not Eligible'}</span> &bull; ${escapeHtml(cw.reason || '')}</div>`;
                }
                fetchEvents();
            } catch (e) {
                if (btn) { btn.disabled = false; btn.textContent = '▶ Run Simulation'; }
                if (resBox) {
                    resBox.style.display = 'block';
                    resBox.innerHTML = `<span class="u-color-ef4444">Error: ${escapeHtml(e.message)}</span>`;
                }
            }
        }

        async function toggleCowatchMovies() {
            if (!isAdmin) { openUnlockModal(); return; }
            const statusEl = document.getElementById('cowatch-movies-status');
            const btn = document.getElementById('cowatch-movies-btn');
            const currentEnabled = statusEl ? statusEl.textContent.trim().toLowerCase() === 'enabled' : false;
            const newTarget = !currentEnabled;
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Updating...';
            }
            try {
                const url = isDemo ? '/api/cowatch/settings?demo=true' : '/api/cowatch/settings';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ co_watch_movies: newTarget })
                });
                if (res.ok) {
                    const data = await res.json();
                    const isNowEnabled = data.co_watch_movies;
                    if (statusEl) statusEl.textContent = isNowEnabled ? 'Enabled' : 'Disabled';
                    if (btn) {
                        btn.textContent = `Toggle Movies (${isNowEnabled ? 'Disable' : 'Enable'})`;
                        btn.disabled = false;
                    }
                } else {
                    alert('Failed to update co-watch movie settings');
                    if (btn) btn.disabled = false;
                }
            } catch (e) {
                alert('Error: ' + e.message);
                if (btn) btn.disabled = false;
            }
        }

        const savedAutoRefresh = localStorage.getItem('omniscrobble_auto_refresh') || localStorage.getItem('plex_trakt_auto_refresh');
        if (savedAutoRefresh === 'true') {
            const toggle = document.getElementById('auto-refresh-toggle');
            if (toggle) toggle.checked = true;
            refreshTimer = setInterval(fetchEvents, 30000);
        }

        // -------------------------------------------------------------
        // Multi-Theme Palette Engine & Accents (Phase 1)
        // -------------------------------------------------------------
        const THEMES = [
            { id: 'slate', name: 'Slate', desc: 'Oceanic Slate (Default)', icon: '🌊', bg: '#0f172a', card: 'var(--bg-surface)', border: 'var(--border-color)' },
            { id: 'oled', name: 'OLED', desc: 'True Black (#000000)', icon: '⬛', bg: '#000000', card: '#09090b', border: '#27272a' },
            { id: 'nord', name: 'Nord', desc: 'Arctic Frost & Polar Night', icon: '❄️', bg: '#242933', card: '#2e3440', border: '#4c566a' },
            { id: 'catppuccin', name: 'Catppuccin', desc: 'Cozy Mocha Dark', icon: '☕', bg: '#181825', card: '#1e1e2e', border: '#313244' },
            { id: 'tokyonight', name: 'Tokyo Night', desc: 'Cyberpunk Neon Dark', icon: '🌃', bg: '#1a1b26', card: '#1f2335', border: '#292e42' },
            { id: 'dracula', name: 'Dracula', desc: 'Classic Vampire Contrast', icon: '🧛', bg: '#21222c', card: '#282a36', border: '#44475a' },
            { id: 'emerald', name: 'Emerald', desc: 'Forest Pine Obsidian', icon: '🌲', bg: '#061a14', card: '#0d2820', border: '#1b4337' },
            { id: 'rosepine', name: 'Rosé Pine', desc: 'All Natural Dark Aesthetic', icon: '🌹', bg: '#191724', card: '#21202e', border: '#393552' }
        ];

        const ACCENTS = [
            { id: 'sky', name: 'Sky', hex: 'var(--accent-color)' },
            { id: 'amber', name: 'Amber', hex: 'var(--status-paused)' },
            { id: 'trakt', name: 'Trakt Red', hex: '#ed1c24' },
            { id: 'plex', name: 'Plex Gold', hex: '#e5a00d' },
            { id: 'jellyfin', name: 'Jellyfin Purple', hex: '#aa5cc3' },
            { id: 'emerald', name: 'Emerald', hex: 'var(--status-playing)' },
            { id: 'cyan', name: 'Cyan', hex: '#06b6d4' },
            { id: 'rose', name: 'Rosé', hex: '#eb6f92' },
            { id: 'mauve', name: 'Mauve', hex: '#cba6f7' }
        ];

        function getCurrentTheme() {
            return localStorage.getItem('omniscrobble_theme') || 'slate';
        }

        function getCurrentAccent() {
            return localStorage.getItem('omniscrobble_accent') || 'sky';
        }

        function setTheme(themeId) {
            const root = document.documentElement;
            THEMES.forEach(t => root.classList.remove('theme-' + t.id));
            if (themeId && themeId !== 'slate') {
                root.classList.add('theme-' + themeId);
            }
            if (themeId === 'oled') {
                root.classList.add('theme-oled');
            }
            localStorage.setItem('omniscrobble_theme', themeId);
            updateThemeUI();
        }

        function setAccent(accentId) {
            const root = document.documentElement;
            ACCENTS.forEach(a => root.classList.remove('accent-' + a.id));
            if (accentId) {
                root.classList.add('accent-' + accentId);
            }
            localStorage.setItem('omniscrobble_accent', accentId);
            updateThemeUI();
        }

        function cycleTheme() {
            const current = getCurrentTheme();
            const idx = THEMES.findIndex(t => t.id === current);
            const nextIdx = (idx + 1) % THEMES.length;
            setTheme(THEMES[nextIdx].id);
        }

        function toggleTheme() {
            // Backward-compatible helper
            const current = getCurrentTheme();
            setTheme(current === 'oled' ? 'slate' : 'oled');
        }

        function updateThemeUI() {
            const currentTheme = getCurrentTheme();
            const themeObj = THEMES.find(t => t.id === currentTheme) || THEMES[0];

            // Header button update
            const btnLabel = document.getElementById('theme-btn-label');
            if (btnLabel) {
                btnLabel.textContent = themeObj.name;
            }
            const themeBtn = document.getElementById('theme-toggle-btn');
            if (themeBtn) {
                themeBtn.title = `Current Theme: ${themeObj.name} (Press 'T' to cycle, click to configure)`;
            }

            renderThemePickers();
            if (typeof updateDensityUI === 'function') updateDensityUI();
            if (typeof renderCardVisibilityPickers === 'function') renderCardVisibilityPickers();
        }

        function renderThemePickers() {
            const currentTheme = getCurrentTheme();
            const currentAccent = getCurrentAccent();

            const themeContainers = [
                document.getElementById('theme-modal-palette-grid'),
                document.getElementById('settings-theme-grid')
            ];

            themeContainers.forEach(container => {
                if (!container) return;
                container.innerHTML = THEMES.map(t => {
                    const isSelected = t.id === currentTheme;
                    return `
                        <div onclick="setTheme('${t.id}')" class="theme-preview-card${isSelected ? ' is-selected' : ''}" style="--theme-card:${t.card};--theme-border:${isSelected ? 'var(--accent-color)' : t.border};--theme-swatch:${t.bg};">
                            <div class="theme-preview-swatch">
                                ${t.icon}
                            </div>
                            <div class="u-min-width-0 u-flex-1">
                                <div class="u-font-size-13px u-font-weight-600 u-color-text-main u-white-space-nowrap u-overflow-hidden u-text-overflow-ellipsis">
                                    ${t.name} ${isSelected ? '✓' : ''}
                                </div>
                                <div class="u-font-size-11px u-color-text-muted u-white-space-nowrap u-overflow-hidden u-text-overflow-ellipsis">
                                    ${t.desc}
                                </div>
                            </div>
                        </div>
                    `;
                }).join('');
            });

            const accentContainers = [
                document.getElementById('theme-modal-accent-grid'),
                document.getElementById('settings-accent-grid')
            ];

            accentContainers.forEach(container => {
                if (!container) return;
                container.innerHTML = ACCENTS.map(a => {
                    const isSelected = a.id === currentAccent;
                    return `
                        <button type="button" onclick="setAccent('${a.id}')" class="accent-preview-button${isSelected ? ' is-selected' : ''}" style="--accent-preview:${a.hex};">
                            <span class="accent-preview-swatch"></span>
                            <span>${a.name}</span>
                            ${isSelected ? '<span class="u-font-size-11px">✓</span>' : ''}
                        </button>
                    `;
                }).join('');
            });
        }

        // Display Density (Comfortable vs Compact)
        function getDensity() {
            return localStorage.getItem('omniscrobble_density') || 'comfortable';
        }

        function setDensity(mode) {
            const root = document.documentElement;
            if (mode === 'compact') {
                root.classList.add('density-compact');
                localStorage.setItem('omniscrobble_density', 'compact');
            } else {
                root.classList.remove('density-compact');
                localStorage.setItem('omniscrobble_density', 'comfortable');
            }
            updateDensityUI();
        }

        function updateDensityUI() {
            const mode = getDensity();
            const btnComf = document.getElementById('density-btn-comfortable');
            const btnComp = document.getElementById('density-btn-compact');
            if (btnComf) {
                if (mode === 'comfortable') {
                    btnComf.style.background = 'var(--accent-color, var(--accent-color))';
                    btnComf.style.color = 'var(--accent-text)';
                    btnComf.style.borderColor = 'var(--accent-color, var(--accent-color))';
                } else {
                    btnComf.style.background = 'var(--bg-surface)';
                    btnComf.style.color = 'var(--text-muted)';
                    btnComf.style.borderColor = 'var(--border-color)';
                }
            }
            if (btnComp) {
                if (mode === 'compact') {
                    btnComp.style.background = 'var(--accent-color, var(--accent-color))';
                    btnComp.style.color = 'var(--accent-text)';
                    btnComp.style.borderColor = 'var(--accent-color, var(--accent-color))';
                } else {
                    btnComp.style.background = 'var(--bg-surface)';
                    btnComp.style.color = 'var(--text-muted)';
                    btnComp.style.borderColor = 'var(--border-color)';
                }
            }
        }

        // Dashboard Card Visibility Controls
        const DASHBOARD_CARDS = [
            { id: 'active-playback-card', label: 'Active Playback Stream', desc: 'Live sessions, progress bar, ambient backdrop' },
            { id: 'card-server-config', label: 'Server & Account Status', desc: 'Trakt account pairing, endpoints, sync controls' },
            { id: 'card-ecosystem', label: 'Multi-Server Ecosystem', desc: 'Plex, Jellyfin, and Emby ingest status cards' },
            { id: 'card-multi-tracker', label: 'Multi-Tracker Cloud Hub', desc: 'Simkl, AniList, and MyAnimeList sync status' },
            { id: 'card-cowatch', label: 'Watch Together (Multi-User)', desc: 'Partner scrobble status, co-watch shows & rules' },
            { id: 'card-reconciliation', label: 'Two-Way Library Reconciliation', desc: 'Scan & sync discrepancies across libraries' },
            { id: 'card-arr-bridge', label: 'Automation & Content Bridge', desc: 'Sonarr & Radarr instant webhook monitoring' },
            { id: 'card-backup', label: 'System Operations & Backup', desc: 'Snapshot creation, state restore, and maintenance' },
            { id: 'card-activity', label: 'Live Activity History', desc: 'Real-time telemetry event stream and search' }
        ];

        function getHiddenCards() {
            try {
                const stored = localStorage.getItem('omniscrobble_hidden_cards');
                return stored ? JSON.parse(stored) : [];
            } catch (e) {
                return [];
            }
        }

        function setCardVisibility(cardId, isVisible) {
            let hidden = getHiddenCards();
            if (isVisible) {
                hidden = hidden.filter(id => id !== cardId);
            } else {
                if (!hidden.includes(cardId)) {
                    hidden.push(cardId);
                }
            }
            localStorage.setItem('omniscrobble_hidden_cards', JSON.stringify(hidden));
            applyCardVisibility();
            renderCardVisibilityPickers();
        }

        function resetCardVisibility() {
            localStorage.removeItem('omniscrobble_hidden_cards');
            applyCardVisibility();
            renderCardVisibilityPickers();
        }

        function applyCardVisibility() {
            const hidden = getHiddenCards();
            let foucStyle = document.getElementById('fouc-card-style');
            if (!foucStyle) {
                foucStyle = document.createElement('style');
                foucStyle.id = 'fouc-card-style';
                document.head.appendChild(foucStyle);
            }
            if (hidden.length > 0) {
                foucStyle.textContent = hidden.map(id => '#' + id + ' { display: none !important; }').join(' ');
            } else {
                foucStyle.textContent = '';
            }
        }

        function renderCardVisibilityPickers() {
            const container = document.getElementById('settings-card-visibility-grid');
            if (!container) return;
            const hidden = getHiddenCards();

            container.innerHTML = DASHBOARD_CARDS.map(card => {
                const isVisible = !hidden.includes(card.id);
                return `
                    <div class="u-background-bg-surface u-border-1px-solid-var-border-color u-border-radius-8px u-padding-10px-14px u-display-flex u-align-items-center u-justify-content-space-between u-gap-12px">
                        <div class="u-min-width-0 u-flex-1">
                            <div class="u-font-size-13px u-font-weight-600 u-color-text-main u-white-space-nowrap u-overflow-hidden u-text-overflow-ellipsis">
                                ${card.label}
                            </div>
                            <div class="u-font-size-11px u-color-text-muted u-white-space-nowrap u-overflow-hidden u-text-overflow-ellipsis">
                                ${card.desc}
                            </div>
                        </div>
                        <label class="u-position-relative u-display-inline-block u-width-38px u-height-22px u-flex-shrink-0 u-cursor-pointer">
                            <input type="checkbox" ${isVisible ? 'checked' : ''} onchange="setCardVisibility('${card.id}', this.checked)" class="u-opacity-0 u-width-0-c40a0e u-height-0">
                            <span class="card-visibility-track" data-visible="${isVisible}">
                                <span class="card-visibility-thumb"></span>
                            </span>
                        </label>
                    </div>
                `;
            }).join('');
        }

        function openThemeModal() {
            const modal = document.getElementById('theme-modal');
            if (modal) {
                modal.style.display = 'flex';
                renderThemePickers();
            }
        }

        function closeThemeModal() {
            const modal = document.getElementById('theme-modal');
            if (modal) modal.style.display = 'none';
        }

        function openShortcutsModal() {
            const modal = document.getElementById('shortcuts-modal');
            if (modal) modal.style.display = 'flex';
        }

        function closeShortcutsModal() {
            const modal = document.getElementById('shortcuts-modal');
            if (modal) modal.style.display = 'none';
        }

        // Global Keyboard Shortcuts
        document.addEventListener('keydown', function(e) {
            const target = e.target;
            const tag = target.tagName ? target.tagName.toUpperCase() : '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) {
                return;
            }

            if (e.key === 'Escape') {
                if (typeof closeThemeModal === 'function') closeThemeModal();
                if (typeof closeShortcutsModal === 'function') closeShortcutsModal();
                if (typeof closeSettingsModal === 'function') closeSettingsModal();
                if (typeof closeLogsModal === 'function') closeLogsModal();
                if (typeof closeScrobbleModal === 'function') closeScrobbleModal();
                if (typeof closeArrModal === 'function') closeArrModal();
                if (typeof closeAddArrModal === 'function') closeAddArrModal();
                if (typeof closeSimklModal === 'function') closeSimklModal();
                if (typeof closeCrossSyncModal === 'function') closeCrossSyncModal();
                if (typeof closeAnilistModal === 'function') closeAnilistModal();
                if (typeof closeMalModal === 'function') closeMalModal();
                if (typeof closeReconcileModal === 'function') closeReconcileModal();
                if (typeof closeReconcileSettingsModal === 'function') closeReconcileSettingsModal();
                if (typeof closeTestWebhookModal === 'function') closeTestWebhookModal();
                if (typeof closeUnlockModal === 'function') closeUnlockModal();
                return;
            }

            if (e.key === 't' || e.key === 'T') {
                e.preventDefault();
                cycleTheme();
            } else if (e.key === '?' || (e.shiftKey && e.key === '/')) {
                e.preventDefault();
                openShortcutsModal();
            } else if (e.key === 's' || e.key === 'S') {
                e.preventDefault();
                openSettingsModal('servers');
            } else if (e.key === 'l' || e.key === 'L') {
                e.preventDefault();
                openLogsModal();
            } else if (e.key === 'r' || e.key === 'R') {
                e.preventDefault();
                fetchEvents();
            }
        });

        // Initialize Theme, Density & Card Visibility UI on load
        function initAppearance() {
            updateThemeUI();
            updateDensityUI();
            applyCardVisibility();
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initAppearance);
        } else {
            initAppearance();
        }

        // Multi-Server Webhook URL Tab Switching
        function switchWebhookTab(platform) {
            const input = document.getElementById('webhook-url-input');
            const instructions = document.getElementById('webhook-instructions');
            const btnPlex = document.getElementById('btn-tab-plex');
            const btnJelly = document.getElementById('btn-tab-jellyfin');
            const btnEmby = document.getElementById('btn-tab-emby');
            if (!input) return;

            [btnPlex, btnJelly, btnEmby].forEach(b => {
                if (b) b.setAttribute('aria-pressed', 'false');
            });

            if (platform === 'jellyfin') {
                input.value = input.dataset.jellyfin || '';
                if (btnJelly) btnJelly.setAttribute('aria-pressed', 'true');
                if (instructions) instructions.innerHTML = 'In Jellyfin: Install the <strong>Webhook plugin</strong> &rarr; Add Generic Destination with this URL &rarr; Select Playback Start, Progress, Stop.';
            } else if (platform === 'emby') {
                input.value = input.dataset.emby || '';
                if (btnEmby) btnEmby.setAttribute('aria-pressed', 'true');
                if (instructions) instructions.innerHTML = 'In Emby: Go to <strong>Server Settings &rarr; Webhooks &rarr; Add Webhook</strong> with this URL &rarr; Check Playback events.';
            } else {
                input.value = input.dataset.plex || '';
                if (btnPlex) btnPlex.setAttribute('aria-pressed', 'true');
                if (instructions) instructions.innerHTML = 'Add in Plex: <strong>Settings &rarr; Webhooks &rarr; Add Webhook</strong> &bull; Jellyfin (<code>/webhook/jellyfin</code>) &bull; Emby (<code>/webhook/emby</code>) &bull; Sonarr (<code>/sonarr</code>) &bull; Radarr (<code>/radarr</code>).';
            }
        }

        // Two-Way Library Reconciliation Logic
        let activeReconcileServer = 'plex';
        let reconcileDiffItems = [];
        let reconcileActiveFilter = 'all';
        let reconcileSelectedIds = new Set();

        function switchReconcileServer(srv) {
            activeReconcileServer = srv;
            ['plex', 'jellyfin', 'emby'].forEach(s => {
                const btn = document.getElementById(`recon-srv-btn-${s}`);
                if (btn) {
                    if (s === srv) {
                        btn.style.background = s === 'plex' ? 'var(--accent-color)' : s === 'jellyfin' ? '#7c3aed' : 'var(--status-playing)';
                        btn.style.color = 'var(--accent-text)';
                        btn.style.fontWeight = '600';
                        btn.style.borderColor = 'transparent';
                    } else {
                        btn.style.background = 'var(--bg-surface)';
                        btn.style.border = '1px solid var(--border-color)';
                        btn.style.color = 'var(--text-heading)';
                        btn.style.fontWeight = 'normal';
                    }
                }
            });

            const srvDisplayName = srv === 'plex' ? 'Plex' : srv === 'jellyfin' ? 'Jellyfin' : 'Emby';
            const colHeader = document.getElementById('reconcile-server-col-header');
            if (colHeader) colHeader.innerText = `${srvDisplayName} Server`;

            const labelDest = document.getElementById('label-filter-dest');
            if (labelDest) labelDest.innerText = srvDisplayName;

            const labelSrc = document.getElementById('label-filter-src');
            if (labelSrc) labelSrc.innerText = srvDisplayName;

            fetchDiscrepancies(false, srv);
        }

        function openReconcileModal(force = false, server = null) {
            const modal = document.getElementById('reconcile-modal');
            if (!modal) return;
            modal.style.display = 'flex';
            if (server && ['plex', 'jellyfin', 'emby'].includes(String(server).toLowerCase())) {
                switchReconcileServer(String(server).toLowerCase());
            } else {
                fetchDiscrepancies(force, activeReconcileServer);
            }
        }

        function closeReconcileModal() {
            const modal = document.getElementById('reconcile-modal');
            if (modal) modal.style.display = 'none';
        }

        async function fetchDiscrepancies(force = false, srv = null) {
            const targetSrv = srv || activeReconcileServer || 'plex';
            const tbody = document.getElementById('reconcile-tbody');
            const badge = document.getElementById('reconcile-count-badge');
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="6" class="u-text-align-center u-padding-24px u-color-accent-color">Scanning ${targetSrv.toUpperCase()} & Trakt libraries...</td></tr>`;
            }
            if (badge) badge.innerText = 'Scanning...';

            try {
                const base = isDemo ? '/api/sync/diff?demo=true' : '/api/sync/diff';
                const sep = base.includes('?') ? '&' : '?';
                const url = `${base}${sep}server=${targetSrv}${force ? '&force=true' : ''}`;
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    reconcileDiffItems = data.diff || [];
                    reconcileSelectedIds.clear();
                    updateReconcileCounts();
                    renderDiscrepanciesTable();
                    const cardBadge = document.getElementById('reconcile-diff-badge');
                    if (cardBadge) cardBadge.innerText = reconcileDiffItems.length;
                } else {
                    const err = await res.json();
                    if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="u-text-align-center u-padding-24px u-color-ef4444">Failed to scan discrepancies: ${escapeHtml(err.detail || 'Error')}</td></tr>`;
                    if (badge) badge.innerText = 'Error';
                }
            } catch (e) {
                if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="u-text-align-center u-padding-24px u-color-ef4444">Error connecting to server: ${escapeHtml(e.message)}</td></tr>`;
                if (badge) badge.innerText = 'Error';
            }
        }

        function updateReconcileCounts() {
            const allCount = reconcileDiffItems.length;
            const traktCount = reconcileDiffItems.filter(i => i.status === 'trakt_only').length;
            const srvCount = reconcileDiffItems.filter(i => i.status === 'plex_only' || i.status === 'jellyfin_only' || i.status === 'emby_only' || i.status === 'server_only').length;
            const ratingCount = reconcileDiffItems.filter(i => i.status === 'rating_mismatch').length;

            const cAll = document.getElementById('count-all');
            const cTrakt = document.getElementById('count-trakt');
            const cPlex = document.getElementById('count-plex');
            const cRatings = document.getElementById('count-ratings');
            const badge = document.getElementById('reconcile-count-badge');

            if (cAll) cAll.innerText = allCount;
            if (cTrakt) cTrakt.innerText = traktCount;
            if (cPlex) cPlex.innerText = srvCount;
            if (cRatings) cRatings.innerText = ratingCount;
            if (badge) badge.innerText = `${allCount} Discrepanc${allCount === 1 ? 'y' : 'ies'}`;
        }

        function filterReconcile(filterType, tabBtn) {
            reconcileActiveFilter = filterType;
            document.querySelectorAll('.reconcile-tab').forEach(b => {
                b.style.background = 'var(--bg-surface)';
                b.style.color = 'var(--text-heading)';
                b.style.border = '1px solid var(--border-color)';
            });
            if (tabBtn) {
                tabBtn.style.background = 'var(--accent-color)';
                tabBtn.style.color = 'var(--accent-text)';
                tabBtn.style.border = 'none';
            }
            renderDiscrepanciesTable();
        }

        function renderDiscrepanciesTable() {
            const tbody = document.getElementById('reconcile-tbody');
            if (!tbody) return;

            let items = reconcileDiffItems;
            if (reconcileActiveFilter === 'server_only') {
                items = items.filter(i => i.status === 'plex_only' || i.status === 'jellyfin_only' || i.status === 'emby_only' || i.status === 'server_only');
            } else if (reconcileActiveFilter !== 'all') {
                items = items.filter(i => i.status === reconcileActiveFilter);
            }

            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="u-text-align-center u-padding-28px u-color-10b981 u-font-weight-500">✓ In sync! No discrepancies found in this category.</td></tr>';
                updateSelectedCount();
                return;
            }

            let html = '';
            for (const item of items) {
                const isChecked = reconcileSelectedIds.has(item.id);
                const titleStr = escapeHtml(item.type === 'episode'
                    ? `${item.series_title} S${String(item.season).padStart(2, '0')}E${String(item.episode).padStart(2, '0')} • ${item.title}`
                    : `${item.title} (${item.year || 'N/A'})`);

                const srvWatched = item.server_watched !== undefined ? item.server_watched : item.plex_watched;
                const srvRating = item.server_rating !== undefined ? item.server_rating : item.plex_rating;

                let srvStatusHtml = '';
                if (srvWatched) {
                    srvStatusHtml = '<span class="u-color-10b981 u-font-weight-600">✓ Watched</span>';
                } else {
                    srvStatusHtml = '<span class="u-color-text-muted">Unwatched</span>';
                }
                if (srvRating !== null && srvRating !== undefined) {
                    srvStatusHtml += ` <span class="u-background-bg-surface u-border-1px-solid-var-border-color u-color-facc15 u-padding-1px-5px u-border-radius-4px u-font-size-11px">★ ${escapeHtml(srvRating)}</span>`;
                }

                let traktStatusHtml = '';
                if (item.trakt_watched) {
                    traktStatusHtml = '<span class="u-color-10b981 u-font-weight-600">✓ Watched</span>';
                } else {
                    traktStatusHtml = '<span class="u-color-text-muted">Unwatched</span>';
                }
                if (item.trakt_rating !== null && item.trakt_rating !== undefined) {
                    traktStatusHtml += ` <span class="u-background-bg-surface u-border-1px-solid-var-border-color u-color-facc15 u-padding-1px-5px u-border-radius-4px u-font-size-11px">★ ${escapeHtml(item.trakt_rating)}</span>`;
                }

                let actionBadge = '';
                const itemSrvName = (item.server || activeReconcileServer || 'Plex');
                const capSrv = escapeHtml(itemSrvName.charAt(0).toUpperCase() + itemSrvName.slice(1));
                if (item.action_recommended && item.action_recommended.startsWith('mark_')) {
                    actionBadge = `<span class="u-background-065f46 u-color-a7f3d0 u-border-1px-solid-059669 u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">&rarr; Mark ${capSrv}</span>`;
                } else if (item.action_recommended === 'sync_to_trakt') {
                    actionBadge = '<span class="u-background-1e3a8a u-color-93c5fd u-border-1px-solid-var-accent-color u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">&rarr; Push Trakt</span>';
                } else {
                    actionBadge = '<span class="u-background-78350f u-color-fde68a u-border-1px-solid-d97706 u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">Sync Rating</span>';
                }

                html += `
                    <tr class="u-border-bottom-1px-solid-1e293b">
                    <td class="u-text-align-center u-padding-10px-14px">
                        <input type="checkbox" class="reconcile-item-cb" data-id="${escapeHtml(String(item.id ?? ''))}" ${isChecked ? 'checked' : ''} onchange="onReconcileItemCheckboxChange(this)" />
                    </td>
                    <td class="u-padding-10px-14px u-color-text-main u-font-weight-500 u-font-size-13px">${titleStr}</td>
                    <td class="u-padding-10px-14px"><span class="u-background-bg-page u-color-93c5fd u-padding-2px-6px u-border-radius-4px u-font-size-11px u-text-transform-uppercase">${escapeHtml(item.type)}</span></td>
                    <td class="u-padding-10px-14px u-font-size-13px">${srvStatusHtml}</td>
                    <td class="u-padding-10px-14px u-font-size-13px">${traktStatusHtml}</td>
                    <td class="u-padding-10px-14px">${actionBadge}</td>
                </tr>
                `;
            }

            tbody.innerHTML = html;
            updateSelectedCount();
        }

        function toggleSelectAllReconcile(masterCb) {
            const isChecked = masterCb.checked;
            let items = reconcileDiffItems;
            if (reconcileActiveFilter === 'server_only') {
                items = items.filter(i => i.status === 'plex_only' || i.status === 'jellyfin_only' || i.status === 'emby_only' || i.status === 'server_only');
            } else if (reconcileActiveFilter !== 'all') {
                items = items.filter(i => i.status === reconcileActiveFilter);
            }
            items.forEach(item => {
                if (isChecked) reconcileSelectedIds.add(item.id);
                else reconcileSelectedIds.delete(item.id);
            });
            renderDiscrepanciesTable();
        }

        function onReconcileItemCheckboxChange(cb) {
            const id = cb.dataset.id;
            if (cb.checked) reconcileSelectedIds.add(id);
            else reconcileSelectedIds.delete(id);
            updateSelectedCount();
        }

        function updateSelectedCount() {
            const countEl = document.getElementById('reconcile-selected-count');
            const syncBtn = document.getElementById('reconcile-selected-btn');
            const count = reconcileSelectedIds.size;
            if (countEl) countEl.innerText = count;
            if (syncBtn) {
                syncBtn.disabled = count === 0;
                syncBtn.innerText = `⚡ Sync Selected (${count})`;
            }
        }

        async function executeSelectedReconcile() {
            const ids = Array.from(reconcileSelectedIds);
            if (ids.length === 0) return;
            await runReconciliation({ item_ids: ids, server: activeReconcileServer });
        }

        async function executeAllReconcile() {
            if (reconcileDiffItems.length === 0) {
                alert('No discrepancies to reconcile.');
                return;
            }
            const srvDisplayName = activeReconcileServer.charAt(0).toUpperCase() + activeReconcileServer.slice(1);
            if (!confirm(`Reconcile all ${reconcileDiffItems.length} discrepancies between ${srvDisplayName} and Trakt?`)) return;
            await runReconciliation({ direction: 'all', server: activeReconcileServer });
        }

        async function quickReconcileTraktToPlex(btn, server = null) {
            const srv = server || activeReconcileServer || 'plex';
            const capSrv = srv.charAt(0).toUpperCase() + srv.slice(1);
            if (btn) {
                btn.disabled = true;
                btn.innerText = 'Syncing...';
            }
            try {
                const url = isDemo ? '/api/sync/reconcile?demo=true' : '/api/sync/reconcile';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ direction: 'trakt_to_server', server: srv }),
                });
                if (res.ok) {
                    const data = await res.json();
                    if (btn) {
                        btn.innerText = `✓ Synced (${data.reconciled || 0})`;
                        btn.style.background = 'var(--status-playing)';
                    }
                    const badge = document.getElementById('reconcile-diff-badge');
                    if (badge) {
                        const cur = parseInt(badge.innerText, 10) || 0;
                        badge.innerText = Math.max(0, cur - (data.reconciled || 0));
                    }
                    setTimeout(() => {
                        if (btn) {
                            btn.innerText = `⚡ Quick Sync (Trakt → ${capSrv})`;
                            btn.style.background = 'var(--status-playing)';
                            btn.disabled = false;
                        }
                    }, 2500);
                } else {
                    const err = await res.json();
                    alert('Reconciliation failed: ' + (err.detail || 'Error'));
                    if (btn) {
                        btn.innerText = 'Error';
                        btn.style.background = 'var(--status-error)';
                        btn.disabled = false;
                    }
                }
            } catch (e) {
                alert('Error: ' + e.message);
                if (btn) btn.disabled = false;
            }
        }

        async function runReconciliation(payload) {
            const pBox = document.getElementById('reconcile-progress-box');
            const pBar = document.getElementById('reconcile-progress-bar');
            const pMsg = document.getElementById('reconcile-progress-msg');
            const pStats = document.getElementById('reconcile-progress-stats');
            const syncBtn = document.getElementById('reconcile-selected-btn');
            const allBtn = document.getElementById('reconcile-all-btn');

            if (pBox) pBox.style.display = 'block';
            if (pBar) pBar.style.width = '10%';
            if (pMsg) pMsg.innerText = 'Starting reconciliation...';
            if (pStats) pStats.innerText = 'Processing';
            if (syncBtn) syncBtn.disabled = true;
            if (allBtn) allBtn.disabled = true;

            try {
                const previewUrl = isDemo ? '/api/sync/reconcile/preview?demo=true' : '/api/sync/reconcile/preview';
                const previewResponse = await fetch(previewUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const preview = await previewResponse.json();
                if (!previewResponse.ok) throw new Error(preview.detail || 'Preview failed');
                if (!preview.total) {
                    if (pMsg) pMsg.innerText = 'Preview found no eligible changes.';
                    if (pBar) pBar.style.width = '100%';
                    return;
                }
                const actionSummary = Object.entries(preview.actions || {}).map(([action, count]) => `${count} ${action.replaceAll('_', ' ')}`).join(', ');
                const uncertain = (preview.warnings || []).map(item => item.title).filter(Boolean);
                const caution = uncertain.length ? `\n\n${uncertain.length} item(s) rely on title matching and need careful review: ${uncertain.slice(0, 5).join(', ')}${uncertain.length > 5 ? ', …' : ''}.` : '';
                if (!confirm(`Review ${preview.total} proposed reconciliation change(s): ${actionSummary}.${caution}\n\nApply this exact preview?`)) {
                    if (pMsg) pMsg.innerText = 'Preview reviewed; no changes applied.';
                    if (pBar) pBar.style.width = '100%';
                    return;
                }
                const url = isDemo ? '/api/sync/reconcile?demo=true' : '/api/sync/reconcile';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preview_id: preview.preview_id, server: payload.server }),
                });
                if (res.ok) {
                    const data = await res.json();
                    if (pBar) pBar.style.width = '100%';
                    if (pMsg) pMsg.innerText = `Reconciliation completed: ${data.reconciled || 0} succeeded, ${data.failed || 0} failed.`;
                    if (pStats) pStats.innerText = '100%';
                    setTimeout(async () => {
                        if (pBox) pBox.style.display = 'none';
                        await fetchDiscrepancies(false);
                    }, 2000);
                } else {
                    const err = await res.json();
                    if (pMsg) pMsg.innerText = 'Reconciliation error: ' + (err.detail || 'Failed');
                    if (pBar) pBar.style.background = 'var(--status-error)';
                }
            } catch (e) {
                if (pMsg) pMsg.innerText = 'Network error: ' + e.message;
                if (pBar) pBar.style.background = 'var(--status-error)';
            } finally {
                if (allBtn) allBtn.disabled = false;
            }
        }

        // Arr Watchlist Automation & Ecosystem
        function openArrModal() {
            const modal = document.getElementById('arr-modal');
            if (modal) modal.style.display = 'flex';
            fetchArrStatus();
        }

        let arrAddType = 'series';
        let arrAddCandidate = null;
        let arrAddTimer = null;
        let arrAddConfig = null;
        let arrAddConfigPromise = null;
        function loadArrAddConfig(forceRefresh = false) {
            if (forceRefresh) { arrAddConfig = null; arrAddConfigPromise = null; }
            if (arrAddConfig) return Promise.resolve(arrAddConfig);
            if (!arrAddConfigPromise) {
                arrAddConfigPromise = fetch('/api/arr/config' + (isDemo ? '?demo=true' : ''))
                    .then(async response => {
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || 'Could not load Sonarr/Radarr options');
                        arrAddConfig = data;
                        const label = document.getElementById('arr-add-cowatch-label');
                        if (label && data.co_watch_user) label.textContent = `Enable Co-Watching with @${data.co_watch_user} (optional)`;
                        updateArrAddOptionVisibility();
                        return data;
                    })
                    .catch(error => { arrAddConfigPromise = null; throw error; });
            }
            return arrAddConfigPromise;
        }
