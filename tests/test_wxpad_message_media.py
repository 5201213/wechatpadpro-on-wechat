import os
import base64
import tempfile
import unittest
from unittest.mock import patch

from bridge.context import ContextType
from channel.wxpad.wxpad_message import WechatPadProMessage
from common import memory


class WxpadMessageMediaTests(unittest.TestCase):
    def test_max_group_mention_marks_message_as_at(self):
        message = WechatPadProMessage({
            "NewMsgId": 7009002669618197215,
            "FromUserName": "25050214260@chatroom",
            "ToUserName": "wxid_bot",
            "ActualUserName": "wxid_member",
            "MsgType": 1,
            "Content": {"string": "我就要吸烟"},
            "CreateTime": 1786265795,
            "MentionedUserIDs": ["wxid_bot"],
        })

        self.assertTrue(message.is_group)
        self.assertTrue(message.is_at)
        self.assertEqual("我就要吸烟", message.content)

    def test_legacy_group_mention_xml_remains_supported(self):
        message = WechatPadProMessage({
            "NewMsgId": 2,
            "FromUserName": "25050214260@chatroom",
            "ToUserName": "wxid_bot",
            "ActualUserName": "wxid_member",
            "MsgType": 1,
            "Content": {"string": "旧接口"},
            "CreateTime": 1786265795,
            "MsgSource": "<msgsource><atuserlist>wxid_bot</atuserlist></msgsource>",
        })

        self.assertTrue(message.is_at)

    def test_save_image_uses_extension_matching_png_signature(self):
        png = b"\x89PNG\r\n\x1a\n" + b"test-payload"
        message = WechatPadProMessage.__new__(WechatPadProMessage)

        with tempfile.TemporaryDirectory() as directory:
            message.content = os.path.join(directory, "incoming.jpg")

            self.assertTrue(message._save_image_data(png))
            self.assertTrue(message.content.endswith(".png"))
            with open(message.content, "rb") as stream:
                self.assertEqual(png, stream.read())

    def test_transcribed_max_voice_is_processed_as_text(self):
        message = WechatPadProMessage({
            "NewMsgId": 3542729869253335121,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 34,
            "Content": {"string": "这是服务端已经完成的语音转写"},
            "CreateTime": 1786264442,
            "Kind": "voice",
        })

        self.assertEqual(ContextType.TEXT, message.ctype)
        self.assertEqual("这是服务端已经完成的语音转写", message.content)
        self.assertIsNone(message._prepare_fn)

    def test_image_uses_real_list_shaped_cdn_contexts(self):
        png = b"\x89PNG\r\n\x1a\n" + b"cdn-payload"

        class FakeClient:
            def __init__(self):
                self.contexts = []

            def download_cdn_image(self, context):
                self.contexts.append(context)
                return {
                    "Code": 200,
                    "Success": True,
                    "Data": {"Image": base64.b64encode(png).decode("ascii")},
                }

            def download_image_context(self, context):
                raise AssertionError("CDN成功后不应回退到分片下载")

        client = FakeClient()
        message = WechatPadProMessage({
            "NewMsgId": 9211766262251648075,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 3,
            "Content": {"string": ""},
            "CreateTime": 1786264620,
            "Kind": "image",
            "Image": {
                "cdn_download_contexts": [
                    {"variant": "thumbnail", "file_no": "thumb", "file_aes_key": "key-t"},
                    {"variant": "original", "file_no": "original", "file_aes_key": "key-o"},
                ],
                "download_context": {"msg_id": 123},
            },
        }, client)

        with tempfile.TemporaryDirectory() as directory:
            message.content = os.path.join(directory, "incoming.png")

            self.assertTrue(message.prepare())
            self.assertEqual("original", client.contexts[0]["variant"])
            self.assertTrue(message.content.endswith(".png"))

    def test_legacy_quote_message_keeps_current_and_referenced_text(self):
        content = (
            "<msg><appmsg><title>现在的问题</title><type>57</type>"
            "<refermsg><type>1</type><displayname>Alice</displayname>"
            "<content>被引用的原文</content></refermsg></appmsg></msg>"
        )

        message = WechatPadProMessage({
            "NewMsgId": 45,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 49,
            "Content": {"string": content},
            "CreateTime": 1786265795,
            "Kind": "quote",
        })

        self.assertEqual(ContextType.TEXT, message.ctype)
        self.assertEqual("现在的问题\n\n[引用了 Alice 的被引用的原文]", message.content)

    def test_structured_quote_message_context_is_rendered(self):
        message = WechatPadProMessage({
            "NewMsgId": 46,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 49,
            "Content": {"string": ""},
            "CreateTime": 1786265795,
            "Kind": "quote",
            "MessageContext": {
                "title": "现在的问题",
                "quote": {
                    "display_name": "Alice",
                    "quote_content": "被引用的原文",
                },
            },
        })

        self.assertEqual(ContextType.TEXT, message.ctype)
        self.assertEqual("现在的问题\n[引用了 Alice 的被引用的原文]", message.content)

    def test_max_quote_textual_at_uses_bot_group_display_name(self):
        def display_name(group_id, wxid):
            return "翊恒" if wxid == "wxid_bot" else "发送者"

        with patch("channel.chat_channel.get_group_member_display_name", side_effect=display_name):
            message = WechatPadProMessage({
                "NewMsgId": 3765986699460331592,
                "FromUserName": "25050214260@chatroom",
                "ToUserName": "wxid_bot",
                "ActualUserName": "wxid_sender",
                "MsgType": 49,
                "Content": {"string": "@翊恒"},
                "CreateTime": 1786267320,
                "Kind": "quote",
                "ReplyContext": {
                    "svr_id": "3765986699460331592",
                    "quote_content": "被引用的原文",
                },
            })

        self.assertTrue(message.is_at)
        self.assertEqual("翊恒", message.self_display_name)
        self.assertEqual("[引用了 对方 的被引用的原文]", message.content)

    def test_contact_type_42_is_not_misclassified_as_voip(self):
        message = WechatPadProMessage({
            "NewMsgId": 47,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 42,
            "Content": {"string": '<msg username="wxid_card" nickname="张三" />'},
            "CreateTime": 1786265795,
            "Kind": "contact",
        })

        self.assertEqual(ContextType.TEXT, message.ctype)
        self.assertEqual("[名片] 张三 (wxid_card)", message.content)
        self.assertNotIn("语音通话", message.content)

    def test_location_type_48_uses_location_xml(self):
        message = WechatPadProMessage({
            "NewMsgId": 48,
            "FromUserName": "wxid_sender",
            "ToUserName": "wxid_bot",
            "MsgType": 48,
            "Content": {
                "string": '<msg><location x="121.4" y="31.2" label="人民广场" /></msg>'
            },
            "CreateTime": 1786265795,
            "Kind": "location",
        })

        self.assertEqual(ContextType.TEXT, message.ctype)
        self.assertEqual("[位置] 人民广场", message.content)

    def test_referenced_file_uses_max_download_file_context(self):
        payload = b"quoted-file"

        class FakeClient:
            api_mode = "wechat08"

            def __init__(self):
                self.contexts = []

            def download_file_context(self, context):
                self.contexts.append(context)
                return {
                    "Code": 200,
                    "Success": True,
                    "Data": {"FileData": base64.b64encode(payload).decode("ascii")},
                }

            def send_cdn_download(self, *args, **kwargs):
                raise AssertionError("MAX引用文件不能调用图片CDN下载接口")

        client = FakeClient()
        message = WechatPadProMessage.__new__(WechatPadProMessage)
        message.client = client
        message.content = "问题正文"
        message.is_group = False
        message.other_user_id = "wxid_sender"
        message._refer_file_info = {
            "has_refer_file": True,
            "file_info": {
                "title": "资料.pdf",
                "fileext": "pdf",
                "appid": "file-app-id",
                "attachid": "attach-id",
                "totallen": str(len(payload)),
                "md5": "quoted-file-md5",
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            fake_tmp_dir = type("FakeTmpDir", (), {"path": lambda self: directory + os.sep})()
            with patch("channel.wxpad.wxpad_message.TmpDir", return_value=fake_tmp_dir):
                message.download_refer_file_for_multimodal()

            cached = memory.USER_IMAGE_CACHE["wxid_sender"]
            del memory.USER_IMAGE_CACHE["wxid_sender"]
            with open(cached["path"], "rb") as stream:
                self.assertEqual(payload, stream.read())

        self.assertEqual("问题正文", message.content)
        self.assertEqual(1, len(client.contexts))
        self.assertEqual({
            "app_id": "file-app-id",
            "attach_id": "attach-id",
            "user_name": "wxid_sender",
            "data_len": len(payload),
            "section": {"start_pos": 0, "data_len": 1048576},
        }, client.contexts[0])


if __name__ == "__main__":
    unittest.main()
