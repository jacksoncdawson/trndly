"""URL helpers for retailer scrapers (listing → PDP dedupe)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlparse


def _product_url_dedup_key(url: str) -> str:
    """
    Stable key so the same PDP is not scraped twice when the listing shows
    multiple tiles that only differ by color/size query params.

    - Default: host + path (query and fragment ignored).
    - gap.com: if ``pid`` (or ``pcid``) is present, key includes it so different
      products under generic paths stay distinct.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    p = urlparse(raw)
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/").lower()
    base = f"{host}{path}"

    if "gap.com" in host:
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        pid = q.get("pid") or q.get("pcid")
        if pid:
            return f"{base}?pid={str(pid).lower()}"
    return base


def dedupe_product_urls_preserve_order(urls: list[str] | None) -> list[str]:
    """First-seen order; skip URLs whose dedup key was already visited."""
    if not urls:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        s = (u or "").strip()
        if not s:
            continue
        k = _product_url_dedup_key(s)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
