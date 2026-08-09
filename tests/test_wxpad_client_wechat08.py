import os
import sys
import unittest
import json
import base64
from urllib.parse import urlparse


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)

from lib.wxpad import client as wxpad_client
from lib.wxpad.client import WxpadClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self):
        self.posts = []
        self.gets = []
        self.next_post_payload = {}
        self.post_payloads = []
        self.next_get_payload = {"Code": 0, "Success": True, "Data": {}}
        self.get_payloads = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        payload = self.post_payloads.pop(0) if self.post_payloads else self.next_post_payload
        return FakeResponse(payload)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        payload = self.get_payloads.pop(0) if self.get_payloads else self.next_get_payload
        return FakeResponse(payload)


class WxpadClientWechat08Test(unittest.TestCase):
    def setUp(self):
        self.original_post = wxpad_client.requests.post
        self.original_get = wxpad_client.requests.get
        self.fake_requests = FakeRequests()
        wxpad_client.requests.post = self.fake_requests.post
        wxpad_client.requests.get = self.fake_requests.get

    def tearDown(self):
        wxpad_client.requests.post = self.original_post
        wxpad_client.requests.get = self.original_get

    def _client(self):
        return WxpadClient(
            "http://example.test:8062",
            admin_key="token-key",
            user_key="auth-code",
        )

    def test_gen_auth_key_uses_max_admin_contract(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Message": "success",
            "Data": ["generated-auth-code"],
        }

        result = self._client().gen_auth_key1(count=2, days=365, remark="test")

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Admin/GenAuthKey", url)
        self.assertEqual({}, kwargs["params"])
        self.assertEqual("token-key", kwargs["headers"]["X-Admin-Token"])
        self.assertEqual({"count": 2, "days": 365, "remark": "test"}, kwargs["json"])
        self.assertEqual(200, result["Code"])
        self.assertEqual(["generated-auth-code"], result["Data"])

    def test_qr_login_uses_pad_endpoint_and_access_header(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Message": "success",
            "Data": {
                "QrUrl": "https://login.weixin.qq.com/qrcode/test",
                "QrBase64": "data:image/png;base64,abc",
                "Uuid": "uuid-1",
                "check_token": "a" * 64,
            },
            "DeviceId": "device-1",
        }
        client = self._client()

        result = client.get_login_qr_code_new("auth-code")

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Login/GetQR", url)
        self.assertEqual({}, kwargs["params"])
        self.assertEqual("auth-code", kwargs["headers"]["X-Access-Token"])
        self.assertEqual("iPad", kwargs["json"]["DeviceName"])
        self.assertFalse(kwargs["json"]["oversea"])
        self.assertEqual("uuid-1", client.last_qr_uuid)
        self.assertEqual("a" * 64, client.last_qr_check_token)
        self.assertEqual(200, result["Code"])

    def test_check_qr_uses_uuid_query(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Message": "登录成功",
            "Data": {"acctSectResp": {"userName": {"str": "wxid_abc"}}},
        }
        client = self._client()
        client.last_qr_uuid = "uuid-1"
        client.last_qr_check_token = "b" * 64

        result = client.check_login_status()

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Login/CheckQR", url)
        self.assertEqual({"uuid": "uuid-1"}, kwargs["params"])
        self.assertEqual("b" * 64, kwargs["headers"]["X-QR-Check-Token"])
        self.assertNotIn("X-Access-Token", kwargs["headers"])
        self.assertEqual(1, result["Data"]["loginState"])
        self.assertEqual("wxid_abc", client.wxid)

    def test_check_qr_preserves_pad_face_verification_state(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Message": "需要完成人脸验证",
            "Data": {
                "status": "verifying_face",
                "requires_face_verification": True,
                "ready": True,
                "challenge_id": "challenge-1",
                "verification_url": "http://example.test/face",
                "verification_qr_base64": "data:image/png;base64,abc",
            },
        }
        client = self._client()
        client.last_qr_uuid = "uuid-1"

        result = client.check_login_status()

        _, kwargs = self.fake_requests.posts[0]
        self.assertEqual("auth-code", kwargs["headers"]["X-Access-Token"])
        self.assertTrue(result["Data"]["requires_face_verification"])
        self.assertEqual("verifying_face", result["Data"]["status"])
        self.assertEqual("challenge-1", result["Data"]["challenge_id"])
        self.assertEqual(0, result["Data"]["loginState"])

    def test_check_qr_preserves_max_error_envelope(self):
        self.fake_requests.next_post_payload = {
            "Code": 400,
            "Success": False,
            "Message": "二维码状态无效",
            "CodeValue": "INVALID_QR_STATE",
            "Data": None,
        }
        client = self._client()
        client.last_qr_uuid = "uuid-1"

        result = client.check_login_status()

        self.assertEqual(400, result["Code"])
        self.assertFalse(result["Success"])
        self.assertEqual("INVALID_QR_STATE", result["CodeValue"])
        self.assertEqual(0, result["Data"]["loginState"])

    def test_invalid_authcode_is_not_reported_as_logged_out_success(self):
        self.fake_requests.next_post_payload = {
            "Code": -2,
            "Success": False,
            "Message": "授权码不存在",
            "Data": None,
            "CodeValue": "INVALID_CREDENTIAL",
        }

        result = self._client().get_login_status("bad-auth-code")

        self.assertEqual(-2, result["Code"])
        self.assertFalse(result["Success"])
        self.assertEqual("INVALID_CREDENTIAL", result["CodeValue"])

    def test_initialize_uses_newinit(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}

        result = self._client().initialize_login()

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Login/Newinit", url)
        self.assertEqual({}, kwargs["params"])
        self.assertEqual("auth-code", kwargs["headers"]["X-Access-Token"])
        self.assertEqual(200, result["Code"])

    def test_realtime_services_use_max_contract(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}
        client = self._client()

        heartbeat_result = client.start_auto_heartbeat()
        sync_result = client.start_auto_sync()

        heartbeat_url, heartbeat_kwargs = self.fake_requests.posts[0]
        sync_url, sync_kwargs = self.fake_requests.posts[1]
        self.assertEqual("http://example.test:8062/api/Login/AutoHeartBeat", heartbeat_url)
        self.assertEqual({}, heartbeat_kwargs["params"])
        self.assertEqual("auth-code", heartbeat_kwargs["headers"]["X-Access-Token"])
        self.assertEqual("http://example.test:8062/api/Msg/StartAutoSync", sync_url)
        self.assertEqual({}, sync_kwargs["params"])
        self.assertEqual("auth-code", sync_kwargs["headers"]["X-Access-Token"])
        self.assertEqual({"TargetURL": ""}, sync_kwargs["json"])
        self.assertEqual(200, heartbeat_result["Code"])
        self.assertEqual(200, sync_result["Code"])

    def test_cache_without_ready_long_link_is_reported_offline(self):
        self.fake_requests.next_post_payload = {
            "Code": 1,
            "Success": True,
            "Message": "成功",
            "Data": {"wxid": "wxid_bot", "login_active": True},
        }
        self.fake_requests.next_get_payload = {
            "Code": 0,
            "Success": True,
            "Message": "成功",
            "Data": {
                "ready": False,
                "registered": False,
                "status": {"state": "disconnected"},
            },
        }

        result = self._client().get_login_status()

        self.assertEqual(200, result["Code"])
        self.assertEqual(0, result["Data"]["loginState"])
        self.assertIn("state=disconnected", result["Data"]["loginErrMsg"])

    def test_cache_with_ready_long_link_is_reported_online(self):
        self.fake_requests.next_post_payload = {
            "Code": 1,
            "Success": True,
            "Message": "成功",
            "Data": {"wxid": "wxid_bot", "login_active": True},
        }
        self.fake_requests.next_get_payload = {
            "Code": 0,
            "Success": True,
            "Message": "成功",
            "Data": {
                "ready": True,
                "registered": True,
                "status": {"state": "connected"},
            },
        }

        result = self._client().get_login_status()

        self.assertEqual(200, result["Code"])
        self.assertEqual(1, result["Data"]["loginState"])

    def test_websocket_url_uses_max_sync_route(self):
        url = self._client().get_websocket_url()

        self.assertEqual("ws://example.test:8089/ws/sync?authcode=auth-code", url)

    def test_text_message_translates_legacy_item(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}

        result = self._client().send_text_message([
            {
                "AtWxIDList": ["wxid_a", "wxid_b"],
                "MsgType": 0,
                "TextContent": "hello",
                "ToUserName": "wxid_target",
            }
        ])

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Msg/SendTxt", url)
        self.assertEqual({}, kwargs["params"])
        self.assertEqual("auth-code", kwargs["headers"]["X-Access-Token"])
        self.assertEqual(
            {"At": "wxid_a,wxid_b", "Content": "hello", "ToWxid": "wxid_target", "Type": 0},
            kwargs["json"],
        )
        self.assertEqual(200, result["Code"])

    def test_quote_message_uses_reply_context_without_losing_group_fields(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {"id": "1652752626827517634", "message_type": 49},
        }
        reply_context = {
            "svr_id": 4588852482559559123,
            "new_msg_id": 4588852482559559123,
            "msg_id": "1513020125",
            "msg_type": "34",
            "sequence": "46191",
            "to_wxid": "123456789@chatroom",
            "conversation_id": "123456789@chatroom",
            "from_user_id": "wxid_group_member",
            "chat_user_id": "123456789@chatroom",
            "quote_content": "原消息",
            "undocumented": "must-not-leak",
        }

        result = self._client().quote_message("引用回复", reply_context)

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Msg/Quote", url)
        self.assertEqual("auth-code", kwargs["headers"]["X-Access-Token"])
        self.assertEqual("引用回复", kwargs["json"]["content"])
        context = kwargs["json"]["reply_context"]
        self.assertEqual("4588852482559559123", context["svr_id"])
        self.assertEqual("4588852482559559123", context["new_msg_id"])
        self.assertEqual(1513020125, context["msg_id"])
        self.assertEqual(34, context["msg_type"])
        self.assertEqual(46191, context["sequence"])
        self.assertEqual("wxid_group_member", context["from_user_id"])
        self.assertEqual("123456789@chatroom", context["chat_user_id"])
        self.assertNotIn("undocumented", context)
        self.assertEqual(200, result["Code"])

    def test_quote_message_rejects_missing_context_without_request(self):
        result = self._client().quote_message("引用回复", {})

        self.assertEqual(400, result["Code"])
        self.assertEqual([], self.fake_requests.posts)

    def test_image_and_voice_use_max_payloads(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}
        client = self._client()

        client.send_image_new_message([{"ImageContent": "img64", "ToUserName": "wxid_target"}])
        client.send_voice("wxid_target", "voice64", 4, 3)

        image_url, image_kwargs = self.fake_requests.posts[0]
        voice_url, voice_kwargs = self.fake_requests.posts[1]
        self.assertEqual("http://example.test:8062/api/Msg/UploadImg", image_url)
        self.assertEqual({"Base64": "img64", "ToWxid": "wxid_target"}, image_kwargs["json"])
        self.assertEqual("http://example.test:8062/api/Msg/SendVoice", voice_url)
        self.assertEqual(
            {"Base64": "voice64", "ToWxid": "wxid_target", "Type": 4, "VoiceTime": 3},
            voice_kwargs["json"],
        )

    def test_media_download_uses_new_snake_case_contract(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}
        client = self._client()

        client.get_msg_big_img(0, "wxid_sender", 123, {"DataLen": 10, "StartPos": 20}, "wxid_target", 30)
        client.send_cdn_download("aes-key", 1, "file-no")

        _, download_kwargs = self.fake_requests.posts[0]
        _, cdn_kwargs = self.fake_requests.posts[1]
        self.assertEqual(
            {
                "compress_type": 0,
                "data_len": 30,
                "msg_id": 123,
                "section": {"data_len": 10, "start_pos": 20},
                "to_wxid": "wxid_target",
            },
            download_kwargs["json"],
        )
        self.assertEqual(
            {"file_aes_key": "aes-key", "file_no": "file-no"},
            cdn_kwargs["json"],
        )

    def test_v2_cdn_image_context_is_forwarded_and_normalized(self):
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {"Image": encoded},
        }
        context = {"file_no": "file-no", "file_aes_key": "aes-key"}

        result = self._client().download_cdn_image(context)

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Tools/CdnDownloadImage", url)
        self.assertEqual(context, kwargs["json"])
        self.assertEqual(encoded, result["Data"]["FileData"])
        self.assertEqual(encoded, result["Data"]["Base64"])

    def test_v2_chunked_media_context_uses_documented_endpoints(self):
        first = base64.b64encode(b"abc").decode("ascii")
        second = base64.b64encode(b"def").decode("ascii")
        self.fake_requests.post_payloads = [
            {"Code": 0, "Success": True, "Data": {"FileData": first}},
            {"Code": 0, "Success": True, "Data": {"FileData": second, "end_flag": 1}},
        ]
        context = {
            "to_wxid": "wxid_sender",
            "msg_id": 123,
            "data_len": 6,
            "section": {"start_pos": 0, "data_len": 3},
            "compress_type": 0,
        }

        result = self._client().download_video_context(context)

        self.assertEqual(2, len(self.fake_requests.posts))
        first_url, first_kwargs = self.fake_requests.posts[0]
        second_url, second_kwargs = self.fake_requests.posts[1]
        self.assertEqual("http://example.test:8062/api/Tools/DownloadVideo", first_url)
        self.assertEqual("http://example.test:8062/api/Tools/DownloadVideo", second_url)
        self.assertEqual({"start_pos": 0, "data_len": 3}, first_kwargs["json"]["section"])
        self.assertEqual({"start_pos": 3, "data_len": 3}, second_kwargs["json"]["section"])
        self.assertEqual(b"abcdef", base64.b64decode(result["Data"]["FileData"]))
        self.assertEqual(2, result["Data"]["ChunkCount"])

    def test_v2_chunked_media_reads_real_protobuf_buffer_envelope(self):
        first = base64.b64encode(b"abc").decode("ascii")
        second = base64.b64encode(b"def").decode("ascii")
        self.fake_requests.post_payloads = [
            {
                "Code": 0,
                "Success": True,
                "Data": {
                    "totalLen": 6,
                    "startPos": 0,
                    "dataLen": 3,
                    "data": {"iLen": 3, "buffer": first},
                },
            },
            {
                "Code": 0,
                "Success": True,
                "Data": {
                    "totalLen": 6,
                    "startPos": 3,
                    "dataLen": 3,
                    "data": {"iLen": 3, "buffer": second},
                },
            },
        ]
        context = {
            "to_wxid": "wxid_sender",
            "msg_id": 123,
            "data_len": 0,
            "section": {"start_pos": 0, "data_len": 1048576},
            "compress_type": 0,
        }

        result = self._client().download_image_context(context)

        self.assertEqual(2, len(self.fake_requests.posts))
        self.assertEqual(3, self.fake_requests.posts[1][1]["json"]["section"]["start_pos"])
        self.assertEqual(b"abcdef", base64.b64decode(result["Data"]["FileData"]))
        self.assertEqual(2, result["Data"]["ChunkCount"])

    def test_inner_base_response_error_overrides_outer_success(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Message": "成功",
            "Data": {
                "BaseResponse": {
                    "ret": -104,
                    "errMsg": {"string": "cacheSize do not equal totalLen"},
                },
                "data": {"iLen": 0},
            },
        }
        context = {
            "to_wxid": "wxid_sender",
            "msg_id": 123,
            "data_len": 456,
            "section": {"start_pos": 0, "data_len": 65536},
        }

        result = self._client().download_image_context(context)

        self.assertFalse(result["Success"])
        self.assertEqual(-104, result["Code"])
        self.assertEqual("cacheSize do not equal totalLen", result["Message"])

    def test_v2_file_download_uses_file_route_and_exact_context(self):
        encoded = base64.b64encode(b"file").decode("ascii")
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {"FileData": encoded, "end_flag": 1},
        }
        context = {
            "app_id": "",
            "attach_id": "attach-id",
            "user_name": "123@chatroom",
            "data_len": 4,
            "section": {"start_pos": 0, "data_len": 1048576},
        }

        result = self._client().download_file_context(context)

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Tools/DownloadFile", url)
        self.assertEqual(context, kwargs["json"])
        self.assertEqual(b"file", base64.b64decode(result["Data"]["FileData"]))

    def test_contact_detail_adapts_single_record_to_legacy_list(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {"userName": {"str": "wxid_target"}, "nickName": {"str": "Tester"}},
        }

        result = self._client().get_contact_details_list([], ["wxid_target"])

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Friend/GetContractDetail", url)
        self.assertEqual({"userName": "wxid_target"}, kwargs["json"])
        self.assertEqual(1, len(result["Data"]["contactList"]))
        self.assertEqual("Tester", result["Data"]["contactList"][0]["nickName"])

    def test_contact_detail_normalizes_real_max_field_names(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {
                "ContactList": [{
                    "UserName": {"string": "wxid_target"},
                    "NickName": {"string": "Tester"},
                    "Remark": {"string": "Remark"},
                }]
            },
        }

        result = self._client().get_contact_details_list([], ["wxid_target"])
        contact = result["Data"]["contactList"][0]

        self.assertEqual("wxid_target", contact["userName"])
        self.assertEqual("Tester", contact["nickName"])
        self.assertEqual("Remark", contact["remark"])

    def test_group_member_detail_uses_endpoint_that_contains_members(self):
        self.fake_requests.next_post_payload = {
            "Code": 0,
            "Success": True,
            "Data": {
                "ContactList": [{
                    "NewChatroomData": {
                        "ChatRoomMember": [{
                            "UserName": "wxid_member",
                            "NickName": "Member",
                        }]
                    }
                }]
            },
        }

        result = self._client().get_chatroom_member_detail("123@chatroom")

        url, kwargs = self.fake_requests.posts[0]
        self.assertEqual("http://example.test:8062/api/Group/GetChatRoomInfo", url)
        self.assertEqual({"QID": "123@chatroom"}, kwargs["json"])
        members = result["Data"]["member_data"]["chatroom_member_list"]
        self.assertEqual("wxid_member", members[0]["user_name"])
        self.assertEqual("Member", members[0]["display_name"])

    def test_every_public_api_wrapper_uses_a_live_swagger_endpoint_in_max_mode(self):
        self.fake_requests.next_post_payload = {"Code": 0, "Success": True, "Data": {}}
        client = self._client()
        client.last_qr_uuid = "uuid-1"
        client.wxid = "wxid_bot"

        calls = [
            lambda: client.delay_auth_key("key", 1),
            lambda: client.delete_auth_key("key"),
            lambda: client.gen_auth_key1(),
            lambda: client.gen_auth_key2(),
            lambda: client.a16_login({}),
            lambda: client.check_can_set_alias(),
            lambda: client.check_login_status(),
            lambda: client.device_login("auth-code", {}),
            lambda: client.get_62_data("auth-code"),
            lambda: client.get_iwx_connect("auth-code"),
            lambda: client.get_init_status("auth-code"),
            lambda: client.get_login_qr_code_new("auth-code"),
            lambda: client.get_login_qr_code_new_x("auth-code"),
            lambda: client.get_login_status(),
            lambda: client.logout(),
            lambda: client.login_new("auth-code", {}),
            lambda: client.phone_device_login("auth-code", {}),
            lambda: client.show_qr_code("auth-code"),
            lambda: client.sms_login("auth-code", {}),
            lambda: client.wake_up_login("auth-code"),
            lambda: client.initialize_login(),
            lambda: client.start_auto_heartbeat(),
            lambda: client.start_auto_sync(),
            lambda: client.get_sync_msg_ws(),
            lambda: client.upload_file("file"),
            lambda: client.send_cdn_file("content", "wxid_a"),
            lambda: client.add_message_mgr([{"TextContent": "x", "ToUserName": "wxid_a"}]),
            lambda: client.cdn_upload_video("thumb", "wxid_a", "video"),
            lambda: client.send_video("video", "thumb", 1, "wxid_a"),
            lambda: client.forward_emoji([{"EmojiMd5": "md5", "ToUserName": "wxid_a"}]),
            lambda: client.forward_image_message([{"Content": "xml", "ToUserName": "wxid_a"}], []),
            lambda: client.forward_video_message([], [{"Content": "xml", "ToUserName": "wxid_a"}]),
            lambda: client.get_msg_big_img(0, "wxid_a", 1, {"DataLen": 1, "StartPos": 0}, "wxid_bot", 1),
            lambda: client.get_msg_video(0, "wxid_a", 1, {"DataLen": 1, "StartPos": 0}, "wxid_bot", 1),
            lambda: client.get_msg_voice("1", 1, "1", "wxid_a"),
            lambda: client.download_cdn_image({"file_no": "n", "file_aes_key": "k"}),
            lambda: client.download_image_context({
                "to_wxid": "wxid_a", "msg_id": 1, "data_len": 1,
                "section": {"start_pos": 0, "data_len": 1}, "compress_type": 0,
            }),
            lambda: client.download_video_context({
                "to_wxid": "wxid_a", "msg_id": 1, "data_len": 1,
                "section": {"start_pos": 0, "data_len": 1}, "compress_type": 0,
            }),
            lambda: client.download_file_context({
                "app_id": "", "attach_id": "a", "user_name": "wxid_a", "data_len": 1,
                "section": {"start_pos": 0, "data_len": 1},
            }),
            lambda: client.download_voice_context({
                "bufid": "1", "fromUserName": "wxid_a", "length": 1, "msgId": 1,
            }),
            lambda: client.group_mass_msg_image("img", ["wxid_a"]),
            lambda: client.group_mass_msg_text("text", ["wxid_a"]),
            lambda: client.http_sync_msg(1),
            lambda: client.new_sync_history_message(),
            lambda: client.revoke_msg(1, 1, 1, "wxid_a"),
            lambda: client.revoke_msg_new(1, 1, 1, "wxid_a"),
            lambda: client.send_app_message([{"ContentXML": "xml", "ToUserName": "wxid_a"}]),
            lambda: client.send_cdn_download("aes", 1, "file-no"),
            lambda: client.send_emoji_message([{"EmojiMd5": "md5", "ToUserName": "wxid_a"}]),
            lambda: client.send_image_message([{"ImageContent": "img", "ToUserName": "wxid_a"}]),
            lambda: client.send_image_new_message([{"ImageContent": "img", "ToUserName": "wxid_a"}]),
            lambda: client.send_text_message([{"TextContent": "text", "ToUserName": "wxid_a"}]),
            lambda: client.quote_message("reply", {
                "svr_id": "1", "new_msg_id": "1", "msg_id": 1, "msg_type": 1,
                "sequence": 1, "to_wxid": "wxid_a", "conversation_id": "wxid_a",
                "from_user_id": "wxid_a", "chat_user_id": "", "quote_content": "text",
            }),
            lambda: client.send_voice("wxid_a", "voice", 4, 1),
            lambda: client.share_card_message("a", 0, "n", "wxid_c", "wxid_a"),
            lambda: client.agree_add("", 1, 1, "v1", "v2", "ok"),
            lambda: client.del_contact("wxid_a"),
            lambda: client.get_contact_details_list([], ["wxid_a"]),
            lambda: client.get_contact_list(0, 0),
            lambda: client.get_friend_list(),
            lambda: client.get_friend_relation("wxid_a"),
            lambda: client.get_gh_list(),
            lambda: client.get_m_friend(),
            lambda: client.search_contact(0, 0, 1, "wxid_a"),
            lambda: client.upload_m_contact("1", ["2"]),
            lambda: client.verify_user("", 1, 1, "v1", "v2", "ok"),
            lambda: client.get_my_qr_code(False, 0),
            lambda: client.get_profile(),
            lambda: client.modify_remark("r", "wxid_a"),
            lambda: client.modify_user_info("c", "cn", 0, "n", "p", 1, "s"),
            lambda: client.set_function_switch(1, 1),
            lambda: client.set_nick_name(0, "n"),
            lambda: client.set_sex_dq("c", "cn", "p", 1),
            lambda: client.set_signature(0, "s"),
            lambda: client.set_wechat("alias"),
            lambda: client.update_auto_pass(1),
            lambda: client.update_nick_name(0, "n"),
            lambda: client.upload_head_image("img"),
            lambda: client.add_chat_room_members("1@chatroom", ["wxid_a"]),
            lambda: client.add_chatroom_admin("1@chatroom", ["wxid_a"]),
            lambda: client.create_chat_room("topic", ["wxid_a"]),
            lambda: client.del_chatroom_admin("1@chatroom", ["wxid_a"]),
            lambda: client.get_chat_room_info(["1@chatroom"]),
            lambda: client.get_chatroom_member_detail("1@chatroom"),
            lambda: client.get_chatroom_qr_code("1@chatroom"),
            lambda: client.group_list(),
            lambda: client.invite_chatroom_members("1@chatroom", ["wxid_a"]),
            lambda: client.move_to_contract("1@chatroom", 1),
            lambda: client.quit_chatroom("1@chatroom"),
            lambda: client.scan_into_url_group("url"),
            lambda: client.send_del_del_chat_room_member("1@chatroom", ["wxid_a"]),
            lambda: client.send_pat("1@chatroom", 0, "wxid_a"),
            lambda: client.send_transfer_group_owner("1@chatroom", "wxid_a"),
            lambda: client.set_chatroom_access_verify("1@chatroom", True),
            lambda: client.set_chatroom_announcement("1@chatroom", "a"),
            lambda: client.set_chatroom_name("1@chatroom", "n"),
            lambda: client.set_get_chat_room_info_detail("1@chatroom"),
            lambda: client.sync_message_http(1),
        ]

        for call in calls:
            call()

        swagger_path = os.path.join(ROOT_DIR, "schemas", "swagger-new.json")
        with open(swagger_path, "r", encoding="utf-8") as swagger_file:
            swagger = json.load(swagger_file)
        swagger_paths = set(swagger["paths"])

        recorded = self.fake_requests.posts + self.fake_requests.gets
        self.assertGreater(len(recorded), 70)
        contract_errors = []
        for method, calls_for_method in (
            ("post", self.fake_requests.posts),
            ("get", self.fake_requests.gets),
        ):
            for url, kwargs in calls_for_method:
                request_path = urlparse(url).path
                self.assertTrue(request_path.startswith("/api/"), request_path)
                swagger_endpoint = request_path[len("/api"):]
                self.assertIn(swagger_endpoint, swagger_paths, swagger_endpoint)
                self.assertIn(method, swagger["paths"][swagger_endpoint])

                operation = swagger["paths"][swagger_endpoint][method]
                header_names = {
                    item["name"] for item in operation.get("parameters", [])
                    if item.get("in") == "header"
                }
                sent_headers = kwargs.get("headers") or {}
                sent_auth_headers = {
                    name for name in sent_headers
                    if name in ("X-Access-Token", "X-Admin-Token", "X-QR-Check-Token")
                }
                if not sent_auth_headers:
                    contract_errors.append((swagger_endpoint, "missing auth header"))
                elif not sent_auth_headers.issubset(header_names):
                    contract_errors.append((swagger_endpoint, sent_auth_headers, header_names))

                query_names = {
                    item["name"] for item in operation.get("parameters", [])
                    if item.get("in") == "query"
                }
                unexpected_query = set((kwargs.get("params") or {})) - query_names
                if unexpected_query:
                    contract_errors.append((swagger_endpoint, "query", unexpected_query, query_names))

                body_parameters = [
                    item for item in operation.get("parameters", [])
                    if item.get("in") == "body"
                ]
                body = kwargs.get("json") or {}
                if body_parameters:
                    schema = body_parameters[0].get("schema", {})
                    refs = [schema.get("$ref", "")]
                    refs.extend(item.get("$ref", "") for item in schema.get("allOf", []))
                    allowed_fields = set()
                    for schema_ref in refs:
                        ref = schema_ref.split("/")[-1]
                        if ref:
                            allowed_fields.update(
                                swagger.get("definitions", {}).get(ref, {}).get("properties", {})
                            )
                    unexpected_body = set(body) - allowed_fields
                    if unexpected_body:
                        contract_errors.append((swagger_endpoint, "body", unexpected_body, allowed_fields))
                else:
                    if body:
                        contract_errors.append((swagger_endpoint, "unexpected body", body))

        self.assertEqual([], contract_errors)

        for url, _ in recorded:
            request_path = urlparse(url).path
            self.assertNotRegex(request_path, r"/(admin|login|message|friend|user|group)/")

        self.assertEqual(501, client.change_pwd("new", "old", 0)["Code"])
        self.assertEqual(501, client.set_proxy(False, "127.0.0.1:1080")["Code"])
        self.assertEqual(501, client.set_send_pat("pat")["Code"])


if __name__ == "__main__":
    unittest.main()
