        function updateArrAddOptionVisibility() {
            const wrap = document.getElementById('arr-add-cowatch-wrap');
            if (wrap) wrap.style.display = arrAddType === 'series' && Boolean(arrAddConfig && arrAddConfig.co_watch_user) ? 'inline-flex' : 'none';
        }
        function openAddArrModal(mediaType, initialTerm) {
            if (!isAdmin && !isDemo) { openUnlockModal(); return; }
            const modal = document.getElementById('add-arr-modal');
            if (modal) modal.style.display = 'flex';
            const searchInput = document.getElementById('arr-add-search');
            if (mediaType === 'series' || mediaType === 'movie') setArrAddType(mediaType);
            if (initialTerm) searchInput.value = initialTerm;
            else if (!mediaType) searchInput.value = '';
            searchInput.focus();
            loadArrAddConfig().catch(error => {
                const message = document.getElementById('arr-add-message');
                if (message) message.textContent = error.message;
            });
            if (!mediaType) setArrAddType('series');
            if (initialTerm) scheduleArrLookup();
        }
        function closeAddArrModal() { const modal = document.getElementById('add-arr-modal'); if (modal) modal.style.display = 'none'; }
        function setArrAddType(type) {
            arrAddType = type; arrAddCandidate = null;
            document.getElementById('arr-kind-series').style.background = type === 'series' ? 'var(--accent-hover)' : 'var(--bg-surface)';
            document.getElementById('arr-kind-movie').style.background = type === 'movie' ? 'var(--accent-hover)' : 'var(--bg-surface)';
            updateArrAddOptionVisibility();
            document.getElementById('arr-add-monitor-mode-wrap').style.display = type === 'series' ? 'inline-flex' : 'none';
            document.getElementById('arr-add-options').style.display = 'none';
            scheduleArrLookup();
        }
        function scheduleArrLookup() { clearTimeout(arrAddTimer); arrAddTimer = setTimeout(fetchArrCandidates, 350); }
        async function fetchArrCandidates() {
            const term = document.getElementById('arr-add-search').value.trim();
            const box = document.getElementById('arr-add-results');
            if (!term) { box.innerHTML = ''; return; }
            box.innerHTML = '<div class="u-color-text-muted u-padding-10px">Searching…</div>';
            try {
                const res = await fetch(`/api/arr/lookup?type=${arrAddType}&term=${encodeURIComponent(term)}${isDemo ? '&demo=true' : ''}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Search failed');
                if (!data.results.length) { box.innerHTML = '<div class="u-color-text-muted u-padding-10px">No results found (or this service is not configured).</div>'; return; }
                box.innerHTML = data.results.map((item, i) => {
                    const poster = /^https?:\/\//i.test(item.poster_url || '') ? `<img src="${escapeHtml(item.poster_url)}" alt="" loading="lazy" class="u-width-48px u-height-68px u-object-fit-cover u-border-radius-5px u-background-bg-surface">` : `<span class="u-font-size-24px u-width-48px u-text-align-center u-align-self-center">${arrAddType === 'series' ? '📺' : '🎬'}</span>`;
                    const studio = item.network ? `<small class="u-display-block u-color-accent-color">${escapeHtml(item.network)}</small>` : '';
                    return `<button onclick="selectArrCandidate(${i})" ${item.in_library ? 'disabled' : ''} class="arr-candidate-button${item.in_library ? ' is-in-library' : ''}">${poster}<span><strong>${escapeHtml(item.title)}${item.year ? ` (${item.year})` : ''}</strong>${studio}<small class="u-display-block u-color-text-muted">${escapeHtml(item.overview || '')}</small></span><span class="arr-candidate-status${item.in_library ? ' is-in-library' : ''}">${item.in_library ? 'In Library' : 'Select'}</span></button>`;
                }).join('');
                window.arrAddResults = data.results;
            } catch (err) { box.innerHTML = `<div class="u-color-f87171 u-padding-10px">${escapeHtml(err.message)}</div>`; }
        }
        async function selectArrCandidate(index) {
            arrAddCandidate = window.arrAddResults[index];
            const message = document.getElementById('arr-add-message');
            message.style.color = 'var(--text-muted)';
            message.textContent = 'Loading service options…';
            let config;
            try { config = await loadArrAddConfig(); }
            catch (error) { message.style.color = 'var(--status-error)'; message.textContent = error.message; return; }
            const opts = config[arrAddType === 'series' ? 'sonarr' : 'radarr'];
            if (!opts || !opts.configured) { document.getElementById('arr-add-message').textContent = 'Configure this service in Settings first.'; return; }
            const fill = (id, rows, key, labelKey, defaultValue) => {
                const el = document.getElementById(id); el.innerHTML = (rows || []).map(x => `<option value="${escapeHtml(String(x[key] ?? ''))}" ${String(x[key]) === String(defaultValue) ? 'selected' : ''}>${escapeHtml(String(x[labelKey] || x[key]))}</option>`).join('');
            };
            fill('arr-add-root', opts.root_folders, 'path', 'path', opts.default_root || (opts.root_folders[0] || {}).path);
            fill('arr-add-profile', opts.quality_profiles, 'id', 'name', opts.default_profile || (opts.quality_profiles[0] || {}).id);
            document.getElementById('arr-add-selected').textContent = `${arrAddCandidate.title}${arrAddCandidate.year ? ` (${arrAddCandidate.year})` : ''}`;
            document.getElementById('arr-add-message').textContent = '';
            document.getElementById('arr-add-cowatch').checked = false;
            document.getElementById('arr-add-options').style.display = 'block';
        }
        async function submitArrAdd() {
            if (!arrAddCandidate) return;
            const btn = document.getElementById('arr-add-submit'), msg = document.getElementById('arr-add-message');
            btn.disabled = true; msg.textContent = 'Adding…';
            try {
                const res = await fetch('/api/arr/add' + (isDemo ? '?demo=true' : ''), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:arrAddType,item_data:arrAddCandidate.payload,root_folder_path:document.getElementById('arr-add-root').value,quality_profile_id:Number(document.getElementById('arr-add-profile').value),monitored:document.getElementById('arr-add-monitored').checked,monitor_option:document.getElementById('arr-add-monitor-mode').value,search_now:document.getElementById('arr-add-search-now').checked,enable_cowatch:arrAddType === 'series' && document.getElementById('arr-add-cowatch').checked})});
                const data = await res.json(); if (!res.ok) throw new Error(data.detail || 'Addition failed');
                msg.style.color = 'var(--status-playing)'; msg.textContent = data.cowatch_enrolled ? 'Added and enrolled in Co-Watch.' : 'Added successfully.';
                document.getElementById('arr-add-options').style.display = 'none';
            } catch (err) { msg.style.color = 'var(--status-error)'; msg.textContent = err.message; }
            finally { btn.disabled = false; }
        }

        function closeArrModal() {
            const modal = document.getElementById('arr-modal');
            if (modal) modal.style.display = 'none';
        }

        async function fetchArrStatus() {
            const container = document.getElementById('arr-modal-content');
            const badge = document.getElementById('arr-modal-status-badge');
            try {
                const url = isDemo ? '/api/arr/status?demo=true' : '/api/arr/status';
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to fetch status');
                const data = await res.json();
                renderArrModalContent(data);
                if (badge) {
                    badge.innerText = data.is_syncing ? 'Syncing...' : 'Ready';
                    badge.style.color = data.is_syncing ? 'var(--status-paused)' : 'var(--status-finished)';
                }
            } catch (err) {
                if (container) container.innerHTML = `<div class="u-color-f87171 u-text-align-center u-padding-16px">Failed to load Arr status: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderArrModalContent(data) {
            const container = document.getElementById('arr-modal-content');
            if (!container) return;
            const res = data.last_result;
            if (!res || !res.items || res.items.length === 0) {
                container.innerHTML = '<div class="u-color-text-muted u-text-align-center u-padding-24px u-font-size-13px">No items processed in the most recent sync. Click "Sync Watchlist Now" to poll Trakt.</div>';
                return;
            }

            let htmlStr = '';
            for (const item of res.items) {
                const isAdded = item.status === 'added';
                const stText = isAdded ? 'Added' : (item.reason || 'Skipped');
                const appName = item.app || (item.type === 'movie' ? 'Radarr' : 'Sonarr');
                const yearStr = item.year ? ` (${escapeHtml(item.year)})` : '';
                const typeIcon = item.type === 'movie' ? '🍿' : '📺';

                htmlStr += `
                <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-10px-14px u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px">
                    <div class="u-display-flex u-align-items-center u-gap-10px">
                        <span>${typeIcon}</span>
                        <div>
                            <strong class="u-color-text-main u-font-size-13px">${escapeHtml(item.title)}${yearStr}</strong>
                            <div class="u-color-text-muted u-font-size-11px">Target: ${escapeHtml(appName)} &bull; ${escapeHtml(item.type || 'media')}</div>
                        </div>
                    </div>
                    <span class="arr-sync-status ${isAdded ? 'is-added' : 'is-skipped'}">
                        ${escapeHtml(stText)}
                    </span>
                </div>
                `;
            }
            container.innerHTML = htmlStr;
        }

        async function triggerArrWatchlistSync(btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const modalBtn = document.getElementById('arr-modal-sync-btn');
            const targetBtn = btn || modalBtn;
            const origText = targetBtn ? targetBtn.innerHTML : '';
            if (targetBtn) {
                targetBtn.disabled = true;
                targetBtn.innerHTML = '⚡ Syncing...';
            }

            try {
                const url = isDemo ? '/api/arr/sync?demo=true' : '/api/arr/sync';
                const res = await fetch(url, { method: 'POST' });
                if (res.status === 401) {
                    openUnlockModal();
                    return;
                }
                const data = await res.json();
                if (targetBtn) {
                    targetBtn.innerHTML = '✓ Synced!';
                    targetBtn.style.background = 'var(--status-playing)';
                }
                setTimeout(() => {
                    if (targetBtn) {
                        targetBtn.innerHTML = origText;
                        targetBtn.style.background = 'var(--status-playing)';
                        targetBtn.disabled = false;
                    }
                }, 2000);
                fetchArrStatus();
            } catch (err) {
                alert('Sync failed: ' + err.message);
                if (targetBtn) {
                    targetBtn.innerHTML = origText;
                    targetBtn.disabled = false;
                }
            }
        }

        // Simkl Multi-Tracker Functions
