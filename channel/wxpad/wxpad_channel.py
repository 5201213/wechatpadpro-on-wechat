import os
import time
import json
import threading
import uuid
import base64
import requests
import tempfile
import urllib.request
from pydub import AudioSegment
from io import BytesIO
import pysilk
import subprocess
from PIL import Image
import io
import qrcode
import sys
import websocket
import urllib.parse
import mimetypes
import shutil

from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel, get_group_member_display_name
from channel.wxpad.wxpad_message import WechatPadProMessage as WxpadMessage
from common.log import logger
from common.singleton import singleton
from common.tmp_dir import TmpDir
from config import conf, save_config
from lib.wxpad.client import WxpadClient
from lib.wxpad.message_adapter import extract_websocket_event, normalize_websocket_message
from voice.audio_convert import mp3_to_silk

MAX_UTF8_LEN = 2048
ROBOT_STAT_PATH = os.path.join(os.path.dirname(__file__), '../../resource/robot_stat.json')
ROBOT_STAT_PATH = os.path.abspath(ROBOT_STAT_PATH)
RECENT_MESSAGE_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), '../../resource/wxpad_recent_messages.json'
)
RECENT_MESSAGE_CACHE_PATH = os.path.abspath(RECENT_MESSAGE_CACHE_PATH)
RECENT_MESSAGE_CACHE_LIMIT = 500



def _format_user_info(user_id, client=None, group_id=None, nickname=None):
    """格式化用户信息显示，只使用已提供的昵称，避免重复API调用"""
    if not user_id:
        return "未知用户"

    # 如果已经提供了昵称，直接使用
    if nickname and nickname != user_id:
        return f"{nickname}({user_id})"

    # 如果没有提供昵称，只显示ID（避免重复API调用）
    return user_id

def _format_group_info(group_id, client=None, group_name=None):
    """格式化群信息显示，只使用已提供的群名称，避免重复API调用"""
    if not group_id or "@chatroom" not in group_id:
        return group_id

    # 如果已经提供了群名称，直接使用
    if group_name and group_name != group_id:
        return f"{group_name}({group_id})"

    # 如果没有提供群名称，只显示ID（避免重复API调用）
    return group_id

@singleton
class WxpadChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        # 直接使用WeChatPadPro配置
        self.base_url = conf().get("wechatpadpro_base_url")
        # 从配置文件读取管理员密钥和普通密钥
        admin_key = conf().get("wechatpadpro_admin_key", "12345")
        user_key = conf().get("wechatpadpro_user_key", None)
        self.client = WxpadClient(self.base_url, admin_key, user_key)
        self.robot_stat = None
        self.wxid = None
        self.device_id = None
        self.device_name = None
        self.last_login_check = 0  # 上次登录检查时间
        self.login_check_interval = 300  # 登录检查间隔（秒）
        # WebSocket相关
        self.ws = None
        self.ws_connected = False
        self.ws_reconnect_count = 0
        self.max_reconnect_attempts = 5
        self.last_heartbeat_time = 0  # 最后收到心跳的时间戳
        # 消息去重
        self.processed_msg_ids = set()  # 已处理的消息ID集合
        self.max_processed_msgs = 1000  # 最多保存1000条消息ID,防止内存溢出
        self._recent_message_lock = threading.Lock()
        self._recent_messages = self._load_recent_messages()
        self._last_pad_login_progress = None
        logger.info(f"[WeChatPadPro] init: base_url: {self.base_url}, admin_key: {admin_key[:3]}***, user_key: {'已配置' if user_key else '未配置'}")

    def _load_recent_messages(self):
        try:
            with open(RECENT_MESSAGE_CACHE_PATH, 'r', encoding='utf-8') as cache_file:
                cached = json.load(cache_file)
            if not isinstance(cached, dict):
                return {}
            items = list(cached.items())[-RECENT_MESSAGE_CACHE_LIMIT:]
            return dict(items)
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f"[wxpad] 读取最近消息缓存失败: {e}")
            return {}

    def _save_recent_messages(self):
        try:
            os.makedirs(os.path.dirname(RECENT_MESSAGE_CACHE_PATH), exist_ok=True)
            temp_path = RECENT_MESSAGE_CACHE_PATH + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as cache_file:
                json.dump(self._recent_messages, cache_file, ensure_ascii=False)
            os.replace(temp_path, RECENT_MESSAGE_CACHE_PATH)
        except Exception as e:
            logger.warning(f"[wxpad] 保存最近消息缓存失败: {e}")

    @staticmethod
    def _message_cache_ids(xmsg):
        msg_data = getattr(xmsg, 'msg_data', {})
        raw_message = msg_data.get('RawMessage', {}) if isinstance(msg_data, dict) else {}
        candidates = [
            getattr(xmsg, 'msg_id', None),
            msg_data.get('MsgId') if isinstance(msg_data, dict) else None,
            msg_data.get('NewMsgId') if isinstance(msg_data, dict) else None,
        ]
        if isinstance(raw_message, dict):
            candidates.extend(
                raw_message.get(key) for key in ('id', 'msg_id', 'new_msg_id', 'svr_id')
            )
        return {str(value) for value in candidates if value not in (None, '', 0, '0')}

    def _remember_message(self, xmsg):
        content = str(getattr(xmsg, 'content', '') or '').strip()
        ids = self._message_cache_ids(xmsg)
        if not content or not ids:
            return

        entry = {
            'content': content[:4000],
            'sender_id': getattr(xmsg, 'actual_user_id', '') or getattr(xmsg, 'from_user_id', ''),
            'sender_name': getattr(xmsg, 'actual_user_nickname', '') or '',
            'msg_type': getattr(xmsg, 'msg_data', {}).get('MsgType', 0),
        }
        with self._recent_message_lock:
            for msg_id in ids:
                self._recent_messages.pop(msg_id, None)
                self._recent_messages[msg_id] = entry
            while len(self._recent_messages) > RECENT_MESSAGE_CACHE_LIMIT:
                oldest = next(iter(self._recent_messages))
                self._recent_messages.pop(oldest, None)
            self._save_recent_messages()

    def _restore_quoted_content(self, xmsg):
        app_info = getattr(xmsg, 'app_info', {})
        if not isinstance(app_info, dict) or app_info.get('category') != 'quote':
            return
        reference = app_info.get('reference', {})
        if not isinstance(reference, dict):
            return

        reference_ids = [
            str(reference.get(key))
            for key in ('svr_id', 'new_msg_id', 'msg_id')
            if reference.get(key) not in (None, '', 0, '0')
        ]
        cached = None
        with self._recent_message_lock:
            for reference_id in reference_ids:
                cached = self._recent_messages.get(reference_id)
                if isinstance(cached, dict):
                    break

        current_content = str(getattr(xmsg, 'content', '') or '').strip()
        if not cached:
            if not current_content:
                xmsg.content = '[引用消息，但原消息不在本地最近消息缓存中]'
            logger.warning(
                f"[wxpad] 引用原消息未命中本地缓存: reference_ids={reference_ids}"
            )
            return

        quoted_content = str(cached.get('content', '') or '').strip()
        display_name = (
            reference.get('display_name')
            or cached.get('sender_name')
            or reference.get('from_user_id')
            or '对方'
        )
        quote_text = f"[引用了 {display_name} 的{quoted_content}]"
        xmsg.content = f"{current_content}\n\n{quote_text}" if current_content else quote_text
        logger.info(
            f"[wxpad] 已从最近消息缓存恢复引用原文: reference_id={reference_ids[0] if reference_ids else ''}"
        )

    def startup(self):
        self._ensure_login()
        if self.client._is_wechat08():
            self._start_max_realtime_services()
        logger.info(f"[wxpad] channel startup, wxid: {self.wxid}")
        threading.Thread(target=self._sync_message_loop, daemon=True).start()

    def _start_max_realtime_services(self):
        """Enable the MAX heartbeat and message synchronization workers."""
        heartbeat_result = self.client.start_auto_heartbeat(self.client.user_key)
        if heartbeat_result.get("Code") == 200:
            logger.info("[wxpad] WeChatPadProMAX 自动心跳已启用")
        else:
            logger.warning(f"[wxpad] 启用 WeChatPadProMAX 自动心跳失败: {heartbeat_result}")

        sync_result = self.client.start_auto_sync(self.client.user_key)
        if sync_result.get("Code") != 200:
            raise Exception(f"启用 WeChatPadProMAX 自动同步失败: {sync_result}")
        logger.info("[wxpad] WeChatPadProMAX 自动消息同步已启用")

    def _wait_for_max_long_link(self, timeout=20, interval=2):
        """Wait briefly for MAX to register and connect its WeChat long link."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.client.get_iwx_connect(self.client.user_key)
            if self.client.is_long_link_ready(result):
                return True
            time.sleep(interval)
        return False

    def _ensure_login(self):
        """确保登录状态"""
        # 1. 检查用户密钥，如果没有则生成
        newly_generated = False
        if not self.client.user_key:
            logger.info("[wxpad] 没有用户密钥，尝试生成授权码")
            if not self._generate_user_key():
                logger.error("[wxpad] 生成用户密钥失败，无法继续")
                raise Exception("生成用户密钥失败，请检查管理员密钥配置")
            logger.info(f"[wxpad] 用户密钥生成成功: {self.client.user_key[:8]}***")
            newly_generated = True
        else:
            logger.info(f"[wxpad] 使用已配置的用户密钥: {self.client.user_key[:8]}***")

        # 2. 如果是新生成的密钥，直接进入二维码登录
        if newly_generated:
            logger.info("[wxpad] 新生成的用户密钥，直接进入二维码登录流程")
            self._qr_code_login()
            return

        # 3. 已有用户密钥，检查在线状态
        try:
            logger.info("[wxpad] 开始检查登录状态..")

            # 使用线程超时方式避免卡住
            import threading
            import queue

            result_queue = queue.Queue()

            def check_status():
                try:
                    result = self.client.get_login_status(self.client.user_key)
                    result_queue.put(('success', result))
                except Exception as e:
                    result_queue.put(('error', e))

            # 启动检查线程
            check_thread = threading.Thread(target=check_status)
            check_thread.daemon = True
            check_thread.start()

            # 等待结果，最多15秒
            try:
                result_type, result_data = result_queue.get(timeout=15)
                if result_type == 'success':
                    status_result = result_data
                    logger.info("[wxpad] 登录状态检查完成")
                    logger.debug(f"[wxpad] 登录状态检查结果: {status_result}")

                    if status_result.get("Code") == 200:
                        data = status_result.get("Data", {})
                        login_state = data.get("loginState")
                        if login_state == 1:  # 在线状态良好
                            logger.info(f"[wxpad] 账号在线状态良好: {data.get('loginErrMsg', '')}")
                            # 缓存会话已经初始化，重复 Newinit 会被 MAX 后端拒绝。
                            # Newinit 只在新的二维码登录成功后执行。
                            # 从状态文件加载wxid等信息
                            self._load_login_info()
                            return
                        else:
                            logger.info(f"[wxpad] 账号离线，状态: {login_state}, 消息: {data.get('loginErrMsg', '')}")
                            if self.client._is_wechat08() and data.get("wxid"):
                                logger.info("[wxpad] 检测到缓存会话，尝试重新初始化")
                                try:
                                    init_result = self.client.initialize_login(self.client.user_key)
                                    if init_result.get("Code") == 200:
                                        self._load_login_info()
                                        logger.info(
                                            "[wxpad] WeChatPadProMAX 缓存会话初始化成功，"
                                            "继续启动实时连接"
                                        )
                                        return
                                    logger.warning(
                                        f"[wxpad] WeChatPadProMAX 缓存会话初始化失败: {init_result}"
                                    )
                                except Exception as recovery_error:
                                    logger.warning(
                                        f"[wxpad] WeChatPadProMAX 缓存会话初始化异常: {recovery_error}"
                                    )
                    else:
                        logger.warning(f"[wxpad] 检查登录状态失败: {status_result}")
                        if (
                            self.client._is_wechat08()
                            and status_result.get("CodeValue") == "INVALID_CREDENTIAL"
                        ):
                            logger.info("[wxpad] 当前授权码无效，使用管理员 TokenKey 重新生成授权码")
                            if not self._generate_user_key():
                                raise Exception("生成 WeChatPadProMAX 授权码失败")
                            self._qr_code_login()
                            return
                else:
                    logger.warning(f"[wxpad] 登录状态检查异常: {result_data}")
            except queue.Empty:
                logger.warning("[wxpad] 登录状态检查超时15秒，跳过在线状态检查")

        except Exception as e:
            logger.warning(f"[wxpad] 检查在线状态异常: {e}")

        # MAX 的 Awaken 在长连接已经失效时可能阻塞数分钟后仍返回
        # MM_ERR_SYS。缓存恢复已经在上面尝试过，失败后直接重新扫码更可靠。
        if self.client._is_wechat08():
            logger.info("[wxpad] MAX 缓存会话无法恢复，直接进入二维码登录流程")
            self._qr_code_login()
            return

        # 4. 尝试唤醒登录
        try:
            logger.info("[wxpad] 尝试唤醒登录...")
            wake_result = self.client.wake_up_login(self.client.user_key)
            logger.debug(f"[wxpad] 唤醒登录结果: {wake_result}")

            if wake_result.get("Code") == 200:
                logger.info("[wxpad] 唤醒登录请求成功，等待用户确认..")
                # 等待用户确认（轮询在线状态）
                if self._wait_for_user_confirmation():
                    return
            else:
                logger.warning(f"[wxpad] 唤醒登录失败: {wake_result}")
        except Exception as e:
            logger.warning(f"[wxpad] 唤醒登录异常: {e}")

        # 5. 进入二维码登录
        logger.info("[wxpad] 唤醒失败，进入二维码登录流程")
        self._qr_code_login()

    def _generate_user_key(self):
        """Generate user key

        Returns:
            bool: Whether generation is successful
        """
        try:
            logger.info("[wxpad] 使用管理员密钥生成普通用户密钥")

            # 尝试使用 gen_auth_key1 生成授权码，有效期1年
            result = self.client.gen_auth_key1(count=1, days=365)
            logger.debug(f"[wxpad] 生成授权码结果: {result}")

            if result.get("Code") == 200:
                auth_keys = result.get("Data")

                # 根据API文档，Data是一个字符串列表
                if isinstance(auth_keys, list) and len(auth_keys) > 0:
                    new_user_key = auth_keys[0]

                    if isinstance(new_user_key, str):
                        # 设置到客户端
                        self.client.user_key = new_user_key

                        # 保存到配置文件
                        try:
                            from config import conf, save_config
                            conf()["wechatpadpro_user_key"] = new_user_key
                            save_config()
                            logger.info(f"[wxpad] 用户密钥已保存到配置文件: {new_user_key[:8]}***")
                        except Exception as e:
                            logger.warning(f"[wxpad] 保存用户密钥到配置文件失败: {e}")

                        return True
                    else:
                        logger.error(f"[wxpad] 授权码格式不正确，期望为字符串: {new_user_key}")
                else:
                    logger.error(f"[wxpad] 未找到授权码或格式不正确: {auth_keys}")
            else:
                logger.error(f"[wxpad] 生成授权码失败: {result}")

            # 如果 gen_auth_key1 失败，尝试gen_auth_key2
            logger.info("[wxpad] 尝试使用 gen_auth_key2 生成授权码")
            result2 = self.client.gen_auth_key2()
            logger.debug(f"[wxpad] gen_auth_key2 结果: {result2}")

            if result2.get("Code") == 200:
                auth_keys = result2.get("Data")

                # 根据API文档，Data是一个字符串列表
                if isinstance(auth_keys, list) and len(auth_keys) > 0:
                    new_user_key = auth_keys[0]

                    if isinstance(new_user_key, str):
                        # 设置到客户端
                        self.client.user_key = new_user_key

                        # 保存到配置文件
                        try:
                            from config import conf, save_config
                            conf()["wechatpadpro_user_key"] = new_user_key
                            save_config()
                            logger.info(f"[wxpad] 用户密钥已保存到配置文件: {new_user_key[:8]}***")
                        except Exception as e:
                            logger.warning(f"[wxpad] 保存用户密钥到配置文件失败: {e}")

                        return True
                    else:
                        logger.error(f"[wxpad] gen_auth_key2 返回的密钥格式不正确，期望为字符串: {new_user_key}")
                else:
                    logger.error(f"[wxpad] gen_auth_key2 未找到授权码或格式不正确: {auth_keys}")
            else:
                logger.error(f"[wxpad] gen_auth_key2 失败: {result2}")

            return False

        except Exception as e:
            logger.error(f"[wxpad] 生成用户密钥异常: {e}")
            return False

    def _load_login_info(self):
        """从状态文件加载登录信息"""
        stat = WxpadClient.load_robot_stat(ROBOT_STAT_PATH)
        if stat and stat.get("wxid"):
            self.wxid = stat["wxid"]
            logger.info(f"[wxpad] 已加载登录信息: wxid={self.wxid}")
        else:
            logger.warning("[wxpad] 状态文件中没有找到wxid信息，尝试从API获取")
            # 主动获取个人资料来获取wxid
            try:
                profile_result = self.client.get_profile(self.client.user_key)
                logger.debug(f"[wxpad] 获取个人资料结果: {profile_result}")

                if profile_result.get("Code") == 200:
                    profile_data = profile_result.get("Data", {})
                    # 从userInfo.userName.str提取wxid
                    user_info = profile_data.get("userInfo", {})
                    user_name = user_info.get("userName", {})
                    wxid = self._extract_str(user_name)

                    if wxid:
                        self.wxid = wxid
                        # 保存到状态文件
                        new_stat = stat or {}
                        new_stat["wxid"] = wxid
                        new_stat["login_time"] = time.time()
                        WxpadClient.save_robot_stat(ROBOT_STAT_PATH, new_stat)
                        logger.info(f"[wxpad] 已从API获取并保存wxid: {wxid}")
                    else:
                        logger.warning(f"[wxpad] 个人资料中未找到wxid字段，返回数据: {profile_data}")
                else:
                    logger.error(f"[wxpad] 获取个人资料失败: {profile_result}")
            except Exception as e:
                logger.error(f"[wxpad] 获取个人资料异常: {e}")

    def _save_pad_qr_image(self, encoded_value, filename):
        """Persist a QR image returned by MAX so an operator can scan it."""
        value = str(encoded_value or "").strip()
        if not value:
            return None
        if value.startswith("data:image") and "," in value:
            value = value.split(",", 1)[1]
        elif value.startswith(("http://", "https://")):
            return None

        try:
            image_data = base64.b64decode(value)
            output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../runtime"))
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, filename)
            with open(output_path, "wb") as image_file:
                image_file.write(image_data)
            return output_path
        except Exception as exc:
            logger.warning(f"[wxpad] 保存 Pad 二维码失败: {exc}")
            return None

    def _report_pad_login_progress(self, result):
        data = result.get("Data", {}) if isinstance(result, dict) else {}
        data = data if isinstance(data, dict) else {}
        status = str(data.get("status") or "")
        target = data.get("verification_url") or data.get("verification_path") or ""
        signature = (
            status,
            data.get("challenge_id") or "",
            target,
            bool(data.get("ready")),
            bool(data.get("requires_face_verification")),
            bool(data.get("requires_slider_verification")),
        )
        if signature == self._last_pad_login_progress:
            return
        self._last_pad_login_progress = signature

        if data.get("requires_face_verification") or data.get("requires_slider_verification"):
            method = "滑块" if data.get("requires_slider_verification") else "人脸"
            logger.warning(f"[wxpad] Pad 登录需要{method}验证，状态: {status or 'preparing'}")
            if target:
                logger.warning(f"[wxpad] Pad {method}验证地址: {target}")
            qr_path = self._save_pad_qr_image(
                data.get("verification_qr_base64"),
                "pad-face-qr.png",
            )
            if qr_path:
                logger.warning(f"[wxpad] Pad {method}二维码已保存: {qr_path}")
            hint = data.get("network_hint")
            if hint:
                logger.info(f"[wxpad] Pad 验证网络提示: {hint}")
        elif status in ("scanned", "resuming_login", "completed"):
            logger.info(f"[wxpad] Pad 登录进度: {status}")

    def _poll_status(
        self,
        api_call_func,
        success_condition_func,
        timeout,
        interval,
        description,
        progress_callback=None,
    ):
        """通用轮询状态辅助函数"""
        start_time = time.time()
        logger.info(f"[wxpad] 开始轮询: {description}，超时时间: {timeout}秒")

        while time.time() - start_time < timeout:
            try:
                result = api_call_func()
                logger.debug(f"[wxpad] 轮询结果 ({description}): {result}")

                if progress_callback:
                    progress_callback(result)

                if success_condition_func(result):
                    logger.info(f"[wxpad] 轮询成功: {description}")
                    return True, result.get("Data", {})

                # 轮询状态提示
                remaining = int(timeout - (time.time() - start_time))
                if remaining > 0 and remaining % 30 == 0:
                     logger.info(f"[wxpad] 等待 {description} 中，剩余时间: {remaining}秒")

            except Exception as e:
                logger.error(f"[wxpad] 轮询异常 ({description}): {e}")

            time.sleep(interval)

        logger.warning(f"[wxpad] 轮询超时: {description}")
        return False, None

    def _wait_for_user_confirmation(self, timeout=300):
        """使用轮询辅助函数等待用户确认"""
        success, _ = self._poll_status(
            api_call_func=lambda: self.client.get_login_status(self.client.user_key),
            success_condition_func=lambda res: res.get("Code") == 200 and res.get("Data", {}).get("loginState") == 1,
            timeout=timeout,
            interval=5,
            description="用户确认登录"
        )
        if success:
            self._load_login_info()
        return success

    def _qr_code_login(self):
        """QR code login process"""
        try:
            logger.info("[wxpad] 开始二维码登录流程")

            # 获取二维码
            qr_result = self.client.get_login_qr_code_new(self.client.user_key)
            logger.debug(f"[wxpad] 二维码获取结果: {qr_result}")

            if qr_result.get("Code") != 200:
                logger.error(f"[wxpad] 获取二维码失败: {qr_result}")
                raise Exception("Failed to get QR code")

            # 从返回结果中提取二维码信息（根据实际API返回格式调整）
            data = qr_result.get("Data", {})
            qr_url = data.get("QrCodeUrl") or data.get("qrUrl") or data.get("QrUrl") or data.get("url")
            qr_base64 = data.get("QrBase64") or data.get("qr_base64")

            if not qr_url and not qr_base64:
                logger.error(f"[wxpad] 未获取到二维码链接，返回内容: {qr_result}")
                raise Exception("Failed to get QR code URL")

            qr_image_path = self._save_pad_qr_image(qr_base64, "pad-login-qr.png")
            if qr_image_path:
                logger.info(f"[wxpad] Pad 登录二维码已保存: {qr_image_path}")

            # API建议自定义生成二维码，以提高稳定性和效率。我们解析出原始微信登录链接进行处理。
            try:
                import urllib.parse
                final_qr_data = None
                if qr_url:
                    parsed_url = urllib.parse.urlparse(qr_url)
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    # 优先使用'url'参数中的链接，如果不存在则使用原始链接
                    final_qr_data = query_params.get('url', [qr_url])[0]
                    logger.info(f"[wxpad] 提取用于生成二维码的链接: {final_qr_data}")
            except Exception as e:
                logger.warning(f"[wxpad] 解析二维码链接失败，将使用原始链接: {e}")
                final_qr_data = qr_url

            if final_qr_data:
                logger.info(f"[wxpad] 请扫码登录: {final_qr_data}")
            elif qr_image_path:
                logger.info(f"[wxpad] 请扫描 Pad 登录二维码文件: {qr_image_path}")

            # 控制台渲染二维码
            try:
                import qrcode
                if final_qr_data:
                    qr = qrcode.QRCode(border=1)
                    qr.add_data(final_qr_data)
                    qr.make(fit=True)
                    qr.print_ascii(out=sys.stdout)
            except Exception as e:
                logger.warning(f"[wxpad] 控制台二维码渲染失败: {e}")

            # 使用轮询辅助函数等待扫码登录
            success, login_data = self._poll_status(
                api_call_func=lambda: self.client.check_login_status(self.client.user_key),
                success_condition_func=lambda res: res.get("Code") == 200 and res.get("Data", {}).get("loginState") == 1,
                timeout=600,
                interval=2,
                description="Pad 扫码及人脸验证",
                progress_callback=self._report_pad_login_progress,
            )

            if success:
                logger.info("[wxpad] 扫码登录成功")
                self._save_login_success()
            else:
                raise Exception("扫码超时或失败，请重启程序重试")

        except Exception as e:
            logger.error(f"[wxpad] 二维码登录失败: {e}")
            raise

    def _save_login_success(self):
        """保存登录成功后的信息"""
        self._initialize_session()
        try:
            # 获取个人资料信息来获取wxid
            profile_result = self.client.get_profile(self.client.user_key)
            if profile_result.get("Code") == 200:
                profile_data = profile_result.get("Data", {})
                # 从userInfo.userName.str提取wxid
                user_info = profile_data.get("userInfo", {})
                user_name = user_info.get("userName", {})
                wxid = self._extract_str(user_name)
                if wxid:
                    self.wxid = wxid
                    # 保存到状态文件
                    stat = {"wxid": wxid, "login_time": time.time()}
                    WxpadClient.save_robot_stat(ROBOT_STAT_PATH, stat)
                    logger.info(f"[wxpad] 登录信息已保存: wxid={wxid}")
                else:
                    logger.warning("[wxpad] 未能从个人资料中获取wxid")
            else:
                logger.warning(f"[wxpad] 获取个人资料失败: {profile_result}")
        except Exception as e:
            logger.error(f"[wxpad] 保存登录信息失败: {e}")

    def _initialize_session(self):
        """初始化登录会话；MAX 后端要求扫码成功后显式调用 Newinit。"""
        if not self.client._is_wechat08():
            return

        result = self.client.initialize_login(self.client.user_key)
        if result.get("Code") != 200:
            raise Exception(f"WeChatPadProMAX 初始化失败: {result.get('Message', result)}")
        logger.info("[wxpad] WeChatPadProMAX 初始化成功")

    def _sync_message_loop(self):
        """消息同步循环 - 使用WebSocket"""
        logger.info("[wxpad] 开始WebSocket消息同步循环")

        while True:
            try:
                self._connect_websocket()
                if self.ws and self.ws_connected:
                    # WebSocket连接成功，等待消息
                    logger.info("[wxpad] WebSocket连接已建立，等待消息...")
                    # WebSocket会在回调中处理消息，这里只需要保持连接
                    while self.ws_connected:
                        time.sleep(1)
                else:
                    logger.error("[wxpad] WebSocket连接失败，等待重试..")
                    time.sleep(5)

            except Exception as e:
                logger.error(f"[wxpad] WebSocket消息同步异常: {e}")
                self.ws_connected = False
                time.sleep(5)  # 异常时等5秒再重试

    def _connect_websocket(self):
        """建立WebSocket连接"""
        if self.ws_connected:
            return

        try:
            # 获取WebSocket URL
            ws_url = self.client.get_websocket_url(self.client.user_key)
            safe_ws_url = ws_url
            if 'authcode=' in safe_ws_url:
                prefix, auth_part = safe_ws_url.split('authcode=', 1)
                suffix = '&' + auth_part.split('&', 1)[1] if '&' in auth_part else ''
                safe_ws_url = f"{prefix}authcode=***{suffix}"
            logger.info(f"[wxpad] 连接WebSocket: {safe_ws_url}")

            # 创建WebSocket连接
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close
            )

            # 启动WebSocket连接（阻塞）
            self.ws.run_forever()

        except Exception as e:
            logger.error(f"[wxpad] WebSocket连接异常: {e}")
            self.ws_connected = False

    def _on_ws_open(self, ws):
        """WebSocket连接打开回调"""
        logger.info("[wxpad] WebSocket连接已建立")
        self.ws_connected = True
        self.ws_reconnect_count = 0

    def _on_ws_message(self, ws, message):
        """WebSocket消息接收回调"""
        try:
            logger.debug(f"[wxpad] 收到WebSocket消息: {message}")
            data = json.loads(message)
            event_type, messages, metadata = extract_websocket_event(data)

            if messages:
                logger.info(f"[wxpad] 收到 {len(messages)} 条WebSocket业务消息")
                for index, raw_message in enumerate(messages, 1):
                    try:
                        if metadata.get('schema') and 'schema' not in raw_message:
                            raw_message = dict(raw_message)
                            raw_message['schema'] = metadata.get('schema')
                        standard_msg = self._convert_message(raw_message)
                        msg_id = standard_msg.get('NewMsgId') or standard_msg.get('MsgId')
                        if msg_id and msg_id in self.processed_msg_ids:
                            logger.debug(f"[wxpad] 忽略重复消息 {index}: msg_id={msg_id}")
                            continue

                        if msg_id:
                            self.processed_msg_ids.add(msg_id)
                            if len(self.processed_msg_ids) > self.max_processed_msgs:
                                msg_list = list(self.processed_msg_ids)
                                self.processed_msg_ids = set(msg_list[len(msg_list) // 2:])
                                logger.debug(f"[wxpad] 清理消息ID缓存,保留 {len(self.processed_msg_ids)} 条")

                        logger.info(
                            f"[wxpad] 处理消息 {index}: "
                            f"from={standard_msg.get('FromUserName')}, "
                            f"type={standard_msg.get('MsgType')}, msg_id={msg_id}"
                        )
                        self._handle_message(standard_msg)
                    except Exception as e:
                        logger.exception(f"[wxpad] 处理消息 {index} 异常: {e}")
            elif event_type == 'sync_update':
                logger.debug(
                    "[wxpad] 同步状态更新: contact_changed=%s, profiles_changed=%s, switches_changed=%s",
                    metadata.get('contacts_changed', metadata.get('contact_changed', 0)),
                    metadata.get('profiles_changed', 0),
                    metadata.get('switches_changed', 0)
                )
            elif event_type in ('welcome', 'connection_ready'):
                # 处理WebSocket连接欢迎消息
                welcome_msg = metadata.get(
                    'message',
                    data.get('Message', data.get('message', 'WebSocket连接已建立'))
                )
                logger.info(f"[wxpad] 收到WebSocket欢迎消息: {welcome_msg}")

                # 如果welcome消息包含额外的连接信息，可以在这里处理
                if 'data' in data and isinstance(data['data'], dict):
                    logger.debug(f"[wxpad] Welcome消息包含额外信息: {data['data']}")
                    # 可以从welcome消息中提取服务器时间、连接ID等信息
                    server_time = data['data'].get('serverTime')
                    connection_id = data['data'].get('connectionId')
                    if server_time:
                        logger.debug(f"[wxpad] 服务器时间: {server_time}")
                    if connection_id:
                        logger.debug(f"[wxpad] 连接ID: {connection_id}")

                # welcome消息通常是连接建立后的确认消息，表示连接正常工作
                logger.info("[wxpad] WebSocket连接已确认，可以正常接收消息")

                # 重置重连计数，表示连接正常
                self.ws_reconnect_count = 0

                pass
            elif event_type == 'heartbeat':
                # 处理WebSocket心跳消息
                timestamp = data.get('timestamp')
                current_time = int(time.time())

                if timestamp:
                    logger.debug(f"[wxpad] 收到心跳消息: timestamp={timestamp}")
                else:
                    logger.debug(f"[wxpad] 收到心跳消息: {data}")

                # 更新最后收到心跳的时间戳
                self.last_heartbeat_time = current_time
                logger.debug(f"[wxpad] 心跳时间已更新: {self.last_heartbeat_time}")

                # 心跳消息表示连接仍然活跃，连接状态良好
                pass
            else:
                logger.warning(f"[wxpad] 收到未知格式的WebSocket消息: {data}")

        except Exception as e:
            logger.error(f"[wxpad] 处理WebSocket消息异常: {e}")

    def _on_ws_error(self, ws, error):
        """WebSocket错误回调"""
        logger.error(f"[wxpad] WebSocket错误: {error}")
        self.ws_connected = False

    def _on_ws_close(self, ws, close_status_code, close_msg):
        """WebSocket连接关闭回调"""
        logger.warning(f"[wxpad] WebSocket连接已关闭: {close_status_code}, {close_msg}")
        self.ws_connected = False

        # 重连逻辑
        if self.ws_reconnect_count < self.max_reconnect_attempts:
            self.ws_reconnect_count += 1
            logger.info(f"[wxpad] 尝试重连WebSocket ({self.ws_reconnect_count}/{self.max_reconnect_attempts})")
            time.sleep(5)  # 等待5秒后重连
        else:
            logger.error(f"[wxpad] WebSocket重连次数已达上限，停止重连")

    def _extract_str(self, value):
        """提取字符串值"""
        return value.get('str', '') if isinstance(value, dict) else str(value or '')

    def _convert_message(self, msg):
        """转换消息格式"""
        return normalize_websocket_message(msg)

    def _should_ignore_message(self, xmsg):
        """统一的消息过滤检查"""
        # 1. 过期消息检查
        if hasattr(xmsg, 'create_time') and xmsg.create_time:
            try:
                current_time = int(time.time())
                msg_time = int(xmsg.create_time)
                if msg_time < current_time - 60 * 5:  # 5分钟过期
                    logger.debug(f"[wxpad] ignore expired message from {xmsg.from_user_id}")
                    return True
            except (ValueError, TypeError):
                pass  # 时间格式无效时继续处理

        # 2. 非用户消息过滤
        if xmsg._is_non_user_message(xmsg.msg_source, xmsg.from_user_id):
            logger.debug(f"[wxpad] ignore non-user/system message from {xmsg.from_user_id}")
            return True

        # 3. 自己发送的消息过滤
        if getattr(xmsg, 'my_msg', False) or (hasattr(xmsg, 'from_user_id') and xmsg.from_user_id == self.wxid):
            logger.debug(f"[wxpad] ignore message from myself: {xmsg.from_user_id}")
            return True

        # 4. 语音消息配置检查
        if xmsg.ctype == ContextType.VOICE and not conf().get("speech_recognition", False):
            logger.debug(f"[wxpad] ignore voice message, speech_recognition disabled")
            return True

        return False

    def _handle_message(self, msg):
        xmsg = WxpadMessage(msg, self.client)

        # MAX 的 quote app 只给被引用消息ID；先从本地最近消息缓存恢复原文。
        self._restore_quoted_content(xmsg)
        # 在过滤自己发出的回显之前也记录，允许用户引用机器人的上一条回复。
        self._remember_message(xmsg)

        # 统一过滤检查
        if self._should_ignore_message(xmsg):
            # 简化过滤日志显示，避免重复API调用
            logger.debug(f"[wxpad] 消息被过滤: from={xmsg.from_user_id}, reason=过滤规则")
            return

        # 格式化有效消息日志显示
        if xmsg.is_group:
            # 直接使用消息对象中已获取的群名称，避免重复调用API
            group_name = getattr(xmsg, 'other_user_nickname', None)  # 对于群聊，other_user_nickname就是群名称
            group_info = _format_group_info(xmsg.from_user_id, self.client, group_name)

            # 获取实际发言人信息（如果有的话）
            actual_user_info = ""
            if hasattr(xmsg, 'actual_user_id') and xmsg.actual_user_id and xmsg.actual_user_id != xmsg.from_user_id:
                # 直接使用消息对象中已获取的昵称，避免重复调用API
                actual_nickname = getattr(xmsg, 'actual_user_nickname', None)
                actual_user_info = f" 发言人: {_format_user_info(xmsg.actual_user_id, self.client, xmsg.from_user_id, actual_nickname)}"
            logger.info(f"[wxpad] 📨 {group_info}{actual_user_info}: {xmsg.content[:50] if xmsg.content else 'None'}")
        else:
            # 直接使用消息对象中已获取的昵称，避免重复调用API
            user_nickname = getattr(xmsg, 'other_user_nickname', None)
            user_info = _format_user_info(xmsg.from_user_id, self.client, None, user_nickname)
            logger.info(f"[wxpad] 💬 {user_info}: {xmsg.content[:50] if xmsg.content else 'None'}")

        # 如果是图片、视频、文件、语音消息，需要立即处理下载（这些是主要内容）
        if xmsg.ctype == ContextType.IMAGE:
            logger.debug(f"[wxpad] 检测到图片消息，开始下载处理")
            xmsg.prepare()  # 触发图片下载

        elif xmsg.ctype == ContextType.VIDEO:
            logger.debug(f"[wxpad] 检测到视频消息，开始下载处理")
            xmsg.prepare()  # 触发视频下载

        elif xmsg.ctype == ContextType.FILE:
            logger.debug(f"[wxpad] 检测到文件消息，开始下载处理")
            xmsg.prepare()  # 触发文件下载

        elif xmsg.ctype == ContextType.VOICE:
            logger.debug(f"[wxpad] 检测到语音消息，开始下载处理")
            xmsg.prepare()  # 触发语音下载

        # 不把一个并不存在的临时路径交给图片识别、语音识别或文件处理
        # 线程；下载失败时保留一条可处理的文本占位消息。
        media_labels = {
            ContextType.IMAGE: "图片",
            ContextType.VIDEO: "视频",
            ContextType.FILE: "文件",
            ContextType.VOICE: "语音",
        }
        if xmsg.ctype in media_labels and not os.path.isfile(str(xmsg.content or "")):
            label = media_labels[xmsg.ctype]
            logger.warning(f"[wxpad] {label}下载失败，已阻止不存在的临时文件进入处理队列")
            xmsg.ctype = ContextType.TEXT
            xmsg.content = f"[{label}消息，下载失败]"

        # 处理消息
        if xmsg.is_group:
            logger.info(f"[wxpad] 准备处理群消息: group_id={xmsg.from_user_id}, group_name={getattr(xmsg, 'other_user_nickname', 'NONE')}, actual_user={xmsg.actual_user_id}")
        context = self._compose_context(xmsg.ctype, xmsg.content, msg=xmsg, isgroup=xmsg.is_group)
        if context is not None:
            # 只有成功生成上下文后，才处理引用图片/文件的下载和缓存
            # 如果是引用图片的文本消息，也需要准备引用图片
            if xmsg.ctype == ContextType.TEXT and hasattr(xmsg, '_refer_image_info') and xmsg._refer_image_info.get('has_refer_image'):
                logger.debug(f"[wxpad] 检测到引用图片的文本消息，开始准备引用图片")
                xmsg.prepare()  # 触发引用图片下载和缓存

            # 如果是引用文件的文本消息，也需要准备引用文件
            elif xmsg.ctype == ContextType.TEXT and hasattr(xmsg, '_refer_file_info') and xmsg._refer_file_info.get('has_refer_file'):
                logger.debug(f"[wxpad] 检测到引用文件的文本消息，开始准备引用文件")
                xmsg.prepare()  # 触发引用文件下载和缓存

            logger.info(f"[wxpad] 消息已提交处理")
            self.produce(context)
        else:
            logger.warning(f"[wxpad] 无法生成上下文，消息类型: {xmsg.ctype}")

    def send(self, reply: Reply, context: Context):
        """发送消息到微信

        Args:
            reply: 回复对象
            context: 上下文对象
        """
        # 获取接收者，优先从context的receiver获取，其次从msg中获取
        receiver = context.get("receiver")
        if not receiver and context.get("msg"):
            msg = context.get("msg")
            # 如果是群聊，接收者应该是群ID
            if hasattr(msg, "from_user_id") and "@chatroom" in (msg.from_user_id or ""):
                receiver = msg.from_user_id
            # 如果是私聊，接收者应该是发送者ID
            elif hasattr(msg, "from_user_id"):
                receiver = msg.from_user_id
            # 备用：尝试从other_user_id获取
            elif hasattr(msg, "other_user_id"):
                receiver = msg.other_user_id

        if not receiver:
            logger.error(f"[wxpad] Cannot determine receiver for reply: {reply.type}")
            return

        # 格式化接收者信息显示 - 优先从数据库获取，避免API调用
        if "@chatroom" in receiver:
            # 群聊消息 - 从数据库获取群名称
            try:
                from database.group_members_db import get_group_name_from_db
                group_name = get_group_name_from_db(receiver)
                receiver_info = _format_group_info(receiver, self.client, group_name)
            except Exception:
                # 如果获取失败，使用简化显示
                receiver_info = f"{receiver}"
        else:
            # 私聊消息 - 从群成员数据库获取用户昵称
            try:
                from database.group_members_db import get_user_nickname_from_db
                user_nickname = get_user_nickname_from_db(receiver)
                receiver_info = _format_user_info(receiver, self.client, None, user_nickname)
            except Exception:
                # 如果获取失败，使用简化显示
                receiver_info = f"{receiver}"

        logger.debug(f"[wxpad] Sending {reply.type} to {receiver_info}")

        try:
            if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
                # 文本消息 - 使用正确的API格式
                msg_item = [{
                    "AtWxIDList": [],
                    "ImageContent": "",
                    "MsgType": 0,  # 文本消息类型
                    "TextContent": reply.content,
                    "ToUserName": receiver
                }]
                result = self.client.send_text_message(msg_item)
                if result.get("Code") == 200:
                    logger.info(f"[wxpad] ✅ 发送文本消息到 {receiver_info}: {reply.content[:50]}...")
                else:
                    logger.error(f"[wxpad] ❌ 发送文本消息失败到 {receiver_info}: {result}")
                    raise Exception(f"发送文本消息失败: {result}")

            elif reply.type == ReplyType.IMAGE:
                success = self.send_image(reply.content, receiver)
                if not success:
                    # 发送失败时，尝试发送错误提示
                    try:
                        error_msg = "图片发送失败，请稍后再试"
                        msg_item = [{
                            "AtWxIDList": [],
                            "ImageContent": "",
                            "MsgType": 0,
                            "TextContent": error_msg,
                            "ToUserName": receiver
                        }]
                        self.client.send_text_message(msg_item)
                        logger.info(f"[wxpad] 图片发送失败，已发送错误提示")
                    except Exception as e:
                        logger.error(f"[wxpad] 发送图片失败提示消息异常: {e}")
                return

            elif reply.type == ReplyType.VOICE:
                # 语音消息 - 使用SILK转换
                try:
                    import os
                    import asyncio
                    import time

                    original_voice_file_path = reply.content
                    if not original_voice_file_path or not os.path.exists(original_voice_file_path):
                        logger.error(f"[wxpad] Send voice failed: Original voice file not found or path is empty: {original_voice_file_path}")
                        return

                    # 支持常见音频格式
                    supported_formats = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.silk', '.sil', '.slk']
                    file_ext = os.path.splitext(original_voice_file_path)[1].lower()
                    if file_ext not in supported_formats:
                        logger.error(f"[wxpad] Send voice failed: Unsupported voice file format: {file_ext}")
                        return

                    temp_files_to_clean = []
                    # 添加原始下载的语音文件到清理列表
                    temp_files_to_clean.append(original_voice_file_path)

                    try:
                        # 微信语音条支持最多60秒，超60秒分段
                        from voice.audio_convert import split_audio
                        total_duration_ms, segment_paths = split_audio(original_voice_file_path, 60 * 1000)
                        temp_files_to_clean.extend(segment_paths) # Add segment paths from split_audio for cleanup

                        if not segment_paths:
                            logger.error(f"[wxpad] Voice splitting failed for {original_voice_file_path}. No segments created.")
                            logger.info(f"[wxpad] Attempting to send {original_voice_file_path} as fallback.")
                            # 直接发送原文件作为回退
                            # 使用兼容非主线程的方式运行异步代码
                            try:
                                # 在 Windows 上使用 SelectorEventLoop 避免 ProactorEventLoop 的信号问题
                                if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
                                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    fallback_result = loop.run_until_complete(self._send_voice(receiver, original_voice_file_path))
                                finally:
                                    try:
                                        loop.close()
                                    except:
                                        pass  # 忽略关闭时的错误
                            except Exception as e:
                                logger.error(f"[wxpad] Failed to run async voice send (fallback): {e}")
                                fallback_result = None

                            if fallback_result and isinstance(fallback_result, dict) and fallback_result.get("Success", False):
                                logger.info(f"[wxpad] Fallback: Sent voice file successfully: {original_voice_file_path}")
                            else:
                                logger.warning(f"[wxpad] Fallback: Sending voice file failed: {original_voice_file_path}, Result: {fallback_result}")
                            return

                        logger.info(f"[wxpad] Voice file {original_voice_file_path} split into {len(segment_paths)} segments.")

                        for i, segment_path in enumerate(segment_paths):
                            # SILK转换和发送都在_send_voice方法中处理
                            # 使用兼容非主线程的方式运行异步代码
                            try:
                                # 在 Windows 上使用 SelectorEventLoop 避免 ProactorEventLoop 的信号问题
                                if hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
                                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    segment_result = loop.run_until_complete(self._send_voice(receiver, segment_path))
                                finally:
                                    try:
                                        loop.close()
                                    except:
                                        pass  # 忽略关闭时的错误
                            except Exception as e:
                                logger.error(f"[wxpad] Failed to run async voice send: {e}")
                                segment_result = None

                            if segment_result and isinstance(segment_result, dict) and segment_result.get("Success", False):
                                logger.info(f"[wxpad] Sent voice segment {i+1}/{len(segment_paths)} successfully: {segment_path}")
                            else:
                                logger.warning(f"[wxpad] Sending voice segment {i+1}/{len(segment_paths)} failed: {segment_path}, Result: {segment_result}")
                                # 如果片段失败,继续发送其他片段

                            # 片段间添加间隔,避免发送过快
                            if i < len(segment_paths) - 1:
                                time.sleep(0.8)

                    except Exception as e_split_send:
                        logger.error(f"[wxpad] Error during voice splitting or segmented sending for {original_voice_file_path}: {e_split_send}")
                        import traceback
                        logger.error(traceback.format_exc())
                    finally:
                        logger.info(f"[wxpad] 开始清理{len(temp_files_to_clean)} 个语音相关文件..")
                        for temp_file_path in temp_files_to_clean:
                            try:
                                if os.path.exists(temp_file_path):
                                    file_size = os.path.getsize(temp_file_path)
                                    os.remove(temp_file_path)
                                    if temp_file_path == original_voice_file_path:
                                        logger.info(f"[wxpad] 已清理原始下载语音文件: {os.path.basename(temp_file_path)} ({file_size} bytes)")
                                    else:
                                        logger.debug(f"[wxpad] 已清理临时语音文件: {os.path.basename(temp_file_path)} ({file_size} bytes)")
                                else:
                                    logger.debug(f"[wxpad] 文件不存在，跳过清理: {temp_file_path}")
                            except Exception as e_cleanup:
                                logger.warning(f"[wxpad] 清理语音文件失败 {temp_file_path}: {e_cleanup}")
                        logger.info(f"[wxpad] 语音文件清理完成")

                except Exception as e:
                    logger.error(f"[wxpad] 语音处理异常: {e}")
                    # 不再抛出异常，避免中断整个发送过程
                    try:
                        error_msg = f"语音发送失败，请稍后再试"
                        msg_item = [{
                            "AtWxIDList": [],
                            "ImageContent": "",
                            "MsgType": 0,
                            "TextContent": error_msg,
                            "ToUserName": receiver
                        }]
                        self.client.send_text_message(msg_item)
                    except:
                        pass

            elif reply.type == ReplyType.VIDEO_URL:
                # 视频URL消息 - 原来的处理逻辑是正确的
                try:
                    import tempfile
                    import os
                    import base64

                    video_url = reply.content
                    logger.info(f"[wxpad] 开始处理视频URL: {video_url}")

                    if not video_url:
                        logger.error("[wxpad] 视频URL为空")
                        msg_item = [{
                            "AtWxIDList": [],
                            "ImageContent": "",
                            "MsgType": 0,
                            "TextContent": "视频URL无效",
                            "ToUserName": receiver
                        }]
                        self.client.send_text_message(msg_item)
                        return

                    # 下载视频
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
                    }

                    # 使用项目临时目录保存视频
                    temp_path = None
                    try:
                        temp_dir = TmpDir().path()
                        temp_path = os.path.join(temp_dir, f"downloaded_video_{uuid.uuid4().hex[:8]}.mp4")

                        logger.info(f"[wxpad] 正在下载视频至临时文件: {temp_path}")

                        # 下载视频到临时文件
                        with open(temp_path, 'wb') as f:
                            response = requests.get(video_url, headers=headers, stream=True, timeout=60)
                            response.raise_for_status()
                            total_size = int(response.headers.get('Content-Length', 0))
                            downloaded = 0

                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    percent = int(downloaded / total_size * 100) if total_size > 0 else 0
                                    if percent % 20 == 0:  # 每20%记录一次
                                        logger.info(f"[wxpad] 视频下载进度: {percent}%")

                        content_type = response.headers.get('Content-Type', '')
                        logger.info(f"[wxpad] 视频下载完成: {temp_path}, 内容类型: {content_type}, 大小: {downloaded}字节")

                        # 用OpenCV提取第一帧为缩略图并获取视频时长（与示例脚本保持一致）
                        thumb_path = temp_path + "_thumb.jpg"
                        video_length = 10  # 默认10秒
                        try:
                            import cv2

                            # 打开视频文件
                            cap = cv2.VideoCapture(temp_path)

                            # 检查视频是否成功打开
                            if not cap.isOpened():
                                raise Exception(f"无法打开视频文件: {temp_path}")

                            # 获取视频时长
                            fps = cap.get(cv2.CAP_PROP_FPS)
                            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                            if fps > 0 and frame_count > 0:
                                duration = frame_count / fps
                                video_length = max(1, int(duration))  # 至少1秒
                                logger.info(f"[wxpad] 获取视频时长成功: {video_length}秒 (FPS: {fps:.2f}, 帧数: {frame_count})")
                            else:
                                logger.warning(f"[wxpad] 无法获取视频时长信息，使用默认值: {video_length}秒")

                            # 读取第一帧
                            ret, frame = cap.read()

                            if not ret:
                                cap.release()
                                raise Exception("无法读取视频帧")

                            # 调整缩略图大小为200x200（与示例脚本一致）
                            frame = cv2.resize(frame, (200, 200))

                            # 保存缩略图
                            cv2.imwrite(thumb_path, frame)

                            # 释放视频对象
                            cap.release()

                            # 验证缩略图文件是否成功生成
                            if not (os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0):
                                raise Exception("缩略图文件生成失败或为空")

                            logger.info(f"[wxpad] 缩略图提取成功: {thumb_path}")
                            logger.info(f"[wxpad] 缩略图文件大小: {os.path.getsize(thumb_path) / 1024:.2f} KB")

                        except ImportError:
                            logger.error(f"[wxpad] OpenCV未安装，无法生成缩略图，停止视频上传")
                            raise Exception("OpenCV未安装，无法生成缩略图")
                        except Exception as e:
                            logger.error(f"[wxpad] 缩略图生成失败: {e}，停止视频上传")
                            raise Exception(f"缩略图生成失败: {e}")



                        # 读取视频和缩略图为base64
                        with open(temp_path, 'rb') as f:
                            video_base64 = base64.b64encode(f.read()).decode('utf-8')

                        # 读取缩略图为base64（缩略图生成已确保成功）
                        with open(thumb_path, 'rb') as f:
                            thumb_data = base64.b64encode(f.read()).decode('utf-8')
                        logger.info(f"[wxpad] 缩略图已准备，大小: {len(thumb_data)} 字符")

                        logger.info(f"[wxpad] 视频Base64大小: {len(video_base64)}, 时长: {video_length}秒")

                        # 使用CDN上传视频（参考示例脚本的成功实现）
                        logger.info(f"[wxpad] 开始上传视频到CDN...")
                        logger.info(f"[wxpad] 包含缩略图数据")

                        # 直接使用base64格式上传（与示例脚本保持一致）
                        upload_result = self.client.cdn_upload_video(
                            thumb_data=thumb_data,
                            to_user_name=receiver,
                            video_data=video_base64  # 修正：直接使用base64字符串格式
                        )

                        # 清理临时文件
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                                logger.debug(f"[wxpad] 已清理临时视频文件: {temp_path}")
                            except Exception as e:
                                logger.warning(f"[wxpad] 清理临时视频文件失败: {e}")

                        # 清理缩略图临时文件
                        if thumb_path and os.path.exists(thumb_path):
                            try:
                                os.remove(thumb_path)
                                logger.debug(f"[wxpad] 已清理临时缩略图文件: {thumb_path}")
                            except Exception as e:
                                logger.warning(f"[wxpad] 清理临时缩略图文件失败: {e}")

                        if upload_result.get("Code") == 200:
                            logger.info(f"[wxpad] 视频URL上传成功")
                            if self.client._is_wechat08():
                                logger.info(f"[wxpad] 视频URL发送成功 {receiver}")
                                return
                            upload_data = upload_result.get("Data", {})

                            # 第二步：转发视频消息
                            forward_video_list = [{
                                "AesKey": upload_data.get("FileAesKey", ""),
                                "CdnThumbLength": upload_data.get("ThumbDataSize", 0),
                                "CdnVideoUrl": upload_data.get("FileID", ""),
                                "Length": upload_data.get("VideoDataSize", len(video_base64)),
                                "PlayLength": video_length,
                                "ToUserName": receiver
                            }]

                            logger.info(f"[wxpad] 开始转发视频消息")

                            forward_result = self.client.forward_video_message(
                                forward_image_list=[],  # 不转发图片
                                forward_video_list=forward_video_list
                            )

                            if forward_result.get("Code") == 200:
                                # 记录更多细节信息
                                forward_data = forward_result.get("Data", [])
                                logger.info(f"[wxpad] 视频URL发送成功 {receiver}")

                                # 检查是否有消息ID等关键信息
                                if forward_data and isinstance(forward_data, list) and len(forward_data) > 0:
                                    first_item = forward_data[0]
                                    if isinstance(first_item, dict):
                                        msg_id = first_item.get("resp", {}).get("MsgId") or first_item.get("resp", {}).get("msgId")
                                        new_msg_id = first_item.get("resp", {}).get("NewMsgId") or first_item.get("resp", {}).get("newMsgId")
                                        if msg_id:
                                            logger.info(f"[wxpad] 视频消息ID: {msg_id}")
                                        if new_msg_id:
                                            logger.info(f"[wxpad] 新消息ID: {new_msg_id}")
                                else:
                                    logger.warning(f"[wxpad] 转发成功但无详细数据返回，可能存在问题")
                            else:
                                logger.error(f"[wxpad] 视频URL转发失败: {forward_result}")
                                # 发送错误消息
                                msg_item = [{
                                    "AtWxIDList": [],
                                    "ImageContent": "",
                                    "MsgType": 0,
                                    "TextContent": "视频发送失败，请稍后再试",
                                    "ToUserName": receiver
                                }]
                                self.client.send_text_message(msg_item)
                        else:
                            logger.error(f"[wxpad] 视频URL上传失败: {upload_result}")
                            msg_item = [{
                                "AtWxIDList": [],
                                "ImageContent": "",
                                "MsgType": 0,
                                "TextContent": "视频上传失败，请稍后再试",
                                "ToUserName": receiver
                            }]
                            self.client.send_text_message(msg_item)

                    except Exception as download_err:
                        logger.error(f"[wxpad] 视频下载失败: {download_err}")
                        msg_item = [{
                            "AtWxIDList": [],
                            "ImageContent": "",
                            "MsgType": 0,
                            "TextContent": "视频下载失败，请稍后再试",
                            "ToUserName": receiver
                        }]
                        self.client.send_text_message(msg_item)
                        # 清理临时文件
                        if temp_path and os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except:
                                pass
                        # 清理缩略图文件
                        thumb_path = temp_path + "_thumb.jpg" if temp_path else None
                        if thumb_path and os.path.exists(thumb_path):
                            try:
                                os.remove(thumb_path)
                            except:
                                pass
                except Exception as e:
                    logger.error(f"[wxpad] 处理视频URL异常: {e}")
                    msg_item = [{
                        "AtWxIDList": [],
                        "ImageContent": "",
                        "MsgType": 0,
                        "TextContent": "处理视频时出错，请稍后再试",
                        "ToUserName": receiver
                    }]
                    self.client.send_text_message(msg_item)

            elif reply.type == ReplyType.VIDEO:
                # 视频消息 - 必须使用两步流程：先上传到CDN，再转发
                if isinstance(reply.content, tuple) and len(reply.content) >= 2:
                    if len(reply.content) == 2:
                        video_data, thumb_data = reply.content
                        play_length = 0  # 默认值，让wxpad自动计算
                    else:
                        video_data, thumb_data, play_length = reply.content
                elif isinstance(reply.content, (BytesIO, bytes)) or hasattr(reply.content, 'read'):
                    # 处理单独的视频数据（BytesIO、bytes或文件对象）
                    video_data = reply.content
                    thumb_data = None  # 需要自动生成缩略图
                    play_length = 0  # 默认值，让wxpad自动计算
                else:
                    logger.error(f"[wxpad] Invalid video content format: {type(reply.content)}")
                    return

                # 处理视频数据 - 统一处理所有格式
                if video_data is not None:
                    # 初始化变量避免作用域问题
                    video_base64 = None
                    thumb_base64 = None

                    # 确保所需模块可用
                    import base64 as b64_module
                    import os
                    import uuid

                    try:
                        # 处理不同类型的视频数据
                        if isinstance(video_data, str):
                            video_base64 = video_data
                        elif isinstance(video_data, BytesIO):
                            # BytesIO对象 - 读取数据并编码
                            video_data.seek(0)  # 重置指针到开头
                            video_bytes = video_data.read()
                            video_base64 = b64_module.b64encode(video_bytes).decode('utf-8')
                        elif isinstance(video_data, bytes):
                            # 字节数据 - 直接编码
                            video_base64 = b64_module.b64encode(video_data).decode('utf-8')
                        elif hasattr(video_data, 'read'):
                            # 文件对象 - 读取数据并编码
                            video_data.seek(0)  # 重置指针到开头
                            video_bytes = video_data.read()
                            video_base64 = b64_module.b64encode(video_bytes).decode('utf-8')
                        else:
                            # 其他类型，尝试直接编码
                            video_base64 = b64_module.b64encode(video_data).decode('utf-8')

                        # 处理缩略图数据 - 如果没有提供则自动生成
                        if not thumb_data or (isinstance(thumb_data, str) and not thumb_data.strip()):
                            logger.info(f"[wxpad] 没有提供缩略图，开始自动生成...")

                            # 需要从video_data生成缩略图
                            temp_dir = TmpDir().path()
                            temp_video_path = os.path.join(temp_dir, f"temp_video_{uuid.uuid4().hex[:8]}.mp4")
                            temp_thumb_path = temp_video_path + "_thumb.jpg"

                            try:
                                # 将视频数据写入临时文件 - 统一处理不同数据类型
                                if isinstance(video_data, str):
                                    # 如果是base64字符串，先解码
                                    video_bytes = b64_module.b64decode(video_data)
                                elif isinstance(video_data, BytesIO):
                                    # BytesIO对象 - 读取字节数据
                                    video_data.seek(0)
                                    video_bytes = video_data.read()
                                elif isinstance(video_data, bytes):
                                    # 字节数据 - 直接使用
                                    video_bytes = video_data
                                elif hasattr(video_data, 'read'):
                                    # 文件对象 - 读取数据
                                    video_data.seek(0)
                                    video_bytes = video_data.read()
                                else:
                                    # 其他类型，尝试直接使用
                                    video_bytes = video_data

                                with open(temp_video_path, 'wb') as f:
                                    f.write(video_bytes)

                                # 使用OpenCV生成缩略图并计算时长
                                import cv2
                                cap = cv2.VideoCapture(temp_video_path)

                                if not cap.isOpened():
                                    raise Exception(f"无法打开临时视频文件: {temp_video_path}")

                                # 计算视频时长
                                fps = cap.get(cv2.CAP_PROP_FPS)
                                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                                if fps > 0 and frame_count > 0:
                                    duration = frame_count / fps
                                    calculated_length = max(1, int(duration))  # 至少1秒
                                    logger.info(f"[wxpad] 计算视频时长: {calculated_length}秒 (FPS: {fps:.2f}, 帧数: {frame_count})")
                                    play_length = calculated_length  # 使用计算的时长
                                else:
                                    logger.warning(f"[wxpad] 无法计算视频时长，使用传入值: {play_length}秒")

                                ret, frame = cap.read()
                                if not ret:
                                    cap.release()
                                    raise Exception("无法读取视频帧")

                                # 调整缩略图大小为200x200
                                frame = cv2.resize(frame, (200, 200))
                                cv2.imwrite(temp_thumb_path, frame)
                                cap.release()

                                # 验证缩略图生成
                                if not (os.path.exists(temp_thumb_path) and os.path.getsize(temp_thumb_path) > 0):
                                    raise Exception("缩略图文件生成失败或为空")

                                # 读取生成的缩略图
                                with open(temp_thumb_path, 'rb') as f:
                                    thumb_base64 = b64_module.b64encode(f.read()).decode('utf-8')

                                logger.info(f"[wxpad] 缩略图自动生成成功")

                                # 清理临时文件
                                try:
                                    os.remove(temp_video_path)
                                    os.remove(temp_thumb_path)
                                except Exception:
                                    pass

                            except ImportError:
                                logger.error(f"[wxpad] OpenCV未安装，无法自动生成缩略图，停止视频上传")
                                thumb_base64 = None  # 确保变量有值
                                raise Exception("OpenCV未安装，无法自动生成缩略图")
                            except Exception as e:
                                logger.error(f"[wxpad] 自动生成缩略图失败: {e}，停止视频上传")
                                thumb_base64 = None  # 确保变量有值
                                # 清理临时文件
                                try:
                                    if os.path.exists(temp_video_path):
                                        os.remove(temp_video_path)
                                    if os.path.exists(temp_thumb_path):
                                        os.remove(temp_thumb_path)
                                except Exception:
                                    pass
                                raise Exception(f"自动生成缩略图失败: {e}")
                        else:
                            # 已提供缩略图数据，但仍需计算时长
                            if isinstance(thumb_data, str):
                                thumb_base64 = thumb_data
                            else:
                                thumb_base64 = b64_module.b64encode(thumb_data).decode('utf-8')
                            logger.info(f"[wxpad] 使用提供的缩略图数据")

                            # 即使有缩略图，也要计算准确的视频时长
                            temp_dir = TmpDir().path()
                            temp_video_path = os.path.join(temp_dir, f"temp_video_{uuid.uuid4().hex[:8]}.mp4")

                            try:
                                # 将视频数据写入临时文件用于时长计算 - 统一处理不同数据类型
                                if isinstance(video_data, str):
                                    video_bytes = b64_module.b64decode(video_data)
                                elif isinstance(video_data, BytesIO):
                                    video_data.seek(0)
                                    video_bytes = video_data.read()
                                elif isinstance(video_data, bytes):
                                    video_bytes = video_data
                                elif hasattr(video_data, 'read'):
                                    video_data.seek(0)
                                    video_bytes = video_data.read()
                                else:
                                    video_bytes = video_data

                                with open(temp_video_path, 'wb') as f:
                                    f.write(video_bytes)

                                # 使用OpenCV计算时长
                                import cv2
                                cap = cv2.VideoCapture(temp_video_path)

                                if cap.isOpened():
                                    fps = cap.get(cv2.CAP_PROP_FPS)
                                    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                                    if fps > 0 and frame_count > 0:
                                        duration = frame_count / fps
                                        calculated_length = max(1, int(duration))
                                        logger.info(f"[wxpad] 重新计算视频时长: {calculated_length}秒 (原传入值: {play_length}秒)")
                                        play_length = calculated_length  # 使用计算的时长
                                    else:
                                        logger.warning(f"[wxpad] 无法计算视频时长，使用传入值: {play_length}秒")
                                    cap.release()
                                else:
                                    logger.warning(f"[wxpad] 无法打开视频文件计算时长，使用传入值: {play_length}秒")

                                # 清理临时文件
                                try:
                                    if os.path.exists(temp_video_path):
                                        os.remove(temp_video_path)
                                except Exception:
                                    pass

                            except Exception as e:
                                logger.warning(f"[wxpad] 时长计算失败: {e}，使用传入值: {play_length}秒")
                                # 清理临时文件
                                try:
                                    if os.path.exists(temp_video_path):
                                        os.remove(temp_video_path)
                                except Exception:
                                    pass

                        # 使用CDN上传视频（参考示例脚本的成功实现）
                        logger.info(f"[wxpad] 开始上传视频到CDN...")

                        # 确保变量已定义
                        if video_base64 is None or thumb_base64 is None:
                            logger.error(f"[wxpad] 视频或缩略图数据未正确初始化")
                            raise Exception("视频或缩略图数据未正确初始化")

                        # MAX 使用单步 SendVideo；旧 PadPro 保留“上传后转发”的两步流程。
                        if self.client._is_wechat08():
                            upload_result = self.client.send_video(
                                video_data=video_base64,
                                image_data=thumb_base64,
                                play_length=play_length,
                                to_user_name=receiver,
                            )
                        else:
                            upload_result = self.client.cdn_upload_video(
                                thumb_data=thumb_base64,
                                to_user_name=receiver,
                                video_data=video_base64
                            )

                        if upload_result.get("Code") == 200:
                            logger.info(f"[wxpad] 视频上传成功")
                            if self.client._is_wechat08():
                                logger.info(f"[wxpad] 视频发送成功 {receiver}")
                                return
                            upload_data = upload_result.get("Data", {})

                            # 第二步：转发视频消息
                            forward_video_list = [{
                                "AesKey": upload_data.get("FileAesKey", ""),
                                "CdnThumbLength": upload_data.get("ThumbDataSize", 0),
                                "CdnVideoUrl": upload_data.get("FileID", ""),
                                "Length": upload_data.get("VideoDataSize", len(video_base64) if video_base64 else 0),
                                "PlayLength": play_length,
                                "ToUserName": receiver
                            }]

                            logger.info(f"[wxpad] 开始转发视频消息...")

                            forward_result = self.client.forward_video_message(
                                forward_image_list=[],  # 不转发图片
                                forward_video_list=forward_video_list
                            )

                            if forward_result.get("Code") == 200:
                                forward_data = forward_result.get("Data", [])
                                logger.info(f"[wxpad] 视频发送成功 {receiver}")

                                # 检查消息ID
                                if forward_data and isinstance(forward_data, list) and len(forward_data) > 0:
                                    first_item = forward_data[0]
                                    if isinstance(first_item, dict):
                                        msg_id = first_item.get("resp", {}).get("MsgId") or first_item.get("resp", {}).get("msgId")
                                        new_msg_id = first_item.get("resp", {}).get("NewMsgId") or first_item.get("resp", {}).get("newMsgId")
                                        if msg_id:
                                            logger.info(f"[wxpad] 视频消息ID: {msg_id}")
                                        if new_msg_id:
                                            logger.info(f"[wxpad] 新消息ID: {new_msg_id}")
                                else:
                                    logger.warning(f"[wxpad] 转发成功但无详细数据返回，可能存在问题")
                            else:
                                logger.error(f"[wxpad] 视频转发失败: {forward_result}")
                        else:
                            logger.error(f"[wxpad] 视频上传失败: {upload_result}")
                    except Exception as e:
                        logger.error(f"[wxpad] 视频发送异常 {e}")
                        import traceback
                        logger.error(f"[wxpad] 详细错误信息: {traceback.format_exc()}")
                else:
                    logger.error(f"[wxpad] Invalid video content format: {type(reply.content)}")

            elif reply.type == ReplyType.EMOJI:
                # 表情消息
                if isinstance(reply.content, tuple) and len(reply.content) == 2:
                    md5, total_len = reply.content
                    emoji_list = [{
                        "EmojiMd5": md5,
                        "EmojiSize": total_len,
                        "ToUserName": receiver
                    }]
                    result = self.client.send_emoji_message(emoji_list)
                    if result.get("Code") == 200:
                        logger.info(f"[wxpad] send emoji to {receiver}")
                    else:
                        logger.error(f"[wxpad] send emoji failed: {result}")
                else:
                    logger.error(f"[wxpad] Invalid emoji content format: {type(reply.content)}")

            elif reply.type == ReplyType.CARD:
                # 名片消息
                if isinstance(reply.content, tuple) and len(reply.content) >= 2:
                    if len(reply.content) == 2:
                        card_wxid, card_nickname = reply.content
                        card_alias = ""
                    else:
                        card_wxid, card_nickname, card_alias = reply.content
                    result = self.client.share_card_message(
                        card_alias=card_alias,
                        card_flag=1,  # 默认标志
                        card_nick_name=card_nickname,
                        card_wx_id=card_wxid,
                        to_user_name=receiver
                    )
                    if result.get("Code") == 200:
                        logger.info(f"[wxpad] send card to {receiver}")
                    else:
                        logger.error(f"[wxpad] send card failed: {result}")
                else:
                    logger.error(f"[wxpad] Invalid card content format: {type(reply.content)}")

            elif reply.type == ReplyType.LINK:
                # 链接消息
                if isinstance(reply.content, str):
                    # 如果是XML字符串，使用正确的API格式发送
                    logger.debug(f"[wxpad] 发送应用消息，XML长度: {len(reply.content)}")
                    app_list = [{
                        "ContentType": 0,
                        "ContentXML": reply.content,
                        "ToUserName": receiver
                    }]
                    result = self.client.send_app_message(app_list)
                    if result.get("Code") == 200:
                        logger.info(f"[wxpad] send link to {receiver}")
                    else:
                        logger.error(f"[wxpad] send link failed: {result}")
                        raise Exception(f"应用消息发送失败 {result}")
                elif isinstance(reply.content, tuple) and len(reply.content) >= 3:
                    # 如果是元组，构造XML
                    title, description, url, thumb_url = reply.content
                    xml = f"""<appmsg appid="" sdkver="0">
                    <title>{title}</title>
                    <des>{description}</des>
                    <url>{url}</url>
                    <thumburl>{thumb_url}</thumburl>
                    <type>5</type>
                    </appmsg>"""
                    logger.debug(f"[wxpad] 发送链接卡片，标题: {title}")
                    app_list = [{
                        "ContentType": 0,
                        "ContentXML": xml,
                        "ToUserName": receiver
                    }]
                    result = self.client.send_app_message(app_list)
                    if result.get("Code") == 200:
                        logger.info(f"[wxpad] send link to {receiver}")
                    else:
                        logger.error(f"[wxpad] send link failed: {result}")
                        raise Exception(f"链接卡片发送失败 {result}")
                else:
                    logger.error(f"[wxpad] Invalid link content format: {type(reply.content)}")
                    raise Exception(f"无效的链接内容格式 {type(reply.content)}")

            elif reply.type == ReplyType.REVOKE:
                # 撤回消息
                if isinstance(reply.content, tuple) and len(reply.content) == 3:
                    client_msg_id, create_time, new_msg_id = reply.content
                    result = self.client.revoke_msg(
                        client_msg_id=client_msg_id,
                        create_time=create_time,
                        new_msg_id=new_msg_id,
                        to_user_name=receiver
                    )
                    if result.get("Code") == 200:
                        logger.info(f"[wxpad] revoke msg from {receiver}")
                    else:
                        logger.error(f"[wxpad] revoke msg failed: {result}")
                else:
                    logger.error(f"[wxpad] Invalid revoke content format: {type(reply.content)}")

            else:
                logger.warning(f"[wxpad] Unsupported reply type: {reply.type}")

        except Exception as e:
            logger.error(f"[wxpad] Failed to send {reply.type} to {receiver}: {e}")
            # 尝试发送错误消息
            try:
                error_msg = f"消息发送失败 {e}"
                msg_item = [{
                    "AtWxIDList": [],
                    "ImageContent": "",
                    "MsgType": 0,
                    "TextContent": error_msg,
                    "ToUserName": receiver
                }]
                self.client.send_text_message(msg_item)
            except Exception as e2:
                logger.error(f"[wxpad] Failed to send error message: {e2}")

    def send_image(self, image_data, to_wxid):
        """发送图片，支持多种数据格式，直接转换为base64发送

        Args:
            image_data: 图片数据，支持以下格式：
                - str: 本地文件路径、图片URL或base64数据
                - bytes: 二进制图片数据
                - BytesIO: 内存中的图片数据
                - PIL.Image: PIL图片对象
            to_wxid: 接收者微信ID

        Returns:
            bool: 发送是否成功
        """
        temp_file_path = None
        try:
            # 根据数据类型处理图片，直接转换为base64
            image_base64 = None

            if isinstance(image_data, str):
                # 检查是否为URL
                if image_data.startswith(('http://', 'https://')):
                    # 直接下载到内存，不保存临时文件
                    try:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        }
                        response = requests.get(image_data, headers=headers, timeout=30)
                        response.raise_for_status()
                        image_base64 = base64.b64encode(response.content).decode("utf-8")
                    except Exception as e:
                        logger.error(f"[send_image] 下载图片失败: {e}")
                        return False

                elif image_data.startswith('data:image/'):
                    # base64格式: data:image/jpeg;base64,/9j/4AAQ...
                    if ',' in image_data:
                        image_base64 = image_data.split(',', 1)[1]
                    else:
                        image_base64 = image_data

                elif len(image_data) > 100 and image_data.replace('+', '').replace('/', '').replace('=', '').isalnum():
                    # 可能是纯base64字符
                    image_base64 = image_data

                else:
                    # 本地文件路径
                    if not os.path.exists(image_data):
                        logger.error(f"[send_image] 本地图片文件不存在 {image_data}")
                        return False

                    # 预处理图片：确保格式兼容
                    try:
                        from PIL import Image
                        import io

                        # 用PIL重新处理图片
                        img = Image.open(image_data)

                        # 转换为RGB模式（确保兼容性）
                        if img.mode != 'RGB':
                            img = img.convert('RGB')

                        # 重新编码为JPEG格式（确保兼容性）
                        output = io.BytesIO()
                        img.save(output, format='JPEG', quality=85, optimize=True)
                        processed_bytes = output.getvalue()

                        image_base64 = base64.b64encode(processed_bytes).decode('utf-8')

                    except Exception as e:
                        # 如果格式转换失败，使用原始数据
                        logger.warning(f"[send_image] 图片格式转换失败，使用原始数据 {e}")
                        with open(image_data, "rb") as f:
                            file_content = f.read()
                            image_base64 = base64.b64encode(file_content).decode("utf-8")

            elif isinstance(image_data, bytes):
                # 二进制数据 - 直接转换
                image_base64 = base64.b64encode(image_data).decode("utf-8")

            elif isinstance(image_data, BytesIO):
                # BytesIO对象 - 直接读取转换
                image_data.seek(0)  # 重置指针到开头
                bytesio_content = image_data.read()
                image_base64 = base64.b64encode(bytesio_content).decode("utf-8")

            elif hasattr(image_data, 'read') and hasattr(image_data, 'seek'):
                # BufferedReader或其他类似的文件对象
                image_data.seek(0)  # 重置指针到开头
                file_content = image_data.read()
                image_base64 = base64.b64encode(file_content).decode("utf-8")
                logger.debug(f"[send_image] 处理文件对象类型: {type(image_data)}, 大小: {len(file_content)}字节")

            elif hasattr(image_data, 'save') and hasattr(image_data, 'format'):
                # PIL.Image对象 - 转换为BytesIO后再转base64
                img_format = image_data.format or 'JPEG'
                if img_format.upper() == 'JPEG' and image_data.mode in ('RGBA', 'LA', 'P'):
                    # JPEG不支持透明度，转换为RGB
                    rgb_image = Image.new('RGB', image_data.size, (255, 255, 255))
                    if image_data.mode == 'P':
                        image_data = image_data.convert('RGBA')
                    rgb_image.paste(image_data, mask=image_data.split()[-1] if image_data.mode == 'RGBA' else None)
                    image_data = rgb_image

                # 保存到内存中的BytesIO对象
                img_buffer = BytesIO()
                image_data.save(img_buffer, format=img_format)
                img_buffer.seek(0)
                buffer_content = img_buffer.read()

                image_base64 = base64.b64encode(buffer_content).decode("utf-8")
                img_buffer.close()

            else:
                logger.error(f"[send_image] 不支持的图片数据类型: {type(image_data)}")
                return False

            if not image_base64:
                logger.error(f"[send_image] 无法获取图片base64数据")
                return False

            # 验证Base64数据完整
            if len(image_base64) < 100:  # Base64数据太短
                logger.error(f"[send_image] Base64数据过短，可能有问题: {len(image_base64)}字符")
                return False

            # 验证Base64格式
            try:
                # 尝试解码验证
                test_decode = base64.b64decode(image_base64)
                if len(test_decode) < 50:  # 解码后数据太短
                    logger.error(f"[send_image] 解码后数据过短，可能有问题: {len(test_decode)}字节")
                    return False
            except Exception as e:
                logger.error(f"[send_image] Base64数据格式验证失败: {e}")
                return False

            # 使用client API发送图片
            msg_item = [{
                "AtWxIDList": [],
                "ImageContent": image_base64,
                "MsgType": 3,  # 图片消息类型
                "TextContent": "",
                "ToUserName": to_wxid
            }]

            # 尝试使用新的图片发送接口
            result = self.client.send_image_new_message(msg_item)

            if result.get("Code") == 200:
                # 检查详细的响应数据 - 新API响应格式
                data = result.get("Data", [])
                if data and isinstance(data, list) and len(data) > 0:
                    first_item = data[0]
                    if isinstance(first_item, dict):
                        # 新API使用resp.baseResponse.ret来判断成功状态
                        resp_data = first_item.get("resp", {})
                        if resp_data:
                            base_response = resp_data.get("baseResponse", {})
                            ret_code = base_response.get("ret", -1)

                            # 新API：ret=0表示成功
                            if ret_code != 0:
                                logger.warning(f"[send_image] 图片发送失败 ret={ret_code}")
                                return False
                        else:
                            # 兼容旧API格式
                            is_success = first_item.get("isSendSuccess", False)
                            if not is_success:
                                err_msg = first_item.get("errMsg", "")
                                logger.warning(f"[send_image] 图片发送失败 {err_msg}")
                                return False

                logger.info(f"[wxpad] ✅ 发送图片到 {to_wxid}")
                return True
            else:
                logger.error(f"[send_image] 图片发送失败 Code={result.get('Code')}, Text={result.get('Text', '')}")
                return False

        except Exception as e:
            logger.error(f"[send_image] 发送图片异常 {e}")
            return False



    async def _send_voice(self, to_user_id, voice_file_path_segment):
        """发送语音消息，自动转换为SILK格式

        Args:
            to_user_id: 接收者ID
            voice_file_path_segment: 语音文件路径

        Returns:
            dict: 包含Success字段的结果数据
        """
        try:
            import os
            import base64
            import time
            import traceback
            from common.tmp_dir import TmpDir

            if not to_user_id:
                logger.error("[wxpad] Send voice failed: receiver ID is empty")
                return {"Success": False, "Message": "Receiver ID empty"}
            if not os.path.exists(voice_file_path_segment):
                logger.error(f"[wxpad] Send voice failed: voice segment file not found at {voice_file_path_segment}")
                return {"Success": False, "Message": f"Voice segment not found: {voice_file_path_segment}"}

            # 微信语音条只支持SILK格式，需要转换
            silk_file_path = None
            temp_files_to_clean = []

            try:
                # 检查是否已经是SILK格式
                if voice_file_path_segment.lower().endswith(('.silk', '.sil', '.slk')):
                    silk_file_path = voice_file_path_segment
                    # 对于已有的SILK文件，尝试获取时长
                    try:
                        import pilk
                        duration_ms = pilk.get_duration(silk_file_path)
                        duration_seconds = max(1, int(duration_ms / 1000))
                        logger.debug(f"[wxpad] 文件已是SILK格式: {voice_file_path_segment}, 时长={duration_seconds}秒")
                    except Exception as e:
                        duration_seconds = 10  # 默认10秒
                        logger.warning(f"[wxpad] 无法获取SILK文件时长，使用默认10秒 {e}")
                else:
                    # 转换为SILK格式
                    from voice.audio_convert import any_to_sil

                    # 创建临时SILK文件
                    temp_dir = TmpDir().path()
                    silk_filename = f"voice_{int(time.time())}_{os.path.basename(voice_file_path_segment)}.silk"
                    silk_file_path = os.path.join(temp_dir, silk_filename)
                    temp_files_to_clean.append(silk_file_path)

                    logger.info(f"[wxpad] 转换语音为SILK格式: {voice_file_path_segment} -> {silk_file_path}")

                    # 执行转换
                    duration_ms = any_to_sil(voice_file_path_segment, silk_file_path)
                    duration_seconds = max(1, int(duration_ms / 1000))
                    logger.info(f"[wxpad] SILK转换成功: 时长={duration_ms}ms ({duration_seconds}秒)")

                # 读取SILK文件并转换为base64
                with open(silk_file_path, "rb") as f:
                    silk_data = f.read()
                    silk_base64 = base64.b64encode(silk_data).decode()

                # 使用xbot协议发送SILK语音
                logger.info(f"[wxpad] 发送SILK语音: 接收者{to_user_id}, 时长={duration_seconds}秒 大小={len(silk_data)}字节")

                # 验证SILK文件质量
                if len(silk_data) < 100:  # SILK文件过小可能有问题
                    logger.warning(f"[wxpad] SILK文件可能过小: {len(silk_data)}字节")

                # 确保时长合理（至多60秒，最少1秒）
                duration_seconds = max(1, min(60, duration_seconds))

                result = self.client.send_voice(
                    to_user_name=to_user_id,
                    voice_data=silk_base64,
                    voice_format=4,  # 修正：SILK格式使用1而不使用4
                    voice_second=duration_seconds
                )

                if result.get("Code") == 200:
                    logger.info(f"[wxpad] 发送SILK语音消息成功: 接收者 {to_user_id}")
                    return {"Success": True, "Data": result.get("Data", {})}
                else:
                    logger.error(f"[wxpad] 发送SILK语音消息失败: {result}")
                    return {"Success": False, "Error": f"API返回错误: {result}"}

            except Exception as e:
                logger.error(f"[wxpad] 发送语音消息失败 {e}")
                return {"Success": False, "Error": str(e)}

            finally:
                # 清理临时文件
                for temp_file in temp_files_to_clean:
                    try:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                            logger.debug(f"[wxpad] 清理临时SILK文件: {temp_file}")
                    except Exception as cleanup_e:
                        logger.warning(f"[wxpad] 清理临时文件失败: {temp_file}, 错误: {cleanup_e}")

        except Exception as e:
            logger.error(f"[wxpad] Exception in _send_voice for {voice_file_path_segment} to {to_user_id}: {e}")
            logger.error(traceback.format_exc())
            return {"Success": False, "Message": f"General exception in _send_voice: {e}"}
