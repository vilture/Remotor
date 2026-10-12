import base64
import re

_HASH = re.compile(r"xt=urn:btih:([0-9a-zA-Z]+)", re.IGNORECASE)


def is_magnet(text: str | None) -> bool:
    if text is None:
        return False
    cleaned = text.strip().lower()
    return cleaned.startswith("magnet:?") and "xt=urn:btih:" in cleaned


def magnet_hash(magnet: str) -> str | None:
    match = _HASH.search(magnet)
    if match is None:
        return None
    raw = match.group(1)
    if len(raw) == 40 and all(char in "0123456789abcdefABCDEF" for char in raw):
        return raw.lower()
    if len(raw) != 32:
        return None
    padding = "=" * ((8 - len(raw) % 8) % 8)
    try:
        decoded = base64.b32decode(raw.upper() + padding)
    except ValueError:
        return None
    if len(decoded) != 20:
        return None
    return decoded.hex()
