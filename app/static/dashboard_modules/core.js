        const isAdmin = window.dashboardConfig.isAdmin;
        const isDemo = window.dashboardConfig.isDemo;
        const isMember = window.dashboardConfig.isMember === true;
        let refreshTimer = null;

        function copyWebhookUrl() {
            const input = document.getElementById('webhook-url-input');
            if (!input) return;
            const btn = document.getElementById('copy-btn');
            const showSuccess = () => {
                if (!btn) return;
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Copied!';
                btn.style.background = 'var(--status-playing)';
                setTimeout(() => {
                    btn.innerHTML = orig;
                    btn.style.background = 'var(--accent-color)';
                }, 2000);
            };

            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(input.value).then(showSuccess).catch(() => {
                    input.select();
                    document.execCommand('copy');
                    showSuccess();
                });
            } else {
                input.select();
                document.execCommand('copy');
                showSuccess();
            }
        }

        // Clean query token from browser address bar once cookie is saved
        if (window.location.search && window.location.search.includes('token=')) {
            try {
                window.history.replaceState({}, document.title, window.location.pathname);
            } catch (e) {}
        }

        function openUnlockModal() {
            const modal = document.getElementById('unlock-modal');
            modal.style.display = 'flex';
            const input = document.getElementById('admin-secret-input');
            input.value = '';
            document.getElementById('unlock-error').style.display = 'none';
            setTimeout(() => input.focus(), 50);
        }

        function closeUnlockModal() {
            document.getElementById('unlock-modal').style.display = 'none';
        }

        async function submitUnlock() {
            const secret = document.getElementById('admin-secret-input').value.trim();
            const errDiv = document.getElementById('unlock-error');
            const btn = document.getElementById('unlock-submit-btn');
            if (!secret) return;
            errDiv.style.display = 'none';
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Unlocking...';
            }
            try {
                const res = await fetch('/api/admin/unlock', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: secret })
                });
                if (res.ok) {
                    window.location.href = window.location.pathname;
                } else {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = 'Unlock';
                    }
                    const data = await res.json();
                    errDiv.textContent = data.detail || 'Invalid secret token';
                    errDiv.style.display = 'block';
                }
            } catch (e) {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = 'Unlock';
                }
                errDiv.textContent = 'Connection error: ' + e.message;
                errDiv.style.display = 'block';
            }
        }

        window.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                toggleCommandPalette();
                return;
            }
            if (e.key === 'Escape') {
                closeCommandPalette();
                if (typeof closeAccountModal === "function") closeAccountModal();
                closeUnlockModal();
                closeScrobbleModal();
                closeLogsModal();
                closeTestWebhookModal();
                if (typeof closeReconcileModal === 'function') closeReconcileModal();
                if (typeof closeSettingsModal === 'function') closeSettingsModal();
                if (typeof closeReconcileSettingsModal === 'function') closeReconcileSettingsModal();
                if (typeof closeArrModal === 'function') closeArrModal();
                if (typeof closeAddArrModal === 'function') closeAddArrModal();
                if (typeof closeSimklModal === 'function') closeSimklModal();
                if (typeof closeAnilistModal === 'function') closeAnilistModal();
                if (typeof closeMalModal === 'function') closeMalModal();
                if (typeof closeCrossSyncModal === 'function') closeCrossSyncModal();
                const box = document.getElementById('sonarr-suggestions');
                if (box) box.style.display = 'none';
            }
        });

        async function lockAdmin() {
            await fetch('/api/admin/lock', { method: 'POST' });
            window.location.href = window.location.pathname;
        }

        function switchScrobbleTab(tab) {
            const btnSearch = document.getElementById('tab-btn-search');
            const btnDirect = document.getElementById('tab-btn-direct');
            const viewSearch = document.getElementById('scrobble-view-search');
            const viewDirect = document.getElementById('scrobble-view-direct');
            if (tab === 'search') {
                if (btnSearch) {
                    btnSearch.style.background = 'var(--accent-color)';
                    btnSearch.style.color = 'var(--accent-text)';
                    btnSearch.style.fontWeight = '600';
                    btnSearch.style.border = 'none';
                }
                if (btnDirect) {
                    btnDirect.style.background = 'var(--bg-surface)';
                    btnDirect.style.color = 'var(--text-muted)';
                    btnDirect.style.fontWeight = 'normal';
                    btnDirect.style.border = '1px solid var(--border-color)';
                }
                if (viewSearch) viewSearch.style.display = 'block';
                if (viewDirect) viewDirect.style.display = 'none';
                setTimeout(() => document.getElementById('scrobble-search-input')?.focus(), 50);
            } else {
                if (btnDirect) {
                    btnDirect.style.background = 'var(--accent-color)';
                    btnDirect.style.color = 'var(--accent-text)';
                    btnDirect.style.fontWeight = '600';
                    btnDirect.style.border = 'none';
                }
                if (btnSearch) {
                    btnSearch.style.background = 'var(--bg-surface)';
                    btnSearch.style.color = 'var(--text-muted)';
                    btnSearch.style.fontWeight = 'normal';
                    btnSearch.style.border = '1px solid var(--border-color)';
                }
                if (viewSearch) viewSearch.style.display = 'none';
                if (viewDirect) viewDirect.style.display = 'block';
                setTimeout(() => document.getElementById('direct-show-input')?.focus(), 50);
            }
        }

        function getSelectedTrackers() {
            const trks = [];
            if (document.getElementById('scrobble-trk-trakt')?.checked) trks.push('trakt');
            if (document.getElementById('scrobble-trk-simkl')?.checked) trks.push('simkl');
            if (document.getElementById('scrobble-trk-tmdb')?.checked) trks.push('tmdb');
            if (document.getElementById('scrobble-trk-anilist')?.checked) trks.push('anilist');
            if (document.getElementById('scrobble-trk-mal')?.checked) trks.push('mal');
            if (document.getElementById('scrobble-trk-kitsu')?.checked) trks.push('kitsu');
            if (document.getElementById('scrobble-trk-letterboxd')?.checked) trks.push('letterboxd');
            if (document.getElementById('scrobble-trk-serializd')?.checked) trks.push('serializd');
            if (document.getElementById('scrobble-trk-mdblist')?.checked) trks.push('mdblist');
            return trks.length > 0 ? trks : ['trakt'];
        }

        function isCowatchChecked() {
            return document.getElementById('scrobble-cowatch-check')?.checked || false;
        }

        function toggleDirectMediaFields(type) {
            const isEp = type === 'episode';
            const showRow = document.getElementById('direct-show-row');
            const seasonCol = document.getElementById('direct-season-col');
            const episodeCol = document.getElementById('direct-episode-col');
            const idRow = document.getElementById('direct-id-row');
            const titleLabel = document.getElementById('direct-title-label');
            const titleInput = document.getElementById('direct-title-input');
            const startBtn = document.getElementById('direct-start-btn');

            if (showRow) showRow.style.display = isEp ? 'block' : 'none';
            if (seasonCol) seasonCol.style.display = isEp ? 'block' : 'none';
            if (episodeCol) episodeCol.style.display = isEp ? 'block' : 'none';
            if (idRow) idRow.style.display = isEp ? 'none' : 'block';
            if (titleLabel) titleLabel.textContent = isEp ? 'Episode Title (Optional)' : 'Movie Title';
            if (titleInput) titleInput.placeholder = isEp ? 'e.g. Good News About Hell' : 'e.g. Dune: Part Two';
            if (startBtn) startBtn.style.display = isEp ? 'none' : 'inline-flex';
        }

        function openManualScrobbleModal() {
            if (!isAdmin) {
                openUnlockModal();
                return;
            }
            document.getElementById('scrobble-modal').style.display = 'flex';
            const input = document.getElementById('scrobble-search-input');
            if (input) input.value = '';
            const resBox = document.getElementById('scrobble-search-results');
            if (resBox) resBox.innerHTML = '<div class="u-color-text-muted u-text-align-center u-padding-24px u-font-size-13px">Type a title above and press Search</div>';
            cancelDisambiguation();
            switchScrobbleTab('search');

            // Reset defaults: only configured trackers checked, co-watch partner unchecked
            const cowatchChk = document.getElementById('scrobble-cowatch-check');
            if (cowatchChk) cowatchChk.checked = false;

            const defaultTrackers = {
                'scrobble-trk-trakt': window.dashboardConfig.trackerDefaults.trakt,
                'scrobble-trk-simkl': window.dashboardConfig.trackerDefaults.simkl,
                'scrobble-trk-tmdb': window.dashboardConfig.trackerDefaults.tmdb,
                'scrobble-trk-anilist': window.dashboardConfig.trackerDefaults.anilist,
                'scrobble-trk-mal': window.dashboardConfig.trackerDefaults.mal,
                'scrobble-trk-kitsu': window.dashboardConfig.trackerDefaults.kitsu,
                'scrobble-trk-letterboxd': window.dashboardConfig.trackerDefaults.letterboxd,
                'scrobble-trk-serializd': window.dashboardConfig.trackerDefaults.serializd,
                'scrobble-trk-mdblist': window.dashboardConfig.trackerDefaults.mdblist,
            };
            for (const [id, isChecked] of Object.entries(defaultTrackers)) {
                const el = document.getElementById(id);
                if (el) el.checked = isChecked;
            }
        }

        function closeScrobbleModal() {
            document.getElementById('scrobble-modal').style.display = 'none';
            const msg = document.getElementById('direct-status-msg');
            if (msg) msg.style.display = 'none';
            cancelDisambiguation();
        }

        let searchTypeFilter = '';

        function setSearchTypeFilter(type, btn) {
            searchTypeFilter = type;
            document.querySelectorAll('.search-type-pill').forEach(b => {
                b.style.background = 'var(--bg-surface)';
                b.style.border = '1px solid var(--border-color)';
                b.style.color = 'var(--text-muted)';
            });
            btn.style.background = 'var(--accent-color)';
            btn.style.border = 'none';
            btn.style.color = 'var(--accent-text)';

            const query = document.getElementById('scrobble-search-input')?.value.trim();
            if (query) {
                executeTraktSearch();
            }
        }

        async function executeTraktSearch() {
            const query = document.getElementById('scrobble-search-input').value.trim();
            if (!query) return;
            const loading = document.getElementById('scrobble-search-loading');
            const resultsContainer = document.getElementById('scrobble-search-results');
            loading.style.display = 'block';
            resultsContainer.innerHTML = '';

            try {
                let url = isDemo ? '/api/search?demo=true&query=' + encodeURIComponent(query) : '/api/search?query=' + encodeURIComponent(query);
                if (searchTypeFilter) {
                    url += '&type=' + encodeURIComponent(searchTypeFilter);
                }
                const res = await fetch(url);
                loading.style.display = 'none';
                if (!res.ok) throw new Error('Search failed');
                const data = await res.json();
                renderSearchResults(data.results);
            } catch (err) {
                loading.style.display = 'none';
                resultsContainer.innerHTML = '<div class="u-color-ef4444 u-text-align-center u-padding-12px">' + escapeHtml(err.message) + '</div>';
            }
        }

        function renderSearchResults(results) {
            const container = document.getElementById('scrobble-search-results');
            if (!results || results.length === 0) {
                container.innerHTML = '<div class="u-color-text-muted u-text-align-center u-padding-24px u-font-size-13px">No results found on Trakt.</div>';
                return;
            }
            let html = '';
            for (const item of results) {
                const type = item.type || (item.movie ? 'movie' : 'show');
                if (searchTypeFilter && type !== searchTypeFilter) continue;
                const media = item.movie || item.show || item;
                const title = escapeHtml(media.title || '');
                const year = media.year ? `(${escapeHtml(media.year)})` : '';
                const payloadData = {
                    media_type: type,
                    title: media.title,
                    year: media.year,
                    ids: media.ids || {}
                };
                const mediaJson = encodeURIComponentForInlineJs(JSON.stringify(payloadData));

                const startBtn = type === 'movie' ? `
                    <button onclick="submitManualScrobble('${mediaJson}', this, 'start')" class="btn-sm u-background-accent-color u-color-accent-text u-font-weight-600 u-white-space-nowrap u-padding-7px-11px" title="Start playback (watching now)">
                        ▶️ Started
                    </button>
                ` : '';

                html += `
                <div class="scrobble-item">
                    <div>
                        <div class="u-display-flex u-align-items-center u-gap-8px">
                            <span class="u-background-border-color u-color-93c5fd u-font-size-11px u-font-weight-600 u-padding-2px-6px u-border-radius-4px u-text-transform-uppercase">${escapeHtml(type)}</span>
                            <span class="u-font-weight-600 u-color-text-main u-font-size-14px">${title} ${year}</span>
                        </div>
                        ${media.overview ? `<p class="u-color-text-muted u-font-size-12px u-margin-4px-0-0 u-line-height-1-3 u-display-webkit-box u-webkit-line-clamp-2 u-webkit-box-orient-vertical u-overflow-hidden">${escapeHtml(media.overview)}</p>` : ''}
                    </div>
                    <div class="u-display-flex u-gap-6px u-flex-wrap-wrap u-align-items-center">
                        <button onclick="submitAddToWatchlist('${mediaJson}', this)" class="btn-sm u-background-accent-color u-color-accent-text u-font-weight-600 u-white-space-nowrap u-padding-7px-11px" title="Add to Trakt Watchlist">
                            🔖 Watchlist
                        </button>
                        ${startBtn}
                        <button onclick="submitManualScrobble('${mediaJson}', this, 'watched')" class="btn-sm u-background-10b981 u-color-accent-text u-font-weight-600 u-white-space-nowrap u-padding-7px-11px">
                            ✓ Watched
                        </button>
                        <button onclick="submitSearchUnscrobble('${mediaJson}', this)" class="btn-sm u-background-7f1d1d u-border-1px-solid-ef4444 u-color-fee2e2 u-font-weight-600 u-white-space-nowrap u-padding-7px-11px" title="Remove / Unscrobble from trackers">
                            🗑️ Remove
                        </button>
                    </div>
                </div>`;
            }
            container.innerHTML = html || '<div class="u-color-text-muted u-text-align-center u-padding-24px u-font-size-13px">No results found matching filter.</div>';
        }

        async function submitAddToWatchlist(mediaJsonEncoded, btn) {
            try {
                const payload = JSON.parse(decodeURIComponent(mediaJsonEncoded));
                btn.disabled = true;
                btn.innerHTML = 'Adding...';
                const url = isDemo ? '/api/watchlist?demo=true' : '/api/watchlist';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    btn.innerHTML = '✓ Watchlisted!';
                    btn.style.background = 'var(--status-playing)';
                } else {
                    const err = await res.json();
                    btn.innerHTML = 'Error';
                    btn.style.background = 'var(--status-error)';
                    btn.disabled = false;
                    alert('Failed to add to watchlist: ' + (err.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
                btn.disabled = false;
            }
        }

        async function submitManualScrobble(mediaJsonEncoded, btn, action = 'watched') {
            const isStart = action === 'start';
            try {
                const payload = JSON.parse(decodeURIComponent(mediaJsonEncoded));
                btn.disabled = true;
                btn.innerHTML = isStart ? 'Starting...' : 'Syncing...';
                const url = isDemo ? '/api/scrobble/manual?demo=true' : '/api/scrobble/manual';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: action,
                        media: payload,
                        trackers: getSelectedTrackers(),
                        cowatch: isCowatchChecked()
                    })
                });
                if (res.ok) {
                    btn.innerHTML = isStart ? '▶️ Started!' : '✓ Watched!';
                    btn.style.background = isStart ? 'var(--accent-color)' : 'var(--status-playing)';
                    fetchEvents();
                    fetchPlayback();
                    setTimeout(() => closeScrobbleModal(), 1200);
                } else {
                    const err = await res.json();
                    btn.innerHTML = 'Error';
                    btn.style.background = 'var(--status-error)';
                    alert('Failed to scrobble: ' + (err.detail || 'Unknown error'));
                    btn.disabled = false;
                }
            } catch (e) {
                alert('Error: ' + e.message);
                btn.disabled = false;
            }
        }

        async function submitSearchUnscrobble(mediaJsonEncoded, btn) {
            try {
                if (!confirm('Remove / Unscrobble this item across selected trackers?')) return;
                const payload = JSON.parse(decodeURIComponent(mediaJsonEncoded));
                btn.disabled = true;
                btn.innerHTML = 'Removing...';
                const url = isDemo ? '/api/history/remove?demo=true' : '/api/history/remove';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        media: payload,
                        trackers: getSelectedTrackers(),
                        cowatch: isCowatchChecked()
                    })
                });
                if (res.ok) {
                    btn.innerHTML = '✓ Removed!';
                    btn.style.background = 'var(--border-color)';
                    fetchEvents();
                    setTimeout(() => closeScrobbleModal(), 1200);
                } else {
                    const err = await res.json();
                    btn.innerHTML = 'Error';
                    btn.style.background = 'var(--status-error)';
                    alert('Failed to unscrobble: ' + (err.detail || 'Unknown error'));
                    btn.disabled = false;
                }
            } catch (e) {
                alert('Error: ' + e.message);
                btn.disabled = false;
            }
        }

        function cancelDisambiguation() {
            const container = document.getElementById('scrobble-disambig-container');
            if (container) container.style.display = 'none';
            const form = document.getElementById('direct-scrobble-form');
            if (form) form.style.display = 'flex';
        }

        async function submitDirectScrobble(action = 'watched', selectedCandidate = null) {
            const isStart = action === 'start';
            const btn = isStart ? document.getElementById('direct-start-btn') : document.getElementById('direct-submit-btn');
            const msgBox = document.getElementById('direct-status-msg');
            const mediaType = document.getElementById('direct-type-select').value;
            const yearVal = document.getElementById('direct-year-input').value.trim();
            const year = yearVal ? parseInt(yearVal, 10) : undefined;
            const idVal = document.getElementById('direct-id-input')?.value.trim() || '';

            let media;
            if (selectedCandidate) {
                // User picked a movie from Option A Disambiguation
                media = {
                    media_type: 'movie',
                    title: selectedCandidate.title,
                    year: selectedCandidate.year,
                    ids: selectedCandidate.ids || {}
                };
                cancelDisambiguation();
            } else if (mediaType === 'episode') {
                const showTitle = document.getElementById('direct-show-input').value.trim();
                const epTitle = document.getElementById('direct-title-input').value.trim();
                const seasonNum = parseInt(document.getElementById('direct-season-input').value, 10) || 1;
                const epNum = parseInt(document.getElementById('direct-episode-input').value, 10) || 1;
                if (!showTitle) {
                    alert('Please enter a Series / Show Title');
                    return;
                }
                media = {
                    media_type: 'episode',
                    show_title: showTitle,
                    title: epTitle || `Episode ${epNum}`,
                    season: seasonNum,
                    episode: epNum,
                    year: year
                };
            } else {
                const movieTitle = document.getElementById('direct-title-input').value.trim();
                if (!movieTitle) {
                    alert('Please enter a Movie Title');
                    return;
                }

                // Parse direct ID if entered
                const ids = {};
                if (idVal) {
                    if (idVal.startsWith('tt')) {
                        ids.imdb = idVal;
                    } else if (!isNaN(parseInt(idVal, 10))) {
                        ids.tmdb = parseInt(idVal, 10);
                    }
                }

                // Option A Disambiguation:
                // If user didn't specify a year or ID, check if multiple matches exist on Trakt
                if (!year && Object.keys(ids).length === 0) {
                    try {
                        const searchUrl = isDemo ? `/api/search?demo=true&query=${encodeURIComponent(movieTitle)}&type=movie` : `/api/search?query=${encodeURIComponent(movieTitle)}&type=movie`;
                        const checkRes = await fetch(searchUrl);
                        if (checkRes.ok) {
                            const checkData = await checkRes.json();
                            const results = (checkData.results || []).filter(r => (r.type === 'movie' || r.movie));
                            if (results.length > 1) {
                                // Show Option A Disambiguation Selector
                                renderDisambiguationList(results, action, movieTitle);
                                return;
                            } else if (results.length === 1) {
                                const cand = results[0].movie || results[0];
                                ids.trakt = cand.ids?.trakt;
                                if (cand.ids?.imdb) ids.imdb = cand.ids.imdb;
                                if (cand.ids?.tmdb) ids.tmdb = cand.ids.tmdb;
                            }
                        }
                    } catch (e) {
                        // Offline or network error: proceed with title
                    }
                }

                media = {
                    media_type: 'movie',
                    title: movieTitle,
                    year: year,
                    ids: ids
                };
            }

            if (btn) {
                btn.disabled = true;
                btn.textContent = isStart ? 'Starting...' : 'Syncing...';
            }
            if (msgBox) msgBox.style.display = 'none';

            try {
                const url = isDemo ? '/api/scrobble/manual?demo=true' : '/api/scrobble/manual';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        action: action,
                        media: media,
                        trackers: getSelectedTrackers(),
                        cowatch: isCowatchChecked()
                    })
                });
                if (res.ok) {
                    if (btn) {
                        btn.textContent = isStart ? '▶️ Started!' : '✓ Watched!';
                        btn.style.background = isStart ? 'var(--accent-color)' : 'var(--status-playing)';
                    }
                    if (msgBox) {
                        msgBox.style.display = 'block';
                        msgBox.style.background = 'var(--status-success-bg)';
                        msgBox.style.color = 'var(--status-success-text)';
                        msgBox.textContent = isStart
                            ? `▶️ Playback started for "${media.title}". Visible in Active Sessions!`
                            : `✓ Successfully marked "${media.title}" as watched across selected trackers!`;
                    }
                    fetchEvents();
                    fetchPlayback();
                    setTimeout(() => closeScrobbleModal(), 1400);
                } else {
                    const err = await res.json();
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = isStart ? '▶️ Just Started Watching' : '✓ Mark Watched';
                    }
                    if (msgBox) {
                        msgBox.style.display = 'block';
                        msgBox.style.background = 'var(--status-error-bg)';
                        msgBox.style.color = 'var(--status-error-text)';
                        msgBox.textContent = 'Error: ' + (err.detail || 'Failed to scrobble');
                    }
                }
            } catch (e) {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = isStart ? '▶️ Just Started Watching' : '✓ Mark Watched';
                }
                if (msgBox) {
                    msgBox.style.display = 'block';
                    msgBox.style.background = 'var(--status-error-bg)';
                    msgBox.style.color = 'var(--status-error-text)';
                    msgBox.textContent = 'Error: ' + e.message;
                }
            }
        }

        function renderDisambiguationList(results, action, queryTitle) {
            const container = document.getElementById('scrobble-disambig-container');
            const list = document.getElementById('scrobble-disambig-list');
            const form = document.getElementById('direct-scrobble-form');
            if (!container || !list) return;

            let html = '';
            for (const item of results) {
                const media = item.movie || item;
                const title = escapeHtml(media.title || '');
                const year = media.year ? `(${escapeHtml(media.year)})` : '';
                const imdb = media.ids?.imdb ? `[IMDb: ${escapeHtml(media.ids.imdb)}]` : '';
                const overview = media.overview ? escapeHtml(media.overview) : '';
                const actionEncoded = encodeURIComponentForInlineJs(action);
                const candidateJson = encodeURIComponentForInlineJs(JSON.stringify({
                    title: media.title,
                    year: media.year,
                    ids: media.ids || {}
                }));

                html += `
                <div class="disambig-card u-background-bg-surface u-border-1px-solid-var-border-color u-border-radius-6px u-padding-8px-12px u-cursor-pointer u-transition-all-0-15s-ease u-display-flex u-justify-content-space-between u-align-items-center" onclick="submitDirectScrobble(decodeURIComponent('${actionEncoded}'), JSON.parse(decodeURIComponent('${candidateJson}')))" >
                    <div class="u-flex-1 u-min-width-0 u-padding-right-10px">
                        <div class="u-font-weight-600 u-color-text-main u-font-size-13px u-display-flex u-align-items-center u-gap-6px">
                            <span>🎬</span> <span>${title} ${year}</span>
                            ${imdb ? `<span class="u-font-size-10px u-background-border-color u-color-text-muted u-padding-1px-5px u-border-radius-3px">${imdb}</span>` : ''}
                        </div>
                        ${overview ? `<p class="u-color-text-muted u-font-size-11px u-margin-3px-0-0 u-line-height-1-3 u-display-webkit-box u-webkit-line-clamp-2 u-webkit-box-orient-vertical u-overflow-hidden">${overview}</p>` : ''}
                    </div>
                    <button type="button" class="btn-sm u-background-accent-color u-color-accent-text u-font-weight-600 u-font-size-11px u-padding-5px-10px u-white-space-nowrap">
                        Choose &rarr;
                    </button>
                </div>`;
            }
            list.innerHTML = html;
            container.style.display = 'block';
            if (form) form.style.display = 'none';
        }

        // Two-Way Reconciliation Settings Modal Logic
