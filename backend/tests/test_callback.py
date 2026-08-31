from __future__ import annotations

import base64
import unittest

from wechat_bot.callback import (
    CallbackParseError,
    parse_callback_event,
    parse_customer_service_event,
)
from wechat_bot.crypto import CallbackCryptoError, WeComCallbackCrypto, signature


TOKEN = "callback-token"
AES_KEY = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")
CORP_ID = "ww-corp-id"


class CallbackCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crypto = WeComCallbackCrypto(TOKEN, AES_KEY, CORP_ID)

    def test_verifies_and_decrypts_callback(self) -> None:
        plaintext = "<xml><ToUserName>ww-corp-id</ToUserName></xml>"
        encrypted = self.crypto.encrypt_for_test(plaintext, random_bytes=b"a" * 16)
        timestamp = "123"
        nonce = "nonce"
        signed = signature(TOKEN, timestamp, nonce, encrypted)
        wrapper = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

        self.assertEqual(
            self.crypto.decrypt_callback(signed, timestamp, nonce, wrapper), plaintext
        )
        self.assertEqual(
            self.crypto.verify_url(signed, timestamp, nonce, encrypted), plaintext
        )

    def test_rejects_invalid_signature(self) -> None:
        encrypted = self.crypto.encrypt_for_test("<xml/>", random_bytes=b"a" * 16)
        with self.assertRaisesRegex(CallbackCryptoError, "signature"):
            self.crypto.verify_url("invalid", "123", "nonce", encrypted)

    def test_rejects_wrong_corp_id(self) -> None:
        other = WeComCallbackCrypto(TOKEN, AES_KEY, "other-corp")
        encrypted = other.encrypt_for_test("<xml/>", random_bytes=b"a" * 16)
        signed = signature(TOKEN, "123", "nonce", encrypted)
        with self.assertRaisesRegex(CallbackCryptoError, "CorpID"):
            self.crypto.verify_url(signed, "123", "nonce", encrypted)


class CallbackParsingTests(unittest.TestCase):
    def test_parses_customer_service_event(self) -> None:
        event = parse_customer_service_event(
            """
            <xml>
              <ToUserName>ww-corp-id</ToUserName>
              <CreateTime>123</CreateTime>
              <Event>kf_msg_or_event</Event>
              <Token>temporary-token</Token>
              <OpenKfId>wk-account</OpenKfId>
            </xml>
            """,
            CORP_ID,
        )
        self.assertEqual(event.open_kfid, "wk-account")
        self.assertEqual(event.token, "temporary-token")

    def test_rejects_other_event(self) -> None:
        with self.assertRaisesRegex(CallbackParseError, "customer-service"):
            parse_customer_service_event(
                "<xml><ToUserName>ww-corp-id</ToUserName><Event>other</Event></xml>",
                CORP_ID,
            )

    def test_ignores_valid_non_customer_service_callback(self) -> None:
        self.assertIsNone(
            parse_callback_event(
                """
                <xml>
                  <ToUserName>ww-corp-id</ToUserName>
                  <MsgType>text</MsgType>
                  <Content>application message</Content>
                </xml>
                """,
                CORP_ID,
            )
        )
