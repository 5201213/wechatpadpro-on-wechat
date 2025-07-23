# encoding:utf-8

import json
import os
import logging

import bridge.bridge
import plugins
from bridge.bridge import Bridge
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common import const
from config import conf, load_config, global_config
from plugins import *
from plugins import pconf

# 定义指令集
COMMANDS = {
    "help": {
        "alias": ["help", "帮助", "功能", "菜单"],
        "desc": "回复此帮助",
    },
    "helpp": {
        "alias": ["help", "帮助"],  # 与help指令共用别名，根据参数数量区分
        "args": ["插件名"],
        "desc": "回复指定插件的详细帮助",
    },

    "model": {
        "alias": ["model", "模型"],
        "desc": "查看和设置全局模型",
    },
    "set_openai_api_key": {
        "alias": ["set_openai_api_key"],
        "args": ["api_key"],
        "desc": "设置你的OpenAI私有api_key",
    },
    "reset_openai_api_key": {
        "alias": ["reset_openai_api_key"],
        "desc": "重置为默认的api_key",
    },
    "set_gpt_model": {
        "alias": ["set_gpt_model"],
        "desc": "设置你的私有模型",
    },
    "reset_gpt_model": {
        "alias": ["reset_gpt_model"],
        "desc": "重置你的私有模型",
    },
    "gpt_model": {
        "alias": ["gpt_model"],
        "desc": "查询你使用的模型",
    },
    "id": {
        "alias": ["id", "用户"],
        "desc": "获取用户id",  # wechaty和wechatmp的用户id不会变化，可用于绑定管理员
    },
    "reset": {
        "alias": ["reset", "重置会话"],
        "desc": "重置会话",
    },
}

ADMIN_COMMANDS = {
    "resume": {
        "alias": ["resume", "恢复服务"],
        "desc": "恢复服务",
    },
    "stop": {
        "alias": ["stop", "暂停服务"],
        "desc": "暂停服务",
    },
    "reconf": {
        "alias": ["reconf", "重载配置"],
        "desc": "重载配置(不包含插件配置)",
    },
    "resetall": {
        "alias": ["resetall", "重置所有会话"],
        "desc": "重置所有会话",
    },
    "scanp": {
        "alias": ["scanp", "扫描插件"],
        "desc": "扫描插件目录是否有新插件",
    },
    "plist": {
        "alias": ["plist", "插件"],
        "desc": "打印当前插件列表",
    },
    "setpri": {
        "alias": ["setpri", "设置插件优先级"],
        "args": ["插件名", "优先级"],
        "desc": "设置指定插件的优先级，越大越优先",
    },
    "reloadp": {
        "alias": ["reloadp", "重载插件"],
        "args": ["插件名"],
        "desc": "重载指定插件配置",
    },
    "enablep": {
        "alias": ["enablep", "启用插件"],
        "args": ["插件名"],
        "desc": "启用指定插件",
    },
    "disablep": {
        "alias": ["disablep", "禁用插件"],
        "args": ["插件名"],
        "desc": "禁用指定插件",
    },
    "installp": {
        "alias": ["installp", "安装插件"],
        "args": ["仓库地址或插件名"],
        "desc": "安装指定插件",
    },
    "uninstallp": {
        "alias": ["uninstallp", "卸载插件"],
        "args": ["插件名"],
        "desc": "卸载指定插件",
    },
    "updatep": {
        "alias": ["updatep", "更新插件"],
        "args": ["插件名"],
        "desc": "更新指定插件",
    },
    "debug": {
        "alias": ["debug", "调试模式", "DEBUG"],
        "desc": "开启机器调试日志",
    },
}




# 定义帮助函数
def get_help_text(isadmin, isgroup):
    help_text = "🤖 机器人功能菜单\n"
    help_text += "═" * 30 + "\n\n"

    # 通用指令部分 - 根据用户权限显示不同内容
    help_text += "📋 基础功能\n"
    help_text += "─" * 20 + "\n"

    for cmd, info in COMMANDS.items():
        # 普通用户不显示的指令
        if not isadmin and cmd in ["set_openai_api_key", "reset_openai_api_key", "set_gpt_model", "reset_gpt_model", "gpt_model", "model"]:
            continue
        # 所有用户都不在帮助中显示的指令（这些是内部使用的）
        if cmd in ["helpp"]:  # helpp是内部指令，不需要在帮助中显示
            continue
        # id指令只在特定渠道显示
        if cmd == "id" and conf().get("channel_type", "wx") not in ["wxy", "wechatmp"]:
            continue

        alias = ["#" + a for a in info["alias"][:1]]
        help_text += f"  • {','.join(alias)}"
        if "args" in info:
            args = [a for a in info["args"]]
            help_text += f" {' '.join(args)}"
        help_text += f" - {info['desc']}\n"

    # 插件指令部分
    plugins = PluginManager().list_plugins()
    enabled_plugins = [plugin for plugin in plugins if plugins[plugin].enabled and not plugins[plugin].hidden]

    if enabled_plugins:
        help_text += f"\n🔧 插件功能 ({len(enabled_plugins)}个)\n"
        help_text += "─" * 20 + "\n"
        for plugin in enabled_plugins:
            namecn = plugins[plugin].namecn
            help_text += f"📌 {namecn}\n"
            plugin_help = PluginManager().instances[plugin].get_help_text(verbose=False).strip()
            if plugin_help:
                # 为插件帮助信息添加缩进，使用更清晰的格式
                indented_help = '\n'.join(['   ' + line for line in plugin_help.split('\n') if line.strip()])
                help_text += indented_help + "\n"
            else:
                help_text += "   暂无详细说明\n"
            help_text += "\n"

    # 管理员指令部分
    if ADMIN_COMMANDS and isadmin:
        help_text += "⚙️ 管理功能\n"
        help_text += "─" * 20 + "\n"
        for cmd, info in ADMIN_COMMANDS.items():
            alias = ["#" + a for a in info["alias"][:1]]
            help_text += f"  • {','.join(alias)}"
            if "args" in info:
                args = [a for a in info["args"]]
                help_text += f" {' '.join(args)}"
            help_text += f" - {info['desc']}\n"
        help_text += "\n"

    help_text += "═" * 30 + "\n"
    help_text += "💡 输入 #help [插件名] 查看插件详细帮助"

    return help_text


@plugins.register(
    name="Godcmd",
    desire_priority=999,
    hidden=True,
    desc="为你的机器人添加指令集，支持基于微信ID的自动认证管理员功能，加载顺序请放在首位，初次运行后插件目录会生成配置文件，配置管理员微信ID即可自动认证",
    version="2.0",
    author="lanvent",
)
class Godcmd(Plugin):
    def __init__(self):
        super().__init__()

        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        gconf = super().load_config()
        if not gconf:
            if not os.path.exists(config_path):
                gconf = {"admin_wxids": [], "auto_auth_notify": True}
                with open(config_path, "w") as f:
                    json.dump(gconf, f, indent=4)
        custom_commands = conf().get("clear_memory_commands", [])
        for custom_command in custom_commands:
            if custom_command and custom_command.startswith("#"):
                custom_command = custom_command[1:]
                if custom_command and custom_command not in COMMANDS["reset"]["alias"]:
                    COMMANDS["reset"]["alias"].append(custom_command)

        self.admin_users = []  # 运行时动态管理的管理员账号列表
        self.admin_wxids = gconf.get("admin_wxids", [])  # 配置的管理员微信ID列表
        self.auto_auth_notify = gconf.get("auto_auth_notify", True)  # 是否发送自动认证成功通知
        global_config["admin_users"] = self.admin_users
        self.isrunning = True  # 机器人是否运行中

        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[Godcmd] inited. 配置的管理员微信ID: {}".format(self.admin_wxids))

    def on_handle_context(self, e_context: EventContext):
        context_type = e_context["context"].type
        if context_type != ContextType.TEXT:
            if not self.isrunning:
                e_context.action = EventAction.BREAK_PASS
            return

        # 检查自动认证
        user = e_context["context"]["receiver"]
        isgroup = e_context["context"].get("isgroup", False)
        if not isgroup and user not in self.admin_users:  # 只在私聊中进行自动认证
            auto_auth_result = self.check_auto_auth(e_context)
            if auto_auth_result:
                # 如果进行了自动认证，发送通知并结束处理
                if self.auto_auth_notify:
                    reply = Reply()
                    reply.type = ReplyType.INFO
                    reply.content = auto_auth_result
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return

        content = e_context["context"].content
        logger.debug("[Godcmd] on_handle_context. content: %s" % content)
        if content.startswith("#"):
            if len(content) == 1:
                reply = Reply()
                reply.type = ReplyType.ERROR
                reply.content = f"空指令，输入#help查看指令列表\n"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            # msg = e_context['context']['msg']
            channel = e_context["channel"]
            user = e_context["context"]["receiver"]
            session_id = e_context["context"]["session_id"]
            isgroup = e_context["context"].get("isgroup", False)
            bottype = Bridge().get_bot_type("chat")
            bot = Bridge().get_bot("chat")
            # 将命令和参数分割
            command_parts = content[1:].strip().split()
            cmd = command_parts[0]
            args = command_parts[1:]
            isadmin = False
            if user in self.admin_users:
                isadmin = True
            ok = False
            result = "string"
            if any(cmd in info["alias"] for info in COMMANDS.values()):
                cmd = next(c for c, info in COMMANDS.items() if cmd in info["alias"])
                if cmd == "help" or cmd == "helpp":
                    if len(args) == 0:
                        ok, result = True, get_help_text(isadmin, isgroup)
                    else:
                        # This can replace the helpp command
                        plugins = PluginManager().list_plugins()
                        query_name = args[0].upper()
                        # search name and namecn
                        for name, plugincls in plugins.items():
                            if not plugincls.enabled:
                                continue
                            if query_name == name or query_name == plugincls.namecn:
                                ok, result = True, PluginManager().instances[name].get_help_text(isgroup=isgroup, isadmin=isadmin, verbose=True)
                                break
                        if not ok:
                            result = "插件不存在或未启用"
                elif cmd == "model":
                    if not isadmin and not self.is_admin_in_group(e_context["context"]):
                        ok, result = False, "需要管理员权限执行"
                    elif len(args) == 0:
                        model = conf().get("model") or const.GPT35
                        ok, result = True, "当前模型为: " + str(model)
                    elif len(args) == 1:
                        if args[0] not in const.MODEL_LIST:
                            ok, result = False, "模型名称不存在"
                        else:
                            conf()["model"] = self.model_mapping(args[0])
                            Bridge().reset_bot()
                            model = conf().get("model") or const.GPT35
                            ok, result = True, "模型设置为: " + str(model)
                elif cmd == "id":
                    ok, result = True, user
                elif cmd == "set_openai_api_key":
                    if len(args) == 1:
                        user_data = conf().get_user_data(user)
                        user_data["openai_api_key"] = args[0]
                        ok, result = True, "你的OpenAI私有api_key已设置为" + args[0]
                    else:
                        ok, result = False, "请提供一个api_key"
                elif cmd == "reset_openai_api_key":
                    try:
                        user_data = conf().get_user_data(user)
                        user_data.pop("openai_api_key")
                        ok, result = True, "你的OpenAI私有api_key已清除"
                    except Exception as e:
                        ok, result = False, "你没有设置私有api_key"
                elif cmd == "set_gpt_model":
                    if len(args) == 1:
                        user_data = conf().get_user_data(user)
                        user_data["gpt_model"] = args[0]
                        ok, result = True, "你的GPT模型已设置为" + args[0]
                    else:
                        ok, result = False, "请提供一个GPT模型"
                elif cmd == "gpt_model":
                    user_data = conf().get_user_data(user)
                    model = conf().get("model")
                    if "gpt_model" in user_data:
                        model = user_data["gpt_model"]
                    ok, result = True, "你的GPT模型为" + str(model)
                elif cmd == "reset_gpt_model":
                    try:
                        user_data = conf().get_user_data(user)
                        user_data.pop("gpt_model")
                        ok, result = True, "你的GPT模型已重置"
                    except Exception as e:
                        ok, result = False, "你没有设置私有GPT模型"
                elif cmd == "reset":
                    if bottype in [const.OPEN_AI, const.CHATGPT, const.CHATGPTONAZURE, const.LINKAI, const.BAIDU, const.XUNFEI, const.QWEN, const.GEMINI, const.ZHIPU_AI, const.CLAUDEAPI, const.DIFY, const.COZE]:
                        bot.sessions.clear_session(session_id)
                        if Bridge().chat_bots.get(bottype):
                            Bridge().chat_bots.get(bottype).sessions.clear_session(session_id)
                        channel.cancel_session(session_id)
                        ok, result = True, "会话已重置"
                    else:
                        ok, result = False, "当前对话机器人不支持重置会话"
                logger.debug("[Godcmd] command: %s by %s" % (cmd, user))
            elif any(cmd in info["alias"] for info in ADMIN_COMMANDS.values()):
                if isadmin:
                    if isgroup:
                        ok, result = False, "群聊不可执行管理员指令"
                    else:
                        cmd = next(c for c, info in ADMIN_COMMANDS.items() if cmd in info["alias"])
                        if cmd == "stop":
                            self.isrunning = False
                            ok, result = True, "服务已暂停"
                        elif cmd == "resume":
                            self.isrunning = True
                            ok, result = True, "服务已恢复"
                        elif cmd == "reconf":
                            load_config()
                            ok, result = True, "配置已重载"
                        elif cmd == "resetall":
                            if bottype in [const.OPEN_AI, const.CHATGPT, const.CHATGPTONAZURE, const.LINKAI, const.DIFY, const.COZE,
                                           const.BAIDU, const.XUNFEI, const.QWEN, const.GEMINI, const.ZHIPU_AI, const.MOONSHOT,
                                           const.MODELSCOPE]:
                                channel.cancel_all_session()
                                bot.sessions.clear_all_session()
                                ok, result = True, "重置所有会话成功"
                            else:
                                ok, result = False, "当前对话机器人不支持重置会话"
                        elif cmd == "debug":
                            if logger.getEffectiveLevel() == logging.DEBUG:  # 判断当前日志模式是否DEBUG
                                logger.setLevel(logging.INFO)
                                ok, result = True, "DEBUG模式已关闭"
                            else:
                                logger.setLevel(logging.DEBUG)
                                ok, result = True, "DEBUG模式已开启"
                        elif cmd == "plist":
                            plugins = PluginManager().list_plugins()
                            ok = True
                            result = "插件列表：\n"
                            for name, plugincls in plugins.items():
                                result += f"{plugincls.name}_v{plugincls.version} {plugincls.priority} - "
                                if plugincls.enabled:
                                    result += "已启用\n"
                                else:
                                    result += "未启用\n"
                        elif cmd == "scanp":
                            new_plugins = PluginManager().scan_plugins()
                            ok, result = True, "插件扫描完成"
                            PluginManager().activate_plugins()
                            if len(new_plugins) > 0:
                                result += "\n发现新插件：\n"
                                result += "\n".join([f"{p.name}_v{p.version}" for p in new_plugins])
                            else:
                                result += ", 未发现新插件"
                        elif cmd == "setpri":
                            if len(args) != 2:
                                ok, result = False, "请提供插件名和优先级"
                            else:
                                ok = PluginManager().set_plugin_priority(args[0], int(args[1]))
                                if ok:
                                    result = "插件" + args[0] + "优先级已设置为" + args[1]
                                else:
                                    result = "插件不存在"
                        elif cmd == "reloadp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok = PluginManager().reload_plugin(args[0])
                                if ok:
                                    result = "插件配置已重载"
                                else:
                                    result = "插件不存在"
                        elif cmd == "enablep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().enable_plugin(args[0])
                        elif cmd == "disablep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok = PluginManager().disable_plugin(args[0])
                                if ok:
                                    result = "插件已禁用"
                                else:
                                    result = "插件不存在"
                        elif cmd == "installp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名或.git结尾的仓库地址"
                            else:
                                ok, result = PluginManager().install_plugin(args[0])
                        elif cmd == "uninstallp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().uninstall_plugin(args[0])
                        elif cmd == "updatep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().update_plugin(args[0])
                        logger.debug("[Godcmd] admin command: %s by %s" % (cmd, user))
                else:
                    ok, result = False, "需要管理员权限才能执行该指令"
            else:
                trigger_prefix = conf().get("plugin_trigger_prefix", "$")
                if trigger_prefix == "#":  # 跟插件聊天指令前缀相同，继续递交
                    return
                ok, result = False, f"未知指令：{cmd}\n查看指令列表请输入#help \n"

            reply = Reply()
            if ok:
                reply.type = ReplyType.INFO
            else:
                reply.type = ReplyType.ERROR
            reply.content = result
            e_context["reply"] = reply

            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
        elif not self.isrunning:
            e_context.action = EventAction.BREAK_PASS

    def check_auto_auth(self, e_context: EventContext) -> str:
        """检查并执行微信ID自动认证

        Args:
            e_context: 事件上下文

        Returns:
            str: 如果进行了自动认证，返回认证成功消息；否则返回空字符串
        """
        try:
            # 获取用户微信ID
            msg = e_context["context"].kwargs.get("msg")
            if not msg:
                return ""

            # 尝试从不同的消息类型中获取微信ID
            user_wxid = None
            if hasattr(msg, 'from_user_id'):
                user_wxid = msg.from_user_id
            elif hasattr(msg, 'other_user_id'):
                user_wxid = msg.other_user_id
            elif hasattr(msg, 'actual_user_id'):
                user_wxid = msg.actual_user_id

            if not user_wxid:
                logger.debug("[Godcmd] 无法获取用户微信ID")
                return ""

            # 检查是否在管理员微信ID列表中
            if user_wxid in self.admin_wxids:
                user = e_context["context"]["receiver"]
                if user not in self.admin_users:
                    # 添加到管理员列表
                    self.admin_users.append(user)
                    global_config["admin_users"].append(user)
                    logger.info(f"[Godcmd] 微信ID {user_wxid} 自动认证成功，用户ID: {user}")
                    return "欢迎管理员！已自动完成认证。"

        except Exception as e:
            logger.error(f"[Godcmd] 自动认证检查异常: {e}")

        return ""

    def get_help_text(self, isadmin=False, isgroup=False, **kwargs):
        return get_help_text(isadmin, isgroup)


    def is_admin_in_group(self, context):
        if context["isgroup"]:
            return context.kwargs.get("msg").actual_user_id in global_config["admin_users"]
        return False


    def model_mapping(self, model) -> str:
        if model == "gpt-4-turbo":
            return const.GPT4_TURBO_PREVIEW
        return model

    def reload(self):
        gconf = pconf(self.name)
        if gconf:
            if gconf.get("admin_wxids"):
                self.admin_wxids = gconf["admin_wxids"]
                logger.info(f"[Godcmd] 重载配置，管理员微信ID: {self.admin_wxids}")
            if gconf.get("auto_auth_notify") is not None:
                self.auto_auth_notify = gconf["auto_auth_notify"]
