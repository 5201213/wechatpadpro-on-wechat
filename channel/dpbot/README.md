# DPBot 通道

DPBot是一个微信自动化工具，支持Wechat 08算法的五端扫码版本。

## 功能特点

- ✅ **多端支持**: iPad、安卓Pad、Windows、Mac、Car、62 iPhone、A16安卓
- ✅ **自动心跳**: 支持自动心跳、自动二次登录、长连接心跳
- ✅ **WebSocket连接**: 支持WebSocket客户端连接
- ✅ **完整API**: 覆盖160个swagger文档定义的API接口
- ✅ **无密钥系统**: DPBot没有密钥系统，使用简单

## 安装依赖

```bash
pip install websocket-client requests
```

## 配置说明

在 `config.json` 中添加以下配置：

```json
{
  "channel_type": "dpbot",
  "dpbot_base_url": "http://127.0.0.1:8059",
  "dpbot_admin_key": "",
  "dpbot_user_key": ""
}
```

### 配置参数说明

- `channel_type`: 设置为 `"dpbot"` 启用DPBot通道
- `dpbot_base_url`: DPBot服务地址，默认为 `http://127.0.0.1:8059`
- `dpbot_admin_key`: 管理员密钥（可选，DPBot无密钥系统）
- `dpbot_user_key`: 用户密钥（可选，DPBot无密钥系统）

## 使用流程

1. **启动DPBot服务**
   ```bash
   # 启动DPBot服务端（端口8059）
   ./dpbot-server
   ```

2. **配置机器人**
   - 修改 `config.json` 设置 `channel_type` 为 `"dpbot"`
   - 设置正确的 `dpbot_base_url`

3. **启动机器人**
   ```bash
   python app.py
   ```

4. **扫码登录**
   - 机器人启动后会自动检查登录状态
   - 如需登录，会生成二维码保存到 `tmp/dpbot_qr.png`
   - 使用微信扫描二维码完成登录

## 支持的消息类型

### 接收消息
- ✅ 文本消息
- ✅ 图片消息  
- ✅ 语音消息
- ✅ 视频消息
- ✅ 链接分享
- ✅ 文件消息
- ✅ 系统消息

### 发送消息
- ✅ 文本消息
- ✅ 图片消息
- ✅ 语音消息
- ✅ @群成员
- ✅ 长文本自动分割

## WebSocket连接

DPBot支持WebSocket实时消息推送，提供更高效的消息同步：

### WebSocket特性
- **连接地址**: `ws://127.0.0.1:8059/ws`
- **自动重连**: 支持断线自动重连（最多5次）
- **消息格式**: 支持你提供的标准化消息格式

### 消息格式支持
```json
{
  "data": {
    "MsgId": 141622302,
    "FromUserName": {"string": "wxid_z3wc4zex3vr822"},
    "ToUserName": {"string": "wxid_z3wc4zex3vr822"},
    "MsgType": 51,
    "Content": {"string": "<msg>...</msg>"},
    "CreateTime": 1750721329,
    "MsgSource": "<msgsource>...</msgsource>",
    "NewMsgId": 6535201297816465912
  },
  "msg_index": 1,
  "timestamp": 1750721484,
  "total_msgs": 4,
  "type": "new_message",
  "wxid": "wxid_z3wc4zex3vr822"
}
```

### 消息过滤
- 自动过滤MsgType 51的系统消息（如HandOffMaster）
- 过滤自己发给自己的消息
- 过滤XML格式的系统通知
- 支持连接状态监控和自动重连

## API接口覆盖

DPBot客户端完整实现了swagger文档中的160个API接口，按功能模块分类：

### 核心模块
- **Favor模块**: 收藏管理（4个接口）
- **Friend模块**: 好友管理（12个接口）
- **Group模块**: 群组管理（17个接口）
- **Msg模块**: 消息处理（15个接口）
- **User模块**: 用户信息（15个接口）

### 扩展模块
- **Login模块**: 登录体系（32个接口）
- **FriendCircle模块**: 朋友圈（9个接口）
- **TenPay模块**: 支付功能（9个接口）
- **Tools模块**: 工具集（15个接口）
- **OfficialAccounts模块**: 公众号（7个接口）
- **Wxapp模块**: 小程序（14个接口）
- **其他模块**: 标签、企微联系人等

## 错误处理

### 常见问题

1. **依赖缺失**
   ```
   No module named 'websocket'
   ```
   解决：`pip install websocket-client`

2. **连接失败**
   ```
   DPBot服务连接失败
   ```
   解决：检查DPBot服务是否启动，端口是否正确

3. **登录失败**
   ```
   二维码登录超时
   ```
   解决：重新启动服务，确保及时扫码

### 日志调试

启用详细日志：
```json
{
  "debug": true
}
```

查看DPBot相关日志：
```bash
tail -f logs/app.log | grep DPBot
```

## 与其他通道对比

| 功能特性 | DPBot | WxPad | 微信网页版 |
|---------|-------|-------|-----------|
| 稳定性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 功能完整性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 多端支持 | ✅ | ✅ | ❌ |
| WebSocket | ✅ | ✅ | ❌ |
| 免费使用 | ✅ | ❌ | ✅ |

## 技术架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   机器人框架     │    │   DPBot通道     │    │   DPBot服务     │
│                │◄──►│                │◄──►│                │
│  wxpad-on-      │    │  dpbot_channel  │    │  HTTP API +     │
│  wechat        │    │  dpbot_message  │    │  WebSocket      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 开发贡献

欢迎提交PR和Issue！

### 开发环境
```bash
# 克隆代码
git clone https://github.com/your-repo/wxpad-on-wechat

# 安装依赖
pip install -r requirements.txt

# 运行测试
python test_dpbot_channel.py
```

## 许可证

本项目遵循MIT许可证。