        function closeAccountModal() {
            document.getElementById('account-modal').style.display = 'none';
        }

        async function accountRequest(url, method = 'GET', payload) {
            const options = {method};
            if (payload !== undefined) {
                options.headers = {'Content-Type': 'application/json'};
                options.body = JSON.stringify(payload);
            }
            const response = await fetch(url, options);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Account operation failed.');
            return data;
        }

        async function logoutDashboardAccount() {
            try {
                await accountRequest('/api/account/logout', 'POST');
                window.location.href = '/login';
            } catch (error) { alert(error.message); }
        }

        function accountInput(type, labelText, value = '') {
            const label = document.createElement('label');
            label.textContent = labelText;
            const input = document.createElement('input');
            input.type = type; input.value = value;
            input.style.cssText = 'display:block;max-width:100%;box-sizing:border-box;';
            if (type === 'password') { input.minLength = 12; input.autocomplete = 'new-password'; }
            label.appendChild(input);
            return {label, input};
        }

        function accountRole(value) {
            const label = document.createElement('label'); label.textContent = 'Role';
            const select = document.createElement('select');
            ['member', 'admin'].forEach(role => {
                const option = document.createElement('option'); option.value = role; option.textContent = role;
                select.appendChild(option);
            });
            select.value = value; label.appendChild(select);
            return {label, input: select};
        }

        function accountButton(text, action) {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'btn-sm'; button.textContent = text;
            button.addEventListener('click', async () => {
                if (button.disabled) return;
                button.disabled = true;
                const status = document.getElementById('account-modal-status');
                try { await action(); }
                catch (error) { status.textContent = error.message; }
                finally { button.disabled = false; }
            });
            return button;
        }

        async function openAccountModal() {
            const modal = document.getElementById('account-modal');
            const content = document.getElementById('account-modal-content');
            const status = document.getElementById('account-modal-status');
            modal.style.display = 'flex'; content.replaceChildren(); status.textContent = 'Loading account settings…';
            try {
                const me = await accountRequest('/api/account/me');
                document.getElementById('account-modal-title').textContent = me.role === 'admin' ? 'Household accounts' : 'My account';
                status.textContent = `Signed in as ${me.username} (${me.role}).`;
                if (me.source === 'local_account') {
                    const trackers = document.createElement('section');
                    const heading = document.createElement('h3'); heading.textContent = 'My tracker connections'; trackers.appendChild(heading);
                    for (const tracker of ['trakt', 'simkl', 'anilist', 'mal']) {
                        const row = document.createElement('div'); row.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0;';
                        const name = document.createElement('span'); name.textContent = `${tracker.toUpperCase()}: loading…`;
                        row.append(name,
                            accountButton('Connect / reconnect', () => openPartnerTrackerAuth(tracker, me.username)),
                            accountButton('Disconnect', async () => {
                                if (!confirm(`Disconnect ${tracker.toUpperCase()} for your profile?`)) return;
                                await accountRequest(`/api/${tracker}/disconnect?user=${encodeURIComponent(me.username)}`, 'POST');
                                await openAccountModal();
                            }));
                        trackers.appendChild(row);
                        accountRequest(`/api/${tracker}/status?user=${encodeURIComponent(me.username)}`)
                            .then(data => { name.textContent = `${tracker.toUpperCase()}: ${data.authenticated ? 'Connected' : 'Not connected'}`; })
                            .catch(error => { name.textContent = `${tracker.toUpperCase()}: ${error.message}`; });
                    }
                    content.appendChild(trackers);
                }
                if (me.role !== 'admin') return;
                const accounts = await accountRequest('/api/admin/accounts');
                const hint = document.createElement('p');
                hint.textContent = 'Use the media-server profile username for each member. Passwords must contain at least 12 characters. Changing a password, role, or account access revokes that account’s sessions.';
                content.appendChild(hint);
                for (const account of accounts.accounts) {
                    const form = document.createElement('form');
                    form.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:end;margin:12px 0;padding:12px;border:1px solid var(--border-color);';
                    const title = document.createElement('strong'); title.textContent = account.username;
                    const role = accountRole(account.role);
                    const password = accountInput('password', 'New password (optional)');
                    const enabledLabel = document.createElement('label'); enabledLabel.textContent = 'Enabled';
                    const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = account.enabled; enabledLabel.appendChild(enabled);
                    form.append(title, role.label, password.label, enabledLabel);
                    const save = accountButton('Save', async () => {
                        if (!form.reportValidity()) return;
                        const payload = {role: role.input.value, enabled: enabled.checked};
                        if (password.input.value) payload.password = password.input.value;
                        await accountRequest(`/api/admin/accounts/${encodeURIComponent(account.username)}`, 'PATCH', payload);
                        password.input.value = '';
                        if (me.source === 'local_account' && me.username === account.username) { window.location.href = '/login'; return; }
                        await openAccountModal();
                    });
                    form.append(save, accountButton('Delete', async () => {
                        if (!confirm(`Delete household account ${account.username}?`)) return;
                        await accountRequest(`/api/admin/accounts/${encodeURIComponent(account.username)}`, 'DELETE');
                        if (me.source === 'local_account' && me.username === account.username) { window.location.href = '/login'; return; }
                        await openAccountModal();
                    }));
                    form.addEventListener('submit', event => { event.preventDefault(); save.click(); });
                    content.appendChild(form);
                }
                const create = document.createElement('form');
                create.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:end;margin:16px 0;';
                const username = accountInput('text', 'New username'); username.input.required = true; username.input.pattern = '[A-Za-z0-9][A-Za-z0-9._-]{2,31}';
                const password = accountInput('password', 'Password'); password.input.required = true;
                const role = accountRole('member');
                const add = accountButton('Create account', async () => {
                    if (!create.reportValidity()) return;
                    await accountRequest('/api/admin/accounts', 'POST', {username: username.input.value, password: password.input.value, role: role.input.value});
                    password.input.value = '';
                    await openAccountModal();
                });
                create.append(username.label, password.label, role.label, add);
                create.addEventListener('submit', event => { event.preventDefault(); add.click(); });
                content.appendChild(create);
            } catch (error) { status.textContent = error.message; }
        }
