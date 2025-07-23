# -*- coding: utf-8 -*-
import base64
import json
import os
import threading
import time
import urllib.parse
import uuid
from functools import wraps
from typing import Optional
from io import BytesIO

import qrcode
import requests
import websocket
import asyncio

from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.dpbot.dpbot_message import DpBotMessage
from common.log import logger
from common.singleton import singleton
from common.tmp_dir import TmpDir
from config import conf
from lib.dpbot.client import DPBotClient

# 检查pysilk库是否可用
try:
    import pysilk
    PYSLIK_AVAILABLE = True
except ImportError:
    PYSLIK_AVAILABLE = False
    pysilk = None
    logger.warning("[wxpad] pysilk库未安装，语音转换功能可能受限。")

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

MAX_UTF8_LEN = 2048
ROBOT_STAT_PATH = os.path.join(os.path.dirname(__file__), '../../resource/robot_stat.json')
ROBOT_STAT_PATH = os.path.abspath(ROBOT_STAT_PATH)


def logged_in(func):
    """
    装饰器：确保执行操作时机器人已登录。
    """
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.wxid or not self.client:
            logger.error("[dpbot] Robot not logged in, cannot perform action.")
            return None
        return func(self, *args, **kwargs)
    return wrapper


@singleton
class DpBotChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.base_url = conf().get("dpbot_base_url")
        if not self.base_url:
            raise ValueError("dpbot_base_url is not configured in config.json")
            
        self.client = DPBotClient(self.base_url)
        self.robot_stat = {}
        self.wxid = None
        self.device_id = None
        self.device_name = None
        
        # 缓存
        self.user_info_cache = {} # {user_id: {info}}
        self.room_members_cache = {} # {room_id: {user_id: {info}}}

        # WebSocket相关
        self.ws = None
        self.ws_connected = False
        self.ws_reconnect_count = 0
        self.max_reconnect_attempts = 5
        logger.info(f"[dpbot] Initialized with base_url: {self.base_url}")

    def startup(self):
        self._ensure_login()
        if self.wxid:
            logger.info(f"[dpbot] Channel startup successful, wxid: {self.wxid}")
            # 启动WebSocket消息同步循环
            threading.Thread(target=self._sync_message_loop, daemon=True).start()
        else:
            logger.error("[dpbot] Startup failed: could not log in.")

    def _ensure_login(self):
        """
        增强版登录流程：
        1. 优先从本地文件恢复会话。
        2. 尝试通过心跳和二次登录验证并恢复会话。
        3. 如果恢复失败，则进行扫码登录。
        """
        stat = DPBotClient.load_robot_stat(ROBOT_STAT_PATH)
        if stat and stat.get("wxid"):
            self.wxid = stat["wxid"]
            self.device_id = stat.get("device_id")
            self.device_name = stat.get("device_name")
            logger.info(f"[dpbot] Loaded session from file: wxid={self.wxid}")

            # 步骤2.1: 尝试心跳验证
            try:
                if self.client.heart_beat(self.wxid).get("Success"):
                    logger.info("[dpbot] Heartbeat successful, session is active.")
                    self.robot_stat = stat
                    return
                else:
                    logger.warning("[dpbot] Heartbeat failed, attempting to re-login with twice_login.")
            except Exception as e:
                logger.warning(f"[dpbot] Heartbeat check failed with exception: {e}. Attempting to re-login.")

            # 步骤2.2: 尝试二次登录恢复
            try:
                if self.client.twice_login(self.wxid).get("Success"):
                    logger.info("[dpbot] twice_login successful, session recovered.")
                    self.robot_stat = stat
                    return
                else:
                    logger.warning("[dpbot] twice_login failed. Proceeding to QR code login.")
            except Exception as e:
                logger.warning(f"[dpbot] twice_login failed with exception: {e}. Proceeding to QR code login.")

        logger.info("[dpbot] No valid session found or session expired. Starting QR code login.")
        self._login_with_qr()
        
    def _login_with_qr(self):
        """
        执行扫码登录流程。
        """
        self.device_id = str(uuid.uuid4())
        self.device_name = f"DpBot_{self.device_id[:8]}"
        try:
            qr_resp = self.client.get_qr(self.device_id, self.device_name, login_type="ipad")
            uuid_code = qr_resp.get("Data", {}).get("Uuid")
            qr_url = qr_resp.get("Data", {}).get("QrUrl")
            if not qr_url or not uuid_code:
                logger.error(f"[dpbot] Failed to get QR code: {qr_resp}")
                raise Exception("Could not get QR code URL or UUID.")
        except Exception as e:
            logger.error(f"[dpbot] Exception when getting QR code: {e}")
            raise e

        logger.info(f"[dpbot] Please scan QR code to log in: {qr_url}")
        try:
            import sys
            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_url)
            qr.make(fit=True)
            qr.print_ascii(out=sys.stdout)
        except Exception as e:
            logger.warning(f"[dpbot] Failed to render QR code in console: {e}")

        # 轮询检查扫码状态
        for i in range(240):
            try:
                check = self.client.check_qr(uuid_code)
                data = check.get("Data", {})
                message = check.get("Message", "")
                
                if message == "登录成功":
                    wxid = data.get("acctSectResp", {}).get("userName") or data.get("Wxid")
                    if not wxid:
                        logger.error(f"[dpbot] Login successful but cannot get wxid from response: {data}")
                        raise Exception("Login failed: could not parse wxid.")
                        
                    self.wxid = wxid
                    self.robot_stat = {"wxid": wxid, "device_id": self.device_id, "device_name": self.device_name}
                    DPBotClient.save_robot_stat(ROBOT_STAT_PATH, self.robot_stat)
                    logger.info(f"[dpbot] Login successful: wxid={self.wxid}")
                    return
                elif data.get("Status") == 1 or data.get("status") == 1:
                    logger.info(f"[dpbot] QR code scanned, waiting for confirmation...")
                else:
                    logger.info(f"[dpbot] Waiting for QR code scan... {240 - i}s left.")
            except Exception as e:
                logger.error(f"[dpbot] Error checking QR status: {e}")
            time.sleep(1)
        raise Exception("Login timeout. Please restart the program and try again.")

    def _sync_message_loop(self):
        """
        WebSocket消息同步循环，包含连接和重连逻辑。
        """
        logger.info("[dpbot] Starting WebSocket message sync loop.")
        while True:
            try:
                if not self.ws_connected:
                    self._connect_websocket()
                # 保持循环，WebSocket会在其自身线程中运行
                time.sleep(5)
            except Exception as e:
                logger.error(f"[dpbot] Error in WebSocket sync loop: {e}", exc_info=True)
                self.ws_connected = False
                self._handle_reconnect() # 出现异常时也触发重连

    def _connect_websocket(self):
        """建立WebSocket连接并阻塞运行。"""
        if self.ws_connected:
            return

        try:
            ws_url = self._get_websocket_url()
            if not ws_url:
                logger.error("[dpbot] Cannot get WebSocket URL, wxid might be missing.")
                return

            logger.info(f"[dpbot] Connecting to WebSocket: {ws_url}")
            self.ws = websocket.WebSocketApp(
                ws_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close
            )
            # 在当前线程阻塞运行，回调函数会在WebSocketApp的内部线程中被调用
            self.ws.run_forever()

        except Exception as e:
            logger.error(f"[dpbot] WebSocket connection failed: {e}")
            self.ws_connected = False
            self._handle_reconnect()

    def _get_websocket_url(self):
        """
        根据base_url构建WebSocket连接URL。
        增加了对wxid的查询参数，以便服务端识别。
        """
        if not self.wxid:
            return None
        
        base_ws_url = self.base_url.rstrip('/')
        if base_ws_url.startswith('http://'):
            base_ws_url = base_ws_url.replace('http://', 'ws://')
        elif base_ws_url.startswith('https://'):
            base_ws_url = base_ws_url.replace('https://', 'wss://')
        
        # 根据用户提供的信息，WebSocket端点格式为 /ws/{wxid}
        ws_url = f"{base_ws_url}/ws/{self.wxid}"
        return ws_url

    def _on_ws_open(self, ws):
        """WebSocket连接打开回调"""
        logger.info("[dpbot] WebSocket connection established.")
        self.ws_connected = True
        self.ws_reconnect_count = 0

    def _on_ws_message(self, ws, message):
        """WebSocket消息接收回调"""
        try:
            logger.debug(f"[dpbot] 收到WebSocket消息: {message}")
            msg_data = json.loads(message)

            # 兼容 {"data": ...} 的包装格式
            if isinstance(msg_data, dict) and 'data' in msg_data:
                actual_message = msg_data['data']
            else:
                actual_message = msg_data

            # 统一处理单条和多条消息
            if isinstance(actual_message, list):
                messages_to_process = actual_message
            else:
                messages_to_process = [actual_message]
            
            for i, msg_payload in enumerate(messages_to_process):
                try:
                    # 关键：在这里进行消息格式转换
                    standard_msg = self._convert_message(msg_payload)
                    
                    # 简化日志
                    log_from = standard_msg.get('FromUserName', 'Unknown')
                    log_type = standard_msg.get('MsgType', 'Unknown')
                    logger.info(f"[dpbot] 处理消息 {i+1}/{len(messages_to_process)}: from={log_from}, type={log_type}")
                    
                    # 使用转换后的消息进行处理
                    self._handle_message(standard_msg)
                    
                except Exception as e:
                    logger.error(f"[dpbot] 处理单条消息异常: {e}", exc_info=True)

        except json.JSONDecodeError:
            logger.warning(f"[dpbot] 无法解析WebSocket消息: {message}")
        except Exception as e:
            logger.error(f"[dpbot] 处理WebSocket消息流异常: {e}", exc_info=True)

    def _on_ws_error(self, ws, error):
        """WebSocket错误回调"""
        logger.error(f"[dpbot] WebSocket error: {error}")
        self.ws_connected = False

    def _on_ws_close(self, ws, close_status_code, close_msg):
        """WebSocket连接关闭回调"""
        logger.warning(f"[dpbot] WebSocket连接已关闭: {close_status_code}, {close_msg}")
        self.ws_connected = False

    def _handle_reconnect(self):
        """处理WebSocket重连逻辑"""
        if self.ws_reconnect_count < self.max_reconnect_attempts:
            self.ws_reconnect_count += 1
            wait_time = min(2 ** self.ws_reconnect_count, 60) # 指数退避，最长60秒
            logger.info(f"[dpbot] Attempting to reconnect WebSocket in {wait_time}s... ({self.ws_reconnect_count}/{self.max_reconnect_attempts})")
            time.sleep(wait_time)
        else:
            logger.error("[dpbot] WebSocket reconnect attempts exceeded. Stopping automatic reconnection.")

    def _extract_str(self, value):
        """安全地从 {'string': '...'} 或 {'str': '...'} 结构中提取字符串值"""
        if isinstance(value, dict):
            return value.get('string', value.get('str', ''))
        return str(value or '')

    def _convert_message(self, msg: dict) -> dict:
        """
        将从WebSocket接收的原始消息(data部分)转换为wxpad兼容的标准化格式。
        """
        # 提取关键字段，使用CamelCase
        from_user = self._extract_str(msg.get('FromUserName', {}))
        to_user = self._extract_str(msg.get('ToUserName', {}))
        content = msg.get('Content', {})  # content可能仍是复杂结构，交由Message类处理

        # 确定MsgType
        msg_type = msg.get('MsgType', 1)

        return {
            'FromUserName': from_user,
            'ToUserName': to_user,
            'Content': content,
            'MsgType': msg_type,
            'CreateTime': msg.get('CreateTime', int(time.time())),
            'MsgSource': msg.get('MsgSource', ''),
            'MsgId': msg.get('MsgId', 0),
            'NewMsgId': msg.get('NewMsgId', 0)
        }

    def _should_ignore_message(self, xmsg: DpBotMessage) -> bool:
        """统一的消息过滤检查"""
        # 1. 过期消息检查 (5分钟)
        if hasattr(xmsg, 'create_time') and xmsg.create_time:
            try:
                current_time = int(time.time())
                msg_time = int(xmsg.create_time)
                if msg_time < current_time - 60 * 5:  # 5分钟过期
                    logger.debug(f"[dpbot] ignore expired message from {xmsg.from_user_id}")
                    return True
            except (ValueError, TypeError):
                pass  # 时间格式无效时继续处理

        # 2. 非用户消息过滤
        if xmsg.ctype == ContextType.NON_USER_MSG:
            logger.debug(f"[dpbot] ignore non-user/system message from {xmsg.from_user_id}")
            return True

        # 3. 自己发送的消息过滤
        if hasattr(xmsg, 'from_user_id') and xmsg.from_user_id == self.wxid:
            logger.debug(f"[dpbot] ignore message from myself: {xmsg.from_user_id}")
            return True

        # 4. 语音消息配置检查
        if xmsg.ctype == ContextType.VOICE and not conf().get("speech_recognition", False):
            logger.debug(f"[dpbot] ignore voice message, speech_recognition disabled")
            return True

        # 5. 空消息ID过滤
        if not xmsg.msg_id:
            logger.debug(f"[dpbot] ignore message with no msg_id")
            return True

        return False

    def _handle_message(self, raw_msg: dict):
        """
        处理单条消息的核心方法。
        采用与wxpad一致的消息处理逻辑。
        """
        # 兼容内层数据结构，例如 {"data": {...real_msg...}}
        if 'data' in raw_msg and isinstance(raw_msg['data'], dict):
            final_msg_data = raw_msg['data']
        else:
            final_msg_data = raw_msg

        msg = DpBotMessage(final_msg_data, self.client)
        
        # 统一过滤检查
        if self._should_ignore_message(msg):
            # 简化过滤日志显示，避免重复API调用
            logger.debug(f"[dpbot] 消息被过滤: from={msg.from_user_id}, reason=过滤规则")
            return

        # 格式化有效消息日志显示
        if msg.is_group:
            # 直接使用消息对象中已获取的群名称，避免重复调用API
            group_name = getattr(msg, 'other_user_nickname', None)  # 对于群聊，other_user_nickname就是群名称
            group_info = f"群聊[{group_name or msg.from_user_id}]({msg.from_user_id})"
            
            # 获取实际发言人信息（如果有的话）
            actual_user_info = ""
            if hasattr(msg, 'actual_user_id') and msg.actual_user_id and msg.actual_user_id != msg.from_user_id:
                # 直接使用消息对象中已获取的昵称，避免重复调用API
                actual_nickname = getattr(msg, 'actual_user_nickname', None)
                actual_user_info = f" 发言人: {actual_nickname or msg.actual_user_id}({msg.actual_user_id})"
            logger.info(f"[dpbot] 📨 {group_info}{actual_user_info}: {msg.content[:50] if msg.content else 'None'}")
        else:
            # 直接使用消息对象中已获取的昵称，避免重复调用API
            user_nickname = getattr(msg, 'other_user_nickname', None)
            user_info = f"{user_nickname or msg.from_user_id}({msg.from_user_id})"
            logger.info(f"[dpbot] 💬 {user_info}: {msg.content[:50] if msg.content else 'None'}")

        # 如果是图片、视频、文件、语音消息，需要立即处理下载（这些是主要内容）
        if msg.ctype == ContextType.IMAGE:
            logger.debug(f"[dpbot] 检测到图片消息，开始下载处理")
            msg.prepare()  # 触发图片下载

        elif msg.ctype == ContextType.VIDEO:
            logger.debug(f"[dpbot] 检测到视频消息，开始下载处理")
            msg.prepare()  # 触发视频下载
            
        elif msg.ctype == ContextType.FILE:
            logger.debug(f"[dpbot] 检测到文件消息，开始下载处理")
            msg.prepare()  # 触发文件下载

        elif msg.ctype == ContextType.VOICE:
            logger.debug(f"[dpbot] 检测到语音消息，开始下载处理")
            msg.prepare()  # 触发语音下载

        # 处理消息
        context = self._compose_context(msg.ctype, msg.content, msg=msg, isgroup=msg.is_group)
        if context is not None:
            # 只有成功生成上下文后，才处理引用图片/文件的下载和缓存
            # 如果是引用图片的文本消息，也需要准备引用图片
            if msg.ctype == ContextType.TEXT and hasattr(msg, '_refer_image_info') and msg._refer_image_info.get('has_refer_image'):
                logger.debug(f"[dpbot] 检测到引用图片的文本消息，开始准备引用图片")
                msg.prepare()  # 触发引用图片下载和缓存

            # 如果是引用文件的文本消息，也需要准备引用文件
            elif msg.ctype == ContextType.TEXT and hasattr(msg, '_refer_file_info') and msg._refer_file_info.get('has_refer_file'):
                logger.debug(f"[dpbot] 检测到引用文件的文本消息，开始准备引用文件")
                msg.prepare()  # 触发引用文件下载和缓存
            
            logger.info(f"[dpbot] 消息已提交处理")
            self.produce(context)
        else:
            logger.warning(f"[dpbot] 无法生成上下文，消息类型: {msg.ctype}")

    def send(self, reply: Reply, context: Context):
        """发送消息到微信
        
        Args:
            reply: 回复对象
            context: 上下文对象
        """
        import os  # 将os导入移到方法开头
        
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
            logger.error(f"[dpbot] Cannot determine receiver for reply: {reply.type}")
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
            
        logger.debug(f"[dpbot] Sending {reply.type} to {receiver_info}")
        
        try:
            if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
                # 文本消息
                result = self.client.send_text(self.wxid, receiver, reply.content)
                if result.get("Success"):
                    logger.info(f"[dpbot] ✅ 发送文本消息到 {receiver_info}: {reply.content[:50]}...")
                else:
                    logger.error(f"[dpbot] ❌ 发送文本消息失败到 {receiver_info}: {result.get('Message')}")
                    raise Exception(f"发送文本消息失败: {result.get('Message')}")
                
            elif reply.type == ReplyType.IMAGE:
                success = self.send_image(reply.content, receiver)
                if not success:
                    # 发送失败时，尝试发送错误提示
                    try:
                        error_msg = "图片发送失败，请稍后再试"
                        self.client.send_text(self.wxid, receiver, error_msg)
                        logger.info(f"[dpbot] 图片发送失败，已发送错误提示")
                    except Exception as e:
                        logger.error(f"[dpbot] 发送图片失败提示消息异常: {e}")
                return
                
            elif reply.type == ReplyType.VOICE:
                original_voice_file_path = reply.content
                if not original_voice_file_path or not os.path.exists(original_voice_file_path):
                    logger.error(f"[dpbot] Send voice failed: Original voice file not found or path is empty: {original_voice_file_path}")
                    return

                loop = asyncio.get_event_loop()
                temp_files_to_clean = []

                try:
                    from voice.audio_convert import split_audio
                    total_duration_ms, segment_paths = split_audio(original_voice_file_path, 60 * 1000)
                
                    if original_voice_file_path not in segment_paths:
                        temp_files_to_clean.append(original_voice_file_path)
                    temp_files_to_clean.extend(segment_paths)

                    if not segment_paths:
                        logger.error(f"[dpbot] Voice splitting failed for {original_voice_file_path}. No segments created.")
                        return

                    logger.info(f"[dpbot] 语音文件(总时长: {total_duration_ms / 1000:.2f}s)被分割成 {len(segment_paths)} 个片段, 开始逐一发送...")

                    for i, segment_path in enumerate(segment_paths):
                        # _send_voice 方法内部会记录详细的成功、失败或警告日志
                        segment_result = loop.run_until_complete(self._send_voice(receiver, segment_path))
                        
                        # 如果一个片段发送不成功 (Success != True)，则中止发送剩余片段
                        if not (segment_result and segment_result.get("Success")):
                            logger.error(f"[dpbot] 语音片段 {i+1}/{len(segment_paths)} 发送失败, 中止发送剩余片段。")
                            break

                        # 在片段之间短暂暂停
                        if i < len(segment_paths) - 1:
                            time.sleep(0.8)

                except Exception as e:
                    logger.error(f"[dpbot] Error during voice splitting or segmented sending: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                finally:
                    logger.info(f"[dpbot] 开始清理 {len(temp_files_to_clean)} 个语音相关临时文件...")
                    for temp_file_path in temp_files_to_clean:
                        try:
                            if os.path.exists(temp_file_path):
                                os.remove(temp_file_path)
                                logger.debug(f"[dpbot] 已清理临时语音文件: {os.path.basename(temp_file_path)}")
                        except Exception as e_cleanup:
                            logger.warning(f"[dpbot] 清理临时语音文件失败 {temp_file_path}: {e_cleanup}")
                    logger.info(f"[dpbot] 语音文件清理完成")
                
            elif reply.type == ReplyType.VIDEO_URL:
                # 视频URL消息 - 下载、提取缩略图并发送
                temp_path = None
                thumb_path = None
                try:
                    import base64
                    import uuid
                    import subprocess
                    from common.tmp_dir import TmpDir
                    
                    video_url = reply.content
                    if not video_url:
                        logger.error("[wxpad] 视频URL为空")
                        return
                    
                    # 下载视频到临时文件
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36"
                    }
                    
                    temp_dir = TmpDir().path()
                    temp_path = os.path.join(temp_dir, f"downloaded_video_{uuid.uuid4().hex[:8]}.mp4")
                    
                    logger.info(f"[wxpad] 正在下载视频至临时文件: {temp_path}")
                    with open(temp_path, 'wb') as f:
                        response = requests.get(video_url, headers=headers, stream=True, timeout=60)
                        response.raise_for_status()
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    logger.info(f"[wxpad] 视频下载完成: {temp_path}, 大小: {os.path.getsize(temp_path)}字节")

                    # 获取视频时长和提取缩略图
                    ffprobe_path = self._get_ffprobe_path()
                    ffmpeg_path = self._get_ffmpeg_path()
                    duration = 0
                    video_length = 10 # 默认
                    thumb_base64 = ""

                    try:
                        # 获取视频时长
                        duration_cmd = [
                            ffprobe_path, "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", temp_path
                        ]
                        duration_result = subprocess.run(duration_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
                        duration = float(duration_result.stdout.decode().strip())
                        video_length = max(1, int(duration))
                        logger.info(f"[wxpad] 获取视频时长成功: {duration:.2f}秒")

                        # 计算中间帧的时间点
                        thumb_time_point = duration / 2
                        
                        # 提取中间位置的帧并缩放为150x150缩略图
                        thumb_path = os.path.join(temp_dir, f"thumb_{uuid.uuid4().hex[:8]}.jpg")
                        thumb_cmd = [
                            ffmpeg_path, "-y", "-ss", str(thumb_time_point), "-i", temp_path,
                            "-vf", "scale=150:150", "-vframes", "1", thumb_path
                        ]
                        logger.debug(f"[wxpad] 执行FFmpeg命令: {' '.join(thumb_cmd)}")
                        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)

                        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                            logger.info(f"[wxpad] 缩略图提取成功: {thumb_path}")
                            with open(thumb_path, 'rb') as f_thumb:
                                thumb_base64 = base64.b64encode(f_thumb.read()).decode('utf-8')
                        else:
                            logger.warning("[wxpad] FFmpeg命令执行后未找到缩略图文件，将不发送缩略图")

                    except Exception as ff_e:
                        logger.warning(f"[wxpad] 使用ffmpeg/ffprobe处理视频失败: {ff_e}, 将不发送缩略图")
                    
                    # 读取视频文件
                    with open(temp_path, 'rb') as f_video:
                        raw_video_base64 = base64.b64encode(f_video.read()).decode('utf-8')

                    # 使用send_video发送, 为base64数据添加前缀
                    result = self.client.send_video(
                        wxid=self.wxid,
                        to_wxid=receiver,
                        base64_video=f"data:video/mp4;base64,{raw_video_base64}",
                        base64_thumb=f"data:image/jpeg;base64,{thumb_base64}" if thumb_base64 else "",
                        play_length=video_length
                    )
                    if result and result.get("Success"):
                        logger.info(f"[wxpad] 视频URL发送成功到 {receiver_info}")
                    else:
                        logger.error(f"[wxpad] 视频URL发送失败: {result.get('Message') if result else 'Unknown error'}")

                except Exception as e:
                    logger.error(f"[wxpad] 处理视频URL异常: {e}")
                    try:
                        error_msg = f"处理视频时出错，请稍后再试: {e}"
                        self.client.send_text(self.wxid, receiver, error_msg)
                    except Exception as e2:
                        logger.error(f"[wxpad] 发送视频错误提示失败: {e2}")
                finally:
                    # 清理临时文件
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                            logger.debug(f"[wxpad] 已清理临时视频文件: {temp_path}")
                        except Exception as e_clean:
                            logger.warning(f"[wxpad] 清理临时视频文件失败: {e_clean}")
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            os.remove(thumb_path)
                            logger.debug(f"[wxpad] 已清理临时缩略图文件: {thumb_path}")
                        except Exception as e_clean:
                            logger.warning(f"[wxpad] 清理临时缩略图文件失败: {e_clean}")
                
            elif reply.type == ReplyType.VIDEO:
                # 视频文件消息 - 提取缩略图并发送
                video_path = reply.content
                thumb_path = None
                try:
                    import base64
                    import uuid
                    import subprocess
                    from common.tmp_dir import TmpDir
                    
                    if not video_path or not os.path.exists(video_path):
                        logger.error(f"[wxpad] 视频文件不存在或路径为空: {video_path}")
                        return

                    logger.info(f"[wxpad] 开始处理视频文件: {video_path}")

                    # 获取视频时长和提取缩略图
                    ffprobe_path = self._get_ffprobe_path()
                    ffmpeg_path = self._get_ffmpeg_path()
                    duration = 0
                    video_length = 10 # 默认
                    thumb_base64 = ""

                    try:
                        # 获取视频时长
                        duration_cmd = [
                            ffprobe_path, "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path
                        ]
                        duration_result = subprocess.run(duration_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
                        duration = float(duration_result.stdout.decode().strip())
                        video_length = max(1, int(duration))
                        logger.info(f"[wxpad] 获取视频时长成功: {duration:.2f}秒")

                        # 计算中间帧的时间点
                        thumb_time_point = duration / 2
                        
                        # 提取中间位置的帧并缩放为150x150缩略图
                        temp_dir = TmpDir().path()
                        thumb_path = os.path.join(temp_dir, f"thumb_{uuid.uuid4().hex[:8]}.jpg")
                        thumb_cmd = [
                            ffmpeg_path, "-y", "-ss", str(thumb_time_point), "-i", video_path,
                            "-vf", "scale=150:150", "-vframes", "1", thumb_path
                        ]
                        logger.debug(f"[wxpad] 执行FFmpeg命令: {' '.join(thumb_cmd)}")
                        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=30)

                        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                            logger.info(f"[wxpad] 缩略图提取成功: {thumb_path}")
                            with open(thumb_path, 'rb') as f_thumb:
                                thumb_base64 = base64.b64encode(f_thumb.read()).decode('utf-8')
                        else:
                            logger.warning("[wxpad] FFmpeg命令执行后未找到缩略图文件，将不发送缩略图")

                    except Exception as ff_e:
                        logger.warning(f"[wxpad] 使用ffmpeg/ffprobe处理视频失败: {ff_e}, 将不发送缩略图")
                    
                    # 读取视频文件
                    with open(video_path, 'rb') as f_video:
                        raw_video_base64 = base64.b64encode(f_video.read()).decode('utf-8')

                    # 使用send_video发送，为base64数据添加前缀
                    result = self.client.send_video(
                        wxid=self.wxid,
                        to_wxid=receiver,
                        base64_video=f"data:video/mp4;base64,{raw_video_base64}",
                        base64_thumb=f"data:image/jpeg;base64,{thumb_base64}" if thumb_base64 else "",
                        play_length=video_length
                    )
                    if result and result.get("Success"):
                        logger.info(f"[wxpad] 视频文件发送成功到 {receiver_info}")
                    else:
                        logger.error(f"[wxpad] 视频文件发送失败: {result.get('Message') if result else 'Unknown error'}")

                except Exception as e:
                    logger.error(f"[wxpad] 处理视频文件异常: {e}")
                    try:
                        error_msg = f"处理视频文件时出错，请稍后再试: {e}"
                        self.client.send_text(self.wxid, receiver, error_msg)
                    except Exception as e2:
                        logger.error(f"[wxpad] 发送视频错误提示失败: {e2}")
                finally:
                    # 假设reply.content中的文件是临时的，也一并清理
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                            logger.debug(f"[wxpad] 已清理临时视频文件: {video_path}")
                        except Exception as e_clean:
                            logger.warning(f"[wxpad] 清理临时视频文件失败: {e_clean}")
                    if thumb_path and os.path.exists(thumb_path):
                        try:
                            os.remove(thumb_path)
                            logger.debug(f"[wxpad] 已清理临时缩略图文件: {thumb_path}")
                        except Exception as e_clean:
                            logger.warning(f"[wxpad] 清理临时缩略图文件失败: {e_clean}")
                
            elif reply.type == ReplyType.EMOJI:
                # 表情消息
                if isinstance(reply.content, tuple) and len(reply.content) == 2:
                    md5, total_len = reply.content
                    result = self.client.send_emoji(self.wxid, receiver, md5, total_len)
                    if result.get("Success"):
                        logger.info(f"[wxpad] send emoji to {receiver}")
                    else:
                        logger.error(f"[wxpad] send emoji failed: {result.get('Message')}")
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
                    result = self.client.share_card(
                        wxid=self.wxid,
                        to_wxid=receiver,
                        card_wxid=card_wxid,
                        card_nickname=card_nickname,
                        card_alias=card_alias
                    )
                    if result.get("Success"):
                        logger.info(f"[wxpad] send card to {receiver}")
                    else:
                        logger.error(f"[wxpad] send card failed: {result.get('Message')}")
                else:
                    logger.error(f"[wxpad] Invalid card content format: {type(reply.content)}")
                
            elif reply.type == ReplyType.LINK:
                # 链接消息
                if isinstance(reply.content, str):
                    # 如果是XML字符串，直接发送
                    logger.debug(f"[wxpad] 发送应用消息，XML长度: {len(reply.content)}")
                    result = self.client.send_app_message(self.wxid, receiver, reply.content)
                    if result.get("Success"):
                        logger.info(f"[wxpad] send link to {receiver}")
                    else:
                        logger.error(f"[wxpad] send link failed: {result.get('Message')}")
                        raise Exception(f"应用消息发送失败 {result.get('Message')}")
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
                    result = self.client.send_app_message(self.wxid, receiver, xml)
                    if result.get("Success"):
                        logger.info(f"[wxpad] send link to {receiver}")
                    else:
                        logger.error(f"[wxpad] send link failed: {result.get('Message')}")
                        raise Exception(f"链接卡片发送失败 {result.get('Message')}")
                else:
                    logger.error(f"[wxpad] Invalid link content format: {type(reply.content)}")
                    raise Exception(f"无效的链接内容格式 {type(reply.content)}")
                
            elif reply.type == ReplyType.REVOKE:
                # 撤回消息
                if isinstance(reply.content, tuple) and len(reply.content) == 3:
                    client_msg_id, create_time, new_msg_id = reply.content
                    result = self.client.revoke_msg(
                        wxid=self.wxid,
                        client_msg_id=client_msg_id,
                        create_time=create_time,
                        new_msg_id=new_msg_id,
                        to_user_name=receiver
                    )
                    if result.get("Success"):
                        logger.info(f"[wxpad] revoke msg from {receiver}")
                    else:
                        logger.error(f"[wxpad] revoke msg failed: {result.get('Message')}")
                else:
                    logger.error(f"[wxpad] Invalid revoke content format: {type(reply.content)}")
                
            else:
                logger.warning(f"[wxpad] Unsupported reply type: {reply.type}")
                
        except Exception as e:
            logger.error(f"[wxpad] Failed to send {reply.type} to {receiver}: {e}")
            # 尝试发送错误消息
            try:
                error_msg = f"消息发送失败 {e}"
                self.client.send_text(self.wxid, receiver, error_msg)
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
            result = self.client.send_image(self.wxid, to_wxid, image_base64)
            
            if result.get("Success"):
                logger.info(f"[wxpad] ✅ 发送图片到 {to_wxid}")
                return True
            else:
                logger.error(f"[send_image] 图片发送失败: {result.get('Message')}")
                return False
                
        except Exception as e:
            logger.error(f"[send_image] 发送图片异常 {e}")
            return False

    def _get_ffmpeg_tool_path(self, tool_name):
        """获取FFmpeg工具套件中指定工具的可执行文件路径
        
        Args:
            tool_name (str): 工具名称，如 'ffmpeg', 'ffprobe'
            
        Returns:
            str: 工具可执行文件的完整路径
        """
        import shutil
        import platform
        
        # Windows系统的可执行文件扩展名
        exe_ext = ".exe" if platform.system() == "Windows" else ""
        tool_exe_name = f"{tool_name}{exe_ext}"
        
        # 检测路径优先级
        search_paths = []
        
        # 1. 用户提供的常见Windows路径
        if platform.system() == "Windows":
            search_paths.extend([
                "D:\\ffmpeg-n7.1-latest-win64-gpl-7.1\\bin",
                "C:\\ffmpeg\\bin",
                "C:\\Program Files\\ffmpeg\\bin",
                "C:\\Program Files (x86)\\ffmpeg\\bin"
            ])
        
        # 2. 常见Linux/macOS路径
        search_paths.extend([
            "/usr/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",  # macOS Homebrew
            "/snap/bin"  # Ubuntu Snap
        ])
        
        # 3. 直接在指定路径中查找
        for path in search_paths:
            full_path = os.path.join(path, tool_exe_name)
            if os.path.exists(full_path) and os.access(full_path, os.X_OK):
                logger.info(f"[wxpad] 找到{tool_name}: {full_path}")
                return full_path
        
        # 4. 使用系统PATH查找
        tool_path = shutil.which(tool_exe_name)
        if tool_path:
            logger.info(f"[wxpad] 在系统PATH中找到{tool_name}: {tool_path}")
            return tool_path
        
        # 5. 如果都找不到，返回默认名称（让系统尝试）
        logger.warning(f"[wxpad] 未找到{tool_name}安装，将使用默认名称: {tool_exe_name}")
        return tool_exe_name

    def _get_ffmpeg_path(self):
        """获取FFmpeg可执行文件路径
        
        Returns:
            str: FFmpeg可执行文件的完整路径
        """
        return self._get_ffmpeg_tool_path("ffmpeg")

    def _get_ffprobe_path(self):
        """获取FFprobe可执行文件路径
        
        Returns:
            str: FFprobe可执行文件的完整路径
        """
        return self._get_ffmpeg_tool_path("ffprobe")

    async def _send_voice(self, to_user_id, voice_file_path_segment):
        """发送语音消息的异步方法 (单个MP3片段路径), 内部处理SILK转换."""
        if not PYSLIK_AVAILABLE:
            logger.error("[dpbot] Send voice failed: pysilk library is not available.")
            return {"Success": False, "Message": "pysilk library not available"}

        try:
            if not to_user_id:
                logger.error("[dpbot] Send voice failed: receiver ID is empty")
                return {"Success": False, "Message": "Receiver ID empty"}
            if not os.path.exists(voice_file_path_segment):
                logger.error(f"[dpbot] Send voice failed: voice segment file not found at {voice_file_path_segment}")
                return {"Success": False, "Message": f"Voice segment not found: {voice_file_path_segment}"}

            # 使用pydub加载MP3片段
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(voice_file_path_segment, format="mp3")
            except Exception as e_pydub_load:
                logger.error(f"[dpbot] Failed to load voice segment {voice_file_path_segment} with pydub: {e_pydub_load}")
                import traceback
                logger.error(traceback.format_exc()) # Log full traceback for pydub errors
                return {"Success": False, "Message": f"Pydub load failed: {e_pydub_load}"}

            # 处理音频: 设置声道和采样率
            audio = audio.set_channels(1)
            supported_rates = [8000, 12000, 16000, 24000] # SILK支持的采样率
            closest_rate = min(supported_rates, key=lambda x: abs(x - audio.frame_rate))
            audio = audio.set_frame_rate(closest_rate)
            duration_ms = len(audio)

            if duration_ms == 0:
                logger.warning(f"[dpbot] Voice segment {voice_file_path_segment} has zero duration after pydub processing. Skipping send.")
                return {"Success": False, "Message": "Zero duration audio"}

            # 使用pysilk编码为SILK
            try:
                if hasattr(pysilk, 'async_encode') and asyncio.iscoroutinefunction(pysilk.async_encode):
                    silk_data = await pysilk.async_encode(audio.raw_data, sample_rate=audio.frame_rate)
                elif hasattr(pysilk, 'encode'): 
                    silk_data = pysilk.encode(audio.raw_data, sample_rate=audio.frame_rate)
                else:
                    logger.error("[dpbot] pysilk does not have a usable 'encode' or 'async_encode' method.")
                    return {"Success": False, "Message": "pysilk encode method not found"}
            except Exception as e_silk_encode:
                logger.error(f"[dpbot] SILK encoding failed for {voice_file_path_segment}: {e_silk_encode}")
                import traceback
                logger.error(traceback.format_exc()) # Log full traceback for silk errors
                return {"Success": False, "Message": f"SILK encoding failed: {e_silk_encode}"}
            
            voice_base64 = base64.b64encode(silk_data).decode('utf-8')

            logger.info(f"[dpbot] 准备发送SILK语音: 接收者={to_user_id}, 文件={voice_file_path_segment}, 时长={duration_ms}ms, 类型=4")
                
            result = self.client.send_voice(
                wxid=self.wxid,
                to_wxid=to_user_id,
                base64_voice=voice_base64,
                voice_type=4,  # SILK 格式
                voice_time=int(duration_ms)
            )
            
            # 检查返回结果，包括内层BaseResponse状态
            base_response_ret = result.get("Data", {}).get("BaseResponse", {}).get("ret")
            if result and result.get("Success"):
                if base_response_ret == 0:
                    logger.info(f"[dpbot] 发送SILK语音成功: 接收者={to_user_id}, 文件={voice_file_path_segment}, 结果: {result}")
                elif base_response_ret is not None and base_response_ret != 0:
                    logger.warning(f"[dpbot] 发送SILK语音警告: 接收者={to_user_id}, 文件={voice_file_path_segment}, BaseResponse.ret={base_response_ret}, 但外层Success=True, 结果: {result}")
                else:
                    logger.info(f"[dpbot] 发送SILK语音成功: 接收者={to_user_id}, 文件={voice_file_path_segment}, 无BaseResponse信息, 结果: {result}")
            else:
                logger.error(f"[dpbot] 发送SILK语音失败: 接收者={to_user_id}, 文件={voice_file_path_segment}, 结果: {result}")
            return result

        except Exception as e:
            logger.error(f"[dpbot] Exception in _send_voice (SILK processing) for {voice_file_path_segment} to {to_user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"Success": False, "Message": f"General exception in _send_voice: {e}"}
