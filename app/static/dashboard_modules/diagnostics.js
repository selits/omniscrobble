        let allRawLogs = [];
        let allLogEntries = [];
        let visibleRawLogs = [];
        let logRefreshTimer = null;

        function parseLogEntry(line) {
            const levelMatch = line.match(/\[([A-Z]+)\]/);
            const timestampMatch = line.match(/\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))/);
            return { line, level: levelMatch ? levelMatch[1] : 'UNKNOWN', timestamp: timestampMatch ? timestampMatch[1] : '' };
        }

        function setLogAutoRefresh() {
            if (logRefreshTimer) clearInterval(logRefreshTimer);
            logRefreshTimer = null;
            const seconds = Number(document.getElementById('logs-refresh-interval')?.value || 0);
            if (!seconds || document.getElementById('diagnostics-pane-logs')?.hidden) return;
            logRefreshTimer = setInterval(fetchLogs, seconds * 1000);
        }

        function updateLogArchiveEstimate() {
            const size = Number(document.getElementById('logs-archive-size')?.value || 10);
            const countInput = document.getElementById('logs-archive-count');
            const maxCount = Math.max(1, Math.min(50, Math.floor(128 / size) - 1));
            if (countInput) {
                countInput.max = String(maxCount);
                if (Number(countInput.value) > maxCount) countInput.value = String(maxCount);
            }
            const count = Number(countInput?.value || 5);
            const estimate = document.getElementById('logs-archive-cap');
            if (estimate) estimate.textContent = `Maximum storage: about ${size * (count + 1)} MiB`;
        }

        async function saveLogArchiveSettings() {
            if (!isAdmin || isDemo) return;
            const sizeInput = document.getElementById('logs-archive-size');
            const countInput = document.getElementById('logs-archive-count');
            const status = document.getElementById('logs-archive-status');
            const maxMiB = Number(sizeInput?.value);
            const backupCount = Number(countInput?.value);
            const maxCount = Number.isFinite(maxMiB) && maxMiB > 0
                ? Math.max(1, Math.min(50, Math.floor(128 / maxMiB) - 1))
                : 1;
            if (!Number.isInteger(maxMiB) || maxMiB < 1 || maxMiB > 64 || !Number.isInteger(backupCount) || backupCount < 1 || backupCount > maxCount) {
                if (status) status.textContent = `Choose a file size from 1–64 MiB and no more than ${maxCount} archives at that size.`;
                return;
            }
            if (status) status.textContent = 'Saving…';
            try {
                const response = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ logs: { max_bytes: maxMiB * 1024 * 1024, backup_count: backupCount } })
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to save log archive settings.');
                const saved = data.settings?.logs || { max_bytes: maxMiB * 1024 * 1024, backup_count: backupCount };
                if (sizeInput) sizeInput.value = String(Math.round(saved.max_bytes / (1024 * 1024)));
                if (countInput) countInput.value = String(saved.backup_count);
                updateLogArchiveEstimate();
                if (status) status.textContent = 'Saved and applied.';
                fetchLogs();
            } catch (error) {
                if (status) status.textContent = `Save failed: ${error.message}`;
            }
        }

        function formatLogTimestamp(line) {
            const match = line.match(/\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))/);
            if (!match) return escapeHtml(line);
            const date = new Date(match[1]);
            if (Number.isNaN(date.getTime())) return escapeHtml(line);
            const local = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' }).format(date);
            return `${escapeHtml(line.slice(0, match.index))}<time datetime="${escapeHtml(match[1])}" title="UTC: ${escapeHtml(date.toISOString())}">${escapeHtml(local)}</time>${escapeHtml(line.slice(match.index + match[1].length))}`;
        }

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
            setLogAutoRefresh();
        }

        function closeDiagnosticsDrawer() {
            const drawer = document.getElementById('diagnostics-modal');
            if (drawer) drawer.style.display = 'none';
            if (logRefreshTimer) clearInterval(logRefreshTimer);
            logRefreshTimer = null;
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
                    allLogEntries = data.entries || allRawLogs.map(parseLogEntry);
                    const archivePanel = document.getElementById('logs-archive-settings');
                    if (archivePanel) archivePanel.hidden = !isAdmin || isDemo;
                    if (data.archive_settings) {
                        const sizeInput = document.getElementById('logs-archive-size');
                        const countInput = document.getElementById('logs-archive-count');
                        if (sizeInput) sizeInput.value = String(Math.round(data.archive_settings.max_bytes / (1024 * 1024)));
                        if (countInput) countInput.value = String(data.archive_settings.backup_count);
                        updateLogArchiveEstimate();
                    }
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

            const filteredEntries = allLogEntries.filter(entry => {
                const line = entry.line || `${entry.timestamp || ''} [${entry.level || 'UNKNOWN'}] ${entry.name || ''}: ${entry.message || ''}`;
                const lower = line.toLowerCase();
                const severity = String(entry.level || 'UNKNOWN').toUpperCase();
                if (query && !lower.includes(query)) return false;
                if (level === 'ERROR') return ['ERROR', 'CRITICAL', 'FATAL'].includes(severity);
                if (level === 'WARNING') return ['WARNING', 'ERROR', 'CRITICAL', 'FATAL'].includes(severity);
                if (level === 'INFO') return ['INFO', 'WARNING', 'ERROR', 'CRITICAL', 'FATAL'].includes(severity);
                return true;
            });
            const filtered = filteredEntries.map(entry => entry.line || `${entry.timestamp || ''} [${entry.level || 'UNKNOWN'}] ${entry.name || ''}: ${entry.message || ''}`);
            visibleRawLogs = filtered;

            if (countLabel) countLabel.textContent = `${filtered.length} lines`;

            if (filtered.length === 0) {
                terminal.innerHTML = '<span class="u-color-text-muted u-font-style-italic">No log lines matched filter criteria.</span>';
                return;
            }

            terminal.innerHTML = filteredEntries.map((entry, index) => {
                const line = filtered[index];
                const safe = formatLogTimestamp(line);
                const severity = String(entry.level || 'UNKNOWN').toUpperCase();
                if (['ERROR', 'CRITICAL', 'FATAL'].includes(severity)) {
                    return `<div class="u-color-f87171">${safe}</div>`;
                }
                if (severity === 'WARNING') {
                    return `<div class="u-color-fbbf24">${safe}</div>`;
                }
                if (severity === 'INFO') {
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
            const text = visibleRawLogs.join('\n');
            const legacyCopy = () => {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                const copied = document.execCommand('copy');
                textarea.remove();
                return copied;
            };
            const showCopied = () => {
                if (!btn) return;
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Copied!';
                btn.style.background = 'var(--status-playing)';
                setTimeout(() => {
                    btn.innerHTML = orig;
                    btn.style.background = 'var(--accent-color)';
                }, 2000);
            };
            const fallbackOrFail = () => {
                if (legacyCopy()) showCopied();
                else alert('Unable to copy logs. Select and copy them manually.');
            };
            if (!navigator.clipboard || !navigator.clipboard.writeText) {
                fallbackOrFail();
                return;
            }
            navigator.clipboard.writeText(text).then(showCopied).catch(fallbackOrFail);
        }
