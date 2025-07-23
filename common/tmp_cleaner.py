"""
临时文件清理模块
定时清理tmp目录下的过期文件，防止磁盘空间浪费
"""

import os
import time
import threading
import logging
from pathlib import Path
from typing import Optional

from config import conf

logger = logging.getLogger(__name__)


class TmpCleaner:
    """临时文件清理器"""
    
    def __init__(self):
        self.tmp_dir = Path("./tmp/")
        self.cleanup_enabled = conf().get("tmp_cleanup_enabled", True)
        self.cleanup_interval = conf().get("tmp_cleanup_interval", 3600)  # 默认1小时
        self.file_max_age = conf().get("tmp_file_max_age", 3600)  # 默认1小时
        self.cleanup_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        logger.info(f"[TmpCleaner] 初始化临时文件清理器")
        logger.info(f"[TmpCleaner] 清理启用: {self.cleanup_enabled}")
        logger.info(f"[TmpCleaner] 清理间隔: {self.cleanup_interval}秒")
        logger.info(f"[TmpCleaner] 文件最大保留时间: {self.file_max_age}秒")
        logger.info(f"[TmpCleaner] 临时目录: {self.tmp_dir.absolute()}")
    
    def start(self):
        """启动清理器"""
        if not self.cleanup_enabled:
            logger.info("[TmpCleaner] 临时文件清理已禁用")
            return
        
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            logger.warning("[TmpCleaner] 清理线程已在运行")
            return
        
        # 确保tmp目录存在
        self.tmp_dir.mkdir(exist_ok=True)
        
        # 启动清理线程
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="TmpCleaner",
            daemon=True
        )
        self.cleanup_thread.start()
        logger.info("[TmpCleaner] 临时文件清理器已启动")
    
    def stop(self):
        """停止清理器"""
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            logger.info("[TmpCleaner] 正在停止临时文件清理器...")
            self.stop_event.set()
            self.cleanup_thread.join(timeout=5)
            if self.cleanup_thread.is_alive():
                logger.warning("[TmpCleaner] 清理线程未能正常停止")
            else:
                logger.info("[TmpCleaner] 临时文件清理器已停止")
    
    def _cleanup_loop(self):
        """清理循环"""
        logger.info("[TmpCleaner] 清理循环已启动")
        
        while not self.stop_event.is_set():
            try:
                self._perform_cleanup()
            except Exception as e:
                logger.error(f"[TmpCleaner] 清理过程中发生异常: {e}")
            
            # 等待下次清理，支持中断
            if self.stop_event.wait(timeout=self.cleanup_interval):
                break
        
        logger.info("[TmpCleaner] 清理循环已退出")
    
    def _perform_cleanup(self):
        """执行一次清理操作"""
        if not self.tmp_dir.exists():
            logger.debug("[TmpCleaner] 临时目录不存在，跳过清理")
            return
        
        current_time = time.time()
        deleted_count = 0
        deleted_size = 0
        error_count = 0
        
        logger.debug(f"[TmpCleaner] 开始清理临时文件，当前时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))}")
        
        try:
            # 遍历tmp目录下的所有文件
            for file_path in self.tmp_dir.rglob("*"):
                if self.stop_event.is_set():
                    logger.info("[TmpCleaner] 收到停止信号，中断清理")
                    break
                
                # 跳过目录
                if file_path.is_dir():
                    continue
                
                try:
                    # 检查文件年龄
                    file_mtime = file_path.stat().st_mtime
                    file_age = current_time - file_mtime
                    
                    if file_age > self.file_max_age:
                        # 文件过期，删除
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        
                        deleted_count += 1
                        deleted_size += file_size
                        
                        logger.debug(f"[TmpCleaner] 已删除过期文件: {file_path.name} "
                                   f"(年龄: {file_age:.1f}秒, 大小: {file_size}字节)")
                    
                except (OSError, FileNotFoundError) as e:
                    # 文件可能已被其他进程删除或正在使用
                    error_count += 1
                    logger.debug(f"[TmpCleaner] 无法删除文件 {file_path.name}: {e}")
                except Exception as e:
                    error_count += 1
                    logger.warning(f"[TmpCleaner] 处理文件 {file_path.name} 时发生异常: {e}")
        
        except Exception as e:
            logger.error(f"[TmpCleaner] 遍历临时目录时发生异常: {e}")
            return
        
        # 记录清理结果
        if deleted_count > 0 or error_count > 0:
            logger.info(f"[TmpCleaner] 清理完成 - 删除文件: {deleted_count}个, "
                       f"释放空间: {self._format_size(deleted_size)}, "
                       f"错误: {error_count}个")
        else:
            logger.debug("[TmpCleaner] 清理完成 - 无过期文件")
        
        # 清理空目录
        self._cleanup_empty_dirs()
    
    def _cleanup_empty_dirs(self):
        """清理空目录"""
        try:
            for dir_path in self.tmp_dir.rglob("*"):
                if dir_path.is_dir() and dir_path != self.tmp_dir:
                    try:
                        # 尝试删除空目录
                        dir_path.rmdir()
                        logger.debug(f"[TmpCleaner] 已删除空目录: {dir_path.relative_to(self.tmp_dir)}")
                    except OSError:
                        # 目录不为空或有其他问题，忽略
                        pass
        except Exception as e:
            logger.debug(f"[TmpCleaner] 清理空目录时发生异常: {e}")
    
    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes == 0:
            return "0B"
        
        units = ['B', 'KB', 'MB', 'GB']
        unit_index = 0
        size = float(size_bytes)
        
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        
        return f"{size:.1f}{units[unit_index]}"
    
    def force_cleanup(self):
        """强制执行一次清理（用于测试或手动触发）"""
        if not self.cleanup_enabled:
            logger.warning("[TmpCleaner] 清理功能已禁用，无法执行强制清理")
            return
        
        logger.info("[TmpCleaner] 执行强制清理")
        self._perform_cleanup()


# 全局清理器实例
_tmp_cleaner: Optional[TmpCleaner] = None


def get_tmp_cleaner() -> TmpCleaner:
    """获取全局清理器实例"""
    global _tmp_cleaner
    if _tmp_cleaner is None:
        _tmp_cleaner = TmpCleaner()
    return _tmp_cleaner


def start_tmp_cleaner():
    """启动临时文件清理器"""
    cleaner = get_tmp_cleaner()
    cleaner.start()


def stop_tmp_cleaner():
    """停止临时文件清理器"""
    global _tmp_cleaner
    if _tmp_cleaner:
        _tmp_cleaner.stop()