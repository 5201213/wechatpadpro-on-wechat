# WeChatPadPro-on-WeChat 🤖

> 🚀 基于 WeChatPadPro 协议的智能微信机器人，支持多AI模型、插件化架构！

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

本项目基于 [chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat) 框架，适配 [WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro) 协议实现。

---

## ✨ 核心特性

### 🤖 AI模型支持
- **多模型适配**：支持Dify、OpenAI、Claude、Gemini、智谱GLM等主流AI模型
- **多模态处理**：支持文本、图片、语音、文件等多种消息类型的智能处理
- **自定义角色**：支持系统提示词设置，打造专属AI助手

### 📱 WeChatPadPro 协议
- **稳定可靠**：基于 [WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro) 协议，支持扫码登录、消息同步
- **全功能支持**：文本、图片、语音、视频、文件、链接卡片等所有消息类型
- **群聊管理**：完整支持群聊功能、好友管理
- **跨平台部署**：Windows、macOS、Linux全平台支持

### 🔌 插件化架构
- **丰富插件**：内置聊天总结、网页总结、管理命令等实用插件
- **易于扩展**：支持自定义插件开发，模块化设计
- **热插拔**：插件支持动态加载和卸载

---

## 🚀 快速开始

### 环境要求
- Python 3.8+
- [WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro) 服务

### 安装部署

```bash
# 1. 克隆项目
git clone https://github.com/5201213/wechatpadpro-on-wechat.git
cd wechatpadpro-on-wechat

# 2. 安装依赖
pip install -r requirements.txt
pip install -r requirements-optional.txt  # 可选功能

# 3. 配置文件
cp config-template.json config.json
# 编辑 config.json 填入配置

# 4. 启动服务
python app.py
```

---

## ⚙️ 配置说明

### 核心配置示例

```json
{
  "channel_type": "wxpad",
  "model": "dify",
  
  // WeChatPadPro 服务配置
  "wechatpadpro_base_url": "",
  "wechatpadpro_admin_key": "",
  "wechatpadpro_user_key": "",
  "wechatpadpro_ws_url": "",
  
  // AI模型配置（根据选择的model配置对应API）
  "dify_api_base": "",
  "dify_api_key": "",
  
  // 触发配置
  "single_chat_prefix": [""],
  "group_chat_prefix": ["@bot", "bot"],
  "group_name_white_list": ["ALL_GROUP"],
  
  // 功能开关
  "image_recognition": true,
  "speech_recognition": true,
  "voice_reply_voice": true,
  "sharing_to_text_enabled": false
}
```

### 关键配置项说明

| 配置项 | 说明 | 必填 |
|--------|------|------|
| `channel_type` | 通道类型，固定为 `wxpad` | ✅ |
| `model` | AI模型名称，如 `dify`、`gpt-4`、`gemini-2.0-flash` 等 | ✅ |
| `wechatpadpro_base_url` | WeChatPadPro服务地址 | ✅ |
| `wechatpadpro_admin_key` | 管理员密钥 | ✅ |
| `wechatpadpro_user_key` | 用户密钥（可选，扫码后自动获取） | ❌ |
| `group_name_white_list` | 群聊白名单，`["ALL_GROUP"]` 表示所有群 | ✅ |

---

## 🔧 WeChatPadPro 服务部署

本项目需要配合 [WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro) 服务使用。

### 下载与配置

1. 前往 [WeChatPadPro Releases](https://github.com/WeChatPadPro/WeChatPadPro/releases) 下载对应平台的版本
2. 解压到目录中
3. 根据需要配置环境变量
4. 启动WeChatPadPro服务
5. 获取管理员密钥，配置到本项目的 `config.json` 中

### 连接配置

```json
{
  "wechatpadpro_base_url": "http://localhost:1239",
  "wechatpadpro_admin_key": "你的管理员密钥",
  "wechatpadpro_ws_url": "ws://localhost:1239/ws/GetSyncMsg"
}
```

---

## 🔌 插件系统

### 内置插件

| 插件名称 | 功能描述 | 使用场景 |
|---------|----------|----------|
| **ChatSummary** | 聊天记录总结 | 群聊内容回顾、会议记录整理 |
| **JinaSum** | 网页内容总结 | 自动抓取并总结网页内容 |
| **GodCmd** | 管理员命令 | 系统管理、状态监控、重启等 |
| **Hello** | 示例插件 | 插件开发参考 |

### 插件开发示例

```python
import plugins
from plugins import *

@plugins.register(name="MyPlugin", desc="我的插件", version="1.0", author="作者")
class MyPlugin(Plugin):
    def __init__(self):
        super().__init__()
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        
    def on_handle_context(self, e_context: EventContext):
        content = e_context['context']['content']
        if content.startswith('$hello'):
            reply = Reply(ReplyType.TEXT, '你好！')
            e_context['reply'] = reply
            e_context.action = EventAction.BREAK_PASS
```

---

## 📁 项目结构

```
wechatpadpro-on-wechat/
├── app.py                    # 主程序入口
├── config-template.json      # 配置模板
├── 
├── bot/                      # AI模型适配层
├── channel/                  # 通信通道层
│   └── wxpad/               # WeChatPadPro通道
├── plugins/                  # 插件系统
├── lib/                      # 第三方库封装
│   └── wxpad/               # WeChatPadPro客户端
├── bridge/                   # 消息桥接层
├── common/                   # 公共工具
└── voice/                    # 语音处理
```

---

## 🔧 高级配置

### 消息处理配置

```json
{
  "sharing_to_text_enabled": true,      // 分享消息处理开关
  "tmp_cleanup_enabled": true,          // 临时文件清理
  "tmp_cleanup_interval": 3600,         // 清理间隔(秒)
  "voice_to_text": "dify",              // 语音转文字服务
  "text_to_voice": "dify",              // 文字转语音服务
  "image_recognition": true             // 图片识别开关
}
```

### 群聊管理配置

```json
{
  "group_name_white_list": ["技术交流群", "ALL_GROUP"],
  "group_name_keyword_white_list": ["技术", "开发"],
  "group_chat_in_one_session": ["技术交流群"],
  "group_chat_prefix": ["@bot", "bot"],
  "group_chat_keyword": ["帮助", "help"]
}
```

---

## 📱 使用场景

### 个人助手
- 日程管理、信息查询、文档处理

### 群聊助手  
- 群管理、知识问答、娱乐互动

### 企业应用
- 客服机器人、内部助手、会议助手

---

## 🛠️ 故障排除

### 常见问题

**Q: 机器人无法登录微信？**
- 检查WeChatPadPro服务是否正常运行
- 确认配置文件中的连接参数正确
- 查看防火墙是否阻止了相关端口

**Q: 消息发送失败？**
- 检查微信账号是否被限制
- 确认目标用户/群聊是否存在
- 查看API调用是否超出限制

**Q: AI回复不准确？**
- 检查AI模型配置和API密钥
- 调整系统提示词(character_desc)
- 确认网络连接正常

### 日志查看

```bash
# 查看运行日志
tail -f logs/wechat_robot.log

# 查看错误日志  
grep "ERROR" logs/wechat_robot.log
```

---

## 🤝 贡献指南

欢迎贡献代码、文档、插件等！

```bash
# 1. Fork 项目
git clone https://github.com/your-username/wechatpadpro-on-wechat.git

# 2. 创建开发分支
git checkout -b feature/your-feature

# 3. 安装依赖
pip install -r requirements.txt

# 4. 提交代码
git commit -m "feat: add your feature"
git push origin feature/your-feature
```

---

## 📄 开源协议

本项目采用 [MIT License](LICENSE) 开源协议。

---

## 🙏 致谢

- [WeChatPadPro](https://github.com/WeChatPadPro/WeChatPadPro) - 提供稳定的微信协议支持
- [chatgpt-on-wechat](https://github.com/zhayujie/chatgpt-on-wechat) - 优秀的AI聊天机器人框架
- [Dify](https://dify.ai/) - 优秀的LLMOps平台

---

## 📞 联系我们

- 项目地址：https://github.com/5201213/wechatpadpro-on-wechat
- 问题反馈：https://github.com/5201213/wechatpadpro-on-wechat/issues
- WeChatPadPro社群：[Telegram](https://t.me/+LK0JuqLxjmk0ZjRh)

---

<div align="center">

**🌟 如果这个项目对你有帮助，请给个Star支持一下！**

Made with ❤️ by the Community

</div>
