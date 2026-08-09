import base64
import requests
import os
import json
from urllib.parse import urlencode, urlparse
from lib.wxpad.api_adapter import build_max_requests

class WxpadClient:
    def __init__(self, base_url, admin_key=None, user_key=None):
        self.base_url = base_url.rstrip('/')

        # 从配置文件读取管理员密钥
        if admin_key is None:
            try:
                from config import conf
                self.admin_key = conf().get("wechatpadpro_admin_key", "12345")
            except Exception:
                self.admin_key = "12345"  # 默认值
        else:
            self.admin_key = admin_key

        # 从配置文件读取普通用户密钥
        if user_key is None:
            try:
                from config import conf
                self.user_key = conf().get("wechatpadpro_user_key", None)
            except Exception:
                self.user_key = None
        else:
            self.user_key = user_key

        self.api_mode = "wechat08" if self._detect_wechat08_mode() else "padpro"
        self.last_qr_uuid = None
        self.last_qr_check_token = None
        self.wxid = None
        self.device_id = None
        self.device_name = os.environ.get("WECHATPAD_DEVICE_NAME", "iPad")

    def _detect_wechat08_mode(self):
        """Detect Wechat 08 backend by its service port."""
        try:
            return urlparse(self.base_url).port == 8062
        except Exception:
            return self.base_url.endswith(":8062")

    def _is_wechat08(self):
        return self.api_mode == "wechat08"

    def _wechat08_url(self, path):
        base_url = self.base_url[:-4] if self.base_url.endswith("/api") else self.base_url
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{base_url}/api{normalized_path}"

    def _wechat08_post(
        self,
        path,
        data=None,
        params=None,
        authcode=None,
        admin_token=None,
        extra_headers=None,
    ):
        url = self._wechat08_url(path)
        headers = {'Content-Type': 'application/json'}
        request_params = dict(params or {})
        if authcode is not None:
            final_authcode = authcode or self.user_key
            if not final_authcode:
                raise Exception("此接口需要授权码，请先生成授权码或配置 wechatpadpro_user_key")
            headers["X-Access-Token"] = final_authcode
        if admin_token is not None:
            final_admin_token = admin_token or self.admin_key
            if not final_admin_token:
                raise Exception("此接口需要管理员令牌，请配置 wechatpadpro_admin_key")
            headers["X-Admin-Token"] = final_admin_token
        headers.update(extra_headers or {})
        return self._request_with_retry(
            'POST',
            url,
            json=data or {},
            params=request_params,
            headers=headers
        )

    def _wechat08_ok(self, result):
        if not isinstance(result, dict):
            return False
        data = result.get("Data", result.get("data"))
        if isinstance(data, dict):
            base_response = data.get("BaseResponse", data.get("baseResponse"))
            if isinstance(base_response, dict):
                inner_ret = base_response.get("ret", base_response.get("Ret"))
                if inner_ret not in (None, 0, "0"):
                    return False
        if "Success" in result:
            return result.get("Success") is True
        return result.get("Code") in (0, 1, 200)

    @staticmethod
    def _wechat08_inner_error(result):
        if not isinstance(result, dict):
            return None, ""
        data = result.get("Data", result.get("data"))
        if not isinstance(data, dict):
            return None, ""
        base_response = data.get("BaseResponse", data.get("baseResponse"))
        if not isinstance(base_response, dict):
            return None, ""
        inner_ret = base_response.get("ret", base_response.get("Ret"))
        err_msg = base_response.get("errMsg", base_response.get("ErrMsg", ""))
        if isinstance(err_msg, dict):
            err_msg = err_msg.get("string", err_msg.get("str", ""))
        return inner_ret, str(err_msg or "")

    def _normalize_wechat08_response(self, result, data=None):
        """Normalize MAX responses to the legacy client contract used by the channel."""
        if not isinstance(result, dict):
            return {
                "Code": -1,
                "Success": False,
                "Message": "Invalid WeChatPadProMAX response",
                "Data": data,
                "raw": result,
            }

        normalized = dict(result)
        ok = self._wechat08_ok(result)
        inner_ret, inner_message = self._wechat08_inner_error(result)
        normalized["RawCode"] = result.get("Code")
        normalized["Code"] = 200 if ok else (
            inner_ret if inner_ret not in (None, 0, "0") else result.get("Code", -1)
        )
        normalized["Success"] = ok
        if not ok and inner_message:
            normalized["Message"] = inner_message
        if data is not None:
            normalized["Data"] = data
        else:
            normalized.setdefault("Data", None)
        return normalized

    def _wechat08_post_many(self, path, payloads, user_key=None):
        """Send legacy list-style payloads through MAX single-message endpoints."""
        results = []
        for payload in payloads:
            result = self._wechat08_post(path, data=payload, authcode=user_key or self.user_key)
            normalized = self._normalize_wechat08_response(result)
            if normalized.get("Code") != 200:
                return normalized
            results.append(normalized.get("Data"))

        return {
            "Code": 200,
            "Success": True,
            "Message": "success",
            "Data": results,
        }

    def _wechat08_unsupported(self, legacy_path, reason):
        return {
            "Code": 501,
            "Success": False,
            "Message": f"WeChatPadProMAX 不支持旧接口 {legacy_path}: {reason}",
            "Data": None,
        }

    def _wechat08_dispatch_legacy(self, legacy_path, data=None, user_key=None):
        """Execute a translated legacy request without ever calling an old URL."""
        requests_to_make, reason = build_max_requests(
            legacy_path,
            data,
            wxid=self.wxid or "",
            device_name=self.device_name,
        )
        if reason:
            return self._wechat08_unsupported(legacy_path, reason)

        results = []
        for request_spec in requests_to_make:
            params = dict(request_spec.get("params") or {})
            access_token = user_key or self.user_key
            if not access_token:
                raise Exception("此接口需要授权码，请配置 wechatpadpro_user_key")
            method = request_spec.get("method", "POST").upper()
            request_kwargs = {
                "params": params,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Access-Token": access_token,
                },
            }
            if method == "POST":
                request_kwargs["json"] = request_spec.get("data") or {}
            result = self._request_with_retry(
                method,
                self._wechat08_url(request_spec["path"]),
                **request_kwargs,
            )
            normalized = self._normalize_wechat08_legacy_response(legacy_path, result)
            if normalized.get("Code") != 200:
                return normalized
            results.append(normalized)

        if len(results) == 1:
            return results[0]
        return {
            "Code": 200,
            "Success": True,
            "Message": "success",
            "Data": [item.get("Data") for item in results],
            "raw": [item.get("raw", item) for item in results],
        }

    def _normalize_wechat08_contact(self, contact):
        if not isinstance(contact, dict):
            return contact
        normalized = dict(contact)
        user_name = (
            self._extract_wechat08_string(contact.get("UserName"))
            or self._extract_wechat08_string(contact.get("userName"))
        )
        nickname = (
            self._extract_wechat08_string(contact.get("NickName"))
            or self._extract_wechat08_string(contact.get("nickName"))
        )
        remark = (
            self._extract_wechat08_string(contact.get("Remark"))
            or self._extract_wechat08_string(contact.get("remark"))
        )
        normalized.update({
            "userName": user_name,
            "nickName": nickname,
            "remark": remark,
            "alias": self._extract_wechat08_string(contact.get("Alias")) or contact.get("alias", ""),
            "smallHeadImgUrl": contact.get("SmallHeadImgUrl", contact.get("smallHeadImgUrl", "")),
            "bigHeadImgUrl": contact.get("BigHeadImgUrl", contact.get("bigHeadImgUrl", "")),
        })
        return normalized

    def _normalize_wechat08_legacy_response(self, legacy_path, result):
        normalized = self._normalize_wechat08_response(result)
        if normalized.get("Code") != 200:
            return normalized

        raw_data = result.get("Data") if isinstance(result, dict) else None
        if legacy_path in ("/friend/GetContactDetailsList",):
            contacts = raw_data.get("ContactList", []) if isinstance(raw_data, dict) else []
            normalized["Data"] = {"contactList": [self._normalize_wechat08_contact(item) for item in contacts]}
        elif legacy_path == "/group/GetChatroomMemberDetail":
            contacts = raw_data.get("ContactList", []) if isinstance(raw_data, dict) else []
            contact = contacts[0] if contacts and isinstance(contacts[0], dict) else {}
            room_data = contact.get("NewChatroomData", {}) if isinstance(contact, dict) else {}
            members = room_data.get("ChatRoomMember", []) if isinstance(room_data, dict) else []
            legacy_members = []
            for member in members:
                if not isinstance(member, dict):
                    continue
                nickname = self._extract_wechat08_string(member.get("NickName"))
                legacy_members.append({
                    "user_name": self._extract_wechat08_string(member.get("UserName")),
                    "nick_name": nickname,
                    "display_name": nickname,
                    "raw": member,
                })
            normalized["Data"] = {
                "member_data": {"chatroom_member_list": legacy_members},
                "raw": raw_data,
            }
        elif legacy_path in ("/message/GetMsgBigImg", "/message/GetMsgVideo", "/message/GetMsgVoice", "/message/SendCdnDownload"):
            data = dict(raw_data) if isinstance(raw_data, dict) else {"raw": raw_data}
            file_data = (
                data.get("FileData")
                or data.get("fileData")
                or data.get("Base64")
                or data.get("base64")
                or data.get("Image")
                or data.get("image")
                or data.get("Video")
                or data.get("video")
                or data.get("File")
                or data.get("file")
                or data.get("Voice")
                or data.get("voice")
            )
            if file_data:
                data.setdefault("FileData", file_data)
                data.setdefault("Base64", file_data)
            normalized["Data"] = data

        normalized["raw"] = result
        return normalized

    @staticmethod
    def _extract_max_media_base64(result, *preferred_keys):
        """Extract a base64 media payload from a MAX response without guessing IDs."""
        if not isinstance(result, dict):
            return ""

        data = result.get("Data", result.get("data", {}))
        if isinstance(data, str):
            return data
        if not isinstance(data, dict):
            return ""

        keys = preferred_keys + (
            "FileData", "fileData", "file_data", "Base64", "base64",
            "Image", "image", "Video", "video", "File", "file",
            "Voice", "voice", "Data", "data",
        )
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value.split(",", 1)[1] if value.startswith("data:") and "," in value else value

        # DownloadImg/DownloadVideo return each binary block in the protobuf
        # buffer envelope: Data.data.buffer.  Keep this lookup explicit so an
        # unrelated nested string can never be mistaken for media bytes.
        block = data.get("data", data.get("Data", {}))
        if isinstance(block, dict):
            value = block.get("buffer", block.get("Buffer"))
            if isinstance(value, str) and value:
                return value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
        return ""

    def _normalize_max_download_response(self, result, *preferred_keys):
        normalized = self._normalize_wechat08_response(result)
        if normalized.get("Code") != 200:
            return normalized

        encoded = self._extract_max_media_base64(result, *preferred_keys)
        data = result.get("Data", {}) if isinstance(result, dict) else {}
        data = dict(data) if isinstance(data, dict) else {}
        if encoded:
            data["FileData"] = encoded
            data["Base64"] = encoded
        normalized["Data"] = data
        normalized["raw"] = result
        return normalized

    def _download_max_media_chunks(self, endpoint, download_context, user_key=None):
        """Download a v2 image/video/file context exactly as documented by MAX."""
        if not self._is_wechat08():
            return self._wechat08_unsupported(endpoint, "结构化媒体下载仅用于 WeChatPadProMAX")
        if not isinstance(download_context, dict) or not download_context:
            return self._wechat08_unsupported(endpoint, "download_context 不能为空")

        context = dict(download_context)
        section = context.get("section", {})
        section = dict(section) if isinstance(section, dict) else {}
        try:
            total_len = max(0, int(context.get("data_len", 0) or 0))
            start_pos = max(0, int(section.get("start_pos", 0) or 0))
            chunk_size = max(1, int(section.get("data_len", 1048576) or 1048576))
        except (TypeError, ValueError):
            return self._wechat08_unsupported(endpoint, "download_context 的长度字段无效")

        chunks = []
        last_result = None
        # 256 MiB with the documented 1 MiB chunks is already far beyond normal
        # chat media.  The cap prevents a malformed response from looping forever.
        for _ in range(256):
            payload = dict(context)
            payload["section"] = {
                "start_pos": start_pos,
                "data_len": chunk_size,
            }
            result = self._wechat08_post(
                endpoint,
                data=payload,
                authcode=user_key or self.user_key,
            )
            last_result = result
            normalized = self._normalize_wechat08_response(result)
            if normalized.get("Code") != 200:
                return normalized

            encoded = self._extract_max_media_base64(result)
            if not encoded:
                return self._normalize_max_download_response(result)
            try:
                chunk = base64.b64decode(encoded)
            except (ValueError, TypeError):
                return {
                    "Code": -1,
                    "Success": False,
                    "Message": f"{endpoint} 返回了无效的 Base64 数据",
                    "Data": None,
                    "raw": result,
                }
            if not chunk:
                break
            chunks.append(chunk)
            start_pos += len(chunk)

            data = result.get("Data", {}) if isinstance(result, dict) else {}
            data = data if isinstance(data, dict) else {}
            response_total_len = data.get("totalLen", data.get("total_len"))
            try:
                response_total_len = max(0, int(response_total_len or 0))
            except (TypeError, ValueError):
                response_total_len = 0
            if response_total_len:
                total_len = response_total_len
            end_flag = data.get("EndFlag", data.get("end_flag"))
            if end_flag in (1, True, "1", "true", "True"):
                break
            if total_len:
                if start_pos >= total_len:
                    break
            elif len(chunk) < chunk_size:
                break
        else:
            return {
                "Code": -1,
                "Success": False,
                "Message": f"{endpoint} 分片数量超过安全上限",
                "Data": None,
            }

        if not chunks:
            return self._normalize_max_download_response(last_result or {})

        encoded = base64.b64encode(b"".join(chunks)).decode("ascii")
        data = (
            dict(last_result.get("Data", {}))
            if isinstance(last_result, dict) and isinstance(last_result.get("Data"), dict)
            else {}
        )
        data.update({
            "FileData": encoded,
            "Base64": encoded,
            "ChunkCount": len(chunks),
        })
        return self._normalize_wechat08_response(last_result or {}, data)

    def download_cdn_image(self, cdn_context, user_key=None):
        """Download one image variant from ``image.cdn_download_contexts``."""
        if not self._is_wechat08():
            return self._wechat08_unsupported("/Tools/CdnDownloadImage", "该接口仅用于 WeChatPadProMAX")
        result = self._wechat08_post(
            "/Tools/CdnDownloadImage",
            data=dict(cdn_context or {}),
            authcode=user_key or self.user_key,
        )
        return self._normalize_max_download_response(result, "Image", "image")

    def download_image_context(self, download_context, user_key=None):
        return self._download_max_media_chunks("/Tools/DownloadImg", download_context, user_key)

    def download_video_context(self, download_context, user_key=None):
        return self._download_max_media_chunks("/Tools/DownloadVideo", download_context, user_key)

    def download_file_context(self, download_context, user_key=None):
        return self._download_max_media_chunks("/Tools/DownloadFile", download_context, user_key)

    def download_voice_context(self, download_context, user_key=None):
        """Download voice using the exact v2 voice context fields."""
        if not self._is_wechat08():
            return self._wechat08_unsupported("/Tools/DownloadVoice", "该接口仅用于 WeChatPadProMAX")
        result = self._wechat08_post(
            "/Tools/DownloadVoice",
            data=dict(download_context or {}),
            authcode=user_key or self.user_key,
        )
        return self._normalize_max_download_response(result, "Voice", "voice")

    def _extract_wechat08_string(self, value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("str", "String", "string", "value", "Value"):
                text = value.get(key)
                if isinstance(text, str) and text:
                    return text
        return ""

    def _extract_wechat08_wxid(self, value):
        if isinstance(value, dict):
            for key in ("WxId", "Wxid", "wxid", "UserName", "userName", "username", "user_name"):
                text = self._extract_wechat08_string(value.get(key))
                if text:
                    return text

            for key in ("acctSectResp", "AcctSectResp", "accountInfo", "AccountInfo", "Data", "data"):
                text = self._extract_wechat08_wxid(value.get(key))
                if text:
                    return text

            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    text = self._extract_wechat08_wxid(nested)
                    if text:
                        return text

        if isinstance(value, list):
            for item in value:
                text = self._extract_wechat08_wxid(item)
                if text:
                    return text

        return ""

    def _wechat08_login_state(self, result):
        envelope_data = result.get("Data") if isinstance(result, dict) else {}
        envelope_data = envelope_data if isinstance(envelope_data, dict) else {}
        account_data = envelope_data.get("acctSectResp") or envelope_data.get("AcctSectResp")
        data = account_data if isinstance(account_data, dict) else envelope_data
        message = (
            result.get("Message", "")
            if isinstance(result, dict)
            else ""
        ) or data.get("Message", "")
        wxid = self._extract_wechat08_wxid(data) or self._extract_wechat08_wxid(envelope_data)
        status = str(data.get("status") or envelope_data.get("status") or "").lower()
        logged_in = (
            status == "logged_in"
            or "登录成功" in message
            or "登陆成功" in message
        )

        if logged_in:
            self.wxid = wxid

        progress_fields = (
            "status",
            "scanned",
            "ready",
            "requires_face_verification",
            "requires_slider_verification",
            "verification_method",
            "challenge_id",
            "verification_path",
            "verification_url",
            "verification_qr_base64",
            "network_hint",
        )
        progress = {}
        for key in progress_fields:
            if key in data:
                progress[key] = data.get(key)
            elif key in envelope_data:
                progress[key] = envelope_data.get(key)

        state_data = {
            "loginState": 1 if logged_in else 0,
            "loginErrMsg": message,
            "wxid": wxid,
            **progress,
            "raw": result,
        }
        if not self._wechat08_ok(result):
            return self._normalize_wechat08_response(result, state_data)

        return {
            "Code": 200,
            "Success": True,
            "Message": message,
            "Data": state_data,
        }

    @staticmethod
    def is_long_link_ready(result):
        """Return whether a MAX LongLinkStatus response is message-ready."""
        if not isinstance(result, dict) or result.get("Code") != 200:
            return False
        data = result.get("Data", {})
        data = data if isinstance(data, dict) else {}
        status = data.get("status", {})
        status = status if isinstance(status, dict) else {}
        state = str(status.get("state", "")).lower()
        return (
            data.get("ready") is True
            and data.get("registered") is True
            and state in ("connected", "ready", "active")
        )

    def _post(self, path, data=None, params=None):
        url = self.base_url + path
        headers = {'Content-Type': 'application/json'}
        # 添加管理员密钥到查询参数
        if params is None:
            params = {}
        params['key'] = self.admin_key
        
        try:
            resp = requests.post(url, json=data, params=params, headers=headers, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            return result
        except Exception as e:
            raise Exception(f"请求 {url} 失败: {e}")

    def _get(self, path, params=None):
        url = self.base_url + path
        # 添加管理员密钥到查询参数
        if params is None:
            params = {}
        params['key'] = self.admin_key

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            return result
        except Exception as e:
            raise Exception(f"请求 {url} 失败: {e}")

    def _request_with_retry(self, method, url, max_retries=3, **kwargs):
        """带重试机制的请求方法"""
        import time

        for attempt in range(max_retries):
            try:
                if method.upper() == 'POST':
                    resp = requests.post(url, timeout=60, **kwargs)
                elif method.upper() == 'GET':
                    resp = requests.get(url, timeout=60, **kwargs)
                else:
                    raise Exception(f"不支持的HTTP方法: {method}")

                # MAX returns a useful JSON error envelope for 4xx responses.
                # Parse and return it so callers can react to Code/CodeValue
                # instead of turning an expected API state into a retry loop.
                result = resp.json()
                status_code = getattr(resp, "status_code", 200)
                if status_code >= 400 and not isinstance(result, dict):
                    resp.raise_for_status()
                return result

            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避：1s, 2s, 4s
                    print(f"请求失败，{wait_time}秒后重试 (第{attempt + 1}/{max_retries}次): {e}")
                    time.sleep(wait_time)
                else:
                    raise Exception(f"请求 {url} 失败: {e}")

    def _post_with_user_key(self, path, data=None, user_key=None, params=None):
        """使用普通用户密钥发送POST请求"""
        # 优先使用传入的user_key，其次使用配置文件中的user_key
        final_user_key = user_key or self.user_key
        if final_user_key is None:
            raise Exception("此接口需要普通用户密钥，请先使用管理接口生成授权码，或在配置文件中设置 wechatpadpro_user_key")

        if self._is_wechat08():
            return self._wechat08_dispatch_legacy(path, data=data, user_key=final_user_key)

        url = self.base_url + path
        headers = {'Content-Type': 'application/json'}
        # 添加用户密钥到查询参数
        if params is None:
            params = {}
        params['key'] = final_user_key

        return self._request_with_retry('POST', url, json=data, params=params, headers=headers)

    def _get_with_user_key(self, path, user_key=None, params=None):
        """使用普通用户密钥发送GET请求"""
        # 优先使用传入的user_key，其次使用配置文件中的user_key
        final_user_key = user_key or self.user_key
        if final_user_key is None:
            raise Exception("此接口需要普通用户密钥，请先使用管理接口生成授权码，或在配置文件中设置 wechatpadpro_user_key")

        if self._is_wechat08():
            return self._wechat08_dispatch_legacy(path, data=params, user_key=final_user_key)

        url = self.base_url + path
        # 添加用户密钥到查询参数
        if params is None:
            params = {}
        params['key'] = final_user_key

        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            return result
        except Exception as e:
            raise Exception(f"请求 {url} 失败: {e}")

    # ==================== 管理接口 ====================
    
    def delay_auth_key(self, key, days=30, expiry_date=""):
        """授权码延期
        
        Args:
            key: 授权码
            days: 延期天数，默认30天
            expiry_date: 过期日期（可选）
            
        Returns:
            延期结果
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Admin/DelayAuthKey',
                data={"authcode": key, "days": days},
                admin_token=self.admin_key,
            )
            return self._normalize_wechat08_response(result)

        data = {
            "Days": days,
            "ExpiryDate": expiry_date,
            "Key": key
        }
        return self._post('/admin/DelayAuthKey', data=data)
    
    def delete_auth_key(self, key, opt=0):
        """删除授权码
        
        Args:
            key: 授权码
            opt: 操作选项，默认0
            
        Returns:
            删除结果
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Admin/DeleteAuthKey',
                data={"authcode": key},
                admin_token=self.admin_key,
            )
            return self._normalize_wechat08_response(result)

        data = {
            "Key": key,
            "Opt": opt
        }
        return self._post('/admin/DeleteAuthKey', data=data)
    
    def gen_auth_key1(self, count=1, days=30, remark="dify-on-wechat-padpro"):
        """生成授权码(新设备)
        
        Args:
            count: 生成数量，默认1
            days: 有效天数，默认30天
            
        Returns:
            生成的授权码信息
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Admin/GenAuthKey',
                data={
                    "count": count,
                    "days": days,
                    "remark": remark,
                },
                admin_token=self.admin_key,
            )
            return self._normalize_wechat08_response(result)

        data = {
            "Count": count,
            "Days": days
        }
        return self._post('/admin/GenAuthKey1', data=data)
    
    def gen_auth_key2(self):
        """生成授权码(新设备) - GET方式
        
        Returns:
            生成的授权码信息
        """
        if self._is_wechat08():
            return self.gen_auth_key1()

        return self._get('/admin/GenAuthKey2')

    # ==================== 登录接口 ====================

    def a16_login(self, data, user_key=None):
        """数据登录

        Args:
            data: 登录数据
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            登录结果
        """
        return self._post_with_user_key('/login/A16Login', data=data, user_key=user_key)

    def check_can_set_alias(self, user_key=None):
        """检测微信登录环境

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            环境检测结果
        """
        return self._get_with_user_key('/login/CheckCanSetAlias', user_key=user_key)

    def check_login_status(self, user_key=None):
        """检测扫码状态

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            扫码状态
        """
        if self._is_wechat08():
            if not self.last_qr_uuid:
                return {
                    "Code": 200,
                    "Success": True,
                    "Message": "No QR login has been requested",
                    "Data": {
                        "loginState": 0,
                        "loginErrMsg": "No QR login has been requested",
                        "wxid": self.wxid or ""
                    }
                }

            extra_headers = {}
            access_token = user_key or self.user_key
            if self.last_qr_check_token:
                extra_headers["X-QR-Check-Token"] = self.last_qr_check_token
                access_token = None
            result = self._wechat08_post(
                '/Login/CheckQR',
                params={"uuid": self.last_qr_uuid},
                authcode=access_token,
                extra_headers=extra_headers,
            )
            return self._wechat08_login_state(result)

        return self._get_with_user_key('/login/CheckLoginStatus', user_key=user_key)

    def device_login(self, user_key, data):
        """62账号密码登录

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            data: 登录数据

        Returns:
            登录结果
        """
        return self._post_with_user_key('/login/DeviceLogin', data=data, user_key=user_key)

    def get_62_data(self, user_key):
        """提取62数据

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            62数据
        """
        return self._get_with_user_key('/login/Get62Data', user_key=user_key)

    def get_iwx_connect(self, user_key):
        """打印链接数量

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            链接数量信息
        """
        return self._get_with_user_key('/login/GetIWXConnect', user_key=user_key)

    def get_init_status(self, user_key):
        """初始化状态

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            初始化状态信息
        """
        return self._get_with_user_key('/login/GetInItStatus', user_key=user_key)

    def get_login_qr_code_new(self, user_key, check=False, proxy=""):
        """获取登录二维码(异地IP用代理)

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            check: 是否检查，默认False
            proxy: 代理地址，默认空

        Returns:
            二维码信息
        """
        if self._is_wechat08():
            data = {
                "DeviceName": self.device_name,
                "Proxy": {
                    "ProxyIp": proxy or "",
                    "ProxyPassword": "",
                    "ProxyUser": "",
                },
                "oversea": False,
            }
            result = self._wechat08_post('/Login/GetQR', data=data, authcode=user_key)
            response_data = result.get("Data", {}) if isinstance(result, dict) else {}
            if not isinstance(response_data, dict):
                response_data = {}

            qr_url = (
                response_data.get("QrCodeUrl")
                or response_data.get("qrUrl")
                or response_data.get("QrUrl")
                or response_data.get("url")
            )
            qr_uuid = response_data.get("Uuid") or response_data.get("uuid")
            qr_check_token = response_data.get("check_token") or response_data.get("CheckToken")
            self.last_qr_uuid = qr_uuid
            self.last_qr_check_token = qr_check_token
            self.device_id = None

            if not self._wechat08_ok(result):
                return self._normalize_wechat08_response(result, response_data)

            return {
                "Code": 200,
                "Success": True,
                "Message": result.get("Message", "") if isinstance(result, dict) else "",
                "Data": {
                    "QrCodeUrl": qr_url,
                    "QrUrl": response_data.get("QrUrl") or qr_url,
                    "QrBase64": response_data.get("QrBase64") or response_data.get("Data62"),
                    "Uuid": qr_uuid,
                    "CheckToken": qr_check_token,
                    "raw": result
                }
            }

        data = {
            "Check": check,
            "Proxy": proxy
        }
        return self._post_with_user_key('/login/GetLoginQrCodeNew', data=data, user_key=user_key)

    def get_login_qr_code_new_x(self, user_key, check=False, proxy=""):
        """获取登录二维码(绕过验证码)

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            check: 是否检查，默认False
            proxy: 代理地址，默认空

        Returns:
            二维码信息
        """
        if self._is_wechat08():
            return self.get_login_qr_code_new(user_key, check=check, proxy=proxy)

        data = {
            "Check": check,
            "Proxy": proxy
        }
        return self._post_with_user_key('/login/GetLoginQrCodeNewX', data=data, user_key=user_key)

    def get_login_status(self, user_key=None):
        """获取在线状态

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            在线状态信息
        """
        if self._is_wechat08():
            final_user_key = user_key or self.user_key
            result = self._wechat08_post('/Login/GetCacheInfo', authcode=final_user_key)
            if self._wechat08_ok(result):
                data = result.get("Data", {}) if isinstance(result, dict) else {}
                wxid = self._extract_wechat08_wxid(data) or self.wxid or ""
                self.wxid = wxid

                # GetCacheInfo only proves that reusable session data exists.  A
                # stale cache can still report login_active=true while the real
                # WeChat long link is disconnected, in which case WebSocket is
                # connected but never receives business messages.
                long_link_result = self.get_iwx_connect(final_user_key)
                long_link_data = (
                    long_link_result.get("Data", {})
                    if isinstance(long_link_result, dict)
                    else {}
                )
                long_link_data = long_link_data if isinstance(long_link_data, dict) else {}
                long_link_status = long_link_data.get("status", {})
                long_link_status = long_link_status if isinstance(long_link_status, dict) else {}
                long_link_state = str(long_link_status.get("state", "")).lower()
                if not self.is_long_link_ready(long_link_result):
                    detail = (
                        "缓存存在，但微信长连接未就绪: "
                        f"state={long_link_state or 'unknown'}, "
                        f"ready={long_link_data.get('ready')}, "
                        f"registered={long_link_data.get('registered')}"
                    )
                    return {
                        "Code": 200,
                        "Success": True,
                        "Message": detail,
                        "Data": {
                            "loginState": 0,
                            "loginErrMsg": detail,
                            "wxid": wxid,
                            "raw": {
                                "cache": result,
                                "longLink": long_link_result,
                            },
                        },
                    }

                return {
                    "Code": 200,
                    "Success": True,
                    "Message": result.get("Message", "") if isinstance(result, dict) else "",
                    "Data": {
                        "loginState": 1,
                        "loginErrMsg": result.get("Message", "") if isinstance(result, dict) else "",
                        "wxid": wxid,
                        "raw": result
                    }
                }

            message = result.get("Message", "Wechat 08 cache not found") if isinstance(result, dict) else "Wechat 08 cache not found"
            return self._normalize_wechat08_response(
                result,
                {
                    "loginState": 0,
                    "loginErrMsg": message,
                    "wxid": self.wxid or "",
                    "raw": result,
                },
            )

        return self._get_with_user_key('/login/GetLoginStatus', user_key=user_key)

    def logout(self, user_key=None):
        """退出登录

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            退出结果
        """
        return self._get_with_user_key('/login/LogOut', user_key=user_key)

    def login_new(self, user_key, data):
        """62LoginNew新疆号登录

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            data: 登录数据

        Returns:
            登录结果
        """
        return self._post_with_user_key('/login/LoginNew', data=data, user_key=user_key)

    def phone_device_login(self, user_key, data):
        """辅助新手机登录

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            data: 登录数据

        Returns:
            登录结果
        """
        return self._post_with_user_key('/login/PhoneDeviceLogin', data=data, user_key=user_key)

    def show_qr_code(self, user_key):
        """HTML展示登录二维码

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）

        Returns:
            二维码HTML
        """
        return self._get_with_user_key('/login/ShowQrCode', user_key=user_key)

    def sms_login(self, user_key, data):
        """短信登录

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            data: 登录数据

        Returns:
            登录结果
        """
        return self._post_with_user_key('/login/SmsLogin', data=data, user_key=user_key)

    def wake_up_login(self, user_key, check=False, proxy=""):
        """唤醒登录(只限扫码登录)

        Args:
            user_key: 普通用户密钥（从管理接口生成，必需）
            check: 是否检查，默认False
            proxy: 代理地址，默认空

        Returns:
            唤醒结果
        """
        if self._is_wechat08():
            result = self._wechat08_post('/Login/Awaken', authcode=user_key)
            return self._normalize_wechat08_response(result)

        data = {
            "Check": check,
            "Proxy": proxy
        }
        return self._post_with_user_key('/login/WakeUpLogin', data=data, user_key=user_key)

    def initialize_login(self, user_key=None, max_synckey="", current_synckey=""):
        """Initialize a WeChatPadProMAX session after QR login or cache restore."""
        if self._is_wechat08():
            params = {}
            if max_synckey:
                params["MaxSynckey"] = max_synckey
            if current_synckey:
                params["CurrentSynckey"] = current_synckey
            result = self._wechat08_post(
                '/Login/Newinit',
                params=params,
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_response(result)

        return self._post_with_user_key('/login/Newinit', data={}, user_key=user_key)

    def start_auto_heartbeat(self, user_key=None):
        """Enable WeChatPadProMAX session heartbeat and automatic re-auth."""
        if not self._is_wechat08():
            return self._wechat08_unsupported(
                "/Login/AutoHeartBeat",
                "旧版 PadPro 不使用 MAX 自动心跳接口",
            )
        result = self._wechat08_post(
            "/Login/AutoHeartBeat",
            authcode=user_key or self.user_key,
        )
        return self._normalize_wechat08_response(result)

    def start_auto_sync(self, user_key=None, target_url=""):
        """Enable WeChatPadProMAX message synchronization."""
        if not self._is_wechat08():
            return self._wechat08_unsupported(
                "/Msg/StartAutoSync",
                "旧版 PadPro 不使用 MAX 自动同步接口",
            )
        result = self._wechat08_post(
            "/Msg/StartAutoSync",
            data={"TargetURL": target_url},
            authcode=user_key or self.user_key,
        )
        return self._normalize_wechat08_response(result)

    # ==================== 同步消息接口 ====================

    def get_sync_msg_ws(self, user_key=None):
        """同步消息，WebSocket协议

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            WebSocket连接信息或消息数据

        Note:
            这是WebSocket接口，通常需要建立持久连接来接收实时消息
        """
        return self._get_with_user_key('/ws/GetSyncMsg', user_key=user_key)

    def get_websocket_url(self, user_key=None):
        """获取WebSocket连接URL

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            WebSocket连接URL
        """
        # 优先使用传入的user_key，其次使用配置文件中的user_key
        final_user_key = user_key or self.user_key
        if final_user_key is None:
            raise Exception("此接口需要普通用户密钥，请先使用管理接口生成授权码，或在配置文件中设置 wechatpadpro_user_key")

        # 将HTTP URL转换为WebSocket URL
        base_url = self.base_url[:-4] if self.base_url.endswith("/api") else self.base_url
        if self._is_wechat08():
            configured_url = os.environ.get("WECHATPAD_WEBSOCKET_URL", "").rstrip("/")
            if configured_url:
                ws_base_url = configured_url
            else:
                parsed = urlparse(base_url)
                ws_scheme = "wss" if parsed.scheme == "https" else "ws"
                ws_port = int(os.environ.get("WECHATPAD_WEBSOCKET_PORT", "8089"))
                host = parsed.hostname or "127.0.0.1"
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                ws_base_url = f"{ws_scheme}://{host}:{ws_port}"
            return f"{ws_base_url}/ws/sync?{urlencode({'authcode': final_user_key})}"
        ws_base_url = base_url.replace('http://', 'ws://').replace('https://', 'wss://')
        return f"{ws_base_url}/ws/GetSyncMsg?key={final_user_key}"

    # ==================== 消息接口 ====================

    def upload_file(self, file_base64, user_key=None):
        """Upload a base64 encoded file through the MAX tools endpoint."""
        if not self._is_wechat08():
            return self._wechat08_unsupported(
                "/Tools/UploadFile",
                "该便捷方法仅用于 WeChatPadProMAX",
            )
        result = self._wechat08_post(
            "/Tools/UploadFile",
            data={"base64": file_base64},
            authcode=user_key or self.user_key,
        )
        return self._normalize_wechat08_response(result)

    def send_cdn_file(self, content, to_wxid, user_key=None):
        """Send file metadata returned by ``upload_file`` to one recipient."""
        if not self._is_wechat08():
            return self._wechat08_unsupported(
                "/Msg/SendCDNFile",
                "该便捷方法仅用于 WeChatPadProMAX",
            )
        result = self._wechat08_post(
            "/Msg/SendCDNFile",
            data={"Content": content, "ToWxid": to_wxid},
            authcode=user_key or self.user_key,
        )
        return self._normalize_wechat08_response(result)

    def add_message_mgr(self, msg_item, user_key=None):
        """添加要发送的文本消息进入管理器

        Args:
            msg_item: 消息项列表，格式：
                [
                    {
                        "AtWxIDList": ["string"],
                        "ImageContent": "",
                        "MsgType": 0,
                        "TextContent": "",
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            添加结果
        """
        data = {
            "MsgItem": msg_item
        }
        return self._post_with_user_key('/message/AddMessageMgr', data=data, user_key=user_key)

    def cdn_upload_video(self, thumb_data, to_user_name, video_data, user_key=None):
        """上传视频

        Args:
            thumb_data: 缩略图数据
            to_user_name: 接收者用户名
            video_data: 视频数据（数组格式）
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            上传结果
        """
        if self._is_wechat08():
            return self.send_video(
                video_data=video_data,
                image_data=thumb_data,
                play_length=0,
                to_user_name=to_user_name,
                user_key=user_key,
            )

        data = {
            "ThumbData": thumb_data,
            "ToUserName": to_user_name,
            "VideoData": video_data
        }
        return self._post_with_user_key('/message/CdnUploadVideo', data=data, user_key=user_key)

    def send_video(self, video_data, image_data, play_length, to_user_name, user_key=None):
        """Send a base64 video through the WeChatPadProMAX single-step endpoint."""
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Msg/SendVideo',
                data={
                    "Base64": video_data,
                    "ImageBase64": image_data,
                    "PlayLength": int(play_length or 0),
                    "ToWxid": to_user_name,
                },
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_response(result)

        return self.cdn_upload_video(image_data, to_user_name, video_data, user_key=user_key)

    def forward_emoji(self, emoji_list, user_key=None):
        """转发表情，包含动图

        Args:
            emoji_list: 表情列表，格式：
                [
                    {
                        "EmojiMd5": "",
                        "EmojiSize": 0,
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            转发结果
        """
        data = {
            "EmojiList": emoji_list
        }
        return self._post_with_user_key('/message/ForwardEmoji', data=data, user_key=user_key)

    def forward_image_message(self, forward_image_list, forward_video_list, user_key=None):
        """转发图片

        Args:
            forward_image_list: 转发图片列表，格式：
                [
                    {
                        "AesKey": "",
                        "CdnMidImgSize": 0,
                        "CdnMidImgUrl": "",
                        "CdnThumbImgSize": 0,
                        "ToUserName": ""
                    }
                ]
            forward_video_list: 转发视频列表，格式：
                [
                    {
                        "AesKey": "",
                        "CdnThumbLength": 0,
                        "CdnVideoUrl": "",
                        "Length": 0,
                        "PlayLength": 0,
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            转发结果
        """
        data = {
            "ForwardImageList": forward_image_list,
            "ForwardVideoList": forward_video_list
        }
        return self._post_with_user_key('/message/ForwardImageMessage', data=data, user_key=user_key)

    def forward_video_message(self, forward_image_list, forward_video_list, user_key=None):
        """转发视频

        Args:
            forward_image_list: 转发图片列表，格式：
                [
                    {
                        "AesKey": "",
                        "CdnMidImgSize": 0,
                        "CdnMidImgUrl": "",
                        "CdnThumbImgSize": 0,
                        "ToUserName": ""
                    }
                ]
            forward_video_list: 转发视频列表，格式：
                [
                    {
                        "AesKey": "",
                        "CdnThumbLength": 0,
                        "CdnVideoUrl": "",
                        "Length": 0,
                        "PlayLength": 0,
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            转发结果
        """
        data = {
            "ForwardImageList": forward_image_list,
            "ForwardVideoList": forward_video_list
        }
        return self._post_with_user_key('/message/ForwardVideoMessage', data=data, user_key=user_key)

    def get_msg_big_img(self, compress_type, from_user_name, msg_id, section, to_user_name, total_len, user_key=None):
        """获取图片(高清图片下载)

        Args:
            compress_type: 压缩类型
            from_user_name: 发送者用户名
            msg_id: 消息ID
            section: 分段信息，格式：
                {
                    "DataLen": 61440,
                    "StartPos": 0
                }
            to_user_name: 接收者用户名
            total_len: 总长度
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            图片数据
        """
        data = {
            "CompressType": compress_type,
            "FromUserName": from_user_name,
            "MsgId": msg_id,
            "Section": section,
            "ToUserName": to_user_name,
            "TotalLen": total_len
        }
        return self._post_with_user_key('/message/GetMsgBigImg', data=data, user_key=user_key)

    def get_msg_video(self, compress_type, from_user_name, msg_id, section, to_user_name, total_len, user_key=None):
        """获取视频(视频数据下载)

        Args:
            compress_type: 压缩类型
            from_user_name: 发送者用户名
            msg_id: 消息ID
            section: 分段信息，格式：
                {
                    "DataLen": 61440,
                    "StartPos": 0
                }
            to_user_name: 接收者用户名
            total_len: 总长度
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            视频数据
        """
        data = {
            "CompressType": compress_type,
            "FromUserName": from_user_name,
            "MsgId": msg_id,
            "Section": section,
            "ToUserName": to_user_name,
            "TotalLen": total_len
        }
        return self._post_with_user_key('/message/GetMsgVideo', data=data, user_key=user_key)

    def get_msg_voice(self, bufid, length, new_msg_id, to_user_name, user_key=None):
        """下载语音消息

        Args:
            bufid: 缓冲区ID
            length: 长度
            new_msg_id: 新消息ID
            to_user_name: 接收者用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            语音数据
        """
        if self._is_wechat08():
            try:
                msg_id = int(new_msg_id)
            except (TypeError, ValueError):
                msg_id = 0
            result = self._wechat08_post(
                '/Tools/DownloadVoice',
                data={
                    "bufid": str(bufid),
                    "fromUserName": to_user_name,
                    "length": int(length),
                    "msgId": msg_id,
                },
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_legacy_response('/message/GetMsgVoice', result)

        data = {
            "Bufid": bufid,
            "Length": length,
            "NewMsgId": new_msg_id,
            "ToUserName": to_user_name
        }
        return self._post_with_user_key('/message/GetMsgVoice', data=data, user_key=user_key)

    def group_mass_msg_image(self, image_base64, to_user_name, user_key=None):
        """群发图片

        Args:
            image_base64: 图片Base64数据
            to_user_name: 接收者用户名列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群发结果
        """
        data = {
            "ImageBase64": image_base64,
            "ToUserName": to_user_name
        }
        return self._post_with_user_key('/message/GroupMassMsgImage', data=data, user_key=user_key)

    def group_mass_msg_text(self, content, to_user_name, user_key=None):
        """群发文本消息

        Args:
            content: 消息内容
            to_user_name: 接收者用户名列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群发结果
        """
        data = {
            "Content": content,
            "ToUserName": to_user_name
        }
        return self._post_with_user_key('/message/GroupMassMsgText', data=data, user_key=user_key)

    def http_sync_msg(self, count, user_key=None):
        """同步消息, HTTP-轮询方式

        Args:
            count: 消息数量
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            同步的消息列表
        """
        data = {
            "Count": count
        }
        return self._post_with_user_key('/message/HttpSyncMsg', data=data, user_key=user_key)

    def new_sync_history_message(self, user_key=None):
        """同步历史消息

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            历史消息
        """
        return self._post_with_user_key('/message/NewSyncHistoryMessage', data={}, user_key=user_key)

    def revoke_msg(self, client_msg_id, create_time, new_msg_id, to_user_name, user_key=None):
        """撤销消息

        Args:
            client_msg_id: 客户端消息ID
            create_time: 创建时间
            new_msg_id: 新消息ID
            to_user_name: 接收者用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            撤销结果
        """
        data = {
            "ClientMsgId": client_msg_id,
            "CreateTime": create_time,
            "NewMsgId": new_msg_id,
            "ToUserName": to_user_name
        }
        if self._is_wechat08():
            result = self._wechat08_post('/Msg/Revoke', data=data, authcode=user_key or self.user_key)
            return self._normalize_wechat08_response(result)
        return self._post_with_user_key('/message/RevokeMsg', data=data, user_key=user_key)

    def revoke_msg_new(self, client_msg_id, create_time, new_msg_id, to_user_name, user_key=None):
        """撤回消息（New）

        Args:
            client_msg_id: 客户端消息ID
            create_time: 创建时间
            new_msg_id: 新消息ID
            to_user_name: 接收者用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            撤回结果
        """
        data = {
            "ClientMsgId": client_msg_id,
            "CreateTime": create_time,
            "NewMsgId": new_msg_id,
            "ToUserName": to_user_name
        }
        return self._post_with_user_key('/message/RevokeMsgNew', data=data, user_key=user_key)

    def send_app_message(self, app_list, user_key=None):
        """发送App消息

        Args:
            app_list: App消息列表，格式：
                [
                    {
                        "ContentType": 0,
                        "ContentXML": "",
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        if self._is_wechat08():
            payloads = []
            for item in app_list:
                payloads.append({
                    "ToWxid": item.get("ToWxid") or item.get("ToUserName", ""),
                    "Type": item.get("Type") or item.get("ContentType") or 5,
                    "Xml": item.get("Xml") or item.get("ContentXML", ""),
                })
            return self._wechat08_post_many('/Msg/ShareLink', payloads, user_key=user_key)

        data = {
            "AppList": app_list
        }
        return self._post_with_user_key('/message/SendAppMessage', data=data, user_key=user_key)

    def send_cdn_download(self, aes_key, file_type, file_url, user_key=None):
        """下载请求

        Args:
            aes_key: AES密钥
            file_type: 文件类型
            file_url: 文件URL
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            下载结果
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Tools/CdnDownloadImage',
                data={
                    "file_aes_key": aes_key,
                    "file_no": file_url,
                },
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_legacy_response('/message/SendCdnDownload', result)

        data = {
            "AesKey": aes_key,
            "FileType": file_type,
            "FileURL": file_url
        }
        return self._post_with_user_key('/message/SendCdnDownload', data=data, user_key=user_key)

    def send_emoji_message(self, emoji_list, user_key=None):
        """发送表情

        Args:
            emoji_list: 表情列表，格式：
                [
                    {
                        "EmojiMd5": "",
                        "EmojiSize": 0,
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        if self._is_wechat08():
            payloads = []
            for item in emoji_list:
                payloads.append({
                    "Md5": item.get("Md5") or item.get("EmojiMd5", ""),
                    "ToWxid": item.get("ToWxid") or item.get("ToUserName", ""),
                    "TotalLen": item.get("TotalLen") or item.get("EmojiSize", 0),
                })
            return self._wechat08_post_many('/Msg/SendEmoji', payloads, user_key=user_key)

        data = {
            "EmojiList": emoji_list
        }
        return self._post_with_user_key('/message/SendEmojiMessage', data=data, user_key=user_key)

    def send_image_message(self, msg_item, user_key=None):
        """发送图片消息

        Args:
            msg_item: 消息项列表，格式：
                [
                    {
                        "AtWxIDList": ["string"],
                        "ImageContent": "",
                        "MsgType": 0,
                        "TextContent": "",
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        data = {
            "MsgItem": msg_item
        }
        return self._post_with_user_key('/message/SendImageMessage', data=data, user_key=user_key)

    def send_image_new_message(self, msg_item, user_key=None):
        """发送图片消息（New）

        Args:
            msg_item: 消息项列表，格式：
                [
                    {
                        "AtWxIDList": ["string"],
                        "ImageContent": "",
                        "MsgType": 0,
                        "TextContent": "",
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        if self._is_wechat08():
            payloads = []
            for item in msg_item:
                payloads.append({
                    "Base64": item.get("Base64") or item.get("ImageContent", ""),
                    "ToWxid": item.get("ToWxid") or item.get("ToUserName", ""),
                })
            return self._wechat08_post_many('/Msg/UploadImg', payloads, user_key=user_key)

        data = {
            "MsgItem": msg_item
        }
        return self._post_with_user_key('/message/SendImageNewMessage', data=data, user_key=user_key)

    def send_text_message(self, msg_item, user_key=None):
        """发送文本消息

        Args:
            msg_item: 消息项列表，格式：
                [
                    {
                        "AtWxIDList": ["string"],
                        "ImageContent": "",
                        "MsgType": 0,
                        "TextContent": "",
                        "ToUserName": ""
                    }
                ]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        if self._is_wechat08():
            payloads = []
            for item in msg_item:
                at_list = item.get("At") or item.get("AtWxIDList") or []
                if isinstance(at_list, (list, tuple)):
                    at_list = ",".join(str(wxid) for wxid in at_list)
                payloads.append({
                    "At": at_list,
                    "Content": item.get("Content") or item.get("TextContent", ""),
                    "ToWxid": item.get("ToWxid") or item.get("ToUserName", ""),
                    "Type": item.get("Type") if "Type" in item else item.get("MsgType", 0),
                })
            return self._wechat08_post_many('/Msg/SendTxt', payloads, user_key=user_key)

        data = {
            "MsgItem": msg_item
        }
        return self._post_with_user_key('/message/SendTextMessage', data=data, user_key=user_key)

    def quote_message(self, content, reply_context, user_key=None):
        """Reply to a MAX message using its canonical ``reply_context``."""
        if not self._is_wechat08():
            return self._wechat08_unsupported('/Msg/Quote', "该引用接口仅用于 WeChatPadProMAX")
        if not isinstance(reply_context, dict) or not reply_context:
            return {
                "Code": 400,
                "Success": False,
                "Message": "reply_context 不能为空",
                "Data": None,
            }

        allowed_fields = {
            "chat_user_id",
            "conversation_id",
            "from_user_id",
            "msg_id",
            "msg_type",
            "new_msg_id",
            "quote_content",
            "sequence",
            "svr_id",
            "to_wxid",
        }
        context = {
            key: value
            for key, value in reply_context.items()
            if key in allowed_fields and value is not None
        }
        for key in ("svr_id", "new_msg_id"):
            if key in context:
                context[key] = str(context[key])
        for key in ("msg_id", "msg_type", "sequence"):
            if key in context:
                try:
                    context[key] = int(context[key])
                except (TypeError, ValueError):
                    return {
                        "Code": 400,
                        "Success": False,
                        "Message": f"reply_context.{key} 必须是整数",
                        "Data": None,
                    }

        result = self._wechat08_post(
            '/Msg/Quote',
            data={"content": str(content or ""), "reply_context": context},
            authcode=user_key or self.user_key,
        )
        return self._normalize_wechat08_response(result)

    def send_voice(self, to_user_name, voice_data, voice_format, voice_second, user_key=None):
        """发送语音

        Args:
            to_user_name: 接收者用户名
            voice_data: 语音数据
            voice_format: 语音格式
            voice_second: 语音时长（秒）
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            发送结果
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Msg/SendVoice',
                data={
                    "Base64": voice_data,
                    "ToWxid": to_user_name,
                    "Type": int(voice_format),
                    "VoiceTime": int(voice_second),
                },
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_response(result)

        data = {
            "ToUserName": to_user_name,
            "VoiceData": voice_data,
            "VoiceFormat": voice_format,
            "VoiceSecond": voice_second  # 修正API参数名
        }
        return self._post_with_user_key('/message/SendVoice', data=data, user_key=user_key)

    def share_card_message(self, card_alias, card_flag, card_nick_name, card_wx_id, to_user_name, user_key=None):
        """分享名片消息

        Args:
            card_alias: 名片别名
            card_flag: 名片标志
            card_nick_name: 名片昵称
            card_wx_id: 名片微信ID
            to_user_name: 接收者用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            分享结果
        """
        data = {
            "CardAlias": card_alias,
            "CardFlag": card_flag,
            "CardNickName": card_nick_name,
            "CardWxId": card_wx_id,
            "ToUserName": to_user_name
        }
        if self._is_wechat08():
            result = self._wechat08_post(
                '/Msg/ShareCard',
                data={
                    "CardAlias": card_alias,
                    "CardNickName": card_nick_name,
                    "CardWxId": card_wx_id,
                    "ToWxid": to_user_name,
                },
                authcode=user_key or self.user_key,
            )
            return self._normalize_wechat08_response(result)
        return self._post_with_user_key('/message/ShareCardMessage', data=data, user_key=user_key)

    # ==================== 朋友接口 ====================

    def agree_add(self, chat_room_user_name, op_code, scene, v3, v4, verify_content, user_key=None):
        """同意好友请求

        Args:
            chat_room_user_name: 聊天室用户名
            op_code: 操作码
            scene: 场景
            v3: V3参数
            v4: V4参数
            verify_content: 验证内容
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            同意结果
        """
        data = {
            "ChatRoomUserName": chat_room_user_name,
            "OpCode": op_code,
            "Scene": scene,
            "V3": v3,
            "V4": v4,
            "VerifyContent": verify_content
        }
        return self._post_with_user_key('/friend/AgreeAdd', data=data, user_key=user_key)

    def del_contact(self, del_user_name, user_key=None):
        """删除好友

        Args:
            del_user_name: 要删除的用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            删除结果
        """
        data = {
            "DelUserName": del_user_name
        }
        return self._post_with_user_key('/friend/DelContact', data=data, user_key=user_key)

    def get_contact_details_list(self, room_wx_id_list, user_names, user_key=None):
        """获取联系人详情

        Args:
            room_wx_id_list: 群聊微信ID列表，格式：["string"]
            user_names: 用户名列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            联系人详情
        """
        if self._is_wechat08():
            contacts = []
            raw_results = []
            for user_name in list(room_wx_id_list or []) + list(user_names or []):
                result = self._wechat08_post(
                    '/Friend/GetContractDetail',
                    data={"userName": user_name},
                    authcode=user_key or self.user_key,
                )
                normalized = self._normalize_wechat08_response(result)
                if normalized.get("Code") != 200:
                    return normalized

                raw_results.append(result)
                response_data = result.get("Data") if isinstance(result, dict) else None
                if isinstance(response_data, list):
                    contacts.extend(self._normalize_wechat08_contact(item) for item in response_data)
                elif isinstance(response_data, dict):
                    contact_list = response_data.get("contactList") or response_data.get("ContactList")
                    if isinstance(contact_list, list):
                        contacts.extend(self._normalize_wechat08_contact(item) for item in contact_list)
                    else:
                        contacts.append(self._normalize_wechat08_contact(response_data))

            return {
                "Code": 200,
                "Success": True,
                "Message": "success",
                "Data": {"contactList": contacts},
                "raw": raw_results,
            }

        data = {
            "RoomWxIDList": room_wx_id_list,
            "UserNames": user_names
        }
        return self._post_with_user_key('/friend/GetContactDetailsList', data=data, user_key=user_key)

    def get_contact_list(self, current_chat_room_contact_seq, current_wxcontact_seq, user_key=None):
        """获取全部联系人

        Args:
            current_chat_room_contact_seq: 当前聊天室联系人序列
            current_wxcontact_seq: 当前微信联系人序列
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            联系人列表
        """
        data = {
            "CurrentChatRoomContactSeq": current_chat_room_contact_seq,
            "CurrentWxcontactSeq": current_wxcontact_seq
        }
        return self._post_with_user_key('/friend/GetContactList', data=data, user_key=user_key)

    def get_friend_list(self, user_key=None):
        """获取好友列表

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            好友列表
        """
        return self._get_with_user_key('/friend/GetFriendList', user_key=user_key)

    def get_friend_relation(self, user_name, user_key=None):
        """获取好友关系

        Args:
            user_name: 用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            好友关系信息
        """
        data = {
            "UserName": user_name
        }
        return self._post_with_user_key('/friend/GetFriendRelation', data=data, user_key=user_key)

    def get_gh_list(self, user_key=None):
        """获取关注的公众号列表

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            公众号列表
        """
        return self._get_with_user_key('/friend/GetGHList', user_key=user_key)

    def get_m_friend(self, user_key=None):
        """获取手机通讯录好友

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            手机通讯录好友列表
        """
        return self._get_with_user_key('/friend/GetMFriend', user_key=user_key)

    def group_list(self, user_key=None):
        """获取保存的群聊列表

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群聊列表
        """
        return self._get_with_user_key('/friend/GroupList', user_key=user_key)

    def search_contact(self, from_scene, op_code, search_scene, user_name, user_key=None):
        """搜索联系人

        Args:
            from_scene: 来源场景
            op_code: 操作码
            search_scene: 搜索场景
            user_name: 用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            搜索结果
        """
        data = {
            "FromScene": from_scene,
            "OpCode": op_code,
            "SearchScene": search_scene,
            "UserName": user_name
        }
        return self._post_with_user_key('/friend/SearchContact', data=data, user_key=user_key)

    def upload_m_contact(self, mobile, mobile_list, user_key=None):
        """上传手机通讯录好友

        Args:
            mobile: 手机号
            mobile_list: 手机号列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            上传结果
        """
        data = {
            "Mobile": mobile,
            "MobileList": mobile_list
        }
        return self._post_with_user_key('/friend/UploadMContact', data=data, user_key=user_key)

    def verify_user(self, chat_room_user_name, op_code, scene, v3, v4, verify_content, user_key=None):
        """验证好友/添加好友

        Args:
            chat_room_user_name: 聊天室用户名
            op_code: 操作码
            scene: 场景
            v3: V3参数
            v4: V4参数
            verify_content: 验证内容
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            验证结果
        """
        data = {
            "ChatRoomUserName": chat_room_user_name,
            "OpCode": op_code,
            "Scene": scene,
            "V3": v3,
            "V4": v4,
            "VerifyContent": verify_content
        }
        return self._post_with_user_key('/friend/VerifyUser', data=data, user_key=user_key)

    # ==================== 用户接口 ====================

    def change_pwd(self, new_pass, old_pass, op_code, user_key=None):
        """更改密码

        Args:
            new_pass: 新密码
            old_pass: 旧密码
            op_code: 操作码
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            更改结果
        """
        data = {
            "NewPass": new_pass,
            "OldPass,": old_pass,  # 注意：API文档中有逗号，保持原样
            "OpCode": op_code
        }
        return self._post_with_user_key('/user/ChangePwd', data=data, user_key=user_key)

    def get_my_qr_code(self, recover, style, user_key=None):
        """获取我的二维码

        Args:
            recover: 是否恢复
            style: 样式
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            二维码信息
        """
        data = {
            "Recover": recover,
            "Style": style
        }
        return self._post_with_user_key('/user/GetMyQrCode', data=data, user_key=user_key)

    def get_profile(self, user_key=None):
        """获取个人资料信息

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            个人资料信息
        """
        if self._is_wechat08():
            result = self._wechat08_post(
                '/User/GetContractProfile',
                authcode=user_key or self.user_key,
            )
            if not self._wechat08_ok(result):
                return self._normalize_wechat08_response(result)

            data = result.get("Data", {}) if isinstance(result, dict) else {}
            data = dict(data) if isinstance(data, dict) else {"raw": data}
            wxid = self._extract_wechat08_wxid(data)
            if wxid:
                self.wxid = wxid
                user_info = data.setdefault("userInfo", {})
                if isinstance(user_info, dict):
                    user_info.setdefault("userName", {"str": wxid})
            return self._normalize_wechat08_response(result, data)

        return self._get_with_user_key('/user/GetProfile', user_key=user_key)

    def modify_remark(self, remark_name, user_name, user_key=None):
        """修改备注

        Args:
            remark_name: 备注名称
            user_name: 用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "RemarkName": remark_name,
            "UserName": user_name
        }
        return self._post_with_user_key('/user/ModifyRemark', data=data, user_key=user_key)

    def modify_user_info(self, city, country, init_flag, nick_name, province, sex, signature, user_key=None):
        """修改资料

        Args:
            city: 城市
            country: 国家
            init_flag: 初始化标志
            nick_name: 昵称
            province: 省份
            sex: 性别
            signature: 签名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "City": city,
            "Country": country,
            "InitFlag": init_flag,
            "NickName": nick_name,
            "Province": province,
            "Sex": sex,
            "Signature": signature
        }
        return self._post_with_user_key('/user/ModifyUserInfo', data=data, user_key=user_key)

    def set_function_switch(self, function, value, user_key=None):
        """设置添加我的方式

        Args:
            function: 功能
            value: 值
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "Function": function,
            "Value": value
        }
        return self._post_with_user_key('/user/SetFunctionSwitch', data=data, user_key=user_key)

    def set_nick_name(self, scene, val, user_key=None):
        """设置昵称

        Args:
            scene: 场景
            val: 值
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "Scene": scene,
            "Val": val
        }
        return self._post_with_user_key('/user/SetNickName', data=data, user_key=user_key)

    def set_proxy(self, check, proxy, user_key=None):
        """修改Socks5代理

        Args:
            check: 是否检查
            proxy: 代理地址
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "Check": check,
            "Proxy": proxy
        }
        return self._post_with_user_key('/user/SetProxy', data=data, user_key=user_key)

    def set_send_pat(self, value, user_key=None):
        """设置拍一拍名称

        Args:
            value: 拍一拍名称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "Value": value
        }
        return self._post_with_user_key('/user/SetSendPat', data=data, user_key=user_key)

    def set_sex_dq(self, city, country, province, sex, user_key=None):
        """修改性别

        Args:
            city: 城市
            country: 国家
            province: 省份
            sex: 性别
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "City": city,
            "Country": country,
            "Province": province,
            "Sex": sex
        }
        return self._post_with_user_key('/user/SetSexDq', data=data, user_key=user_key)

    def set_signature(self, scene, val, user_key=None):
        """修改签名

        Args:
            scene: 场景
            val: 签名值
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "Scene": scene,
            "Val": val
        }
        return self._post_with_user_key('/user/SetSignature', data=data, user_key=user_key)

    def set_wechat(self, alisa, user_key=None):
        """设置微信号

        Args:
            alisa: 微信号别名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "Alisa": alisa
        }
        return self._post_with_user_key('/user/SetWechat', data=data, user_key=user_key)

    def update_auto_pass(self, switch_type, user_key=None):
        """修改加好友需要验证属性

        Args:
            switch_type: 开关类型
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "SwitchType": switch_type
        }
        return self._post_with_user_key('/user/UpdateAutoPass', data=data, user_key=user_key)

    def update_nick_name(self, scene, val, user_key=None):
        """修改名称

        Args:
            scene: 场景
            val: 名称值
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            修改结果
        """
        data = {
            "Scene": scene,
            "Val": val
        }
        return self._post_with_user_key('/user/UpdateNickName', data=data, user_key=user_key)

    def upload_head_image(self, base64, user_key=None):
        """上传头像

        Args:
            base64: 头像的Base64编码
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            上传结果
        """
        data = {
            "Base64": base64
        }
        return self._post_with_user_key('/user/UploadHeadImage', data=data, user_key=user_key)

    # ==================== 群管理接口 ====================

    def add_chat_room_members(self, chat_room_name, user_list, user_key=None):
        """添加群成员

        Args:
            chat_room_name: 群聊名称
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            添加结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/AddChatRoomMembers', data=data, user_key=user_key)

    def add_chatroom_admin(self, chat_room_name, user_list, user_key=None):
        """添加群管理员

        Args:
            chat_room_name: 群聊名称
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            添加结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/AddChatroomAdmin', data=data, user_key=user_key)

    def create_chat_room(self, top_ic, user_list, user_key=None):
        """创建群请求

        Args:
            top_ic: 群主题
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            创建结果
        """
        data = {
            "TopIc": top_ic,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/CreateChatRoom', data=data, user_key=user_key)

    def del_chatroom_admin(self, chat_room_name, user_list, user_key=None):
        """删除群管理员

        Args:
            chat_room_name: 群聊名称
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            删除结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/DelChatroomAdmin', data=data, user_key=user_key)

    def get_chat_room_info(self, chat_room_wx_id_list, user_key=None):
        """获取群详情

        Args:
            chat_room_wx_id_list: 群聊微信ID列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群详情信息
        """
        data = {
            "ChatRoomWxIdList": chat_room_wx_id_list
        }
        return self._post_with_user_key('/group/GetChatRoomInfo', data=data, user_key=user_key)

    def get_chatroom_member_detail(self, chat_room_name, user_key=None):
        """获取群成员详细

        Args:
            chat_room_name: 群聊名称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群成员详细信息
        """
        data = {
            "ChatRoomName": chat_room_name
        }
        return self._post_with_user_key('/group/GetChatroomMemberDetail', data=data, user_key=user_key)

    def get_chatroom_qr_code(self, chat_room_name, user_key=None):
        """获取群二维码

        Args:
            chat_room_name: 群聊名称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群二维码信息
        """
        data = {
            "ChatRoomName": chat_room_name
        }
        return self._post_with_user_key('/group/GetChatroomQrCode', data=data, user_key=user_key)

    def group_list(self, user_key=None):
        """获取群列表

        Args:
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群列表
        """
        return self._get_with_user_key('/group/GroupList', user_key=user_key)

    def invite_chatroom_members(self, chat_room_name, user_list, user_key=None):
        """邀请群成员

        Args:
            chat_room_name: 群聊名称
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            邀请结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/InviteChatroomMembers', data=data, user_key=user_key)

    def move_to_contract(self, chat_room_name, val, user_key=None):
        """获取群聊

        Args:
            chat_room_name: 群聊名称
            val: 值
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            操作结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "Val": val
        }
        return self._post_with_user_key('/group/MoveToContract', data=data, user_key=user_key)

    def quit_chatroom(self, chat_room_name, user_key=None):
        """退出群聊

        Args:
            chat_room_name: 群聊名称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            退出结果
        """
        data = {
            "ChatRoomName": chat_room_name
        }
        return self._post_with_user_key('/group/QuitChatroom', data=data, user_key=user_key)

    def scan_into_url_group(self, url, user_key=None):
        """扫码入群

        Args:
            url: 群二维码URL
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            入群结果
        """
        data = {
            "Url": url
        }
        return self._post_with_user_key('/group/ScanIntoUrlGroup', data=data, user_key=user_key)

    def send_del_del_chat_room_member(self, chat_room_name, user_list, user_key=None):
        """删除群成员

        Args:
            chat_room_name: 群聊名称
            user_list: 用户列表，格式：["string"]
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            删除结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "UserList": user_list
        }
        return self._post_with_user_key('/group/SendDelDelChatRoomMember', data=data, user_key=user_key)

    def send_pat(self, chat_room_name, scene, to_user_name, user_key=None):
        """群拍一拍功能

        Args:
            chat_room_name: 群聊名称
            scene: 场景
            to_user_name: 目标用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            拍一拍结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "Scene": scene,
            "ToUserName": to_user_name
        }
        return self._post_with_user_key('/group/SendPat', data=data, user_key=user_key)

    def send_transfer_group_owner(self, chat_room_name, new_owner_user_name, user_key=None):
        """转让群

        Args:
            chat_room_name: 群聊名称
            new_owner_user_name: 新群主用户名
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            转让结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "NewOwnerUserName": new_owner_user_name
        }
        return self._post_with_user_key('/group/SendTransferGroupOwner', data=data, user_key=user_key)

    def set_chatroom_access_verify(self, chat_room_name, enable, user_key=None):
        """设置群聊邀请开关

        Args:
            chat_room_name: 群聊名称
            enable: 是否启用
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "Enable": enable
        }
        return self._post_with_user_key('/group/SetChatroomAccessVerify', data=data, user_key=user_key)

    def set_chatroom_announcement(self, chat_room_name, content, user_key=None):
        """设置群公告

        Args:
            chat_room_name: 群聊名称
            content: 公告内容
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "Content": content
        }
        return self._post_with_user_key('/group/SetChatroomAnnouncement', data=data, user_key=user_key)

    def set_chatroom_name(self, chat_room_name, nickname, user_key=None):
        """设置群昵称

        Args:
            chat_room_name: 群聊名称
            nickname: 昵称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            设置结果
        """
        data = {
            "ChatRoomName": chat_room_name,
            "Nickname": nickname
        }
        return self._post_with_user_key('/group/SetChatroomName', data=data, user_key=user_key)

    def set_get_chat_room_info_detail(self, chat_room_name, user_key=None):
        """获取群公告

        Args:
            chat_room_name: 群聊名称
            user_key: 普通用户密钥（可选，优先使用传入值，否则从配置文件读取）

        Returns:
            群公告信息
        """
        data = {
            "ChatRoomName": chat_room_name
        }
        return self._post_with_user_key('/group/SetGetChatRoomInfoDetail', data=data, user_key=user_key)

    # ==================== 便捷方法 ====================

    @staticmethod
    def load_robot_stat(file_path):
        """加载机器人状态文件

        Args:
            file_path: 状态文件路径

        Returns:
            dict: 状态数据，如果文件不存在返回None
        """
        try:
            import os
            import json
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            from common.log import logger
            logger.warning(f"[WxpadClient] 加载状态文件失败: {e}")
        return None

    @staticmethod
    def save_robot_stat(file_path, stat):
        """保存机器人状态文件

        Args:
            file_path: 状态文件路径
            stat: 状态数据

        Returns:
            bool: 保存是否成功
        """
        try:
            import os
            import json
            # 确保目录存在
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(stat, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            from common.log import logger
            logger.error(f"[WxpadClient] 保存状态文件失败: {e}")
            return False

    def sync_message_http(self, count=50):
        """HTTP方式同步消息

        Args:
            count: 获取消息数量，默认50

        Returns:
            dict: 消息同步结果
        """
        return self.http_sync_msg(count)

    # ==================== 接口实现完成 ====================
