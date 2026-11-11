"""VAPID key checks that need no Flask (so they are easy to test and to run from the console).

Web Push needs THREE server settings, and all three must be right:
  VAPID_PRIVATE_KEY  the secret key (never sent to the browser)
  VAPID_PUBLIC_KEY   the matching public key (the browser needs it to subscribe)
  VAPID_CONTACT      mailto:you@example.org or https://your-site (push services may require a real one)
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ENV_PRIVATE = "VAPID_PRIVATE_KEY"
ENV_PUBLIC = "VAPID_PUBLIC_KEY"
ENV_CONTACT = "VAPID_CONTACT"
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "localhost")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    text = (text or "").strip().replace("\n", "")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def normalize_private_key(value: str) -> str:
    """Accept PEM, base64url raw (32 bytes) or base64 DER. Return the raw key as base64url. Raises ValueError."""
    value = (value or "").strip().strip("'\"")
    if not value:
        raise ValueError("empty")
    try:
        if "BEGIN" in value:
            key = serialization.load_pem_private_key(value.replace("\\n", "\n").encode(), password=None)
        else:
            raw = b64url_decode(value)
            if len(raw) == 32:
                key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
            else:
                key = serialization.load_der_private_key(raw, password=None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("unreadable") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("wrong curve (need P-256)")
    return b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def public_from_private(raw_b64url: str) -> str:
    key = ec.derive_private_key(int.from_bytes(b64url_decode(raw_b64url), "big"), ec.SECP256R1())
    point = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64url(point)


def normalize_public_key(value: str) -> str:
    value = (value or "").strip().strip("'\"")
    try:
        raw = b64url_decode(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("unreadable") from exc
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("not a P-256 public key")
    return b64url(raw)


def normalize_contact(value: str) -> str | None:
    value = (value or "").strip().strip("'\"")
    if not value:
        return None
    if "@" in value and ":" not in value:
        value = "mailto:" + value
    if value.startswith("mailto:"):
        addr = value[7:]
        domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
        if not domain or "." not in domain or domain in _PLACEHOLDER_DOMAINS:
            return None
        return value
    if value.startswith("https://") and len(value) > 10:
        return value
    return None


@dataclass
class VapidStatus:
    configured: bool = False
    missing: list[str] = field(default_factory=list)      # env variables that are empty
    problems: list[str] = field(default_factory=list)     # "VAPID_X: why it is wrong"
    private_raw: str | None = None
    public: str | None = None
    contact: str | None = None

    @property
    def broken_vars(self) -> list[str]:
        return self.missing + [p.split(":")[0] for p in self.problems]


def evaluate(private: str, public: str, contact: str) -> VapidStatus:
    st = VapidStatus()
    if not (private or "").strip():
        st.missing.append(ENV_PRIVATE)
    if not (public or "").strip():
        st.missing.append(ENV_PUBLIC)
    if not (contact or "").strip():
        st.missing.append(ENV_CONTACT)

    if ENV_PRIVATE not in st.missing:
        try:
            st.private_raw = normalize_private_key(private)
        except ValueError as exc:
            st.problems.append(f"{ENV_PRIVATE}: {exc}")
    if ENV_PUBLIC not in st.missing:
        try:
            st.public = normalize_public_key(public)
        except ValueError as exc:
            st.problems.append(f"{ENV_PUBLIC}: {exc}")
    if ENV_CONTACT not in st.missing:
        st.contact = normalize_contact(contact)
        if st.contact is None:
            st.problems.append(f"{ENV_CONTACT}: must be mailto:real@address or https://your-site (not a placeholder)")
    if st.private_raw and st.public and public_from_private(st.private_raw) != st.public:
        st.problems.append(f"{ENV_PUBLIC}: does not belong to {ENV_PRIVATE} (generate ONE new pair and set both)")
    st.configured = not st.missing and not st.problems
    return st


def generate_keypair() -> tuple[str, str]:
    """(private_raw_b64url, public_b64url) - paste them into .env."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = b64url(key.private_numbers().private_value.to_bytes(32, "big"))
    return raw, public_from_private(raw)
