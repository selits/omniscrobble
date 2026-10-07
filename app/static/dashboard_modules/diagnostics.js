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
