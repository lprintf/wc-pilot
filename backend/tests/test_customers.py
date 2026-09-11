import test_admin
import httpx
import unittest


class CustomerManagementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_admin.AdminEndpointTests.asyncSetUp
    asyncTearDown = test_admin.AdminEndpointTests.asyncTearDown
    send_text = test_admin.AdminEndpointTests.send_text
    headers = test_admin.AdminEndpointTests.headers

    async def test_edit_persists_across_profile_sync_and_search(self) -> None:
        headers = {**self.headers(), "x-requested-with": "XMLHttpRequest"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://example.test") as client:
            response = await client.patch(f"/api/admin/users/{self.user_id}", headers=headers,
                json={"nickname": "客户备注名", "gender": 2, "notes": "待跟进", "tags": ["重点", "重点"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["user"]["tags"], ["重点"])
            self.store.update_customer_profile(user_id=self.user_id, external_userid="external-secret", nickname="微信新昵称", avatar_url="", gender=1, unionid=None, scene="", scene_param="")
            detail = await client.get(f"/api/admin/users/{self.user_id}", headers=headers)
            users = await client.get("/api/admin/users?q=客户备注名", headers=headers)
            self.assertEqual(detail.json()["user"]["nickname"], "客户备注名")
            self.assertEqual(detail.json()["user"]["notes"], "待跟进")
            self.assertEqual(detail.json()["user"]["gender"], 2)
            self.assertEqual(users.json()["total"], 1)
            self.assertNotIn("external-secret", detail.text)

    async def test_identity_and_mutations_require_auth_scope_and_csrf(self) -> None:
        other = self.store.get_or_create_customer("other-account", "other-customer", seen_at=100)
        body = {"nickname": "测试", "gender": 0, "notes": "", "tags": []}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://example.test") as client:
            for method, suffix, payload in (("GET", "/identity", None), ("PATCH", "", body), ("DELETE", "", {"confirm_user_id": other})):
                denied = await client.request(method, f"/api/admin/users/{other}{suffix}", json=payload)
                self.assertEqual(denied.status_code, 401)
                scoped = await client.request(method, f"/api/admin/users/{other}{suffix}", headers={**self.headers(), "x-requested-with": "XMLHttpRequest"}, json=payload)
                self.assertEqual(scoped.status_code, 404)
            for method, payload in (("PATCH", body), ("DELETE", {"confirm_user_id": self.user_id})):
                denied = await client.request(method, f"/api/admin/users/{self.user_id}", headers=self.headers(), json=payload)
                self.assertEqual(denied.status_code, 403)
            identity = await client.get(f"/api/admin/users/{self.user_id}/identity", headers=self.headers())
            self.assertEqual(identity.json()["external_userid"], "external-secret")
            self.assertEqual(identity.headers["cache-control"], "no-store")

    async def test_delete_confirms_and_preserves_quota_and_deduplication(self) -> None:
        headers = {**self.headers(), "x-requested-with": "XMLHttpRequest"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://example.test") as client:
            denied = await client.request("DELETE", f"/api/admin/users/{self.user_id}", headers=headers, json={"confirm_user_id": -1})
            self.assertEqual(denied.status_code, 400)
            deleted = await client.request("DELETE", f"/api/admin/users/{self.user_id}", headers=headers, json={"confirm_user_id": self.user_id})
            self.assertEqual(deleted.status_code, 204)
            self.assertEqual((await client.get(f"/api/admin/users/{self.user_id}", headers=headers)).status_code, 404)
        self.assertTrue(self.store.is_processed("in-1"))
        self.assertEqual(self.store.reply_budget("wk-account", "external-secret").used, 1)
        self.assertEqual(self.store.history(self.user_id), [])
        replacement = self.store.get_or_create_customer("wk-account", "external-secret", seen_at=125)
        self.assertNotEqual(replacement, self.user_id)
        self.assertEqual(self.store.history(replacement), [])

    async def test_pending_message_prevents_deletion(self) -> None:
        self.store.create_admin_message(user_id=self.user_id, open_kfid="wk-account", content="pending", operator_id="agent", request_id="pending")
        with self.assertRaisesRegex(ValueError, "正在发送"):
            self.store.delete_managed_customer(self.user_id, "wk-account")
        self.assertIsNotNone(self.store.get_admin_user(self.user_id, "wk-account"))

    async def test_delete_cascades_sessions_and_preserves_other_customers(self) -> None:
        other = self.store.get_or_create_customer("wk-account", "other", seen_at=100)
        with self.store._connection:
            self.store._connection.execute("INSERT INTO web_session(session_hash,user_id,expires_at,created_at,last_seen_at) VALUES ('session',?,999,100,100)", (self.user_id,))
            self.store._connection.execute("INSERT INTO login_ticket(token_hash,user_id,expires_at,created_at) VALUES ('ticket',?,999,100)", (self.user_id,))
        self.store.update_managed_customer(self.user_id, "wk-account", nickname="name", gender=0, notes="private", tags=["tag"], operator_id="agent")
        self.store.delete_managed_customer(self.user_id, "wk-account")
        for table in ("web_session", "login_ticket", "customer_management", "conversation_message", "customer_profile", "user_identity"):
            self.assertEqual(self.store._connection.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (self.user_id,)).fetchone()[0], 0)
        self.assertEqual(self.store._connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIsNotNone(self.store.get_admin_user(other, "wk-account"))

    async def test_delete_rejects_customer_with_other_account_identity(self) -> None:
        with self.store._connection:
            self.store._connection.execute("INSERT INTO user_identity(user_id,provider,subject_id,external_id,created_at) VALUES (?,'wecom_kf','other','identity',100)", (self.user_id,))
        with self.assertRaisesRegex(ValueError, "其他账号"):
            self.store.delete_managed_customer(self.user_id, "wk-account")

    async def test_invalid_edit_payload_does_not_change_customer(self) -> None:
        body = {"nickname": "测试", "gender": 0, "notes": "", "tags": []}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://example.test") as client:
            for patch in ({"nickname": "x" * 101}, {"gender": True}, {"notes": "x" * 2001}, {"tags": ["x" * 21]}, {"tags": [1]}):
                response = await client.patch(f"/api/admin/users/{self.user_id}", headers={**self.headers(), "x-requested-with": "XMLHttpRequest"}, json={**body, **patch})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.get_admin_user(self.user_id, "wk-account").nickname, "张三")
