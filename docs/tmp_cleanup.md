# 临时文件自动清理功能

## 功能概述

为了防止临时文件积累导致磁盘空间不足，系统新增了自动清理功能，定期清理 `./tmp/` 目录下的过期文件。

## 配置选项

在 `config.json` 中可以配置以下选项：

```json
{
  "tmp_cleanup_enabled": true,
  "tmp_cleanup_interval": 3600,
  "tmp_file_max_age": 3600
}
```

### 配置说明

- **tmp_cleanup_enabled** (boolean, 默认: true)
  - 是否启用临时文件自动清理功能
  - 设置为 `false` 可完全禁用清理功能

- **tmp_cleanup_interval** (integer, 默认: 3600)
  - 清理检查间隔，单位为秒
  - 默认每小时检查一次
  - 建议值：1800-7200（30分钟到2小时）

- **tmp_file_max_age** (integer, 默认: 3600)
  - 临时文件最大保留时间，单位为秒
  - 超过此时间的文件将被删除
  - 默认保留1小时
  - 建议值：1800-7200（30分钟到2小时）

## 工作原理

1. **启动时机**：应用启动后自动启动清理器
2. **清理周期**：按配置的间隔定期执行清理
3. **清理规则**：
   - 删除修改时间超过 `tmp_file_max_age` 的文件
   - 清理空目录
   - 跳过正在使用的文件（通过异常处理）
4. **安全机制**：
   - 只清理 `./tmp/` 目录下的文件
   - 不会删除正在使用的文件
   - 详细的日志记录

## 日志输出

清理器会输出详细的日志信息：

```
[TmpCleaner] 初始化临时文件清理器
[TmpCleaner] 清理启用: True
[TmpCleaner] 清理间隔: 3600秒
[TmpCleaner] 文件最大保留时间: 3600秒
[TmpCleaner] 临时目录: /path/to/project/tmp
[TmpCleaner] 临时文件清理器已启动
[TmpCleaner] 清理完成 - 删除文件: 5个, 释放空间: 2.3MB, 错误: 0个
```

## 手动清理

如需手动触发清理，可以在代码中调用：

```python
from common.tmp_cleaner import get_tmp_cleaner

# 强制执行一次清理
cleaner = get_tmp_cleaner()
cleaner.force_cleanup()
```

## 性能影响

- **CPU使用**：清理过程对CPU影响很小
- **I/O影响**：清理时会遍历文件系统，但频率较低
- **内存使用**：清理器本身内存占用极小
- **并发安全**：使用守护线程，不影响主程序运行

## 故障排除

### 清理器未启动
- 检查 `tmp_cleanup_enabled` 是否为 `true`
- 查看启动日志是否有错误信息

### 文件未被清理
- 确认文件确实超过了 `tmp_file_max_age` 时间
- 检查文件是否正在被其他进程使用
- 查看清理日志中的错误信息

### 磁盘空间仍然不足
- 考虑减少 `tmp_file_max_age` 的值
- 增加 `tmp_cleanup_interval` 的频率
- 手动检查是否有其他大文件占用空间

## 注意事项

1. **配置调整**：修改配置后需要重启应用才能生效
2. **文件安全**：清理器只删除确实过期的文件，不会影响正在使用的文件
3. **目录权限**：确保应用对 `./tmp/` 目录有读写权限
4. **监控建议**：建议定期检查清理日志，确保功能正常工作

## 相关文件

- `common/tmp_cleaner.py` - 清理器实现
- `config.py` - 配置定义
- `app.py` - 启动集成
- `tests/test_tmp_cleaner.py` - 测试用例