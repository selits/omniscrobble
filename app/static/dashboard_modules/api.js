        function encodeURIComponentForInlineJs(value) {
            // encodeURIComponent leaves apostrophes unescaped, which can break out of
            // single-quoted inline handlers after HTML parsing.
            return encodeURIComponent(String(value ?? '')).replace(/'/g, '%27');
        }

        // Automatic CSRF Protection Interceptor for State-Mutating AJAX Calls
        (function() {
            const originalFetch = window.fetch;
            window.fetch = function(url, options) {
                options = options || {};
                const method = (options.method || 'GET').toUpperCase();
                if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
                    const match = document.cookie.match(/(^|;\s*)csrf_token=([^;]+)/);
                    const csrfToken = match ? decodeURIComponent(match[2]) : '';
                    if (csrfToken) {
                        options.headers = options.headers || {};
                        if (options.headers instanceof Headers) {
                            if (!options.headers.has('x-csrf-token')) {
                                options.headers.set('x-csrf-token', csrfToken);
                            }
                        } else if (Array.isArray(options.headers)) {
                            options.headers.push(['x-csrf-token', csrfToken]);
                        } else {
                            options.headers['x-csrf-token'] = csrfToken;
                        }
                    }
                }
                return originalFetch(url, options);
            };
        })();
