import os
import shutil
import wave
import subprocess
import tempfile
import importlib.util

from common.log import logger

# 检查是否安装了pysilk
pysilk_available = importlib.util.find_spec("pysilk") is not None
if pysilk_available:
    import pysilk

# 检查是否安装了pilk
pilk_available = importlib.util.find_spec("pilk") is not None
if pilk_available:
    import pilk

try:
    from pydub import AudioSegment
except ImportError:
    logger.warning("import pydub failed, wechat voice conversion will not be supported. Try: pip install pydub")

sil_supports = [8000, 12000, 16000, 24000, 32000, 44100, 48000]  # slk转wav时，支持的采样率


def find_closest_sil_supports(sample_rate):
    """
    查找最接近的SILK支持的采样率
    SILK格式支持的采样率: 8000, 12000, 16000, 24000, 32000, 44100, 48000 Hz
    
    Args:
        sample_rate: 目标采样率
    
    Returns:
        最接近的支持采样率
    """
    supported_rates = [8000, 12000, 16000, 24000, 32000, 44100, 48000]
    return min(supported_rates, key=lambda x: abs(x - sample_rate))


def get_pcm_from_wav(wav_path):
    """
    从 wav 文件中读取 pcm

    :param wav_path: wav 文件路径
    :returns: pcm 数据
    """
    wav = wave.open(wav_path, "rb")
    return wav.readframes(wav.getnframes())


def any_to_mp3(any_path, mp3_path, target_duration=None):
    """
    把任意格式转成mp3文件 - 优化音质版本

    Args:
        any_path: 输入文件路径
        mp3_path: 输出的mp3文件路径
        target_duration: 目标时长（秒），如果提供，将调整输出MP3的时长
    """
    try:
        # 如果已经是mp3格式，直接复制
        if any_path.endswith(".mp3"):
            shutil.copy2(any_path, mp3_path)
            return

        # 如果是silk格式，优先使用pysilk转换（更好的音质）
        if any_path.endswith((".sil", ".silk", ".slk")):
            try:
                # 读取SILK数据
                with open(any_path, "rb") as f:
                    silk_data = f.read()
                
                # 先使用默认采样率解码
                target_sample_rate = 24000  # 默认采样率
                pcm_data = pysilk.decode(silk_data, sample_rate=target_sample_rate)
                
                # 使用pydub将PCM数据转换为MP3
                audio = AudioSegment(
                    pcm_data,
                    sample_width=2,      # 16-bit PCM
                    frame_rate=target_sample_rate,  # 使用相同的采样率
                    channels=1           # 单声道
                )
                
                # 如果指定了目标时长，调整音频时长
                if target_duration and target_duration > 0:
                    current_duration = len(audio) / 1000.0  # 当前时长（秒）
                    if abs(current_duration - target_duration) > 0.5:  # 如果差异超过0.5秒
                        logger.info(f"[音频转换] 调整音频时长: 当前={current_duration:.2f}秒, 目标={target_duration:.2f}秒")
                        # 计算需要的采样率调整比例
                        ratio = current_duration / target_duration
                        # 直接裁剪音频到目标时长
                        if current_duration > target_duration:
                            # 如果当前音频比目标时长长，直接截取
                            target_ms = int(target_duration * 1000)
                            audio = audio[:target_ms]
                            logger.info(f"[音频转换] 裁剪音频到目标时长: {target_duration:.2f}秒")
                
                # 增加音量，改善听觉效果
                audio = audio + 6  # 增加 6 dB
                
                # 导出为MP3，使用较高比特率
                audio.export(mp3_path, format="mp3", bitrate="192k")
                
                # 检查生成的MP3时长是否接近目标时长
                if target_duration and target_duration > 0:
                    mp3_audio = AudioSegment.from_file(mp3_path)
                    actual_duration = len(mp3_audio) / 1000.0
                    logger.info(f"[音频转换] SILK->MP3转换完成: 目标时长={target_duration:.2f}秒, 实际时长={actual_duration:.2f}秒")
                else:
                    logger.info(f"[音频转换] SILK->MP3转换完成: {any_path} -> {mp3_path} (pysilk)")
                
                return
                
            except Exception as pysilk_error:
                logger.warning(f"[音频转换] pysilk转换失败，尝试pilk: {pysilk_error}")
                # 如果pysilk失败，回退到pilk方式
                try:
                    pcm_path = any_path + '.pcm'
                    
                    # 使用pilk解码SILK文件
                    pilk.decode(any_path, pcm_path)
                    
                    # 使用pydub把PCM转成MP3
                    audio = AudioSegment.from_raw(pcm_path, format="raw",
                                                frame_rate=24000,  # 使用固定采样率
                                                channels=1,
                                                sample_width=2)
                    
                    # 如果指定了目标时长，调整音频时长
                    if target_duration and target_duration > 0:
                        current_duration = len(audio) / 1000.0  # 当前时长（秒）
                        if abs(current_duration - target_duration) > 0.5:  # 如果差异超过0.5秒
                            logger.info(f"[音频转换] 调整音频时长: 当前={current_duration:.2f}秒, 目标={target_duration:.2f}秒 (pilk)")
                            # 直接裁剪音频到目标时长
                            if current_duration > target_duration:
                                # 如果当前音频比目标时长长，直接截取
                                target_ms = int(target_duration * 1000)
                                audio = audio[:target_ms]
                                logger.info(f"[音频转换] 裁剪音频到目标时长: {target_duration:.2f}秒 (pilk)")
                    
                    # 增加音量
                    audio = audio + 6  # 增加 6 dB
                    
                    # 导出为高质量MP3
                    audio.export(mp3_path, format="mp3", bitrate="160k")

                    # 清理临时PCM文件
                    os.remove(pcm_path)
                    
                    # 检查生成的MP3时长
                    if target_duration and target_duration > 0:
                        mp3_audio = AudioSegment.from_file(mp3_path)
                        actual_duration = len(mp3_audio) / 1000.0
                        logger.info(f"[音频转换] SILK->MP3转换完成: 目标时长={target_duration:.2f}秒, 实际时长={actual_duration:.2f}秒 (pilk)")
                    else:
                        logger.info(f"[音频转换] SILK->MP3转换完成: {any_path} -> {mp3_path} (pilk)")
                    
                    return
                    
                except Exception as pilk_error:
                    logger.error(f"[音频转换] pilk转换也失败: {pilk_error}")
                    raise

        # 其他格式使用pydub转换，优化输出质量
        audio = AudioSegment.from_file(any_path)
        
        # 标准化音频格式
        audio = audio.set_channels(1)  # 转为单声道
        
        # 如果指定了目标时长，调整音频时长
        if target_duration and target_duration > 0:
            current_duration = len(audio) / 1000.0  # 当前时长（秒）
            if abs(current_duration - target_duration) > 0.5:  # 如果差异超过0.5秒
                logger.info(f"[音频转换] 调整音频时长: 当前={current_duration:.2f}秒, 目标={target_duration:.2f}秒")
                # 直接裁剪音频到目标时长
                if current_duration > target_duration:
                    # 如果当前音频比目标时长长，直接截取
                    target_ms = int(target_duration * 1000)
                    audio = audio[:target_ms]
                    logger.info(f"[音频转换] 裁剪音频到目标时长: {target_duration:.2f}秒")
        
        # 确保采样率不低于24000Hz以保证音质
        if audio.frame_rate < 24000:
            audio = audio.set_frame_rate(24000)
            
        # 轻微增加音量
        audio = audio + 5  # 增加 5 dB
        
        # 导出为高质量MP3
        audio.export(mp3_path, format="mp3", bitrate="160k")
        
        # 检查生成的MP3时长
        if target_duration and target_duration > 0:
            mp3_audio = AudioSegment.from_file(mp3_path)
            actual_duration = len(mp3_audio) / 1000.0
            logger.info(f"[音频转换] 转换完成: 目标时长={target_duration:.2f}秒, 实际时长={actual_duration:.2f}秒")
        else:
            logger.info(f"[音频转换] 转换完成: {any_path} -> {mp3_path}")

    except Exception as e:
        logger.error(f"转换文件到mp3失败: {str(e)}")
        raise


def any_to_wav(any_path, wav_path):
    """
    把任意格式转成wav文件
    """
    if any_path.endswith(".wav"):
        shutil.copy2(any_path, wav_path)
        return
    if any_path.endswith(".sil") or any_path.endswith(".silk") or any_path.endswith(".slk"):
        return sil_to_wav(any_path, wav_path)
    audio = AudioSegment.from_file(any_path)
    audio.set_frame_rate(8000)    # 百度语音转写支持8000采样率, pcm_s16le, 单通道语音识别
    audio.set_channels(1)
    audio.export(wav_path, format="wav", codec='pcm_s16le')


def any_to_sil(any_path, sil_path):
    """
    把任意格式转成sil文件 - 优化音质版本
    """
    if any_path.endswith(".sil") or any_path.endswith(".silk") or any_path.endswith(".slk"):
        shutil.copy2(any_path, sil_path)
        return 10000

    audio = AudioSegment.from_file(any_path)

    # 优化音质设置：强制使用48000Hz采样率以获得最佳音质
    # SILK支持的最高采样率，提供最佳音质
    target_rate = 48000

    logger.info(f"[SILK转换] 原始采样率: {audio.frame_rate}Hz -> 目标采样率: {target_rate}Hz")

    # 转换为高质量PCM格式
    # 1. 设置为单声道（微信语音要求）
    audio = audio.set_channels(1)
    # 2. 设置为16位采样深度
    audio = audio.set_sample_width(2)
    # 3. 设置为48000Hz高采样率
    audio = audio.set_frame_rate(target_rate)

    # 获取PCM数据
    wav_data = audio.raw_data

    # 使用pysilk编码为SILK格式
    silk_data = pysilk.encode(wav_data, data_rate=target_rate, sample_rate=target_rate)

    with open(sil_path, "wb") as f:
        f.write(silk_data)

    logger.info(f"[SILK转换] 转换完成: {any_path} -> {sil_path}, 采样率: {target_rate}Hz")
    return audio.duration_seconds * 1000

def mp3_to_silk(mp3_path: str, silk_path: str) -> int:
    """Convert MP3 file to SILK format - 高音质版本
    Args:
        mp3_path: Path to input MP3 file
        silk_path: Path to output SILK file
    Returns:
        Duration of the SILK file in milliseconds
    """
    # 加载MP3文件
    audio = AudioSegment.from_file(mp3_path)

    # 优化音质设置：使用48000Hz采样率
    target_rate = 48000
    logger.info(f"[SILK转换] MP3原始采样率: {audio.frame_rate}Hz -> 目标采样率: {target_rate}Hz")

    # 转换为高质量格式
    audio = audio.set_channels(1)  # 单声道
    audio = audio.set_sample_width(2)  # 16位
    audio = audio.set_frame_rate(target_rate)  # 48000Hz高采样率

    # 导出为PCM格式
    pcm_path = os.path.splitext(mp3_path)[0] + '.pcm'
    audio.export(pcm_path, format='s16le')

    # 使用pilk转换为SILK格式（高采样率）
    pilk.encode(pcm_path, silk_path, pcm_rate=target_rate, tencent=True)

    # 清理临时PCM文件
    os.remove(pcm_path)

    # 获取SILK文件时长
    duration = pilk.get_duration(silk_path)
    logger.info(f"[SILK转换] MP3转换完成: {mp3_path} -> {silk_path}, 采样率: {target_rate}Hz")
    return duration

def any_to_amr(any_path, amr_path):
    """
    把任意格式转成amr文件
    """
    if any_path.endswith(".amr"):
        shutil.copy2(any_path, amr_path)
        return
    if any_path.endswith(".sil") or any_path.endswith(".silk") or any_path.endswith(".slk"):
        raise NotImplementedError("Not support file type: {}".format(any_path))
    audio = AudioSegment.from_file(any_path)
    audio = audio.set_frame_rate(8000)  # only support 8000
    audio.export(amr_path, format="amr")
    return audio.duration_seconds * 1000

# TODO: 删除pysilk，改用pilk
def sil_to_wav(silk_path, wav_path, rate: int = 24000):
    """
    silk 文件转 wav
    """
    wav_data = pysilk.decode_file(silk_path, to_wav=True, sample_rate=rate)
    with open(wav_path, "wb") as f:
        f.write(wav_data)


def split_audio(file_path, max_segment_length_ms=60000):
    """
    分割音频文件
    """
    audio = AudioSegment.from_file(file_path)
    audio_length_ms = len(audio)
    if audio_length_ms <= max_segment_length_ms:
        return audio_length_ms, [file_path]
    segments = []
    for start_ms in range(0, audio_length_ms, max_segment_length_ms):
        end_ms = min(audio_length_ms, start_ms + max_segment_length_ms)
        segment = audio[start_ms:end_ms]
        segments.append(segment)
    file_prefix = file_path[: file_path.rindex(".")]
    format = file_path[file_path.rindex(".") + 1 :]
    files = []
    for i, segment in enumerate(segments):
        path = f"{file_prefix}_{i+1}" + f".{format}"
        segment.export(path, format=format)
        files.append(path)
    return audio_length_ms, files