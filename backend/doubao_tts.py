from pathlib import Path
import base64
import json
import os
import uuid
import requests


def iter_json_objects_from_chunked_text(response: requests.Response):
    decoder = json.JSONDecoder()
    buffer = ""

    for chunk in response.iter_content(chunk_size=4096, decode_unicode=True):
        if not chunk:
            continue

        buffer += chunk

        while buffer:
            buffer = buffer.lstrip()

            try:
                item, index = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break

            yield item
            buffer = buffer[index:]


def _synthesize_icl(text: str, output_path: Path, api_key: str, speaker: str, audio_format: str) -> Path:
    """火山引擎 ICL 接口，用于克隆音色（voice_type 以 S_ 开头）。"""
    url = "https://openspeech.bytedance.com/api/v1/tts"

    payload = {
        "app": {
            "cluster": "volcano_icl"
        },
        "user": {
            "uid": "linxi-local-user"
        },
        "audio": {
            "voice_type": speaker,
            "encoding": audio_format,
            "speed_ratio": 1.0
        },
        "request": {
            "reqid": str(uuid.uuid4()).replace("-", ""),
            "text": text,
            "operation": "query"
        }
    }

    headers = {
        "Authorization": f"Bearer; {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=120)

    if response.status_code != 200:
        raise RuntimeError(
            f"ICL TTS HTTP 状态码异常：{response.status_code}，"
            f"返回内容：{response.text[:500]}"
        )

    data = response.json()
    code = data.get("code")
    if code != 3000:
        raise RuntimeError(
            f"ICL TTS 接口返回错误：code={code}，"
            f"message={data.get('message')}，原始返回={data}"
        )

    audio_data = data.get("data", {}).get("audio")
    if not audio_data:
        raise RuntimeError("ICL TTS 没有返回音频数据。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(audio_data))

    return output_path


def synthesize_text_to_audio_file(text: str, output_path: Path, speaker: str | None = None) -> Path:
    api_key = os.getenv("DOUBAO_SPEECH_API_KEY")
    if not api_key:
        raise RuntimeError("没有读取到 DOUBAO_SPEECH_API_KEY，请检查项目根目录的 .env 文件。")

    speaker = speaker or os.getenv("DOUBAO_TTS_SPEAKER", "zh_female_vv_uranus_bigtts")
    audio_format = os.getenv("DOUBAO_TTS_FORMAT", "mp3")

    text = text.strip()
    if not text:
        raise RuntimeError("TTS 输入文本为空。")

    if speaker.startswith("S_"):
        return _synthesize_icl(text, output_path, api_key, speaker, audio_format)

    url = os.getenv(
        "DOUBAO_TTS_URL",
        "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    )
    resource_id = os.getenv("DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0")
    sample_rate = int(os.getenv("DOUBAO_TTS_SAMPLE_RATE", "24000"))

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }

    payload = {
        "user": {
            "uid": "linxi-local-user"
        },
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": audio_format,
                "sample_rate": sample_rate
            }
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=120, stream=True)

    if response.status_code != 200:
        raise RuntimeError(
            f"HTTP 状态码异常：{response.status_code}，"
            f"返回内容：{response.text[:500]}"
        )

    audio_bytes = bytearray()
    last_message = ""

    for item in iter_json_objects_from_chunked_text(response):
        code = item.get("code")
        message = item.get("message", "")
        data = item.get("data")

        if message:
            last_message = message

        if code not in (None, 0, 20000000):
            raise RuntimeError(f"TTS 接口返回错误：code={code}，message={message}，原始返回={item}")

        if data:
            audio_bytes.extend(base64.b64decode(data))

    if not audio_bytes:
        raise RuntimeError(f"没有拿到音频数据。接口最后消息：{last_message}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(audio_bytes))

    return output_path
