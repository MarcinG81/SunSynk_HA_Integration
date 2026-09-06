"""Tests for SunsynkAuth (custom_components/sunsynk/api/auth.py)."""
from __future__ import annotations

import time

import aiohttp
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from custom_components.sunsynk.api.auth import SunsynkAuth, SunsynkAuthError, _get_nonce
from tests.conftest import FakeResponse, fake_session


def _public_key_body() -> tuple[rsa.RSAPrivateKey, str]:
    """Generate a throwaway RSA keypair and return (private_key, bare PEM body).

    "Bare" meaning without the -----BEGIN/END----- markers — that's the
    format the Sunsynk API returns and _encrypt_password re-wraps.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode()
    lines = pem.strip().splitlines()
    body = "\n".join(lines[1:-1])  # drop BEGIN/END PUBLIC KEY lines
    return private_key, body


def test_get_nonce_is_current_time_in_milliseconds():
    before = int(time.time() * 1000)
    nonce = _get_nonce()
    after = int(time.time() * 1000)
    assert before <= nonce <= after


class TestSource:
    def test_uses_elinter_source_for_inteless_server(self):
        auth = SunsynkAuth("pv.inteless.com", "user", "pass")
        assert auth._source == "elinter"

    def test_uses_sunsynk_source_for_sunsynk_server(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        assert auth._source == "sunsynk"


class TestTokenValidity:
    def test_token_invalid_when_never_authenticated(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        assert auth._is_token_valid() is False
        assert auth.token == ""

    def test_token_valid_when_not_yet_expired(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        auth._token = "abc"
        auth._token_expires_at = time.time() + 3600
        assert auth._is_token_valid() is True

    def test_token_invalid_once_past_expiry(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        auth._token = "abc"
        auth._token_expires_at = time.time() - 1
        assert auth._is_token_valid() is False


class TestEncryptPassword:
    def test_encrypted_password_decrypts_back_to_original(self):
        from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15

        private_key, public_body = _public_key_body()
        auth = SunsynkAuth("api.sunsynk.net", "user", "my-secret-password")

        encrypted_b64 = auth._encrypt_password(public_body)

        import base64
        decrypted = private_key.decrypt(base64.b64decode(encrypted_b64), PKCS1v15())
        assert decrypted.decode() == "my-secret-password"


class TestGetPublicKey:
    @pytest.mark.asyncio
    async def test_returns_data_field_on_success(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        session = fake_session(
            get=FakeResponse({"msg": "Success", "data": "the-public-key-body"})
        )
        result = await auth._async_get_public_key(session)
        assert result == "the-public-key-body"

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_connection_error(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        session = fake_session()
        session.get.side_effect = aiohttp.ClientConnectionError("boom")
        result = await auth._async_get_public_key(session)
        assert result == ""


class TestAsyncGetToken:
    @pytest.mark.asyncio
    async def test_returns_cached_token_without_hitting_network(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        auth._token = "cached-token"
        auth._token_expires_at = time.time() + 3600
        session = fake_session()  # no get/post configured — would fail if called

        result = await auth.async_get_token(session)

        assert result == "cached-token"
        session.get.assert_not_called()
        session.post.assert_not_called()


class TestAsyncAuthenticate:
    @pytest.mark.asyncio
    async def test_full_flow_sets_token_and_expiry(self):
        _private_key, public_body = _public_key_body()
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")

        session = fake_session(
            get=FakeResponse({"msg": "Success", "data": public_body}),
            post=FakeResponse({
                "msg": "Success",
                "data": {"access_token": "new-token", "expires_in": 3600},
            }),
        )

        before = time.time()
        result = await auth._async_authenticate(session)
        after = time.time()

        assert result == "new-token"
        assert auth.token == "new-token"
        # expires_at = now + expires_in - margin(300), give a wide tolerance
        # for wall-clock time spent in the test itself.
        assert before + 3600 - 300 <= auth._token_expires_at <= after + 3600 - 300

    @pytest.mark.asyncio
    async def test_raises_when_public_key_fetch_fails(self):
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        session = fake_session()
        session.get.side_effect = aiohttp.ClientConnectionError("boom")

        with pytest.raises(SunsynkAuthError, match="public key"):
            await auth._async_authenticate(session)

    @pytest.mark.asyncio
    async def test_raises_when_login_rejected(self):
        _, public_body = _public_key_body()
        auth = SunsynkAuth("api.sunsynk.net", "user", "wrong-pass")

        session = fake_session(
            get=FakeResponse({"msg": "Success", "data": public_body}),
            post=FakeResponse({"msg": "invalid username or password"}),
        )

        with pytest.raises(SunsynkAuthError, match="invalid username or password"):
            await auth._async_authenticate(session)

    @pytest.mark.asyncio
    async def test_raises_on_post_connection_error(self):
        _, public_body = _public_key_body()
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")

        session = fake_session(get=FakeResponse({"msg": "Success", "data": public_body}))
        session.post.side_effect = aiohttp.ClientConnectionError("boom")

        with pytest.raises(SunsynkAuthError, match="Connection error"):
            await auth._async_authenticate(session)


class TestAsyncGetTokenTriggersAuthentication:
    @pytest.mark.asyncio
    async def test_authenticates_when_no_cached_token(self):
        _private_key, public_body = _public_key_body()
        auth = SunsynkAuth("api.sunsynk.net", "user", "pass")
        session = fake_session(
            get=FakeResponse({"msg": "Success", "data": public_body}),
            post=FakeResponse({
                "msg": "Success",
                "data": {"access_token": "fresh-token", "expires_in": 3600},
            }),
        )

        result = await auth.async_get_token(session)

        assert result == "fresh-token"
        session.get.assert_called_once()
        session.post.assert_called_once()
