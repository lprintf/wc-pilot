"""WeCom callback signature validation and AES-CBC decryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class CallbackCryptoError(ValueError):
    """Raised when an encrypted callback cannot be trusted or decoded."""


def signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    joined = "".join(sorted((token, timestamp, nonce, encrypted)))
    return hashlib.sha1(joined.encode("utf-8"), usedforsecurity=False).hexdigest()


class WeComCallbackCrypto:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str) -> None:
        if not token:
            raise CallbackCryptoError("callback token is empty")
        if len(encoding_aes_key) != 43:
            raise CallbackCryptoError("EncodingAESKey must contain 43 characters")
        try:
            key = base64.b64decode(encoding_aes_key + "=", validate=True)
        except ValueError as exc:
            raise CallbackCryptoError("EncodingAESKey is not valid base64") from exc
        if len(key) != 32:
            raise CallbackCryptoError("EncodingAESKey must decode to 32 bytes")
        self._token = token
        self._key = key
        self._corp_id = corp_id

    def verify_url(
        self, msg_signature: str, timestamp: str, nonce: str, echo_str: str
    ) -> str:
        self._verify_signature(msg_signature, timestamp, nonce, echo_str)
        return self.decrypt(echo_str)

    def decrypt_callback(
        self, msg_signature: str, timestamp: str, nonce: str, encrypted_xml: str
    ) -> str:
        encrypted = _xml_text(encrypted_xml, "Encrypt")
        self._verify_signature(msg_signature, timestamp, nonce, encrypted)
        return self.decrypt(encrypted)

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
            decryptor = Cipher(
                algorithms.AES(self._key), modes.CBC(self._key[:16])
            ).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            payload = _unpad(padded)
            message_length = struct.unpack(">I", payload[16:20])[0]
            message_end = 20 + message_length
            message = payload[20:message_end]
            corp_id = payload[message_end:].decode("utf-8")
        except (ValueError, UnicodeDecodeError, struct.error) as exc:
            raise CallbackCryptoError("failed to decrypt callback") from exc
        if not hmac.compare_digest(corp_id, self._corp_id):
            raise CallbackCryptoError("callback CorpID does not match configuration")
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CallbackCryptoError("callback plaintext is not UTF-8") from exc

    def encrypt_for_test(
        self, plaintext: str, *, random_bytes: bytes | None = None
    ) -> str:
        random_bytes = random_bytes or os.urandom(16)
        if len(random_bytes) != 16:
            raise ValueError("random prefix must contain 16 bytes")
        message = plaintext.encode("utf-8")
        payload = random_bytes + struct.pack(">I", len(message)) + message
        payload += self._corp_id.encode("utf-8")
        encryptor = Cipher(
            algorithms.AES(self._key), modes.CBC(self._key[:16])
        ).encryptor()
        return base64.b64encode(
            encryptor.update(_pad(payload)) + encryptor.finalize()
        ).decode("ascii")

    def _verify_signature(
        self, provided: str, timestamp: str, nonce: str, encrypted: str
    ) -> None:
        expected = signature(self._token, timestamp, nonce, encrypted)
        if not hmac.compare_digest(expected, provided):
            raise CallbackCryptoError("callback signature is invalid")


def _xml_text(xml: str, tag: str) -> str:
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise CallbackCryptoError("DTD and entities are not allowed")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise CallbackCryptoError("callback XML is invalid") from exc
    value = root.findtext(tag, "").strip()
    if not value:
        raise CallbackCryptoError(f"callback XML has no {tag}")
    return value


def _pad(payload: bytes) -> bytes:
    amount = 32 - len(payload) % 32
    return payload + bytes((amount,)) * amount


def _unpad(payload: bytes) -> bytes:
    if not payload:
        raise CallbackCryptoError("decrypted callback is empty")
    amount = payload[-1]
    if amount < 1 or amount > 32 or payload[-amount:] != bytes((amount,)) * amount:
        raise CallbackCryptoError("callback padding is invalid")
    unpadded = payload[:-amount]
    if len(unpadded) < 20:
        raise CallbackCryptoError("callback payload is too short")
    return unpadded
