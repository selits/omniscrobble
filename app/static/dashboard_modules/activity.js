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

            if (cw && cw.status === 'scheduled') {
                return '<span class="activity-status-badge activity-status-queued" title="Co-Watch sync is running">⏳ Co-Watch pending</span>';
            }
            if (cw && cw.status === 'queued') {
                return '<span class="activity-status-badge activity-status-queued" title="Co-Watch history is waiting for retry">⏳ Co-Watch queued</span>';
            }
            if (cw && cw.status === 'partial') {
                return '<span class="activity-status-badge activity-status-failed" title="Some Co-Watch destinations did not sync">! Co-Watch partial</span>';
            }
            if (cw && cw.status === 'failed') {
                return '<span class="activity-status-badge activity-status-failed" title="Co-Watch sync failed">✕ Co-Watch failed</span>';
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
                if (typeof updateSetupChecklist === 'function') updateSetupChecklist();
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
                    <td class="activity-time" data-label="When">${escapeHtml(ev.timestamp)}</td>
                    <td class="activity-title" data-label="Title">${escapeHtml(ev.title)}</td>
                    <td data-label="Type"><span class="activity-type">${escapeHtml(ev.type)}</span></td>
                    <td class="activity-user" data-label="User"><div class="activity-user-content">${serverBadge}<span>${escapeHtml(ev.user)}</span></div></td>
                    <td data-label="Action"><span class="activity-action">${escapeHtml(actionText)}</span></td>
                    <td data-label="Status"><div class="activity-status-group">${statusBadgeHtml}${renderTrackerDeliveryBadges(ev.tracker_delivery)}</div></td>
                    ${actionCol}
                </tr>`;
            }
            tbody.innerHTML = html;
            activityFreshKeys.clear();
            if (typeof updateSetupChecklist === 'function') updateSetupChecklist();
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
