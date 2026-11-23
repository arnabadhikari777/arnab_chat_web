def register_security_headers(app) -> None:
    from flask import request

    def build_csp() -> str:
        # The browser must be allowed to reach the calling server (fetch /health /ready /ice and wss:///ws).
        extra = ""
        url = (app.config.get("CALLING_SERVER_URL") or "").strip().rstrip("/")
        if url.startswith(("https://", "http://")):
            host = url.split("://", 1)[1].split("/", 1)[0]
            scheme = "wss" if url.startswith("https://") else "ws"
            extra = f" {url} {scheme}://{host}"
        return csp_template.replace("connect-src 'self'", "connect-src 'self'" + extra)

    csp_template = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' https://fonts.googleapis.com",
            "font-src https://fonts.gstatic.com",
            "img-src 'self' data: blob: https://res.cloudinary.com",
            "media-src 'self' blob: mediastream: https://res.cloudinary.com",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )

    @app.after_request
    def _headers(response):
        response.headers.setdefault("Content-Security-Policy", build_csp())
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(self), geolocation=(), microphone=(self)"
        )
        # Pages and API answers can hold private data: browsers and the service worker must not keep them.
        if request.endpoint != "static" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
            response.headers.add("Vary", "Cookie")
        if app.config.get("APP_ENV") == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
