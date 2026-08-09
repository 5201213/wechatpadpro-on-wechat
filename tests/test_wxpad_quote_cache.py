import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from channel.wxpad.wxpad_channel import WxpadChannel


class WxpadQuoteCacheTests(unittest.TestCase):
    def _channel(self):
        channel_class = next(
            cell.cell_contents
            for cell in WxpadChannel.__closure__
            if isinstance(cell.cell_contents, type)
        )
        channel = channel_class.__new__(channel_class)
        channel._recent_message_lock = threading.Lock()
        channel._recent_messages = {}
        return channel

    def test_quote_reference_restores_cached_original_content(self):
        channel = self._channel()
        channel._recent_messages["7409285496504272052"] = {
            "content": "“白海豚”将提前登陆！",
            "sender_id": "wxid_sender",
            "sender_name": "🐛",
            "msg_type": 49,
        }
        message = SimpleNamespace(
            app_info={
                "category": "quote",
                "reference": {
                    "display_name": "🐛",
                    "svr_id": "7409285496504272052",
                },
            },
            content="",
        )

        channel._restore_quoted_content(message)

        self.assertEqual("[引用了 🐛 的“白海豚”将提前登陆！]", message.content)

    def test_missing_reference_keeps_processable_placeholder(self):
        channel = self._channel()
        message = SimpleNamespace(
            app_info={
                "category": "quote",
                "reference": {"new_msg_id": "missing"},
            },
            content="",
        )

        channel._restore_quoted_content(message)

        self.assertIn("原消息不在本地", message.content)

    def test_remember_message_indexes_all_server_ids_and_persists(self):
        channel = self._channel()
        message = SimpleNamespace(
            content="原消息",
            msg_id="id-1",
            actual_user_id="wxid_sender",
            actual_user_nickname="Alice",
            from_user_id="123@chatroom",
            msg_data={
                "MsgType": 1,
                "MsgId": "msg-1",
                "NewMsgId": "new-1",
                "RawMessage": {"svr_id": "svr-1"},
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            cache_path = os.path.join(directory, "recent.json")
            with patch("channel.wxpad.wxpad_channel.RECENT_MESSAGE_CACHE_PATH", cache_path):
                channel._remember_message(message)
            with open(cache_path, "r", encoding="utf-8") as cache_file:
                persisted = json.load(cache_file)

        self.assertEqual("原消息", channel._recent_messages["svr-1"]["content"])
        self.assertEqual("原消息", persisted["new-1"]["content"])


if __name__ == "__main__":
    unittest.main()
