        async function downloadAnalyticsExport(kind, format) {
            const status = document.getElementById('analytics-export-status');
            if (isDemo) {
                if (status) status.textContent = 'Exports are available in an authenticated Omniscrobble instance.';
                return;
            }
            const params = new URLSearchParams({kind, format});
            const filters = {
                start_date: 'analytics-export-start',
                end_date: 'analytics-export-end',
                profile: 'analytics-export-profile',
                media_type: 'analytics-export-media',
                server: 'analytics-export-server',
                tracker: 'analytics-export-tracker',
                viewing: 'analytics-export-viewing',
            };
            for (const [name, id] of Object.entries(filters)) {
                const value = document.getElementById(id)?.value?.trim();
                if (value) params.set(name, value);
            }
            if (status) status.textContent = 'Preparing export…';
            try {
                const response = await fetch(`/api/analytics/export?${params.toString()}`);
                if (!response.ok) {
                    let message = 'Export failed.';
                    try { message = (await response.json()).detail || message; } catch (error) {}
                    throw new Error(message);
                }
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                const disposition = response.headers.get('Content-Disposition') || '';
                const match = disposition.match(/filename=([^;]+)/i);
                link.download = match ? match[1].replaceAll('"', '') : `omniscrobble_${kind}.${format}`;
                link.href = url;
                document.body.append(link);
                link.click();
                link.remove();
                window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                if (status) status.textContent = 'Export downloaded.';
            } catch (error) {
                if (status) status.textContent = error.message || 'Export failed.';
            }
        }
