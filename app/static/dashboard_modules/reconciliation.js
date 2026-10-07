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
