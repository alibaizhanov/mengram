"""The address a request really came from, for per-IP rate limits.

Production sits behind Cloudflare and Railway's proxy, and gunicorn runs
without --forwarded-allow-ips, so `request.client.host` is the proxy's address:
every visitor shared one bucket, and a handful of signups anywhere locked
signup out for everyone. Cloudflare puts the visitor's address in
CF-Connecting-IP (overwriting any value the client sent); X-Forwarded-For is
the fallback for requests that reach us through another proxy.
"""


def client_ip(request) -> str:
    """CF-Connecting-IP, else the first X-Forwarded-For entry, else the socket
    peer, else "unknown"."""
    headers = getattr(request, "headers", None) or {}
    cf = (headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    forwarded = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    return host or "unknown"
