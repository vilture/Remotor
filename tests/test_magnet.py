import base64

from remotor.magnet import is_magnet, magnet_hash


def test_magnet_hash_hex_and_base32() -> None:
    digest = bytes.fromhex("ab" * 20)
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    assert magnet_hash(f"magnet:?xt=urn:btih:{digest.hex()}") == digest.hex()
    assert magnet_hash(f"magnet:?xt=urn:btih:{encoded}") == digest.hex()
    assert is_magnet(f"  magnet:?xt=urn:btih:{digest.hex()}&dn=a") is True
    assert is_magnet("https://example") is False
