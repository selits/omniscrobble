        let watchLists = [];
        let watchListCowatchShows = [];
        let watchListCowatchAvailable = false;
        let watchListCatalogMatches = [];
        function watchListMessage(message, error = false) {
            const node = document.getElementById('watch-list-message');
            if (node) { node.textContent = message; node.style.color = error ? 'var(--status-error)' : 'var(--text-muted)'; }
        }
        async function watchListRequest(url, options = {}) {
            const response = await fetch(url, options);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Watch-list request failed');
            return data;
        }
        async function loadWatchLists() {
            if (!isAdmin && !isDemo) {
                watchListMessage('Unlock admin access to view and manage your private watch lists.', true);
                return;
            }
            if (isDemo) {
                watchLists = [{id:'demo-watch-list', name:'Weekend picks', items:[
                    {id:'demo-movie', title:'Arrival', media_type:'movie', year:2016, ids:{}, position:0},
                    {id:'demo-anime', title:'Frieren: Beyond Journey’s End', media_type:'anime', year:2023, ids:{anilist:154587}, position:1}
                ]}];
                watchListCowatchShows = ['Frieren: Beyond Journey’s End'];
                watchListCowatchAvailable = true;
                document.querySelectorAll('#card-watch-lists button, #card-watch-lists input, #card-watch-lists select').forEach(control => { control.disabled = true; });
                document.getElementById('watch-list-select').innerHTML = '<option value="demo-watch-list">Weekend picks</option>';
                renderWatchListItems();
                watchListMessage('Sample data shown in demo mode.');
                return;
            }
            try {
                const [data, shows] = await Promise.all([
                    watchListRequest('/api/watch-lists'),
                    watchListRequest('/api/cowatch')
                ]);
                watchLists = data.lists || [];
                watchListCowatchShows = shows.status?.shows || [];
                watchListCowatchAvailable = Boolean(data.cowatch_available);
                const select = document.getElementById('watch-list-select');
                const previous = select.value;
                select.innerHTML = watchLists.map(list => `<option value="${escapeHtml(list.id)}">${escapeHtml(list.name)}</option>`).join('');
                if (watchLists.some(list => list.id === previous)) select.value = previous;
                renderWatchListItems();
                watchListMessage(watchLists.length ? `${watchLists.length} list${watchLists.length === 1 ? '' : 's'} saved locally.` : 'Create a list to get started.');
            } catch (error) { watchListMessage(error.message, true); }
        }
        function selectedWatchList() { return watchLists.find(list => list.id === document.getElementById('watch-list-select')?.value); }
        async function createWatchList() {
            const name = window.prompt('Name this watch list:');
            if (!name?.trim()) return;
            try { await watchListRequest('/api/watch-lists', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function renameWatchList() {
            const list = selectedWatchList(); if (!list) return;
            const name = window.prompt('Rename this list:', list.name); if (!name?.trim()) return;
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function deleteWatchList() {
            const list = selectedWatchList(); if (!list || !window.confirm(`Delete “${list.name}” and its ${list.items.length} item(s)?`)) return;
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}`, {method:'DELETE'}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function addWatchListItem(item = null) {
            const list = selectedWatchList(); if (!list) { watchListMessage('Create or select a list first.', true); return; }
            const title = item?.title || document.getElementById('watch-item-title').value.trim();
            const type = item?.media_type || document.getElementById('watch-item-type').value;
            const yearValue = item?.year || document.getElementById('watch-item-year').value;
            if (!title) { watchListMessage('Enter a title first.', true); return; }
            const payload = item || {title, media_type:type, year:yearValue ? Number(yearValue) : null};
            try {
                await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
                document.getElementById('watch-item-title').value = ''; document.getElementById('watch-item-year').value = '';
                await loadWatchLists(); watchListMessage(`Added ${title}.`);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function searchAnimeForWatchList() {
            const title = document.getElementById('watch-item-title').value.trim();
            if (!title) { watchListMessage('Enter an anime title to search.', true); return; }
            const year = document.getElementById('watch-item-year').value;
            try {
                const data = await watchListRequest(`/api/watch-lists/anime-search?title=${encodeURIComponent(title)}${year ? `&year=${encodeURIComponent(year)}` : ''}`);
                if (!data.match) { watchListMessage('AniList did not find a match. You can add the title manually.', true); return; }
                if (!window.confirm(`Add AniList match “${data.match.title}${data.match.year ? ` (${data.match.year})` : ''}”?`)) return;
                await addWatchListItem(data.match);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function searchWatchListCatalog() {
            const title = document.getElementById('watch-item-title').value.trim();
            const mediaType = document.getElementById('watch-item-type').value;
            const year = document.getElementById('watch-item-year').value;
            const box = document.getElementById('watch-list-search-results');
            if (!title) { watchListMessage('Enter a title to search.', true); return; }
            if (mediaType === 'anime') { watchListMessage('Use the AniList search for anime, or add the title manually.'); return; }
            box.textContent = 'Searching the configured catalog…';
            try {
                const kind = mediaType === 'movie' ? 'movie' : 'series';
                const response = await watchListRequest(`/api/arr/lookup?type=${kind}&term=${encodeURIComponent(title)}`);
                watchListCatalogMatches = response.results || [];
                if (!watchListCatalogMatches.length) { box.textContent = 'No catalog matches found. You can still add the title manually.'; return; }
                box.innerHTML = watchListCatalogMatches.map((match, index) => `<button type="button" class="u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-8px-10px u-color-text-main u-cursor-pointer" onclick="addWatchListCatalogMatch(${index})"><span><strong>${escapeHtml(match.title)}${match.year ? ` (${escapeHtml(match.year)})` : ''}</strong>${match.network ? ` · ${escapeHtml(match.network)}` : ''}</span><span>${match.in_library ? 'Already in library' : 'Add to list'}</span></button>`).join('');
            } catch (error) { box.textContent = `${error.message}. You can still add the title manually.`; }
        }
        async function addWatchListCatalogMatch(index) {
            const match = watchListCatalogMatches[index]; if (!match) return;
            const payload = match.payload || {};
            const ids = {};
            if (payload.tmdbId) ids.tmdb = payload.tmdbId;
            if (payload.tvdbId) ids.tvdb = payload.tvdbId;
            if (payload.imdbId) ids.imdb = payload.imdbId;
            await addWatchListItem({title:match.title, media_type:document.getElementById('watch-item-type').value,
                year:match.year || null, ids, poster_url:match.poster_url || null});
            document.getElementById('watch-list-search-results').replaceChildren();
            watchListCatalogMatches = [];
        }
        function renderWatchListItems() {
            const list = selectedWatchList();
            const box = document.getElementById('watch-list-items'); if (!box) return;
            if (!list) { box.innerHTML = '<p class="u-color-text-muted">No watch lists yet. Select New list to create one.</p>'; return; }
            if (!list.items.length) { box.innerHTML = '<p class="u-color-text-muted">This list is empty.</p>'; return; }
            const readOnly = isDemo ? 'disabled' : '';
            box.innerHTML = list.items.map((item, index) => {
                const year = item.year ? ` (${item.year})` : '';
                const cw = item.media_type !== 'movie' && watchListCowatchShows.some(show => show.toLowerCase() === item.title.toLowerCase());
                const arrType = item.media_type === 'movie' ? 'movie' : 'series';
                return `<article class="u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-10px-12px">
                    <div><strong>${escapeHtml(item.title)}${escapeHtml(year)}</strong><div class="u-color-text-muted u-font-size-11px">${escapeHtml(item.media_type.toUpperCase())}${item.ids?.anilist ? ` · AniList #${escapeHtml(item.ids.anilist)}` : ''}</div></div>
                    <div class="u-display-flex u-flex-wrap-wrap u-gap-6px">
                        <button class="btn-sm" aria-label="Move ${escapeHtml(item.title)} up" onclick="moveWatchListItem(${index},-1)" ${readOnly || (index === 0 ? 'disabled' : '')}>↑</button><button class="btn-sm" aria-label="Move ${escapeHtml(item.title)} down" onclick="moveWatchListItem(${index},1)" ${readOnly || (index === list.items.length - 1 ? 'disabled' : '')}>↓</button>
                        <button class="btn-sm" onclick="sendWatchItemToArr(${index})" ${readOnly}>Add to ${arrType === 'movie' ? 'Radarr' : 'Sonarr'}</button>
                        ${item.media_type !== 'movie' ? `<button class="btn-sm" onclick="toggleWatchItemCowatch(${index})" ${readOnly || (watchListCowatchAvailable ? '' : 'disabled title="Configure a Co-Watch partner account first"')}>${cw ? 'Remove Co-Watch' : 'Add Co-Watch'}</button>` : ''}
                        <button class="btn-sm" onclick="removeWatchListItem(${index})" aria-label="Remove ${escapeHtml(item.title)}" ${readOnly}>Remove</button>
                    </div></article>`;
            }).join('');
        }
        async function removeWatchListItem(index) {
            const list = selectedWatchList(), item = list?.items[index]; if (!item) return;
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {method:'DELETE'}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function moveWatchListItem(index, delta) {
            const list = selectedWatchList(); if (!list) return;
            const ids = list.items.map(item => item.id), target = index + delta;
            if (target < 0 || target >= ids.length) return;
            [ids[index], ids[target]] = [ids[target], ids[index]];
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items/order`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({item_ids:ids})}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        function sendWatchItemToArr(index) {
            const item = selectedWatchList()?.items[index]; if (!item) return;
            openAddArrModal(item.media_type === 'movie' ? 'movie' : 'series', item.title);
        }
        async function toggleWatchItemCowatch(index) {
            const item = selectedWatchList()?.items[index]; if (!item) return;
            const enrolled = watchListCowatchShows.some(show => show.toLowerCase() === item.title.toLowerCase());
            try {
                const url = `/api/cowatch/shows${enrolled ? `?show=${encodeURIComponent(item.title)}` : ''}`;
                const data = await watchListRequest(url, enrolled ? {method:'DELETE'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({show:item.title})});
                watchListCowatchShows = data.shows || []; renderWatchListItems();
                watchListMessage(enrolled ? 'Removed from Co-Watch.' : 'Added to Co-Watch.');
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function exportWatchLists(format) {
            try {
                const response = await fetch(`/api/watch-lists/export?format=${format}`); if (!response.ok) throw new Error('Export failed');
                const blob = await response.blob(), link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `watch-lists.${format}`; link.click(); URL.revokeObjectURL(link.href);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function copyWatchLists() {
            try { const response = await fetch('/api/watch-lists/export?format=txt'); if (!response.ok) throw new Error('Export failed'); await navigator.clipboard.writeText(await response.text()); watchListMessage('Watch lists copied to clipboard.'); }
            catch (error) { watchListMessage(`Clipboard access failed: ${error.message}`, true); }
        }
        async function importWatchListData(data) {
            try {
                const preview = await watchListRequest('/api/watch-lists/import/preview', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data})});
                const summary = `${preview.list_count} list(s), ${preview.item_count} unique item(s), ${preview.duplicates_skipped} duplicate(s) skipped.`;
                const choice = window.prompt(`Import preview: ${summary}\n\nType merge to add items to matching lists, replace to overwrite all saved lists, or cancel to abort.`, 'merge');
                if (!choice || !['merge', 'replace'].includes(choice.trim().toLowerCase())) return;
                const replace = choice.trim().toLowerCase() === 'replace';
                if (replace && !window.confirm('Replace every saved watch list with the imported data? This cannot be undone.')) return;
                const result = await watchListRequest('/api/watch-lists/import', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data, replace})});
                await loadWatchLists(); watchListMessage(`Import complete: ${summary} ${result.duplicates_skipped} existing duplicate(s) skipped.`);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function readWatchListImport(input) {
            const file = input.files?.[0]; if (!file) return;
            if (file.size > 2_000_000) { watchListMessage('Import is too large (2 MB maximum).', true); input.value = ''; return; }
            await importWatchListData(await file.text()); input.value = '';
        }
        async function openWatchListPaste() {
            const text = window.prompt('Paste JSON or exported watch-list text here:');
            if (text?.trim()) await importWatchListData(text);
        }
        document.addEventListener('DOMContentLoaded', loadWatchLists);
