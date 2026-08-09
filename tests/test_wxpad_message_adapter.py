import unittest

from lib.wxpad.message_adapter import extract_websocket_event, normalize_websocket_message


class WxpadMessageAdapterTests(unittest.TestCase):
    def test_extracts_max_sync_message_envelope(self):
        payload = {
            "Code": 0,
            "Success": True,
            "Data": {
                "data": {
                    "count": 1,
                    "messages": [{
                        "content": "登录",
                        "conversation_id": "wxid_bxpso7rap1tu12",
                        "created_at": 1785985268,
                        "direction": "incoming",
                        "id": "3939387452091022867",
                        "is_group": False,
                        "kind": "text",
                        "local_id": 1705525875,
                        "message_source": "<msgsource><signature>x</signature></msgsource>",
                        "recipient_id": "wxid_pdb0f6bqhupwl2",
                        "sender_id": "wxid_bxpso7rap1tu12",
                        "sequence": 840564138,
                        "source": "wechat_pad",
                        "status": 3,
                        "type": 1,
                    }],
                    "schema": "wechatpad.message.v1",
                    "timestamp": 1785985273,
                    "type": "sync_message",
                },
                "Data62": "",
                "Debug": "",
            },
        }

        event_type, messages, metadata = extract_websocket_event(payload)

        self.assertEqual("sync_message", event_type)
        self.assertEqual(1, len(messages))
        self.assertEqual("wechatpad.message.v1", metadata["schema"])

        normalized = normalize_websocket_message(messages[0])
        self.assertEqual("wxid_bxpso7rap1tu12", normalized["FromUserName"])
        self.assertEqual("wxid_pdb0f6bqhupwl2", normalized["ToUserName"])
        self.assertEqual({"str": "登录"}, normalized["Content"])
        self.assertEqual(1, normalized["MsgType"])
        self.assertEqual("3939387452091022867", normalized["NewMsgId"])
        self.assertFalse(normalized["IsOutgoing"])

    def test_preserves_group_conversation_and_actual_sender(self):
        normalized = normalize_websocket_message({
            "content": "你好",
            "conversation_id": "123456@chatroom",
            "created_at": 1785985268,
            "direction": "incoming",
            "id": "42",
            "is_group": True,
            "kind": "text",
            "recipient_id": "wxid_bot",
            "sender_id": "wxid_member",
            "mentioned_user_ids": ["wxid_bot"],
            "type": 1,
        })

        self.assertEqual("123456@chatroom", normalized["FromUserName"])
        self.assertEqual("wxid_member", normalized["ActualUserName"])
        self.assertEqual({"str": "你好"}, normalized["Content"])
        self.assertEqual(["wxid_bot"], normalized["MentionedUserIDs"])

    def test_preserves_v2_media_and_reply_context(self):
        message = {
            "content": "",
            "conversation_id": "123456@chatroom",
            "created_at": 1785985268,
            "direction": "incoming",
            "id": "42",
            "is_group": True,
            "kind": "image",
            "recipient_id": "wxid_bot",
            "sender_id": "wxid_member",
            "type": 3,
            "schema": "wechatpad.message.v2",
            "reply_context": {"svr_id": "42", "to_wxid": "123456@chatroom"},
            "message_context": {
                "title": "被引用的标题",
                "mentioned_user_ids": ["wxid_bot"],
                "media": {"thumbnail": {"width": 120}},
            },
            "app": {"app_message_type": 57, "title": "当前回复"},
            "image": {
                "download_context": {
                    "to_wxid": "123456@chatroom",
                    "msg_id": 7,
                    "data_len": 3,
                    "section": {"start_pos": 0, "data_len": 3},
                },
                "cdn_download_contexts": {
                    "original": {"file_no": "n", "file_aes_key": "k"},
                },
            },
        }

        normalized = normalize_websocket_message(message)

        self.assertEqual("wechatpad.message.v2", normalized["Schema"])
        self.assertEqual(message["reply_context"], normalized["ReplyContext"])
        self.assertEqual(message["message_context"], normalized["MessageContext"])
        self.assertEqual(["wxid_bot"], normalized["MentionedUserIDs"])
        self.assertEqual(message["app"], normalized["App"])
        self.assertEqual(message["image"], normalized["Image"])
        self.assertEqual("image", normalized["Kind"])

    def test_location_kind_supplies_type_when_server_echoes_zero(self):
        normalized = normalize_websocket_message({
            "content": "",
            "conversation_id": "wxid_target",
            "direction": "incoming",
            "id": "44",
            "kind": "location",
            "recipient_id": "wxid_bot",
            "sender_id": "wxid_target",
            "type": 0,
        })

        self.assertEqual(48, normalized["MsgType"])

    def test_hybrid_legacy_envelope_keeps_lowercase_max_contexts(self):
        normalized = normalize_websocket_message({
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgId": 45,
            "MsgType": 49,
            "Content": "回复正文",
            "reply_context": {"svr_id": "45", "to_wxid": "wxid_sender"},
            "message_context": {"title": "被引用标题"},
            "file": {"download_context": {"attach_id": "attach-id"}},
        })

        self.assertEqual({"str": "回复正文"}, normalized["Content"])
        self.assertEqual("45", normalized["ReplyContext"]["svr_id"])
        self.assertEqual("被引用标题", normalized["MessageContext"]["title"])
        self.assertEqual("attach-id", normalized["File"]["download_context"]["attach_id"])

    def test_type_zero_text_echo_uses_canonical_kind(self):
        normalized = normalize_websocket_message({
            "content": "reply",
            "conversation_id": "wxid_target",
            "direction": "outgoing",
            "id": "43",
            "kind": "text",
            "recipient_id": "wxid_target",
            "sender_id": "wxid_bot",
            "type": 0,
        })

        self.assertEqual(1, normalized["MsgType"])
        self.assertTrue(normalized["IsOutgoing"])

    def test_extracts_sync_update_without_treating_it_as_message(self):
        payload = {
            "Data": {
                "data": {
                    "contact_changed": 0,
                    "profiles_changed": 2,
                    "switches_changed": 1,
                    "type": "sync_update",
                }
            }
        }

        event_type, messages, metadata = extract_websocket_event(payload)

        self.assertEqual("sync_update", event_type)
        self.assertEqual([], messages)
        self.assertEqual(2, metadata["profiles_changed"])

    def test_extracts_connection_ready_control_event(self):
        payload = {
            "Code": 0,
            "Success": True,
            "Message": "实时消息通道已就绪",
            "Data": {"timestamp": 1785985585, "type": "connection_ready"},
        }

        event_type, messages, metadata = extract_websocket_event(payload)

        self.assertEqual("connection_ready", event_type)
        self.assertEqual([], messages)
        self.assertEqual(1785985585, metadata["timestamp"])

    def test_keeps_legacy_direct_message_compatible(self):
        legacy = {
            "msg_id": 7,
            "new_msg_id": 8,
            "from_user_name": {"str": "wxid_sender"},
            "to_user_name": {"str": "wxid_bot"},
            "content": {"str": "legacy"},
            "msg_type": 1,
            "create_time": 100,
        }

        event_type, messages, _ = extract_websocket_event(legacy)
        normalized = normalize_websocket_message(messages[0])

        self.assertEqual("sync_message", event_type)
        self.assertEqual("wxid_sender", normalized["FromUserName"])
        self.assertEqual({"str": "legacy"}, normalized["Content"])
        self.assertEqual(8, normalized["NewMsgId"])


if __name__ == "__main__":
    unittest.main()
