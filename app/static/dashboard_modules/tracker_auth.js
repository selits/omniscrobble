        let simklPollInterval = null;

        function openSimklModal() {
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
            if (isMember) { openAccountModal(); return; }
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
                tbody.innerHTML = '<tr><td colspan="6" class="u-text-align-center u-padding-24px u-color-accent-color">Scanning configured tracker accounts...</td></tr>';
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
            const t2tmdbCount = crossSyncDiffItems.filter(i => i.direction === 'trakt_to_tmdb').length;
            const tmdb2tCount = crossSyncDiffItems.filter(i => i.direction === 'tmdb_to_trakt').length;
            const ratingCount = crossSyncDiffItems.filter(i => i.sync_type === 'rating').length;

            const cAll = document.getElementById('cross-count-all');
            const cT2s = document.getElementById('cross-count-t2s');
            const cS2t = document.getElementById('cross-count-s2t');
            const cT2tmdb = document.getElementById('cross-count-t2tmdb');
            const cTmdb2t = document.getElementById('cross-count-tmdb2t');
            const cRatings = document.getElementById('cross-count-ratings');
            const badge = document.getElementById('cross-sync-count-badge');

            if (cAll) cAll.innerText = allCount;
            if (cT2s) cT2s.innerText = t2sCount;
            if (cS2t) cS2t.innerText = s2tCount;
            if (cT2tmdb) cT2tmdb.innerText = t2tmdbCount;
            if (cTmdb2t) cTmdb2t.innerText = tmdb2tCount;
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
            } else if (crossSyncActiveFilter === 'trakt_to_tmdb') {
                items = items.filter(i => i.direction === 'trakt_to_tmdb');
            } else if (crossSyncActiveFilter === 'tmdb_to_trakt') {
                items = items.filter(i => i.direction === 'tmdb_to_trakt');
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
                const detailNotes = [];
                if (item.match_reason) detailNotes.push(`${item.match_reason}; ${item.source_of_truth === 'tmdb' ? 'TMDb' : 'Trakt'} is the source`);
                if (item.rating_conflict_key) {
                    const otherTracker = item.rating_conflict_key.includes(':tmdb:') ? 'TMDb' : 'Simkl';
                    detailNotes.push(`Trakt rated: ${item.trakt_rated_at || 'timestamp unavailable'} · ${otherTracker} rated: ${item[otherTracker.toLowerCase() + '_rated_at'] || 'timestamp unavailable'}`);
                }
                const matchNote = detailNotes.length
                    ? `<div class="u-font-size-10px u-color-text-muted u-margin-top-3px">${detailNotes.map(note => escapeHtml(note)).join('<br>')}</div>`
                    : '';

                const dirBadge = item.direction === 'trakt_to_simkl'
                    ? '<span class="u-background-1e1b4b u-border-1px-solid-4338ca u-color-a5b4fc u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">Trakt &rarr; Simkl</span>'
                    : item.direction === 'trakt_to_tmdb'
                        ? '<span class="u-background-164e63 u-border-1px-solid-0891b2 u-color-a5f3fc u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">Trakt &rarr; TMDb</span>'
                        : item.direction === 'tmdb_to_trakt'
                            ? '<span class="u-background-164e63 u-border-1px-solid-0891b2 u-color-a5f3fc u-padding-2px-8px u-border-radius-4px u-font-size-11px u-font-weight-600">TMDb &rarr; Trakt</span>'
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
                        <td class="u-padding-10px-14px u-color-text-main u-font-weight-500">${titleStr}${matchNote}</td>
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
            } else if (crossSyncActiveFilter === 'trakt_to_tmdb') {
                items = items.filter(i => i.direction === 'trakt_to_tmdb');
            } else if (crossSyncActiveFilter === 'tmdb_to_trakt') {
                items = items.filter(i => i.direction === 'tmdb_to_trakt');
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
            const conflictPolicy = document.getElementById('cross-sync-conflict-policy')?.value || 'manual';

            if (pBox) pBox.style.display = 'block';
            if (pMsg) pMsg.innerText = 'Starting cross-tracker synchronization...';
            if (pBar) pBar.style.width = '10%';
            if (pStats) pStats.innerText = 'Initializing';

            try {
                const url = isDemo ? '/api/cross-sync/execute?demo=true' : '/api/cross-sync/execute';
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ...payload, conflict_policy: conflictPolicy })
                });
                const data = await res.json();

                if (!res.ok || data.status === 'error') {
                    if (pBar) pBar.style.width = '100%';
                    if (pMsg) pMsg.innerText = data.detail || data.message || 'Cross-tracker synchronization could not start.';
                    if (pStats) pStats.innerText = 'Needs review';
                    return;
                }

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
            if (isMember) {
                const subtitle = document.getElementById("header-title-sub");
                if (subtitle) subtitle.textContent = "My activity, tracker connections, and shared watch lists";
                return;
            }
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
