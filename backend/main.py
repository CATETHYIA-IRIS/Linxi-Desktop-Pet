from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import wave
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_DIR = Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)

PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2  # 16bit = 2 bytes


@app.get("/")
def home():
    return {"message": "林曦后端正在运行"}


def save_wav_from_pcm(pcm_chunks: list[bytes], output_path: Path):
    pcm_data = b"".join(pcm_chunks)

    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(PCM_CHANNELS)
        wav_file.setsampwidth(PCM_SAMPLE_WIDTH)
        wav_file.setframerate(PCM_SAMPLE_RATE)
        wav_file.writeframes(pcm_data)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("林曦前端已连接")

    pcm_chunks: list[bytes] = []
    pcm_chunk_count = 0
    wav_file_path = AUDIO_DIR / "test.wav"

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
                    save_wav_from_pcm(pcm_chunks, wav_file_path)
                    print("PCM 已保存为 WAV：", wav_file_path)
                    await websocket.send_text(f"PCM 已保存为 WAV：{wav_file_path}")

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