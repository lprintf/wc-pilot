from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from scripts import test_wechat_kf_api as script
from wechat_bot.store import MessageStore


class KfScriptTests(TestCase):
    def test_database_lists_old_customers_offline_and_filters_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "customers #1.db"
            store = MessageStore(path)
            store.get_or_create_customer("kf-a", "old-customer", seen_at=100)
            store.get_or_create_customer("kf-b", "another-customer", seen_at=200)
            store.close()
            before = path.read_bytes()
            out = StringIO()
            with patch("sys.argv", ["script", "--list-customers", "--database", str(path)]), patch.object(script, "load_env") as config, patch.object(script, "request_json") as request, redirect_stdout(out):
                self.assertEqual(script.main(), 0)
            config.assert_not_called()
            request.assert_not_called()
            self.assertIn("customers: 2", out.getvalue())
            self.assertIn("old-customer", out.getvalue())
            self.assertIn("another-customer", out.getvalue())
            out = StringIO()
            with redirect_stdout(out):
                script.print_database_customers(path, "kf-a")
            self.assertIn("customers: 1", out.getvalue())
            self.assertIn("old-customer", out.getvalue())
            self.assertNotIn("another-customer", out.getvalue())
            self.assertEqual(before, path.read_bytes())

    def test_missing_database_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with patch("sys.argv", ["script", "--list-customers", "--database", str(path)]), redirect_stderr(StringIO()):
                self.assertEqual(script.main(), 1)
            self.assertFalse(path.exists())

    def test_customer_listing_batches_deduplicates_and_keeps_missing_profiles(self) -> None:
        messages = [
            {"origin": 3, "external_userid": f"customer-{index}", "send_time": index + 1}
            for index in range(101)
        ] + [
            {"origin": 3, "external_userid": "customer-0", "send_time": 200},
            {"origin": 4, "external_userid": "agent", "send_time": 300},
        ]
        out = StringIO()
        with patch.object(script, "request_json", return_value={
            "errcode": 0, "customer_list": [{"external_userid": "customer-0", "nickname": "张三", "gender": 1}],
        }) as request, redirect_stdout(out):
            script.print_customers("token", messages)
        self.assertEqual([len(call.args[2]["external_userid_list"]) for call in request.call_args_list], [100, 1])
        rows = out.getvalue().splitlines()
        self.assertEqual(len(rows), 103)
        self.assertTrue(rows[2].startswith('customer-0\t"张三"\t男\t'))
        self.assertNotIn("agent", out.getvalue())

    def test_profile_failure_still_lists_customer_id_and_error_code(self) -> None:
        out, err = StringIO(), StringIO()
        with patch.object(script, "request_json", return_value={"errcode": 48002, "errmsg": "denied"}), redirect_stdout(out), redirect_stderr(err):
            script.print_customers("token", [{"origin": 3, "external_userid": "customer", "send_time": 123}])
        self.assertIn("customer\t", out.getvalue())
        self.assertIn("errcode=48002", err.getvalue())

    def run_main(self, args: list[str]):
        out, err = StringIO(), StringIO()
        with patch("sys.argv", ["script", *args]), patch.object(script, "load_env", return_value={
            "CorpID": "corp", "APP_AGENT_ID": "1", "APP_AGENT_SECRET": "secret",
        }), patch.object(script, "get_access_token", return_value="token"), patch.object(script, "list_accounts", return_value=[{"open_kfid": "kf"}]), patch.object(script, "sync_messages", return_value=([], "cursor", 1)) as sync, patch.object(script, "send_text", side_effect=script.ApiError("kf/send_msg", 95002, "expired")) as send, redirect_stdout(out), redirect_stderr(err):
            result = script.main()
        return result, out.getvalue(), err.getvalue(), sync, send

    def test_explicit_target_bypasses_sync_and_reports_real_error(self) -> None:
        result, _, err, sync, send = self.run_main(["--send-test", "--external-userid", "old-customer", "--content", "测试"])
        self.assertEqual(result, 1)
        sync.assert_not_called()
        send.assert_called_once_with("token", "kf", "old-customer", "测试")
        self.assertIn("errcode=95002", err)

    def test_list_customers_never_sends_without_send_test(self) -> None:
        result, out, _, sync, send = self.run_main(["--list-customers", "--external-userid", "customer"])
        self.assertEqual(result, 0)
        sync.assert_called_once()
        send.assert_not_called()
        self.assertIn("customers: 0", out)
