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
        async function copySettingsField(fieldId, button) {
            const field = document.getElementById(fieldId);
            if (!field?.value) return;
            try {
                await navigator.clipboard.writeText(field.value);
                const original = button.textContent;
                button.textContent = 'Copied';
                window.setTimeout(() => { button.textContent = original; }, 1400);
            } catch (error) {
                const status = document.getElementById('settings-modal-status-msg');
                if (status) status.textContent = 'Clipboard access was unavailable. Select and copy the client ID manually.';
            }
        }
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
