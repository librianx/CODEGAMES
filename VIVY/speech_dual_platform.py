
import base64
import io
import os
import sys
import struct
import subprocess
import tempfile
import threading
import time
import wave
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


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _powershell_exe() -> str:
    return _env("VIVY_POWERSHELL_EXE", "powershell")


def _run_powershell(script: str, timeout: int = 30) -> str:
    tmp_dir = Path(tempfile.gettempdir()) / "vivy_voice_ps"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script_path = tmp_dir / "vivy_speech.ps1"
    script_path.write_text(script, encoding="utf-8-sig")

    try:
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
    except subprocess.TimeoutExpired:
        raise RuntimeError("PS_TIMEOUT")

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        detail = stderr or stdout or f"PowerShell exit code: {proc.returncode}"
        raise RuntimeError(detail)

    return stdout


# =========================
# STT (Windows voice typing handoff)
# =========================

def trigger_system_voice_dictation() -> bool:
    if os.name == "nt":
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002
        VK_LWIN = 0x5B
        VK_H = 0x48

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint),
                ("time", ctypes.c_uint),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class _INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_uint), ("u", _INPUTUNION)]

        def _key(vk: int, keyup: bool = False) -> INPUT:
            flags = KEYEVENTF_KEYUP if keyup else 0
            return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0))

        sequence = (_key(VK_LWIN, False), _key(VK_H, False), _key(VK_H, True), _key(VK_LWIN, True))
        payload = (INPUT * len(sequence))(*sequence)
        sent = user32.SendInput(len(sequence), payload, ctypes.sizeof(INPUT))
        if sent != len(sequence):
            raise RuntimeError("Failed to trigger Windows voice typing.")
        return True

    if sys.platform == "darwin":
        return False

    return False


def trigger_windows_voice_typing() -> None:
    started = trigger_system_voice_dictation()
    if not started:
        raise RuntimeError("System voice dictation is not auto-triggered on this platform.")


# =========================
# TTS helpers
# =========================

_tts_lock = threading.Lock()
_tts_state_lock = threading.Lock()
_tts_current_token = 0
_tts_cancel_event = threading.Event()
_tts_active_proc = None
_tts_active_player = None


def set_speech_token(token: int):
    global _tts_current_token, _tts_cancel_event
    with _tts_state_lock:
        _tts_current_token = int(token)
        _tts_cancel_event = threading.Event()


def _is_cancel_requested(token: Optional[int] = None) -> bool:
    with _tts_state_lock:
        if token is not None and int(token) != _tts_current_token:
            return True
        return _tts_cancel_event.is_set()


def _set_active_proc(proc):
    global _tts_active_proc
    with _tts_state_lock:
        _tts_active_proc = proc


def _clear_active_proc(proc):
    global _tts_active_proc
    with _tts_state_lock:
        if _tts_active_proc is proc:
            _tts_active_proc = None


def _set_active_player(player):
    global _tts_active_player
    with _tts_state_lock:
        _tts_active_player = player


def _clear_active_player(player):
    global _tts_active_player
    with _tts_state_lock:
        if _tts_active_player is player:
            _tts_active_player = None


def stop_speaking():
    global _tts_active_proc, _tts_active_player
    with _tts_state_lock:
        _tts_cancel_event.set()
        proc = _tts_active_proc
        player = _tts_active_player
        _tts_active_proc = None
        _tts_active_player = None

    if player is not None:
        try:
            player.stop()
        except Exception:
            pass
        try:
            player.close()
        except Exception:
            pass

    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
        if getattr(proc, "poll", lambda: None)() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if os.name == "nt":
        try:
            import winsound

            winsound.PlaySound(None, 0)
        except Exception:
            pass


def _looks_like_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _write_bytes_temp(data: bytes, suffix: str = ".wav") -> str:
    tmp_dir = Path(tempfile.gettempdir()) / "vivy_gsv_audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="vivy_", suffix=suffix, dir=str(tmp_dir))
    os.close(fd)
    Path(path).write_bytes(data)
    return path


def _play_wav_file_blocking(path: str, token: Optional[int] = None):
    if os.name == "nt":
        import winsound

        with wave.open(path, "rb") as wav_file:
            frames = wav_file.getnframes()
            sample_rate = max(1, wav_file.getframerate())
            duration = frames / float(sample_rate)

        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        deadline = time.time() + duration + 0.5
        while time.time() < deadline:
            if _is_cancel_requested(token):
                winsound.PlaySound(None, 0)
                return
            time.sleep(0.05)
        winsound.PlaySound(None, 0)
        return

    if sys.platform == "darwin":
        proc = subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _set_active_proc(proc)
        try:
            while proc.poll() is None:
                if _is_cancel_requested(token):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
                time.sleep(0.05)
        finally:
            _clear_active_proc(proc)
        return

    raise RuntimeError("WAV playback is not supported on this platform in the bundled runtime.")


def _normalize_tts_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t

    # 缁熶竴 VIVY 鐨勮娉曪紝閬垮厤鎸夊瓧姣嶉€愪釜璇诲嚭
    t = t.replace("VIVY", "銉淬偅銉淬偅")
    t = t.replace("Vivy", "銉淬偅銉淬偅")
    t = t.replace("vivy", "銉淬偅銉淬偅")

    return t


def _system_tts(text: str, token: Optional[int] = None):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    if sys.platform == "darwin":
        voice_name = _env("VIVY_TTS_VOICE_NAME", "")
        rate = int(_env("VIVY_TTS_RATE", "180"))
        args = ["say", "-r", str(max(90, min(rate, 320)))]
        if voice_name.strip():
            args.extend(["-v", voice_name.strip()])
        args.append(clean_text)
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
        _set_active_proc(proc)
        try:
            deadline = time.time() + max(30, min(180, len(clean_text) // 2 + 10))
            while proc.poll() is None:
                if _is_cancel_requested(token):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
                if time.time() > deadline:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    raise RuntimeError("System TTS playback timed out.")
                time.sleep(0.05)

            stderr = (proc.communicate()[1] or "").strip()
            if proc.returncode != 0 and not _is_cancel_requested(token):
                raise RuntimeError(stderr or f"say exit code: {proc.returncode}")
        finally:
            _clear_active_proc(proc)
        return

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
    }}
}}
$textBytes = [System.Convert]::FromBase64String('{text_b64}')
$text = [System.Text.Encoding]::UTF8.GetString($textBytes)
$synth.Speak($text)
"""
    proc = subprocess.Popen(
        [
            _powershell_exe(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    _set_active_proc(proc)
    try:
        deadline = time.time() + max(30, min(180, len(clean_text) // 2 + 10))
        while proc.poll() is None:
            if _is_cancel_requested(token):
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            if time.time() > deadline:
                try:
                    proc.terminate()
                except Exception:
                    pass
                raise RuntimeError("System TTS playback timed out.")
            time.sleep(0.05)

        stdout, stderr = proc.communicate()
        if proc.returncode != 0 and not _is_cancel_requested(token):
            detail = (stderr or stdout or f"PowerShell exit code: {proc.returncode}").strip()
            raise RuntimeError(detail)
    finally:
        _clear_active_proc(proc)


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
                raise RuntimeError(f"鏆傚彧鏀寔 PCM 娴佸紡鎾斁锛屽綋鍓?audio_format={audio_format}")
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
            raise RuntimeError("Windows is required for waveOut streaming playback.")
        if bits_per_sample not in (8, 16):
            raise RuntimeError(f"鏆傚彧鏀寔 8/16 bit PCM锛屽綋鍓嶄负 {bits_per_sample}")

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
            raise RuntimeError(f"waveOutOpen 澶辫触锛岄敊璇爜={res}")

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
            raise RuntimeError(f"waveOutPrepareHeader 澶辫触锛岄敊璇爜={res}")
        res = self._winmm.waveOutWrite(self.handle, c.byref(hdr), c.sizeof(hdr))
        if res != 0:
            self._winmm.waveOutUnprepareHeader(self.handle, c.byref(hdr), c.sizeof(hdr))
            raise RuntimeError(f"waveOutWrite 澶辫触锛岄敊璇爜={res}")

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

    def stop(self):
        if self._closed:
            return
        try:
            self._winmm.waveOutReset(self.handle)
        except Exception:
            pass
        self._cleanup_done()

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


def _iter_gsv_stream_bytes(text: str, token: Optional[int] = None) -> Iterator[bytes]:
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    payload = _build_gsv_payload(clean_text)
    timeout = int(_env("VIVY_GSV_TIMEOUT", "120"))
    errors: list[str] = []

    for url in _gsv_candidate_urls():
        if _is_cancel_requested(token):
            return
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
                    if _is_cancel_requested(token):
                        return
                    if not chunk:
                        continue
                    yielded = True
                    yield chunk
                if yielded:
                    return
                errors.append(f"POST {url} -> empty audio stream")
        except Exception as e:
            errors.append(f"POST {url} -> {e}")

    joined = " ; ".join(errors[-4:]) if errors else "GPT-SoVITS request failed"
    if _is_cancel_requested(token):
        return
    raise RuntimeError(joined)


def _play_gsv_streaming(text: str, token: Optional[int] = None):
    header_buf = bytearray()
    player = None
    try:
        for chunk in _iter_gsv_stream_bytes(text, token=token):
            if _is_cancel_requested(token):
                return
            if player is None:
                header_buf.extend(chunk)
                parsed = _parse_streaming_wav_header(header_buf)
                if parsed is None:
                    # header 杩樻病鏀跺叏锛岀户缁敀
                    if len(header_buf) > 65536:
                        raise RuntimeError("Streaming audio header parse failed: WAV header was not detected in time.")
                    continue

                channels, sample_rate, bits_per_sample, data_offset = parsed
                player = _WaveOutPlayer(channels, sample_rate, bits_per_sample)
                _set_active_player(player)
                initial_pcm = bytes(header_buf[data_offset:])
                header_buf.clear()
                if initial_pcm:
                    player.write(initial_pcm)
            else:
                player.write(chunk)

        if _is_cancel_requested(token):
            return
        if player is None:
            raise RuntimeError("Streaming audio did not return a playable WAV header.")
        player.drain()
    finally:
        if player is not None:
            _clear_active_player(player)
            player.close()


def _request_gsv_audio(text: str, token: Optional[int] = None) -> bytes:
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return b""

    payload = _build_gsv_payload(clean_text)
    # 鏄庣‘鍏抽棴娴佸紡锛屼繚鐣欑粰闈炴祦寮忛檷绾фā寮?    payload["streaming_mode"] = False

    timeout = int(_env("VIVY_GSV_TIMEOUT", "120"))
    errors: list[str] = []

    for url in _gsv_candidate_urls():
        if _is_cancel_requested(token):
            return b""
        headers = {"Accept": "*/*"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if _is_cancel_requested(token):
                return b""
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
            "Current GPT-SoVITS configuration is missing VIVY_GSV_REF_AUDIO or VIVY_GSV_PROMPT_TEXT. "
            "Many /tts endpoints require both values."
        )
    else:
        extra = (
            "Please confirm the GPT-SoVITS service is running, the port is correct, and the model plus reference audio are available."
        )

    joined = " ; ".join(errors[-4:]) if errors else "GPT-SoVITS request failed"
    if _is_cancel_requested(token):
        return b""
    raise RuntimeError(joined + " ; " + extra)


# =========================
# Public TTS API
# =========================

def speak_text(text: str, token: Optional[int] = None):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    if _is_cancel_requested(token):
        return

    mode = _env("VIVY_TTS_MODE", "gptsovits").lower()
    fallback_system = _env_bool("VIVY_TTS_FALLBACK_SYSTEM", False)
    true_stream = _env_bool("VIVY_GSV_TRUE_STREAMING", True) and os.name == "nt"

    with _tts_lock:
        if _is_cancel_requested(token):
            return
        if mode == "system":
            _system_tts(clean_text, token=token)
            return

        try:
            if true_stream:
                _play_gsv_streaming(clean_text, token=token)
                return

            audio = _request_gsv_audio(clean_text, token=token)
            if not audio:
                return
            wav_path = _write_bytes_temp(audio, suffix=".wav")
            try:
                _play_wav_file_blocking(wav_path, token=token)
            finally:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
        except Exception:
            if _is_cancel_requested(token):
                return
            if fallback_system:
                _system_tts(clean_text, token=token)
            else:
                raise


def speak_text_async(text: str, token: Optional[int] = None):
    clean_text = _normalize_tts_text(text)
    if not clean_text:
        return

    def _worker():
        try:
            speak_text(clean_text, token=token)
        except Exception as e:
            print(f"[VIVY TTS] {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()





