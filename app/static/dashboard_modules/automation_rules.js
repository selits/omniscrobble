        let automationRuleDrafts = [];

        function automationRuleList(value) {
            return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
        }

        function automationRuleSummary(rule) {
            const conditions = rule.conditions || {};
            const parts = [];
            for (const key of ['servers', 'libraries', 'media_types', 'devices', 'users']) {
                if (conditions[key]?.length) parts.push(`${key.replace('_', ' ')}: ${conditions[key].join(', ')}`);
            }
            if (conditions.time_window) {
                const window = conditions.time_window;
                parts.push(`${window.days.join(', ')} ${window.start}–${window.end} ${window.timezone}`);
            }
            const destinations = rule.action === 'route'
                ? ` → ${(rule.trackers || []).concat(rule.profiles || []).join(', ')}`
                : '';
            return `${rule.action} • priority ${rule.priority} • ${parts.join(' · ')}${destinations}`;
        }

        function renderAutomationRules() {
            const list = document.getElementById('automation-rules-list');
            if (!list) return;
            list.replaceChildren();
            if (!automationRuleDrafts.length) {
                const empty = document.createElement('p');
                empty.className = 'u-color-text-muted';
                empty.textContent = 'No event rules are configured. Events pass through by default.';
                list.appendChild(empty);
                return;
            }
            automationRuleDrafts.forEach((rule, index) => {
                const row = document.createElement('div');
                row.className = 'automation-rule-row';
                const description = document.createElement('div');
                const name = document.createElement('strong');
                name.textContent = `${rule.enabled ? '' : 'Disabled · '}${rule.name}`;
                const details = document.createElement('p');
                details.textContent = automationRuleSummary(rule);
                description.append(name, details);
                row.appendChild(description);
                if (isAdmin && !isDemo) {
                    const remove = document.createElement('button');
                    remove.type = 'button';
                    remove.className = 'btn-sm';
                    remove.textContent = 'Remove';
                    remove.setAttribute('aria-label', `Remove rule ${rule.name}`);
                    remove.addEventListener('click', () => {
                        automationRuleDrafts.splice(index, 1);
                        renderAutomationRules();
                    });
                    row.appendChild(remove);
                }
                list.appendChild(row);
            });
        }

        async function loadAutomationRules(force = false) {
            const admin = document.getElementById('automation-rules-admin');
            const readonly = document.getElementById('automation-rules-readonly');
            const status = document.getElementById('automation-rules-status');
            if (admin) admin.hidden = !isAdmin || isDemo;
            if (readonly) readonly.hidden = isAdmin && !isDemo;
            if (isDemo) {
                automationRuleDrafts = [];
                renderAutomationRules();
                if (readonly) readonly.textContent = 'Automation rules are disabled in demo mode.';
                return;
            }
            if (!isAdmin) {
                if (readonly) readonly.textContent = 'Automation rules can be managed by an administrator.';
                return;
            }
            if (!force && document.getElementById('automation-rules-card')?.dataset.loaded === 'true') return;
            if (status) status.textContent = 'Loading rules…';
            try {
                const response = await fetch('/api/automation/rules');
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to load automation rules.');
                automationRuleDrafts = data.rules || [];
                renderAutomationRules();
                if (status) status.textContent = 'No match means allow. Lower priority numbers run first; saved order breaks ties.';
                const card = document.getElementById('automation-rules-card');
                if (card) card.dataset.loaded = 'true';
                await loadAutomationReviewQueue();
            } catch (error) {
                if (status) status.textContent = error.message;
            }
        }

        function addAutomationRuleDraft() {
            const status = document.getElementById('automation-rules-status');
            const name = document.getElementById('automation-rule-name').value.trim();
            const conditions = {
                servers: automationRuleList(document.getElementById('automation-rule-servers').value),
                libraries: automationRuleList(document.getElementById('automation-rule-libraries').value),
                media_types: automationRuleList(document.getElementById('automation-rule-media-types').value),
                devices: automationRuleList(document.getElementById('automation-rule-devices').value),
                users: automationRuleList(document.getElementById('automation-rule-users').value),
            };
            const start = document.getElementById('automation-rule-window-start').value;
            const end = document.getElementById('automation-rule-window-end').value;
            const days = automationRuleList(document.getElementById('automation-rule-window-days').value);
            if (start || end || days.length) {
                conditions.time_window = {
                    start,
                    end,
                    days,
                    timezone: document.getElementById('automation-rule-window-timezone').value.trim() || 'UTC',
                };
            }
            const action = document.getElementById('automation-rule-action').value;
            const rule = {
                name,
                enabled: true,
                priority: Number(document.getElementById('automation-rule-priority').value || 100),
                conditions,
                action,
                trackers: action === 'route' ? automationRuleList(document.getElementById('automation-rule-trackers').value) : [],
                profiles: action === 'route' ? automationRuleList(document.getElementById('automation-rule-profiles').value) : [],
            };
            if (!name) {
                if (status) status.textContent = 'Enter a rule name first.';
                return;
            }
            if (!Object.values(conditions).some(value => Array.isArray(value) ? value.length : value)) {
                if (status) status.textContent = 'Add at least one condition. Match-all rules are blocked for safety.';
                return;
            }
            automationRuleDrafts.push(rule);
            renderAutomationRules();
            if (status) status.textContent = 'Rule added to the draft. Save rules to activate it.';
            document.getElementById('automation-rule-name').value = '';
        }

        async function saveAutomationRules() {
            const status = document.getElementById('automation-rules-status');
            if (!isAdmin || isDemo) return;
            try {
                const response = await fetch('/api/automation/rules', {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(automationRuleDrafts),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to save automation rules.');
                automationRuleDrafts = data.rules || [];
                renderAutomationRules();
                if (status) status.textContent = 'Rules saved. Suppress and review matches will be held before tracker dispatch.';
            } catch (error) {
                if (status) status.textContent = error.message;
            }
        }

        function automationSampleEvent() {
            return {
                server: document.getElementById('automation-sample-server').value.trim(),
                library: document.getElementById('automation-sample-library').value.trim(),
                media_type: document.getElementById('automation-sample-media-type').value,
                device: document.getElementById('automation-sample-device').value.trim(),
                user: document.getElementById('automation-sample-user').value.trim(),
            };
        }

        async function evaluateAutomationSample(previewDrafts) {
            const output = document.getElementById('automation-rules-preview-result');
            if (!output) return;
            if (!isAdmin || isDemo) {
                output.textContent = 'Sign in as an administrator to evaluate automation rules.';
                return;
            }
            try {
                const url = previewDrafts ? '/api/automation/rules/preview' : '/api/automation/rules/evaluate';
                const body = previewDrafts ? {rules: automationRuleDrafts, event: automationSampleEvent()} : {event: automationSampleEvent()};
                const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Evaluation failed.');
                output.textContent = JSON.stringify(data, null, 2);
            } catch (error) {
                output.textContent = error.message;
            }
        }

        async function loadAutomationReviewQueue() {
            const list = document.getElementById('automation-review-list');
            if (!list || !isAdmin || isDemo) return;
            list.replaceChildren(Object.assign(document.createElement('p'), {textContent: 'Loading held events…'}));
            try {
                const response = await fetch('/api/automation/review');
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to load the review queue.');
                list.replaceChildren();
                if (!data.pending?.length) {
                    list.appendChild(Object.assign(document.createElement('p'), {className: 'u-color-text-muted', textContent: 'No events are waiting for review.'}));
                    return;
                }
                data.pending.forEach((event) => {
                    const row = document.createElement('article');
                    row.className = 'automation-review-row';
                    const title = document.createElement('strong');
                    title.textContent = `${event.title || 'Untitled'} · ${event.user || 'unknown user'}`;
                    const reason = document.createElement('p');
                    reason.textContent = event.rule_evaluation?.reason || 'Held by an automation rule.';
                    const actions = document.createElement('div');
                    actions.className = 'u-display-flex u-flex-wrap-wrap u-gap-8px';
                    const approve = document.createElement('button');
                    approve.type = 'button'; approve.className = 'btn-sm'; approve.textContent = 'Approve & dispatch';
                    approve.addEventListener('click', () => decideAutomationReview(event.event_id, 'approve', actions));
                    const reject = document.createElement('button');
                    reject.type = 'button'; reject.className = 'btn-sm'; reject.textContent = 'Reject';
                    reject.addEventListener('click', () => decideAutomationReview(event.event_id, 'reject', actions));
                    actions.append(approve, reject);
                    row.append(title, reason, actions);
                    list.appendChild(row);
                });
            } catch (error) {
                list.replaceChildren(Object.assign(document.createElement('p'), {textContent: error.message}));
            }
        }

        async function decideAutomationReview(eventId, decision, actions) {
            const verb = decision === 'approve' ? 'dispatch this held event' : 'reject this held event';
            if (!confirm(`Are you sure you want to ${verb}?`)) return;
            const buttons = actions ? [...actions.querySelectorAll("button")] : [];
            buttons.forEach(button => { button.disabled = true; });
            try {
                const response = await fetch(`/api/automation/review/${encodeURIComponent(eventId)}`, {
                    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision}),
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Unable to resolve held event.');
                await loadAutomationReviewQueue();
                if (typeof fetchEvents === 'function') await fetchEvents();
            } catch (error) {
                const status = document.getElementById('automation-rules-status');
                if (status) status.textContent = error.message;
            } finally {
                buttons.forEach(button => { button.disabled = false; });
            }
        }
