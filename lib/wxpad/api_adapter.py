"""Translate legacy WeChatPadPro calls to the WeChatPadProMAX API contract.

The application keeps its existing public client methods so other modules do not
need to know which backend is in use.  This module is the single translation
boundary for legacy endpoint names and request fields.
"""


def _pick(data, *names, default=None):
    data = data if isinstance(data, dict) else {}
    for name in names:
        if name in data:
            return data[name]

    lower_values = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        if name.lower() in lower_values:
            return lower_values[name.lower()]
    return default


def _join_ids(value):
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value if item)
    return str(value or "")


def _proxy(value):
    if isinstance(value, dict):
        return value
    return {
        "ProxyIp": str(value or ""),
        "ProxyPassword": "",
        "ProxyUser": "",
    }


def _request(path, data=None, method="POST", params=None):
    return {
        "method": method,
        "path": path,
        "data": data or {},
        "params": params or {},
    }


def unsupported(reason):
    return [], reason


def build_max_requests(legacy_path, data=None, *, wxid="", device_name=""):
    """Build one or more MAX request specifications for a legacy client call."""
    data = data if isinstance(data, dict) else {}

    if legacy_path == "/login/A16Login":
        return [_request("/Login/A16Data", {
            "A16": _pick(data, "A16", "A16Data", default=""),
            "DeviceName": _pick(data, "DeviceName", default=device_name),
            "Extend": _pick(data, "Extend", default=""),
            "Password": _pick(data, "Password", default=""),
            "Proxy": _proxy(_pick(data, "Proxy", default="")),
            "UserName": _pick(data, "UserName", default=""),
        })], None
    if legacy_path == "/login/CheckCanSetAlias":
        return [_request("/User/CheckCanSetAlias", method="GET")], None
    if legacy_path in ("/login/DeviceLogin", "/login/LoginNew"):
        return [_request("/Login/62data", {
            "Data62": _pick(data, "Data62", default=""),
            "DeviceName": _pick(data, "DeviceName", default=device_name),
            "Password": _pick(data, "Password", default=""),
            "Proxy": _proxy(_pick(data, "Proxy", default="")),
            "UserName": _pick(data, "UserName", default=""),
        })], None
    if legacy_path == "/login/Get62Data":
        return [_request("/Login/Get62Data")], None
    if legacy_path == "/login/GetIWXConnect":
        return [_request("/Login/LongLinkStatus", method="GET")], None
    if legacy_path == "/login/GetInItStatus":
        return [_request("/Login/GetCacheInfo")], None
    if legacy_path == "/login/LogOut":
        return [_request("/Login/LogOut")], None
    if legacy_path == "/login/PhoneDeviceLogin":
        return [_request("/Login/TwiceAutoAuth")], None
    if legacy_path == "/login/ShowQrCode":
        return [_request("/Login/GetQR", {
            "DeviceName": device_name,
            "Proxy": _proxy(""),
            "oversea": False,
        })], None
    if legacy_path == "/login/SmsLogin":
        return [_request("/Login/62dataSMSVerify", {
            "Cookie": _pick(data, "Cookie", default=""),
            "Proxy": _proxy(_pick(data, "Proxy", default="")),
            "Sms": _pick(data, "Sms", "SMS", "Code", default=""),
            "Url": _pick(data, "Url", "URL", default=""),
        })], None
    if legacy_path == "/ws/GetSyncMsg":
        return [_request("/Msg/Sync", {"Scene": 0, "Synckey": ""})], None

    if legacy_path == "/message/AddMessageMgr":
        requests = []
        for item in _pick(data, "MsgItem", default=[]) or []:
            image = _pick(item, "ImageContent", "Base64", default="")
            target = _pick(item, "ToUserName", "ToWxid", default="")
            if image:
                requests.append(_request("/Msg/UploadImg", {"Base64": image, "ToWxid": target}))
            else:
                requests.append(_request("/Msg/SendTxt", {
                    "At": _join_ids(_pick(item, "AtWxIDList", "At", default=[])),
                    "Content": _pick(item, "TextContent", "Content", default=""),
                    "ToWxid": target,
                    "Type": _pick(item, "MsgType", "Type", default=0),
                }))
        return (requests, None) if requests else unsupported("MsgItem 不能为空")
    if legacy_path == "/message/ForwardEmoji":
        requests = [
            _request("/Msg/SendEmoji", {
                "Md5": _pick(item, "EmojiMd5", "Md5", default=""),
                "ToWxid": _pick(item, "ToUserName", "ToWxid", default=""),
                "TotalLen": _pick(item, "EmojiSize", "TotalLen", default=0),
            })
            for item in (_pick(data, "EmojiList", default=[]) or [])
        ]
        return (requests, None) if requests else unsupported("EmojiList 不能为空")
    if legacy_path in ("/message/ForwardImageMessage", "/message/ForwardVideoMessage"):
        list_name = "ForwardImageList" if legacy_path.endswith("ImageMessage") else "ForwardVideoList"
        endpoint = "/Msg/SendCDNImg" if legacy_path.endswith("ImageMessage") else "/Msg/SendCDNVideo"
        requests = []
        for item in _pick(data, list_name, default=[]) or []:
            content = _pick(item, "Content", "ContentXML", "Xml", default="")
            if not content:
                return unsupported(f"{list_name} 缺少 MAX 接口要求的 Content/XML")
            requests.append(_request(endpoint, {
                "Content": content,
                "ToWxid": _pick(item, "ToUserName", "ToWxid", default=""),
            }))
        return (requests, None) if requests else unsupported(f"{list_name} 不能为空")
    if legacy_path in ("/message/GetMsgBigImg", "/message/GetMsgVideo"):
        section = _pick(data, "Section", default={}) or {}
        endpoint = "/Tools/DownloadImg" if legacy_path.endswith("BigImg") else "/Tools/DownloadVideo"
        return [_request(endpoint, {
            "compress_type": _pick(data, "CompressType", default=0),
            "data_len": _pick(data, "TotalLen", default=0),
            "msg_id": _pick(data, "MsgId", default=0),
            "section": {
                "data_len": _pick(section, "DataLen", "SectionLen", default=61440),
                "start_pos": _pick(section, "StartPos", "SectionStart", default=0),
            },
            "to_wxid": _pick(data, "ToUserName", "ToWxid", default=""),
        })], None
    if legacy_path == "/message/GroupMassMsgImage":
        targets = _pick(data, "ToUserName", default=[])
        targets = targets if isinstance(targets, (list, tuple, set)) else [targets]
        return [
            _request("/Msg/UploadImg", {
                "Base64": _pick(data, "ImageBase64", default=""),
                "ToWxid": target,
            }) for target in targets if target
        ], None
    if legacy_path == "/message/GroupMassMsgText":
        targets = _pick(data, "ToUserName", default=[])
        targets = targets if isinstance(targets, (list, tuple, set)) else [targets]
        return [
            _request("/Msg/SendTxt", {
                "At": "",
                "Content": _pick(data, "Content", default=""),
                "ToWxid": target,
                "Type": 0,
            }) for target in targets if target
        ], None
    if legacy_path in ("/message/HttpSyncMsg", "/message/NewSyncHistoryMessage"):
        return [_request("/Msg/Sync", {"Scene": 0, "Synckey": ""})], None
    if legacy_path in ("/message/RevokeMsg", "/message/RevokeMsgNew"):
        return [_request("/Msg/Revoke", {
            "ClientMsgId": _pick(data, "ClientMsgId", default=0),
            "CreateTime": _pick(data, "CreateTime", default=0),
            "NewMsgId": _pick(data, "NewMsgId", default=0),
            "ToUserName": _pick(data, "ToUserName", default=""),
        })], None
    if legacy_path == "/message/SendImageMessage":
        requests = [
            _request("/Msg/UploadImg", {
                "Base64": _pick(item, "ImageContent", "Base64", default=""),
                "ToWxid": _pick(item, "ToUserName", "ToWxid", default=""),
            })
            for item in (_pick(data, "MsgItem", default=[]) or [])
        ]
        return (requests, None) if requests else unsupported("MsgItem 不能为空")

    if legacy_path == "/friend/AgreeAdd":
        return [_request("/Friend/PassVerify", {
            "opcode": _pick(data, "OpCode", default=0),
            "scene": _pick(data, "Scene", default=0),
            "v1": _pick(data, "V3", "V1", default=""),
            "v2": _pick(data, "V4", "V2", default=""),
        })], None
    if legacy_path == "/friend/DelContact":
        return [_request("/Friend/Delete", {"toWxid": _pick(data, "DelUserName", default="")})], None
    if legacy_path in ("/friend/GetContactList", "/friend/GetFriendList", "/friend/GetGHList"):
        return [_request("/Friend/GetContractList", {
            "currentChatRoomContactSeq": _pick(data, "CurrentChatRoomContactSeq", default=0),
            "currentWxcontactSeq": _pick(data, "CurrentWxcontactSeq", default=0),
        })], None
    if legacy_path == "/friend/GetFriendRelation":
        return [_request("/Friend/GetFriendstate", {
            "opCode": _pick(data, "OpCode", default=0),
            "toWxid": _pick(data, "UserName", "ToWxid", default=""),
        })], None
    if legacy_path == "/friend/GetMFriend":
        return [_request("/Friend/GetMFriend")], None
    if legacy_path == "/friend/GroupList":
        return [_request("/Group/List", method="GET", params={"force": False})], None
    if legacy_path == "/friend/SearchContact":
        return [_request("/Friend/Search", {
            "fromScene": _pick(data, "FromScene", default=0),
            "keyword": _pick(data, "UserName", "Keyword", default=""),
            "searchScene": _pick(data, "SearchScene", default=1),
        })], None
    if legacy_path == "/friend/UploadMContact":
        return [_request("/Friend/Upload", {
            "currentPhoneNo": _pick(data, "Mobile", default=""),
            "opcode": _pick(data, "OpCode", default=0),
            "phoneNo": _join_ids(_pick(data, "MobileList", default=[])),
        })], None
    if legacy_path == "/friend/VerifyUser":
        return [_request("/Friend/SendRequest", {
            "v1": _pick(data, "V3", "V1", default=""),
            "v2": _pick(data, "V4", "V2", default=""),
        })], None

    if legacy_path == "/user/ChangePwd":
        ticket = _pick(data, "Ticket", default="")
        if not ticket:
            return unsupported("MAX 的 SetPasswd 必须提供 Ticket，旧接口的 OldPass 不能替代")
        return [_request("/User/SetPasswd", {
            "NewPassword": _pick(data, "NewPass", "NewPassword", default=""),
            "Ticket": ticket,
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/GetMyQrCode":
        return [_request("/User/GetQRCode", {
            "Style": _pick(data, "Style", default=0),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/ModifyRemark":
        return [_request("/Friend/SetRemarks", {
            "remarks": _pick(data, "RemarkName", default=""),
            "toWxid": _pick(data, "UserName", default=""),
        })], None
    if legacy_path == "/user/ModifyUserInfo":
        return [_request("/User/UpdateProfile", {
            "City": _pick(data, "City", default=""),
            "Country": _pick(data, "Country", default=""),
            "NickName": _pick(data, "NickName", default=""),
            "Province": _pick(data, "Province", default=""),
            "Sex": _pick(data, "Sex", default=0),
            "Signature": _pick(data, "Signature", default=""),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/SetFunctionSwitch":
        return [_request("/User/PrivacySettings", {
            "Function": _pick(data, "Function", default=0),
            "Value": _pick(data, "Value", default=0),
            "Wxid": wxid,
        })], None
    if legacy_path in ("/user/SetNickName", "/user/UpdateNickName"):
        return [_request("/User/UpdateProfile", {
            "NickName": _pick(data, "Val", "NickName", default=""),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/SetProxy":
        return unsupported("MAX 没有运行时修改代理的等价接口，代理需在登录请求中设置")
    if legacy_path == "/user/SetSendPat":
        return unsupported("MAX Swagger 没有设置拍一拍名称的接口")
    if legacy_path == "/user/SetSexDq":
        return [_request("/User/UpdateProfile", {
            "City": _pick(data, "City", default=""),
            "Country": _pick(data, "Country", default=""),
            "Province": _pick(data, "Province", default=""),
            "Sex": _pick(data, "Sex", default=0),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/SetSignature":
        return [_request("/User/UpdateProfile", {
            "Signature": _pick(data, "Val", "Signature", default=""),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/SetWechat":
        return [_request("/User/SetAlisa", {
            "Alisa": _pick(data, "Alisa", default=""),
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/UpdateAutoPass":
        return [_request("/User/PrivacySettings", {
            "Function": _pick(data, "SwitchType", default=0),
            "Value": 1,
            "Wxid": wxid,
        })], None
    if legacy_path == "/user/UploadHeadImage":
        return [_request("/User/UploadHeadImage", {
            "Base64": _pick(data, "Base64", default=""),
            "Wxid": wxid,
        })], None

    group_routes = {
        "/group/AddChatRoomMembers": ("/Group/AddChatRoomMember", None),
        "/group/CreateChatRoom": ("/Group/CreateChatRoom", None),
        "/group/InviteChatroomMembers": ("/Group/InviteChatRoomMember", None),
        "/group/SendDelDelChatRoomMember": ("/Group/DelChatRoomMember", None),
    }
    if legacy_path in group_routes:
        endpoint, _ = group_routes[legacy_path]
        payload = {"ToWxids": _join_ids(_pick(data, "UserList", "ToWxids", default=[]))}
        if legacy_path != "/group/CreateChatRoom":
            payload["ChatRoomName"] = _pick(data, "ChatRoomName", default="")
        return [_request(endpoint, payload)], None
    if legacy_path in ("/group/AddChatroomAdmin", "/group/DelChatroomAdmin"):
        return [_request("/Group/OperateChatRoomAdmin", {
            "QID": _pick(data, "ChatRoomName", default=""),
            "ToWxids": _join_ids(_pick(data, "UserList", default=[])),
            "Val": 1 if legacy_path.endswith("AddChatroomAdmin") else 0,
        })], None
    if legacy_path == "/group/GetChatRoomInfo":
        return [_request("/Group/GetChatRoomInfo", {
            "QID": _join_ids(_pick(data, "ChatRoomWxIdList", default=[])),
        })], None
    if legacy_path == "/group/GetChatroomMemberDetail":
        # GetChatRoomInfo contains ChatRoomMember; GetChatRoomMemberDetail only
        # returns the member count in this MAX build.
        return [_request("/Group/GetChatRoomInfo", {
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None
    if legacy_path == "/group/GetChatroomQrCode":
        return [_request("/Group/GetQRCode", {"QID": _pick(data, "ChatRoomName", default="")})], None
    if legacy_path == "/group/GroupList":
        return [_request("/Group/GroupList", method="GET", params={"force": False})], None
    if legacy_path == "/group/MoveToContract":
        return [_request("/Group/MoveContractList", {
            "QID": _pick(data, "ChatRoomName", default=""),
            "Val": _pick(data, "Val", default=0),
        })], None
    if legacy_path == "/group/QuitChatroom":
        return [_request("/Group/Quit", {"QID": _pick(data, "ChatRoomName", default="")})], None
    if legacy_path == "/group/ScanIntoUrlGroup":
        return [_request("/Group/ScanIntoGroup", {"Url": _pick(data, "Url", default="")})], None
    if legacy_path == "/group/SendPat":
        return [_request("/Group/SendPat", {
            "QID": _pick(data, "ChatRoomName", default=""),
            "Scene": _pick(data, "Scene", default=0),
            "ToUserName": _pick(data, "ToUserName", default=""),
        })], None
    if legacy_path == "/group/SendTransferGroupOwner":
        return [_request("/Group/SendTransferGroupOwner", {
            "NewOwnerUserName": _pick(data, "NewOwnerUserName", default=""),
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None
    if legacy_path == "/group/SetChatroomAccessVerify":
        return [_request("/Group/SetChatroomAccessVerify", {
            "Enable": _pick(data, "Enable", default=False),
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None
    if legacy_path == "/group/SetChatroomAnnouncement":
        return [_request("/Group/SetChatRoomAnnouncement", {
            "Content": _pick(data, "Content", default=""),
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None
    if legacy_path == "/group/SetChatroomName":
        return [_request("/Group/SetChatRoomName", {
            "Content": _pick(data, "Nickname", "Content", default=""),
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None
    if legacy_path == "/group/SetGetChatRoomInfoDetail":
        return [_request("/Group/GetChatRoomInfoDetail", {
            "QID": _pick(data, "ChatRoomName", default=""),
        })], None

    return unsupported(f"未找到旧接口 {legacy_path} 的 MAX 映射")
