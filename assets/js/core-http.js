// Shared network utility for timeout-aware same-origin requests.
export function fetchWithTimeout(
                resource,
                options = {},
                timeoutMs = 5000,
                controller = null,
                transformResponse = null
            ) {
                const requestController = controller || (
                    typeof AbortController === 'function' ? new AbortController() : null
                );
                const requestOptions = { ...options };
                if (requestController) requestOptions.signal = requestController.signal;

                let timeout = 0;
                const timeoutPromise = new Promise((resolve, reject) => {
                    timeout = window.setTimeout(() => {
                        if (requestController) requestController.abort();
                        reject(new Error(`请求超时（${timeoutMs}ms）`));
                    }, timeoutMs);
                });

                const request = fetch(resource, requestOptions).then((response) => (
                    typeof transformResponse === 'function' ? transformResponse(response) : response
                ));
                return Promise.race([request, timeoutPromise])
                    .finally(() => window.clearTimeout(timeout));
            }
