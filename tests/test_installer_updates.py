import base64
import hashlib
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.request import Request

import pytest

from koddi.installer import service, updates


@pytest.fixture
def signing(monkeypatch):
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(updates, "PINNED_PUBLIC_KEY", public)
    monkeypatch.setattr(updates, "MANIFEST_URL", "https://releases.example/manifest.json")
    monkeypatch.setattr(updates, "_runtime_platform", lambda: "windows-x86_64")
    body = {
        "version": "1.2.3", "platform": "windows-x86_64",
        "url": "https://releases.example/Koddi.exe", "size": 7,
        "sha256": hashlib.sha256(b"release").hexdigest(),
        "expires": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def sign(value=None):
        raw = json.dumps(body if value is None else value, separators=(",", ":")).encode()
        return raw, private.sign(raw)

    return SimpleNamespace(private=private, body=body, sign=sign)


def verify(signed):
    return updates.verify_signed_manifest(*signed, current_version="1.0.0")


def test_valid_signature_and_artifact_only_verify_without_running(signing, tmp_path):
    manifest = verify(signing.sign())
    artifact = tmp_path / "Koddi.exe"
    artifact.write_bytes(b"release")
    assert updates.verify_artifact(artifact, manifest) is None
    assert manifest.url == signing.body["url"] and manifest.version == "1.2.3"
    assert service.update_check()["status"] == "disabled"


@pytest.mark.parametrize("change", [
    {"version": "1.0.0"}, {"version": "0.9.9"}, {"version": "01.2.3"},
    {"version": "1.2.3\ntoken=secret"}, {"version": "1.2.3.4"},
    {"platform": "windows-arm64"}, {"platform": "linux-x86_64"},
    {"url": "http://releases.example/Koddi.exe"},
    {"url": "https://other.example/Koddi.exe"},
    {"url": "https://releases.example:444/Koddi.exe"},
    {"url": "https://releases.example:0/Koddi.exe"},
    {"url": "https://user:secret@releases.example/Koddi.exe"},
    {"url": "https://releases.example/Koddi.exe?token=secret"},
    {"url": "https://releases.example/Koddi.exe#secret"},
    {"url": "https://releases.example\\@evil.example/Koddi.exe"},
    {"url": "https://releases.example/\nsecret"},
    {"size": 0}, {"size": -1}, {"size": True}, {"size": 7.0},
    {"size": updates.MAX_ARTIFACT_BYTES + 1},
    {"sha256": "a" * 63}, {"sha256": "A" * 64}, {"sha256": None},
    {"expires": "2020-01-01T00:00:00Z"}, {"expires": "2099-01-01T00:00:00Z"},
    {"expires": "2026-99-99T00:00:00Z"}, {"expires": None},
    {"public_key": "attacker-controlled-key"},
])
def test_signed_but_unsafe_manifest_is_rejected(signing, change):
    with pytest.raises(updates.UpdateVerificationError) as error:
        verify(signing.sign(signing.body | change))
    assert "secret" not in str(error.value)
    assert "attacker-controlled" not in str(error.value)


def test_wrong_key_bad_signature_and_changed_bytes_are_rejected(signing):
    raw, signature = signing.sign()
    for signed in ((raw + b" ", signature), (raw, b"\0" * 64), (raw, b"")):
        with pytest.raises(updates.UpdateVerificationError):
            verify(signed)
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    with pytest.raises(updates.UpdateVerificationError):
        verify((raw, ed25519.Ed25519PrivateKey.generate().sign(raw)))


def test_duplicate_fields_oversized_input_and_wrong_json_shape_fail_closed(signing):
    raw, _ = signing.sign()
    duplicate = b'{"version":"9.9.9",' + raw[1:]
    for candidate in (duplicate, b"[]", b"{" * 2000, b"x" * (updates.MAX_MANIFEST_BYTES + 1)):
        with pytest.raises(updates.UpdateVerificationError):
            verify((candidate, signing.private.sign(candidate)))


@pytest.mark.parametrize("attribute,value", [
    ("PINNED_PUBLIC_KEY", None), ("PINNED_PUBLIC_KEY", b"short"),
    ("Ed25519PublicKey", None), ("MANIFEST_URL", None),
    ("MANIFEST_URL", "http://releases.example/manifest.json"),
    ("_runtime_platform", lambda: "linux-x86_64"),
])
def test_missing_build_configuration_crypto_or_wrong_runtime_rejects(signing, monkeypatch, attribute, value):
    monkeypatch.setattr(updates, attribute, value)
    with pytest.raises(updates.UpdateVerificationError):
        verify(signing.sign())


@pytest.mark.parametrize("destination", [
    "https://other.example/Koddi.exe", "http://releases.example/Koddi.exe",
    "https://releases.example:444/Koddi.exe", "https://releases.example/Koddi.exe?token=secret",
])
def test_redirect_to_other_origin_or_unsafe_url_is_rejected(signing, destination):
    with pytest.raises(updates.UpdateVerificationError):
        updates.SameOriginRedirects().redirect_request(Request(updates.MANIFEST_URL), None, 302, "", {}, destination)


def test_same_origin_https_redirect_is_allowed(signing):
    request = updates.SameOriginRedirects().redirect_request(
        Request(updates.MANIFEST_URL), None, 302, "", {}, "https://releases.example/current.json"
    )
    assert request.full_url == "https://releases.example/current.json"


def test_fetch_checks_tls_redirect_policy_bounds_and_signature(signing, monkeypatch):
    raw, signature = signing.sign()
    envelope = json.dumps({"manifest": base64.b64encode(raw).decode(), "signature": base64.b64encode(signature).decode()}).encode()

    def read(limit):
        assert limit == updates.MAX_RESPONSE_BYTES + 1
        return envelope

    response = SimpleNamespace(geturl=lambda: updates.MANIFEST_URL, read=read)

    def open_request(request, timeout):
        assert request.full_url == updates.MANIFEST_URL and timeout == 10
        return nullcontext(response)

    def build_opener(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], updates.SameOriginRedirects)
        assert handlers[2]._context.check_hostname
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(updates.urllib.request, "build_opener", build_opener)
    assert updates.fetch_verified_manifest(current_version="1.0.0").version == "1.2.3"
    response.geturl = lambda: "https://other.example/manifest.json"
    with pytest.raises(updates.UpdateVerificationError):
        updates.fetch_verified_manifest(current_version="1.0.0")
    response.geturl = lambda: updates.MANIFEST_URL
    for invalid in (b"x" * (updates.MAX_RESPONSE_BYTES + 1), b'{}', b'{"manifest":null,"signature":42}'):
        response.read = lambda limit: invalid
        with pytest.raises(updates.UpdateVerificationError):
            updates.fetch_verified_manifest(current_version="1.0.0")


def test_default_disabled_channel_never_reads_runtime_key_or_makes_network_request(monkeypatch):
    monkeypatch.setenv("KODDI_UPDATE_PUBLIC_KEY", "not-a-build-key")
    monkeypatch.setenv("KODDI_UPDATE_MANIFEST_URL", "https://attacker.example/")
    monkeypatch.setattr(updates.urllib.request, "build_opener", lambda *args: pytest.fail("канал выключен"))
    assert updates.PINNED_PUBLIC_KEY is None and updates.MANIFEST_URL is None
    with pytest.raises(updates.UpdateVerificationError):
        updates.fetch_verified_manifest(current_version="1.0.0")


@pytest.mark.parametrize("content", [b"releasE", b"short", b"release-extra"])
def test_modified_truncated_or_oversized_artifact_is_rejected(signing, tmp_path, content):
    artifact = tmp_path / "Koddi.exe"
    artifact.write_bytes(content)
    with pytest.raises(updates.UpdateVerificationError):
        updates.verify_artifact(artifact, verify(signing.sign()))


def test_artifact_read_error_is_redacted(signing, tmp_path):
    with pytest.raises(updates.UpdateVerificationError) as error:
        updates.verify_artifact(tmp_path / "private-secret-user.exe", verify(signing.sign()))
    assert "private-secret-user" not in str(error.value)
