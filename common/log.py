import logging
import sys

try:
    import colorlog
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False


def _reset_logger(log):
    for handler in log.handlers:
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False

    # 控制台处理器 - 支持彩色输出
    console_handle = logging.StreamHandler(sys.stdout)
    if HAS_COLORLOG:
        # 使用彩色格式化器
        color_formatter = colorlog.ColoredFormatter(
            "%(log_color)s[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                'DEBUG': 'blue',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'purple'
            }
        )
        console_handle.setFormatter(color_formatter)
    else:
        # 降级到普通格式化器
        console_handle.setFormatter(
            logging.Formatter(
                "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    # 文件处理器 - 保持原有格式（无颜色代码）
    file_handle = logging.FileHandler("run.log", encoding="utf-8")
    file_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    log.addHandler(file_handle)
    log.addHandler(console_handle)


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.INFO)  # 默认设置为INFO级别，而不是DEBUG
    return log


# 日志句柄
logger = _get_logger()
