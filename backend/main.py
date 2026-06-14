from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
from doubao_asr import recognize_wav_file
import os
import wave
import uvicorn
import requests

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
load_dotenv(ROOT_DIR / ".env")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_DIR = BACKEND_DIR / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

WAV_FILE_PATH = AUDIO_DIR / "test.wav"

PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2

AVAILABLE_MODELS = {
    "pro": {
        "id": "doubao-seed-2-0-pro-260215",
        "name": "Pro",
        "desc": "最强效果，适合最终展示"
    },
    "lite": {
        "id": "doubao-seed-2-0-lite-260428",
        "name": "Lite",
        "desc": "日常开发推荐，速度和效果均衡"
    },
    "mini": {
        "id": "doubao-seed-2-0-mini-260428",
        "name": "Mini",
        "desc": "最快最省，适合测试"
    }
}


class ChatRequest(BaseModel):
    text: str
    model: str = "lite"


@app.get("/")
def home():
    return {"message": "林曦后端正在运行"}


@app.get("/models")
def get_models():
    return {
        "ok": True,
        "models": AVAILABLE_MODELS
    }


def save_wav_from_pcm(pcm_chunks: list[bytes], output_path: Path):
    pcm_data = b"".join(pcm_chunks)

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(PCM_CHANNELS)
        wav_file.setsampwidth(PCM_SAMPLE_WIDTH)
        wav_file.setframerate(PCM_SAMPLE_RATE)
        wav_file.writeframes(pcm_data)


def extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]

    outputs = data.get("output", [])
    texts = []

    for item in outputs:
        content_list = item.get("content", [])
        for content in content_list:
            if content.get("text"):
                texts.append(content["text"])
            elif content.get("value"):
                texts.append(content["value"])

    if texts:
        return "\n".join(texts)

    return f"林曦没有拿到有效回复，原始返回：{data}"


@app.post("/chat")
def chat(req: ChatRequest):
    api_key = os.getenv("ARK_API_KEY")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

    model_key = req.model
    if model_key not in AVAILABLE_MODELS:
        model_key = "lite"

    model_id = AVAILABLE_MODELS[model_key]["id"]

    if not api_key:
        return {
            "ok": False,
            "reply": "没有读取到 ARK_API_KEY，请检查项目根目录的 .env 文件。",
            "model": model_id
        }

    url = f"{base_url}/responses"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "你叫林曦，是一个运行在用户电脑桌面上的 AI 桌宠。"
        "你的语气自然、简短、亲切，不要像客服，不要长篇大论。"
        "你现在处于早期原型阶段，已经具备文字聊天能力，后续会接入语音、音色克隆、记忆海、数据海和桌面形象。"
        "回答时尽量像一个真实桌宠角色，而不是普通助手。"
    )

    payload = {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"{system_prompt}\n\n用户说：{req.text}\n\n请以林曦的身份回复："
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()

        data = response.json()
        reply = extract_response_text(data)

        return {
            "ok": True,
            "reply": reply,
            "model": model_id,
            "model_key": model_key
        }

    except requests.exceptions.HTTPError as e:
        return {
            "ok": False,
            "reply": f"LLM HTTP 请求失败：{str(e)}。请检查 API Key、模型 ID 和接口地址。",
            "model": model_id
        }
    except Exception as e:
        return {
            "ok": False,
            "reply": f"LLM 调用异常：{str(e)}",
            "model": model_id
        }


@app.post("/asr/recognize-last")
def recognize_last_audio():
    if not WAV_FILE_PATH.exists():
        return {
            "ok": False,
            "text": "",
            "message": "还没有找到录音文件，请先点击“开始录音”，说话后再点击“结束录音”。"
        }

    if WAV_FILE_PATH.stat().st_size <= 44:
        return {
            "ok": False,
            "text": "",
            "message": "录音文件太小，可能没有录到声音，请重新录一段。"
        }

    try:
        text = recognize_wav_file(WAV_FILE_PATH)
        return {
            "ok": True,
            "text": text,
            "message": "ASR 识别完成"
        }
    except Exception as e:
        return {
            "ok": False,
            "text": "",
            "message": f"ASR 识别失败：{str(e)}"
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("林曦前端已连接")

    pcm_chunks: list[bytes] = []
    pcm_chunk_count = 0

    try:
        while True:
            message = await websocket.receive()

            if "text" in message and message["text"] is not None:
                text = message["text"]
                print("收到前端文本：", text)

                if text == "START_PCM":
                    pcm_chunks = []
                    pcm_chunk_count = 0
                    print("开始接收 PCM 音频流")
                    await websocket.send_text("后端开始接收 PCM 音频流")

                elif text == "STOP_PCM":
                    save_wav_from_pcm(pcm_chunks, WAV_FILE_PATH)
                    print("PCM 已保存为 WAV：", WAV_FILE_PATH)
                    await websocket.send_text(f"PCM 已保存为 WAV：{WAV_FILE_PATH}")

                else:
                    await websocket.send_text(f"后端收到文本：{text}")

            elif "bytes" in message and message["bytes"] is not None:
                pcm_data = message["bytes"]
                pcm_chunks.append(pcm_data)
                pcm_chunk_count += 1

                print(
                    f"收到 PCM 音频片段：第 {pcm_chunk_count} 段，"
                    f"大小 {len(pcm_data)} bytes"
                )

                if pcm_chunk_count % 20 == 0:
                    await websocket.send_text(
                        f"后端已收到 {pcm_chunk_count} 段 PCM 音频"
                    )

    except Exception as e:
        print("前端连接已断开：", e)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
