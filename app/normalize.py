"""Turn a messy URL into one canonical website address.

Two different-looking links can be the same company:

    https://www.Stripe.com/about
    http://stripe.com/

Both become:

    https://stripe.com

That canonical form is what we use to avoid saving the same company twice.
"""

from urllib.parse import urlparse


def normalize_website(raw: str) -> str | None:
    """Return https://domain, or None if this is not a usable website address."""
    text = raw.strip().strip('"').strip("'")
    if not text or text.startswith("#"):
        return None

    if "://" not in text:
        text = "https://" + text

    parsed = urlparse(text)
    host = parsed.hostname
    if not host or "." not in host:
        return None

    host = host.lower().removeprefix("www.")
    if not host or "." not in host:
        return None

    return f"https://{host}"
