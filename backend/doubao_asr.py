from pathlib import Path
import base64
import os
import uuid
import requests


def recognize_wav_file(wav_path: Path) -> str:
    """
    调用豆包语音：大模型录音文件极速版识别 API。
    输入：本地 wav 文件路径。
    输出：识别文本。
    """
    api_key = os.getenv("DOUBAO_SPEECH_API_KEY")
    if not api_key:
        raise RuntimeError("没有读取到 DOUBAO_SPEECH_API_KEY，请检查项目根目录的 .env 文件。")

    if not wav_path.exists():
        raise FileNotFoundError(f"找不到音频文件：{wav_path}")

    url = os.getenv(
        "DOUBAO_ASR_FLASH_URL",
        "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    )

    resource_id = os.getenv("DOUBAO_ASR_RESOURCE_ID", "volc.bigasr.auc_turbo")

    audio_bytes = wav_path.read_bytes()
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }

    payload = {
        "user": {
            "uid": "linxi-local-user"
        },
        "audio": {
            "format": "wav",
            "data": audio_base64
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)

    status_code = response.headers.get("X-Api-Status-Code", "")
    status_message = response.headers.get("X-Api-Message", "")

    try:
        data = response.json()
    except Exception:
        data = {}

    if response.status_code != 200:
        raise RuntimeError(
            f"HTTP 状态码异常：{response.status_code}，"
            f"接口消息：{status_message}，返回内容：{response.text[:500]}"
        )

    if status_code and status_code != "20000000":
        raise RuntimeError(
            f"ASR 状态码异常：{status_code}，"
            f"接口消息：{status_message}，返回内容：{data}"
        )

    text = (
        data.get("result", {}).get("text")
        or data.get("text")
        or data.get("result", {}).get("utterances", [{}])[0].get("text", "")
    )

    if not text:
        raise RuntimeError(f"没有解析到识别文本，原始返回：{data}")

    return text
