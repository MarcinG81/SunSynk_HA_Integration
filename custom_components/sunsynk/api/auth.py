"""Sunsynk authentication - async version using aiohttp."""
from __future__ import annotations

import base64
import hashlib
import logging
import time

import aiohttp
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.serialization import load_pem_public_key

_LOGGER = logging.getLogger(__name__)

_TOKEN_MARGIN = 300  # refresh token 5 min before expiry


def _get_nonce() -> int:
    return int(time.time() * 1000)


class SunsynkAuth:
    """Handles Sunsynk API authentication and token management."""

    def __init__(self, api_server: str, username: str, password: str) -> None:
        self._api_server = api_server
        self._username = username
        self._password = password
        self._token: str = ""
        self._token_expires_at: float = 0.0
        self._source = "elinter" if api_server == "pv.inteless.com" else "sunsynk"

    @property
    def token(self) -> str:
        return self._token

    def _is_token_valid(self) -> bool:
        return bool(self._token) and time.time() < self._token_expires_at

    async def async_get_token(self, session: aiohttp.ClientSession) -> str:
        """Return cached token or authenticate to get a new one."""
        if self._is_token_valid():
            return self._token
        return await self._async_authenticate(session)

    async def _async_authenticate(self, session: aiohttp.ClientSession) -> str:
        """Perform full RSA + OAuth authentication."""
        public_key_string = await self._async_get_public_key(session)
        if not public_key_string:
            raise SunsynkAuthError("Failed to retrieve public key")

        encrypted_password = self._encrypt_password(public_key_string)

        token_nonce = _get_nonce()
        token_sign = hashlib.md5(
            f"nonce={token_nonce}&source=sunsynk{public_key_string[:10]}".encode()
        ).hexdigest()

        payload = {
            "client_id": "csp-web",
            "grant_type": "password",
            "password": encrypted_password,
            "source": self._source,
            "username": self._username,
            "nonce": token_nonce,
            "sign": token_sign,
        }

        url = f"https://{self._api_server}/oauth/token/new"
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("msg") == "Success":
                token_data = data["data"]
                self._token = token_data["access_token"]
                expires_in = int(token_data.get("expires_in", 3600))
                self._token_expires_at = time.time() + expires_in - _TOKEN_MARGIN
                _LOGGER.debug("Sunsynk authentication successful, token valid for %ds", expires_in)
                return self._token

            msg = data.get("msg", "Unknown error")
            _LOGGER.error("Sunsynk authentication failed: %s", msg)
            raise SunsynkAuthError(f"Login failed: {msg}")

        except aiohttp.ClientError as err:
            raise SunsynkAuthError(f"Connection error during authentication: {err}") from err

    async def _async_get_public_key(self, session: aiohttp.ClientSession) -> str:
        nonce = _get_nonce()
        sign = hashlib.md5(
            f"nonce={nonce}&source={self._source}POWER_VIEW".encode()
        ).hexdigest()

        url = f"https://{self._api_server}/anonymous/publicKey"
        try:
            async with session.get(
                url,
                params={"source": self._source, "nonce": nonce, "sign": sign},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get("data", "")
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to get public key: %s", err)
            return ""

    def _encrypt_password(self, public_key_string: str) -> str:
        pem = f"-----BEGIN PUBLIC KEY-----\n{public_key_string}\n-----END PUBLIC KEY-----\n"
        public_key = load_pem_public_key(pem.encode("utf-8"))
        encrypted = public_key.encrypt(self._password.encode("utf-8"), PKCS1v15())
        return base64.b64encode(encrypted).decode("utf-8")


class SunsynkAuthError(Exception):
    """Raised when authentication fails."""
