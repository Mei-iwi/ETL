window.addEventListener('DOMContentLoaded', () => {
  window.ui = SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    validatorUrl: null,
    queryConfigEnabled: false,
    persistAuthorization: false,
    requestInterceptor: async request => {
      request.credentials = 'same-origin'
      if (!['GET', 'HEAD', 'OPTIONS'].includes((request.method || 'GET').toUpperCase()) && new URL(request.url, location.href).origin === location.origin) {
        const configResponse = await fetch('/api/v1/auth/config', { cache: 'no-store' })
        if (!configResponse.ok) throw new Error('Authentication service unavailable')
        const config = await configResponse.json()
        if (config.mode !== 'legacy_local') {
          const response = await fetch('/api/v1/auth/csrf', { cache: 'no-store', credentials: 'same-origin' })
          if (!response.ok) throw new Error('CSRF verification unavailable')
          request.headers['X-CSRF-Token'] = (await response.json()).csrf_token
        }
      }
      return request
    },
  })
})
