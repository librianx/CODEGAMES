
import base64
import io
import os
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterator, Optional

import requests


# =========================
# Common helpers
# =========================

def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    if value is None:
        return default
    value = str(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "y", "on")


def _powershell_exe() -> str:
    return _env("VIVY_POWERSHELL_EXE", "powershell")


def _run_powershell(script: str, timeout: int = 30) -> str:
    tmp_dir = Path(tempfile.gettempdir()) / "vivy_voice_ps"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / "vivy_speech.ps1"
    script_path.write_text(script, encoding="utf-8-sig")

    proc = subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        detail = stderr or stdout or f"PowerShell 退出码：{proc.returncode}"
        raise RuntimeError(detail)

    return stdout


# =========================
# STT (Windows system speech recognition)
# =========================

def recognize_once(timeout_seconds: int = 8, culture: Optional[str] = None) -> str:
    culture = (culture or _env("VIVY_STT_CULTURE", "")).strip()
    timeout_seconds = max(3, int(timeout_seconds))

    culture_ps = culture.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$culture = '{culture_ps}'
$installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if ($null -eq $installed -or $installed.Count -eq 0) {{
    throw 'NO_STT_ENGINE'
}}

$chosen = $null
if (-not [string]::IsNullOrWhiteSpace($culture)) {{
    foreach ($r in $installed) {{
        if ($r.Culture.Name -eq $culture -or $r.Culture.TwoLetterISOLanguageName -eq $culture) {{
            $chosen = $r
            break
        }}
    }}
}}
if ($null -eq $chosen) {{
    $chosen = $installed[0]
}}

$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($chosen)
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($grammar)
$engine.SetInputToDefaultAudioDevice()

$result = $engine.Recognize([TimeSpan]::FromSeconds({timeout_seconds}))
if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) {{
    throw 'NO_STT_TEXT'
}}

Write-Output $result.Text
"""
    try:
        text = _run_powershell(script, timeout=timeout_seconds + 8).strip()
    except RuntimeError as e:
        msg = str(e)
        if "NO_STT_ENGINE" in msg:
            raise RuntimeError("当前系统没有可用的 Windows 语音识别引擎。请先在 Windows 设置里安装语音识别/语音包。")
        if "NO_STT_TEXT" in msg:
            raise RuntimeError("没有识别到有效文字。请确认麦克风可用，并在点击后尽快开始说话。")
        raise
    if not text:
        raise RuntimeError("没有识别到有效文字。")
    return text


# =========================
# TTS helpers
# =========================

_tts_lock = threading.Lock()


def _looks_like_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _write_bytes_temp(data: bytes, suffix: str = ".wav") -> str:
    tmp_dir = Path(tempfile.gettempdir()) / "vivy_gsv_audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="vivy_", suffix=suffix, dir=str(tmp_dir))
    os.close(fd)
    Path(path).write_bytes(data)
    return path


def _play_wav_file_blocking(path: str):
    if os.name != "nt":
        raise RuntimeError("当前环境不是 Windows，无法直接用系统方式播放 wav。")
    import winsound

    winsound.PlaySound(path, winsound.SND_FILENAME)


def _normalize_tts_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t

    # 统一 VIVY 的读法，避免按字母逐个读出
    t = t.replace("VIVY", "ヴィヴィ")
    t = t.replace("Vivy", "ヴィヴィ")
    t = t.replace("vivy", "ヴィヴィ")

    return t


def _system_tts(text: str):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    voice_name = _env("VIVY_TTS_VOICE_NAME", "").replace("'", "''")
    rate = int(_env("VIVY_TTS_RATE", "0"))
    volume = int(float(_env("VIVY_TTS_VOLUME", "1.0")) * 100)
    volume = max(0, min(volume, 100))

    text_b64 = base64.b64encode(clean_text.encode("utf-8")).decode("ascii")

    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = {rate}
$synth.Volume = {volume}
$voiceName = '{voice_name}'
if (-not [string]::IsNullOrWhiteSpace($voiceName)) {{
    try {{
        $synth.SelectVoice($voiceName)
    }} catch {{
        # 找不到指定音色时保持默认
    }}
}}
$textBytes = [System.Convert]::FromBase64String('{text_b64}')
$text = [System.Text.Encoding]::UTF8.GetString($textBytes)
$synth.Speak($text)
"""
    _run_powershell(script, timeout=max(30, min(180, len(clean_text) // 2 + 10)))


def _gsv_candidate_urls() -> list[str]:
    raw = (
        _env("VIVY_GSV_TTS_URL")
        or _env("VIVY_GPTSOVITS_URL")
        or _env("VIVY_TTS_URL")
        or "http://127.0.0.1:9872/tts"
    ).rstrip("/")

    if not raw:
        raw = "http://127.0.0.1:9872/tts"

    out: list[str] = []
    for url in (
        raw,
        raw + "/tts" if not raw.endswith("/tts") else raw[:-4],
    ):
        url = url.rstrip("/")
        if url and url not in out:
            out.append(url)
    return out


def _build_gsv_payload(text: str) -> dict:
    streaming_pref = _env("VIVY_GSV_STREAMING_MODE", "3").strip()
    try:
        if streaming_pref.lower() in ("true", "false"):
            streaming_mode = streaming_pref.lower() == "true"
        else:
            streaming_mode = int(streaming_pref)
    except Exception:
        streaming_mode = 3

    payload = {
        "text": text,
        "text_lang": _env("VIVY_GSV_TEXT_LANG", "zh") or "zh",
        "media_type": _env("VIVY_GSV_MEDIA_TYPE", "wav") or "wav",
        "streaming_mode": streaming_mode,
        "fragment_interval": float(_env("VIVY_GSV_FRAGMENT_INTERVAL", "0.05") or 0.05),
        "overlap_length": int(_env("VIVY_GSV_OVERLAP_LENGTH", "2") or 2),
        "min_chunk_length": int(_env("VIVY_GSV_MIN_CHUNK_LENGTH", "8") or 8),
    }

    ref_audio_path = _env("VIVY_GSV_REF_AUDIO", "")
    prompt_text = _env("VIVY_GSV_PROMPT_TEXT", "")
    prompt_lang = _env("VIVY_GSV_PROMPT_LANG", "zh") or "zh"
    text_split_method = _env("VIVY_GSV_TEXT_SPLIT_METHOD", "cut0") or "cut0"
    speed_factor = _env("VIVY_GSV_SPEED_FACTOR", "")
    top_k = _env("VIVY_GSV_TOP_K", "")
    top_p = _env("VIVY_GSV_TOP_P", "")
    temperature = _env("VIVY_GSV_TEMPERATURE", "")
    batch_size = _env("VIVY_GSV_BATCH_SIZE", "")
    batch_threshold = _env("VIVY_GSV_BATCH_THRESHOLD", "")
    split_bucket = _env("VIVY_GSV_SPLIT_BUCKET", "")
    parallel_infer = _env("VIVY_GSV_PARALLEL_INFER", "")
    repetition_penalty = _env("VIVY_GSV_REPETITION_PENALTY", "")

    if ref_audio_path:
        payload["ref_audio_path"] = ref_audio_path
    if prompt_text:
        payload["prompt_text"] = prompt_text
    if prompt_lang:
        payload["prompt_lang"] = prompt_lang
    if text_split_method:
        payload["text_split_method"] = text_split_method
    if speed_factor:
        try:
            payload["speed_factor"] = float(speed_factor)
        except Exception:
            pass
    if top_k:
        try:
            payload["top_k"] = int(top_k)
        except Exception:
            pass
    if top_p:
        try:
            payload["top_p"] = float(top_p)
        except Exception:
            pass
    if temperature:
        try:
            payload["temperature"] = float(temperature)
        except Exception:
            pass
    if batch_size:
        try:
            payload["batch_size"] = int(batch_size)
        except Exception:
            pass
    if batch_threshold:
        try:
            payload["batch_threshold"] = float(batch_threshold)
        except Exception:
            pass
    if split_bucket:
        payload["split_bucket"] = split_bucket.lower() in ("1", "true", "yes", "y", "on")
    if parallel_infer:
        payload["parallel_infer"] = parallel_infer.lower() in ("1", "true", "yes", "y", "on")
    if repetition_penalty:
        try:
            payload["repetition_penalty"] = float(repetition_penalty)
        except Exception:
            pass

    return payload


def _response_error_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data)
    except Exception:
        txt = (resp.text or "").strip()
        if txt:
            return txt[:500]
        return f"HTTP {resp.status_code}"


def _parse_streaming_wav_header(data: bytes):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None

    offset = 12
    channels = None
    sample_rate = None
    bits_per_sample = None
    data_offset = None

    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        body_start = offset + 8
        body_end = body_start + chunk_size

        if body_end > len(data):
            return None

        if chunk_id == b"fmt " and chunk_size >= 16:
            audio_format, channels, sample_rate = struct.unpack("<HHI", data[body_start:body_start + 8])
            bits_per_sample = struct.unpack("<H", data[body_start + 14:body_start + 16])[0]
            if audio_format != 1:
                raise RuntimeError(f"暂只支持 PCM 流式播放，当前 audio_format={audio_format}")
        elif chunk_id == b"data":
            data_offset = body_start
            break

        offset = body_end + (chunk_size % 2)

    if channels and sample_rate and bits_per_sample and data_offset is not None:
        return channels, sample_rate, bits_per_sample, data_offset
    return None


class _WaveOutPlayer:
    def __init__(self, channels: int, sample_rate: int, bits_per_sample: int):
        if os.name != "nt":
            raise RuntimeError("当前环境不是 Windows，无法使用 waveOut 流式播放。")
        if bits_per_sample not in (8, 16):
            raise RuntimeError(f"暂只支持 8/16 bit PCM，当前为 {bits_per_sample}")

        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._winmm = ctypes.WinDLL("winmm")
        self._pending = []
        self._closed = False

        class WAVEFORMATEX(ctypes.Structure):
            _fields_ = [
                ("wFormatTag", wintypes.WORD),
                ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD),
            ]

        class WAVEHDR(ctypes.Structure):
            _fields_ = [
                ("lpData", ctypes.c_void_p),
                ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD),
                ("dwUser", ctypes.c_void_p),
                ("dwFlags", wintypes.DWORD),
                ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p),
            ]

        self.WAVEHDR = WAVEHDR
        self.WAVEFORMATEX = WAVEFORMATEX
        self.WHDR_DONE = 0x00000001
        self.CALLBACK_NULL = 0
        self.WAVE_MAPPER = 0xFFFFFFFF
        self.handle = wintypes.HANDLE()

        block_align = channels * bits_per_sample // 8
        avg_bytes = sample_rate * block_align
        self._fmt = WAVEFORMATEX(
            wFormatTag=1,
            nChannels=channels,
            nSamplesPerSec=sample_rate,
            nAvgBytesPerSec=avg_bytes,
            nBlockAlign=block_align,
            wBitsPerSample=bits_per_sample,
            cbSize=0,
        )

        res = self._winmm.waveOutOpen(
            self._ctypes.byref(self.handle),
            self.WAVE_MAPPER,
            self._ctypes.byref(self._fmt),
            0,
            0,
            self.CALLBACK_NULL,
        )
        if res != 0:
            raise RuntimeError(f"waveOutOpen 失败，错误码={res}")

    def write(self, pcm_bytes: bytes):
        if self._closed or not pcm_bytes:
            return
        c = self._ctypes
        buf = c.create_string_buffer(pcm_bytes)
        hdr = self.WAVEHDR()
        hdr.lpData = c.cast(buf, c.c_void_p)
        hdr.dwBufferLength = len(pcm_bytes)
        hdr.dwBytesRecorded = 0
        hdr.dwUser = None
        hdr.dwFlags = 0
        hdr.dwLoops = 0
        hdr.lpNext = None
        hdr.reserved = None

        res = self._winmm.waveOutPrepareHeader(self.handle, c.byref(hdr), c.sizeof(hdr))
        if res != 0:
            raise RuntimeError(f"waveOutPrepareHeader 失败，错误码={res}")
        res = self._winmm.waveOutWrite(self.handle, c.byref(hdr), c.sizeof(hdr))
        if res != 0:
            self._winmm.waveOutUnprepareHeader(self.handle, c.byref(hdr), c.sizeof(hdr))
            raise RuntimeError(f"waveOutWrite 失败，错误码={res}")

        self._pending.append((buf, hdr))
        self._cleanup_done()

    def _cleanup_done(self):
        c = self._ctypes
        keep = []
        for buf, hdr in self._pending:
            if hdr.dwFlags & self.WHDR_DONE:
                self._winmm.waveOutUnprepareHeader(self.handle, c.byref(hdr), c.sizeof(hdr))
            else:
                keep.append((buf, hdr))
        self._pending = keep

    def drain(self):
        while self._pending:
            self._cleanup_done()
            time.sleep(0.01)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.drain()
        finally:
            try:
                self._winmm.waveOutClose(self.handle)
            except Exception:
                pass


def _iter_gsv_stream_bytes(text: str) -> Iterator[bytes]:
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    payload = _build_gsv_payload(clean_text)
    timeout = int(_env("VIVY_GSV_TIMEOUT", "120"))
    errors: list[str] = []

    for url in _gsv_candidate_urls():
        headers = {"Accept": "*/*"}
        try:
            with requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True) as resp:
                if not resp.ok:
                    errors.append(f"POST {url} -> {_response_error_text(resp)}")
                    continue

                content_type = (resp.headers.get("Content-Type") or "").lower()
                if "json" in content_type:
                    errors.append(f"POST {url} -> {_response_error_text(resp)}")
                    continue

                yielded = False
                for chunk in resp.iter_content(chunk_size=4096):
                    if not chunk:
                        continue
                    yielded = True
                    yield chunk
                if yielded:
                    return
                errors.append(f"POST {url} -> 服务返回了空音频流")
        except Exception as e:
            errors.append(f"POST {url} -> {e}")

    joined = "；".join(errors[-4:]) if errors else "GPT-SoVITS 请求失败"
    raise RuntimeError(joined)


def _play_gsv_streaming(text: str):
    header_buf = bytearray()
    player = None
    try:
        for chunk in _iter_gsv_stream_bytes(text):
            if player is None:
                header_buf.extend(chunk)
                parsed = _parse_streaming_wav_header(header_buf)
                if parsed is None:
                    # header 还没收全，继续攒
                    if len(header_buf) > 65536:
                        raise RuntimeError("流式音频头解析失败：收到的数据过多仍未识别 wav header。")
                    continue

                channels, sample_rate, bits_per_sample, data_offset = parsed
                player = _WaveOutPlayer(channels, sample_rate, bits_per_sample)
                initial_pcm = bytes(header_buf[data_offset:])
                header_buf.clear()
                if initial_pcm:
                    player.write(initial_pcm)
            else:
                player.write(chunk)

        if player is None:
            raise RuntimeError("流式音频没有返回可播放的 WAV 头。")
        player.drain()
    finally:
        if player is not None:
            player.close()


def _request_gsv_audio(text: str) -> bytes:
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return b""

    payload = _build_gsv_payload(clean_text)
    # 明确关闭流式，保留给非流式降级模式
    payload["streaming_mode"] = False

    timeout = int(_env("VIVY_GSV_TIMEOUT", "120"))
    errors: list[str] = []

    for url in _gsv_candidate_urls():
        headers = {"Accept": "*/*"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            ctype = (resp.headers.get("Content-Type") or "").lower()

            if resp.ok and (ctype.startswith("audio/") or _looks_like_wav(resp.content)):
                return resp.content

            if (
                resp.ok
                and resp.content
                and payload.get("media_type") == "wav"
                and _looks_like_wav(resp.content)
            ):
                return resp.content

            errors.append(f"POST {url} -> {_response_error_text(resp)}")
        except Exception as e:
            errors.append(f"POST {url} -> {e}")

    ref_audio = (payload.get("ref_audio_path") or "").strip()
    prompt_text = (payload.get("prompt_text") or "").strip()

    if not ref_audio or not prompt_text:
        extra = (
            "当前未设置 VIVY_GSV_REF_AUDIO 或 VIVY_GSV_PROMPT_TEXT。"
            "很多 GPT-SoVITS /tts 接口需要这两个参数。"
        )
    else:
        extra = "请确认 GPT-SoVITS 服务已启动、端口正确、并且模型/参考音频已经可用。"

    joined = "；".join(errors[-4:]) if errors else "GPT-SoVITS 请求失败"
    raise RuntimeError(joined + "；" + extra)


# =========================
# Public TTS API
# =========================

def speak_text(text: str):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    mode = _env("VIVY_TTS_MODE", "gptsovits").lower()
    fallback_system = _env_bool("VIVY_TTS_FALLBACK_SYSTEM", False)
    true_stream = _env_bool("VIVY_GSV_TRUE_STREAMING", True)

    with _tts_lock:
        if mode == "system":
            _system_tts(clean_text)
            return

        try:
            if true_stream:
                _play_gsv_streaming(clean_text)
                return

            audio = _request_gsv_audio(clean_text)
            if not audio:
                raise RuntimeError("GPT-SoVITS 没有返回音频数据。")
            wav_path = _write_bytes_temp(audio, suffix=".wav")
            try:
                _play_wav_file_blocking(wav_path)
            finally:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
        except Exception:
            if fallback_system:
                _system_tts(clean_text)
            else:
                raise


def speak_text_async(text: str):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    def _worker():
        try:
            speak_text(clean_text)
        except Exception as e:
            print(f"[VIVY TTS] {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
