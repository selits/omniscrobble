        let watchLists = [];
        let watchListCowatchShows = [];
        let watchListCowatchAvailable = false;
        let watchListsCanTransfer = false;
        let watchListsCanRequest = false;
        let watchListAutomationOptions = ['manual'];
        let watchListRequestService = 'Overseerr';
        let watchListCatalogMatches = [];
        function watchListDialog({title, message, value = '', type = 'text', confirmLabel = 'Continue', danger = false, options = []}) {
            return new Promise(resolve => {
                const returnFocus = document.activeElement;
                const modal = document.getElementById('watch-list-modal');
                const heading = document.getElementById('watch-list-dialog-title');
                const description = document.getElementById('watch-list-dialog-message');
                const field = document.getElementById('watch-list-dialog-field');
                const confirm = document.getElementById('watch-list-dialog-confirm');
                const cancel = document.getElementById('watch-list-dialog-cancel');
                heading.textContent = title; description.textContent = message;
                field.replaceChildren();
                if (type === 'text' || type === 'select' || type === 'textarea') {
                    const control = document.createElement(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
                    if (type === 'text') control.type = 'text';
                    control.id = 'watch-list-dialog-input'; control.className = 'watch-list-dialog-input';
                    control.setAttribute('aria-label', title);
                    if (type === 'select') options.forEach(option => { const node = document.createElement('option'); node.value = option.value; node.textContent = option.label; control.append(node); });
                    else control.value = value;
                    field.append(control);
                }
                confirm.textContent = confirmLabel;
                confirm.classList.toggle('is-danger', danger);
                modal.style.display = 'flex';
                const input = field.querySelector('input, select, textarea');
                (input || confirm).focus();
                const finish = result => {
                    modal.style.display = 'none';
                    confirm.removeEventListener('click', accept); cancel.removeEventListener('click', decline);
                    modal.removeEventListener('click', outside); document.removeEventListener('keydown', keydown);
                    if (returnFocus?.isConnected) returnFocus.focus();
                    resolve(result);
                };
                const accept = () => finish(type === 'confirm' ? true : (input ? input.value : true));
                const decline = () => finish(null);
                const outside = event => { if (event.target === modal) decline(); };
                const keydown = event => { if (event.key === 'Escape') decline(); else if (event.key === 'Enter' && event.target === input && type !== 'textarea') { event.preventDefault(); accept(); } };
                confirm.addEventListener('click', accept); cancel.addEventListener('click', decline);
                modal.addEventListener('click', outside); document.addEventListener('keydown', keydown);
            });
        }
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
            if (isDemo) {
                watchLists = [{id:'demo-watch-list', name:'Weekend picks', items:[
                    {id:'demo-movie', title:'Arrival', media_type:'movie', year:2016, ids:{}, position:0},
                    {id:'demo-anime', title:'Frieren: Beyond Journey’s End', media_type:'anime', year:2023, ids:{anilist:154587}, position:1}
                ]}];
                watchListCowatchShows = ['Frieren: Beyond Journey’s End'];
                watchListCowatchAvailable = true;
                watchListsCanTransfer = false;
                watchListsCanRequest = false;
                watchListAutomationOptions = ['manual'];
                document.querySelectorAll('#card-watch-lists button, #card-watch-lists input, #card-watch-lists select').forEach(control => { control.disabled = true; });
                document.getElementById('watch-list-select').innerHTML = '<option value="demo-watch-list">Weekend picks</option>';
                renderWatchListItems();
                watchListMessage('Sample data shown in demo mode.');
                return;
            }
            try {
                const [data, shows] = await Promise.all([
                    watchListRequest('/api/watch-lists'),
                    isAdmin ? watchListRequest('/api/cowatch') : Promise.resolve({status:{shows:[]}})
                ]);
                watchLists = data.lists || [];
                watchListsCanTransfer = Boolean(data.can_transfer);
                watchListsCanRequest = Boolean(data.can_request);
                watchListAutomationOptions = data.automation_options || ['manual'];
                watchListRequestService = data.request_service_name || 'Overseerr';
                watchListCowatchShows = shows.status?.shows || [];
                watchListCowatchAvailable = Boolean(data.cowatch_available);
                const select = document.getElementById('watch-list-select');
                const previous = select.value;
                select.innerHTML = watchLists.map(list => `<option value="${escapeHtml(list.id)}">${escapeHtml(list.name)}</option>`).join('');
                if (watchLists.some(list => list.id === previous)) select.value = previous;
                const transferActions = document.getElementById('watch-list-transfer-actions');
                if (transferActions) transferActions.hidden = !watchListsCanTransfer;
                renderWatchListItems();
                watchListMessage(watchLists.length ? `${watchLists.length} shared or personal list${watchLists.length === 1 ? '' : 's'} available.` : 'Create a list to get started.');
            } catch (error) { watchListMessage(error.message, true); }
        }
        function selectedWatchList() { return watchLists.find(list => list.id === document.getElementById('watch-list-select')?.value); }
        function updateWatchListControls() {
            const list = selectedWatchList();
            const editable = Boolean(list?.can_edit) && !isDemo;
            const manageable = Boolean(list?.can_manage) && !isDemo;
            for (const id of ['watch-list-rename', 'watch-list-delete', 'watch-list-share', 'watch-list-automation']) {
                const button = document.getElementById(id);
                if (button) button.disabled = !manageable;
            }
            const refreshButton = document.getElementById('watch-list-refresh-availability');
            if (refreshButton) refreshButton.disabled = !list || isDemo;
            const automationButton = document.getElementById('watch-list-automation');
            if (automationButton) automationButton.textContent = `On item added: ${{manual:'Manual',request:'Request',acquire:'Acquire'}[list?.auto_action || 'manual']}`;
            for (const id of ['watch-item-title', 'watch-item-type', 'watch-item-year', 'watch-item-tags', 'watch-item-notes']) {
                const field = document.getElementById(id);
                if (field) field.disabled = !editable;
            }
            document.querySelectorAll('#card-watch-lists [data-watch-list-edit]').forEach(button => {
                button.disabled = !editable || (button.dataset.watchListEdit === 'cowatch' && !watchListCowatchAvailable);
            });
            document.querySelectorAll('#card-watch-lists [data-watch-list-admin-action]').forEach(button => { button.disabled = !isAdmin || isDemo; });
            for (const id of ['watch-list-add-item', 'watch-list-search-catalog', 'watch-list-search-anime']) {
                const button = document.getElementById(id);
                if (button) button.disabled = !editable;
            }
        }
        async function createWatchList() {
            const name = await watchListDialog({title:'Create a watch list', message:'Choose a name for this list.', confirmLabel:'Create list'});
            if (!name?.trim()) return;
            try { await watchListRequest('/api/watch-lists', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function shareWatchList() {
            const list = selectedWatchList(); if (!list?.can_manage) return;
            const current = Object.entries(list.members || {}).map(([username, role]) => `${username}=${role}`).join('\n');
            const value = await watchListDialog({title:'Share watch list', message:'Enter one dashboard member per line as username=editor or username=viewer. Clear the field to remove all shared access.', value:current, type:'textarea', confirmLabel:'Save access'});
            if (value === null) return;
            const members = {};
            for (const rawLine of value.split('\n').map(line => line.trim()).filter(Boolean)) {
                const [username, role, ...extra] = rawLine.split('=').map(part => part.trim());
                if (!username || !['editor', 'viewer'].includes(role) || extra.length) { watchListMessage('Use one username=editor or username=viewer entry per line.', true); return; }
                members[username] = role;
            }
            try {
                await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/access`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({members})});
                await loadWatchLists(); watchListMessage('Shared list access updated.');
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function configureWatchListAutomation() {
            const list = selectedWatchList(); if (!list?.can_manage || isDemo) return;
            const labels = {manual:'Manual — no automatic action', request:'Request through Overseerr/Jellyseerr', acquire:'Add to Sonarr/Radarr and search'};
            const action = await watchListDialog({title:'Choose what happens when an item is added', message:'Automatic requests or library acquisition can submit downloads. Provider approval settings apply to requests; acquisition uses the configured root folder and quality profile and starts a search.', type:'select', value:list.auto_action || 'manual', confirmLabel:'Save choice', options:watchListAutomationOptions.map(value => ({value, label:labels[value]}))});
            if (action === null) return;
            try {
                await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/automation`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action})});
                await loadWatchLists(); watchListMessage(`Automatic action set to ${action}.`);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function refreshWatchListAvailability() {
            const list = selectedWatchList(); if (!list || isDemo) return;
            const button = document.getElementById('watch-list-refresh-availability');
            if (button) { button.disabled = true; button.textContent = 'Checking…'; }
            try {
                const result = await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/availability`, {method:'POST'});
                await loadWatchLists();
                watchListMessage(`Availability refreshed for ${result.checked_count} item${result.checked_count === 1 ? '' : 's'} at ${new Date(result.checked_at).toLocaleString()}.`);
            } catch (error) { watchListMessage(error.message, true); }
            finally { if (button) { button.disabled = false; button.textContent = 'Refresh availability'; updateWatchListControls(); } }
        }
        async function renameWatchList() {
            const list = selectedWatchList(); if (!list) return;
            const name = await watchListDialog({title:'Rename watch list', message:'Enter a new name for this list.', value:list.name, confirmLabel:'Save name'}); if (!name?.trim()) return;
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name})}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function deleteWatchList() {
            const list = selectedWatchList(); if (!list || !await watchListDialog({title:'Delete watch list?', message:`“${list.name}” and all ${list.items.length} ${list.items.length === 1 ? 'item' : 'items'} in it will be deleted.`, type:'confirm', confirmLabel:'Delete list', danger:true})) return;
            try { await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}`, {method:'DELETE'}); await loadWatchLists(); }
            catch (error) { watchListMessage(error.message, true); }
        }
        async function addWatchListItem(item = null) {
            const list = selectedWatchList(); if (!list) { watchListMessage('Create or select a list first.', true); return; }
            const title = item?.title || document.getElementById('watch-item-title').value.trim();
            const type = item?.media_type || document.getElementById('watch-item-type').value;
            const yearValue = item?.year || document.getElementById('watch-item-year').value;
            if (!title) { watchListMessage('Enter a title first.', true); return; }
            const tags = (document.getElementById('watch-item-tags').value || '').split(',').map(tag => tag.trim()).filter(Boolean);
            const notes = document.getElementById('watch-item-notes').value.trim();
            const payload = item ? {...item, tags, notes} : {title, media_type:type, year:yearValue ? Number(yearValue) : null, tags, notes};
            try {
                const added = await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
                for (const id of ['watch-item-title', 'watch-item-year', 'watch-item-tags', 'watch-item-notes']) document.getElementById(id).value = '';
                await loadWatchLists();
                const automation = added.automation;
                watchListMessage(automation ? `Added ${title}. ${automation.status}: ${automation.reason}.` : `Added ${title}.`, automation?.status === 'failed');
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function searchAnimeForWatchList() {
            const title = document.getElementById('watch-item-title').value.trim();
            if (!title) { watchListMessage('Enter an anime title to search.', true); return; }
            const year = document.getElementById('watch-item-year').value;
            try {
                const data = await watchListRequest(`/api/watch-lists/anime-search?title=${encodeURIComponent(title)}${year ? `&year=${encodeURIComponent(year)}` : ''}`);
                if (!data.match) { watchListMessage('AniList did not find a match. You can add the title manually.', true); return; }
                if (!await watchListDialog({title:'Add AniList match?', message:`Add “${data.match.title}${data.match.year ? ` (${data.match.year})` : ''}” to this list?`, type:'confirm', confirmLabel:'Add item'})) return;
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
            if (!list) { updateWatchListControls(); box.innerHTML = '<p class="u-color-text-muted">No watch lists yet. Select New list to create one.</p>'; return; }
            updateWatchListControls();
            if (!list.items.length) { box.innerHTML = `<div class="watch-list-empty"><p class="u-color-text-muted">This list is empty.</p>${list.can_edit ? '<button type="button" class="btn-sm" onclick="document.getElementById(\'watch-item-title\').focus()">Add your first movie, show, or anime</button>' : ''}</div>`; return; }
            const readOnly = isDemo || !list.can_edit ? 'disabled' : '';
            box.innerHTML = list.items.map((item, index) => {
                const year = item.year ? ` (${item.year})` : '';
                const availability = item.availability;
                const availabilityLabel = availability ? `${availability.status.replaceAll('_', ' ')} · checked ${new Date(availability.checked_at).toLocaleString()}` : 'not checked yet';
                const serviceAvailability = availability?.services ? Object.entries(availability.services).map(([service, state]) => `${service}: ${String(state).replaceAll('_', ' ')}`).join(' · ') : '';
                const metadata = [...(item.tags || []).map(tag => `<span class="watch-list-tag">${escapeHtml(tag)}</span>`), item.notes ? `<div class="u-color-text-muted u-font-size-11px">Note: ${escapeHtml(item.notes)}</div>` : ''].join('');
                const autoResult = item.automation ? `<div class="u-color-text-muted u-font-size-11px">On add: ${escapeHtml(item.automation.status)} · ${new Date(item.automation.checked_at).toLocaleString()} · ${escapeHtml(item.automation.reason)}</div>` : '';
                const cw = item.media_type !== 'movie' && watchListCowatchShows.some(show => show.toLowerCase() === item.title.toLowerCase());
                const arrType = item.media_type === 'movie' ? 'movie' : 'series';
                return `<article class="u-display-flex u-justify-content-space-between u-align-items-center u-flex-wrap-wrap u-gap-8px u-background-bg-page u-border-1px-solid-var-border-color u-border-radius-8px u-padding-10px-12px">
                    <div><strong>${escapeHtml(item.title)}${escapeHtml(year)}</strong><div class="u-color-text-muted u-font-size-11px">${escapeHtml(item.media_type.toUpperCase())}${item.ids?.anilist ? ` · AniList #${escapeHtml(item.ids.anilist)}` : ''}</div>${metadata ? `<div class="u-display-flex u-flex-wrap-wrap u-gap-6px u-margin-top-4px">${metadata}</div>` : ''}<div class="u-color-text-muted u-font-size-11px">Availability: ${escapeHtml(availabilityLabel)}${serviceAvailability ? ` · ${escapeHtml(serviceAvailability)}` : ''}</div>${autoResult}</div>
                    <div class="u-display-flex u-flex-wrap-wrap u-gap-6px">
                        <button data-watch-list-edit="order" class="btn-sm" aria-label="Move ${escapeHtml(item.title)} up" onclick="moveWatchListItem(${index},-1)" ${readOnly || (index === 0 ? 'disabled' : '')}>↑</button><button data-watch-list-edit="order" class="btn-sm" aria-label="Move ${escapeHtml(item.title)} down" onclick="moveWatchListItem(${index},1)" ${readOnly || (index === list.items.length - 1 ? 'disabled' : '')}>↓</button>
                        <button data-watch-list-edit="metadata" class="btn-sm" onclick="editWatchListItem(${index})" ${readOnly}>Tags / note</button>
                        <button data-watch-list-admin-action class="btn-sm" onclick="sendWatchItemToArr(${index})" ${isAdmin && !isDemo ? '' : 'disabled'}>Add to ${arrType === 'movie' ? 'Radarr' : 'Sonarr'}</button>
                        ${watchListsCanRequest && item.ids?.tmdb ? `<button data-watch-list-admin-action class="btn-sm" onclick="requestWatchListItem(${index})">Request on ${escapeHtml(watchListRequestService)}</button>` : ''}
                        ${item.media_type !== 'movie' ? `<button data-watch-list-admin-action class="btn-sm" onclick="toggleWatchItemCowatch(${index})" ${isAdmin && !isDemo && watchListCowatchAvailable ? '' : 'disabled title="Admin access and a Co-Watch partner account are required"'}>${cw ? 'Remove Co-Watch' : 'Add Co-Watch'}</button>` : ''}
                        <button data-watch-list-edit="remove" class="btn-sm" onclick="removeWatchListItem(${index})" aria-label="Remove ${escapeHtml(item.title)}" ${readOnly}>Remove</button>
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
        async function editWatchListItem(index) {
            const list = selectedWatchList(), item = list?.items[index]; if (!item || !list.can_edit) return;
            const tagsText = await watchListDialog({title:'Edit item tags', message:'Enter up to 20 comma-separated tags.', value:(item.tags || []).join(', '), confirmLabel:'Continue'});
            if (tagsText === null) return;
            const notes = await watchListDialog({title:'Edit item note', message:'Add an optional note (up to 1,000 characters).', value:item.notes || '', type:'textarea', confirmLabel:'Save item'});
            if (notes === null) return;
            const tags = tagsText.split(',').map(tag => tag.trim()).filter(Boolean);
            try {
                await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tags, notes})});
                await loadWatchLists(); watchListMessage(`Updated ${item.title}.`);
            } catch (error) { watchListMessage(error.message, true); }
        }
        async function requestWatchListItem(index) {
            const list = selectedWatchList(), item = list?.items[index]; if (!item || !watchListsCanRequest || !item.ids?.tmdb) return;
            const accepted = await watchListDialog({title:'Submit media request?', message:`Send “${item.title}${item.year ? ` (${item.year})` : ''}” to ${watchListRequestService}? The service may approve it automatically or place it in its approval queue.`, type:'confirm', confirmLabel:'Submit request'});
            if (!accepted) return;
            try {
                const result = await watchListRequest(`/api/watch-lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}/request`, {method:'POST'});
                watchListMessage(result.status === 'skipped' ? result.reason : `Request submitted to ${result.service}.`);
                await refreshWatchListAvailability();
            } catch (error) { watchListMessage(error.message, true); }
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
                const choice = await watchListDialog({title:'Import watch lists', message:`Preview: ${summary} Choose how to apply the import.`, type:'select', options:[{value:'merge',label:'Merge with saved lists'},{value:'replace',label:'Replace all saved lists'}], confirmLabel:'Continue'});
                if (!choice) return;
                const replace = choice === 'replace';
                if (replace && !await watchListDialog({title:'Replace all watch lists?', message:'Every saved list will be replaced with the imported data. This cannot be undone.', type:'confirm', confirmLabel:'Replace lists', danger:true})) return;
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
            const text = await watchListDialog({title:'Paste watch-list data', message:'Paste JSON or exported watch-list text below.', type:'textarea', confirmLabel:'Preview import'});
            if (text?.trim()) await importWatchListData(text);
        }
        document.addEventListener('DOMContentLoaded', loadWatchLists);
