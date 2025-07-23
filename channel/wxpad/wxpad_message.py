import base64
import uuid
import re
from bridge.context import ContextType
from channel.chat_message import ChatMessage
from common.log import logger
from common.tmp_dir import TmpDir
from config import conf
from lib.wxpad.client import WxpadClient
import requests
import xml.etree.ElementTree as ET
from common import memory


class WechatPadProMessage(ChatMessage):
    def __init__(self, msg, client: WxpadClient = None):
        super().__init__(msg)
        self.msg = msg
        self.content = ''  # 初始化self.content为空字符串
        
        # 安全初始化：确保关键属性在所有执行路径中都有默认值
        self.msg_source = ''
        self.from_user_id = ''
        self.to_user_id = ''
        self.other_user_id = ''
        self.ctype = ContextType.TEXT

        # 适配多种消息格式：WebSocket直接格式、API嵌套格式和转换后格式
        self.msg_data = {}
        if 'Data' in msg:
            # API返回的嵌套格式
            self.msg_data = msg['Data']
        elif 'data' in msg:
            # 其他可能的嵌套格式
            self.msg_data = msg['data']
        elif 'msg_id' in msg or 'new_msg_id' in msg:
            # WebSocket直接格式 - 字段名转换，保留Content原始字典结构
            self.msg_data = {
                'NewMsgId': msg.get('new_msg_id', msg.get('msg_id', '')),
                'FromUserName': {'string': msg.get('from_user_name', {}).get('str', '')},
                'ToUserName': {'string': msg.get('to_user_name', {}).get('str', '')},
                'MsgType': msg.get('msg_type', 0),
                'Content': msg.get('content', {}),  # 保留原始字典结构
                'CreateTime': msg.get('create_time', 0),
                'MsgSource': msg.get('msg_source', ''),
                'ImgBuf': msg.get('img_buf', {})
            }
        elif 'FromUserName' in msg and 'ToUserName' in msg and ('MsgId' in msg or 'NewMsgId' in msg):
            # 已转换的标准格式（通道预处理过的）- 现在Content保留原始字典结构
            self.msg_data = {
                'NewMsgId': msg.get('NewMsgId', msg.get('MsgId', 0)),
                'FromUserName': {'string': msg.get('FromUserName', '')},
                'ToUserName': {'string': msg.get('ToUserName', '')},
                'MsgType': msg.get('MsgType', 1),
                'Content': msg.get('Content', {}),  # 保留原始字典结构
                'CreateTime': msg.get('CreateTime', 0),
                'MsgSource': msg.get('MsgSource', ''),
                'ImgBuf': msg.get('ImgBuf', {})
            }
        else:
            logger.warning(f"[wxpad] Unknown message format, missing expected fields")
            logger.debug(f"[wxpad] Available keys: {list(msg.keys())}")
            # 对于未知消息格式，使用默认值，确保基本属性可访问
            # msg_source, from_user_id 等已在初始化时设置为默认值
            return
            
        self.create_time = self.msg_data.get('CreateTime', 0)
        if not self.msg_data or not self.msg_data.get('NewMsgId'):
            logger.warning(f"[wxpad] Invalid message data or missing NewMsgId")
            logger.debug(f"[wxpad] msg_data: {self.msg_data}")
            # 即使消息数据无效，也要确保关键属性已设置，避免后续访问出错
            self.msg_id = 0
            self.is_group = False
            # msg_source 已在初始化时设置为默认值
            return
        self.msg_id = self.msg_data['NewMsgId']
        self.is_group = True if "@chatroom" in self.msg_data['FromUserName']['string'] else False
        
        # 更新msg_source属性（覆盖初始化时的默认值）
        self.msg_source = self.msg_data.get('MsgSource', '')

        notes_join_group = ["加入群聊", "加入了群聊", "invited", "joined", "移出了群聊"]
        notes_bot_join_group = ["邀请你", "invited you", "You've joined", "你通过扫描"]

        self.client = client
        msg_type = self.msg_data['MsgType']
    
        self.from_user_id = self.msg_data['FromUserName']['string']
        self.to_user_id = self.msg_data['ToUserName']['string']
        self.other_user_id = self.from_user_id
        # 检查是否是公众号等非用户账号的消息
        content_dict = self.msg_data.get('Content', {})
        content = content_dict.get('str', content_dict.get('string', ''))
        if self._is_non_user_message(self.msg_source, self.from_user_id, content, msg_type):
            self.ctype = ContextType.NON_USER_MSG
            self.content = content  # 确保获取字符串
            logger.debug(f"[wxpad] detected non-user message from {self.from_user_id}: {self.content}")
            return

        if msg_type == 1:  # Text message
            self.ctype = ContextType.TEXT
            content_dict = self.msg_data.get('Content', {})
            self.content = content_dict.get('str', content_dict.get('string', ''))
        elif msg_type == 34:  # Voice message
            self.ctype = ContextType.VOICE
            # 生成语音文件路径
            silk_file_name = f"voice_{uuid.uuid4()}.silk"
            self.content = TmpDir().path() + silk_file_name
            # 设置延迟下载函数
            self._prepare_fn = self.download_voice
        elif msg_type == 43:  # Video message
            self.ctype = ContextType.VIDEO
            # 生成视频文件路径
            video_file_name = f"video_{uuid.uuid4()}.mp4"
            self.content = TmpDir().path() + video_file_name
            # 设置延迟下载函数
            self._prepare_fn = self.download_video
        elif msg_type == 3:  # Image message
            self.ctype = ContextType.IMAGE
            self.content = TmpDir().path() + str(self.msg_id) + ".png"
            self._prepare_fn = self.download_image
        elif msg_type == 49:  # 引用消息，小程序，公众号等
            # After getting content_xml
            content_dict = self.msg_data.get('Content', {})
            content_xml = content_dict.get('str', content_dict.get('string', ''))
            # Find the position of '<?xml' declaration and remove any prefix
            xml_start = content_xml.find('<?xml version=')
            if xml_start != -1:
                content_xml = content_xml[xml_start:]
            try:
                root = ET.fromstring(content_xml)
                appmsg = root.find('appmsg')
                if appmsg is not None:
                    msg_type_node = appmsg.find('type')
                    if msg_type_node is not None and msg_type_node.text == '57':
                        refermsg = appmsg.find('refermsg')
                        if refermsg is not None:
                            # 统一处理引用消息
                            self._process_refer_message(refermsg, appmsg)
                        else:
                            self.ctype = ContextType.TEXT
                            self.content = content_xml
                    elif msg_type_node is not None and msg_type_node.text == '6':
                        # 文件消息
                        self.ctype = ContextType.FILE
                        title = appmsg.find('title').text if appmsg.find('title') is not None else "未知文件"
                        appattach = appmsg.find('appattach')
                        if appattach is not None:
                            fileext = appattach.find('fileext').text if appattach.find('fileext') is not None else ""
                            file_name = f"file_{uuid.uuid4()}.{fileext}" if fileext else f"file_{uuid.uuid4()}"
                            self.content = TmpDir().path() + file_name
                            self._prepare_fn = self.download_file
                            # 保存文件信息，用于下载
                            self._file_info = {
                                'title': title,
                                'fileext': fileext,
                                'attachid': appattach.find('attachid').text if appattach.find('attachid') is not None else "",
                                'cdnattachurl': appattach.find('cdnattachurl').text if appattach.find('cdnattachurl') is not None else "",
                                'aeskey': appattach.find('aeskey').text if appattach.find('aeskey') is not None else "",
                                'totallen': appattach.find('totallen').text if appattach.find('totallen') is not None else "0",
                                'md5': appmsg.find('md5').text if appmsg.find('md5') is not None else ""
                            }
                        else:
                            self.ctype = ContextType.TEXT
                            self.content = f"[文件] {title}"
                    elif msg_type_node is not None and msg_type_node.text == '5':
                        title = appmsg.find('title').text if appmsg.find('title') is not None else "无标题"
                        if "加入群聊" in title:
                            self.ctype = ContextType.TEXT
                            self.content = content_xml
                        else:
                            url = appmsg.find('url').text if appmsg.find('url') is not None else ""
                            self.ctype = ContextType.SHARING
                            self.content = url
                    else:
                        self.ctype = ContextType.TEXT
                        self.content = content_xml
                else:
                    self.ctype = ContextType.TEXT
                    self.content = content_xml
            except ET.ParseError:
                self.ctype = ContextType.TEXT
                self.content = content_xml
        elif msg_type == 51:
            self.ctype = ContextType.STATUS_SYNC
            content_dict = self.msg_data.get('Content', {})
            self.content = content_dict.get('str', content_dict.get('string', ''))
            return
        elif msg_type == 10002 and self.is_group:  # 群系统消息
            content_dict = self.msg_data.get('Content', {})
            content = content_dict.get('str', content_dict.get('string', ''))
            logger.debug(f"[wxpad] detected group system message: {content}")
            
            if any(note in content for note in notes_bot_join_group):
                logger.warn("机器人加入群聊消息，不处理~")
                self.content = content
                return
                
            if any(note in content for note in notes_join_group):
                try:
                    xml_content = content.split(':\n', 1)[1] if ':\n' in content else content
                    root = ET.fromstring(xml_content)
                    
                    sysmsgtemplate = root.find('.//sysmsgtemplate')
                    if sysmsgtemplate is None:
                        raise ET.ParseError("No sysmsgtemplate found")
                        
                    content_template = sysmsgtemplate.find('.//content_template')
                    if content_template is None:
                        raise ET.ParseError("No content_template found")
                        
                    content_type = content_template.get('type')
                    if content_type not in ['tmpl_type_profilewithrevoke', 'tmpl_type_profile']:
                        raise ET.ParseError(f"Invalid content_template type: {content_type}")
                    
                    template = content_template.find('.//template')
                    if template is None:
                        raise ET.ParseError("No template element found")

                    link_list = content_template.find('.//link_list')
                    target_nickname = "未知用户"
                    target_username = None
                    
                    if link_list is not None:
                        # 根据消息类型确定要查找的link name
                        link_name = 'names' if content_type == 'tmpl_type_profilewithrevoke' else 'kickoutname'
                        action_link = link_list.find(f".//link[@name='{link_name}']")
                        
                        if action_link is not None:
                            members = action_link.findall('.//member')
                            nicknames = []
                            usernames = []
                            
                            for member in members:
                                nickname_elem = member.find('nickname')
                                username_elem = member.find('username')
                                nicknames.append(nickname_elem.text if nickname_elem is not None else "未知用户")
                                usernames.append(username_elem.text if username_elem is not None else None)
                            
                            # 处理分隔符（主要针对邀请消息）
                            separator_elem = action_link.find('separator')
                            separator = separator_elem.text if separator_elem is not None else '、'
                            target_nickname = separator.join(nicknames) if nicknames else "未知用户"
                            
                            # 取第一个有效username（根据业务需求调整）
                            target_username = next((u for u in usernames if u), None)

                    # 构造最终消息内容
                    if content_type == 'tmpl_type_profilewithrevoke':
                        self.content = f'你邀请"{target_nickname}"加入了群聊'
                        self.ctype = ContextType.JOIN_GROUP
                    elif content_type == 'tmpl_type_profile':
                        self.content = f'你将"{target_nickname}"移出了群聊'
                        self.ctype = ContextType.EXIT_GROUP  # 可根据需要创建新的ContextType

                    self.actual_user_nickname = target_nickname
                    self.actual_user_id = target_username
                    
                    logger.debug(f"[wxpad] parsed group system message: {self.content} "
                                f"type: {content_type} user: {target_nickname} ({target_username})")
                    
                except ET.ParseError as e:
                    logger.error(f"[wxpad] Failed to parse group system message XML: {e}")
                    self.content = content
                except Exception as e:
                    logger.error(f"[wxpad] Unexpected error parsing group system message: {e}")
                    self.content = content
        elif msg_type == 10002:  # 系统消息（非群聊）
            content_dict = self.msg_data.get('Content', {})
            content = content_dict.get('str', content_dict.get('string', ''))
            logger.debug(f"[wxpad] detected system message (non-group): {content}")
            # 对于非群聊的系统消息，直接设置为文本类型但不处理
            self.ctype = ContextType.TEXT
            self.content = content
        elif msg_type == 37:  # 好友请求消息
            content_dict = self.msg_data.get('Content', {})
            content = content_dict.get('str', content_dict.get('string', ''))
            logger.debug(f"[wxpad] detected friend request message: {content}")
            # 好友请求消息设置为文本类型但不处理
            self.ctype = ContextType.TEXT
            self.content = content
        elif msg_type == 10000:  # 系统提示消息（如添加好友成功）
            content_dict = self.msg_data.get('Content', {})
            content = content_dict.get('str', content_dict.get('string', ''))
            logger.debug(f"[wxpad] detected system notification message: {content}")
            # 系统提示消息设置为文本类型但不处理
            self.ctype = ContextType.TEXT
            self.content = content
        elif msg_type == 47:
            self.ctype = ContextType.EMOJI
            content_dict = self.msg_data.get('Content', {})
            self.content = content_dict.get('str', content_dict.get('string', ''))
        else:
            # 对于未知类型的消息，记录日志但不抛出异常，设置为文本类型
            logger.warning(f"[wxpad] Unsupported message type: Type:{msg_type}, will process as text")
            self.ctype = ContextType.TEXT
            content_dict = self.msg_data.get('Content', {})
            self.content = content_dict.get('str', content_dict.get('string', ''))

        # 获取群聊或好友的名称
        # 优先从数据库获取，避免重复API调用
        if "@chatroom" in self.other_user_id:
            # 群聊 - 先尝试从数据库获取群名称
            try:
                from database.group_members_db import get_group_name_from_db
                cached_group_name = get_group_name_from_db(self.other_user_id)
                if cached_group_name:
                    self.other_user_nickname = cached_group_name
                    logger.debug(f"[wxpad] 从数据库获取群名称: {self.other_user_id} -> {cached_group_name}")
                else:
                    # 如果数据库中没有，使用API获取
                    if self.client:
                        try:
                            logger.debug(f"[wxpad] 尝试获取群聊信息: {self.other_user_id}")
                            contact_info_response = self.client.get_contact_details_list([], [self.other_user_id], user_key=None)
                            
                            if contact_info_response.get('Code') == 200 and contact_info_response.get('Data'):
                                data = contact_info_response['Data']
                                contact_list = data.get('contactList', []) if isinstance(data, dict) else []
                                if contact_list:
                                    contact_info = contact_list[0]
                                    nick_name = contact_info.get('nickName', self.other_user_id)
                                    if isinstance(nick_name, dict):
                                        nick_name = nick_name.get('str', nick_name.get('string', self.other_user_id))
                                    self.other_user_nickname = nick_name
                                    
                                    # 保存群名称到数据库
                                    try:
                                        from database.group_members_db import save_group_info
                                        save_group_info(self.other_user_id, nick_name)
                                    except Exception as e:
                                        logger.warning(f"[wxpad] 保存群名称到数据库失败: {e}")
                                else:
                                    self.other_user_nickname = self.other_user_id
                            else:
                                self.other_user_nickname = self.other_user_id
                        except Exception as e:
                            logger.warning(f"[wxpad] 获取群聊信息失败: {e}")
                            self.other_user_nickname = self.other_user_id
                    else:
                        self.other_user_nickname = self.other_user_id
            except Exception as e:
                logger.debug(f"[wxpad] 从数据库获取群名称失败: {e}")
                self.other_user_nickname = self.other_user_id
        else:
            # 私聊 - 先尝试从群成员数据库获取昵称
            try:
                from database.group_members_db import get_user_nickname_from_db
                cached_nickname = get_user_nickname_from_db(self.other_user_id)
                if cached_nickname:
                    self.other_user_nickname = cached_nickname
                    logger.debug(f"[wxpad] 从数据库获取用户昵称: {self.other_user_id} -> {cached_nickname}")
                else:
                    # 如果数据库中没有，使用API获取
                    if self.client:
                        try:
                            logger.debug(f"[wxpad] 尝试获取用户信息: {self.other_user_id}")
                            contact_info_response = self.client.get_contact_details_list([], [self.other_user_id], user_key=None)
                            
                            if contact_info_response.get('Code') == 200 and contact_info_response.get('Data'):
                                data = contact_info_response['Data']
                                contact_list = data.get('contactList', []) if isinstance(data, dict) else []
                                if contact_list:
                                    contact_info = contact_list[0]
                                    nick_name = contact_info.get('nickName', self.other_user_id)
                                    if isinstance(nick_name, dict):
                                        nick_name = nick_name.get('str', nick_name.get('string', self.other_user_id))
                                    self.other_user_nickname = nick_name
                                else:
                                    self.other_user_nickname = self.other_user_id
                            else:
                                self.other_user_nickname = self.other_user_id
                        except Exception as e:
                            logger.warning(f"[wxpad] 获取用户信息失败: {e}")
                            self.other_user_nickname = self.other_user_id
                    else:
                        self.other_user_nickname = self.other_user_id
            except Exception as e:
                logger.debug(f"[wxpad] 从数据库获取用户昵称失败: {e}")
                self.other_user_nickname = self.other_user_id

        logger.debug(f"[wxpad] 准备进入群聊消息解析逻辑: is_group={self.is_group}")

        if self.is_group:
            # 群聊消息：获取实际发送者信息
            content_str = self.msg_data.get('Content', {})
            if isinstance(content_str, dict):
                # 尝试不同的字段名：str (新格式) 或 string (旧格式)
                content_text = content_str.get('str', content_str.get('string', ''))
            else:
                content_text = str(content_str)
            
            if ':' in content_text:
                self.actual_user_id = content_text.split(':', 1)[0]
            else:
                self.actual_user_id = ''

            # 使用主通道缓存获取群成员昵称
            try:
                from channel.chat_channel import get_group_member_display_name
                self.actual_user_nickname = get_group_member_display_name(self.from_user_id, self.actual_user_id) or self.actual_user_id
            except Exception as e:
                logger.warning(f"[wxpad] Failed to get group member display name: {e}")
                self.actual_user_nickname = self.actual_user_id

            # 检查是否被@：优先XML解析，失败则检查内容
            self.is_at = False
            msg_source = self.msg_data.get('MsgSource', '')
            
            # 尝试从XML解析@列表
            if msg_source:
                try:
                    root = ET.fromstring(msg_source)
                    atuserlist_elem = root.find('atuserlist')
                    if atuserlist_elem is not None and atuserlist_elem.text:
                        self.is_at = self.to_user_id in atuserlist_elem.text
                except ET.ParseError:
                    # XML解析失败，检查消息内容
                    push_content = self.msg_data.get('PushContent', '')
                    original_content_dict = self.msg_data.get('Content', {})
                    original_content = original_content_dict.get('str', original_content_dict.get('string', ''))
                    at_indicators = ['在群聊中@了你', '@小艾', '@' + self.to_user_id]
                    self.is_at = any(indicator in push_content or indicator in original_content 
                                   for indicator in at_indicators)
            # 清理群聊消息格式
            self.content = str(self.content)
            if self.actual_user_id:
                self.content = re.sub(f'{re.escape(self.actual_user_id)}:\n', '', self.content)
            self.content = re.sub(r'@[^\u2005]+\u2005', '', self.content)
        else:
            # 私聊消息：统一字段设置
            self.actual_user_id = self.other_user_id
            self.actual_user_nickname = self.other_user_nickname

        self.my_msg = self.msg.get('Wxid') == self.from_user_id

    def download_voice(self):
        """通过API下载语音并转换为MP3"""
        try:
            if not self.client:
                logger.error("[wxpad] 没有客户端实例，无法下载语音")
                return

            # 从XML内容中解析语音参数
            content_dict = self.msg_data.get('Content', {})
            content_xml = content_dict.get('str', content_dict.get('string', ''))
            if not content_xml:
                logger.error("[wxpad] 没有找到语音XML内容")
                return
                
            import xml.etree.ElementTree as ET
            
            try:
                # 处理群聊消息中的用户ID前缀
                if self.is_group and ':' in content_xml:
                    # 群聊消息格式: "wxid_xxx:\n<msg>..."
                    content_xml = content_xml.split(':', 1)[1].strip()
                
                # 确保XML格式正确
                if not content_xml.startswith('<'):
                    logger.error(f"[wxpad] 语音XML格式不正确: {content_xml[:50]}...")
                    return
                
                # 解析XML获取语音参数
                root = ET.fromstring(content_xml)
                voicemsg = root.find('voicemsg')
                if voicemsg is None:
                    logger.error("[wxpad] XML中没有找到voicemsg元素")
                    return
                    
                # 从XML提取参数
                bufid = voicemsg.get('bufid', '0')
                length = voicemsg.get('length', '0')
                voice_length = int(voicemsg.get('voicelength', '0'))  # 语音时长（毫秒）
                new_msg_id = self.msg_data.get('NewMsgId', 0)
                
                # 参数验证
                if not bufid or not length or not length.isdigit() or int(length) <= 0 or not new_msg_id:
                    logger.error(f"[wxpad] 语音参数无效: bufid={bufid}, length={length}, new_msg_id={new_msg_id}")
                    return

                # 确保from_user_id是字符串
                from_user = self.from_user_id
                if isinstance(from_user, dict):
                    from_user = from_user.get('string', '')

                # 调用API下载语音
                voice_response = self.client.get_msg_voice(
                    bufid=bufid,
                    length=int(length),
                    new_msg_id=str(new_msg_id),
                    to_user_name=from_user
                )

                if voice_response.get("Code") == 200:
                    voice_base64 = voice_response.get("Data", {}).get("Base64")
                    
                    if voice_base64:
                        import base64
                        import os
                        from voice.audio_convert import any_to_mp3
                        
                        # 解码Base64获取SILK数据
                        voice_data = base64.b64decode(voice_base64)
                        
                        # 保存SILK文件
                        silk_file_path = self.content
                        with open(silk_file_path, "wb") as f:
                            f.write(voice_data)
                        
                        logger.info(f"[wxpad] SILK文件保存成功: {silk_file_path}")
                        
                        # 转换为MP3，传入目标时长（毫秒转秒）
                        mp3_file_path = os.path.splitext(silk_file_path)[0] + ".mp3"
                        target_duration = voice_length / 1000.0 if voice_length > 0 else None
                        any_to_mp3(silk_file_path, mp3_file_path, target_duration=target_duration)
                        
                        # 使用MP3文件
                        if os.path.exists(mp3_file_path):
                            # 转换成功后删除原始SILK文件，因为我们只需要MP3文件
                            os.remove(silk_file_path)  # 删除临时SILK文件
                            # 直接将self.content更新为MP3文件路径
                            self.content = mp3_file_path
                            logger.info(f"[wxpad] 语音处理完成: {mp3_file_path}, 时长: {target_duration}秒")
                        else:
                            logger.warning(f"[wxpad] MP3转换失败，使用SILK文件: {silk_file_path}")
                    else:
                        logger.error("[wxpad] API响应中没有Base64语音数据")
                else:
                    logger.error(f"[wxpad] 语音下载API失败: {voice_response}")
            
            except ET.ParseError as e:
                logger.error(f"[wxpad] 解析语音XML失败: {e}, 内容: {content_xml[:50]}...")
                
        except Exception as e:
            logger.error(f"[wxpad] 语音文件下载异常: {e}", exc_info=True)

    def _extract_cdn_info_from_xml(self, xml_content: str) -> dict:
        """从XML内容中提取图片CDN信息"""
        try:
            import xml.etree.ElementTree as ET
            
            # 清理XML前缀（处理群聊消息中的用户ID前缀）
            xml_start = xml_content.find('<?xml')
            if xml_start != -1:
                xml_content = xml_content[xml_start:]
            
            root = ET.fromstring(xml_content)
            img_element = root.find('img')

            if img_element is None:
                return {}

            return {
                'aes_key': img_element.get('aeskey'),
                'md5': img_element.get('md5'),
                'big_url': img_element.get('cdnbigimgurl'),
                'mid_url': img_element.get('cdnmidimgurl'),
                'thumb_url': img_element.get('cdnthumburl')
            }

        except Exception:
            return {}

    def download_image(self):
        """下载图片使用wxpad库的CDN接口"""
        try:
            import os
            
            if not self.client:
                return

            content_dict = self.msg_data.get('Content', {})
            content_xml = content_dict.get('str', content_dict.get('string', ''))
            if not content_xml:
                return

            cdn_info = self._extract_cdn_info_from_xml(content_xml)
            if not cdn_info.get('aes_key'):
                return

            # 使用MD5作为文件名，方便查找
            if cdn_info.get('md5'):
                self.content = TmpDir().path() + cdn_info['md5'] + ".jpg"
            else:
                base_path = os.path.splitext(self.content)[0]
                self.content = base_path + ".jpg"

            # 检查文件是否已存在，避免重复下载
            if os.path.exists(self.content):
                logger.info(f"[wxpad] 图片文件已存在: {self.content}")
                return

            # 按优先级尝试下载：高清 -> 正常 -> 缩略图
            download_urls = [
                (1, cdn_info.get('big_url')),  # 高清
                (2, cdn_info.get('mid_url')),  # 正常
                (3, cdn_info.get('thumb_url'))  # 缩略图
            ]

            for file_type, cdn_url in download_urls:
                if not cdn_url:
                    continue
                    
                result = self.client.send_cdn_download(
                    aes_key=cdn_info['aes_key'],
                    file_type=file_type,
                    file_url=cdn_url
                )
                
                if result and isinstance(result, dict) and result.get("Code") == 200:
                    image_data = result.get("Data", {}).get("FileData")
                    if image_data and self._save_image_data(image_data):
                        logger.info(f"[wxpad] 图片下载成功: {self.content}")
                        return
                
        except Exception as e:
            logger.error(f"[wxpad] 图片下载异常: {e}")

    def download_refer_image(self):
        """下载引用图片消息中的图片"""
        try:
            import os
            import html
            
            if not self.client or not hasattr(self, '_refer_content'):
                return

            # 解码HTML实体
            refer_content = html.unescape(self._refer_content)
            
            # 提取引用图片的CDN信息
            cdn_info = self._extract_cdn_info_from_xml(refer_content)
            if not cdn_info.get('aes_key'):
                logger.error("[wxpad] 引用图片CDN信息提取失败")
                return

            # 使用MD5作为文件名，方便查找
            if cdn_info.get('md5'):
                self.content = TmpDir().path() + cdn_info['md5'] + ".jpg"
            else:
                base_path = os.path.splitext(self.content)[0]
                self.content = base_path + ".jpg"

            # 检查文件是否已存在，避免重复下载
            if os.path.exists(self.content):
                logger.info(f"[wxpad] 引用图片文件已存在: {self.content}")
                return

            # 按优先级尝试下载：高清 -> 正常 -> 缩略图
            download_urls = [
                (1, cdn_info.get('big_url')),  # 高清
                (2, cdn_info.get('mid_url')),  # 正常
                (3, cdn_info.get('thumb_url'))  # 缩略图
            ]

            for file_type, cdn_url in download_urls:
                if not cdn_url:
                    continue
                    
                result = self.client.send_cdn_download(
                    aes_key=cdn_info['aes_key'],
                    file_type=file_type,
                    file_url=cdn_url
                )
                
                if result and isinstance(result, dict) and result.get("Code") == 200:
                    image_data = result.get("Data", {}).get("FileData")
                    if image_data and self._save_image_data(image_data):
                        logger.info(f"[wxpad] 引用图片下载成功: {self.content}")
                        return

            logger.warning("[wxpad] 所有引用图片下载尝试均失败")

        except Exception as e:
            logger.error(f"[wxpad] 引用图片下载异常: {e}")

    def _save_image_data(self, image_data):
        """保存图片数据到文件"""
        try:
            import os
            import base64
            
            os.makedirs(os.path.dirname(self.content), exist_ok=True)

            # 处理不同类型的图片数据
            if isinstance(image_data, str):
                if ',' in image_data and 'base64' in image_data:
                    image_data = image_data.split(',', 1)[1]
                image_bytes = base64.b64decode(image_data.strip())
            elif isinstance(image_data, bytes):
                image_bytes = image_data
            else:
                return False

            if not image_bytes:
                return False

            # 强制使用JPG扩展名
            base_path = os.path.splitext(self.content)[0]
            self.content = base_path + ".jpg"

            with open(self.content, 'wb') as f:
                f.write(image_bytes)
            
            return True

        except Exception as e:
            logger.error(f"[wxpad] 保存图片失败: {e}")
            return False

    def _extract_video_info_from_xml(self, xml_content: str) -> dict:
        """从XML内容中提取视频CDN信息"""
        try:
            import xml.etree.ElementTree as ET
            
            # 清理XML前缀（处理群聊消息中的用户ID前缀）
            xml_start = xml_content.find('<?xml')
            if xml_start != -1:
                xml_content = xml_content[xml_start:]
            
            root = ET.fromstring(xml_content)
            video_element = root.find('videomsg')

            if video_element is None:
                return {}

            return {
                'aes_key': video_element.get('aeskey'),
                'md5': video_element.get('md5'),
                'cdn_video_url': video_element.get('cdnvideourl'),
                'cdn_thumb_url': video_element.get('cdnthumburl'),
                'cdn_thumb_aes_key': video_element.get('cdnthumbaeskey'),
                'length': video_element.get('length'),
                'play_length': video_element.get('playlength')
            }

        except Exception as e:
            logger.error(f"[wxpad] 解析视频XML失败: {e}")
            return {}
            
    def download_video(self):
        """下载视频使用wxpad库的CDN接口"""
        try:
            import os
            import base64
            
            if not self.client:
                logger.error("[wxpad] 没有客户端实例，无法下载视频")
                return

            content_dict = self.msg_data.get('Content', {})
            content_xml = content_dict.get('str', content_dict.get('string', ''))
            if not content_xml:
                logger.error("[wxpad] 没有找到视频XML内容")
                return

            video_info = self._extract_video_info_from_xml(content_xml)
            if not video_info.get('aes_key') or not video_info.get('cdn_video_url'):
                logger.error("[wxpad] 视频CDN信息提取失败")
                return

            # 使用MD5作为文件名，方便查找
            if video_info.get('md5'):
                self.content = TmpDir().path() + video_info['md5'] + ".mp4"
            
            # 检查文件是否已存在，避免重复下载
            if os.path.exists(self.content):
                return

            # 下载视频文件
            result = self.client.send_cdn_download(
                aes_key=video_info['aes_key'],
                file_type=4,  # 视频类型为4
                file_url=video_info['cdn_video_url']
            )
            
            if result and result.get("Code") == 200:
                video_data = result.get("Data", {}).get("FileData")
                if video_data:
                    try:
                        os.makedirs(os.path.dirname(self.content), exist_ok=True)
                        with open(self.content, 'wb') as f:
                            f.write(base64.b64decode(video_data))
                        logger.info(f"[wxpad] 视频下载成功: {self.content}")
                    except Exception as e:
                        logger.error(f"[wxpad] 保存视频失败: {e}")
                else:
                    logger.error("[wxpad] API响应中没有FileData字段")
            else:
                logger.error(f"[wxpad] 视频下载API失败: {result}")
                
        except Exception as e:
            logger.error(f"[wxpad] 视频下载异常: {e}")

    def download_file(self):
        """下载文件使用wxpad库的CDN接口"""
        try:
            import os
            import base64
            
            if not self.client:
                logger.error("[wxpad] 没有客户端实例，无法下载文件")
                return

            if not hasattr(self, '_file_info') or not self._file_info:
                logger.error("[wxpad] 文件信息不存在")
                return
                
            file_info = self._file_info
            if not file_info.get('aeskey') or not file_info.get('cdnattachurl'):
                logger.error("[wxpad] 文件CDN信息提取失败")
                return

            # 使用MD5作为文件名，方便查找
            if file_info.get('md5'):
                file_ext = f".{file_info['fileext']}" if file_info.get('fileext') else ""
                self.content = TmpDir().path() + file_info['md5'] + file_ext
            
            # 检查文件是否已存在，避免重复下载
            if os.path.exists(self.content):
                logger.info(f"[wxpad] 文件已存在: {self.content}")
                return

            # 下载文件
            result = self.client.send_cdn_download(
                aes_key=file_info['aeskey'],
                file_type=5,  # 文件类型为5
                file_url=file_info['cdnattachurl']
            )
            
            if result and result.get("Code") == 200:
                file_data = result.get("Data", {}).get("FileData")
                if file_data:
                    try:
                        os.makedirs(os.path.dirname(self.content), exist_ok=True)
                        with open(self.content, 'wb') as f:
                            f.write(base64.b64decode(file_data))
                        logger.info(f"[wxpad] 文件下载成功: {self.content} ({file_info.get('title')})")
                    except Exception as e:
                        logger.error(f"[wxpad] 保存文件失败: {e}", exc_info=True)
                else:
                    logger.error("[wxpad] API响应中没有FileData字段")
            else:
                logger.error(f"[wxpad] 文件下载API失败: {result}")
                
        except Exception as e:
            logger.error(f"[wxpad] 文件下载异常: {e}", exc_info=True)

    def prepare(self):
        if self._prepare_fn:
            self._prepare_fn()
            
    def _clear_image_cache_for_text(self):
        """为普通文本消息清理图片缓存，避免误用之前的多模态内容"""
        try:
            from common import memory
            from config import conf
            
            # 获取正确的session_id
            if self.is_group:
                group_id = self.other_user_id
                actual_user_id = self.actual_user_id
                
                # 检查是否是共享会话群
                group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                group_name = self.other_user_nickname
                
                if group_name in group_chat_in_one_session or "ALL_GROUP" in group_chat_in_one_session:
                    session_id = group_id  # 共享会话群
                else:
                    session_id = f"{actual_user_id}@@{group_id}"  # 非共享会话群
            else:
                session_id = self.other_user_id  # 私聊
            
            # 清理可能存在的图片缓存
            if session_id in memory.USER_IMAGE_CACHE:
                del memory.USER_IMAGE_CACHE[session_id]
                logger.debug(f"[wxpad] 普通文本消息清理了图片缓存: session_id={session_id}")
                
        except Exception as e:
            logger.error(f"[wxpad] 清理图片缓存异常: {e}")
            

    def _is_non_user_message(self, msg_source: str, from_user_id, content: str = '', msg_type: int = 0) -> bool:
        """检查消息是否来自非用户账号（如公众号、腾讯游戏、微信团队等）
        
        Args:
            msg_source: 消息的MsgSource字段内容
            from_user_id: 消息发送者的ID (可能是字符串或者字典)
            content: 消息内容（可选）
            msg_type: 消息类型（可选）
            
        Returns:
            bool: 如果是非用户消息返回True，否则返回False
            
        Note:
            通过以下方式判断是否为非用户消息：
            1. 检查MsgSource中是否包含特定标签
            2. 检查发送者ID是否为特殊账号或以特定前缀开头
            3. 检查特定消息类型和内容模式
        """
        # 确保from_user_id是字符串
        if isinstance(from_user_id, dict):
            from_user_id = from_user_id.get('string', '')
        
        # 将from_user_id转换为字符串，确保类型安全
        from_user_id = str(from_user_id)
        
        # 检查发送者ID
        special_accounts = ["Tencent-Games", "weixin", "newsapp", "fmessage"]
        if from_user_id in special_accounts or from_user_id.startswith("gh_"):
            logger.debug(f"[wxpad] non-user message detected by sender id: {from_user_id}")
            return True

        # 检查消息源中的标签
        # 示例:<msgsource>\n\t<tips>3</tips>\n\t<bizmsg>\n\t\t<bizmsgshowtype>0</bizmsgshowtype>\n\t\t<bizmsgfromuser><![CDATA[weixin]]></bizmsgfromuser>\n\t</bizmsg>
        non_user_indicators = [
            "<tips>3</tips>",
            "<bizmsgshowtype>",
            "</bizmsgshowtype>",
            "<bizmsgfromuser>",
            "</bizmsgfromuser>"
        ]
        if any(indicator in msg_source for indicator in non_user_indicators):
            logger.debug(f"[wxpad] non-user message detected by msg_source indicators")
            return True

        # 检查系统提示消息（如添加好友成功）
        if msg_type == 10000:
            system_notifications = [
                "你已添加了",
                "现在可以开始聊天了",
                "已成为好友",
                "added you as a contact"
            ]
            if any(notification in content for notification in system_notifications):
                logger.debug(f"[wxpad] non-user message detected by system notification content: {content}")
                return True

        return False

    def download_refer_image_for_multimodal(self):
        """使用现有方法下载引用图片，然后加入多模态缓存"""
        try:
            import os
            from common import memory
            from config import conf
            
            # 保存原始的文本内容
            original_content = self.content
            
            # 调用下载方法
            self.download_refer_image()
            
            # 保存图片路径
            image_path = self.content
            
            # 检查图片是否下载成功
            if image_path and os.path.exists(image_path):
                # 获取正确的session_id
                if self.is_group:
                    group_id = self.other_user_id
                    actual_user_id = self.actual_user_id
                    
                    # 检查是否是共享会话群
                    group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                    group_name = self.other_user_nickname
                    
                    if group_name in group_chat_in_one_session or "ALL_GROUP" in group_chat_in_one_session:
                        session_id = group_id  # 共享会话群
                    else:
                        session_id = f"{actual_user_id}@@{group_id}"  # 非共享会话群
                else:
                    session_id = self.other_user_id  # 私聊
                
                # 将引用图片加入用户图片缓存，用于多模态处理
                memory.USER_IMAGE_CACHE[session_id] = {
                    "path": image_path,
                    "msg": self
                }
                
                logger.info(f"[wxpad] 引用图片已加入多模态缓存: session_id={session_id}")
            
            # 恢复原始的文本内容
            self.content = original_content
            
        except Exception as e:
            logger.error(f"[wxpad] 引用图片多模态处理异常: {e}")

    def download_refer_file_for_multimodal(self):
        """下载引用文件并加入多模态缓存供AI处理"""
        try:
            import os
            from common import memory
            from config import conf
            
            if not self.client or not hasattr(self, '_refer_file_info'):
                return

            # 保存原始的文本内容
            original_content = self.content
            
            # 获取引用文件信息
            refer_file_info = self._refer_file_info.get('file_info', {})
            if not refer_file_info:
                return
            
            # 构建文件路径，使用MD5作为文件名
            file_ext = refer_file_info.get('fileext', '')
            if refer_file_info.get('md5'):
                file_name = refer_file_info['md5'] + (f".{file_ext}" if file_ext else "")
            else:
                file_name = f"refer_file_{uuid.uuid4()}.{file_ext}" if file_ext else f"refer_file_{uuid.uuid4()}"
            
            file_path = TmpDir().path() + file_name
            
            # 检查文件是否已存在，避免重复下载
            if not os.path.exists(file_path):
                # 临时设置文件信息和路径，复用现有下载逻辑
                self._file_info = {
                    'title': refer_file_info.get('title', ''),
                    'fileext': file_ext,
                    'attachid': refer_file_info.get('attachid', ''),
                    'cdnattachurl': refer_file_info.get('cdnattachurl', ''),
                    'aeskey': refer_file_info.get('aeskey', ''),
                    'totallen': refer_file_info.get('totallen', '0'),
                    'md5': refer_file_info.get('md5', '')
                }
                temp_content = self.content
                self.content = file_path
                
                # 调用现有的文件下载方法
                self.download_file()
                
                # 恢复原始内容
                self.content = temp_content
            
            # 检查文件是否下载成功
            if file_path and os.path.exists(file_path):
                # 获取正确的session_id
                if self.is_group:
                    group_id = self.other_user_id
                    actual_user_id = self.actual_user_id
                    
                    # 检查是否是共享会话群
                    group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                    group_name = self.other_user_nickname
                    
                    if group_name in group_chat_in_one_session or "ALL_GROUP" in group_chat_in_one_session:
                        session_id = group_id  # 共享会话群
                    else:
                        session_id = f"{actual_user_id}@@{group_id}"  # 非共享会话群
                else:
                    session_id = self.other_user_id  # 私聊
                
                # 将引用文件加入用户图片缓存（复用现有缓存机制），用于AI处理
                memory.USER_IMAGE_CACHE[session_id] = {
                    "path": file_path,
                    "msg": self,
                    "file_info": refer_file_info,
                    "type": "file"  # 标记为文件类型
                }
                
                logger.info(f"[wxpad] 引用文件已加入缓存: session_id={session_id}, file={refer_file_info.get('title')}")
            
            # 恢复原始的文本内容
            self.content = original_content
            
        except Exception as e:
            logger.error(f"[wxpad] 引用文件多模态处理异常: {e}")

    def _process_refer_message(self, refermsg, appmsg):
        """统一处理引用消息（图片、文件、文本）"""
        refer_type = refermsg.find('type')
        displayname = refermsg.find('displayname').text if refermsg.find('displayname') is not None else ''
        refer_content = refermsg.find('content').text if refermsg.find('content') is not None else ''
        title = appmsg.find('title').text if appmsg.find('title') is not None else ''
        
        self.ctype = ContextType.TEXT  # 统一设置为TEXT类型
        
        if refer_type is not None and refer_type.text == '3':
            # 引用图片消息
            refer_desc = "图片"
            self._refer_content = refer_content
            self._refer_image_info = {'displayname': displayname, 'has_refer_image': True}
            self._prepare_fn = self.download_refer_image_for_multimodal
            
        elif refer_type is not None and refer_type.text == '49':
            # 引用文件消息
            import html
            refer_file_info = self._parse_refer_file_content(html.unescape(refer_content))
            
            if refer_file_info and refer_file_info.get('file_type') == '6':
                # 构建文件显示名称
                file_title = refer_file_info.get('title', '未知文件')
                file_ext = refer_file_info.get('fileext', '')
                if file_ext and not file_title.lower().endswith(f'.{file_ext.lower()}'):
                    refer_desc = f"文件: {file_title}.{file_ext}"
                else:
                    refer_desc = f"文件: {file_title}"
                
                # 保存引用文件信息
                self._refer_content = refer_content
                self._refer_file_info = {'displayname': displayname, 'has_refer_file': True, 'file_info': refer_file_info}
                self._prepare_fn = self.download_refer_file_for_multimodal
            else:
                # 引用的不是文件消息，按普通文本处理 - 需要清理可能存在的图片缓存
                refer_desc = refer_content
                self._prepare_fn = self._clear_image_cache_for_text
        else:
            # 引用普通文本消息 - 需要清理可能存在的图片缓存
            refer_desc = refer_content
            self._prepare_fn = self._clear_image_cache_for_text
        
        # 设置统一的内容格式
        if title.strip():
            self.content = f"{title.strip()}\n\n[引用了 {displayname} 的{refer_desc}]"
        else:
            self.content = f"[引用了 {displayname} 的{refer_desc}]"

    def _parse_refer_file_content(self, refer_content: str) -> dict:
        """解析引用文件消息的content，提取文件信息
        
        Args:
            refer_content: 引用消息的content字段（已解码HTML实体）
            
        Returns:
            dict: 包含文件信息的字典，如果解析失败返回空字典
        """
        try:
            import xml.etree.ElementTree as ET
            
            # 尝试解析XML内容
            root = ET.fromstring(refer_content)
            appmsg = root.find('appmsg')
            
            if appmsg is None:
                logger.debug("[wxpad] 引用内容中没有找到appmsg元素")
                return {}
            
            # 获取消息类型
            msg_type_node = appmsg.find('type')
            if msg_type_node is None or msg_type_node.text != '6':
                logger.debug(f"[wxpad] 引用消息类型不是文件: {msg_type_node.text if msg_type_node is not None else 'None'}")
                return {}
            
            # 提取文件信息
            title = appmsg.find('title').text if appmsg.find('title') is not None else "未知文件"
            appattach = appmsg.find('appattach')
            
            if appattach is None:
                logger.debug("[wxpad] 引用文件消息中没有找到appattach元素")
                return {}
            
            file_info = {
                'file_type': '6',
                'title': title,
                'fileext': appattach.find('fileext').text if appattach.find('fileext') is not None else "",
                'attachid': appattach.find('attachid').text if appattach.find('attachid') is not None else "",
                'cdnattachurl': appattach.find('cdnattachurl').text if appattach.find('cdnattachurl') is not None else "",
                'aeskey': appattach.find('aeskey').text if appattach.find('aeskey') is not None else "",
                'totallen': appattach.find('totallen').text if appattach.find('totallen') is not None else "0",
                'md5': appmsg.find('md5').text if appmsg.find('md5') is not None else ""
            }
            
            logger.debug(f"[wxpad] 成功解析引用文件信息: {file_info['title']}")
            return file_info
            
        except ET.ParseError as e:
            logger.warning(f"[wxpad] 解析引用文件XML失败: {e}")
            return {}
        except Exception as e:
            logger.error(f"[wxpad] 解析引用文件内容异常: {e}")
            return {}