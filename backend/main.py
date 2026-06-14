from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from dotenv import load_dotenv
from doubao_asr import recognize_wav_file
from doubao_tts import synthesize_text_to_audio_file
from doubao_stream_asr import DoubaoStreamASR
from linxi_llm import AVAILABLE_MODELS, chat_with_linxi
import json
import wave
import uuid
import uvicorn

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

app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")

WAV_FILE_PATH = AUDIO_DIR / "test.wav"

PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2


class ChatRequest(BaseModel):
    text: str
    model: str = "lite"


class TtsRequest(BaseModel):
    text: str
    speaker: str | None = None


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


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        result = chat_with_linxi(req.text, req.model)
        return result
    except Exception as e:
        return {
            "ok": False,
            "reply": f"LLM 调用异常：{str(e)}",
            "model": "",
            "model_key": req.model
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


@app.post("/tts/speak")
def speak_text(req: TtsRequest):
    text = req.text.strip()

    if not text:
        return {
            "ok": False,
            "audio_url": "",
            "message": "TTS 文本为空，无法合成语音。"
        }

    output_path = AUDIO_DIR / f"linxi_reply_{uuid.uuid4().hex}.mp3"

    try:
        synthesize_text_to_audio_file(text, output_path, req.speaker)

        return {
            "ok": True,
            "audio_url": f"/audio/{output_path.name}",
            "message": "TTS 合成完成"
        }
    except Exception as e:
        return {
            "ok": False,
            "audio_url": "",
            "message": f"TTS 合成失败：{str(e)}"
        }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("林曦前端已连接")

    pcm_chunks: list[bytes] = []
    pcm_chunk_count = 0
    stream_asr: DoubaoStreamASR | None = None

    async def send_json_to_frontend(data: dict):
        await websocket.send_text(json.dumps(data, ensure_ascii=False))

    async def on_stream_asr_text(text: str, is_final: bool, raw: dict):
        if text:
            await send_json_to_frontend({
                "type": "asr_stream",
                "text": text,
                "is_final": is_final
            })
        elif raw.get("error") or raw.get("server_error"):
            await send_json_to_frontend({
                "type": "asr_stream_error",
                "message": str(raw)
            })

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

                    try:
                        stream_asr = DoubaoStreamASR()
                        await stream_asr.start(on_stream_asr_text)
                        await send_json_to_frontend({
                            "type": "asr_stream_status",
                            "message": "流式 ASR 已连接，开始实时字幕"
                        })
                    except Exception as e:
                        stream_asr = None
                        await send_json_to_frontend({
                            "type": "asr_stream_error",
                            "message": f"流式 ASR 启动失败：{str(e)}。录音识别仍可继续使用。"
                        })

                elif text == "STOP_PCM":
                    save_wav_from_pcm(pcm_chunks, WAV_FILE_PATH)
                    print("PCM 已保存为 WAV：", WAV_FILE_PATH)
                    await websocket.send_text(f"PCM 已保存为 WAV：{WAV_FILE_PATH}")

                    if stream_asr:
                        try:
                            await stream_asr.finish()
                        except Exception as e:
                            await send_json_to_frontend({
                                "type": "asr_stream_error",
                                "message": f"流式 ASR 结束失败：{str(e)}"
                            })
                        finally:
                            stream_asr = None

                else:
                    await websocket.send_text(f"后端收到文本：{text}")

            elif "bytes" in message and message["bytes"] is not None:
                pcm_data = message["bytes"]
                pcm_chunks.append(pcm_data)
                pcm_chunk_count += 1

                if stream_asr:
                    try:
                        await stream_asr.send_audio(pcm_data)
                    except Exception as e:
                        await send_json_to_frontend({
                            "type": "asr_stream_error",
                            "message": f"发送音频到流式 ASR 失败：{str(e)}"
                        })
                        try:
                            await stream_asr.close()
                        except Exception:
                            pass
                        stream_asr = None

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

        if stream_asr:
            try:
                await stream_asr.close()
            except Exception:
                pass


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
