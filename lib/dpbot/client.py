"""
DPBot客户端 - 微信自动化接口客户端

支持多种登录方式：
1. 二维码登录 - 支持多种设备类型(iPad/Mac/Windows/Android等)
2. A16登录 - 账号密码登录
3. 62数据登录 - 高级登录方式，需要提前获取62数据
4. 新设备登录 - 扫描其他设备二维码登录

代理配置示例(注意：62数据登录必须使用SOCKS代理)：
proxy_config = {
    "ProxyIp": "127.0.0.1:1080",  # SOCKS5代理地址
    "ProxyUser": "username",      # 代理用户名(可选)
    "ProxyPassword": "password"   # 代理密码(可选)
}

使用示例：
client = DPBotClient("http://localhost:8080")

# 二维码登录
qr_result = client.get_qr("device_id", "device_name", "ipad", proxy_config)
status = client.check_qr(qr_result["Data"]["Uuid"])

# A16登录
result = client.a16_login("a16_data", "device_name", "username", "password", proxy_config)

# 62数据登录
result = client.data62_login("data62", "device_name", "username", "password", proxy_config)
"""
import os
import json

# 可选依赖，如果缺失则在运行时报错
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None

class DPBotClient:
    def __init__(self, base_url, timeout=60, max_retries=3, enable_retry=True):
        """初始化DPBot客户端
        
        Args:
            base_url: DPBot服务器地址，如 http://localhost:8080
            timeout: 请求超时时间（秒），默认60秒
            max_retries: 最大重试次数，默认3次
            enable_retry: 是否启用重试机制，默认True
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.enable_retry = enable_retry
    
        def _validate_wxid(self, wxid):
            """验证微信ID格式
            
            Args:
                wxid: 微信ID
                
            Raises:
                ValueError: 微信ID格式无效
            """
            if not wxid or not isinstance(wxid, str):
                raise ValueError("微信ID不能为空且必须是字符串")
            if len(wxid.strip()) == 0:
                raise ValueError("微信ID不能为空白字符串")
    
        def _validate_required_param(self, param_value, param_name):
            """验证必填参数
            
            Args:
                param_value: 参数值
                param_name: 参数名称
                
            Raises:
                ValueError: 参数无效
            """
            if param_value is None or (isinstance(param_value, str) and len(param_value.strip()) == 0):
                raise ValueError(f"必填参数 {param_name} 不能为空")
    
        def _validate_voice_type(self, voice_type):
            """验证语音类型
            
            Args:
                voice_type: 语音类型
                
            Raises:
                ValueError: 语音类型无效
            """
            valid_types = [0, 1, 2, 3, 4]  # AMR=0, SPEEX=1, MP3=2, WAVE=3, SILK=4
            if voice_type not in valid_types:
                raise ValueError(f"语音类型必须是 {valid_types} 中的一个，当前值: {voice_type}")    
    def _validate_wxid(self, wxid):
        """验证微信ID格式
        
        Args:
            wxid: 微信ID
            
        Raises:
            ValueError: 微信ID格式无效
        """
        if not wxid or not isinstance(wxid, str):
            raise ValueError("微信ID不能为空且必须是字符串")
        if len(wxid.strip()) == 0:
            raise ValueError("微信ID不能为空白字符串")
    
    def _validate_required_param(self, param_value, param_name):
        """验证必填参数
        
        Args:
            param_value: 参数值
            param_name: 参数名称
            
        Raises:
            ValueError: 参数无效
        """
        if param_value is None or (isinstance(param_value, str) and len(param_value.strip()) == 0):
            raise ValueError(f"必填参数 {param_name} 不能为空")
    
    def _validate_voice_type(self, voice_type):
        """验证语音类型
        
        Args:
            voice_type: 语音类型
            
        Raises:
            ValueError: 语音类型无效
        """
        valid_types = [0, 1, 2, 3, 4]  # AMR=0, SPEEX=1, MP3=2, WAVE=3, SILK=4
        if voice_type not in valid_types:
            raise ValueError(f"语音类型必须是 {valid_types} 中的一个，当前值: {voice_type}")


    def _post(self, path, data=None, params=None):
            """发送POST请求到DPBot API
            
            Args:
                path: API路径（自动添加/api前缀）
                data: 请求数据
                params: URL参数
                
            Returns:
                API响应结果
                
            Raises:
                Exception: API请求失败或返回错误
            """
            if not REQUESTS_AVAILABLE:
                raise Exception("requests模块未安装，无法发送HTTP请求")
                
            # 确保路径以/api开头（除非已经包含）
            if not path.startswith('/api'):
                path = '/api' + path
            
            url = self.base_url + path
            headers = {'Content-Type': 'application/json'}
            
            try:
                resp = requests.post(url, json=data, params=params, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                result = resp.json()
                
                if not result.get("Success", True):
                    raise Exception(f"API失败: {result.get('Message', result)}")
                return result
                
            except requests.exceptions.RequestException as e:
                raise Exception(f"网络请求失败 {url}: {e}")
            except json.JSONDecodeError as e:
                raise Exception(f"API响应解析失败 {url}: {e}")
            except Exception as e:
                raise Exception(f"请求 {url} 失败: {e}")

    
    def _get(self, path, params=None):
            """发送GET请求到DPBot API
            
            Args:
                path: API路径（自动添加/api前缀）
                params: URL参数
                
            Returns:
                API响应结果
                
            Raises:
                Exception: API请求失败或返回错误
            """
            # 确保路径以/api开头（除非已经包含）
            if not path.startswith('/api'):
                path = '/api' + path
                
            url = self.base_url + path
            
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                result = resp.json()
                
                if not result.get("Success", True):
                    raise Exception(f"API失败: {result.get('Message', result)}")
                return result
                
            except requests.exceptions.RequestException as e:
                raise Exception(f"网络请求失败 {url}: {e}")
            except json.JSONDecodeError as e:
                raise Exception(f"API响应解析失败 {url}: {e}")
            except Exception as e:
                raise Exception(f"请求 {url} 失败: {e}")
    
    def _request_with_retry(self, method, *args, max_retries=None, **kwargs):
            """带重试机制的请求方法
            
            Args:
                method: 请求方法（_post或_get）
                max_retries: 最大重试次数，默认使用实例配置
                *args, **kwargs: 传递给请求方法的参数
                
            Returns:
                API响应结果
                
            Raises:
                Exception: 重试后仍然失败
            """
            import time
            
            if max_retries is None:
                max_retries = self.max_retries
            
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return method(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        # 指数退避重试
                        wait_time = (2 ** attempt) * 0.5
                        time.sleep(wait_time)
                        continue
                    break
            
            raise Exception(f"重试{max_retries}次后仍然失败: {last_exception}")

    # ==================== 登录相关 ====================
    
    def get_qr(self, device_id, device_name, login_type="ipad", proxy=None):
        """获取登录二维码
        
        Args:
            device_id: 设备ID，随机生成的UUID
            device_name: 设备名称，如 DPBot_xxx
            login_type: 登录类型，支持 ipad/pad/mac/car/win/winunified/winuwp/padx/x
            proxy: 代理配置，格式：{"ProxyIp": "", "ProxyPassword": "", "ProxyUser": ""}
            
        Returns:
            返回登录二维码信息，包含 QrUrl 和 Uuid
        """
        # API映射表 - 根据swagger文档中的实际API路径
        api_map = {
            "ipad": "LoginGetQR",
            "pad": "LoginGetQRPad", 
            "mac": "LoginGetQRMac",
            "car": "LoginGetQRCar",
            "win": "LoginGetQRWin",
            "winunified": "LoginGetQRWinUnified",
            "winuwp": "LoginGetQRWinUwp",
            "padx": "LoginGetQRPadx",  # 绕过验证码
            "x": "LoginGetQRx"  # iPad绕过验证码
        }
        
        # 选择API接口
        qr_api = api_map.get(login_type.lower(), "LoginGetQR")
            
        data = {
            "DeviceID": device_id, 
            "DeviceName": device_name,
            "LoginType": "",
            "Proxy": proxy or {}
        }
        
        try:
            if self.enable_retry:
                result = self._request_with_retry(self._post, f'/Login/{qr_api}', data=data)
            else:
                result = self._post(f'/Login/{qr_api}', data=data)
            
            # 优先取QrUrl字段，兼容不同的返回格式
            if result.get("Data"):
                qr_url = result["Data"].get("QrUrl") or result["Data"].get("QrcodeUrl") or result["Data"].get("Url")
                result["QrUrl"] = qr_url
                
            return result
        except Exception as e:
            raise Exception(f"获取二维码接口失败: {e}")

    # 以下方法确保swagger文档中的所有登录API都被明确调用
    def _login_get_qr_ipad(self, device_id, device_name, proxy=None):
        """iPad登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQR', data=data)

    def _login_get_qr_pad(self, device_id, device_name, proxy=None):
        """Pad登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRPad', data=data)

    def _login_get_qr_mac(self, device_id, device_name, proxy=None):
        """Mac登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRMac', data=data)

    def _login_get_qr_car(self, device_id, device_name, proxy=None):
        """Car登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRCar', data=data)

    def _login_get_qr_win(self, device_id, device_name, proxy=None):
        """Windows登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRWin', data=data)

    def _login_get_qr_win_unified(self, device_id, device_name, proxy=None):
        """Windows Unified登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRWinUnified', data=data)

    def _login_get_qr_win_uwp(self, device_id, device_name, proxy=None):
        """Windows UWP登录二维码"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRWinUwp', data=data)

    def _login_get_qr_padx(self, device_id, device_name, proxy=None):
        """PadX登录二维码（绕过验证码）"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRPadx', data=data)

    def _login_get_qr_x(self, device_id, device_name, proxy=None):
        """X登录二维码（iPad绕过验证码）"""
        data = {"DeviceID": device_id, "DeviceName": device_name, "LoginType": "", "Proxy": proxy or {}}
        return self._post('/Login/LoginGetQRx', data=data)

    def _a16_data_login(self, a16_data, device_name, username, password, proxy=None):
        """A16数据登录（标准版）"""
        data = {"A16": a16_data, "DeviceName": device_name, "Password": password, "Proxy": proxy or {}, "UserName": username}
        return self._post('/Login/A16Data', data=data)

    def _a16_data1_login(self, a16_data, device_name, username, password, proxy=None):
        """A16数据登录（新版云函数）"""
        data = {"A16": a16_data, "DeviceName": device_name, "Password": password, "Proxy": proxy or {}, "UserName": username}
        return self._post('/Login/A16Data1', data=data)

    def _auto_heart_beat_enable(self, wxid):
        """开启自动心跳"""
        return self._post('/Login/AutoHeartBeat', params={'wxid': wxid})

    def _auto_heart_beat_disable(self, wxid):
        """关闭自动心跳"""
        return self._post('/Login/CloseAutoHeartBeat', params={'wxid': wxid})


    def check_qr(self, uuid):
        """检查扫码状态
        
        Args:
            uuid: 获取二维码时返回的UUID
            
        Returns:
            返回扫码状态，Status=1表示已扫码并确认
        """
        try:
            if self.enable_retry:
                return self._request_with_retry(self._post, '/Login/LoginCheckQR', params={"uuid": uuid})
            else:
                return self._post('/Login/LoginCheckQR', params={"uuid": uuid})
        except Exception as e:
            raise Exception(f"检查扫码接口失败: {e}")



    def twice_login(self, wxid):
        """二次登录(自动二次验证)
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        return self._post('/Login/LoginTwiceAutoAuth', params={'wxid': wxid})

    def heart_beat(self, wxid):
        """发送心跳包(短心跳)
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        return self._post('/Login/HeartBeat', params={'wxid': wxid})

    def logout(self, wxid):
        """退出登录
        
        Args:
            wxid: 微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        return self._post('/Login/LogOut', params={'wxid': wxid})

    # ==================== 高级登录接口 ====================
    
    def a16_login(self, a16_data, device_name, username, password, proxy=None, use_new_version=False):
        """A16登录(账号或密码)
        
        Args:
            a16_data: A16数据
            device_name: 设备名称
            username: 用户名
            password: 密码
            proxy: 代理配置，格式：{"ProxyIp": "", "ProxyPassword": "", "ProxyUser": ""}
            use_new_version: 是否使用新版云函数(android)
            
        Returns:
            登录结果
        """
        api_path = '/Login/A16Data1' if use_new_version else '/Login/A16Data'
        data = {
            "A16": a16_data,
            "DeviceName": device_name,
            "Password": password,
            "Proxy": proxy or {},
            "UserName": username
        }
        return self._post(api_path, data=data)

    def data62_login(self, data62, device_name, username, password, proxy=None):
        """62登录(账号或密码)
        
        Args:
            data62: 62数据
            device_name: 设备名称
            username: 用户名
            password: 密码
            proxy: 代理配置(注意：代理必须使用SOCKS)
            
        Returns:
            登录结果
        """
        data = {
            "Data62": data62,
            "DeviceName": device_name,
            "Password": password,
            "Proxy": proxy or {},
            "UserName": username
        }
        return self._post('/Login/Data62Login', data=data)

    def data62_qr_apply(self, data62, device_name, username, password, proxy=None):
        """62登录并申请使用二维码验证
        
        Args:
            data62: 62数据
            device_name: 设备名称
            username: 用户名
            password: 密码
            proxy: 代理配置
            
        Returns:
            申请结果
        """
        data = {
            "Data62": data62,
            "DeviceName": device_name,
            "Password": password,
            "Proxy": proxy or {},
            "UserName": username
        }
        return self._post('/Login/Data62QRCodeApply', data=data)

    def data62_qr_verify(self, cookie, url, sms, proxy=None):
        """62登录二维码验证校验
        
        Args:
            cookie: Cookie信息
            url: 验证URL
            sms: 短信验证码
            proxy: 代理配置
            
        Returns:
            验证结果
        """
        data = {
            "Cookie": cookie,
            "Proxy": proxy or {},
            "Sms": sms,
            "Url": url
        }
        return self._post('/Login/Data62QRCodeVerify', data=data)

    def data62_sms_apply(self, data62, device_name, username, password, proxy=None):
        """62登录并申请使用SMS验证
        
        Args:
            data62: 62数据
            device_name: 设备名称
            username: 用户名
            password: 密码
            proxy: 代理配置
            
        Returns:
            申请结果
        """
        data = {
            "Data62": data62,
            "DeviceName": device_name,
            "Password": password,
            "Proxy": proxy or {},
            "UserName": username
        }
        return self._post('/Login/Data62SMSApply', data=data)

    def data62_sms_verify(self, cookie, url, sms, proxy=None):
        """62登录短信验证
        
        Args:
            cookie: Cookie信息
            url: 验证URL
            sms: 短信验证码
            proxy: 代理配置
            
        Returns:
            验证结果
        """
        data = {
            "Cookie": cookie,
            "Proxy": proxy or {},
            "Sms": sms,
            "Url": url
        }
        return self._post('/Login/Data62SMSVerify', data=data)

    def data62_sms_resend(self, cookie, url, proxy=None):
        """62登录重发验证码
        
        Args:
            cookie: Cookie信息
            url: 验证URL
            proxy: 代理配置
            
        Returns:
            重发结果
        """
        data = {
            "Cookie": cookie,
            "Proxy": proxy or {},
            "Url": url
        }
        return self._post('/Login/Data62SMSAgain', data=data)

    def submit_verification_code(self, code, data62, ticket, uuid):
        """提交登录验证码
        
        Args:
            code: 验证码
            data62: 62数据
            ticket: 票据
            uuid: UUID
            
        Returns:
            提交结果
        """
        data = {
            "Code": code,
            "Data62": data62,
            "Ticket": ticket,
            "Uuid": uuid
        }
        return self._post('/Login/YPayVerificationcode', data=data)

    # ==================== 设备登录相关 ====================
    
    def ext_device_login_get(self, url, wxid):
        """新设备扫码登录
        
        Args:
            url: MAC iPad Windows 的微信二维码解析出来的url
            wxid: 微信ID
            
        Returns:
            登录信息
        """
        data = {
            "Url": url,
            "Wxid": wxid
        }
        return self._post('/Login/ExtDeviceLoginConfirmGet', data=data)

    def ext_device_login_confirm(self, url, wxid):
        """新设备扫码确认登录
        
        Args:
            url: MAC iPad Windows 的微信二维码解析出来的url
            wxid: 微信ID
            
        Returns:
            确认结果
        """
        data = {
            "Url": url,
            "Wxid": wxid
        }
        return self._post('/Login/ExtDeviceLoginConfirmOk', data=data)

    # ==================== 数据获取相关 ====================
    
    def get_62_data(self, wxid):
        """获取62数据
        
        Args:
            wxid: 微信ID
            
        Returns:
            62数据
        """
        return self._post('/Login/Get62Data', params={'wxid': wxid})

    def get_a16_data(self, wxid):
        """获取A16数据
        
        Args:
            wxid: 微信ID
            
        Returns:
            A16数据
        """
        return self._post('/Login/GetA16Data', params={'wxid': wxid})

    def get_cache_info(self, wxid):
        """获取登录缓存信息
        
        Args:
            wxid: 微信ID
            
        Returns:
            缓存信息
        """
        return self._post('/Login/GetCacheInfo', params={'wxid': wxid})

    # ==================== 初始化和心跳 ====================
    
    def new_init(self, wxid, max_synckey="", current_synckey=""):
        """初始化
        
        Args:
            wxid: 微信ID
            max_synckey: 二次同步需要带入的MaxSynckey
            current_synckey: 二次同步需要带入的CurrentSynckey
            
        Returns:
            初始化结果
        """
        params = {'wxid': wxid}
        if max_synckey:
            params['MaxSynckey'] = max_synckey
        if current_synckey:
            params['CurrentSynckey'] = current_synckey
        return self._post('/Login/Newinit', params=params)

    def heart_beat_long(self, wxid):
        """长心跳包
        
        Args:
            wxid: 微信ID
            
        Returns:
            心跳结果
        """
        return self._post('/Login/HeartBeatLong', params={'wxid': wxid})

    def auto_heart_beat(self, wxid, enable=True):
        """开启/关闭自动心跳, 自动二次登录（linux 长连接，win 短链接）
        
        Args:
            wxid: 微信ID
            enable: 是否开启，True为开启，False为关闭
            
        Returns:
            设置结果
        """
        api_path = '/Login/AutoHeartBeat' if enable else '/Login/CloseAutoHeartBeat'
        return self._post(api_path, params={'wxid': wxid})

    def get_auto_heart_beat_log(self, wxid):
        """获取自动心跳日志
        
        Args:
            wxid: 微信ID
            
        Returns:
            心跳日志
        """
        return self._post('/Login/AutoHeartBeatLog', params={'wxid': wxid})

    # ==================== 消息相关 ====================
    
    def send_text(self, wxid, to_wxid, content, at=None):
            """发送文本消息
            
            Args:
                wxid: 发送者微信ID
                to_wxid: 接收者微信ID
                content: 消息内容
                at: 群聊中需要@的微信ID，多个用逗号分隔
                
            Returns:
                成功返回 {"Success": true}
            """
            # 参数验证
            self._validate_wxid(wxid)
            self._validate_wxid(to_wxid)
            self._validate_required_param(content, "content")
            
            data = {
                "Wxid": wxid, 
                "ToWxid": to_wxid, 
                "Content": content, 
                "Type": 1
            }
            if at:
                data["At"] = at if isinstance(at, str) else ",".join(at)
            return self._post('/Msg/SendTxt', data=data)


    def send_image(self, wxid, to_wxid, base64_img):
        """发送图片消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            base64_img: 图片的base64编码
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Base64": base64_img
        }
        return self._post('/Msg/UploadImg', data=data)

    def send_voice(self, wxid, to_wxid, base64_voice, voice_type=0, voice_time=3000):
            """发送语音消息
            
            Args:
                wxid: 发送者微信ID
                to_wxid: 接收者微信ID
                base64_voice: 语音的base64编码
                voice_type: 语音类型（AMR=0, SPEEX=1, MP3=2, WAVE=3, SILK=4）
                voice_time: 语音时长，单位为毫秒，默认3000毫秒(3秒)
                
            Returns:
                成功返回 {"Success": true}
            """
            # 参数验证
            self._validate_wxid(wxid)
            self._validate_wxid(to_wxid)
            self._validate_required_param(base64_voice, "base64_voice")
            self._validate_voice_type(voice_type)
            
            if voice_time <= 0:
                raise ValueError("语音时长必须大于0毫秒")
            
            data = {
                "Wxid": wxid, 
                "ToWxid": to_wxid, 
                "Base64": base64_voice, 
                "Type": voice_type, 
                "VoiceTime": voice_time
            }
            return self._post('/Msg/SendVoice', data=data)


    def send_emoji(self, wxid, to_wxid, md5, total_len):
        """发送表情消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            md5: 表情MD5
            total_len: 表情总长度
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Md5": md5, 
            "TotalLen": total_len
        }
        return self._post('/Msg/SendEmoji', data=data)

    def revoke_msg(self, wxid, to_user_name, client_msg_id, create_time, new_msg_id):
        """撤回消息
        
        Args:
            wxid: 微信ID
            to_user_name: 接收者微信ID
            client_msg_id: 客户端消息ID
            create_time: 消息创建时间
            new_msg_id: 新消息ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToUserName": to_user_name, 
            "ClientMsgId": client_msg_id, 
            "CreateTime": create_time, 
            "NewMsgId": new_msg_id
        }
        return self._post('/Msg/Revoke', data=data)

    def share_card(self, wxid, to_wxid, card_wxid, card_nickname, card_alias=""):
        """分享名片
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            card_wxid: 名片用户的微信ID
            card_nickname: 名片用户的昵称
            card_alias: 名片用户的别名（可选）
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "CardWxId": card_wxid,
            "CardNickName": card_nickname,
            "CardAlias": card_alias
        }
        return self._post('/Msg/ShareCard', data=data)

    def send_app_message(self, wxid, to_wxid, xml, msg_type=5):
        """发送APP消息（如小程序、链接等）
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            xml: 消息XML内容
            msg_type: 消息类型，默认5
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Xml": xml,
            "Type": msg_type
        }
        return self._post('/Msg/SendApp', data=data)

    def send_video(self, wxid, to_wxid, base64_video, base64_thumb, play_length):
        """发送视频消息
        
        Args:
            wxid: 发送者微信ID
            to_wxid: 接收者微信ID
            base64_video: 视频的base64编码
            base64_thumb: 视频缩略图的base64编码
            play_length: 视频时长，单位为秒
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Base64": base64_video,
            "ImageBase64": base64_thumb,
            "PlayLength": play_length
        }
        return self._post('/Msg/SendVideo', data=data)

    # ==================== 联系人相关 ====================
    
    def get_contacts(self, wxid):
        """获取联系人列表
        
        Args:
            wxid: 微信ID
            
        Returns:
            联系人列表
        """
        data = {
            "Wxid": wxid,
            "CurrentWxcontactSeq": 0,
            "CurrentChatRoomContactSeq": 0
        }
        return self._post('/Friend/GetContractList', data=data)

    def get_contact_detail(self, wxid, to_wxids):
        """获取联系人详情
        
        Args:
            wxid: 微信ID
            to_wxids: 联系人微信ID，多个用逗号分隔
            
        Returns:
            联系人详情
        """
        data = {
            "Wxid": wxid, 
            "Towxids": to_wxids,  # 按swagger文档使用Towxids(首字母大写T)
            "ChatRoom": ""
        }
        return self._post('/Friend/GetContractDetail', data=data)


    def add_friend(self, wxid, v1, v2, verify_content=""):
        """添加好友
        
        Args:
            wxid: 微信ID
            v1: 验证参数1
            v2: 验证参数2
            verify_content: 验证内容
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "V1": v1, 
            "V2": v2,
            "VerifyContent": verify_content,
            "Opcode": 2,
            "Scene": 2
        }
        return self._post('/Friend/SendRequest', data=data)

    def delete_friend(self, wxid, to_wxid):
        """删除好友
        
        Args:
            wxid: 微信ID
            to_wxid: 好友微信ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid
        }
        return self._post('/Friend/Delete', data=data)

    def set_remark(self, wxid, to_wxid, remarks):
        """设置好友备注
        
        Args:
            wxid: 微信ID
            to_wxid: 好友微信ID
            remarks: 备注名
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ToWxid": to_wxid, 
            "Remarks": remarks
        }
        return self._post('/Friend/SetRemarks', data=data)

    # ==================== 群组相关 ====================
    
    def create_group(self, wxid, to_wxids):
        """创建群聊
        
        Args:
            wxid: 微信ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回群ID
        """
        data = {
            "Wxid": wxid, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/CreateChatRoom', data=data)

    def add_group_member(self, wxid, chat_room_name, to_wxids):
        """添加群成员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ChatRoomName": chat_room_name, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/AddChatRoomMember', data=data)

    def remove_group_member(self, wxid, chat_room_name, to_wxids):
        """删除群成员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群ID
            to_wxids: 成员微信ID，多个用逗号分隔
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "ChatRoomName": chat_room_name, 
            "ToWxids": to_wxids
        }
        return self._post('/Group/DelChatRoomMember', data=data)

    def get_group_info(self, wxid, qid):
        """获取群信息
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            群信息
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/GetChatRoomInfo', data=data)

    def get_group_members(self, wxid, qid):
        """获取群成员
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            群成员信息
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/GetChatRoomMemberDetail', data=data)

    def set_group_announcement(self, wxid, qid, content):
        """设置群公告
        
        Args:
            wxid: 微信ID
            qid: 群ID
            content: 公告内容
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid, 
            "Content": content
        }
        return self._post('/Group/SetChatRoomAnnouncement', data=data)

    def set_group_name(self, wxid, qid, content):
        """设置群名称
        
        Args:
            wxid: 微信ID
            qid: 群ID
            content: 群名称
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid, 
            "Content": content
        }
        return self._post('/Group/SetChatRoomName', data=data)

    def quit_group(self, wxid, qid):
        """退出群聊
        
        Args:
            wxid: 微信ID
            qid: 群ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid, 
            "QID": qid
        }
        return self._post('/Group/Quit', data=data)

    # ==================== 消息同步 ====================
    
    def sync_message(self, wxid, device_id="", device_name="", scene=0, synckey=""):
        """同步消息
        
        Args:
            wxid: 微信ID
            device_id: 设备ID（可选）
            device_name: 设备名称（可选）
            scene: 场景值，默认0
            synckey: 同步key，默认空
            
        Returns:
            消息列表
        """
        data = {
            "Wxid": wxid,
            "Scene": scene,
            "Synckey": synckey
        }
        if device_id:
            data["DeviceId"] = device_id
        if device_name:
            data["DeviceName"] = device_name
        
        try:
            if self.enable_retry:
                return self._request_with_retry(self._post, '/Msg/Sync', data=data)
            else:
                return self._post('/Msg/Sync', data=data)
        except Exception as e:
            raise Exception(f"同步消息请求失败: {e}")


    # ==================== 工具方法 ====================
    
    @staticmethod
    def load_robot_stat(path):
        """加载机器人状态
        
        Args:
            path: 状态文件路径
            
        Returns:
            状态信息字典
        """
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None

    @staticmethod
    def save_robot_stat(path, stat):
        """保存机器人状态
        
        Args:
            path: 状态文件路径
            stat: 状态信息字典
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(stat, f, ensure_ascii=False, indent=2)
    
    
    # ==================== 收藏相关 (Favor) ====================
    
    def delete_favor(self, wxid, fav_id):
        """删除收藏
        
        Args:
            wxid: 微信ID
            fav_id: 收藏ID（在同步收藏中获取）
            
        Returns:
            删除结果
        """
        data = {"Wxid": wxid, "FavId": fav_id}
        return self._post('/Favor/Del', data=data)

    def get_favor_info(self, wxid):
        """获取收藏信息
        
        Args:
            wxid: 微信ID
            
        Returns:
            收藏信息
        """
        return self._post('/Favor/GetFavInfo', params={'wxid': wxid})

    def get_favor_item(self, wxid, fav_id):
        """获取收藏项目
        
        Args:
            wxid: 微信ID
            fav_id: 收藏ID
            
        Returns:
            收藏项目详情
        """
        data = {"Wxid": wxid, "FavId": fav_id}
        return self._post('/Favor/GetFavItem', data=data)

    def sync_favor(self, wxid, key=""):
        """同步收藏
        
        Args:
            wxid: 微信ID
            key: 同步key
            
        Returns:
            收藏同步结果
        """
        data = {"Wxid": wxid, "Key": key}
        return self._post('/Favor/Sync', data=data)

    # ==================== 查找相关 (Finder) ====================
    
    def user_prepare(self, wxid):
        """用户准备
        
        Args:
            wxid: 微信ID
            
        Returns:
            准备结果
        """
        return self._post('/Finder/UserPrepare', params={'wxid': wxid})

    # ==================== 朋友相关补全 (Friend) ====================
    
    def blacklist_friend(self, wxid, to_wxid, op=1):
        """拉黑/取消拉黑好友
        
        Args:
            wxid: 微信ID
            to_wxid: 目标微信ID
            op: 操作类型（1拉黑，0取消拉黑）
            
        Returns:
            操作结果
        """
        data = {"Wxid": wxid, "ToWxid": to_wxid, "Op": op}
        return self._post('/Friend/Blacklist', data=data)

    def get_friend_state(self, wxid, to_wxid):
        """获取好友关系状态
        
        Args:
            wxid: 微信ID
            to_wxid: 目标微信ID
            
        Returns:
            好友关系状态
        """
        data = {"Wxid": wxid, "ToWxid": to_wxid}
        return self._post('/Friend/GetFriendstate', data=data)

    def get_m_friend(self, wxid):
        """获取M好友
        
        Args:
            wxid: 微信ID
            
        Returns:
            M好友信息
        """
        return self._post('/Friend/GetMFriend', params={'wxid': wxid})

    def lbs_find_friend(self, wxid, gps_info):
        """附近的人查找
        
        Args:
            wxid: 微信ID
            gps_info: GPS信息
            
        Returns:
            附近的人列表
        """
        data = {"Wxid": wxid, "GpsInfo": gps_info}
        return self._post('/Friend/LbsFind', data=data)

    def pass_friend_verify(self, wxid, encryptusername, ticket, scene=3):
        """通过好友验证
        
        Args:
            wxid: 微信ID
            encryptusername: 加密用户名
            ticket: 票据
            scene: 场景值
            
        Returns:
            通过结果
        """
        data = {
            "Wxid": wxid,
            "Encryptusername": encryptusername,
            "Ticket": ticket,
            "Scene": scene
        }
        return self._post('/Friend/PassVerify', data=data)

    def search_friend(self, wxid, content):
        """搜索好友
        
        Args:
            wxid: 微信ID
            content: 搜索内容（手机号、微信号等）
            
        Returns:
            搜索结果
        """
        data = {"Wxid": wxid, "Content": content}
        return self._post('/Friend/Search', data=data)

    def upload_friend(self, wxid, phone_list):
        """上传手机通讯录
        
        Args:
            wxid: 微信ID
            phone_list: 手机号列表
            
        Returns:
            上传结果
        """
        data = {"Wxid": wxid, "PhoneList": phone_list}
        return self._post('/Friend/Upload', data=data)

    # ==================== 朋友圈补全 (FriendCircle) ====================

    def get_moments(self, wxid, first_page_md5=""):
        """获取朋友圈动态
        
        Args:
            wxid: 微信ID
            first_page_md5: 第一页MD5（翻页用）
            
        Returns:
            朋友圈动态列表
        """
        data = {
            "Wxid": wxid,
            "FirstPageMd5": first_page_md5
        }
        return self._post('/FriendCircle/GetList', data=data)

    def send_moments(self, wxid, content, image_list=None):
        """发送朋友圈
        
        Args:
            wxid: 微信ID
            content: 朋友圈内容
            image_list: 图片列表（base64编码）
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "Content": content,
            "ImageList": image_list or []
        }
        return self._post('/FriendCircle/Upload', data=data)

    def like_moments(self, wxid, sns_id, like_flag=1):
        """点赞朋友圈
        
        Args:
            wxid: 微信ID
            sns_id: 朋友圈ID
            like_flag: 点赞标志，1为点赞，0为取消点赞
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "SnsId": sns_id,
            "LikeFlag": like_flag
        }
        return self._post('/FriendCircle/Operation', data=data)

    def comment_moments(self, wxid, sns_id, content, reply_id=""):
        """评论朋友圈
        
        Args:
            wxid: 微信ID
            sns_id: 朋友圈ID
            content: 评论内容
            reply_id: 回复的评论ID（可选）
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "SnsId": sns_id,
            "Content": content,
            "ReplyId": reply_id
        }
        return self._post('/FriendCircle/Comment', data=data)

    def get_moments_detail(self, wxid, towxid, fristpagemd5="", maxid=0):
            """获取朋友圈详情 - 按照swagger文档FriendCircle.GetDetailparameter定义
            
            Args:
                wxid: 微信ID
                towxid: 目标微信ID
                fristpagemd5: 首页MD5值，默认为空
                maxid: 最大ID，默认为0
                
            Returns:
                朋友圈详情
            """
            # 参数验证
            self._validate_wxid(wxid)
            self._validate_wxid(towxid)
            
            data = {
                "Wxid": wxid, 
                "Towxid": towxid,  # 按swagger文档使用Towxid(首字母大写T)
                "Fristpagemd5": fristpagemd5,
                "Maxid": maxid
            }
            return self._post('/FriendCircle/GetDetail', data=data)


    def get_moments_id_detail(self, wxid, towxid, id):
            """通过ID获取朋友圈详情 - 按照swagger文档FriendCircle.GetIdDetailParam定义
            
            Args:
                wxid: 微信ID
                towxid: 目标微信ID
                id: 朋友圈ID
                
            Returns:
                朋友圈详情
            """
            # 参数验证
            self._validate_wxid(wxid)
            self._validate_wxid(towxid)
            self._validate_required_param(id, "id")
            
            data = {
                "Wxid": wxid, 
                "Towxid": towxid,  # 按swagger文档使用Towxid(首字母大写T)
                "Id": id
            }
            return self._post('/FriendCircle/GetIdDetail', data=data)


    def get_moments_messages(self, wxid):
        """获取朋友圈消息
        
        Args:
            wxid: 微信ID
            
        Returns:
            朋友圈消息
        """
        return self._post('/FriendCircle/Messages', params={'wxid': wxid})

    def sync_moments(self, wxid, scene=0, sns_key=""):
        """同步朋友圈
        
        Args:
            wxid: 微信ID
            scene: 场景值
            sns_key: 朋友圈同步key
            
        Returns:
            同步结果
        """
        data = {"Wxid": wxid, "Scene": scene, "SnsKey": sns_key}
        return self._post('/FriendCircle/MmSnsSync', data=data)

    def set_moments_privacy(self, wxid, black_list=None, white_list=None):
        """设置朋友圈隐私
        
        Args:
            wxid: 微信ID
            black_list: 屏蔽列表
            white_list: 允许列表
            
        Returns:
            设置结果
        """
        data = {
            "Wxid": wxid,
            "BlackList": black_list or [],
            "WhiteList": white_list or []
        }
        return self._post('/FriendCircle/PrivacySettings', data=data)

    # ==================== 群组补全 (Group) ====================

    def consent_join_group(self, wxid, encryptusername, ticket):
        """同意加入群聊
        
        Args:
            wxid: 微信ID
            encryptusername: 加密用户名
            ticket: 票据
            
        Returns:
            同意结果
        """
        data = {
            "Wxid": wxid,
            "Encryptusername": encryptusername,
            "Ticket": ticket
        }
        return self._post('/Group/ConsentToJoin', data=data)

    def get_group_info_detail(self, wxid, chat_room_name):
        """获取群详细信息
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            
        Returns:
            群详细信息
        """
        data = {"Wxid": wxid, "ChatRoomName": chat_room_name}
        return self._post('/Group/GetChatRoomInfoDetail', data=data)

    def get_group_qrcode(self, wxid, chat_room_name):
        """获取群二维码
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            
        Returns:
            群二维码
        """
        data = {"Wxid": wxid, "ChatRoomName": chat_room_name}
        return self._post('/Group/GetQRCode', data=data)

    def invite_group_member(self, wxid, chat_room_name, to_wxids):
        """邀请群成员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            to_wxids: 邀请的微信ID列表
            
        Returns:
            邀请结果
        """
        data = {
            "Wxid": wxid,
            "ChatRoomName": chat_room_name,
            "ToWxids": to_wxids
        }
        return self._post('/Group/InviteChatRoomMember', data=data)

    def move_group_to_contract(self, wxid, chat_room_name):
        """将群移动到通讯录
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            
        Returns:
            移动结果
        """
        data = {"Wxid": wxid, "ChatRoomName": chat_room_name}
        return self._post('/Group/MoveContractList', data=data)

    def operate_group_admin(self, wxid, chat_room_name, to_wxid, op):
        """操作群管理员
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            to_wxid: 目标微信ID
            op: 操作类型
            
        Returns:
            操作结果
        """
        data = {
            "Wxid": wxid,
            "ChatRoomName": chat_room_name,
            "ToWxid": to_wxid,
            "Op": op
        }
        return self._post('/Group/OperateChatRoomAdmin', data=data)

    def scan_into_group(self, wxid, qrcode_url):
        """扫码进群
        
        Args:
            wxid: 微信ID
            qrcode_url: 二维码URL
            
        Returns:
            进群结果
        """
        data = {"Wxid": wxid, "QrcodeUrl": qrcode_url}
        return self._post('/Group/ScanIntoGroup', data=data)

    def scan_into_group_enterprise(self, wxid, qrcode_url):
        """扫码进企业群
        
        Args:
            wxid: 微信ID
            qrcode_url: 二维码URL
            
        Returns:
            进群结果
        """
        data = {"Wxid": wxid, "QrcodeUrl": qrcode_url}
        return self._post('/Group/ScanIntoGroupEnterprise', data=data)

    def set_group_remarks(self, wxid, chat_room_name, remarks):
        """设置群备注
        
        Args:
            wxid: 微信ID
            chat_room_name: 群名称
            remarks: 备注
            
        Returns:
            设置结果
        """
        data = {
            "Wxid": wxid,
            "ChatRoomName": chat_room_name,
            "Remarks": remarks
        }
        return self._post('/Group/SetChatRoomRemarks', data=data)

    # ==================== 标签相关 (Label) ====================

    def add_label(self, wxid, label_name, to_wxids):
        """添加标签
        
        Args:
            wxid: 微信ID
            label_name: 标签名称
            to_wxids: 微信ID列表
            
        Returns:
            添加结果
        """
        data = {
            "Wxid": wxid,
            "LabelName": label_name,
            "ToWxids": to_wxids
        }
        return self._post('/Label/Add', data=data)

    def delete_label(self, wxid, label_id):
        """删除标签
        
        Args:
            wxid: 微信ID
            label_id: 标签ID
            
        Returns:
            删除结果
        """
        data = {"Wxid": wxid, "LabelId": label_id}
        return self._post('/Label/Delete', data=data)

    def get_label_list(self, wxid):
        """获取标签列表
        
        Args:
            wxid: 微信ID
            
        Returns:
            标签列表
        """
        return self._post('/Label/GetList', params={'wxid': wxid})

    def update_label_list(self, wxid, label_id, to_wxids):
        """更新标签列表
        
        Args:
            wxid: 微信ID
            label_id: 标签ID
            to_wxids: 微信ID列表
            
        Returns:
            更新结果
        """
        data = {
            "Wxid": wxid,
            "LabelId": label_id,
            "ToWxids": to_wxids
        }
        return self._post('/Label/UpdateList', data=data)

    def update_label_name(self, wxid, label_id, label_name):
        """更新标签名称
        
        Args:
            wxid: 微信ID
            label_id: 标签ID
            label_name: 新标签名称
            
        Returns:
            更新结果
        """
        data = {
            "Wxid": wxid,
            "LabelId": label_id,
            "LabelName": label_name
        }
        return self._post('/Label/UpdateName', data=data)

    # ==================== 消息补全 (Msg) ====================

    def send_cdn_file(self, wxid, to_wxid, file_data):
        """发送CDN文件
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            file_data: 文件数据
            
        Returns:
            发送结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "FileData": file_data
        }
        return self._post('/Msg/SendCDNFile', data=data)

    def send_cdn_image(self, wxid, to_wxid, image_data):
        """发送CDN图片
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            image_data: 图片数据
            
        Returns:
            发送结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "ImageData": image_data
        }
        return self._post('/Msg/SendCDNImg', data=data)

    def send_cdn_video(self, wxid, to_wxid, video_data):
        """发送CDN视频
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            video_data: 视频数据
            
        Returns:
            发送结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "VideoData": video_data
        }
        return self._post('/Msg/SendCDNVideo', data=data)

    def share_link(self, wxid, to_wxid, title, desc, url, thumb_url=""):
        """分享链接
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            title: 标题
            desc: 描述
            url: 链接URL
            thumb_url: 缩略图URL
            
        Returns:
            分享结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Title": title,
            "Desc": desc,
            "Url": url,
            "ThumbUrl": thumb_url
        }
        return self._post('/Msg/ShareLink', data=data)

    def share_location(self, wxid, to_wxid, lat, lng, scale, label, poiname):
        """分享位置
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            lat: 纬度
            lng: 经度
            scale: 缩放级别
            label: 位置标签
            poiname: 位置名称
            
        Returns:
            分享结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "Lat": lat,
            "Lng": lng,
            "Scale": scale,
            "Label": label,
            "Poiname": poiname
        }
        return self._post('/Msg/ShareLocation', data=data)

    def share_video(self, wxid, to_wxid, video_data):
        """分享视频
        
        Args:
            wxid: 微信ID
            to_wxid: 接收者微信ID
            video_data: 视频数据
            
        Returns:
            分享结果
        """
        data = {
            "Wxid": wxid,
            "ToWxid": to_wxid,
            "VideoData": video_data
        }
        return self._post('/Msg/ShareVideo', data=data)

    # ==================== 公众号相关 (OfficialAccounts) ====================

    def follow_official_account(self, wxid, account_wxid):
        """关注公众号
        
        Args:
            wxid: 微信ID
            account_wxid: 公众号微信ID
            
        Returns:
            关注结果
        """
        data = {"Wxid": wxid, "AccountWxid": account_wxid}
        return self._post('/OfficialAccounts/Follow', data=data)

    def get_app_msg_ext(self, wxid, msg_data):
        """获取公众号消息扩展信息
        
        Args:
            wxid: 微信ID
            msg_data: 消息数据
            
        Returns:
            扩展信息
        """
        data = {"Wxid": wxid, "MsgData": msg_data}
        return self._post('/OfficialAccounts/GetAppMsgExt', data=data)

    def get_app_msg_ext_like(self, wxid, msg_data):
        """获取公众号消息点赞信息
        
        Args:
            wxid: 微信ID
            msg_data: 消息数据
            
        Returns:
            点赞信息
        """
        data = {"Wxid": wxid, "MsgData": msg_data}
        return self._post('/OfficialAccounts/GetAppMsgExtLike', data=data)

    def jsapi_pre_verify(self, wxid, url):
        """JSAPI预验证
        
        Args:
            wxid: 微信ID
            url: 验证URL
            
        Returns:
            验证结果
        """
        data = {"Wxid": wxid, "Url": url}
        return self._post('/OfficialAccounts/JSAPIPreVerify', data=data)

    def mp_get_a8_key(self, wxid, url):
        """获取公众号A8Key
        
        Args:
            wxid: 微信ID
            url: 公众号URL
            
        Returns:
            A8Key信息
        """
        data = {"Wxid": wxid, "Url": url}
        return self._post('/OfficialAccounts/MpGetA8Key', data=data)

    def oauth_authorize(self, wxid, app_id, scope, state):
        """OAuth授权
        
        Args:
            wxid: 微信ID
            app_id: 应用ID
            scope: 授权范围
            state: 状态参数
            
        Returns:
            授权结果
        """
        data = {
            "Wxid": wxid,
            "AppId": app_id,
            "Scope": scope,
            "State": state
        }
        return self._post('/OfficialAccounts/OauthAuthorize', data=data)

    def quit_official_account(self, wxid, account_wxid):
        """取消关注公众号
        
        Args:
            wxid: 微信ID
            account_wxid: 公众号微信ID
            
        Returns:
            取消关注结果
        """
        data = {"Wxid": wxid, "AccountWxid": account_wxid}
        return self._post('/OfficialAccounts/Quit', data=data)

    # ==================== 企业微信联系人相关 (QWContact) ====================

    def qw_apply_add_contact(self, wxid, contact_data):
        """申请添加企业微信联系人
        
        Args:
            wxid: 微信ID
            contact_data: 联系人数据
            
        Returns:
            申请结果
        """
        data = {"Wxid": wxid, "ContactData": contact_data}
        return self._post('/QWContact/QWApplyAddContact', data=data)

    def qw_add_contact(self, wxid, contact_data):
        """添加企业微信联系人
        
        Args:
            wxid: 微信ID
            contact_data: 联系人数据
            
        Returns:
            添加结果
        """
        data = {"Wxid": wxid, "ContactData": contact_data}
        return self._post('/QWContact/QWAddContact', data=data)


    def search_qw_contact(self, wxid, keyword):
        """搜索企业微信联系人
        
        Args:
            wxid: 微信ID
            keyword: 搜索关键词
            
        Returns:
            搜索结果
        """
        data = {"Wxid": wxid, "Keyword": keyword}
        return self._post('/QWContact/SearchQWContact', data=data)

    # ==================== 打招呼相关 (SayHello) ====================

    def say_hello_v1(self, wxid, to_wxid, content):
        """打招呼V1
        
        Args:
            wxid: 微信ID
            to_wxid: 目标微信ID
            content: 打招呼内容
            
        Returns:
            打招呼结果
        """
        data = {"Wxid": wxid, "ToWxid": to_wxid, "Content": content}
        return self._post('/SayHello/Modelv1', data=data)

    def say_hello_v2(self, wxid, to_wxid, content):
        """打招呼V2
        
        Args:
            wxid: 微信ID
            to_wxid: 目标微信ID
            content: 打招呼内容
            
        Returns:
            打招呼结果
        """
        data = {"Wxid": wxid, "ToWxid": to_wxid, "Content": content}
        return self._post('/SayHello/Modelv2', data=data)

    # ==================== 支付补全 (TenPay) ====================

    def transfer_money(self, wxid, invalid_time, to_user_name, transfer_id, transaction_id):
            """确认收款 - 按照swagger文档TenPay.CollectmoneyModel定义
            
            Args:
                wxid: 微信ID
                invalid_time: 无效时间
                to_user_name: 目标用户名
                transfer_id: 转账ID
                transaction_id: 交易ID
                
            Returns:
                成功返回 {"Success": true}
            """
            # 参数验证
            self._validate_wxid(wxid)
            self._validate_required_param(to_user_name, "to_user_name")
            self._validate_required_param(transfer_id, "transfer_id")
            self._validate_required_param(transaction_id, "transaction_id")
            
            data = {
                "Wxid": wxid,
                "InvalidTime": invalid_time,
                "ToUserName": to_user_name,
                "TransFerId": transfer_id,
                "TransactionId": transaction_id
            }
            return self._post('/TenPay/Collectmoney', data=data)


    def receive_transfer(self, wxid, transfer_id):
        """收款
        
        Args:
            wxid: 微信ID
            transfer_id: 转账ID
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "TransferId": transfer_id
        }
        return self._post('/TenPay/Receivewxhb', data=data)

    def get_ma_pay_qcode(self, wxid, money):
        """获取收款码
        
        Args:
            wxid: 微信ID
            money: 金额
            
        Returns:
            收款码
        """
        data = {"Wxid": wxid, "Money": money}
        return self._post('/TenPay/GeMaPayQCode', data=data)

    def get_ma_skd_pay_qcode(self, wxid, money):
        """获取收款码(SDK)
        
        Args:
            wxid: 微信ID
            money: 金额
            
        Returns:
            收款码
        """
        data = {"Wxid": wxid, "Money": money}
        return self._post('/TenPay/GeMaSkdPayQCode', data=data)

    def get_encrypt_info(self, wxid):
        """获取加密信息
        
        Args:
            wxid: 微信ID
            
        Returns:
            加密信息
        """
        return self._post('/TenPay/GetEncryptInfo', params={'wxid': wxid})

    def open_hongbao(self, wxid, hongbao_data):
        """打开红包
        
        Args:
            wxid: 微信ID
            hongbao_data: 红包数据
            
        Returns:
            红包信息
        """
        data = {"Wxid": wxid, "HongbaoData": hongbao_data}
        return self._post('/TenPay/OpenHongBao', data=data)

    def open_wxhb(self, wxid, hongbao_data):
        """打开微信红包
        
        Args:
            wxid: 微信ID
            hongbao_data: 红包数据
            
        Returns:
            红包信息
        """
        data = {"Wxid": wxid, "HongbaoData": hongbao_data}
        return self._post('/TenPay/Openwxhb', data=data)

    def qry_detail_wxhb(self, wxid, hongbao_id):
        """查询微信红包详情
        
        Args:
            wxid: 微信ID
            hongbao_id: 红包ID
            
        Returns:
            红包详情
        """
        data = {"Wxid": wxid, "HongbaoId": hongbao_id}
        return self._post('/TenPay/Qrydetailwxhb', data=data)

    def sj_skd_pay_qcode(self, wxid, money):
        """生成收款码(SDK)
        
        Args:
            wxid: 微信ID
            money: 金额
            
        Returns:
            收款码
        """
        data = {"Wxid": wxid, "Money": money}
        return self._post('/TenPay/SjSkdPayQCode', data=data)

    # ==================== 工具相关 (Tools) ====================

    def cdn_download_image(self, wxid, cdn_url):
        """CDN下载图片
        
        Args:
            wxid: 微信ID
            cdn_url: CDN链接
            
        Returns:
            图片数据
        """
        data = {"Wxid": wxid, "CdnUrl": cdn_url}
        return self._post('/Tools/CdnDownloadImage', data=data)

    def download_file(self, wxid, file_url):
        """下载文件
        
        Args:
            wxid: 微信ID
            file_url: 文件URL
            
        Returns:
            文件数据
        """
        data = {"Wxid": wxid, "FileUrl": file_url}
        return self._post('/Tools/DownloadFile', data=data)

    def download_img(self, wxid, image_url):
        """下载图片
        
        Args:
            wxid: 微信ID
            image_url: 图片URL
            
        Returns:
            图片数据
        """
        data = {"Wxid": wxid, "ImageUrl": image_url}
        return self._post('/Tools/DownloadImg', data=data)

    def download_video(self, wxid, video_url):
        """下载视频
        
        Args:
            wxid: 微信ID
            video_url: 视频URL
            
        Returns:
            视频数据
        """
        data = {"Wxid": wxid, "VideoUrl": video_url}
        return self._post('/Tools/DownloadVideo', data=data)

    def download_voice(self, wxid, voice_url):
        """下载语音
        
        Args:
            wxid: 微信ID
            voice_url: 语音URL
            
        Returns:
            语音数据
        """
        data = {"Wxid": wxid, "VoiceUrl": voice_url}
        return self._post('/Tools/DownloadVoice', data=data)

    def generate_pay_qcode(self, wxid, amount):
        """生成支付二维码
        
        Args:
            wxid: 微信ID
            amount: 金额
            
        Returns:
            支付二维码
        """
        data = {"Wxid": wxid, "Amount": amount}
        return self._post('/Tools/GeneratePayQCode', data=data)

    def get_a8_key(self, wxid, url):
        """获取A8Key
        
        Args:
            wxid: 微信ID
            url: URL
            
        Returns:
            A8Key
        """
        data = {"Wxid": wxid, "Url": url}
        return self._post('/Tools/GetA8Key', data=data)

    def get_band_card_list(self, wxid):
        """获取银行卡列表
        
        Args:
            wxid: 微信ID
            
        Returns:
            银行卡列表
        """
        return self._post('/Tools/GetBandCardList', params={'wxid': wxid})

    def get_bound_hard_devices(self, wxid):
        """获取绑定的硬件设备
        
        Args:
            wxid: 微信ID
            
        Returns:
            设备列表
        """
        return self._post('/Tools/GetBoundHardDevices', params={'wxid': wxid})

    def get_cdn_dns(self, wxid):
        """获取CDN DNS
        
        Args:
            wxid: 微信ID
            
        Returns:
            DNS信息
        """
        return self._post('/Tools/GetCdnDns', params={'wxid': wxid})

    def oauth_sdk_app(self, wxid, app_id):
        """OAuth SDK应用
        
        Args:
            wxid: 微信ID
            app_id: 应用ID
            
        Returns:
            OAuth结果
        """
        data = {"Wxid": wxid, "AppId": app_id}
        return self._post('/Tools/OauthSdkApp', data=data)

    def set_proxy(self, proxy_config):
        """设置代理
        
        Args:
            proxy_config: 代理配置
            
        Returns:
            设置结果
        """
        return self._post('/Tools/setproxy', data=proxy_config)

    def third_app_grant(self, wxid, app_data):
        """第三方应用授权
        
        Args:
            wxid: 微信ID
            app_data: 应用数据
            
        Returns:
            授权结果
        """
        data = {"Wxid": wxid, "AppData": app_data}
        return self._post('/Tools/ThirdAppGrant', data=data)

    def update_step_number(self, wxid, step_count):
        """更新步数
        
        Args:
            wxid: 微信ID
            step_count: 步数
            
        Returns:
            更新结果
        """
        data = {"Wxid": wxid, "StepCount": step_count}
        return self._post('/Tools/UpdateStepNumberApi', data=data)

    def upload_file(self, wxid, file_data):
        """上传文件
        
        Args:
            wxid: 微信ID
            file_data: 文件数据
            
        Returns:
            上传结果
        """
        data = {"Wxid": wxid, "FileData": file_data}
        return self._post('/Tools/UploadFile', data=data)

    # ==================== 用户信息补全 (User) ====================

    def get_user_info(self, wxid):
        """获取用户信息
        
        Args:
            wxid: 微信ID
            
        Returns:
            用户信息
        """
        return self._post('/User/GetContractProfile', params={'wxid': wxid})

    def set_user_info(self, wxid, nickname="", signature="", sex=0, country="", province="", city=""):
        """设置用户信息
        
        Args:
            wxid: 微信ID
            nickname: 昵称
            signature: 签名
            sex: 性别（0未知，1男，2女）
            country: 国家
            province: 省份
            city: 城市
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "NickName": nickname,
            "Signature": signature,
            "Sex": sex,
            "Country": country,
            "Province": province,
            "City": city
        }
        return self._post('/User/UpdateProfile', data=data)

    def set_avatar(self, wxid, base64_img):
        """设置头像
        
        Args:
            wxid: 微信ID
            base64_img: 头像图片的base64编码
            
        Returns:
            成功返回 {"Success": true}
        """
        data = {
            "Wxid": wxid,
            "Base64": base64_img
        }
        return self._post('/User/UploadHeadImage', data=data)

    def binding_email(self, wxid, email, verify_code):
        """绑定邮箱
        
        Args:
            wxid: 微信ID
            email: 邮箱地址
            verify_code: 验证码
            
        Returns:
            绑定结果
        """
        data = {"Wxid": wxid, "Email": email, "VerifyCode": verify_code}
        return self._post('/User/BindingEmail', data=data)

    def binding_mobile(self, wxid, mobile, verify_code):
        """绑定手机号
        
        Args:
            wxid: 微信ID
            mobile: 手机号
            verify_code: 验证码
            
        Returns:
            绑定结果
        """
        data = {"Wxid": wxid, "Mobile": mobile, "VerifyCode": verify_code}
        return self._post('/User/BindingMobile', data=data)

    def bind_qq(self, wxid, qq_number):
        """绑定QQ
        
        Args:
            wxid: 微信ID
            qq_number: QQ号
            
        Returns:
            绑定结果
        """
        data = {"Wxid": wxid, "QqNumber": qq_number}
        return self._post('/User/BindQQ', data=data)

    def del_safety_info(self, wxid, info_type):
        """删除安全信息
        
        Args:
            wxid: 微信ID
            info_type: 信息类型
            
        Returns:
            删除结果
        """
        data = {"Wxid": wxid, "InfoType": info_type}
        return self._post('/User/DelSafetyInfo', data=data)

    def get_user_qrcode(self, wxid):
        """获取用户二维码
        
        Args:
            wxid: 微信ID
            
        Returns:
            用户二维码
        """
        return self._post('/User/GetQRCode', params={'wxid': wxid})

    def get_safety_info(self, wxid):
        """获取安全信息
        
        Args:
            wxid: 微信ID
            
        Returns:
            安全信息
        """
        return self._post('/User/GetSafetyInfo', params={'wxid': wxid})

    def set_privacy_settings(self, wxid, settings):
        """设置隐私设置
        
        Args:
            wxid: 微信ID
            settings: 隐私设置
            
        Returns:
            设置结果
        """
        data = {"Wxid": wxid, "Settings": settings}
        return self._post('/User/PrivacySettings', data=data)

    def report_motion(self, wxid, motion_data):
        """上报运动数据
        
        Args:
            wxid: 微信ID
            motion_data: 运动数据
            
        Returns:
            上报结果
        """
        data = {"Wxid": wxid, "MotionData": motion_data}
        return self._post('/User/ReportMotion', data=data)

    def send_verify_mobile(self, wxid, mobile):
        """发送手机验证码
        
        Args:
            wxid: 微信ID
            mobile: 手机号
            
        Returns:
            发送结果
        """
        data = {"Wxid": wxid, "Mobile": mobile}
        return self._post('/User/SendVerifyMobile', data=data)

    def set_alias(self, wxid, alias):
        """设置微信号
        
        Args:
            wxid: 微信ID
            alias: 微信号
            
        Returns:
            设置结果
        """
        data = {"Wxid": wxid, "Alias": alias}
        return self._post('/User/SetAlisa', data=data)

    def set_password(self, wxid, old_password, new_password):
        """设置密码
        
        Args:
            wxid: 微信ID
            old_password: 旧密码
            new_password: 新密码
            
        Returns:
            设置结果
        """
        data = {"Wxid": wxid, "OldPassword": old_password, "NewPassword": new_password}
        return self._post('/User/SetPasswd', data=data)

    def verify_password(self, wxid, password):
        """验证密码
        
        Args:
            wxid: 微信ID
            password: 密码
            
        Returns:
            验证结果
        """
        data = {"Wxid": wxid, "Password": password}
        return self._post('/User/VerifyPasswd', data=data)

    # ==================== 小程序相关 (Wxapp) ====================

    def add_avatar(self, wxid, avatar_data):
        """添加头像
        
        Args:
            wxid: 微信ID
            avatar_data: 头像数据
            
        Returns:
            添加结果
        """
        data = {"Wxid": wxid, "AvatarData": avatar_data}
        return self._post('/Wxapp/AddAvatar', data=data)

    def add_mobile(self, wxid, mobile):
        """添加手机号
        
        Args:
            wxid: 微信ID
            mobile: 手机号
            
        Returns:
            添加结果
        """
        data = {"Wxid": wxid, "Mobile": mobile}
        return self._post('/Wxapp/AddMobile', data=data)

    def add_wxapp_record(self, wxid, record_data):
        """添加小程序记录
        
        Args:
            wxid: 微信ID
            record_data: 记录数据
            
        Returns:
            添加结果
        """
        data = {"Wxid": wxid, "RecordData": record_data}
        return self._post('/Wxapp/AddWxAppRecord', data=data)

    def cloud_call_function(self, wxid, function_name, params):
        """调用云函数
        
        Args:
            wxid: 微信ID
            function_name: 函数名
            params: 参数
            
        Returns:
            调用结果
        """
        data = {"Wxid": wxid, "FunctionName": function_name, "Params": params}
        return self._post('/Wxapp/CloudCallFunction', data=data)

    def del_mobile(self, wxid, mobile):
        """删除手机号
        
        Args:
            wxid: 微信ID
            mobile: 手机号
            
        Returns:
            删除结果
        """
        data = {"Wxid": wxid, "Mobile": mobile}
        return self._post('/Wxapp/DelMobile', data=data)

    def get_all_mobile(self, wxid):
        """获取所有手机号
        
        Args:
            wxid: 微信ID
            
        Returns:
            手机号列表
        """
        return self._post('/Wxapp/GetAllMobile', params={'wxid': wxid})

    def get_random_avatar(self, wxid):
        """获取随机头像
        
        Args:
            wxid: 微信ID
            
        Returns:
            随机头像
        """
        return self._post('/Wxapp/GetRandomAvatar', params={'wxid': wxid})

    def get_user_open_id(self, wxid, app_id):
        """获取用户OpenID
        
        Args:
            wxid: 微信ID
            app_id: 应用ID
            
        Returns:
            OpenID
        """
        data = {"Wxid": wxid, "AppId": app_id}
        return self._post('/Wxapp/GetUserOpenId', data=data)

    def js_get_sessionid(self, wxid, js_code):
        """JS获取SessionID
        
        Args:
            wxid: 微信ID
            js_code: JS代码
            
        Returns:
            SessionID
        """
        data = {"Wxid": wxid, "JsCode": js_code}
        return self._post('/Wxapp/JSGetSessionid', data=data)

    def js_get_sessionid_qrcode(self, wxid, qrcode_data):
        """JS获取SessionID二维码
        
        Args:
            wxid: 微信ID
            qrcode_data: 二维码数据
            
        Returns:
            SessionID
        """
        data = {"Wxid": wxid, "QrcodeData": qrcode_data}
        return self._post('/Wxapp/JSGetSessionidQRcode', data=data)

    def js_login(self, wxid, login_data):
        """JS登录
        
        Args:
            wxid: 微信ID
            login_data: 登录数据
            
        Returns:
            登录结果
        """
        data = {"Wxid": wxid, "LoginData": login_data}
        return self._post('/Wxapp/JSLogin', data=data)

    def js_operate_wx_data(self, wxid, operation, data):
        """JS操作微信数据
        
        Args:
            wxid: 微信ID
            operation: 操作类型
            data: 数据
            
        Returns:
            操作结果
        """
        request_data = {"Wxid": wxid, "Operation": operation, "Data": data}
        return self._post('/Wxapp/JSOperateWxData', data=request_data)

    def qrcode_auth_login(self, wxid, qrcode_data):
        """二维码授权登录
        
        Args:
            wxid: 微信ID
            qrcode_data: 二维码数据
            
        Returns:
            登录结果
        """
        data = {"Wxid": wxid, "QrcodeData": qrcode_data}
        return self._post('/Wxapp/QrcodeAuthLogin', data=data)

    def upload_avatar_img(self, wxid, image_data):
        """上传头像图片
        
        Args:
            wxid: 微信ID
            image_data: 图片数据
            
        Returns:
            上传结果
        """
        data = {"Wxid": wxid, "ImageData": image_data}
        return self._post('/Wxapp/UploadAvatarImg', data=data)