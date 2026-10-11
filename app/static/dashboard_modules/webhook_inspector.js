        let currentDebugWebhooks = [];

        function formatLocalTimestamp(value) {
            const date = new Date(value);
            if (!value || Number.isNaN(date.getTime())) return value || 'Time unavailable';
            return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(date);
        }

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
                    filterDebugWebhooks();
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
                const visibleCount = document.getElementById('webhook-debugger-visible-count');
                if (visibleCount) visibleCount.textContent = '0 shown';
                const message = currentDebugWebhooks.length
                    ? 'No webhook events match these filters.'
                    : 'No incoming webhooks recorded yet. Incoming media server and standalone scrobbles will appear here automatically.';
                listEl.innerHTML = `<div class="u-color-text-muted u-font-size-12px u-font-style-italic u-padding-12px-0">${message}</div>`;
                return;
            }

            const visibleCount = document.getElementById('webhook-debugger-visible-count');
            if (visibleCount) visibleCount.textContent = `${webhooks.length} shown`;
            listEl.innerHTML = webhooks.map((wh, idx) => {
                const wid = encodeURIComponentForInlineJs(wh.id || `wh_${idx}`);
                const sourceName = String(wh.source || 'webhook').toLowerCase();
                const srcKey = ['plex', 'jellyfin', 'emby', 'standalone'].includes(sourceName) ? sourceName : 'default';
                const src = escapeHtml(sourceName.toUpperCase());
                const event = escapeHtml(wh.event || 'media.event');
                const title = escapeHtml(wh.media_title || 'Media Event');
                const rawTimestamp = wh.iso_timestamp || wh.timestamp || '';
                const ts = escapeHtml(formatLocalTimestamp(rawTimestamp));
                const utcTimestamp = escapeHtml(rawTimestamp);
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
                    <div class="debug-webhook-heading">
                        <div class="debug-webhook-metadata">
                            <span class="webhook-source-badge webhook-source-${srcKey}">${src}</span>
                            <strong class="u-color-text-main u-font-size-13px">${title}</strong>
                            ${statusBadge}
                            <span class="u-font-size-11px u-color-text-muted">${event}</span>
                        </div>
                        <div class="debug-webhook-actions">
                            <time class="u-font-size-10px u-color-text-muted" datetime="${utcTimestamp}" title="UTC: ${utcTimestamp}">${ts}</time>
                            <button type="button" onclick="togglePayloadViewer(decodeURIComponent('${wid}'))" class="btn-sm u-padding-2px-8px u-font-size-11px u-background-bg-surface u-border-1px-solid-var-border-color u-color-text-heading u-cursor-pointer">
                                View Payload
                            </button>
                            <button type="button" onclick="replayPayloadFromDebugger(decodeURIComponent('${wid}'))" class="btn-sm u-padding-2px-8px u-font-size-11px u-background-accent-color u-color-accent-text u-border-none u-cursor-pointer u-font-weight-600">
                                Test Payload
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

        function filterDebugWebhooks() {
            const query = (document.getElementById('webhook-debugger-search')?.value || '').trim().toLowerCase();
            const source = document.getElementById('webhook-debugger-source')?.value || '';
            const status = document.getElementById('webhook-debugger-status')?.value || '';
            const filtered = currentDebugWebhooks.filter((wh) => {
                const text = `${wh.media_title || ''} ${wh.event || ''} ${wh.reason || ''}`.toLowerCase();
                return (!query || text.includes(query)) && (!source || String(wh.source || '').toLowerCase() === source) && (!status || String(wh.status || 'received').toLowerCase() === status);
            });
            renderDebugWebhooksList(filtered);
        }

        async function copyVisibleWebhookPayloads() {
            const query = (document.getElementById('webhook-debugger-search')?.value || '').trim().toLowerCase();
            const source = document.getElementById('webhook-debugger-source')?.value || '';
            const status = document.getElementById('webhook-debugger-status')?.value || '';
            const items = currentDebugWebhooks.filter((wh) => {
                const text = `${wh.media_title || ''} ${wh.event || ''} ${wh.reason || ''}`.toLowerCase();
                return (!query || text.includes(query)) && (!source || String(wh.source || '').toLowerCase() === source) && (!status || String(wh.status || 'received').toLowerCase() === status);
            });
            try {
                await navigator.clipboard.writeText(items.map((wh) => JSON.stringify(wh.payload || {}, null, 2)).join('\n\n'));
                const count = document.getElementById('webhook-debugger-visible-count');
                if (count) count.textContent = `${items.length} payloads copied`;
            } catch (_) {
                const count = document.getElementById('webhook-debugger-visible-count');
                if (count) count.textContent = 'Clipboard unavailable';
            }
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
