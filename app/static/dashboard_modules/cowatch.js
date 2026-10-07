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
