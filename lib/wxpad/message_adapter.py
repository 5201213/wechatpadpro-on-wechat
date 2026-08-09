"""Adapters for the WebSocket message contracts used by WeChatPadPro."""

import time


_KIND_TO_MSG_TYPE = {
    "text": 1,
    "image": 3,
    "voice": 34,
    "contact": 42,
    "video": 43,
    "emoji": 47,
    "location": 48,
    "app": 49,
    "file": 49,
    "link": 49,
    "quote": 49,
    "reply": 49,
    "share": 49,
    "system": 10002,
}


def _as_text(value):
    if isinstance(value, dict):
        return value.get("str", value.get("string", ""))
    return str(value or "")


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_websocket_event(payload):
    """Return ``(event_type, messages, metadata)`` for old and MAX payloads."""
    if isinstance(payload, list):
        return "sync_message", [item for item in payload if isinstance(item, dict)], {}

    if not isinstance(payload, dict):
        return "unknown", [], {}

    # Legacy servers push a message directly without an event envelope.
    if "msg_id" in payload or "new_msg_id" in payload:
        return "sync_message", [payload], {}

    # Some MAX builds can also push the canonical message object directly.
    if "id" in payload and ("sender_id" in payload or "conversation_id" in payload):
        return "sync_message", [payload], {}

    body = payload.get("Data", payload.get("data", payload))
    if not isinstance(body, dict):
        return str(payload.get("type", "unknown")), [], {}

    inner = body.get("data", body.get("Data", body))
    if not isinstance(inner, dict):
        inner = body

    event_type = str(
        inner.get("type")
        or body.get("type")
        or payload.get("type")
        or "unknown"
    )
    messages = inner.get("messages", body.get("messages", []))
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        messages = []

    return event_type, [item for item in messages if isinstance(item, dict)], inner


def _mapping(value):
    return dict(value) if isinstance(value, dict) else {}


def normalize_websocket_message(message):
    """Convert legacy or ``wechatpad.message.v1/v2`` data to the channel contract."""
    if not isinstance(message, dict):
        raise TypeError("WebSocket message must be a mapping")

    # Already normalized data is kept intact while Content is made predictable.
    if "FromUserName" in message and "ToUserName" in message:
        normalized = dict(message)
        content = normalized.get("Content", {})
        if not isinstance(content, dict):
            normalized["Content"] = {"str": _as_text(content)}
        for target, source in (
            ("ReplyContext", "reply_context"),
            ("MessageContext", "message_context"),
            ("App", "app"),
            ("Image", "image"),
            ("Video", "video"),
            ("File", "file"),
            ("Voice", "voice"),
        ):
            if target not in normalized and source in message:
                normalized[target] = _mapping(message.get(source))
        message_context = normalized.get("MessageContext", {})
        if "MentionedUserIDs" not in normalized and isinstance(message_context, dict):
            normalized["MentionedUserIDs"] = message_context.get(
                "mentioned_user_ids", message_context.get("mentionedUserIDs", [])
            )
        return normalized


    is_canonical = "sender_id" in message or "conversation_id" in message
    if is_canonical:
        conversation_id = _as_text(message.get("conversation_id"))
        sender_id = _as_text(message.get("sender_id"))
        recipient_id = _as_text(message.get("recipient_id"))
        is_group = bool(message.get("is_group")) or conversation_id.endswith("@chatroom")
        from_user = conversation_id if is_group else (sender_id or conversation_id)
        msg_id = message.get("id") or message.get("local_id") or message.get("sequence") or 0
        kind = _as_text(message.get("kind")).lower()
        message_context = _mapping(message.get("message_context"))
        mentioned_user_ids = message.get(
            "mentioned_user_ids",
            message.get("mentionedUserIDs", message.get("MentionedUserIDs")),
        )
        if mentioned_user_ids is None:
            mentioned_user_ids = message_context.get(
                "mentioned_user_ids", message_context.get("mentionedUserIDs", [])
            )
        kind_type = _KIND_TO_MSG_TYPE.get(kind, 1)
        msg_type = _as_int(message.get("type"), kind_type)
        # MAX uses Type=0 for some outgoing text echoes.  The canonical `kind`
        # remains authoritative and avoids treating those echoes as unknown.
        if msg_type <= 0 and kind in _KIND_TO_MSG_TYPE:
            msg_type = kind_type

        return {
            "FromUserName": from_user,
            "ToUserName": recipient_id,
            "ActualUserName": sender_id if is_group else "",
            "Content": {"str": _as_text(message.get("content"))},
            "MsgType": msg_type,
            "CreateTime": _as_int(message.get("created_at"), int(time.time())),
            "MsgSource": _as_text(message.get("message_source")),
            "MsgId": msg_id,
            "NewMsgId": msg_id,
            "IsOutgoing": message.get("direction") == "outgoing",
            "PushContent": _as_text(message.get("push_content")),
            "Kind": kind,
            "Schema": _as_text(message.get("schema")),
            "Direction": _as_text(message.get("direction")),
            "ConversationId": conversation_id,
            "SenderId": sender_id,
            "RecipientId": recipient_id,
            "Sequence": message.get("sequence", 0),
            "Status": message.get("status", 0),
            # MAX exposes group mentions as structured fields instead of only
            # embedding them in the legacy MsgSource XML.  Keep the original
            # value shape here; the message layer normalizes list/string/map
            # variants in one place.
            "MentionedUserIDs": mentioned_user_ids,
            "AtUserList": message.get(
                "at_user_list",
                message.get("atUserList", message.get("AtUserList", [])),
            ),
            "IsAt": message.get(
                "is_at",
                message.get("is_mentioned", message.get("IsAt", False)),
            ),
            "ReplyContext": _mapping(message.get("reply_context")),
            "MessageContext": message_context,
            "App": _mapping(message.get("app")),
            "Image": _mapping(message.get("image")),
            "Video": _mapping(message.get("video")),
            "File": _mapping(message.get("file")),
            "Voice": _mapping(message.get("voice")),
            "RawMessage": message,
        }

    content = message.get("content", {})
    if not isinstance(content, dict):
        content = {"str": _as_text(content)}
    message_context = _mapping(message.get("message_context"))
    mentioned_user_ids = message.get(
        "mentioned_user_ids",
        message.get("mentionedUserIDs", message.get("MentionedUserIDs")),
    )
    if mentioned_user_ids is None:
        mentioned_user_ids = message_context.get(
            "mentioned_user_ids", message_context.get("mentionedUserIDs", [])
        )
    return {
        "FromUserName": _as_text(message.get("from_user_name", {})),
        "ToUserName": _as_text(message.get("to_user_name", {})),
        "Content": content,
        "MsgType": _as_int(message.get("msg_type"), 1),
        "CreateTime": _as_int(message.get("create_time"), int(time.time())),
        "MsgSource": _as_text(message.get("msg_source", "")),
        "MsgId": message.get("msg_id", 0),
        "NewMsgId": message.get("new_msg_id") or message.get("msg_id", 0),
        "IsOutgoing": bool(message.get("is_outgoing", False)),
        "MentionedUserIDs": mentioned_user_ids,
        "AtUserList": message.get(
            "at_user_list",
            message.get("atUserList", message.get("AtUserList", [])),
        ),
        "IsAt": message.get(
            "is_at",
            message.get("is_mentioned", message.get("IsAt", False)),
        ),
        "ReplyContext": _mapping(message.get("reply_context")),
        "MessageContext": message_context,
        "App": _mapping(message.get("app")),
        "Image": _mapping(message.get("image")),
        "Video": _mapping(message.get("video")),
        "File": _mapping(message.get("file")),
        "Voice": _mapping(message.get("voice")),
        "RawMessage": message,
    }
