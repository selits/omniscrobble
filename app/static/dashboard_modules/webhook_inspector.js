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
