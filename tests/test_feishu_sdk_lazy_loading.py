"""Offline regressions for optional Feishu SDK loading (no real delivery)."""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.notification_sender import feishu_sender as fs


def config(**overrides):
    values = dict(feishu_app_id="test-app", feishu_app_secret="test-secret",
                  feishu_chat_id="test-chat", feishu_domain="feishu")
    values.update(overrides)
    return SimpleNamespace(**values)


class TestFeishuColdImport(unittest.TestCase):
    def test_email_and_webhook_paths_do_not_attempt_sdk_import(self):
        # A fresh interpreter catches attempted imports even when SDK is absent.
        code = '''
import importlib.abc, json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
attempts = []
class RejectSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "lark_oapi" or fullname.startswith("lark_oapi."):
            attempts.append(fullname)
            raise ImportError("SDK deliberately unavailable")
sys.meta_path.insert(0, RejectSDK())
from src.notification import NotificationService
from src.notification_sender import EmailSender, FeishuSender
email = EmailSender(SimpleNamespace(email_sender="sender@example.invalid",
    email_password="synthetic", email_receivers=["receiver@example.invalid"]))
with mock.patch("src.notification_sender.email_sender.smtplib.SMTP_SSL") as smtp:
    # Use a known local code branch, with all transport methods mocked.
    email._email_config["sender"] = "sender@qq.com"
    assert email.send_to_email("# synthetic")
    smtp.assert_called_once()
empty = FeishuSender(SimpleNamespace(feishu_chat_id="test-chat"))
assert not empty.send_to_feishu("synthetic")
missing_chat = FeishuSender(SimpleNamespace(feishu_app_id="test", feishu_app_secret="test"))
assert not missing_chat.send_to_feishu("synthetic")
sender = FeishuSender(SimpleNamespace(feishu_webhook_url="https://example.invalid/hook",
    feishu_app_id="test", feishu_app_secret="test", feishu_chat_id="test"))
with mock.patch("src.notification_sender.feishu_sender.requests.post") as post:
    post.return_value.status_code = 200
    post.return_value.json.return_value = {"code": 0}
    assert sender.send_to_feishu("synthetic")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "report.md"
        path.write_text("# synthetic", encoding="utf-8")
        assert sender.send_feishu_file(str(path))
print(json.dumps({"attempts": attempts}))
'''
        result = subprocess.run([sys.executable, "-c", code], text=True,
                                capture_output=True, timeout=60, check=True)
        self.assertEqual(json.loads(result.stdout.splitlines()[-1])["attempts"], [])

    def test_full_analysis_skips_unconfigured_cloud_doc_import(self):
        self._check_full_analysis_cloud_doc(configured=False)

    def test_full_analysis_preserves_configured_cloud_doc_creation(self):
        self._check_full_analysis_cloud_doc(configured=True)

    def _check_full_analysis_cloud_doc(self, *, configured):
        code = '''
import builtins, json, sys
from types import SimpleNamespace
from unittest import mock
from tests.test_main_schedule_mode import MainScheduleModeTestCase
import main
case = MainScheduleModeTestCase()
case.setUp()
try:
    configured = CONFIGURED
    cfg = case._make_config(trading_day_check_enabled=False, market_review_enabled=True,
        daily_market_context_enabled=False, single_stock_notify=False,
        merge_email_notification=False, analysis_delay=0,
        feishu_app_id="synthetic" if configured else "",
        feishu_app_secret="synthetic" if configured else "",
        feishu_folder_token="synthetic" if configured else "")
    args = case._make_args(no_notify=True)
    pipeline = mock.MagicMock()
    pipeline.run.return_value = []
    manager = mock.MagicMock()
    manager.is_configured.return_value = True
    manager.create_daily_doc.return_value = None
    fake_module = SimpleNamespace(FeishuDocManager=mock.Mock(return_value=manager))
    imported = []
    original_import = builtins.__import__
    def tracked_import(name, *args, **kwargs):
        if name == "src.feishu_doc":
            imported.append(name)
            return fake_module
        return original_import(name, *args, **kwargs)
    with mock.patch("builtins.__import__", side_effect=tracked_import), \\
         mock.patch("main._refresh_stock_index_cache_for_analysis"), \\
         mock.patch("main._run_auto_backtest"), \\
         mock.patch("main._compute_trading_day_filter", return_value=([], "cn", False)), \\
         mock.patch.dict(sys.modules, {
             "src.core.pipeline": SimpleNamespace(StockAnalysisPipeline=mock.Mock(return_value=pipeline)),
             "src.core.market_review": SimpleNamespace(run_market_review=mock.Mock(return_value="# synthetic")),
         }):
        assert main.run_full_analysis(cfg, args, []) is True
    assert bool(imported) == configured, imported
    assert manager.create_daily_doc.call_count == int(configured)
    print(json.dumps({"configured": configured, "cloud_imports": len(imported)}))
finally:
    case.tearDown()
'''.replace("CONFIGURED", repr(configured))
        result = subprocess.run([sys.executable, "-c", code], text=True,
                                capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestFeishuLazyLoader(unittest.TestCase):
    def setUp(self):
        # Restore globals after every test so no fake SDK leaks into other tests.
        self.state = mock.patch.multiple(
            fs, _SDK_LOAD_ATTEMPTED=False, FEISHU_SDK_AVAILABLE=False,
            FEISHU_FILE_SDK_AVAILABLE=False, _lark=None,
            CreateMessageRequest=None, CreateMessageRequestBody=None,
            _CreateFileRequest=None, _CreateFileRequestBody=None,
            FEISHU_DOMAIN="feishu", LARK_DOMAIN="lark",
        )
        self.state.start()
        self.addCleanup(self.state.stop)
        self.sdk = mock.MagicMock()
        self.im = mock.MagicMock()
        self.domains = SimpleNamespace(FEISHU_DOMAIN="https://feishu.invalid",
                                       LARK_DOMAIN="https://lark.invalid")
        self.modules = {"lark_oapi": self.sdk, "lark_oapi.api.im.v1": self.im,
                        "lark_oapi.core.const": self.domains}
        self.importer = mock.patch.object(fs.importlib, "import_module", side_effect=self.modules.__getitem__)
        self.load = self.importer.start()
        self.addCleanup(self.importer.stop)

    def test_missing_sdk_is_cached_and_fails_closed(self):
        self.load.side_effect = ImportError("synthetic missing package")
        with self.assertLogs(fs.__name__, level="WARNING") as logs:
            self.assertFalse(fs.FeishuSender(config()).send_to_feishu("synthetic"))
            self.assertFalse(fs.FeishuSender(config()).send_to_feishu("synthetic"))
        self.assertEqual(self.load.call_count, 1)
        self.assertFalse(fs.FEISHU_SDK_AVAILABLE)
        self.assertIsNone(fs._lark)
        self.assertTrue(all("pip install -r requirements.txt" in x for x in logs.output))

    def test_failed_core_import_does_not_publish_partial_state(self):
        def load(name):
            if name.endswith(".const"):
                raise ImportError("synthetic incomplete SDK")
            return self.modules[name]
        self.load.side_effect = load
        self.assertFalse(fs._load_feishu_sdk())
        self.assertIsNone(fs._lark)
        self.assertIsNone(fs.CreateMessageRequest)
        self.assertFalse(fs.FEISHU_SDK_AVAILABLE)
        self.assertFalse(fs.FEISHU_FILE_SDK_AVAILABLE)

    def test_unconfigured_app_does_not_attempt_import(self):
        sender = fs.FeishuSender(config(feishu_app_secret=""))
        self.assertFalse(sender.send_to_feishu("synthetic"))
        self.assertIsNone(sender._ensure_app_client())
        self.load.assert_not_called()

    def test_configuration_added_with_new_sender_loads_sdk(self):
        fs.FeishuSender(config(feishu_app_secret=""))._ensure_app_client()
        self.load.assert_not_called()
        self.assertIsNotNone(fs.FeishuSender(config())._ensure_app_client())
        self.assertEqual(self.load.call_count, 3)

    def test_domains_resolve_after_first_load_even_for_existing_senders(self):
        feishu = fs.FeishuSender(config())
        lark = fs.FeishuSender(config(feishu_domain="lark"))
        feishu._ensure_app_client()
        lark._ensure_app_client()
        calls = self.sdk.Client.builder.return_value.app_id.return_value.app_secret.return_value.domain.call_args_list
        self.assertEqual([c.args[0] for c in calls], [self.domains.FEISHU_DOMAIN, self.domains.LARK_DOMAIN])

    def test_concurrent_first_load_and_client_construction_are_once(self):
        sender = fs.FeishuSender(config())
        start = threading.Barrier(8)
        def get_client(_):
            start.wait(timeout=10)
            return sender._ensure_app_client()
        with ThreadPoolExecutor(max_workers=8) as pool:
            clients = list(pool.map(get_client, range(8)))
        self.assertTrue(all(c is clients[0] for c in clients))
        self.assertEqual(self.load.call_count, 3)
        self.sdk.Client.builder.assert_called_once()

    def test_concurrent_senders_share_one_sdk_load(self):
        senders = [fs.FeishuSender(config()) for _ in range(8)]
        start = threading.Barrier(8)
        def slow_import(name):
            # Release the GIL so a missing module lock would permit duplicate loads.
            time.sleep(0.02)
            return self.modules[name]
        self.load.side_effect = slow_import
        def get_client(sender):
            start.wait(timeout=10)
            return sender._ensure_app_client()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(get_client, senders))
        self.assertEqual(self.load.call_count, 3)
        self.assertEqual(self.sdk.Client.builder.call_count, 8)

    def test_old_sdk_without_file_support_still_sends_text(self):
        del self.im.CreateFileRequest
        del self.im.CreateFileRequestBody
        sender = fs.FeishuSender(config())
        client = sender._ensure_app_client()
        client.im.v1.message.create.return_value.success.return_value = True
        self.assertTrue(sender.send_to_feishu("synthetic"))
        self.assertFalse(fs.FEISHU_FILE_SDK_AVAILABLE)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"synthetic")
            self.assertFalse(sender.send_feishu_file(str(path)))
        client.im.v1.file.create.assert_not_called()

    def test_first_file_send_loads_sdk_and_uploads_before_message(self):
        sender = fs.FeishuSender(config())
        client = self.sdk.Client.builder.return_value.app_id.return_value.app_secret.return_value.domain.return_value.log_level.return_value.build.return_value
        client.im.v1.file.create.return_value.success.return_value = True
        client.im.v1.file.create.return_value.data.file_key = "synthetic-key"
        client.im.v1.message.create.return_value.success.return_value = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.pdf"
            path.write_bytes(b"synthetic")
            self.assertTrue(sender.send_feishu_file(str(path)))
        self.assertTrue(fs.FEISHU_FILE_SDK_AVAILABLE)
        self.assertEqual(self.load.call_count, 3)
        client.im.v1.file.create.assert_called_once()
        client.im.v1.message.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
