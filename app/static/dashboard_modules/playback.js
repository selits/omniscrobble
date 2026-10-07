        async function fetchPlayback() {
            try {
                const url = isDemo ? '/api/playback?demo=true' : '/api/playback';
                const res = await fetch(url);
                if (res.ok) {
                    const data = await res.json();
                    renderPlayback(data);
                }
            } catch (e) {
                console.error('Error fetching playback:', e);
            }
        }

        function renderPlayback(data) {
            const card = document.getElementById('active-playback-card');
            if (!card) return;
            const sessions = data.active_sessions || [];
            const posterImg = document.getElementById('stream-poster-img');
            const posterFallback = document.getElementById('stream-poster-fallback');
            const backdropEl = document.getElementById('stream-ambient-backdrop');

            if (sessions.length > 0) {
                const s = sessions[0];
                card.hidden = false;
                card.classList.remove('playback-state-playing', 'playback-state-paused', 'playback-state-finished');
                card.classList.add(s.state === 'playing' ? 'playback-state-playing' : 'playback-state-paused');
                document.getElementById('stream-state-badge').textContent = s.state === 'playing' ? 'Currently Streaming' : 'Paused';
                const devStr = (isAdmin && s.player) ? (` on ${s.player}${s.device ? ' (' + s.device + ')' : ''}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${s.username}${devStr}`;
                document.getElementById('stream-title').textContent = s.title;
                document.getElementById('stream-trakt-link').href = s.trakt_url || 'https://trakt.tv';
                const progText = s.remaining_str ? `${s.progress.toFixed(1)}% • ${s.remaining_str}` : `${s.progress.toFixed(1)}%`;
                document.getElementById('stream-progress-text').textContent = progText;
                document.getElementById('stream-progress-bar').style.width = `${s.progress}%`;

                // Poster & Ambient Backdrop Update
                const pUrl = s.poster_url || (s.ids && s.ids.imdb ? `https://images.metahub.space/poster/medium/${s.ids.imdb}/img` : null);
                const bUrl = s.backdrop_url || (s.ids && s.ids.imdb ? `https://images.metahub.space/background/medium/${s.ids.imdb}/img` : pUrl);
                if (posterImg && posterFallback) {
                    if (pUrl) {
                        posterImg.src = pUrl;
                        posterImg.hidden = false;
                        posterFallback.hidden = true;
                    } else {
                        posterImg.hidden = true;
                        posterFallback.hidden = false;
                    }
                }
                if (backdropEl) {
                    if (bUrl) {
                        backdropEl.style.backgroundImage = `url('${bUrl}')`;
                    } else {
                        backdropEl.style.backgroundImage = 'none';
                        backdropEl.style.backgroundImage = 'radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%)';
                    }
                }
            } else if (data.recently_finished) {
                const f = data.recently_finished;
                card.hidden = false;
                card.classList.remove('playback-state-playing', 'playback-state-paused', 'playback-state-finished');
                card.classList.add('playback-state-finished');
                document.getElementById('stream-state-badge').textContent = 'Recently Finished';
                const fDevStr = (isAdmin && f.player) ? (` on ${f.player}`) : '';
                document.getElementById('stream-user-device').textContent = `• ${f.username}${fDevStr}`;
                document.getElementById('stream-title').textContent = f.title;
                document.getElementById('stream-trakt-link').href = f.trakt_url || 'https://trakt.tv';
                document.getElementById('stream-progress-text').textContent = '100.0% • Finished';
                document.getElementById('stream-progress-bar').style.width = '100%';

                // Poster & Ambient Backdrop Update
                const pUrl = f.poster_url;
                const bUrl = f.backdrop_url || pUrl;
                if (posterImg && posterFallback) {
                    if (pUrl) {
                        posterImg.src = pUrl;
                        posterImg.hidden = false;
                        posterFallback.hidden = true;
                    } else {
                        posterImg.hidden = true;
                        posterFallback.hidden = false;
                    }
                }
                if (backdropEl) {
                    if (bUrl) {
                        backdropEl.style.backgroundImage = `url('${bUrl}')`;
                    } else {
                        backdropEl.style.backgroundImage = 'none';
                        backdropEl.style.backgroundImage = 'radial-gradient(circle at top right, var(--accent-glow, rgba(56, 189, 248, 0.3)), transparent 60%)';
                    }
                }
            } else {
                card.hidden = true;
            }
        }
