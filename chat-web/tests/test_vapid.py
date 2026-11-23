"""VAPID setup checks. Pure functions: no app, no network."""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import vapid


@pytest.fixture(scope="module")
def pair():
    return vapid.generate_keypair()


def test_a_generated_pair_is_valid(pair):
    st = vapid.evaluate(pair[0], pair[1], "mailto:owner@gmail.com")
    assert st.configured and not st.missing and not st.problems
    assert st.public == pair[1]


def test_every_missing_variable_is_named(pair):
    assert vapid.evaluate("", "", "").missing == ["VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_CONTACT"]
    assert vapid.evaluate(pair[0], pair[1], "").missing == ["VAPID_CONTACT"]
    assert vapid.evaluate("", pair[1], "owner@gmail.com").missing == ["VAPID_PRIVATE_KEY"]


def test_public_key_must_belong_to_the_private_key(pair):
    other = vapid.generate_keypair()
    st = vapid.evaluate(pair[0], other[1], "owner@gmail.com")
    assert not st.configured and any(p.startswith("VAPID_PUBLIC_KEY") for p in st.problems)


@pytest.mark.parametrize("contact", ["mailto:admin@example.com", "nonsense", "http://insecure.org", "mailto:nobody"])
def test_bad_or_placeholder_contacts_are_rejected(pair, contact):
    st = vapid.evaluate(pair[0], pair[1], contact)
    assert not st.configured and any(p.startswith("VAPID_CONTACT") for p in st.problems)


@pytest.mark.parametrize("contact,expected", [("owner@gmail.com", "mailto:owner@gmail.com"),
                                              ("https://my-site.example.org", "https://my-site.example.org")])
def test_contact_is_normalised(contact, expected):
    assert vapid.normalize_contact(contact) == expected


def test_garbage_private_key_is_reported_not_raised(pair):
    st = vapid.evaluate("not a key!!", pair[1], "owner@gmail.com")
    assert not st.configured and st.problems[0].startswith("VAPID_PRIVATE_KEY")


def test_pem_private_key_is_accepted_and_converted(pair):
    key = ec.derive_private_key(int.from_bytes(vapid.b64url_decode(pair[0]), "big"), ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    st = vapid.evaluate(pem.replace("\n", "\\n"), pair[1], "owner@gmail.com")   # one-line .env style
    assert st.configured and st.private_raw == pair[0]


def test_non_p256_key_is_rejected():
    key = ec.generate_private_key(ec.SECP384R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    with pytest.raises(ValueError):
        vapid.normalize_private_key(pem)


def test_wrong_length_public_key_is_rejected(pair):
    assert not vapid.evaluate(pair[0], "AAAA", "owner@gmail.com").configured
