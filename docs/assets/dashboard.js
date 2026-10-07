// ---- api.js ----
        function encodeURIComponentForInlineJs(value) {
            // encodeURIComponent leaves apostrophes unescaped, which can break out of
            // single-quoted inline handlers after HTML parsing.
            return encodeURIComponent(String(value ?? '')).replace(/'/g, '%27');
        }

        // Automatic CSRF Protection Interceptor for State-Mutating AJAX Calls
        (function() {
            const originalFetch = window.fetch;
            window.fetch = function(url, options) {
                options = options || {};
                const method = (options.method || 'GET').toUpperCase();
                if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
                    const match = document.cookie.match(/(^|;\s*)csrf_token=([^;]+)/);
                    const csrfToken = match ? decodeURIComponent(match[2]) : '';
                    if (csrfToken) {
                        options.headers = options.headers || {};
                        if (options.headers instanceof Headers) {
                            if (!options.headers.has('x-csrf-token')) {
                                options.headers.set('x-csrf-token', csrfToken);
                            }
                        } else if (Array.isArray(options.headers)) {
                            options.headers.push(['x-csrf-token', csrfToken]);
                        } else {
                            options.headers['x-csrf-token'] = csrfToken;
                        }
                    }
                }
                return originalFetch(url, options);
            };
        })();

// ---- core.js ----
        const isAdmin = window.dashboardConfig.isAdmin;
        const isDemo = window.dashboardConfig.isDemo;
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

// ---- settings.js ----
        let activeReconSettingsTab = 'plex';

        function switchReconSettingsTab(srv) {
            activeReconSettingsTab = srv;
            ['plex', 'jellyfin', 'emby'].forEach(s => {
                const btn = document.getElementById(`recon-tab-btn-${s}`);
                const panel = document.getElementById(`recon-panel-${s}`);
                if (btn) {
                    if (s === srv) {
                        btn.style.background = s === 'plex' ? 'var(--accent-color)' : s === 'jellyfin' ? '#7c3aed' : 'var(--status-playing)';
                        btn.style.color = 'var(--accent-text)';
                    } else {
                        btn.style.background = 'var(--bg-surface)';
                        btn.style.color = 'var(--text-heading)';
                    }
                }
                if (panel) {
                    panel.style.display = (s === srv) ? 'block' : 'none';
                }
            });
        }

        // -------------------------------------------------------------
        // Omniscrobble Unified Settings Hub
        // -------------------------------------------------------------
        let currentSettingsData = null;
        let activeSettingsServerSubTab = 'plex';

        function openSettingsModal(tab = 'servers', section = null) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const modal = document.getElementById('settings-modal');
            if (modal) modal.style.display = 'flex';
            switchSettingsTab(tab || 'servers');
            if (section) {
                setTimeout(() => {
                    const el = document.getElementById(`settings-section-${section}`);
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        const origBorder = el.style.borderColor;
                        el.style.borderColor = 'var(--accent-color)';
                        setTimeout(() => { el.style.borderColor = origBorder || 'var(--border-color)'; }, 2000);
                    }
                }, 100);
            }
            fetchSettingsModalData();
        }

        function closeSettingsModal() {
            const modal = document.getElementById('settings-modal');
            if (modal) modal.style.display = 'none';
            const statusMsg = document.getElementById('settings-modal-status-msg');
            if (statusMsg) statusMsg.textContent = '';
        }

        function switchSettingsTab(tabName) {
            const tabs = ['servers', 'trackers', 'automation', 'notifications', 'rules', 'appearance'];
            if (tabName === 'appearance') {
                renderThemePickers();
                if (typeof updateDensityUI === 'function') updateDensityUI();
                if (typeof renderCardVisibilityPickers === 'function') renderCardVisibilityPickers();
            }
            tabs.forEach(t => {
                const btn = document.getElementById(`settings-tab-btn-${t}`);
                const panel = document.getElementById(`settings-panel-${t}`);
                if (btn) {
                    if (t === tabName) {
                        btn.style.background = 'var(--accent-color, var(--accent-color))';
                        btn.style.color = 'var(--accent-text)';
                        btn.style.fontWeight = '600';
                        btn.style.border = 'none';
                    } else {
                        btn.style.background = 'var(--bg-surface)';
                        btn.style.color = 'var(--text-heading)';
                        btn.style.fontWeight = 'normal';
                        btn.style.border = '1px solid var(--border-color)';
                    }
                }
                if (panel) {
                    panel.style.display = (t === tabName) ? 'flex' : 'none';
                }
            });
        }

        function filterSettingsTrackers(category, btn) {
            const btns = document.querySelectorAll('.settings-trk-cat-btn');
            btns.forEach(b => {
                b.style.background = 'var(--bg-surface)';
                b.style.borderColor = 'var(--border-color)';
                b.style.color = 'var(--text-heading)';
                b.style.fontWeight = 'normal';
            });
            if (btn) {
                btn.style.background = 'var(--accent-color)';
                btn.style.borderColor = 'var(--accent-color)';
                btn.style.color = 'var(--accent-text)';
                btn.style.fontWeight = '600';
            }
            const cards = document.querySelectorAll('#settings-panel-trackers div[data-tracker-cat]');
            cards.forEach(card => {
                if (category === 'all' || card.getAttribute('data-tracker-cat') === category) {
                    card.style.display = 'block';
                } else {
                    card.style.display = 'none';
                }
            });
        }

        function filterHubTrackers(category, btn) {
            const btns = document.querySelectorAll('.hub-cat-tab');
            btns.forEach(b => {
                b.style.background = 'var(--bg-surface)';
                b.style.borderColor = 'var(--border-color)';
                b.style.color = 'var(--text-heading)';
                b.style.fontWeight = 'normal';
            });
            if (btn) {
                btn.style.background = 'var(--accent-color)';
                btn.style.borderColor = 'var(--accent-color)';
                btn.style.color = 'var(--accent-text)';
                btn.style.fontWeight = '600';
            }
            const items = document.querySelectorAll('#hub-trackers-grid .hub-tracker-item');
            items.forEach(item => {
                if (category === 'all' || item.getAttribute('data-cat') === category) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        }

        function switchSettingsServerSubTab(srv) {
            activeSettingsServerSubTab = srv;
            const servers = ['plex', 'jellyfin', 'emby'];
            servers.forEach(s => {
                const btn = document.getElementById(`settings-srv-tab-${s}`);
                const form = document.getElementById(`settings-srv-form-${s}`);
                if (btn) {
                    if (s === srv) {
                        btn.style.background = 'var(--accent-color)';
                        btn.style.color = 'var(--accent-text)';
                        btn.style.fontWeight = '600';
                        btn.style.border = 'none';
                    } else {
                        btn.style.background = 'var(--bg-surface)';
                        btn.style.color = 'var(--text-heading)';
                        btn.style.fontWeight = 'normal';
                        btn.style.border = '1px solid var(--border-color)';
                    }
                }
                if (form) {
                    form.style.display = (s === srv) ? 'flex' : 'none';
                }
            });
        }

        async function fetchSettingsModalData() {
            const url = isDemo ? '/api/settings?demo=true' : '/api/settings';
            try {
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to load settings');
                const raw = await res.json();
                const data = raw.settings || raw;
                currentSettingsData = data;

                // 1. Server Ingestion Listeners
                const servers = data.servers || {};
                ['plex', 'jellyfin', 'emby'].forEach(srv => {
                    const isEnabled = Boolean(servers[srv]);
                    const statusEl = document.getElementById(`settings-listener-status-${srv}`);
                    const btnEl = document.getElementById(`settings-toggle-btn-${srv}`);
                    if (statusEl) {
                        statusEl.textContent = isEnabled ? '● Active' : '● Inactive';
                        statusEl.style.color = isEnabled ? 'var(--status-playing)' : 'var(--text-muted)';
                    }
                    if (btnEl) {
                        btnEl.textContent = isEnabled ? 'Pause' : 'Resume';
                        btnEl.style.background = isEnabled ? 'var(--bg-surface)' : 'var(--status-success-bg)';
                        btnEl.style.color = isEnabled ? 'var(--text-heading)' : 'var(--status-success-text)';
                        btnEl.style.borderColor = isEnabled ? 'var(--border-color)' : 'var(--status-playing)';
                    }
                });

                // 2. Server Direct Connection Profiles (Reconciliation)
                const recon = data.reconciliation || {};
                const plexUrlInput = document.getElementById('settings-plex-url');
                const plexTokenInput = document.getElementById('settings-plex-token');
                const plexBadge = document.getElementById('settings-plex-token-badge');
                if (plexUrlInput) plexUrlInput.value = recon.plex_url || '';
                if (plexTokenInput) plexTokenInput.value = recon.masked_plex_token || '';
                if (plexBadge) {
                    const isSet = Boolean(recon.has_plex_token || recon.is_plex_token_set);
                    plexBadge.textContent = isSet ? '✓ Configured' : 'Not Configured';
                    plexBadge.style.color = isSet ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const jfUrlInput = document.getElementById('settings-jellyfin-url');
                const jfTokenInput = document.getElementById('settings-jellyfin-token');
                const jfUserInput = document.getElementById('settings-jellyfin-user-id');
                if (jfUrlInput) jfUrlInput.value = recon.jellyfin_url || '';
                if (jfTokenInput) jfTokenInput.value = recon.masked_jellyfin_token || '';
                if (jfUserInput) jfUserInput.value = recon.jellyfin_user_id || '';

                const embyUrlInput = document.getElementById('settings-emby-url');
                const embyTokenInput = document.getElementById('settings-emby-token');
                const embyUserInput = document.getElementById('settings-emby-user-id');
                if (embyUrlInput) embyUrlInput.value = recon.emby_url || '';
                if (embyTokenInput) embyTokenInput.value = recon.masked_emby_token || '';
                if (embyUserInput) embyUserInput.value = recon.emby_user_id || '';

                // 3. Trackers & Scrobblers
                const trackers = data.trackers || {};
                const creds = data.credentials || {};

                // Trakt
                const traktCreds = creds.trakt || {};
                const traktIdInput = document.getElementById('settings-trakt-client-id');
                const traktSecretInput = document.getElementById('settings-trakt-client-secret');
                if (traktIdInput) traktIdInput.value = traktCreds.client_id || '';
                if (traktSecretInput) traktSecretInput.value = traktCreds.masked_client_secret || '';

                const traktEnabled = Boolean(trackers.trakt);
                const traktStatus = document.getElementById('settings-trk-status-trakt');
                const traktToggleBtn = document.getElementById('settings-trk-toggle-btn-trakt');
                if (traktStatus) {
                    traktStatus.textContent = traktEnabled ? '● Connected' : '● Paused';
                    traktStatus.style.color = traktEnabled ? 'var(--status-playing)' : 'var(--status-paused)';
                }
                if (traktToggleBtn) {
                    traktToggleBtn.textContent = traktEnabled ? 'Pause' : 'Resume';
                    traktToggleBtn.style.color = traktEnabled ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // Simkl
                const simklCreds = creds.simkl || {};
                const simklIdInput = document.getElementById('settings-simkl-client-id');
                const simklSecretInput = document.getElementById('settings-simkl-client-secret');
                if (simklIdInput) simklIdInput.value = simklCreds.client_id || '';
                if (simklSecretInput) simklSecretInput.value = simklCreds.masked_client_secret || '';

                const simklStatus = document.getElementById('settings-trk-status-simkl');
                const simklToggleBtn = document.getElementById('settings-trk-toggle-btn-simkl');
                const simklHasCreds = Boolean(simklCreds.client_id || simklCreds.has_credentials);
                const simklActive = Boolean(trackers.simkl && simklHasCreds);
                if (simklStatus) {
                    if (simklActive) {
                        simklStatus.textContent = '● Active';
                        simklStatus.style.color = 'var(--status-playing)';
                    } else if (simklHasCreds) {
                        simklStatus.textContent = '● Paused';
                        simklStatus.style.color = 'var(--status-paused)';
                    } else {
                        simklStatus.textContent = 'Not Configured';
                        simklStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (simklToggleBtn) {
                    simklToggleBtn.textContent = Boolean(trackers.simkl) ? 'Pause' : 'Resume';
                    simklToggleBtn.style.color = Boolean(trackers.simkl) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // AniList
                const aniCreds = creds.anilist || {};
                const aniTokenInput = document.getElementById('settings-anilist-token');
                if (aniTokenInput) aniTokenInput.value = aniCreds.masked_access_token || '';

                const aniStatus = document.getElementById('settings-trk-status-anilist');
                const aniToggleBtn = document.getElementById('settings-trk-toggle-btn-anilist');
                const aniHasCreds = Boolean(aniCreds.has_credentials);
                const aniActive = Boolean(trackers.anilist && aniHasCreds);
                if (aniStatus) {
                    if (aniActive) {
                        aniStatus.textContent = '● Connected';
                        aniStatus.style.color = 'var(--status-playing)';
                    } else if (aniHasCreds) {
                        aniStatus.textContent = '● Paused';
                        aniStatus.style.color = 'var(--status-paused)';
                    } else {
                        aniStatus.textContent = 'Not Configured';
                        aniStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (aniToggleBtn) {
                    aniToggleBtn.textContent = Boolean(trackers.anilist) ? 'Pause' : 'Resume';
                    aniToggleBtn.style.color = Boolean(trackers.anilist) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // MyAnimeList (MAL)
                const malCreds = creds.mal || {};
                const malIdInput = document.getElementById('settings-mal-client-id');
                const malSecretInput = document.getElementById('settings-mal-client-secret');
                const malTokenInput = document.getElementById('settings-mal-token');
                if (malIdInput) malIdInput.value = malCreds.client_id || '';
                if (malSecretInput) malSecretInput.value = malCreds.masked_client_secret || '';
                if (malTokenInput) malTokenInput.value = malCreds.masked_access_token || '';

                const malStatus = document.getElementById('settings-trk-status-mal');
                const malToggleBtn = document.getElementById('settings-trk-toggle-btn-mal');
                const malHasCreds = Boolean(malCreds.has_credentials || malCreds.client_id);
                const malActive = Boolean(trackers.mal && malHasCreds);
                if (malStatus) {
                    if (malActive) {
                        malStatus.textContent = '● Connected';
                        malStatus.style.color = 'var(--status-playing)';
                    } else if (malHasCreds) {
                        malStatus.textContent = '● Paused';
                        malStatus.style.color = 'var(--status-paused)';
                    } else {
                        malStatus.textContent = 'Not Configured';
                        malStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (malToggleBtn) {
                    malToggleBtn.textContent = Boolean(trackers.mal) ? 'Pause' : 'Resume';
                    malToggleBtn.style.color = Boolean(trackers.mal) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // TMDb
                const tmdbCreds = creds.tmdb || {};
                const tmdbApiKeyInput = document.getElementById('settings-tmdb-api-key');
                const tmdbSessionInput = document.getElementById('settings-tmdb-session-id');
                const tmdbTokenInput = document.getElementById('settings-tmdb-access-token');
                if (tmdbApiKeyInput) tmdbApiKeyInput.value = tmdbCreds.masked_api_key || tmdbCreds.api_key || '';
                if (tmdbSessionInput) tmdbSessionInput.value = tmdbCreds.masked_session_id || tmdbCreds.session_id || '';
                if (tmdbTokenInput) tmdbTokenInput.value = tmdbCreds.masked_access_token || tmdbCreds.access_token || '';
                const tmdbStatus = document.getElementById('settings-trk-status-tmdb');
                const tmdbToggleBtn = document.getElementById('settings-trk-toggle-btn-tmdb');
                const tmdbHasCreds = Boolean(tmdbCreds.api_key || tmdbCreds.access_token || tmdbCreds.has_credentials);
                const tmdbActive = Boolean(trackers.tmdb && tmdbHasCreds);
                if (tmdbStatus) {
                    if (tmdbActive) {
                        tmdbStatus.textContent = '● Active';
                        tmdbStatus.style.color = 'var(--status-playing)';
                    } else if (tmdbHasCreds) {
                        tmdbStatus.textContent = '● Paused';
                        tmdbStatus.style.color = 'var(--status-paused)';
                    } else {
                        tmdbStatus.textContent = 'Not Configured';
                        tmdbStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (tmdbToggleBtn) {
                    tmdbToggleBtn.textContent = Boolean(trackers.tmdb) ? 'Pause' : 'Resume';
                    tmdbToggleBtn.style.color = Boolean(trackers.tmdb) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // Kitsu
                const kitsuCreds = creds.kitsu || {};
                const kitsuTokenInput = document.getElementById('settings-kitsu-token');
                if (kitsuTokenInput) kitsuTokenInput.value = kitsuCreds.masked_api_token || kitsuCreds.api_token || '';
                const kitsuStatus = document.getElementById('settings-trk-status-kitsu');
                const kitsuToggleBtn = document.getElementById('settings-trk-toggle-btn-kitsu');
                const kitsuHasCreds = Boolean(kitsuCreds.api_token || kitsuCreds.has_credentials);
                const kitsuActive = Boolean(trackers.kitsu && kitsuHasCreds);
                if (kitsuStatus) {
                    if (kitsuActive) {
                        kitsuStatus.textContent = '● Active';
                        kitsuStatus.style.color = 'var(--status-playing)';
                    } else if (kitsuHasCreds) {
                        kitsuStatus.textContent = '● Paused';
                        kitsuStatus.style.color = 'var(--status-paused)';
                    } else {
                        kitsuStatus.textContent = 'Not Configured';
                        kitsuStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (kitsuToggleBtn) {
                    kitsuToggleBtn.textContent = Boolean(trackers.kitsu) ? 'Pause' : 'Resume';
                    kitsuToggleBtn.style.color = Boolean(trackers.kitsu) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // Letterboxd
                const lbCreds = creds.letterboxd || {};
                const lbUserInput = document.getElementById('settings-letterboxd-username');
                if (lbUserInput) lbUserInput.value = lbCreds.username || '';
                const lbStatus = document.getElementById('settings-trk-status-letterboxd');
                const lbToggleBtn = document.getElementById('settings-trk-toggle-btn-letterboxd');
                const lbHasCreds = Boolean(lbCreds.username);
                const lbActive = Boolean(trackers.letterboxd && lbHasCreds);
                if (lbStatus) {
                    if (lbActive) {
                        lbStatus.textContent = '● Active';
                        lbStatus.style.color = 'var(--status-playing)';
                    } else if (lbHasCreds) {
                        lbStatus.textContent = '● Paused';
                        lbStatus.style.color = 'var(--status-paused)';
                    } else {
                        lbStatus.textContent = 'Not Configured';
                        lbStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (lbToggleBtn) {
                    lbToggleBtn.textContent = Boolean(trackers.letterboxd) ? 'Pause' : 'Resume';
                    lbToggleBtn.style.color = Boolean(trackers.letterboxd) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // Serializd
                const serCreds = creds.serializd || {};
                const serUserInput = document.getElementById('settings-serializd-username');
                const serTokenInput = document.getElementById('settings-serializd-token');
                if (serUserInput) serUserInput.value = serCreds.username || '';
                if (serTokenInput) serTokenInput.value = serCreds.masked_token || serCreds.token || '';
                const serStatus = document.getElementById('settings-trk-status-serializd');
                const serToggleBtn = document.getElementById('settings-trk-toggle-btn-serializd');
                const serHasCreds = Boolean(serCreds.username || serCreds.token || serCreds.has_credentials);
                const serActive = Boolean(trackers.serializd && serHasCreds);
                if (serStatus) {
                    if (serActive) {
                        serStatus.textContent = '● Active';
                        serStatus.style.color = 'var(--status-playing)';
                    } else if (serHasCreds) {
                        serStatus.textContent = '● Paused';
                        serStatus.style.color = 'var(--status-paused)';
                    } else {
                        serStatus.textContent = 'Not Configured';
                        serStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (serToggleBtn) {
                    serToggleBtn.textContent = Boolean(trackers.serializd) ? 'Pause' : 'Resume';
                    serToggleBtn.style.color = Boolean(trackers.serializd) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // MDBList
                const mdbCreds = creds.mdblist || {};
                const mdbApiKeyInput = document.getElementById('settings-mdblist-api-key');
                if (mdbApiKeyInput) mdbApiKeyInput.value = mdbCreds.masked_api_key || mdbCreds.api_key || '';
                const mdbStatus = document.getElementById('settings-trk-status-mdblist');
                const mdbToggleBtn = document.getElementById('settings-trk-toggle-btn-mdblist');
                const mdbHasCreds = Boolean(mdbCreds.api_key || mdbCreds.has_credentials);
                const mdbActive = Boolean(trackers.mdblist && mdbHasCreds);
                if (mdbStatus) {
                    if (mdbActive) {
                        mdbStatus.textContent = '● Active';
                        mdbStatus.style.color = 'var(--status-playing)';
                    } else if (mdbHasCreds) {
                        mdbStatus.textContent = '● Paused';
                        mdbStatus.style.color = 'var(--status-paused)';
                    } else {
                        mdbStatus.textContent = 'Not Configured';
                        mdbStatus.style.color = 'var(--text-muted)';
                    }
                }
                if (mdbToggleBtn) {
                    mdbToggleBtn.textContent = Boolean(trackers.mdblist) ? 'Pause' : 'Resume';
                    mdbToggleBtn.style.color = Boolean(trackers.mdblist) ? 'var(--text-heading)' : 'var(--status-success-text)';
                }

                // 4. Automation & Arrs
                const reconTypeSelect = document.getElementById('settings-recon-server-type');
                const reconIntSelect = document.getElementById('settings-recon-interval');
                const reconRatingsCheck = document.getElementById('settings-recon-ratings-check');
                const reconStartupCheck = document.getElementById('settings-recon-startup-check');
                if (reconTypeSelect && recon.server_type) reconTypeSelect.value = recon.server_type;
                if (reconIntSelect && recon.interval_minutes !== undefined) reconIntSelect.value = String(recon.interval_minutes);
                if (reconRatingsCheck) reconRatingsCheck.checked = Boolean(recon.sync_ratings);
                if (reconStartupCheck) reconStartupCheck.checked = Boolean(recon.sync_on_startup);
                const mirrorCheck = document.getElementById('settings-multi-server-mirroring-check');
                if (mirrorCheck) mirrorCheck.checked = Boolean(data.multi_server_mirroring);

                const arr = data.arr || {};
                const sonarrUrlInput = document.getElementById('settings-arr-sonarr-url');
                const sonarrKeyInput = document.getElementById('settings-arr-sonarr-key');
                const radarrUrlInput = document.getElementById('settings-arr-radarr-url');
                const radarrKeyInput = document.getElementById('settings-arr-radarr-key');
                const overseerrUrlInput = document.getElementById('settings-arr-overseerr-url');
                const overseerrKeyInput = document.getElementById('settings-arr-overseerr-key');
                const overseerrEnabledCheck = document.getElementById('settings-arr-overseerr-enabled');
                const arrAutoAddCheck = document.getElementById('settings-arr-auto-add');
                const arrSearchAddCheck = document.getElementById('settings-arr-search-add');
                if (sonarrUrlInput) sonarrUrlInput.value = arr.sonarr_url || '';
                if (sonarrKeyInput) sonarrKeyInput.value = arr.masked_sonarr_key || '';
                if (radarrUrlInput) radarrUrlInput.value = arr.radarr_url || '';
                if (radarrKeyInput) radarrKeyInput.value = arr.masked_radarr_key || '';
                if (overseerrUrlInput) overseerrUrlInput.value = arr.overseerr_url || '';
                if (overseerrKeyInput) overseerrKeyInput.value = arr.masked_overseerr_key || '';
                if (overseerrEnabledCheck) overseerrEnabledCheck.checked = Boolean(arr.overseerr_enabled);
                if (arrAutoAddCheck) arrAutoAddCheck.checked = Boolean(arr.auto_add_watchlist || arr.auto_add_from_watchlist);
                if (arrSearchAddCheck) arrSearchAddCheck.checked = Boolean(arr.search_on_add);

                // 5. Notifications
                const notifs = data.notifications || {};
                const scrobbleCheck = document.getElementById('settings-notif-scrobble-check');
                const rateCheck = document.getElementById('settings-notif-rate-check');
                const colCheck = document.getElementById('settings-notif-collection-check');
                const failCheck = document.getElementById('settings-notif-failure-check');
                if (scrobbleCheck && notifs.notify_on_scrobble !== undefined) scrobbleCheck.checked = Boolean(notifs.notify_on_scrobble);
                if (rateCheck && notifs.notify_on_rate !== undefined) rateCheck.checked = Boolean(notifs.notify_on_rate);
                if (colCheck && notifs.notify_on_collection !== undefined) colCheck.checked = Boolean(notifs.notify_on_collection);
                if (failCheck && notifs.notify_on_failure !== undefined) failCheck.checked = Boolean(notifs.notify_on_failure);

                const dcUrl = document.getElementById('settings-notif-discord-url');
                const dcBadge = document.getElementById('settings-notif-discord-badge');
                if (dcUrl) dcUrl.value = notifs.discord_webhook_url || '';
                if (dcBadge) {
                    const hasDc = Boolean(notifs.discord_webhook_url);
                    dcBadge.textContent = hasDc ? '✓ Configured' : 'Not Configured';
                    dcBadge.style.color = hasDc ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const teleToken = document.getElementById('settings-notif-telegram-token');
                const teleChat = document.getElementById('settings-notif-telegram-chat');
                const teleBadge = document.getElementById('settings-notif-telegram-badge');
                if (teleToken) teleToken.value = notifs.telegram_bot_token || '';
                if (teleChat) teleChat.value = notifs.telegram_chat_id || '';
                if (teleBadge) {
                    const hasTele = Boolean(notifs.telegram_bot_token && notifs.telegram_chat_id);
                    teleBadge.textContent = hasTele ? '✓ Configured' : 'Not Configured';
                    teleBadge.style.color = hasTele ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const ntfyUrl = document.getElementById('settings-notif-ntfy-url');
                const ntfyAuth = document.getElementById('settings-notif-ntfy-auth');
                const ntfyBadge = document.getElementById('settings-notif-ntfy-badge');
                if (ntfyUrl) ntfyUrl.value = notifs.ntfy_url || '';
                if (ntfyAuth) ntfyAuth.value = notifs.ntfy_auth_token || '';
                if (ntfyBadge) {
                    const hasNtfy = Boolean(notifs.ntfy_url);
                    ntfyBadge.textContent = hasNtfy ? '✓ Configured' : 'Not Configured';
                    ntfyBadge.style.color = hasNtfy ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const pushUser = document.getElementById('settings-notif-pushover-user');
                const pushToken = document.getElementById('settings-notif-pushover-token');
                const pushBadge = document.getElementById('settings-notif-pushover-badge');
                if (pushUser) pushUser.value = notifs.pushover_user_key || '';
                if (pushToken) pushToken.value = notifs.pushover_api_token || '';
                if (pushBadge) {
                    const hasPush = Boolean(notifs.pushover_user_key && notifs.pushover_api_token);
                    pushBadge.textContent = hasPush ? '✓ Configured' : 'Not Configured';
                    pushBadge.style.color = hasPush ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const gotifyUrl = document.getElementById('settings-notif-gotify-url');
                const gotifyToken = document.getElementById('settings-notif-gotify-token');
                const gotifyBadge = document.getElementById('settings-notif-gotify-badge');
                if (gotifyUrl) gotifyUrl.value = notifs.gotify_url || '';
                if (gotifyToken) gotifyToken.value = notifs.masked_gotify_token || notifs.gotify_token || '';
                if (gotifyBadge) {
                    const hasGotify = Boolean(notifs.gotify_url && (notifs.gotify_token || notifs.masked_gotify_token));
                    gotifyBadge.textContent = hasGotify ? '✓ Configured' : 'Not Configured';
                    gotifyBadge.style.color = hasGotify ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const matrixUrl = document.getElementById('settings-notif-matrix-url');
                const matrixRoom = document.getElementById('settings-notif-matrix-room');
                const matrixToken = document.getElementById('settings-notif-matrix-token');
                const matrixBadge = document.getElementById('settings-notif-matrix-badge');
                if (matrixUrl) matrixUrl.value = notifs.matrix_homeserver_url || '';
                if (matrixRoom) matrixRoom.value = notifs.matrix_room_id || '';
                if (matrixToken) matrixToken.value = notifs.masked_matrix_token || notifs.matrix_access_token || '';
                if (matrixBadge) {
                    const hasMatrix = Boolean(notifs.matrix_homeserver_url && notifs.matrix_room_id);
                    matrixBadge.textContent = hasMatrix ? '✓ Configured' : 'Not Configured';
                    matrixBadge.style.color = hasMatrix ? 'var(--status-playing)' : 'var(--text-muted)';
                }

                const digestEnabledCheck = document.getElementById('settings-digest-enabled');
                const digestDaySelect = document.getElementById('settings-digest-day');
                const digestHourInput = document.getElementById('settings-digest-hour');
                if (digestEnabledCheck) digestEnabledCheck.checked = Boolean(notifs.weekly_digest_enabled);
                if (digestDaySelect && notifs.weekly_digest_day) digestDaySelect.value = String(notifs.weekly_digest_day).toLowerCase();
                if (digestHourInput && notifs.weekly_digest_hour !== undefined) digestHourInput.value = notifs.weekly_digest_hour;

                // 5. Rules & Filters
                const rules = data.rules || {};
                const epThresh = document.getElementById('settings-rules-threshold');
                const epThreshVal = document.getElementById('settings-rules-threshold-val');
                if (epThresh) {
                    epThresh.value = rules.scrobble_threshold !== undefined ? rules.scrobble_threshold : 80;
                    if (epThreshVal) epThreshVal.textContent = epThresh.value + '%';
                }
                const movThresh = document.getElementById('settings-rules-movie-threshold');
                const movThreshVal = document.getElementById('settings-rules-movie-threshold-val');
                if (movThresh) {
                    movThresh.value = rules.movie_scrobble_threshold !== undefined ? rules.movie_scrobble_threshold : 90;
                    if (movThreshVal) movThreshVal.textContent = movThresh.value + '%';
                }
                const minDurInput = document.getElementById('settings-rules-min-duration');
                if (minDurInput) minDurInput.value = rules.min_duration_seconds !== undefined ? rules.min_duration_seconds : 300;
                const applyEpsCheck = document.getElementById('settings-rules-apply-episodes');
                if (applyEpsCheck) applyEpsCheck.checked = Boolean(rules.apply_min_duration_to_episodes);
                const ignoreLibsInput = document.getElementById('settings-rules-ignore-libraries');
                if (ignoreLibsInput) ignoreLibsInput.value = (rules.ignore_libraries || []).join(', ');
                const ignorePathsInput = document.getElementById('settings-rules-ignore-paths');
                if (ignorePathsInput) ignorePathsInput.value = (rules.ignore_path_patterns || []).join('\n');

            } catch (err) {
                console.error('Error fetching settings modal data:', err);
            }
        }

        async function saveAllSettingsFromModal() {
            const saveBtn = document.getElementById('settings-save-all-btn');
            const statusMsg = document.getElementById('settings-modal-status-msg');

            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.innerText = 'Saving...';
            }
            if (statusMsg) {
                statusMsg.style.color = 'var(--status-finished)';
                statusMsg.innerText = 'Persisting settings...';
            }

            try {
                const payload = {
                    credentials: {
                        trakt: {
                            client_id: document.getElementById('settings-trakt-client-id')?.value.trim() || '',
                            client_secret: document.getElementById('settings-trakt-client-secret')?.value.trim() || ''
                        },
                        simkl: {
                            client_id: document.getElementById('settings-simkl-client-id')?.value.trim() || '',
                            client_secret: document.getElementById('settings-simkl-client-secret')?.value.trim() || ''
                        },
                        anilist: {
                            access_token: document.getElementById('settings-anilist-token')?.value.trim() || ''
                        },
                        mal: {
                            client_id: document.getElementById('settings-mal-client-id')?.value.trim() || '',
                            client_secret: document.getElementById('settings-mal-client-secret')?.value.trim() || '',
                            access_token: document.getElementById('settings-mal-token')?.value.trim() || ''
                        },
                        tmdb: {
                            api_key: document.getElementById('settings-tmdb-api-key')?.value.trim() || '',
                            session_id: document.getElementById('settings-tmdb-session-id')?.value.trim() || '',
                            access_token: document.getElementById('settings-tmdb-access-token')?.value.trim() || ''
                        },
                        kitsu: {
                            api_token: document.getElementById('settings-kitsu-token')?.value.trim() || ''
                        },
                        letterboxd: {
                            username: document.getElementById('settings-letterboxd-username')?.value.trim() || ''
                        },
                        serializd: {
                            username: document.getElementById('settings-serializd-username')?.value.trim() || '',
                            token: document.getElementById('settings-serializd-token')?.value.trim() || ''
                        },
                        mdblist: {
                            api_key: document.getElementById('settings-mdblist-api-key')?.value.trim() || ''
                        }
                    },
                    reconciliation: {
                        server_type: document.getElementById('settings-recon-server-type')?.value || 'plex',
                        plex_url: document.getElementById('settings-plex-url')?.value.trim() || '',
                        plex_token: document.getElementById('settings-plex-token')?.value.trim() || '',
                        jellyfin_url: document.getElementById('settings-jellyfin-url')?.value.trim() || '',
                        jellyfin_token: document.getElementById('settings-jellyfin-token')?.value.trim() || '',
                        jellyfin_user_id: document.getElementById('settings-jellyfin-user-id')?.value.trim() || '',
                        emby_url: document.getElementById('settings-emby-url')?.value.trim() || '',
                        emby_token: document.getElementById('settings-emby-token')?.value.trim() || '',
                        emby_user_id: document.getElementById('settings-emby-user-id')?.value.trim() || '',
                        interval_minutes: parseInt(document.getElementById('settings-recon-interval')?.value, 10) || 0,
                        sync_ratings: Boolean(document.getElementById('settings-recon-ratings-check')?.checked),
                        sync_on_startup: Boolean(document.getElementById('settings-recon-startup-check')?.checked)
                    },
                    multi_server_mirroring: Boolean(document.getElementById('settings-multi-server-mirroring-check')?.checked),
                    arr: {
                        sonarr_url: document.getElementById('settings-arr-sonarr-url')?.value.trim() || '',
                        sonarr_api_key: document.getElementById('settings-arr-sonarr-key')?.value.trim() || '',
                        radarr_url: document.getElementById('settings-arr-radarr-url')?.value.trim() || '',
                        radarr_api_key: document.getElementById('settings-arr-radarr-key')?.value.trim() || '',
                        overseerr_url: document.getElementById('settings-arr-overseerr-url')?.value.trim() || '',
                        overseerr_api_key: document.getElementById('settings-arr-overseerr-key')?.value.trim() || '',
                        overseerr_enabled: Boolean(document.getElementById('settings-arr-overseerr-enabled')?.checked),
                        auto_add_watchlist: Boolean(document.getElementById('settings-arr-auto-add')?.checked),
                        search_on_add: Boolean(document.getElementById('settings-arr-search-add')?.checked)
                    },
                    notifications: {
                        discord_webhook_url: document.getElementById('settings-notif-discord-url')?.value.trim() || '',
                        telegram_bot_token: document.getElementById('settings-notif-telegram-token')?.value.trim() || '',
                        telegram_chat_id: document.getElementById('settings-notif-telegram-chat')?.value.trim() || '',
                        ntfy_url: document.getElementById('settings-notif-ntfy-url')?.value.trim() || '',
                        ntfy_auth_token: document.getElementById('settings-notif-ntfy-auth')?.value.trim() || '',
                        pushover_user_key: document.getElementById('settings-notif-pushover-user')?.value.trim() || '',
                        pushover_api_token: document.getElementById('settings-notif-pushover-token')?.value.trim() || '',
                        gotify_url: document.getElementById('settings-notif-gotify-url')?.value.trim() || '',
                        gotify_token: document.getElementById('settings-notif-gotify-token')?.value.trim() || '',
                        matrix_homeserver_url: document.getElementById('settings-notif-matrix-url')?.value.trim() || '',
                        matrix_room_id: document.getElementById('settings-notif-matrix-room')?.value.trim() || '',
                        matrix_access_token: document.getElementById('settings-notif-matrix-token')?.value.trim() || '',
                        weekly_digest_enabled: Boolean(document.getElementById('settings-digest-enabled')?.checked),
                        weekly_digest_day: document.getElementById('settings-digest-day')?.value || 'sunday',
                        weekly_digest_hour: parseInt(document.getElementById('settings-digest-hour')?.value, 10) || 20,
                        notify_on_scrobble: Boolean(document.getElementById('settings-notif-scrobble-check')?.checked),
                        notify_on_rate: Boolean(document.getElementById('settings-notif-rate-check')?.checked),
                        notify_on_collection: Boolean(document.getElementById('settings-notif-collection-check')?.checked),
                        notify_on_failure: Boolean(document.getElementById('settings-notif-failure-check')?.checked)
                    },
                    rules: {
                        scrobble_threshold: parseInt(document.getElementById('settings-rules-threshold')?.value, 10) || 80,
                        movie_scrobble_threshold: parseInt(document.getElementById('settings-rules-movie-threshold')?.value, 10) || 90,
                        min_duration_seconds: parseInt(document.getElementById('settings-rules-min-duration')?.value, 10) || 0,
                        apply_min_duration_to_episodes: Boolean(document.getElementById('settings-rules-apply-episodes')?.checked),
                        ignore_libraries: (document.getElementById('settings-rules-ignore-libraries')?.value || '')
                            .split(',')
                            .map(s => s.trim())
                            .filter(Boolean),
                        ignore_path_patterns: (document.getElementById('settings-rules-ignore-paths')?.value || '')
                            .split('\n')
                            .map(s => s.trim())
                            .filter(Boolean)
                    }
                };

                const url = isDemo ? '/api/settings?demo=true' : '/api/settings';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (res.ok) {
                    if (saveBtn) {
                        saveBtn.innerText = '✓ Saved!';
                        saveBtn.style.background = 'var(--status-playing)';
                    }
                    if (statusMsg) {
                        statusMsg.style.color = 'var(--status-playing)';
                        statusMsg.innerText = '✓ Settings persisted successfully! Refreshing...';
                    }
                    setTimeout(() => {
                        closeSettingsModal();
                        window.location.reload();
                    }, 800);
                } else {
                    const err = await res.json();
                    if (saveBtn) {
                        saveBtn.disabled = false;
                        saveBtn.innerText = '💾 Save Settings';
                    }
                    if (statusMsg) {
                        statusMsg.style.color = 'var(--status-error)';
                        statusMsg.innerText = 'Error: ' + (err.detail || 'Failed to save settings');
                    }
                }
            } catch (err) {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.innerText = '💾 Save Settings';
                }
                if (statusMsg) {
                    statusMsg.style.color = 'var(--status-error)';
                    statusMsg.innerText = 'Error: ' + err.message;
                }
            }
        }

        async function testSettingsServerConnection(server = 'plex') {
            const srv = (server || activeSettingsServerSubTab || 'plex').toLowerCase();
            const testBtn = document.getElementById(`settings-${srv}-test-btn`);
            const statusEl = document.getElementById(`settings-${srv}-test-status`);
            const urlVal = document.getElementById(`settings-${srv}-url`)?.value.trim() || '';
            const tokenVal = document.getElementById(`settings-${srv}-token`)?.value.trim() || '';
            const userIdVal = document.getElementById(`settings-${srv}-user-id`)?.value.trim() || '';

            if (testBtn) {
                testBtn.disabled = true;
                testBtn.innerText = 'Testing...';
            }
            if (statusEl) {
                statusEl.innerText = `Connecting to ${srv.charAt(0).toUpperCase() + srv.slice(1)}...`;
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const url = isDemo ? '/api/sync/test-connection?demo=true' : '/api/sync/test-connection';
                const body = { server: srv, url: urlVal, token: tokenVal };
                if (userIdVal) body.user_id = userIdVal;

                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (res.ok && data.status === 'connected') {
                    if (statusEl) {
                        const ver = data.version ? ` (v${data.version})` : '';
                        statusEl.innerText = `● Connected${ver}`;
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = `● ${data.message || 'Connection failed'}`;
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = `● Error: ${err.message}`;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (testBtn) {
                    testBtn.disabled = false;
                    testBtn.innerText = srv === 'plex' ? '🔌 Test Connection' : (srv === 'jellyfin' ? '🔌 Test Jellyfin' : '🔌 Test Emby');
                }
            }
        }

        async function registerServerWebhook(server = 'plex') {
            const srv = (server || activeSettingsServerSubTab || 'plex').toLowerCase();
            const btn = document.getElementById(`settings-${srv}-webhook-btn`);
            const statusEl = document.getElementById(`settings-${srv}-test-status`);

            if (btn) {
                btn.disabled = true;
                btn.innerText = '⚡ Registering...';
            }
            if (statusEl) {
                statusEl.innerText = `Connecting to ${srv.charAt(0).toUpperCase() + srv.slice(1)} API...`;
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const url = isDemo ? '/api/sync/register-webhook?demo=true' : '/api/sync/register-webhook';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ server: srv })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    if (statusEl) {
                        statusEl.innerText = `✓ ${data.message || 'Webhook auto-registered!'}`;
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = `✗ ${data.message || 'Auto-registration failed'}`;
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = `✗ Error: ${err.message}`;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = '⚡ Auto-Register Webhook';
                }
            }
        }

        async function testSettingsArrConnection(appType = 'sonarr') {
            const app = appType.toLowerCase();
            const testBtn = document.getElementById(`settings-${app}-test-btn`);
            const statusEl = document.getElementById(`settings-${app}-test-status`);
            const urlVal = document.getElementById(`settings-arr-${app}-url`)?.value.trim() || '';
            const keyVal = document.getElementById(`settings-arr-${app}-key`)?.value.trim() || '';

            if (testBtn) {
                testBtn.disabled = true;
                testBtn.innerText = 'Testing...';
            }
            if (statusEl) {
                statusEl.innerText = 'Connecting...';
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const url = isDemo ? '/api/arr/test-connection?demo=true' : '/api/arr/test-connection';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ app: app, url: urlVal, api_key: keyVal })
                });
                const data = await res.json();
                if (res.ok && data.status === 'connected') {
                    if (statusEl) {
                        const ver = data.version ? ` (v${data.version})` : '';
                        statusEl.innerText = `● Connected${ver}`;
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = `● ${data.message || 'Connection failed'}`;
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = `● Error: ${err.message}`;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (testBtn) {
                    testBtn.disabled = false;
                    testBtn.innerText = `🔌 Test ${app.charAt(0).toUpperCase() + app.slice(1)}`;
                }
            }
        }

        async function testNotificationChannel(channel) {
            const ch = (channel || 'discord').toLowerCase();
            const btn = document.getElementById(`settings-notif-${ch}-test-btn`);
            const statusEl = document.getElementById(`settings-notif-${ch}-status`);

            if (btn) {
                btn.disabled = true;
                btn.innerText = 'Testing...';
            }
            if (statusEl) {
                statusEl.innerText = 'Dispatching test alert...';
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const body = { channel: ch };
                if (ch === 'discord') {
                    body.discord_webhook_url = document.getElementById('settings-notif-discord-url')?.value.trim() || '';
                } else if (ch === 'telegram') {
                    body.telegram_bot_token = document.getElementById('settings-notif-telegram-token')?.value.trim() || '';
                    body.telegram_chat_id = document.getElementById('settings-notif-telegram-chat')?.value.trim() || '';
                } else if (ch === 'ntfy') {
                    body.ntfy_url = document.getElementById('settings-notif-ntfy-url')?.value.trim() || '';
                    body.ntfy_auth_token = document.getElementById('settings-notif-ntfy-auth')?.value.trim() || '';
                } else if (ch === 'pushover') {
                    body.pushover_user_key = document.getElementById('settings-notif-pushover-user')?.value.trim() || '';
                    body.pushover_api_token = document.getElementById('settings-notif-pushover-token')?.value.trim() || '';
                } else if (ch === 'gotify') {
                    body.gotify_url = document.getElementById('settings-notif-gotify-url')?.value.trim() || '';
                    body.gotify_token = document.getElementById('settings-notif-gotify-token')?.value.trim() || '';
                } else if (ch === 'matrix') {
                    body.matrix_homeserver_url = document.getElementById('settings-notif-matrix-url')?.value.trim() || '';
                    body.matrix_room_id = document.getElementById('settings-notif-matrix-room')?.value.trim() || '';
                    body.matrix_access_token = document.getElementById('settings-notif-matrix-token')?.value.trim() || '';
                }

                const url = isDemo ? '/api/notifications/test?demo=true' : '/api/notifications/test';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    if (statusEl) {
                        statusEl.innerText = '✓ Alert delivered!';
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = '✗ ' + (data.message || 'Delivery failed');
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = '✗ ' + err.message;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = `🔔 Test ${ch.charAt(0).toUpperCase() + ch.slice(1)}`;
                }
            }
        }

        async function triggerWeeklyDigestNow() {
            const btn = document.getElementById('settings-digest-now-btn');
            const statusEl = document.getElementById('settings-digest-status');

            if (btn) {
                btn.disabled = true;
                btn.innerText = 'Dispatching...';
            }
            if (statusEl) {
                statusEl.innerText = 'Compiling 7-day watch telemetry...';
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const url = isDemo ? '/api/notifications/digest?demo=true' : '/api/notifications/digest';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    if (statusEl) {
                        statusEl.innerText = `✓ ${data.message || 'Weekly digest dispatched successfully!'}`;
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = `✗ ${data.message || 'Failed to send weekly digest'}`;
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = `✗ Error: ${err.message}`;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = '⚡ Send Digest Now';
                }
            }
        }

        async function saveSimklCredsAndStartPin() {
            const clientId = document.getElementById('settings-simkl-client-id')?.value.trim() || '';
            const clientSecret = document.getElementById('settings-simkl-client-secret')?.value.trim() || '';
            if (!clientId) {
                alert('Please enter a Simkl Client ID first.');
                return;
            }
            const statusMsg = document.getElementById('settings-modal-status-msg');
            if (statusMsg) {
                statusMsg.style.color = 'var(--status-finished)';
                statusMsg.innerText = 'Saving Simkl credentials...';
            }
            try {
                const url = isDemo ? '/api/settings?demo=true' : '/api/settings';
                await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        credentials: {
                            simkl: { client_id: clientId, client_secret: clientSecret }
                        }
                    })
                });
                closeSettingsModal();
                openSimklModal();
                startSimklPinFlow();
            } catch (err) {
                if (statusMsg) {
                    statusMsg.style.color = 'var(--status-error)';
                    statusMsg.innerText = 'Error saving Simkl credentials: ' + err.message;
                }
            }
        }

        async function saveAniListFromSettings() {
            const tokenVal = document.getElementById('settings-anilist-token')?.value.trim() || '';
            if (!tokenVal) {
                alert('Please enter an AniList Personal Access Token.');
                return;
            }
            if (tokenVal.startsWith('••••') || tokenVal.startsWith('●●●●')) {
                alert('AniList token is already configured. To update, enter a new Bearer token.');
                return;
            }
            const statusMsg = document.getElementById('settings-modal-status-msg');
            if (statusMsg) {
                statusMsg.style.color = 'var(--status-finished)';
                statusMsg.innerText = 'Verifying AniList token with GraphQL...';
            }
            try {
                const url = isDemo ? '/api/anilist/token?demo=true' : '/api/anilist/token';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: tokenVal })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    if (statusMsg) {
                        statusMsg.style.color = 'var(--status-playing)';
                        statusMsg.innerText = `✓ Connected to AniList as @${data.user || 'user'}!`;
                    }
                    setTimeout(() => {
                        closeSettingsModal();
                        window.location.reload();
                    }, 1200);
                } else {
                    if (statusMsg) {
                        statusMsg.style.color = 'var(--status-error)';
                        statusMsg.innerText = 'AniList verification failed: ' + (data.error || data.detail || 'Invalid token');
                    }
                }
            } catch (err) {
                if (statusMsg) {
                    statusMsg.style.color = 'var(--status-error)';
                    statusMsg.innerText = 'Network error: ' + err.message;
                }
            }
        }

        async function saveMalFromSettings() {
            const clientId = document.getElementById('settings-mal-client-id')?.value.trim() || '';
            const clientSecret = document.getElementById('settings-mal-client-secret')?.value.trim() || '';
            const tokenVal = document.getElementById('settings-mal-token')?.value.trim() || '';
            const statusMsg = document.getElementById('settings-modal-status-msg');

            if (!clientId && !tokenVal) {
                alert('Please enter a MAL Client ID and/or Access Token.');
                return;
            }

            if (statusMsg) {
                statusMsg.style.color = 'var(--status-finished)';
                statusMsg.innerText = 'Saving MAL credentials...';
            }

            try {
                const url = isDemo ? '/api/settings?demo=true' : '/api/settings';
                await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        credentials: {
                            mal: { client_id: clientId, client_secret: clientSecret }
                        }
                    })
                });

                if (tokenVal && !tokenVal.startsWith('••••') && !tokenVal.startsWith('●●●●')) {
                    const tokUrl = isDemo ? '/api/mal/token?demo=true' : '/api/mal/token';
                    const res = await fetch(tokUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: tokenVal })
                    });
                    const data = await res.json();
                    if (!res.ok || data.status !== 'success') {
                        if (statusMsg) {
                            statusMsg.style.color = 'var(--status-error)';
                            statusMsg.innerText = 'MAL token verification failed: ' + (data.error || data.detail || 'Invalid token');
                        }
                        return;
                    }
                }

                if (statusMsg) {
                    statusMsg.style.color = 'var(--status-playing)';
                    statusMsg.innerText = '✓ MAL credentials saved successfully!';
                }
                setTimeout(() => {
                    closeSettingsModal();
                    window.location.reload();
                }, 1200);
            } catch (err) {
                if (statusMsg) {
                    statusMsg.style.color = 'var(--status-error)';
                    statusMsg.innerText = 'Error: ' + err.message;
                }
            }
        }

        async function toggleServerFromSettings(srv) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const currentStatus = currentSettingsData?.servers?.[srv] ?? true;
            const newStatus = !currentStatus;
            try {
                const url = isDemo ? '/api/settings/toggle?demo=true' : '/api/settings/toggle';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: 'server', key: srv, enabled: newStatus })
                });
                if (res.ok) {
                    fetchSettingsModalData();
                } else {
                    const err = await res.json();
                    alert('Failed to toggle server: ' + (err.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function toggleTrackerFromSettings(trk) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const currentStatus = currentSettingsData?.trackers?.[trk] ?? true;
            const newStatus = !currentStatus;
            try {
                const url = isDemo ? '/api/settings/toggle?demo=true' : '/api/settings/toggle';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: 'tracker', key: trk, enabled: newStatus })
                });
                if (res.ok) {
                    fetchSettingsModalData();
                } else {
                    const err = await res.json();
                    alert('Failed to toggle tracker: ' + (err.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        function openReconcileSettingsModal(server = 'plex') {
            if (!isAdmin) {
                openUnlockModal();
                return;
            }
            const modal = document.getElementById('reconcile-settings-modal');
            if (modal) modal.style.display = 'flex';
            const srv = (server && ['plex', 'jellyfin', 'emby'].includes(String(server).toLowerCase())) ? String(server).toLowerCase() : 'plex';
            switchReconSettingsTab(srv);
            fetchReconcileSettings();
        }

        function closeReconcileSettingsModal() {
            const modal = document.getElementById('reconcile-settings-modal');
            if (modal) modal.style.display = 'none';
            const msg = document.getElementById('recon-settings-msg');
            if (msg) msg.style.display = 'none';
            ['plex', 'jellyfin', 'emby'].forEach(s => {
                const testStatus = document.getElementById(`recon-test-status-${s}`);
                if (testStatus) testStatus.innerText = '';
            });
        }

        async function fetchReconcileSettings() {
            const url = isDemo ? '/api/sync/settings?demo=true' : '/api/sync/settings';
            try {
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to load settings');
                const data = await res.json();

                // Plex
                const plexUrlInput = document.getElementById('recon-plex-url');
                if (plexUrlInput) plexUrlInput.value = data.plex_url || '';
                const plexTokenInput = document.getElementById('recon-plex-token');
                const plexBadge = document.getElementById('recon-token-badge-plex');
                if (plexTokenInput) plexTokenInput.value = data.masked_plex_token || '';
                if (plexBadge) {
                    if (data.is_plex_token_set || data.has_plex_token) {
                        plexBadge.innerText = '✓ Configured';
                        plexBadge.style.color = 'var(--status-playing)';
                    } else {
                        plexBadge.innerText = 'Not Configured';
                        plexBadge.style.color = 'var(--text-muted)';
                    }
                }

                // Jellyfin
                const jfUrlInput = document.getElementById('recon-jellyfin-url');
                if (jfUrlInput) jfUrlInput.value = data.jellyfin_url || '';
                const jfTokenInput = document.getElementById('recon-jellyfin-token');
                const jfUserInput = document.getElementById('recon-jellyfin-user-id');
                const jfBadge = document.getElementById('recon-token-badge-jellyfin');
                if (jfTokenInput) jfTokenInput.value = data.masked_jellyfin_token || '';
                if (jfUserInput) jfUserInput.value = data.jellyfin_user_id || '';
                if (jfBadge) {
                    if (data.is_jellyfin_token_set || data.has_jellyfin_token) {
                        jfBadge.innerText = '✓ Configured';
                        jfBadge.style.color = 'var(--status-playing)';
                    } else {
                        jfBadge.innerText = 'Not Configured';
                        jfBadge.style.color = 'var(--text-muted)';
                    }
                }

                // Emby
                const embyUrlInput = document.getElementById('recon-emby-url');
                if (embyUrlInput) embyUrlInput.value = data.emby_url || '';
                const embyTokenInput = document.getElementById('recon-emby-token');
                const embyUserInput = document.getElementById('recon-emby-user-id');
                const embyBadge = document.getElementById('recon-token-badge-emby');
                if (embyTokenInput) embyTokenInput.value = data.masked_emby_token || '';
                if (embyUserInput) embyUserInput.value = data.emby_user_id || '';
                if (embyBadge) {
                    if (data.is_emby_token_set || data.has_emby_token) {
                        embyBadge.innerText = '✓ Configured';
                        embyBadge.style.color = 'var(--status-playing)';
                    } else {
                        embyBadge.innerText = 'Not Configured';
                        embyBadge.style.color = 'var(--text-muted)';
                    }
                }

                // Primary server selection
                const srvTypeSelect = document.getElementById('recon-server-type-select');
                if (srvTypeSelect && data.server_type) srvTypeSelect.value = data.server_type;

                const intSelect = document.getElementById('recon-interval-select');
                if (intSelect) intSelect.value = String(data.interval_minutes !== undefined ? data.interval_minutes : 60);

                const dirSelect = document.getElementById('recon-direction-select');
                if (dirSelect) dirSelect.value = data.direction_default || 'all';

                const ratingsCheck = document.getElementById('recon-ratings-check');
                if (ratingsCheck) ratingsCheck.checked = Boolean(data.sync_ratings);

                const startupCheck = document.getElementById('recon-startup-check');
                if (startupCheck) startupCheck.checked = Boolean(data.sync_on_startup);
            } catch (err) {
                console.error('Error fetching reconcile settings:', err);
            }
        }

        function toggleReconTokenVisibility(inputId, btnId) {
            const tokenInput = document.getElementById(inputId);
            const btn = document.getElementById(btnId);
            if (!tokenInput) return;
            if (tokenInput.type === 'password') {
                tokenInput.type = 'text';
                if (btn) btn.innerText = '🙈';
            } else {
                tokenInput.type = 'password';
                if (btn) btn.innerText = '👁️';
            }
        }

        async function testReconcileConnection(server = 'plex') {
            const srv = (server || activeReconSettingsTab || 'plex').toLowerCase();
            const testBtn = document.getElementById(`recon-test-btn-${srv}`);
            const statusEl = document.getElementById(`recon-test-status-${srv}`);
            const urlVal = document.getElementById(`recon-${srv}-url`)?.value.trim() || '';
            const tokenVal = document.getElementById(`recon-${srv}-token`)?.value.trim() || '';
            const userIdVal = document.getElementById(`recon-${srv}-user-id`)?.value.trim() || '';

            if (testBtn) {
                testBtn.disabled = true;
                testBtn.innerText = 'Testing...';
            }
            if (statusEl) {
                statusEl.innerText = `Connecting to ${srv.charAt(0).toUpperCase() + srv.slice(1)}...`;
                statusEl.style.color = 'var(--status-finished)';
            }

            try {
                const url = isDemo ? '/api/sync/test-connection?demo=true' : '/api/sync/test-connection';
                const body = { server: srv, url: urlVal, token: tokenVal };
                if (userIdVal) body.user_id = userIdVal;

                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (res.ok && data.status === 'connected') {
                    if (statusEl) {
                        const ver = data.version ? ` (v${data.version})` : '';
                        statusEl.innerText = `● Connected${ver}`;
                        statusEl.style.color = 'var(--status-playing)';
                    }
                } else {
                    if (statusEl) {
                        statusEl.innerText = `● ${data.message || 'Connection failed'}`;
                        statusEl.style.color = 'var(--status-error)';
                    }
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.innerText = `● Error: ${err.message}`;
                    statusEl.style.color = 'var(--status-error)';
                }
            } finally {
                if (testBtn) {
                    testBtn.disabled = false;
                    testBtn.innerText = '🔌 Test Connection';
                }
            }
        }

        async function saveReconcileSettings() {
            const saveBtn = document.getElementById('recon-save-btn');
            const msgBox = document.getElementById('recon-settings-msg');

            const srvType = document.getElementById('recon-server-type-select')?.value || 'plex';
            const plexUrlVal = document.getElementById('recon-plex-url')?.value.trim() || '';
            const plexTokenVal = document.getElementById('recon-plex-token')?.value.trim() || '';
            const jfUrlVal = document.getElementById('recon-jellyfin-url')?.value.trim() || '';
            const jfTokenVal = document.getElementById('recon-jellyfin-token')?.value.trim() || '';
            const jfUserIdVal = document.getElementById('recon-jellyfin-user-id')?.value.trim() || '';
            const embyUrlVal = document.getElementById('recon-emby-url')?.value.trim() || '';
            const embyTokenVal = document.getElementById('recon-emby-token')?.value.trim() || '';
            const embyUserIdVal = document.getElementById('recon-emby-user-id')?.value.trim() || '';

            const intVal = parseInt(document.getElementById('recon-interval-select')?.value, 10) || 0;
            const dirVal = document.getElementById('recon-direction-select')?.value || 'all';
            const ratingsVal = document.getElementById('recon-ratings-check')?.checked || false;
            const startupVal = document.getElementById('recon-startup-check')?.checked || false;

            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.innerText = 'Saving...';
            }
            if (msgBox) msgBox.style.display = 'none';

            try {
                const url = isDemo ? '/api/sync/settings?demo=true' : '/api/sync/settings';
                const payload = {
                    server_type: srvType,
                    plex_url: plexUrlVal,
                    jellyfin_url: jfUrlVal,
                    jellyfin_user_id: jfUserIdVal,
                    emby_url: embyUrlVal,
                    emby_user_id: embyUserIdVal,
                    interval_minutes: intVal,
                    direction_default: dirVal,
                    sync_ratings: ratingsVal,
                    sync_on_startup: startupVal
                };
                if (plexTokenVal && !plexTokenVal.startsWith('••••') && !plexTokenVal.startsWith('●●●●')) {
                    payload.plex_token = plexTokenVal;
                }
                if (jfTokenVal && !jfTokenVal.startsWith('••••') && !jfTokenVal.startsWith('●●●●')) {
                    payload.jellyfin_token = jfTokenVal;
                }
                if (embyTokenVal && !embyTokenVal.startsWith('••••') && !embyTokenVal.startsWith('●●●●')) {
                    payload.emby_token = embyTokenVal;
                }

                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    if (saveBtn) {
                        saveBtn.innerText = '✓ Saved!';
                        saveBtn.style.background = 'var(--status-playing)';
                    }
                    if (msgBox) {
                        msgBox.style.display = 'block';
                        msgBox.style.background = 'var(--status-success-bg)';
                        msgBox.style.color = 'var(--status-success-text)';
                        msgBox.innerText = '✓ Reconciliation settings saved successfully!';
                    }
                    setTimeout(() => {
                        closeReconcileSettingsModal();
                        window.location.reload();
                    }, 900);
                } else {
                    const err = await res.json();
                    if (saveBtn) {
                        saveBtn.disabled = false;
                        saveBtn.innerText = '💾 Save Settings';
                    }
                    if (msgBox) {
                        msgBox.style.display = 'block';
                        msgBox.style.background = 'var(--status-error-bg)';
                        msgBox.style.color = 'var(--status-error-text)';
                        msgBox.innerText = 'Error: ' + (err.detail || 'Failed to save settings');
                    }
                }
            } catch (err) {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.innerText = '💾 Save Settings';
                }
                if (msgBox) {
                    msgBox.style.display = 'block';
                    msgBox.style.background = 'var(--status-error-bg)';
                    msgBox.style.color = 'var(--status-error-text)';
                    msgBox.innerText = 'Error: ' + err.message;
                }
            }
        }

// ---- reconciliation.js ----
        async function submitDirectUnscrobble(btn) {
            const msgBox = document.getElementById('direct-status-msg');
            const mediaType = document.getElementById('direct-type-select').value;
            const yearVal = document.getElementById('direct-year-input').value.trim();
            const year = yearVal ? parseInt(yearVal, 10) : undefined;

            let media;
            if (mediaType === 'episode') {
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
                media = {
                    media_type: 'movie',
                    title: movieTitle,
                    year: year
                };
            }

            if (!confirm('Remove / Unscrobble this item across selected trackers?')) return;
            btn.disabled = true;
            btn.textContent = 'Removing...';
            msgBox.style.display = 'none';

            try {
                const url = isDemo ? '/api/history/remove?demo=true' : '/api/history/remove';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        media: media,
                        trackers: getSelectedTrackers(),
                        cowatch: isCowatchChecked()
                    })
                });
                if (res.ok) {
                    btn.textContent = '✓ Removed!';
                    msgBox.style.display = 'block';
                    msgBox.style.background = 'var(--status-success-bg)';
                    msgBox.style.color = 'var(--status-success-text)';
                    msgBox.textContent = '✓ Successfully removed from history across selected trackers!';
                    fetchEvents();
                    setTimeout(() => closeScrobbleModal(), 1400);
                } else {
                    const err = await res.json();
                    btn.disabled = false;
                    btn.textContent = '🗑️ Unscrobble';
                    msgBox.style.display = 'block';
                    msgBox.style.background = 'var(--status-error-bg)';
                    msgBox.style.color = 'var(--status-error-text)';
                    msgBox.textContent = 'Error: ' + (err.detail || 'Failed to remove');
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = '🗑️ Unscrobble';
                msgBox.style.display = 'block';
                msgBox.style.background = 'var(--status-error-bg)';
                msgBox.style.color = 'var(--status-error-text)';
                msgBox.textContent = 'Error: ' + e.message;
            }
        }

        async function quickUnscrobble(mediaJsonEncoded, btn) {
            if (!isAdmin) {
                openUnlockModal();
                return;
            }
            if (!confirm('Unscrobble / Remove this item from connected trackers (Trakt, Simkl, AniList, MAL)?')) return;
            try {
                const payload = JSON.parse(decodeURIComponent(mediaJsonEncoded));
                btn.disabled = true;
                btn.innerHTML = 'Removing...';
                const url = isDemo ? '/api/history/remove?demo=true' : '/api/history/remove';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        media: payload,
                        trackers: ['trakt', 'simkl', 'anilist', 'mal'],
                        cowatch: true
                    })
                });
                if (res.ok) {
                    btn.innerHTML = '✓ Removed';
                    btn.style.background = 'var(--border-color)';
                    btn.style.borderColor = 'var(--border-color)';
                    fetchEvents();
                } else {
                    const err = await res.json();
                    alert('Failed to unscrobble: ' + (err.detail || 'Unknown error'));
                    btn.disabled = false;
                    btn.innerHTML = '🗑️ Unscrobble';
                }
            } catch (e) {
                alert('Error: ' + e.message);
                btn.disabled = false;
                btn.innerHTML = '🗑️ Unscrobble';
            }
        }

        async function toggleSetting(category, key, enabled, btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            if (btn) btn.disabled = true;
            try {
                const url = isDemo ? '/api/settings/toggle?demo=true' : '/api/settings/toggle';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category, key, enabled })
                });
                if (res.ok) {
                    if (isDemo) {
                        updateDemoSettingUI(category, key, enabled, btn);
                    } else {
                        window.location.reload();
                    }
                } else {
                    const err = await res.json();
                    alert('Failed to update setting: ' + (err.detail || 'Unknown error'));
                    if (btn) btn.disabled = false;
                }
            } catch (e) {
                alert('Error: ' + e.message);
                if (btn) btn.disabled = false;
            }
        }

        function updateDemoSettingUI(category, key, enabled, triggeringBtn) {
            const isServer = category === 'server';
            const nameMap = {
                plex: 'Plex',
                jellyfin: 'Jellyfin',
                emby: 'Emby',
                trakt: 'Trakt',
                simkl: 'Simkl',
                anilist: 'AniList',
                mal: 'MAL'
            };
            const name = nameMap[key] || (key.charAt(0).toUpperCase() + key.slice(1));

            // Find all buttons that control this setting
            const allBtns = document.querySelectorAll(`button[onclick*="'${category}', '${key}'"]`);
            const targetBtns = allBtns.length > 0 ? Array.from(allBtns) : (triggeringBtn ? [triggeringBtn] : []);

            targetBtns.forEach(btn => {
                btn.disabled = false;
                const card = btn.closest('.eco-card');
                if (card) {
                    if (!enabled) {
                        card.classList.add('eco-card-disabled');
                        const badge = card.querySelector('.eco-status-badge');
                        if (badge) {
                            badge.textContent = isServer ? 'Disabled' : 'Paused';
                            badge.className = 'eco-status-badge is-disabled';
                        }
                        btn.innerHTML = `<span>▶</span><span>${isServer ? 'Enable' : 'Resume'}</span>`;
                        btn.title = `${isServer ? 'Enable' : 'Resume'} ${name}`;
                        btn.classList.add('eco-control-enable');
                        btn.setAttribute('onclick', `toggleSetting('${category}', '${key}', true, this)`);
                    } else {
                        card.classList.remove('eco-card-disabled');
                        const badge = card.querySelector('.eco-status-badge');
                        if (badge) {
                            if (key === 'plex') {
                                badge.textContent = 'Online';
                                badge.className = 'eco-status-badge is-connected';
                            } else if (key === 'jellyfin' || key === 'emby') {
                                badge.textContent = 'Ready';
                                badge.className = 'eco-status-badge is-available';
                            } else {
                                badge.textContent = 'Active';
                                badge.className = 'eco-status-badge is-connected';
                            }
                        }
                        btn.innerHTML = `<span>⏸</span><span>${isServer ? 'Disable' : 'Pause'}</span>`;
                        btn.title = `${isServer ? 'Disable' : 'Pause'} ${name}`;
                        btn.classList.remove('eco-control-enable');
                        btn.setAttribute('onclick', `toggleSetting('${category}', '${key}', false, this)`);
                    }
                } else {
                    // Standalone tracker buttons (e.g. Simkl, AniList, MAL sections)
                    if (!enabled) {
                        btn.innerHTML = `▶ Resume ${name}`;
                        btn.style.color = 'var(--status-success-text)';
                        btn.setAttribute('onclick', `toggleSetting('${category}', '${key}', true, this)`);
                    } else {
                        btn.innerHTML = `⏸ Pause ${name}`;
                        btn.style.color = 'var(--text-heading)';
                        btn.setAttribute('onclick', `toggleSetting('${category}', '${key}', false, this)`);
                    }
                }
            });
        }

// ---- playback.js ----
        async function fetchPlayback() {
            try {
                const url = isDemo ? '/api/playback?demo=true' : '/api/playback';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    renderPlayback(data);
                }
            } catch (e) {
                console.error('Error fetching playback:', e);
            }
        }

        function renderPlayback(data) {
            const card = document.getElementById('active-playback-card');
            if (!card) return;
            const sessions = data.active_sessions || [];
            const posterImg = document.getElementById('stream-poster-img');
            const posterFallback = document.getElementById('stream-poster-fallback');
            const backdropEl = document.getElementById('stream-ambient-backdrop');

            if (sessions.length > 0) {
                const s = sessions[0];
                card.hidden = false;
                card.classList.remove('playback-state-playing', 'playback-state-paused', 'playback-state-finished');
                card.classList.add(s.state === 'playing' ? 'playback-state-playing' : 'playback-state-paused');
                document.getElementById('stream-state-badge').textContent = s.state === 'playing' ? 'Currently Streaming' : 'Paused';
                const devStr = (isAdmin && s.player) ? (` on ${s.player}${s.device ? ' (' + s.device + ')' : ''}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${s.username}${devStr}`;
                document.getElementById('stream-title').textContent = s.title;
                document.getElementById('stream-trakt-link').href = s.trakt_url || 'https://trakt.tv';
                const progText = s.remaining_str ? `${s.progress.toFixed(1)}% • ${s.remaining_str}` : `${s.progress.toFixed(1)}%`;
                document.getElementById('stream-progress-text').textContent = progText;
                document.getElementById('stream-progress-bar').style.width = `${s.progress}%`;

                // Poster & Ambient Backdrop Update
                const pUrl = s.poster_url || (s.ids && s.ids.imdb ? `https://images.metahub.space/poster/medium/${s.ids.imdb}/img` : null);
                const bUrl = s.backdrop_url || (s.ids && s.ids.imdb ? `https://images.metahub.space/background/medium/${s.ids.imdb}/img` : pUrl);
                if (posterImg && posterFallback) {
                    if (pUrl) {
                        posterImg.src = pUrl;
                        posterImg.hidden = false;
                        posterFallback.hidden = true;
                    } else {
                        posterImg.hidden = true;
                        posterFallback.hidden = false;
                    }
                }
                if (backdropEl) {
                    if (bUrl) {
                        backdropEl.style.backgroundImage = `url('${bUrl}')`;
                    } else {
                        backdropEl.style.backgroundImage = 'none';
                        backdropEl.style.backgroundImage = 'radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%)';
                    }
                }
            } else if (data.recently_finished) {
                const f = data.recently_finished;
                card.hidden = false;
                card.classList.remove('playback-state-playing', 'playback-state-paused', 'playback-state-finished');
                card.classList.add('playback-state-finished');
                document.getElementById('stream-state-badge').textContent = 'Recently Finished';
                const fDevStr = (isAdmin && f.player) ? (` on ${f.player}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${f.username}${fDevStr}`;
                document.getElementById('stream-title').textContent = f.title;
                document.getElementById('stream-trakt-link').href = f.trakt_url || 'https://trakt.tv';
                document.getElementById('stream-progress-text').textContent = '100.0% • Finished';
                document.getElementById('stream-progress-bar').style.width = '100%';

                // Poster & Ambient Backdrop Update
                const pUrl = f.poster_url;
                const bUrl = f.backdrop_url || pUrl;
                if (posterImg && posterFallback) {
                    if (pUrl) {
                        posterImg.src = pUrl;
                        posterImg.hidden = false;
                        posterFallback.hidden = true;
                    } else {
                        posterImg.hidden = true;
                        posterFallback.hidden = false;
                    }
                }
                if (backdropEl) {
                    if (bUrl) {
                        backdropEl.style.backgroundImage = `url('${bUrl}')`;
                    } else {
                        backdropEl.style.backgroundImage = 'none';
                        backdropEl.style.backgroundImage = 'radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%)';
                    }
                }
            } else {
                card.hidden = true;
            }
        }

// ---- activity.js ----
        let allEvents = null;
        let eventsCurrentPage = 1;
        let eventsPageSize = 10;
        const activityFilters = { type: 'all', status: 'all', search: '', user: '' };
        let activityBaselineReady = false;
        const activityFreshKeys = new Set();

        function setActivityFilter(group, value, button) {
            activityFilters[group] = value;
            if (button) {
                const parent = button.closest('.activity-filter-group');
                parent?.querySelectorAll('.activity-filter-chip').forEach((chip) => chip.setAttribute('aria-pressed', String(chip === button)));
            }
            eventsCurrentPage = 1;
            renderRows();
        }

        function setActivitySearch(value) {
            activityFilters.search = String(value || '').trim().toLowerCase();
            eventsCurrentPage = 1;
            renderRows();
        }

        function setActivityUser(value) {
            activityFilters.user = String(value || '');
            eventsCurrentPage = 1;
            renderRows();
        }

        function updateActivityUsers(events) {
            const select = document.getElementById('activity-user-filter');
            if (!select) return;
            const selected = activityFilters.user;
            const users = Array.from(new Set(events.map((ev) => String(ev.user || '')).filter(Boolean))).sort((a, b) => a.localeCompare(b));
            select.replaceChildren(new Option('All users', ''));
            users.forEach((user) => select.add(new Option(user, user)));
            select.value = users.includes(selected) ? selected : '';
            activityFilters.user = select.value;
        }

        function getFilteredActivityEvents(events) {
            return events.filter((ev) => {
                const type = String(ev.type || ev.media_payload?.media_type || '').toLowerCase();
                if (activityFilters.user && String(ev.user || '') !== activityFilters.user) return false;
                if (activityFilters.type === 'movie' && !['movie', 'film'].includes(type)) return false;
                if (activityFilters.type === 'tv' && !['episode', 'show', 'series', 'tv'].includes(type)) return false;
                if (activityFilters.type === 'anime' && !(ev.is_anime || ev.media_payload?.is_anime || type.includes('anime'))) return false;
                const status = String(ev.result_status || '').toLowerCase();
                const trackerStates = Object.values(ev.tracker_delivery || {});
                if (activityFilters.status === 'queued' && status !== 'queued' && !trackerStates.includes('queued')) return false;
                if (activityFilters.status === 'failed' && !(status === 'error' || status === 'failed' || status === '429' || /^4\d\d$/.test(status) || /^5\d\d$/.test(status) || trackerStates.includes('failed'))) return false;
                if (activityFilters.status === 'success' && !(status === 'ok' || status === 'success' || status === '200' || status === '201' || trackerStates.includes('success'))) return false;
                if (activityFilters.search) {
                    const haystack = `${ev.title || ''} ${ev.user || ''}`.toLowerCase();
                    if (!haystack.includes(activityFilters.search)) return false;
                }
                return true;
            });
        }

        function activityEventKey(ev) {
            return `${ev.timestamp || ''}|${ev.title || ''}|${ev.action || ''}|${ev.user || ''}`;
        }

        function showActivityToast(ev) {
            const region = document.getElementById('activity-toast-region');
            if (!region) return;
            const toast = document.createElement('div');
            toast.className = 'activity-toast';
            toast.textContent = `New activity: ${ev.title || 'Media event'}`;
            region.appendChild(toast);
            window.setTimeout(() => toast.remove(), 4500);
        }

        function formatActionLabel(rawAction) {
            if (!rawAction) return '';
            const clean = String(rawAction).trim();
            if (clean === 'scrobble_start') return 'play';
            if (clean === 'scrobble_pause') return 'pause';
            if (clean === 'scrobble_stop') return 'scrobble';
            if (clean === 'mark_watched') return 'scrobble';
            if (clean === 'playback_stopped') return 'stop';
            if (clean === 'test_webhook') return 'test';
            if (clean === 'none') return 'ignored';
            return clean;
        }

        function shouldDisplayCowatchBadge(action, resultStatus, progress) {
            const act = String(action || '').toLowerCase().trim();
            const status = String(resultStatus || '').toLowerCase().trim();
            const prog = String(progress || '').trim();
            if (status === 'ignored' || status === 'error' || prog === '0.0%') {
                return false;
            }
            return act.startsWith('mark_watched') || act.startsWith('scrobble_stop') || act.startsWith('test_webhook') || act === 'scrobble' || act === 'watched';
        }

        function renderStatusBadge(action, resultStatus, progress, cowatchStatus) {
            const rawAct = String(action || '').toLowerCase().trim();
            const cleanAct = formatActionLabel(rawAct).toLowerCase();
            const stat = String(resultStatus || '').toLowerCase().trim();
            const cw = cowatchStatus;

            if (cw && cw.synced && shouldDisplayCowatchBadge(action, resultStatus, progress)) {
                const targetTxt = cw.target ? `@${cw.target}` : 'partner';
                const reasonTxt = escapeHtml(cw.reason || 'Shared show whitelist match');
                return `<span class="activity-status-badge activity-status-cowatch" title="Synced to ${escapeHtml(targetTxt)}: ${reasonTxt}">👥 Co-Watched</span>`;
            }

            if (stat === 'ok' || stat === '200' || stat === '201') {
                let label = '✓ OK';
                let tooltip = 'Action successful';
                if (cleanAct === 'scrobble' || rawAct.startsWith('mark_watched') || rawAct.startsWith('scrobble_stop')) {
                    label = '✓ Scrobbled';
                    if (cw && cw.reason) {
                        tooltip = `Scrobbled (Solo: ${escapeHtml(cw.reason)})`;
                    } else {
                        tooltip = 'Scrobbled to connected trackers';
                    }
                } else if (cleanAct === 'collection' || rawAct === 'collection') {
                    label = '✓ Added';
                    tooltip = 'Added to collection';
                } else if (cleanAct === 'rate' || rawAct.startsWith('rate')) {
                    label = '✓ Rated';
                    tooltip = 'Rating synchronized';
                }
                return `<span class="activity-status-badge activity-status-success" title="${tooltip}">${label}</span>`;
            }

            if (stat === 'ignored') {
                return '<span class="activity-status-badge activity-status-ignored" title="Playback or event skipped">Ignored</span>';
            }

            if (stat === 'queued') {
                return '<span class="activity-status-badge activity-status-queued" title="Saved to offline retry queue">⏳ Queued</span>';
            }

            if (stat === 'error' || stat === '500' || stat === '502' || stat === '503' || stat === '504') {
                return '<span class="activity-status-badge activity-status-failed" title="Action failed">✕ Failed</span>';
            }

            return `<span class="activity-status-badge activity-status-unknown">${escapeHtml(resultStatus)}</span>`;
        }

        function renderTrackerDeliveryBadges(delivery) {
            const labels = { trakt: 'TRK', simkl: 'SKL', anilist: 'ANL', mal: 'MAL', myanimelist: 'MAL', kitsu: 'KTS', tmdb: 'TMDB', letterboxd: 'LBD', serializd: 'SER', mdblist: 'MDB' };
            const states = {
                success: ['✓', 'Delivered'], queued: ['⌛', 'Queued for retry'],
                failed: ['×', 'Delivery failed'], skipped: ['—', 'Not applicable to this event']
            };
            const badges = Object.entries(delivery || {}).map(([tracker, status]) => {
                const label = labels[String(tracker).toLowerCase()];
                const state = states[String(status).toLowerCase()];
                if (!label || !state) return '';
                const title = `${label}: ${state[1]}`;
                return `<span class="tracker-delivery-badge tracker-delivery-${String(status).toLowerCase()}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${label} ${state[0]}</span>`;
            }).join('');
            return badges ? `<span class="tracker-delivery-badges" aria-label="Tracker delivery">${badges}</span>` : '';
        }

        function renderRows(events) {
            if (events !== undefined && events !== null) {
                const previous = allEvents || [];
                const previousKeys = new Set(previous.map(activityEventKey));
                const incoming = events;
                const autoRefresh = document.getElementById('auto-refresh-toggle')?.checked;
                if (activityBaselineReady && autoRefresh) {
                    incoming.filter((ev) => !previousKeys.has(activityEventKey(ev))).slice(0, 3).forEach((ev) => {
                        activityFreshKeys.add(activityEventKey(ev));
                        showActivityToast(ev);
                    });
                }
                allEvents = events;
                activityBaselineReady = true;
                updateActivityUsers(allEvents);
            }
            const tbody = document.getElementById('events-tbody');
            const colSpan = isAdmin ? 7 : 6;
            const allItems = allEvents || [];
            const items = getFilteredActivityEvents(allItems);
            const total = items.length;

            const pageInfo = document.getElementById('events-page-info');
            const pageNum = document.getElementById('events-page-num');
            const prevBtn = document.getElementById('events-prev-btn');
            const nextBtn = document.getElementById('events-next-btn');

            if (total === 0) {
                const message = allItems.length ? 'No events match these filters.' : 'No scrobble events received yet. Start playing media on Plex, Jellyfin, or Emby to test!';
                tbody.innerHTML = `<tr><td colspan="${colSpan}" class="activity-empty">${escapeHtml(message)}</td></tr>`;
                if (pageInfo) pageInfo.textContent = allItems.length ? `0 of ${allItems.length} events` : '0 events';
                if (pageNum) pageNum.textContent = 'Page 1 of 1';
                if (prevBtn) prevBtn.disabled = true;
                if (nextBtn) nextBtn.disabled = true;
                return;
            }

            const effectiveSize = eventsPageSize === 'all' ? total : parseInt(eventsPageSize, 10);
            const totalPages = Math.max(1, Math.ceil(total / effectiveSize));
            if (eventsCurrentPage > totalPages) eventsCurrentPage = totalPages;
            if (eventsCurrentPage < 1) eventsCurrentPage = 1;

            const startIdx = (eventsCurrentPage - 1) * effectiveSize;
            const endIdx = Math.min(startIdx + effectiveSize, total);
            const pageEvents = items.slice(startIdx, endIdx);

            if (pageInfo) pageInfo.textContent = `Showing ${startIdx + 1}–${endIdx} of ${total} events`;
            if (pageNum) pageNum.textContent = `Page ${eventsCurrentPage} of ${totalPages}`;
            if (prevBtn) prevBtn.disabled = eventsCurrentPage <= 1;
            if (nextBtn) nextBtn.disabled = eventsCurrentPage >= totalPages;

            let html = '';
            for (const ev of pageEvents) {
                let actionBtns = '';
                if (isAdmin) {
                    const showTitle = ev.show_title || (ev.type === 'show' ? (ev.media_payload?.title || ev.title) : null);
                    if (showTitle && !ev.is_cowatch_show) {
                        const showEnc = encodeURIComponent(showTitle);
                        actionBtns += `<button data-show="${showEnc}" onclick="quickAddShow(decodeURIComponent(this.dataset.show), this)" class="btn-sm activity-row-button activity-row-button-cowatch" title="Add show to co-watch whitelist">+ Co-Watch</button>`;
                    }
                    if (ev.media_payload) {
                        const mediaEnc = encodeURIComponentForInlineJs(JSON.stringify(ev.media_payload));
                        const rawAct = String(ev.action || '').toLowerCase().trim();
                        const resStat = String(ev.result_status || '').toLowerCase().trim();
                        const progVal = String(ev.progress || '').trim();
                        const isCompletion = rawAct.startsWith('mark_watched') || rawAct.startsWith('scrobble_stop') || rawAct.startsWith('collection') || rawAct.startsWith('rate') || rawAct === 'scrobble' || rawAct === 'watched';
                        if (isCompletion && resStat !== 'ignored' && progVal !== '0.0%') {
                            const cw = ev.cowatch_status || {};
                            if (!cw.synced) {
                                actionBtns += `<button onclick="quickSyncPartner('${mediaEnc}', this)" class="btn-sm activity-row-button activity-row-button-partner" title="Sync to partner">+ Sync Partner</button>`;
                            }
                            if (rawAct.startsWith('mark_watched') || rawAct.startsWith('scrobble_stop') || rawAct === 'scrobble' || rawAct === 'watched') {
                                actionBtns += `<button onclick="quickUnscrobble('${mediaEnc}', this)" class="btn-sm activity-row-button activity-row-button-danger" title="Unscrobble / Remove from connected trackers">🗑️ Unscrobble</button>`;
                            }
                        }
                    }
                }
                const actionCol = isAdmin ? `<td class="activity-actions-cell"><div class="activity-row-actions">${actionBtns}</div></td>` : '';

                const statusBadgeHtml = renderStatusBadge(ev.action, ev.result_status, ev.progress, ev.cowatch_status);

                const srv = (ev.server || 'plex').toLowerCase();
                let serverBadge = '<span class="activity-server-badge activity-server-plex">Plex</span>';
                if (srv === 'jellyfin') {
                    serverBadge = '<span class="activity-server-badge activity-server-jellyfin">Jellyfin</span>';
                } else if (srv === 'emby') {
                    serverBadge = '<span class="activity-server-badge activity-server-emby">Emby</span>';
                }

                const rawAction = ev.action || '';
                const cleanAction = formatActionLabel(rawAction);
                const prog = ev.progress ? String(ev.progress).trim() : '';
                let actionText = cleanAction;
                if (prog && !cleanAction.includes(prog) && !cleanAction.includes('(') && cleanAction.toLowerCase() !== 'collection' && cleanAction.toLowerCase() !== 'ignored') {
                    actionText = `${cleanAction} (${prog})`;
                }

                const rowClass = ['activity-row', activityFreshKeys.has(activityEventKey(ev)) ? 'activity-row-enter' : ''].filter(Boolean).join(' ');
                html += `
                <tr class="${rowClass}">
                    <td class="activity-time">${escapeHtml(ev.timestamp)}</td>
                    <td class="activity-title">${escapeHtml(ev.title)}</td>
                    <td><span class="activity-type">${escapeHtml(ev.type)}</span></td>
                    <td class="activity-user"><div class="activity-user-content">${serverBadge}<span>${escapeHtml(ev.user)}</span></div></td>
                    <td><span class="activity-action">${escapeHtml(actionText)}</span></td>
                    <td><div class="activity-status-group">${statusBadgeHtml}${renderTrackerDeliveryBadges(ev.tracker_delivery)}</div></td>
                    ${actionCol}
                </tr>`;
            }
            tbody.innerHTML = html;
            activityFreshKeys.clear();
        }

        function changeEventsPageSize(newSize) {
            eventsPageSize = newSize;
            eventsCurrentPage = 1;
            renderRows();
        }

        function prevEventsPage() {
            if (eventsCurrentPage > 1) {
                eventsCurrentPage--;
                renderRows();
            }
        }

        function nextEventsPage() {
            const effectiveSize = eventsPageSize === 'all' ? (allEvents ? allEvents.length : 1) : parseInt(eventsPageSize, 10);
            const totalPages = Math.max(1, Math.ceil((allEvents ? allEvents.length : 0) / effectiveSize));
            if (eventsCurrentPage < totalPages) {
                eventsCurrentPage++;
                renderRows();
            }
        }

        async function fetchEvents() {
            try {
                const url = isDemo ? '/api/events?demo=true' : '/api/events';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    renderRows(data.events);
                }
            } catch (e) {
                console.error('Error fetching live events:', e);
            }
            fetchPlayback();
        }

        async function retryQueue() {
            if (!isAdmin) {
                openUnlockModal();
                return;
            }
            if (isDemo) {
                alert('Demo mode: queue retry simulated (0 pending items).');
                return;
            }
            try {
                const res = await fetch('/api/queue/retry', { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                }
            } catch (e) {
                console.error('Error retrying queue:', e);
            }
        }

        async function clearHistory() {
            if (!isAdmin) {
                openUnlockModal();
                return;
            }
            if (confirm('Clear all recent activity logs?')) {
                const url = isDemo ? '/api/events/clear?demo=true' : '/api/events/clear';
                await fetch(url, { method: 'POST' });
                allEvents = [];
                eventsCurrentPage = 1;
                fetchEvents();
            }
        }

        function toggleAutoRefresh(cb) {
            const val = cb.checked ? 'true' : 'false';
            localStorage.setItem('omniscrobble_auto_refresh', val);
            localStorage.setItem('plex_trakt_auto_refresh', val);
            if (cb.checked) {
                refreshTimer = setInterval(fetchEvents, 30000);
            } else {
                if (refreshTimer) clearInterval(refreshTimer);
                refreshTimer = null;
            }
        }

// ---- cowatch.js ----
        let sonarrDebounceTimer = null;
        async function onCowatchShowInput(val) {
            clearTimeout(sonarrDebounceTimer);
            const box = document.getElementById('sonarr-suggestions');
            if (!box) return;
            if (!val || val.trim().length < 1) {
                box.style.display = 'none';
                return;
            }
            sonarrDebounceTimer = setTimeout(async () => {
                try {
                    const url = isDemo ? '/api/sonarr/shows?demo=true&q=' + encodeURIComponent(val.trim()) : '/api/sonarr/shows?q=' + encodeURIComponent(val.trim());
                    const res = await fetch(url);
                    if (!res.ok) { box.style.display = 'none'; return; }
                    const data = await res.json();
                    if (!data.configured || !data.shows || data.shows.length === 0) {
                        box.style.display = 'none';
                        return;
                    }
                    box.innerHTML = data.shows.map(s => {
                        const yr = s.year ? ` (${escapeHtml(s.year)})` : '';
                        const st = s.status ? ` • <span class="u-color-text-muted u-font-size-11px">${escapeHtml(s.status)}</span>` : '';
                        const encTitle = encodeURIComponent(s.title);
                        const safeTitle = escapeHtml(s.title);
                        return `<div data-title="${encTitle}" onclick="selectSonarrShow(decodeURIComponent(this.dataset.title))" class="sonarr-suggestion-row u-padding-8px-12px u-cursor-pointer u-font-size-13px u-border-bottom-1px-solid-334155 u-color-text-main u-display-flex u-justify-content-space-between u-align-items-center">
                            <div><strong>${safeTitle}</strong><span class="u-color-text-muted u-font-size-12px">${yr}</span>${st}</div>
                            <span class="u-color-accent-color u-font-size-11px u-font-weight-600">+ Select</span>
                        </div>`;
                    }).join('');
                    box.style.display = 'block';
                } catch (e) {
                    box.style.display = 'none';
                }
            }, 200);
        }

        function escapeHtml(str) {
            return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
        }

        function filterCowatchChips(query) {
            const q = (query || '').toLowerCase().trim();
            const chips = document.querySelectorAll('.cowatch-chip');
            chips.forEach(chip => {
                const title = chip.getAttribute('data-title') || '';
                chip.style.display = (!q || title.includes(q)) ? 'inline-flex' : 'none';
            });
        }

        function renderCowatchChips(shows) {
            const container = document.getElementById('cowatch-chips-container');
            const countBadge = document.getElementById('cowatch-count-badge');
            if (!container) return;
            if (countBadge) countBadge.textContent = shows ? shows.length : 0;
            if (!shows || shows.length === 0) {
                container.innerHTML = '<span class="cowatch-empty">No shows added yet. Add shows below or directly from recent activity.</span>';
                return;
            }
            const sorted = [...shows].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
            container.innerHTML = sorted.map(s => {
                const enc = encodeURIComponent(s);
                const safeTitle = escapeHtml(s);
                return `<span class="cowatch-chip" data-title="${safeTitle.toLowerCase()}">
                    ${safeTitle}
                    <button data-show="${enc}" onclick="removeCowatchShow(decodeURIComponent(this.dataset.show))" title="Remove ${safeTitle}" class="cowatch-chip-del">&times;</button>
                </span>`;
            }).join('');
            const filterInput = document.getElementById('cowatch-filter-input');
            if (filterInput && filterInput.value) {
                filterCowatchChips(filterInput.value);
            }
        }

        function selectSonarrShow(title) {
            const input = document.getElementById('cowatch-show-input');
            const box = document.getElementById('sonarr-suggestions');
            if (input) input.value = title;
            if (box) box.style.display = 'none';
            addCowatchShow();
        }

        document.addEventListener('click', (e) => {
            const box = document.getElementById('sonarr-suggestions');
            const input = document.getElementById('cowatch-show-input');
            if (box && e.target !== input && !box.contains(e.target)) {
                box.style.display = 'none';
            }
        });

        async function addCowatchShow() {
            if (!isAdmin) { openUnlockModal(); return; }
            const input = document.getElementById('cowatch-show-input');
            const showName = input ? input.value.trim() : '';
            if (!showName) return;
            try {
                const url = isDemo ? '/api/cowatch/shows?demo=true' : '/api/cowatch/shows';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ show: showName })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (input) input.value = '';
                    renderCowatchChips(data.shows);
                } else {
                    const err = await res.json();
                    alert('Failed to add show: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function removeCowatchShow(showName) {
            if (!isAdmin) { openUnlockModal(); return; }
            if (!confirm(`Remove "${showName}" from shared shows?`)) return;
            try {
                const url = isDemo ? '/api/cowatch/shows?demo=true&show=' + encodeURIComponent(showName) : '/api/cowatch/shows?show=' + encodeURIComponent(showName);
                const res = await fetch(url, {
                    method: 'DELETE'
                });
                if (res.ok) {
                    const data = await res.json();
                    renderCowatchChips(data.shows);
                } else {
                    const err = await res.json();
                    alert('Failed to remove show: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        function renderCowatchDeviceChips(devices) {
            const container = document.getElementById('cowatch-devices-chips-container');
            const countBadge = document.getElementById('cowatch-devices-count-badge');
            const hasDevs = Array.isArray(devices) && devices.length > 0;

            if (countBadge) countBadge.textContent = hasDevs ? devices.length : 'All';

            if (!container) return;
            if (!hasDevs) {
                container.innerHTML = '<span class="cowatch-empty">All devices allowed (no device filtering). Playback on any player triggers co-watch.</span>';
                return;
            }

            const sorted = [...devices].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
            container.innerHTML = sorted.map(d => {
                const enc = encodeURIComponent(d);
                const safeDev = escapeHtml(d);
                return `<span class="cowatch-device-chip" data-title="${safeDev.toLowerCase()}">
                    📺 ${safeDev}
                    <button data-device="${enc}" onclick="removeCowatchDevice(decodeURIComponent(this.dataset.device))" title="Remove ${safeDev}" class="cowatch-chip-del">&times;</button>
                </span>`;
            }).join('');
        }

        async function addCowatchDevice(devName) {
            if (!isAdmin) { openUnlockModal(); return; }
            const input = document.getElementById('cowatch-device-input');
            const targetDev = (devName !== undefined ? devName : (input ? input.value : '')).trim();
            if (!targetDev) return;
            try {
                const url = isDemo ? '/api/cowatch/devices?demo=true' : '/api/cowatch/devices';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ device: targetDev })
                });
                if (res.ok) {
                    const data = await res.json();
                    if (input && devName === undefined) input.value = '';
                    renderCowatchDeviceChips(data.devices);
                } else {
                    const err = await res.json();
                    alert('Failed to add device: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function removeCowatchDevice(devName) {
            if (!isAdmin) { openUnlockModal(); return; }
            if (!confirm(`Remove "${devName}" from allowed co-watch devices?`)) return;
            try {
                const url = isDemo ? '/api/cowatch/devices?demo=true&device=' + encodeURIComponent(devName) : '/api/cowatch/devices?device=' + encodeURIComponent(devName);
                const res = await fetch(url, {
                    method: 'DELETE'
                });
                if (res.ok) {
                    const data = await res.json();
                    renderCowatchDeviceChips(data.devices);
                } else {
                    const err = await res.json();
                    alert('Failed to remove device: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function quickAddShow(showName, btn) {
            if (!isAdmin) { openUnlockModal(); return; }
            btn.disabled = true;
            btn.textContent = 'Adding...';
            try {
                const url = isDemo ? '/api/cowatch/shows?demo=true' : '/api/cowatch/shows';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ show: showName })
                });
                if (res.ok) {
                    const data = await res.json();
                    btn.textContent = '✓ Added';
                    btn.style.background = 'var(--status-playing)';
                    renderCowatchChips(data.shows);
                } else {
                    btn.textContent = 'Error';
                    btn.disabled = false;
                }
            } catch (e) {
                btn.textContent = 'Error';
                btn.disabled = false;
            }
        }

        async function quickSyncPartner(payloadEnc, btn) {
            if (!isAdmin) { openUnlockModal(); return; }
            if (isDemo) {
                btn.textContent = '✓ Synced';
                btn.style.background = 'var(--status-playing)';
                btn.disabled = true;
                return;
            }
            btn.disabled = true;
            btn.textContent = 'Syncing...';
            try {
                const payload = JSON.parse(decodeURIComponent(payloadEnc));
                const res = await fetch('/api/cowatch/sync', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    btn.textContent = '✓ Synced';
                    btn.style.background = 'var(--status-playing)';
                } else {
                    const err = await res.json();
                    alert('Sync failed: ' + (err.detail || 'Error'));
                    btn.textContent = 'Error';
                }
            } catch (e) {
                alert('Error: ' + e.message);
                btn.textContent = 'Error';
            }
        }

        function promptLinkAccount() {
            if (!isAdmin) { openUnlockModal(); return; }
            if (isDemo) {
                alert('Demo mode: linking simulated.');
                return;
            }
            const uname = prompt('Enter media server username:');
            if (uname && uname.trim()) {
                window.location.href = '/auth?user=' + encodeURIComponent(uname.trim());
            }
        }

        function openHouseholdRuleModal() {
            if (!isAdmin) { openUnlockModal(); return; }
            const modal = document.getElementById('household-rule-modal');
            if (modal) modal.style.display = 'flex';
        }

        function closeHouseholdRuleModal() {
            const modal = document.getElementById('household-rule-modal');
            if (modal) modal.style.display = 'none';
        }

        async function submitHouseholdRule() {
            if (!isAdmin) { openUnlockModal(); return; }
            const name = (document.getElementById('hr-rule-name')?.value || '').trim();
            const targetsRaw = (document.getElementById('hr-rule-targets')?.value || '').trim();
            const devicesRaw = (document.getElementById('hr-rule-devices')?.value || '').trim();
            const showsRaw = (document.getElementById('hr-rule-shows')?.value || '').trim();
            const movieChecked = document.getElementById('hr-type-movie')?.checked ?? true;
            const episodeChecked = document.getElementById('hr-type-episode')?.checked ?? true;

            const targets = targetsRaw.split(',').map(s => s.trim().replace(/^@/, '')).filter(Boolean);
            const devices = devicesRaw ? devicesRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
            const shows = showsRaw ? showsRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
            const media_types = [];
            if (movieChecked) media_types.push('movie');
            if (episodeChecked) media_types.push('episode');

            if (!targets.length) {
                alert('Please provide at least one target profile username.');
                return;
            }

            try {
                const url = isDemo ? '/api/household/rules?demo=true' : '/api/household/rules';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: name,
                        targets: targets,
                        devices: devices,
                        shows: shows,
                        media_types: media_types,
                        enabled: true
                    })
                });
                if (res.ok) {
                    closeHouseholdRuleModal();
                    loadHouseholdRules();
                } else {
                    const err = await res.json();
                    alert('Failed to save rule: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error saving household rule: ' + e.message);
            }
        }

        async function deleteHouseholdRule(ruleId) {
            if (!isAdmin) { openUnlockModal(); return; }
            if (!confirm('Are you sure you want to delete this household routing rule?')) return;
            try {
                const url = isDemo ? `/api/household/rules/${encodeURIComponent(ruleId)}?demo=true` : `/api/household/rules/${encodeURIComponent(ruleId)}`;
                const res = await fetch(url, { method: 'DELETE' });
                if (res.ok) {
                    loadHouseholdRules();
                } else {
                    const err = await res.json();
                    alert('Failed to delete rule: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function toggleHouseholdRule(ruleId) {
            if (!isAdmin) { openUnlockModal(); return; }
            try {
                const url = isDemo ? `/api/household/rules/${encodeURIComponent(ruleId)}/toggle?demo=true` : `/api/household/rules/${encodeURIComponent(ruleId)}/toggle`;
                const res = await fetch(url, { method: 'POST' });
                if (res.ok) {
                    loadHouseholdRules();
                } else {
                    const err = await res.json();
                    alert('Failed to toggle rule: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function loadHouseholdRules() {
            try {
                const url = isDemo ? '/api/household/rules?demo=true' : '/api/household/rules';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    renderHouseholdRulesList(data.rules || []);
                }
            } catch (e) {
                console.error('Failed to load household rules:', e);
            }
        }

        function renderHouseholdRulesList(rules) {
            const container = document.getElementById('household-rules-container');
            const badge = document.getElementById('household-rules-count-badge');
            if (badge) badge.innerText = rules.length;
            if (!container) return;

            if (!rules.length) {
                container.innerHTML = '<div class="household-rules-empty">No custom household routing rules configured. Secondary scrobbles follow the default partner settings above.</div>';
                return;
            }

            container.innerHTML = rules.map(r => {
                const rid = escapeHtml(r.id || '');
                const rname = escapeHtml(r.name || 'Rule');
                const renabled = r.enabled !== false;
                const targetsStr = escapeHtml((r.targets || []).map(t => '@' + t).join(', ') || 'None');
                const devicesStr = escapeHtml((r.devices && r.devices.length) ? r.devices.join(', ') : 'All Devices');
                const showsStr = escapeHtml((r.shows && r.shows.length) ? r.shows.join(', ') : 'All Shows');
                const mediaStr = escapeHtml((r.media_types && r.media_types.length) ? r.media_types.map(m => m.charAt(0).toUpperCase() + m.slice(1)).join(', ') : 'All Media');

                const statusTxt = renabled ? 'Active' : 'Paused';

                let actions = '';
                if (isAdmin) {
                    const encId = encodeURIComponentForInlineJs(r.id || '');
                    actions = `
                    <div class="household-rule-actions">
                        <button onclick="toggleHouseholdRule(decodeURIComponent('${encId}'))" class="btn-sm household-rule-toggle">
                            ${renabled ? 'Pause' : 'Activate'}
                        </button>
                        <button onclick="deleteHouseholdRule(decodeURIComponent('${encId}'))" class="btn-sm household-rule-delete">
                            &times; Delete
                        </button>
                    </div>`;
                }

                return `
                <div class="household-rule-card">
                    <div class="household-rule-heading">
                        <div class="household-rule-title-group">
                            <strong>${rname}</strong>
                            <span class="household-rule-status is-${renabled ? 'active' : 'paused'}">${statusTxt}</span>
                        </div>
                        ${actions}
                    </div>
                    <div class="household-rule-details">
                        <div><span>Targets:</span> <strong>${targetsStr}</strong></div>
                        <div><span>Players:</span> <strong>📺 ${devicesStr}</strong></div>
                        <div><span>Media:</span> <strong>🎬 ${mediaStr}</strong></div>
                        <div><span>Shows:</span> <strong>📺 ${showsStr}</strong></div>
                    </div>
                </div>`;
            }).join('');
        }

        async function uploadBackup(input) {
            if (!isAdmin) { openUnlockModal(); return; }
            if (isDemo) {
                alert('Demo mode: backup restore simulated successfully.');
                input.value = '';
                return;
            }
            if (!input.files || !input.files[0]) return;
            const file = input.files[0];
            if (!confirm(`Restore system configuration and tokens from "${file.name}"? This will overwrite existing tokens and configuration.`)) {
                input.value = '';
                return;
            }
            const formData = new FormData();
            formData.append('backup_file', file);
            try {
                const res = await fetch('/api/restore', {
                    method: 'POST',
                    body: formData
                });
                if (res.ok) {
                    alert('✓ Backup successfully restored!');
                    window.location.reload();
                } else {
                    const err = await res.json();
                    alert('Restore failed: ' + (err.detail || 'Error'));
                }
            } catch (e) {
                alert('Restore error: ' + e.message);
            }
            input.value = '';
        }

// ---- diagnostics.js ----
        let allRawLogs = [];

        function openLogsModal() {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            openDiagnosticsDrawer('logs');
            fetchLogs();
        }

        function closeLogsModal() {
            closeDiagnosticsDrawer();
        }

        function openDiagnosticsDrawer(tab = 'logs') {
            const drawer = document.getElementById('diagnostics-modal');
            if (drawer) drawer.style.display = 'flex';
            switchDiagnosticsTab(tab);
        }

        function switchDiagnosticsTab(tab) {
            ['logs', 'inspector'].forEach((name) => {
                const pane = document.getElementById(`diagnostics-pane-${name}`);
                const button = document.getElementById(`diagnostics-tab-${name}`);
                if (pane) pane.hidden = name !== tab;
                if (button) button.setAttribute('aria-selected', String(name === tab));
            });
            if (tab === 'inspector') loadDebugWebhooks();
        }

        function closeDiagnosticsDrawer() {
            const drawer = document.getElementById('diagnostics-modal');
            if (drawer) drawer.style.display = 'none';
        }

        async function fetchLogs() {
            const terminal = document.getElementById('logs-terminal');
            const badge = document.getElementById('logs-source-badge');
            if (!terminal) return;
            try {
                const url = isDemo ? '/api/logs?demo=true' : '/api/logs';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    if (badge) {
                        badge.textContent = data.source === 'journalctl' ? 'journalctl --user' : (data.source.includes('demo') ? 'simulated journal' : 'app buffer');
                    }
                    allRawLogs = data.lines || [];
                    filterLogs();
                } else if (res.status === 401) {
                    closeLogsModal();
                    openUnlockModal();
                } else {
                    terminal.innerHTML = '<span class="u-color-ef4444">Failed to load logs from server.</span>';
                }
            } catch (e) {
                terminal.innerHTML = '<span class="u-color-ef4444">Network error fetching logs: ' + escapeHtml(e.message) + '</span>';
            }
        }

        function filterLogs() {
            const searchInput = document.getElementById('logs-search-input');
            const levelSelect = document.getElementById('logs-level-select');
            const terminal = document.getElementById('logs-terminal');
            const countLabel = document.getElementById('logs-count-label');
            const autoScroll = document.getElementById('logs-tail-checkbox');
            if (!terminal) return;

            const query = (searchInput ? searchInput.value : '').toLowerCase().trim();
            const level = levelSelect ? levelSelect.value : 'ALL';

            const filtered = allRawLogs.filter(line => {
                const lower = line.toLowerCase();
                if (query && !lower.includes(query)) return false;
                if (level === 'ERROR') return lower.includes('[error]') || lower.includes('error:');
                if (level === 'WARNING') return lower.includes('[warning]') || lower.includes('warning:') || lower.includes('[error]') || lower.includes('error:');
                if (level === 'INFO') return lower.includes('[info]') || lower.includes('[warning]') || lower.includes('[error]');
                return true;
            });

            if (countLabel) countLabel.textContent = `${filtered.length} lines`;

            if (filtered.length === 0) {
                terminal.innerHTML = '<span class="u-color-text-muted u-font-style-italic">No log lines matched filter criteria.</span>';
                return;
            }

            terminal.innerHTML = filtered.map(line => {
                const safe = escapeHtml(line);
                if (safe.includes('[ERROR]') || safe.includes('ERROR:')) {
                    return `<div class="u-color-f87171">${safe}</div>`;
                }
                if (safe.includes('[WARNING]') || safe.includes('WARNING:')) {
                    return `<div class="u-color-fbbf24">${safe}</div>`;
                }
                if (safe.includes('[INFO]')) {
                    return `<div class="u-color-text-heading"><span class="u-color-accent-color">[INFO]</span>${safe.replace('[INFO]', '')}</div>`;
                }
                return `<div>${safe}</div>`;
            }).join('');

            if (autoScroll && autoScroll.checked) {
                terminal.scrollTop = terminal.scrollHeight;
            }
        }

        function copyLogsToClipboard() {
            const btn = document.getElementById('copy-logs-btn');
            const text = allRawLogs.join('\n');
            if (!navigator.clipboard) {
                alert('Clipboard API not available');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                if (!btn) return;
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Copied!';
                btn.style.background = 'var(--status-playing)';
                setTimeout(() => {
                    btn.innerHTML = orig;
                    btn.style.background = 'var(--accent-color)';
                }, 2000);
            }).catch(err => {
                alert('Failed to copy: ' + err);
            });
        }

// ---- webhook_tests.js ----
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
                const url = isDemo ? '/api/sync/reconcile?demo=true' : '/api/sync/reconcile';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
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

// ---- arr.js ----
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

// ---- tracker_auth.js ----
        let simklPollInterval = null;

        function openSimklModal() {
            const modal = document.getElementById('simkl-modal');
            if (modal) modal.style.display = 'flex';
            fetchSimklStatus();
        }

        function closeSimklModal() {
            const modal = document.getElementById('simkl-modal');
            if (modal) modal.style.display = 'none';
            if (simklPollInterval) {
                clearInterval(simklPollInterval);
                simklPollInterval = null;
            }
        }

        async function fetchSimklStatus() {
            const container = document.getElementById('simkl-modal-content');
            if (!container) return;
            try {
                const url = isDemo ? '/api/simkl/status?demo=true' : '/api/simkl/status';
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to load status');
                const data = await res.json();
                renderSimklModal(data);
            } catch (err) {
                container.innerHTML = `<div class="u-color-f87171 u-text-align-center u-padding-16px">Failed to load Simkl status: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderSimklModal(data) {
            const container = document.getElementById('simkl-modal-content');
            if (!container) return;

            if (data.authenticated) {
                const userDisp = data.user || 'Linked Account';
                const accId = data.account_id ? ` (ID: #${escapeHtml(String(data.account_id))})` : '';
                container.innerHTML = `
                    <div class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-12px-14px u-border-radius-8px u-font-size-13px u-display-flex u-align-items-center u-gap-8px">
                        <span>✓</span>
                        <span>Connected to Simkl as <strong>@${escapeHtml(userDisp)}</strong>${accId}</span>
                    </div>
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-14px u-font-size-13px u-line-height-1-6 u-color-text-heading">
                        <div class="u-font-weight-600 u-color-text-main u-margin-bottom-6px">Multi-Tracker Features:</div>
                        <ul class="u-margin-0 u-padding-left-20px u-color-text-muted">
                            <li>Simultaneous scrobbling on media playback (start, pause, scrobble)</li>
                            <li>Multi-type support: Movies, TV Shows, Anime</li>
                            <li>Two-way rating synchronization</li>
                            <li>Zero-latency decoupled background dispatch</li>
                        </ul>
                    </div>
                    <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-top-8px">
                        <button onclick="startSimklPinFlow()" class="btn-sm u-background-bg-surface u-border-1px-solid-var-border-color u-color-accent-color">🔄 Re-authenticate PIN</button>
                        <button onclick="disconnectSimkl()" class="btn-sm u-background-7f1d1d u-border-1px-solid-ef4444 u-color-fee2e2">Disconnect Account</button>
                    </div>
                `;
            } else if (!data.configured) {
                container.innerHTML = `
                    <div class="u-background-bg-surface u-border-1px-solid-var-border-color u-border-radius-8px u-padding-14px u-font-size-13px u-color-text-heading u-line-height-1-6">
                        <p class="u-margin-0-0-10px-0">To connect Simkl, configure your Simkl API client credentials in <code>.env</code>:</p>
                        <pre class="u-background-bg-subtle u-padding-10px u-border-radius-6px u-font-size-12px u-overflow-x-auto u-color-accent-color u-border-1px-solid-var-border-subtle">SIMKL_CLIENT_ID=your_client_id_here
SIMKL_ENABLED=true</pre>
                        <p class="u-margin-10px-0-0-0 u-font-size-12px u-color-text-muted">Create an app at <a href="https://simkl.com/settings/developer/new" target="_blank" rel="noopener" class="u-color-accent-color">simkl.com/settings/developer</a> to get your Client ID.</p>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-16px u-text-align-center">
                        <p class="u-margin-0-0-14px-0 u-font-size-13px u-color-text-heading">Click below to generate a quick activation PIN to link your Simkl account without typing passwords.</p>
                        <button onclick="startSimklPinFlow()" id="simkl-gen-pin-btn" class="btn-sm u-background-accent-color u-color-accent-text u-font-weight-600 u-padding-10px-20px u-font-size-13px">🔑 Generate Activation PIN</button>
                    </div>
                `;
            }
        }

        async function startSimklPinFlow() {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const container = document.getElementById('simkl-modal-content');
            if (container) {
                container.innerHTML = '<div class="u-color-accent-color u-text-align-center u-padding-24px u-font-size-13px">Requesting Device PIN from Simkl...</div>';
            }
            try {
                if (isDemo) {
                    setTimeout(() => {
                        renderSimklModal({
                            authenticated: true,
                            user: 'demo_viewer',
                            account_id: 987654,
                            configured: true,
                            enabled: true
                        });
                    }, 1200);
                    return;
                }
                const res = await fetch('/api/simkl/pin', { method: 'POST' });
                if (res.status === 401) {
                    openUnlockModal();
                    return;
                }
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.error || !data.user_code) {
                    throw new Error(data.detail || data.error || 'Simkl Client ID is not configured. Please add your credentials in Settings Hub or .env.');
                }
                const userCode = data.user_code;
                const deviceCode = data.device_code || '';
                const verifyUrl = data.verification_url || `https://simkl.com/pin?user_code=${encodeURIComponent(userCode)}`;

                container.innerHTML = `
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-18px u-text-align-center">
                        <div class="u-font-size-12px u-color-text-muted u-margin-bottom-8px">Enter this PIN code on Simkl:</div>
                        <div class="u-font-size-32px u-font-weight-700 u-letter-spacing-4px u-color-accent-color u-margin-10px-0 u-user-select-all u-font-family-monospace">${escapeHtml(userCode)}</div>
                        <div class="u-margin-14px-0-16px">
                            <a href="${escapeHtml(verifyUrl)}" target="_blank" rel="noopener" class="btn-sm u-background-accent-color u-color-accent-text u-font-weight-600 u-padding-8px-16px u-text-decoration-none u-display-inline-block">Open Simkl Activation Page ↗</a>
                        </div>
                        <div id="simkl-poll-indicator" class="u-font-size-12px u-color-text-muted u-display-flex u-align-items-center u-justify-content-center u-gap-6px">
                            <span class="pulse-indicator"></span> Waiting for approval on Simkl...
                        </div>
                    </div>
                `;

                if (simklPollInterval) clearInterval(simklPollInterval);
                simklPollInterval = setInterval(async () => {
                    try {
                        const pRes = await fetch('/api/simkl/poll', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ user_code: userCode, device_code: deviceCode })
                        });
                        const pollData = await pRes.json();
                        if ((pollData.result === 'OK' || pollData.status === 'success') && pollData.access_token) {
                            clearInterval(simklPollInterval);
                            simklPollInterval = null;
                            container.innerHTML = `
                                <div class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-16px u-border-radius-8px u-text-align-center">
                                    <div class="u-font-size-18px u-font-weight-700 u-margin-bottom-6px">✓ Simkl Connected!</div>
                                    <div class="u-font-size-13px">Your account is linked. Reloading dashboard...</div>
                                </div>
                            `;
                            setTimeout(() => {
                                window.location.reload();
                            }, 1500);
                        } else if (pollData.status === 'error') {
                            clearInterval(simklPollInterval);
                            simklPollInterval = null;
                            const errDetail = pollData.detail && pollData.detail !== pollData.error ? `<div class="u-font-size-12px u-color-text-muted u-margin-top-6px">${escapeHtml(pollData.detail)}</div>` : '';
                            container.innerHTML = `
                                <div class="u-color-f87171 u-text-align-center u-padding-16px">
                                    <div class="u-font-weight-600 u-margin-bottom-6px">Authorization Error</div>
                                    <div class="u-font-size-13px u-line-height-1-5">${escapeHtml(pollData.error || pollData.message || 'Polling failed')}</div>
                                    ${errDetail}
                                    <div class="u-margin-top-14px">
                                        <button onclick="fetchSimklStatus()" class="btn-sm u-background-border-color u-color-accent-text">Try Again</button>
                                    </div>
                                </div>
                            `;
                        }
                    } catch (e) {}
                }, 4000);
            } catch (err) {
                if (container) {
                    container.innerHTML = `<div class="u-color-f87171 u-text-align-center u-padding-16px">Error initiating PIN flow: ${escapeHtml(err.message)}</div>`;
                }
            }
        }

        async function disconnectTrakt(btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            if (!confirm('Are you sure you want to disconnect your Trakt account? This will remove saved Trakt tokens.')) return;
            if (isDemo) {
                alert('Trakt disconnected (Demo simulation)');
                window.location.reload();
                return;
            }
            try {
                const res = await fetch('/api/trakt/disconnect', { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                } else {
                    alert('Failed to disconnect Trakt');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function disconnectSimkl(btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            if (!confirm('Are you sure you want to disconnect your Simkl account?')) return;
            if (isDemo) {
                alert('Simkl disconnected (Demo simulation)');
                fetchSimklStatus();
                return;
            }
            try {
                const res = await fetch('/api/simkl/disconnect', { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                } else {
                    alert('Failed to disconnect Simkl');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        // -------------------------------------------------------------
        // AniList Integration
        // -------------------------------------------------------------
        function openAnilistModal() {
            const modal = document.getElementById('anilist-modal');
            if (modal) modal.style.display = 'flex';
            fetchAnilistStatus();
        }

        function closeAnilistModal() {
            const modal = document.getElementById('anilist-modal');
            if (modal) modal.style.display = 'none';
        }

        async function fetchAnilistStatus() {
            const container = document.getElementById('anilist-modal-content');
            if (!container) return;
            try {
                const url = isDemo ? '/api/anilist/status?demo=true' : '/api/anilist/status';
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to load AniList status');
                const data = await res.json();
                renderAnilistModal(data);
            } catch (err) {
                container.innerHTML = `<div class="u-color-f87171 u-text-align-center u-padding-16px">Failed to load AniList status: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderAnilistModal(data) {
            const container = document.getElementById('anilist-modal-content');
            if (!container) return;

            if (data.authenticated) {
                const userDisp = data.user || 'Linked Account';
                const avatarImg = data.avatar ? `<img src="${escapeHtml(data.avatar)}" class="u-width-36px u-height-36px u-border-radius-50 u-border-1px-solid-059669" />` : '';
                container.innerHTML = `
                    <div class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-12px-14px u-border-radius-8px u-font-size-13px u-display-flex u-align-items-center u-justify-content-space-between u-gap-10px">
                        <div class="u-display-flex u-align-items-center u-gap-10px">
                            ${avatarImg}
                            <div>
                                <div class="u-font-weight-700">Connected to AniList</div>
                                <div class="u-font-size-12px u-opacity-0-9">@${escapeHtml(userDisp)} (ID: #${escapeHtml(String(data.id || ''))})</div>
                            </div>
                        </div>
                        <span class="u-background-059669 u-color-accent-text u-font-size-11px u-padding-2px-8px u-border-radius-4px u-font-weight-600">Active</span>
                    </div>
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-14px u-font-size-13px u-line-height-1-6 u-color-text-heading">
                        <div class="u-font-weight-600 u-color-text-main u-margin-bottom-6px">AniList Scrobbler Features:</div>
                        <ul class="u-margin-0 u-padding-left-20px u-color-text-muted">
                            <li>Live GraphQL mutation scrobbling (<code>SaveMediaListEntry</code>)</li>
                            <li>Heuristic detection &amp; title normalization for anime</li>
                            <li>Two-way score and rating synchronization</li>
                            <li>Zero impact on media playback latency</li>
                        </ul>
                    </div>
                    <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-top-8px">
                        <a href="${isDemo ? 'javascript:void(0)' : '/auth/anilist'}" ${isDemo ? 'onclick="alert(\'Token portal is simulated in demo mode.\')"' : ''} class="btn-sm u-background-bg-surface u-border-1px-solid-var-border-color u-color-accent-color u-text-decoration-none u-padding-6px-12px u-font-size-12px">Update Token Portal ↗</a>
                        <button onclick="disconnectAnilist(this)" class="btn-sm u-background-7f1d1d u-border-1px-solid-ef4444 u-color-fee2e2">Disconnect AniList</button>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-16px">
                        <p class="u-margin-0-0-12px-0 u-font-size-13px u-color-text-heading">Enter your AniList Personal Access Token to link your account:</p>
                        <form onsubmit="submitAnilistToken(event)" class="u-display-flex u-flex-direction-column u-gap-10px">
                            <input type="password" id="anilist-modal-token-input" placeholder="Paste your Bearer token here..." class="u-background-bg-surface u-border-1px-solid-var-border-color u-border-radius-6px u-padding-10px-12px u-color-text-main u-font-size-13px u-font-family-monospace" required autocomplete="off" />
                            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-top-4px">
                                <a href="https://anilist.co/api/v2/oauth/authorize?client_id=27495&response_type=token" target="_blank" rel="noopener" class="u-color-accent-color u-font-size-12px u-text-decoration-none">Get Token from AniList ↗</a>
                                <button type="submit" id="anilist-modal-save-btn" class="btn-sm u-background-02a9ff u-color-accent-text u-font-weight-600 u-padding-8px-16px">⚡ Connect AniList</button>
                            </div>
                        </form>
                        <div id="anilist-modal-status" class="u-margin-top-10px u-font-size-12px u-min-height-16px"></div>
                    </div>
                `;
            }
        }

        async function submitAnilistToken(e) {
            e.preventDefault();
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const input = document.getElementById('anilist-modal-token-input');
            const token = input ? input.value.trim() : '';
            const statusEl = document.getElementById('anilist-modal-status');
            const btn = document.getElementById('anilist-modal-save-btn');
            if (!token) return;

            if (isDemo) {
                if (btn) btn.disabled = true;
                if (statusEl) statusEl.innerHTML = '<span class="u-color-accent-color">Connecting to AniList GraphQL...</span>';
                setTimeout(() => {
                    renderAnilistModal({
                        authenticated: true,
                        user: 'demo_otaku',
                        id: 842105,
                        avatar: 'https://s4.anilist.co/file/anilistcdn/user/avatar/large/default.png',
                    });
                }, 1000);
                return;
            }

            if (btn) { btn.disabled = true; btn.textContent = 'Verifying...'; }
            if (statusEl) statusEl.innerHTML = '<span class="u-color-accent-color">Verifying token with AniList GraphQL...</span>';

            try {
                const res = await fetch('/api/anilist/token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: token })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    if (statusEl) statusEl.innerHTML = `<span class="u-color-10b981 u-font-weight-600">✓ Connected as @${escapeHtml(data.user || 'user')}!</span>`;
                    setTimeout(() => { window.location.reload(); }, 1200);
                } else {
                    if (statusEl) statusEl.innerHTML = `<span class="u-color-ef4444">Error: ${escapeHtml(data.error || data.detail || 'Invalid token')}</span>`;
                    if (btn) { btn.disabled = false; btn.textContent = '⚡ Connect AniList'; }
                }
            } catch (err) {
                if (statusEl) statusEl.innerHTML = `<span class="u-color-ef4444">Network error: ${escapeHtml(err.message)}</span>`;
                if (btn) { btn.disabled = false; btn.textContent = '⚡ Connect AniList'; }
            }
        }

        async function disconnectAnilist(btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            if (!confirm('Are you sure you want to disconnect your AniList account?')) return;
            if (isDemo) {
                alert('AniList disconnected (Demo simulation)');
                fetchAnilistStatus();
                return;
            }
            try {
                const res = await fetch('/api/anilist/disconnect', { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                } else {
                    alert('Failed to disconnect AniList');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        // -------------------------------------------------------------
        // MyAnimeList (MAL) Integration
        // -------------------------------------------------------------
        function openMalModal() {
            const modal = document.getElementById('mal-modal');
            if (modal) modal.style.display = 'flex';
            fetchMalStatus();
        }

        function closeMalModal() {
            const modal = document.getElementById('mal-modal');
            if (modal) modal.style.display = 'none';
        }

        async function fetchMalStatus() {
            const container = document.getElementById('mal-modal-content');
            if (!container) return;
            try {
                const url = isDemo ? '/api/mal/status?demo=true' : '/api/mal/status';
                const res = await fetch(url);
                if (!res.ok) throw new Error('Failed to load MAL status');
                const data = await res.json();
                renderMalModal(data);
            } catch (err) {
                container.innerHTML = `<div class="u-color-f87171 u-text-align-center u-padding-16px">Failed to load MAL status: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderMalModal(data) {
            const container = document.getElementById('mal-modal-content');
            if (!container) return;

            if (data.authenticated) {
                const userDisp = data.user || 'Linked Account';
                const avatarImg = data.avatar ? `<img src="${escapeHtml(data.avatar)}" class="u-width-36px u-height-36px u-border-radius-50 u-border-1px-solid-2e51a2" />` : '';
                container.innerHTML = `
                    <div class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-12px-14px u-border-radius-8px u-font-size-13px u-display-flex u-align-items-center u-justify-content-space-between u-gap-10px">
                        <div class="u-display-flex u-align-items-center u-gap-10px">
                            ${avatarImg}
                            <div>
                                <div class="u-font-weight-700">Connected to MyAnimeList</div>
                                <div class="u-font-size-12px u-opacity-0-9">@${escapeHtml(userDisp)} (ID: #${escapeHtml(String(data.id || ''))})</div>
                            </div>
                        </div>
                        <span class="u-background-059669 u-color-accent-text u-font-size-11px u-padding-2px-8px u-border-radius-4px u-font-weight-600">Active</span>
                    </div>
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-14px u-font-size-13px u-line-height-1-6 u-color-text-heading">
                        <div class="u-font-weight-600 u-color-text-main u-margin-bottom-6px">MyAnimeList Scrobbler Features:</div>
                        <ul class="u-margin-0 u-padding-left-20px u-color-text-muted">
                            <li>Official MAL API v2 user status synchronization</li>
                            <li>Episode progress tracking and rating synchronization</li>
                            <li>Automatic translation from TVDB / AniList IDs to MAL IDs</li>
                            <li>Zero-latency decoupled background dispatch</li>
                        </ul>
                    </div>
                    <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-top-8px">
                        <a href="${isDemo ? 'javascript:void(0)' : '/auth/mal'}" ${isDemo ? 'onclick="alert(\'Token portal is simulated in demo mode.\')"' : ''} class="btn-sm u-background-bg-surface u-border-1px-solid-var-border-color u-color-818cf8 u-text-decoration-none u-padding-6px-12px u-font-size-12px">Update Token Portal ↗</a>
                        <button onclick="disconnectMal(this)" class="btn-sm u-background-7f1d1d u-border-1px-solid-ef4444 u-color-fee2e2">Disconnect MAL</button>
                    </div>
                `;
            } else {
                container.innerHTML = `
                    <div class="u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-16px">
                        <p class="u-margin-0-0-12px-0 u-font-size-13px u-color-text-heading">Enter your MyAnimeList Bearer Access Token to link your account:</p>
                        <form onsubmit="submitMalToken(event)" class="u-display-flex u-flex-direction-column u-gap-10px">
                            <input type="password" id="mal-modal-token-input" placeholder="Paste your MAL access token here..." class="u-background-bg-surface u-border-1px-solid-var-border-color u-border-radius-6px u-padding-10px-12px u-color-text-main u-font-size-13px u-font-family-monospace" required autocomplete="off" />
                            <div class="u-display-flex u-justify-content-space-between u-align-items-center u-margin-top-4px">
                                <a href="https://myanimelist.net/apiconfig" target="_blank" rel="noopener" class="u-color-818cf8 u-font-size-12px u-text-decoration-none">MAL API Config ↗</a>
                                <button type="submit" id="mal-modal-save-btn" class="btn-sm u-background-2e51a2 u-color-accent-text u-font-weight-600 u-padding-8px-16px">🎌 Connect MAL</button>
                            </div>
                        </form>
                        <div id="mal-modal-status" class="u-margin-top-10px u-font-size-12px u-min-height-16px"></div>
                    </div>
                `;
            }
        }

        async function submitMalToken(e) {
            e.preventDefault();
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            const input = document.getElementById('mal-modal-token-input');
            const token = input ? input.value.trim() : '';
            const statusEl = document.getElementById('mal-modal-status');
            const btn = document.getElementById('mal-modal-save-btn');
            if (!token) return;

            if (isDemo) {
                if (btn) btn.disabled = true;
                if (statusEl) statusEl.innerHTML = '<span class="u-color-818cf8">Connecting to MyAnimeList API...</span>';
                setTimeout(() => {
                    renderMalModal({
                        authenticated: true,
                        user: 'demo_otaku',
                        id: 1492084,
                        avatar: 'https://cdn.myanimelist.net/images/userimages/default.jpg',
                    });
                }, 1000);
                return;
            }

            if (btn) { btn.disabled = true; btn.textContent = 'Verifying...'; }
            if (statusEl) statusEl.innerHTML = '<span class="u-color-818cf8">Verifying token with MyAnimeList API v2...</span>';

            try {
                const res = await fetch('/api/mal/token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: token })
                });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    if (statusEl) statusEl.innerHTML = `<span class="u-color-10b981 u-font-weight-600">✓ Connected as @${escapeHtml(data.user || 'user')}!</span>`;
                    setTimeout(() => { window.location.reload(); }, 1200);
                } else {
                    if (statusEl) statusEl.innerHTML = `<span class="u-color-ef4444">Error: ${escapeHtml(data.error || data.detail || 'Invalid token')}</span>`;
                    if (btn) { btn.disabled = false; btn.textContent = '🎌 Connect MAL'; }
                }
            } catch (err) {
                if (statusEl) statusEl.innerHTML = `<span class="u-color-ef4444">Network error: ${escapeHtml(err.message)}</span>`;
                if (btn) { btn.disabled = false; btn.textContent = '🎌 Connect MAL'; }
            }
        }

        async function disconnectMal(btn) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }
            if (!confirm('Are you sure you want to disconnect your MyAnimeList account?')) return;
            if (isDemo) {
                alert('MyAnimeList disconnected (Demo simulation)');
                fetchMalStatus();
                return;
            }
            try {
                const res = await fetch('/api/mal/disconnect', { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                } else {
                    alert('Failed to disconnect MyAnimeList');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        // -------------------------------------------------------------
        // Cross-Tracker Synchronization (Trakt <-> Simkl)
        // -------------------------------------------------------------
        let crossSyncDiffItems = [];
        let crossSyncSelectedIds = new Set();
        let crossSyncActiveFilter = 'all';
        let crossSyncProgressTimer = null;

        function openCrossSyncModal(force = false) {
            const modal = document.getElementById('cross-sync-modal');
            if (!modal) return;
            modal.style.display = 'flex';
            fetchCrossSyncDiscrepancies(force);
        }

        function closeCrossSyncModal() {
            const modal = document.getElementById('cross-sync-modal');
            if (modal) modal.style.display = 'none';
            if (crossSyncProgressTimer) {
                clearInterval(crossSyncProgressTimer);
                crossSyncProgressTimer = null;
            }
        }

        async function fetchCrossSyncDiscrepancies(force = false) {
            const tbody = document.getElementById('cross-sync-tbody');
            const badge = document.getElementById('cross-sync-count-badge');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="6" class="u-text-align-center u-padding-24px u-color-accent-color">Scanning Trakt & Simkl libraries...</td></tr>';
            }
            if (badge) badge.innerText = 'Scanning...';

            try {
                const url = (isDemo ? '/api/cross-sync/diff?demo=true' : '/api/cross-sync/diff') + (force ? '?force=true' : '');
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    crossSyncDiffItems = data.diff || [];
                    crossSyncSelectedIds.clear();
                    updateCrossSyncCounts();
                    renderCrossSyncTable();
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

        function updateCrossSyncCounts() {
            const allCount = crossSyncDiffItems.length;
            const t2sCount = crossSyncDiffItems.filter(i => i.direction === 'trakt_to_simkl').length;
            const s2tCount = crossSyncDiffItems.filter(i => i.direction === 'simkl_to_trakt').length;
            const ratingCount = crossSyncDiffItems.filter(i => i.sync_type === 'rating').length;

            const cAll = document.getElementById('cross-count-all');
            const cT2s = document.getElementById('cross-count-t2s');
            const cS2t = document.getElementById('cross-count-s2t');
            const cRatings = document.getElementById('cross-count-ratings');
            const badge = document.getElementById('cross-sync-count-badge');

            if (cAll) cAll.innerText = allCount;
            if (cT2s) cT2s.innerText = t2sCount;
            if (cS2t) cS2t.innerText = s2tCount;
            if (cRatings) cRatings.innerText = ratingCount;
            if (badge) badge.innerText = `${allCount} Discrepanc${allCount === 1 ? 'y' : 'ies'}`;
        }

        function filterCrossSync(filterType, tabBtn) {
            crossSyncActiveFilter = filterType;
            document.querySelectorAll('.cross-sync-tab').forEach(b => {
                b.style.background = 'var(--bg-surface)';
                b.style.color = 'var(--text-heading)';
                b.style.border = '1px solid var(--border-color)';
            });
            if (tabBtn) {
                tabBtn.style.background = 'var(--accent-color)';
                tabBtn.style.color = 'var(--accent-text)';
                tabBtn.style.border = 'none';
            }
            renderCrossSyncTable();
        }

        function renderCrossSyncTable() {
            const tbody = document.getElementById('cross-sync-tbody');
            if (!tbody) return;

            let items = crossSyncDiffItems;
            if (crossSyncActiveFilter === 'trakt_to_simkl') {
                items = items.filter(i => i.direction === 'trakt_to_simkl');
            } else if (crossSyncActiveFilter === 'simkl_to_trakt') {
                items = items.filter(i => i.direction === 'simkl_to_trakt');
            } else if (crossSyncActiveFilter === 'rating') {
                items = items.filter(i => i.sync_type === 'rating');
            }

            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="u-text-align-center u-padding-28px u-color-10b981 u-font-weight-500">✓ In sync! No cross-tracker discrepancies in this category.</td></tr>';
                updateCrossSyncSelectedCount();
                return;
            }

            let html = '';
            for (const item of items) {
                const isChecked = crossSyncSelectedIds.has(item.id);
                const titleStr = item.media_type === 'episode'
                    ? `${escapeHtml(item.show_title || '')} S${String(item.season || 1).padStart(2, '0')}E${String(item.episode || 1).padStart(2, '0')} &bull; ${escapeHtml(item.title || '')}`
                    : `${escapeHtml(item.title || '')} (${item.year || 'N/A'})`;

                const dirBadge = item.direction === 'trakt_to_simkl'
                    ? '<span class="u-background-1e1b4b u-border-1px-solid-4338ca u-color-a5b4fc u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">Trakt &rarr; Simkl</span>'
                    : '<span class="u-background-064e3b u-border-1px-solid-059669 u-color-a7f3d0 u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">Simkl &rarr; Trakt</span>';

                const typeBadge = item.sync_type === 'rating'
                    ? '<span class="u-background-451a03 u-border-1px-solid-b45309 u-color-fde68a u-padding-2px-6px u-border-radius-4px u-font-size-10px u-font-weight-600">Rating</span>'
                    : '<span class="u-background-bg-page u-border-1px-solid-var-border-color u-color-text-muted u-padding-2px-6px u-border-radius-4px u-font-size-10px">' + (item.media_type || 'Media') + '</span>';

                html += `
                    <tr class="u-border-bottom-1px-solid-1e293b">
                        <td class="u-text-align-center u-padding-10px-14px">
                            <input type="checkbox" data-id="${escapeHtml(item.id)}" ${isChecked ? 'checked' : ''} onchange="toggleCrossSyncItem(this, '${escapeHtml(item.id)}')" />
                        </td>
                        <td class="u-padding-10px-14px u-white-space-nowrap">${dirBadge}</td>
                        <td class="u-padding-10px-14px u-color-text-main u-font-weight-500">${titleStr}</td>
                        <td class="u-padding-10px-14px">${typeBadge}</td>
                        <td class="u-padding-10px-14px u-color-10b981 u-font-weight-600">${escapeHtml(item.source_status || 'watched')}</td>
                        <td class="u-padding-10px-14px u-color-text-muted">${escapeHtml(item.target_status || 'unwatched')}</td>
                    </tr>
                `;
            }
            tbody.innerHTML = html;
            updateCrossSyncSelectedCount();
        }

        function toggleCrossSyncItem(cb, id) {
            if (cb.checked) {
                crossSyncSelectedIds.add(id);
            } else {
                crossSyncSelectedIds.delete(id);
            }
            updateCrossSyncSelectedCount();
        }

        function toggleSelectAllCrossSync(masterCb) {
            let items = crossSyncDiffItems;
            if (crossSyncActiveFilter === 'trakt_to_simkl') {
                items = items.filter(i => i.direction === 'trakt_to_simkl');
            } else if (crossSyncActiveFilter === 'simkl_to_trakt') {
                items = items.filter(i => i.direction === 'simkl_to_trakt');
            } else if (crossSyncActiveFilter === 'rating') {
                items = items.filter(i => i.sync_type === 'rating');
            }

            if (masterCb.checked) {
                items.forEach(i => crossSyncSelectedIds.add(i.id));
            } else {
                items.forEach(i => crossSyncSelectedIds.delete(i.id));
            }
            renderCrossSyncTable();
        }

        function updateCrossSyncSelectedCount() {
            const count = crossSyncSelectedIds.size;
            const countSpan = document.getElementById('cross-sync-selected-count');
            const syncBtn = document.getElementById('cross-sync-selected-btn');
            if (countSpan) countSpan.innerText = count;
            if (syncBtn) {
                syncBtn.disabled = count === 0;
                syncBtn.innerText = `⚡ Sync Selected (${count})`;
            }
        }

        async function executeSelectedCrossSync() {
            if (crossSyncSelectedIds.size === 0) return;
            const ids = Array.from(crossSyncSelectedIds);
            await triggerCrossSyncExecution({ item_ids: ids });
        }

        async function executeAllCrossSync() {
            await triggerCrossSyncExecution({ direction: 'both' });
        }

        async function triggerCrossSyncExecution(payload) {
            if (!isAdmin && !isDemo) {
                openUnlockModal();
                return;
            }

            const pBox = document.getElementById('cross-sync-progress-box');
            const pMsg = document.getElementById('cross-sync-progress-msg');
            const pBar = document.getElementById('cross-sync-progress-bar');
            const pStats = document.getElementById('cross-sync-progress-stats');

            if (pBox) pBox.style.display = 'block';
            if (pMsg) pMsg.innerText = 'Starting cross-tracker synchronization...';
            if (pBar) pBar.style.width = '10%';
            if (pStats) pStats.innerText = 'Initializing';

            try {
                const url = isDemo ? '/api/cross-sync/execute?demo=true' : '/api/cross-sync/execute';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();

                if (isDemo || data.status === 'completed') {
                    if (pBar) pBar.style.width = '100%';
                    if (pStats) pStats.innerText = '100%';
                    if (pMsg) pMsg.innerText = data.message || 'Sync complete!';
                    setTimeout(() => {
                        fetchCrossSyncDiscrepancies(true);
                    }, 1200);
                } else {
                    pollCrossSyncProgress();
                }
            } catch (e) {
                if (pMsg) pMsg.innerText = `Sync failed: ${escapeHtml(e.message)}`;
            }
        }

        function pollCrossSyncProgress() {
            if (crossSyncProgressTimer) clearInterval(crossSyncProgressTimer);
            crossSyncProgressTimer = setInterval(async () => {
                try {
                    const res = await fetch('/api/cross-sync/progress');
                    const prog = await res.json();
                    const pBar = document.getElementById('cross-sync-progress-bar');
                    const pMsg = document.getElementById('cross-sync-progress-msg');
                    const pStats = document.getElementById('cross-sync-progress-stats');

                    if (prog.total > 0) {
                        const pct = Math.round((prog.current / prog.total) * 100);
                        if (pBar) pBar.style.width = `${pct}%`;
                        if (pStats) pStats.innerText = `${pct}% (${prog.current}/${prog.total})`;
                    }
                    if (pMsg) pMsg.innerText = prog.message || 'Synchronizing...';

                    if (!prog.in_progress) {
                        clearInterval(crossSyncProgressTimer);
                        crossSyncProgressTimer = null;
                        setTimeout(() => {
                            fetchCrossSyncDiscrepancies(true);
                        }, 1200);
                    }
                } catch (e) {}
            }, 1000);
        }

        // Dynamically update header subtitle reflecting active media servers -> connected cloud trackers
        async function updateDynamicSubtitle() {
            try {
                const subEl = document.getElementById('header-title-sub');
                if (!subEl) return;
                const res = await fetch(isDemo ? '/api/trackers/status?demo=true' : '/api/trackers/status');
                if (!res.ok) return;
                const data = await res.json();
                const trackerMap = {
                    trakt: 'Trakt',
                    simkl: 'Simkl',
                    anilist: 'AniList',
                    mal: 'MAL',
                    myanimelist: 'MAL',
                    tmdb: 'TMDb',
                    kitsu: 'Kitsu',
                    letterboxd: 'Letterboxd',
                    serializd: 'Serializd',
                    mdblist: 'MDBList'
                };
                const trackers = (data.active_trackers || []).map(t => trackerMap[t.toLowerCase()] || (t.charAt(0).toUpperCase() + t.slice(1)));

                let servers = ['Plex'];
                try {
                    const sRes = await fetch(isDemo ? '/api/settings?demo=true' : '/api/settings');
                    if (sRes.ok) {
                        const sData = await sRes.json();
                        const srvObj = sData.servers || {};
                        const activeSrvs = Object.keys(srvObj).filter(k => srvObj[k]);
                        if (activeSrvs.length > 0) {
                            servers = activeSrvs.map(s => s.charAt(0).toUpperCase() + s.slice(1));
                        }
                    }
                } catch (_) {}

                if (trackers.length > 0) {
                    subEl.innerHTML = `${servers.join(' • ')} &rarr; ${trackers.join(' • ')}`;
                }
            } catch (_) {}
        }

        async function triggerBackgroundCloudSync(btn) {
            if (btn) { btn.disabled = true; btn.textContent = 'Syncing...'; }
            try {
                const url = isDemo ? '/api/sync/background/run?demo=true' : '/api/sync/background/run';
                const res = await fetch(url, { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    alert('Automated cloud synchronization completed successfully! ' + (data.items_reconciled || 0) + ' items reconciled.');
                    window.location.reload();
                } else {
                    alert('Cloud sync failed: ' + (data.detail || data.message || 'Unknown error'));
                    if (btn) { btn.disabled = false; btn.textContent = '⚡ Run Cloud Sync Now'; }
                }
            } catch (e) {
                alert('Network error: ' + e.message);
                if (btn) { btn.disabled = false; btn.textContent = '⚡ Run Cloud Sync Now'; }
            }
        }

        async function disconnectPartnerTracker(tracker, user) {
            if (!confirm(`Disconnect ${tracker.toUpperCase()} for partner @${user}?`)) return;
            try {
                const res = await fetch(`/api/${tracker}/disconnect?user=${encodeURIComponent(user)}`, { method: 'POST' });
                if (res.ok) {
                    window.location.reload();
                } else {
                    const data = await res.json();
                    alert(`Failed to disconnect: ${data.detail || data.message || 'Error'}`);
                }
            } catch (e) {
                alert(`Network error: ${e.message}`);
            }
        }

        async function openPartnerTrackerAuth(tracker, user) {
            if (tracker === 'trakt') {
                window.location.href = `/auth?user=${encodeURIComponent(user)}`;
                return;
            }
            if (tracker === 'simkl') {
                try {
                    const res = await fetch(`/api/simkl/pin?user=${encodeURIComponent(user)}`, { method: 'POST' });
                    const data = await res.json();
                    if (res.ok && data.user_code) {
                        const url = data.verification_url || 'https://simkl.com/pin';
                        window.open(url, '_blank');
                        const confirmPoll = confirm(`Simkl PIN: ${data.user_code}\n\nAuthorize Omniscrobble on Simkl at ${url} with code ${data.user_code}, then click OK to verify.`);
                        if (confirmPoll) {
                            const pollRes = await fetch('/api/simkl/poll', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ user_code: data.user_code, device_code: data.device_code, user: user })
                            });
                            const pollData = await pollRes.json();
                            if (pollRes.ok && (pollData.result === 'OK' || pollData.status === 'success' || pollData.access_token)) {
                                alert(`✓ Simkl successfully linked for partner @${user}!`);
                                window.location.reload();
                            } else {
                                alert(`Simkl authorization not completed: ${pollData.message || 'Pending authorization'}`);
                            }
                        }
                    } else {
                        alert(`Failed to get Simkl PIN: ${data.detail || data.error || 'Error'}`);
                    }
                } catch (e) {
                    alert(`Network error: ${e.message}`);
                }
                return;
            }
            if (tracker === 'anilist') {
                const token = prompt(`Enter AniList Personal Access Token for partner @${user}:\n(Generated from AniList Settings > Developer)`);
                if (!token || !token.trim()) return;
                try {
                    const res = await fetch('/api/anilist/token', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: token.trim(), user: user })
                    });
                    const data = await res.json();
                    if (res.ok && data.status === 'success') {
                        alert(`✓ AniList connected for @${user} (@${data.user || 'user'})!`);
                        window.location.reload();
                    } else {
                        alert(`Failed to link AniList: ${data.detail || data.error || 'Invalid token'}`);
                    }
                } catch (e) {
                    alert(`Network error: ${e.message}`);
                }
                return;
            }
            if (tracker === 'mal') {
                const token = prompt(`Enter MyAnimeList Access Token for partner @${user}:`);
                if (!token || !token.trim()) return;
                try {
                    const res = await fetch('/api/mal/token', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: token.trim(), user: user })
                    });
                    const data = await res.json();
                    if (res.ok && data.status === 'success') {
                        alert(`✓ MyAnimeList connected for @${user} (@${data.user || 'user'})!`);
                        window.location.reload();
                    } else {
                        alert(`Failed to link MyAnimeList: ${data.detail || data.error || 'Invalid token'}`);
                    }
                } catch (e) {
                    alert(`Network error: ${e.message}`);
                }
                return;
            }
        }

        // ==========================================
        // Webhook Inspector & Payload Debugger Logic
        // ==========================================

// ---- webhook_inspector.js ----
        let currentDebugWebhooks = [];

        function openWebhookDebuggerModal() {
            if (!isAdmin && !isDemo) { openUnlockModal(); return; }
            openDiagnosticsDrawer('inspector');
        }

        function closeWebhookDebuggerModal() {
            closeDiagnosticsDrawer();
        }

        async function loadDebugWebhooks() {
            const listEl = document.getElementById('webhook-debugger-list');
            const countBadge = document.getElementById('webhook-debugger-count');
            if (!listEl) return;
            listEl.innerHTML = '<div class="u-color-text-muted u-font-size-12px u-font-style-italic u-padding-12px-0">Loading webhook history...</div>';
            try {
                const url = isDemo ? '/api/debug/webhooks?demo=true' : '/api/debug/webhooks';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    currentDebugWebhooks = data.webhooks || [];
                    if (countBadge) countBadge.innerText = `${currentDebugWebhooks.length} events`;
                    renderDebugWebhooksList(currentDebugWebhooks);
                } else {
                    listEl.innerHTML = '<div class="u-color-f87171 u-font-size-12px u-padding-8px-0">Failed to load webhook history.</div>';
                }
            } catch (e) {
                listEl.innerHTML = `<div class="u-color-f87171 u-font-size-12px u-padding-8px-0">Error: ${escapeHtml(e.message)}</div>`;
            }
        }

        async function clearDebugWebhooks() {
            if (!isAdmin && !isDemo) { openUnlockModal(); return; }
            if (!confirm('Are you sure you want to clear the captured webhook history?')) return;
            try {
                const url = isDemo ? '/api/debug/webhooks?demo=true' : '/api/debug/webhooks';
                const res = await fetch(url, { method: 'DELETE' });
                if (res.ok) {
                    loadDebugWebhooks();
                }
            } catch (e) {
                alert('Error clearing webhooks: ' + e.message);
            }
        }

        function renderDebugWebhooksList(webhooks) {
            const listEl = document.getElementById('webhook-debugger-list');
            if (!listEl) return;
            if (!webhooks.length) {
                listEl.innerHTML = '<div class="u-color-text-muted u-font-size-12px u-font-style-italic u-padding-12px-0">No incoming webhooks recorded yet. Incoming media server and standalone scrobbles will appear here automatically.</div>';
                return;
            }

            listEl.innerHTML = webhooks.map((wh, idx) => {
                const wid = encodeURIComponentForInlineJs(wh.id || `wh_${idx}`);
                const sourceName = String(wh.source || 'webhook').toLowerCase();
                const srcKey = ['plex', 'jellyfin', 'emby', 'standalone'].includes(sourceName) ? sourceName : 'default';
                const src = escapeHtml(sourceName.toUpperCase());
                const event = escapeHtml(wh.event || 'media.event');
                const title = escapeHtml(wh.media_title || 'Media Event');
                const ts = escapeHtml(wh.timestamp || '');
                const status = escapeHtml(wh.status || 'received');
                const reason = escapeHtml(wh.reason || '');
                const payloadJson = escapeHtml(JSON.stringify(wh.payload || {}, null, 2));

                let statusBadge = '<span class="u-background-bg-surface u-border-1px-solid-var-border-color u-color-text-muted u-padding-1px-6px u-border-radius-4px u-font-size-10px">Received</span>';
                if (status === 'processed') {
                    statusBadge = '<span class="u-background-065f46 u-color-34d399 u-padding-1px-6px u-border-radius-4px u-font-size-10px u-font-weight-600">Processed</span>';
                } else if (status === 'ignored') {
                    statusBadge = '<span class="u-background-451a03 u-color-fbbf24 u-padding-1px-6px u-border-radius-4px u-font-size-10px">Ignored</span>';
                } else if (status === 'error') {
                    statusBadge = '<span class="u-background-7f1d1d u-color-fca5a5 u-padding-1px-6px u-border-radius-4px u-font-size-10px">Error</span>';
                }

                return `
                <div class="debug-webhook-card u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-10px-12px u-margin-bottom-8px">
                    <div class="u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px u-margin-bottom-6px">
                        <div class="u-display-flex u-align-items-center u-gap-8px u-flex-wrap-wrap">
                            <span class="webhook-source-badge webhook-source-${srcKey}">${src}</span>
                            <strong class="u-color-text-main u-font-size-13px">${title}</strong>
                            ${statusBadge}
                            <span class="u-font-size-11px u-color-text-muted">${event}</span>
                        </div>
                        <div class="u-display-flex u-gap-6px u-align-items-center">
                            <span class="u-font-size-10px u-color-text-muted u-margin-right-4px">${ts}</span>
                            <button onclick="togglePayloadViewer(decodeURIComponent('${wid}'))" class="btn-sm u-padding-2px-8px u-font-size-11px u-background-bg-surface u-border-1px-solid-var-border-color u-color-text-heading u-cursor-pointer">
                                View Payload
                            </button>
                            <button onclick="replayPayloadFromDebugger(decodeURIComponent('${wid}'))" class="btn-sm u-padding-2px-8px u-font-size-11px u-background-accent-color u-color-accent-text u-border-none u-cursor-pointer u-font-weight-600">
                                🔁 Replay
                            </button>
                        </div>
                    </div>
                    ${reason ? `<div class="u-font-size-11px u-color-text-muted u-margin-bottom-6px">${reason}</div>` : ''}
                    <div id="payload-viewer-${wid}" class="u-display-none u-margin-top-8px u-background-bg-subtle u-border-1px-solid-var-border-subtle u-border-radius-6px u-padding-8px-10px">
                        <pre class="u-margin-0 u-font-size-11px u-color-accent-color u-font-family-monospace u-white-space-pre-wrap u-word-break-break-all u-max-height-220px u-overflow-y-auto">${payloadJson}</pre>
                    </div>
                </div>`;
            }).join('');
        }

        function togglePayloadViewer(wid) {
            const el = document.getElementById(`payload-viewer-${wid}`);
            if (el) {
                el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
            }
        }

        function replayPayloadFromDebugger(wid) {
            const wh = currentDebugWebhooks.find(w => w.id === wid);
            if (!wh) return;
            closeWebhookDebuggerModal();
            openTestWebhookModal();
            const payload = wh.payload || {};
            const metadata = payload.Metadata || payload.Item || payload;
            const titleInput = document.getElementById('test-media-title');
            if (titleInput && (metadata.title || payload.title)) {
                titleInput.value = metadata.title || payload.title;
            }
            const typeInput = document.getElementById('test-media-type');
            if (typeInput && (metadata.type || payload.media_type)) {
                typeInput.value = (metadata.type || payload.media_type).toLowerCase() === 'movie' ? 'movie' : 'episode';
            }
        }

        // ==========================================
        // Personal Analytics & OmniWrapped Logic
        // ==========================================

// ---- wrapped.js ----
        let currentOmniWrappedData = null;

        function openOmniWrappedModal() {
            document.getElementById('omniwrapped-modal').style.display = 'flex';
            loadOmniWrapped();
        }

        function closeOmniWrappedModal() {
            document.getElementById('omniwrapped-modal').style.display = 'none';
        }

        async function loadOmniWrapped(year = 2026) {
            try {
                const url = isDemo ? `/api/analytics/wrapped?year=${year}&demo=true` : `/api/analytics/wrapped?year=${year}`;
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    currentOmniWrappedData = data;
                    renderOmniWrappedModal(data);
                }
            } catch (e) {
                console.error('Failed to load OmniWrapped:', e);
            }
        }

        function renderOmniWrappedModal(data) {
            if (!data) return;
            const headlineEl = document.getElementById('omniwrapped-headline');
            if (headlineEl) headlineEl.innerText = data.headline || `Your ${data.year || 2026} OmniWrapped`;

            const archetypeEl = document.getElementById('omniwrapped-archetype');
            if (archetypeEl) archetypeEl.innerText = data.archetype || 'The Media Connoisseur';

            const descEl = document.getElementById('omniwrapped-description');
            if (descEl) descEl.innerText = data.description || '';

            const wtEl = document.getElementById('omniwrapped-watchtime');
            if (wtEl) wtEl.innerText = data.total_watch_time || `${data.total_watch_hours || 0}h`;

            const titlesEl = document.getElementById('omniwrapped-titles');
            if (titlesEl) titlesEl.innerText = data.total_scrobbles || 0;
            const titlesSubEl = document.getElementById('omniwrapped-titles-sub');
            if (titlesSubEl) titlesSubEl.innerText = `${data.unique_titles || 0} unique titles`;

            const topShowEl = document.getElementById('omniwrapped-top-show');
            const topShowSubEl = document.getElementById('omniwrapped-top-show-sub');
            if (topShowEl && data.top_binge) {
                topShowEl.innerText = data.top_binge.show || 'Series';
                if (topShowSubEl) topShowSubEl.innerText = `${data.top_binge.episodes || 0} episodes logged`;
            }

            const cwPctEl = document.getElementById('omniwrapped-cowatch-pct');
            const cwPartnerEl = document.getElementById('omniwrapped-cowatch-partner');
            if (cwPctEl && data.cowatch_breakdown) {
                cwPctEl.innerText = `${data.cowatch_breakdown.cowatch_percentage || 0}% Shared`;
                if (cwPartnerEl) cwPartnerEl.innerText = `with @${data.cowatch_breakdown.partner_user || 'Partner'}`;
            }
        }

        function downloadOmniWrappedJson() {
            if (!currentOmniWrappedData) return;
            const blob = new Blob([JSON.stringify(currentOmniWrappedData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `omniwrapped_${currentOmniWrappedData.year || 2026}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        // Reset scroll position for co-watch chips on load and reload so refresh starts cleanly at top

// ---- command_palette.js ----
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
