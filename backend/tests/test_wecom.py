from __future__ import annotations

import json
import unittest

import httpx

from wechat_bot.wecom import WeComAPIError, WeComClient


class WeComCustomerTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_get_customers_sends_expected_request_and_parses_fields(
        self,
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/gettoken"):
                return httpx.Response(
                    200,
                    json={"errcode": 0, "access_token": "token", "expires_in": 7200},
                )
            self.assertTrue(request.url.path.endswith("/kf/customer/batchget"))
            body = json.loads(request.content)
            self.assertEqual(body["external_userid_list"], ["external-user"])
            self.assertEqual(body["need_enter_session_context"], 1)
            self.assertEqual(request.extensions["timeout"]["connect"], 5.0)
            self.assertEqual(request.extensions["timeout"]["read"], 10.0)
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "customer_list": [
                        {
                            "external_userid": "external-user",
                            "nickname": "昵称",
                            "avatar": "https://example.test/avatar.png",
                            "gender": 2,
                            "unionid": "union-id",
                            "enter_session_context": {
                                "scene": "scene",
                                "scene_param": "parameter",
                            },
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeComClient("corp", "secret", http_client=http)
            customers = await client.batch_get_customers(["external-user"])

        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0].nickname, "昵称")
        self.assertEqual(customers[0].avatar_url, "https://example.test/avatar.png")
        self.assertEqual(customers[0].unionid, "union-id")
        self.assertEqual(customers[0].scene_param, "parameter")

    async def test_batch_size_must_be_between_one_and_one_hundred(self) -> None:
        async def unexpected_request(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("validation should happen before any HTTP request")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(unexpected_request)
        ) as http:
            client = WeComClient("corp", "secret", http_client=http)
            with self.assertRaisesRegex(ValueError, "between 1 and 100"):
                await client.batch_get_customers([])
            with self.assertRaisesRegex(ValueError, "between 1 and 100"):
                await client.batch_get_customers(
                    [f"external-{index}" for index in range(101)]
                )

    async def test_transient_customer_request_is_retried_once(self) -> None:
        customer_requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal customer_requests
            if request.url.path.endswith("/gettoken"):
                return httpx.Response(
                    200,
                    json={"errcode": 0, "access_token": "token", "expires_in": 7200},
                )
            customer_requests += 1
            if customer_requests == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"errcode": 0, "customer_list": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeComClient("corp", "secret", http_client=http)
            customers = await client.batch_get_customers(["external-user"])

        self.assertEqual(customers, [])
        self.assertEqual(customer_requests, 2)

    async def test_permanent_api_error_is_not_retried(self) -> None:
        customer_requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal customer_requests
            if request.url.path.endswith("/gettoken"):
                return httpx.Response(
                    200,
                    json={"errcode": 0, "access_token": "token", "expires_in": 7200},
                )
            customer_requests += 1
            return httpx.Response(
                200, json={"errcode": 40096, "errmsg": "invalid external userid"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeComClient("corp", "secret", http_client=http)
            with self.assertRaises(WeComAPIError):
                await client.batch_get_customers(["external-user"])

        self.assertEqual(customer_requests, 1)
