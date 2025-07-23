# Google Gemini Bot 使用指南

## 🌟 功能特性

- ✅ **多模态支持**：文本、图片、文件、语音、视频处理
- ✅ **图片生成**：支持Gemini 2.0 Flash的图片生成功能
- ✅ **多媒体回复**：自动处理文本+图片的混合回复
- ✅ **系统提示词**：支持自定义角色设定和行为模式
- ✅ **对话记忆**：维护多轮对话上下文
- ✅ **自定义端点**：支持代理或中转服务
- ✅ **流式响应**：（可选）实时回复生成

## 📋 配置说明

### 基础配置

在 `config.json` 中添加以下配置项：

```json
{
  "model": "gemini-2.0-flash",
  "gemini_api_key": "your-api-key-here",
  "gemini_api_base": "",
  "character_desc": "你是一个专业的AI助手，具有以下特点：友好、耐心、专业。你会用清晰易懂的语言回答问题，并提供有价值的帮助。"
}
```

### 配置项详解

| 配置项 | 说明 | 示例值 | 是否必需 |
|--------|------|--------|----------|
| `model` | Gemini模型名称 | `gemini-2.0-flash` | ✅ |
| `gemini_api_key` | Google AI API密钥 | `AIza...` | ✅ |
| `gemini_api_base` | 自定义API端点（可选） | `https://api.example.com` | ❌ |
| `character_desc` | 系统提示词/角色设定 | 见下方示例 | ✅ |

### 系统提示词示例

```json
{
  "character_desc": "你是一个专业的技术顾问，专长于：\n1. 软件开发和编程问题解答\n2. 技术方案设计和建议\n3. 代码审查和优化建议\n\n回答风格：简洁、准确、实用。"
}
```

## 🚀 支持的消息类型

### 1. 文本消息
- 普通对话问答
- 包含系统提示词的角色扮演
- 多轮对话记忆

### 2. 图片分析
支持格式：`.jpg`, `.jpeg`, `.png`, `.webp`, `.heic`, `.heif`

### 3. 文件处理
支持格式：`.pdf`, `.txt`, `.md`, `.doc`, `.docx`, `.csv`, `.json`
文件大小限制：50MB

### 4. 语音消息
自动转录为文本后处理

### 5. 视频分析
作为文件类型处理

## 🔧 故障排除

### 问题：系统提示词不生效

**原因**：缺少 `character_desc` 配置项

**解决方案**：
1. 在 `config.json` 中添加 `character_desc` 字段
2. 重启服务生效

### 问题：API调用失败

**检查项**：
1. ✅ `gemini_api_key` 是否正确
2. ✅ 网络是否可以访问Google AI API
3. ✅ API密钥是否有足够权限

### 问题：图片/文件处理失败

**检查项**：
1. ✅ 文件格式是否支持
2. ✅ 文件大小是否超过50MB限制
3. ✅ 文件是否损坏

## 📝 最近更新

### v2.0 - 2024-12-19
- ✅ **修复**：系统提示词不生效的问题
- ✅ **新增**：对话历史上下文支持
- ✅ **优化**：多模态内容处理流程
- ✅ **改进**：错误处理和日志记录

### 核心修复内容
1. **系统提示词集成**：使用正确的 `system_instruction` 参数（符合官方API规范）
2. **对话历史管理**：维护最近3轮对话上下文
3. **多模态优化**：改进图片和文件处理流程
4. **会话管理**：使用标准的 `session_reply()` 方法保存回复到会话历史
5. **API合规性**：遵循Google GenAI SDK官方文档的最佳实践
6. **架构一致性**：与其他Bot保持相同的会话管理模式
7. **通道兼容性**：修复wxpad通道对`BufferedReader`类型图片数据的支持

## 🔧 技术实现说明

### 系统提示词的正确实现

基于Google官方文档，我们使用了正确的API调用方式：

```python
from google.genai import types

config = types.GenerateContentConfig(
    system_instruction=system_prompt,  # 正确的参数名
    temperature=0.7,
    top_p=0.95,
    top_k=20,
    max_output_tokens=2000,
)

response = client.models.generate_content(
    model='gemini-2.0-flash-001',
    contents=contents,
    config=config
)
```

### 会话管理的正确实现

与项目中其他Bot保持一致的会话管理模式：

```python
# 🔄 标准的会话处理流程
session = self.sessions.session_query(query, session_id)  # 添加用户输入
response = self._generate_content(contents, session, context)  # 生成回复
self.sessions.session_reply(reply_text, session_id, None)  # 保存回复
```

### 与其他实现的区别

- ❌ **错误做法**：将系统提示词混合到用户消息中
- ✅ **正确做法**：使用 `system_instruction` 参数独立设置
- ✅ **优势**：更好的角色一致性和指令遵循能力
- ✅ **架构一致性**：与ChatGPT、Claude等Bot使用相同的会话管理机制

### 通道兼容性修复

修复了wxpad通道处理图片数据类型的问题：

```python
# 现在支持更多图片数据类型
elif hasattr(image_data, 'read') and hasattr(image_data, 'seek'):
    # BufferedReader或其他类似的文件对象
    image_data.seek(0)
    file_content = image_data.read()
    image_base64 = base64.b64encode(file_content).decode("utf-8")
```

**支持的图片数据类型**：
- ✅ `str`: 文件路径、URL、base64字符串
- ✅ `bytes`: 二进制图片数据
- ✅ `BytesIO`: 内存中的图片数据
- ✅ `_io.BufferedReader`: 文件对象（新增支持）
- ✅ `PIL.Image`: PIL图片对象

## 🎨 图片生成功能

Gemini 2.0 Flash支持强大的图片生成功能：

### 支持的生成类型
- 🎨 **艺术创作**：绘画、插图、艺术风格图片
- 📊 **图表图形**：简单的图表、示意图、流程图
- 🌅 **场景描述**：风景、人物、动物等真实场景
- 🎭 **创意设计**：Logo、图标、装饰图案

### 使用示例
```
用户: 画一只橘色的小猫，有绿色的眼睛，蜷缩在毛毯上睡觉
Bot: [返回描述文字] + [生成的图片]

用户: 设计一个简单的流程图，展示用户注册的步骤
Bot: [返回文字说明] + [生成的流程图]
```

### 技术实现
- 自动检测响应中的 `inlineData` 图片数据
- 使用MD5哈希命名确保文件唯一性
- 支持混合回复（文本+图片同时发送）
- 异步发送多张图片避免消息阻塞

## 🎯 使用建议

1. **系统提示词设计**：
   - 明确角色定位和专业领域
   - 设定回答风格和语气
   - 包含必要的行为约束

2. **多模态使用**：
   - 图片分析时可配合文字描述
   - 文件处理支持具体问题询问
   - 注意文件大小和格式限制
   - 使用描述性语言请求图片生成

3. **性能优化**：
   - 定期清理会话记忆（使用清除记忆命令）
   - 合理设置 `conversation_max_tokens`
   - 避免过长的单次输入

---

如有问题，请查看日志文件或提交Issue。 