        let currentOmniWrappedData = null;

        function openOmniWrappedModal() {
            document.getElementById('omniwrapped-modal').style.display = 'flex';
            loadOmniWrapped();
        }

        function closeOmniWrappedModal() {
            document.getElementById('omniwrapped-modal').style.display = 'none';
        }

        async function loadOmniWrapped(year = 2026) {
            try {
                const url = isDemo ? `/api/analytics/wrapped?year=${year}&demo=true` : `/api/analytics/wrapped?year=${year}`;
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    currentOmniWrappedData = data;
                    renderOmniWrappedModal(data);
                }
            } catch (e) {
                console.error('Failed to load OmniWrapped:', e);
            }
        }

        function renderOmniWrappedModal(data) {
            if (!data) return;
            const headlineEl = document.getElementById('omniwrapped-headline');
            if (headlineEl) headlineEl.innerText = data.headline || `Your ${data.year || 2026} OmniWrapped`;

            const archetypeEl = document.getElementById('omniwrapped-archetype');
            if (archetypeEl) archetypeEl.innerText = data.archetype || 'The Media Connoisseur';

            const descEl = document.getElementById('omniwrapped-description');
            if (descEl) descEl.innerText = data.description || '';

            const wtEl = document.getElementById('omniwrapped-watchtime');
            if (wtEl) wtEl.innerText = data.total_watch_time || `${data.total_watch_hours || 0}h`;

            const titlesEl = document.getElementById('omniwrapped-titles');
            if (titlesEl) titlesEl.innerText = data.total_scrobbles || 0;
            const titlesSubEl = document.getElementById('omniwrapped-titles-sub');
            if (titlesSubEl) titlesSubEl.innerText = `${data.unique_titles || 0} unique titles`;

            const topShowEl = document.getElementById('omniwrapped-top-show');
            const topShowSubEl = document.getElementById('omniwrapped-top-show-sub');
            if (topShowEl && data.top_binge) {
                topShowEl.innerText = data.top_binge.show || 'Series';
                if (topShowSubEl) topShowSubEl.innerText = `${data.top_binge.episodes || 0} episodes logged`;
            }

            const cwPctEl = document.getElementById('omniwrapped-cowatch-pct');
            const cwPartnerEl = document.getElementById('omniwrapped-cowatch-partner');
            if (cwPctEl && data.cowatch_breakdown) {
                cwPctEl.innerText = `${data.cowatch_breakdown.cowatch_percentage || 0}% Shared`;
                if (cwPartnerEl) cwPartnerEl.innerText = `with @${data.cowatch_breakdown.partner_user || 'Partner'}`;
            }
        }

        function downloadOmniWrappedJson() {
            if (!currentOmniWrappedData) return;
            const blob = new Blob([JSON.stringify(currentOmniWrappedData, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `omniwrapped_${currentOmniWrappedData.year || 2026}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        // Reset scroll position for co-watch chips on load and reload so refresh starts cleanly at top
