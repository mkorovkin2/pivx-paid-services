import base64
import hashlib
import hmac
import json
import secrets

from .errors import ServiceError
from .network import SAPLING_HRP

ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def polymod(values):
    generators = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for index, generator in enumerate(generators):
            if (top >> index) & 1:
                check ^= generator
    return check


def shield_address_bytes(address):
    """Validate the testnet Bech32 checksum and decode the 43-byte recipient.

    This checks encoding; curve validity remains the receiving wallet's job.
    """
    prefix = SAPLING_HRP + "1"
    if not isinstance(address, str) or address != address.lower() or not address.startswith(prefix) or len(address) != len(prefix) + 75:
        return None
    try:
        data = [ALPHABET.index(c) for c in address[len(prefix):]]
    except ValueError:
        return None
    expanded = [ord(c) >> 5 for c in SAPLING_HRP] + [0] + [ord(c) & 31 for c in SAPLING_HRP]
    if polymod(expanded + data) != 1 or data[-7] & 1:
        return None
    value = 0
    for part in data[:-6]:
        value = (value << 5) | part
    return (value >> 1).to_bytes(43, "big")


def shield_address_valid(address):
    return shield_address_bytes(address) is not None


def random_id():
    return base64.b32encode(secrets.token_bytes(10)).decode().rstrip("=")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def sign_token(key, claims):
    header = b64(b'{"alg":"HS256","typ":"JWT"}')
    payload = b64(canonical(claims).encode())
    message = f"{header}.{payload}"
    return message + "." + b64(hmac.digest(key, message.encode(), "sha256"))


def verify_token(key, token, now):
    try:
        header, payload, signature = token.split(".")
        message = f"{header}.{payload}"
        expected = b64(hmac.digest(key, message.encode(), "sha256"))
        if not hmac.compare_digest(expected, signature):
            raise ValueError("signature")
        if json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))) != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("algorithm")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if claims["aud"] != "pivx-paid-services" or not claims["nbf"] <= now < claims["exp"]:
            raise ValueError("scope or expiry")
        if not all(isinstance(claims[k], str) for k in ("sub", "iid", "service", "jti")):
            raise ValueError("claims")
        return claims
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ServiceError(401, "invalid_token", "Invalid or expired access token") from None
