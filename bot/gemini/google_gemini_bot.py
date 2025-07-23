"""
Google Gemini Bot - 多模态聊天机器人

支持功能：
- 文本对话（TEXT）
- 图片分析（IMAGE）  
- 文件处理（FILE）
- 语音转录（VOICE）
- 视频分析（VIDEO）
- 多轮对话记忆
- 自定义API Base URL支持
- 流式响应（可选）
"""

import os
import time
from typing import List, Dict, Any, Optional, Union

from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from bot.bot import Bot
from bot.session_manager import SessionManager
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from common.log import logger
from config import conf
from common import memory

# 导入新的Google GenAI SDK
try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
    logger.info("[Gemini] 使用 Google GenAI SDK")
except ImportError as e:
    logger.error(f"[Gemini] 无法导入Google GenAI SDK: {e}")
    logger.error("[Gemini] 请安装: pip install google-genai>=0.8.0")
    raise ImportError("Google GenAI SDK is required for Gemini Bot")


class GoogleGeminiBot(Bot):
    def __init__(self):
        super().__init__()
        self.api_key = conf().get("gemini_api_key")
        self.api_base = conf().get("gemini_api_base")
        
        # 复用chatGPT的token计算方式和会话管理
        self.sessions = SessionManager(ChatGPTSession, model=conf().get("model") or "gpt-3.5-turbo")
        self.model = conf().get("model") or "gemini-2.0-flash"
        if self.model == "gemini":
            self.model = "gemini-2.0-flash"
            
        # 初始化客户端
        self.client = None
        self._initialize_client()
        
        # 支持的文件类型（基于文档）
        self.supported_image_types = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
        self.supported_file_types = {'.pdf', '.txt', '.md', '.doc', '.docx', '.csv', '.json'}
        self.max_file_size = 50 * 1024 * 1024  # 50MB限制
        
        logger.info(f"[Gemini] 初始化完成，模型: {self.model}")

    def _initialize_client(self):
        """初始化Gemini客户端"""
        try:
            if self.api_base:
                # 配置HTTP选项以支持自定义端点
                http_options = {"base_url": self.api_base}
                self.client = genai.Client(api_key=self.api_key, http_options=http_options)
                logger.info(f"[Gemini] 客户端初始化成功，自定义端点: {self.api_base}")
            else:
                self.client = genai.Client(api_key=self.api_key)
                logger.info(f"[Gemini] 客户端初始化成功，官方端点")
                    
        except Exception as e:
            logger.error(f"[Gemini] 客户端初始化失败: {e}")
            raise

    def reply(self, query, context=None) -> Reply:
        """处理各种类型的消息并生成回复"""
        try:
            logger.info(f"[Gemini] 收到消息，类型: {context.type}, 查询: {query[:50]}...")
            
            # 获取会话ID和会话
            session_id = context.get("session_id")
            session = self.sessions.session_query(query, session_id)
            
            # 根据消息类型处理
            if context.type == ContextType.TEXT:
                return self._handle_text_message(query, session, context)
            elif context.type == ContextType.IMAGE:
                return self._handle_image_message(query, session, context)
            elif context.type == ContextType.FILE:
                return self._handle_file_message(query, session, context)
            elif context.type == ContextType.VOICE:
                return self._handle_voice_message(query, session, context)
            elif context.type == ContextType.VIDEO:
                return self._handle_video_message(query, session, context)
            else:
                logger.warn(f"[Gemini] 不支持的消息类型: {context.type}")
                return Reply(ReplyType.TEXT, f"暂不支持 {context.type} 类型的消息")
                
        except Exception as e:
            logger.error(f"[Gemini] 处理消息时发生错误: {e}", exc_info=True)
            return Reply(ReplyType.TEXT, f"处理消息时发生错误: {str(e)}")

    def _handle_text_message(self, query: str, session, context) -> Reply:
        """处理纯文本消息"""
        try:
            # 检查是否有多模态缓存需要处理
            session_id = context.get("session_id")
            img_cache = memory.USER_IMAGE_CACHE.get(session_id)
            
            if img_cache:
                # 有图片缓存，使用多模态处理
                return self._handle_multimodal_message(query, session, context, img_cache)
            else:
                # 纯文本处理
                return self._generate_content([query], session, context)
                    
        except Exception as e:
            logger.error(f"[Gemini] 处理文本消息错误: {e}")
            return Reply(ReplyType.TEXT, f"处理文本消息时发生错误: {str(e)}")

    def _handle_image_message(self, query: str, session, context) -> Reply:
        """处理图片消息"""
        try:
            # 从上下文获取图片路径
            image_path = context.kwargs.get('msg').content
            if not image_path or not os.path.exists(image_path):
                return Reply(ReplyType.TEXT, "图片文件不存在")
            
            # 验证图片格式
            if not self._is_supported_image(image_path):
                return Reply(ReplyType.TEXT, "不支持的图片格式")
                
            # 构建多模态内容
            contents = self._build_image_contents(query, image_path)
            
            # 生成回复
            response = self._generate_content(contents, session, context)
            
            # 清理图片缓存
            self._cleanup_image_cache(context)
            
            return response
            
        except Exception as e:
            logger.error(f"[Gemini] 处理图片消息错误: {e}")
            return Reply(ReplyType.TEXT, f"处理图片时发生错误: {str(e)}")

    def _handle_file_message(self, query: str, session, context) -> Reply:
        """处理文件消息"""
        try:
            # 从上下文获取文件路径
            file_path = context.kwargs.get('msg').content
            if not file_path or not os.path.exists(file_path):
                return Reply(ReplyType.TEXT, "文件不存在")
            
            # 验证文件格式和大小
            if not self._is_supported_file(file_path):
                return Reply(ReplyType.TEXT, "不支持的文件格式")
                
            if not self._check_file_size(file_path):
                return Reply(ReplyType.TEXT, "文件过大，请上传小于50MB的文件")
            
            # 上传文件到Gemini API
            uploaded_file = self._upload_file_to_gemini(file_path)
            if not uploaded_file:
                return Reply(ReplyType.TEXT, "文件上传失败")
            
            # 构建文件内容
            contents = self._build_file_contents(query, uploaded_file)
            
            # 生成回复
            response = self._generate_content(contents, session, context)
            
            # 清理文件缓存
            self._cleanup_file_cache(context)
            
            return response
            
        except Exception as e:
            logger.error(f"[Gemini] 处理文件消息错误: {e}")
            return Reply(ReplyType.TEXT, f"处理文件时发生错误: {str(e)}")

    def _handle_voice_message(self, query: str, session, context) -> Reply:
        """处理语音消息（转为文本后处理）"""
        try:
            # 语音已经被转录为文本，直接处理文本
            return self._handle_text_message(query, session, context)
        except Exception as e:
            logger.error(f"[Gemini] 处理语音消息错误: {e}")
            return Reply(ReplyType.TEXT, f"处理语音时发生错误: {str(e)}")

    def _handle_video_message(self, query: str, session, context) -> Reply:
        """处理视频消息（当作文件处理）"""
        try:
            # 将视频当作文件处理
            return self._handle_file_message(query, session, context)
        except Exception as e:
            logger.error(f"[Gemini] 处理视频消息错误: {e}")
            return Reply(ReplyType.TEXT, f"处理视频时发生错误: {str(e)}")

    def _handle_multimodal_message(self, query: str, session, context, img_cache) -> Reply:
        """处理包含图片或文件缓存的多模态消息"""
        try:
            file_path = img_cache.get("path")
            cache_type = img_cache.get("type", "image")  # 默认为图片类型
            
            if not file_path or not os.path.exists(file_path):
                # 清理无效缓存
                self._cleanup_image_cache(context)
                return self._handle_text_message(query, session, context)
            
            # 根据缓存类型处理不同的多模态内容
            if cache_type == "file":
                # 处理引用的文件
                contents = self._build_cached_file_contents(query, file_path, img_cache)
            else:
                # 处理引用的图片（默认）
                contents = self._build_image_contents(query, file_path)
            
            # 生成回复
            response = self._generate_content(contents, session, context)
            
            # 清理缓存
            self._cleanup_image_cache(context)
            
            return response
            
        except Exception as e:
            logger.error(f"[Gemini] 处理多模态消息错误: {e}")
            # 清理缓存并回退到文本处理
            self._cleanup_image_cache(context)
            return self._handle_text_message(query, session, context)

    def _build_image_contents(self, query: str, image_path: str) -> List:
        """构建包含图片的内容列表"""
        try:
            contents = []
            
            # 添加文本部分
            if query.strip():
                contents.append(query.strip())
            
            # 添加图片部分
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            
            # 检测MIME类型
            mime_type = self._get_image_mime_type(image_path)
            
            # 使用新SDK的方式添加图片
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
            
            return contents
            
        except Exception as e:
            logger.error(f"[Gemini] 构建图片内容失败: {e}")
            return [query] if query.strip() else ["请描述这张图片"]

    def _build_file_contents(self, query: str, uploaded_file) -> List:
        """构建包含文件的内容列表"""
        try:
            contents = []
            
            # 添加文本部分
            if query.strip():
                contents.append(query.strip())
            else:
                contents.append("请总结这个文件的内容")
            
            # 添加文件引用
            contents.append(uploaded_file)
            
            return contents
            
        except Exception as e:
            logger.error(f"[Gemini] 构建文件内容失败: {e}")
            return [query] if query.strip() else ["请总结这个文件的内容"]

    def _build_cached_file_contents(self, query: str, file_path: str, cache_info: dict) -> List:
        """构建包含缓存文件的内容列表（用于引用文件的多模态处理）"""
        try:
            # 检查文件格式是否支持
            if not self._is_supported_file(file_path):
                logger.warning(f"[Gemini] 引用文件格式不支持: {file_path}")
                return [query] if query.strip() else ["引用的文件格式不支持"]
            
            # 检查文件大小
            if not self._check_file_size(file_path):
                logger.warning(f"[Gemini] 引用文件过大: {file_path}")
                return [query] if query.strip() else ["引用的文件过大"]
            
            # 上传文件到Gemini API
            uploaded_file = self._upload_file_to_gemini(file_path)
            if not uploaded_file:
                logger.error(f"[Gemini] 引用文件上传失败: {file_path}")
                # 如果上传失败，降级为文本处理，包含文件信息
                file_info = cache_info.get("file_info", {})
                file_title = file_info.get("title", "文件")
                fallback_text = f"{query}\n\n[引用了文件：{file_title}，但处理失败]" if query.strip() else f"[引用了文件：{file_title}，但处理失败]"
                return [fallback_text]
            
            # 构建包含文件的内容
            contents = []
            
            # 添加文本部分和文件描述
            file_info = cache_info.get("file_info", {})
            file_title = file_info.get("title", "文件")
            
            if query.strip():
                # 如果有查询内容，说明用户对引用的文件有特定问题
                contents.append(f"{query.strip()}\n\n(以下是引用的文件：{file_title})")
            else:
                # 如果没有查询内容，请求分析引用的文件
                contents.append(f"请分析这个引用的文件：{file_title}")
            
            # 添加文件引用
            contents.append(uploaded_file)
            
            logger.info(f"[Gemini] 成功构建引用文件内容: {file_title}")
            return contents
            
        except Exception as e:
            logger.error(f"[Gemini] 构建引用文件内容失败: {e}")
            # 降级处理
            file_info = cache_info.get("file_info", {}) if isinstance(cache_info, dict) else {}
            file_title = file_info.get("title", "文件")
            fallback_text = f"{query}\n\n[引用了文件：{file_title}，但处理失败]" if query.strip() else f"[引用了文件：{file_title}，但处理失败]"
            return [fallback_text]

    def _generate_content(self, contents: List, session, context) -> Reply:
        """生成内容回复"""
        try:
            # 获取系统提示词
            system_prompt = session.system_prompt if session.system_prompt else ""
            
            # 构建对话历史上下文（将历史对话整合到当前内容中）
            final_contents = []
            
            # 如果有对话历史，构建上下文
            if hasattr(session, 'messages') and len(session.messages) > 1:
                # 获取历史对话（排除system role和当前消息）
                history_messages = [msg for msg in session.messages[:-1] if msg.get('role') != 'system']
                if history_messages:
                    context_text = "\n[对话历史]:\n"
                    for msg in history_messages[-6:]:  # 只保留最近3轮对话
                        if msg.get('role') == 'user':
                            context_text += f"用户: {msg.get('content', '')}\n"
                        elif msg.get('role') == 'assistant':
                            context_text += f"助手: {msg.get('content', '')}\n"
                    context_text += "\n[当前对话]:\n"
                    
                    # 处理当前用户输入
                    if isinstance(contents, list):
                        text_parts = []
                        non_text_parts = []
                        
                        for content in contents:
                            if isinstance(content, str):
                                text_parts.append(content)
                            else:
                                non_text_parts.append(content)
                        
                        # 构建包含历史的文本内容
                        if text_parts:
                            combined_text = context_text + " ".join(text_parts)
                            final_contents.append(combined_text)
                        elif non_text_parts:
                            final_contents.append(context_text + "请分析这个内容：")
                        
                        # 添加非文本内容
                        final_contents.extend(non_text_parts)
                    else:
                        final_contents = [context_text + str(contents)]
                else:
                    # 没有历史对话，直接使用原始内容
                    final_contents = contents if isinstance(contents, list) else [contents]
            else:
                # 没有历史对话，直接使用原始内容
                final_contents = contents if isinstance(contents, list) else [contents]
            
            # 生成配置 - 使用正确的system_instruction参数
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt.strip() else None,
                temperature=0.7,
                top_p=0.95,
                top_k=20,
                max_output_tokens=2000,
                safety_settings=[
                    types.SafetySetting(
                        category='HARM_CATEGORY_HATE_SPEECH',
                        threshold='BLOCK_NONE',
                    ),
                    types.SafetySetting(
                        category='HARM_CATEGORY_HARASSMENT', 
                        threshold='BLOCK_NONE',
                    ),
                    types.SafetySetting(
                        category='HARM_CATEGORY_SEXUALLY_EXPLICIT',
                        threshold='BLOCK_NONE',
                    ),
                    types.SafetySetting(
                        category='HARM_CATEGORY_DANGEROUS_CONTENT',
                        threshold='BLOCK_NONE',
                    ),
                ]
            )
            
            # 记录使用的系统提示词
            if system_prompt.strip():
                logger.debug(f"[Gemini] 使用系统提示词: {system_prompt[:100]}...")
            
            # 发送请求
            logger.debug(f"[Gemini] 发送内容数量: {len(final_contents)}")
            response = self.client.models.generate_content(
                model=self.model,
                contents=final_contents,
                config=config
            )
            
            if response and hasattr(response, 'candidates') and response.candidates:
                # 处理多模态响应（可能包含文本和图片）
                candidate = response.candidates[0]
                parts = candidate.content.parts if hasattr(candidate.content, 'parts') else []
                
                text_parts = []
                image_parts = []
                
                # 分离文本和图片内容
                for part in parts:
                    if hasattr(part, 'text') and part.text:
                        text_parts.append(part.text.strip())
                    elif hasattr(part, 'inline_data') and part.inline_data:
                        image_parts.append(part.inline_data)
                
                # 获取会话ID用于保存回复
                session_id = context.get("session_id")
                
                # 如果有图片生成，处理图片
                if image_parts:
                    logger.info(f"[Gemini] 检测到图片生成，图片数量: {len(image_parts)}")
                    
                    # 保存生成的图片
                    image_paths = []
                    for i, inline_data in enumerate(image_parts):
                        image_path = self._save_generated_image(inline_data, session_id, i)
                        if image_path:
                            image_paths.append(image_path)
                    
                    # 如果有文本内容，先发送文本
                    if text_parts:
                        reply_text = '\n'.join(text_parts)
                        self.sessions.session_reply(reply_text, session_id, None)
                        
                        # 返回多媒体回复：文本 + 图片
                        import threading
                        
                        # 先返回文本回复
                        text_reply = Reply(ReplyType.TEXT, reply_text)
                        
                        # 异步发送图片
                        def send_images():
                            channel = context.get("channel")
                            if channel and image_paths:
                                import time
                                time.sleep(0.5)  # 稍微延迟确保文本先发送
                                for image_path in image_paths:
                                    image_reply = Reply(ReplyType.IMAGE, image_path)
                                    channel.send(image_reply, context)
                        
                        if image_paths:
                            thread = threading.Thread(target=send_images)
                            thread.start()
                        
                        return text_reply
                    else:
                        # 只有图片，直接返回第一张图片
                        if image_paths:
                            self.sessions.session_reply("[生成了一张图片]", session_id, None)
                            return Reply(ReplyType.IMAGE, image_paths[0])
                        else:
                            return Reply(ReplyType.TEXT, "图片生成失败")
                
                # 只有文本内容
                elif text_parts:
                    reply_text = '\n'.join(text_parts).strip()
                    logger.info(f"[Gemini] 文本内容生成成功，长度: {len(reply_text)}")
                    
                    # 使用SessionManager的标准方法保存回复到会话历史
                    self.sessions.session_reply(reply_text, session_id, None)
                    
                    return Reply(ReplyType.TEXT, reply_text)
                else:
                    logger.warn(f"[Gemini] 响应中没有可用内容")
                    return Reply(ReplyType.TEXT, "抱歉，我无法生成回复")
            else:
                logger.warn(f"[Gemini] 生成内容为空")
                return Reply(ReplyType.TEXT, "抱歉，我无法生成回复")
                
        except Exception as e:
            logger.error(f"[Gemini] 生成内容错误: {e}")
            return Reply(ReplyType.TEXT, f"生成回复时发生错误: {str(e)}")

    def _save_generated_image(self, inline_data, session_id: str, index: int = 0) -> Optional[str]:
        """保存Gemini生成的图片到本地文件"""
        try:
            import base64
            import hashlib
            from common.tmp_dir import TmpDir
            
            # 获取图片数据
            if hasattr(inline_data, 'data'):
                image_data = inline_data.data
            else:
                logger.error(f"[Gemini] inline_data没有data属性")
                return None
            
            # 获取MIME类型
            mime_type = getattr(inline_data, 'mime_type', 'image/png')
            file_ext = mime_type.split('/')[-1] if '/' in mime_type else 'png'
            
            # 解码base64图片数据
            try:
                image_bytes = base64.b64decode(image_data)
            except Exception as e:
                logger.error(f"[Gemini] base64解码失败: {e}")
                return None
            
            # 生成文件名（使用MD5哈希确保唯一性）
            hash_md5 = hashlib.md5(image_bytes).hexdigest()
            filename = f"gemini_generated_{hash_md5}_{index}.{file_ext}"
            file_path = os.path.join(TmpDir().path(), filename)
            
            # 保存图片文件
            with open(file_path, 'wb') as f:
                f.write(image_bytes)
            
            logger.info(f"[Gemini] 图片保存成功: {file_path}, 大小: {len(image_bytes)} bytes")
            return file_path
            
        except Exception as e:
            logger.error(f"[Gemini] 保存生成图片失败: {e}")
            return None

    def _upload_file_to_gemini(self, file_path: str):
        """上传文件到Gemini API"""
        try:
            if not self.client:
                return None
                
            logger.info(f"[Gemini] 开始上传文件: {file_path}")
            uploaded_file = self.client.files.upload(file=file_path)
            
            if uploaded_file:
                logger.info(f"[Gemini] 文件上传成功: {uploaded_file.name}")
                return uploaded_file
            else:
                logger.error(f"[Gemini] 文件上传失败")
                return None
                
        except Exception as e:
            logger.error(f"[Gemini] 文件上传异常: {e}")
            return None

    def _is_supported_image(self, file_path: str) -> bool:
        """检查是否支持的图片格式"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_image_types

    def _is_supported_file(self, file_path: str) -> bool:
        """检查是否支持的文件格式"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_file_types

    def _check_file_size(self, file_path: str) -> bool:
        """检查文件大小是否在限制内"""
        try:
            size = os.path.getsize(file_path)
            return size <= self.max_file_size
        except:
            return False

    def _get_image_mime_type(self, image_path: str) -> str:
        """获取图片的MIME类型"""
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg', 
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.heic': 'image/heic',
            '.heif': 'image/heif'
        }
        return mime_map.get(ext, 'image/jpeg')

    def _cleanup_image_cache(self, context):
        """清理图片缓存"""
        try:
            session_id = context.get("session_id")
            if session_id in memory.USER_IMAGE_CACHE:
                del memory.USER_IMAGE_CACHE[session_id]
                logger.debug(f"[Gemini] 清理图片缓存: {session_id}")
        except Exception as e:
            logger.error(f"[Gemini] 清理图片缓存失败: {e}")

    def _cleanup_file_cache(self, context):
        """清理文件缓存"""
        try:
            session_id = context.get("session_id")
            if session_id in memory.USER_IMAGE_CACHE:
                del memory.USER_IMAGE_CACHE[session_id]
                logger.debug(f"[Gemini] 清理文件缓存: {session_id}")
        except Exception as e:
            logger.error(f"[Gemini] 清理文件缓存失败: {e}")

    def filter_messages(self, messages: list) -> list:
        """过滤消息，保留最近的对话历史"""
        # 保留最近10轮对话
        return messages[-20:] if len(messages) > 20 else messages
