from __future__ import annotations

import asyncio
import gzip
import json
import os
import uuid
from typing import Awaitable, Callable, Optional

import websockets


PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_WITH_SEQUENCE = 0b0011

NO_SERIALIZATION = 0b0000
JSON_SERIALIZATION = 0b0001

NO_COMPRESSION = 0b0000
GZIP = 0b0001


TextCallback = Callable[[str, bool, dict], Awaitable[None]]


def generate_header(
    message_type: int = FULL_CLIENT_REQUEST,
    message_type_specific_flags: int = NO_SEQUENCE,
    serial_method: int = JSON_SERIALIZATION,
    compression_type: int = GZIP,
    reserved_data: int = 0x00,
) -> bytearray:
    header = bytearray()
    header.append((PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    return header


def sequence_bytes(sequence: int) -> bytes:
    return sequence.to_bytes(4, "big", signed=True)


def make_frame(
    payload: bytes,
    message_type: int,
    flags: int,
    sequence: Optional[int] = None,
    serial_method: int = JSON_SERIALIZATION,
    compression_type: int = GZIP,
) -> bytes:
    if compression_type == GZIP:
        payload = gzip.compress(payload)

    frame = bytearray(
        generate_header(
            message_type=message_type,
            message_type_specific_flags=flags,
            serial_method=serial_method,
            compression_type=compression_type,
        )
    )

    if flags in (POS_SEQUENCE, NEG_WITH_SEQUENCE):
        if sequence is None:
            raise ValueError("当前帧需要 sequence，但 sequence 为空。")
        frame.extend(sequence_bytes(sequence))

    frame.extend(len(payload).to_bytes(4, "big", signed=False))
    frame.extend(payload)
    return bytes(frame)


def parse_binary_response(data: bytes) -> dict:
    if len(data) < 4:
        return {"ok": False, "error": "响应长度不足", "raw_len": len(data)}

    protocol_version = data[0] >> 4
    header_size = data[0] & 0x0F
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization_method = data[2] >> 4
    compression = data[2] & 0x0F

    offset = header_size * 4
    sequence = None

    if flags in (POS_SEQUENCE, NEG_WITH_SEQUENCE):
        sequence = int.from_bytes(data[offset:offset + 4], "big", signed=True)
        offset += 4

    if len(data) < offset + 4:
        return {
            "ok": False,
            "error": "响应缺少 payload size",
            "message_type": message_type,
            "flags": flags,
            "sequence": sequence,
        }

    payload_size = int.from_bytes(data[offset:offset + 4], "big", signed=False)
    offset += 4
    payload = data[offset:offset + payload_size]

    if compression == GZIP and payload:
        payload = gzip.decompress(payload)

    payload_json = None
    payload_text = ""

    if serialization_method == JSON_SERIALIZATION and payload:
        try:
            payload_json = json.loads(payload.decode("utf-8"))
        except Exception:
            payload_text = payload.decode("utf-8", errors="replace")
    elif payload:
        payload_text = payload.decode("utf-8", errors="replace")

    return {
        "ok": True,
        "protocol_version": protocol_version,
        "message_type": message_type,
        "flags": flags,
        "sequence": sequence,
        "is_last": sequence is not None and sequence < 0,
        "payload_size": payload_size,
        "payload_json": payload_json,
        "payload_text": payload_text,
    }


def extract_text(payload: dict) -> str:
    if not payload:
        return ""

    if isinstance(payload.get("text"), str):
        return payload["text"]

    result = payload.get("result")

    if isinstance(result, dict):
        if isinstance(result.get("text"), str):
            return result["text"]

        utterances = result.get("utterances")
        if isinstance(utterances, list):
            texts = []
            for item in utterances:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    texts.append(item["text"])
            if texts:
                return "".join(texts)

    if isinstance(payload.get("utterances"), list):
        texts = []
        for item in payload["utterances"]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
        if texts:
            return "".join(texts)

    return ""


class DoubaoStreamASR:
    """
    豆包大模型流式语音识别 WebSocket 客户端。
    这个模块只负责：连接豆包、发送 PCM 音频片段、解析返回文本。
    """

    def __init__(self):
        self.api_key = os.getenv("DOUBAO_SPEECH_API_KEY", "").strip()
        self.url = os.getenv(
            "DOUBAO_STREAM_ASR_WS_URL",
            "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel",
        )
        self.resource_id = os.getenv(
            "DOUBAO_STREAM_ASR_RESOURCE_ID",
            "volc.bigasr.sauc.duration",
        )
        self.sample_rate = int(os.getenv("DOUBAO_STREAM_ASR_SAMPLE_RATE", "16000"))

        self.ws = None
        self.sequence = 1
        self.receive_task: Optional[asyncio.Task] = None
        self.on_text: Optional[TextCallback] = None
        self.closed = False

    async def start(self, on_text: TextCallback):
        if not self.api_key:
            raise RuntimeError("没有读取到 DOUBAO_SPEECH_API_KEY，请检查项目根目录的 .env 文件。")

        self.on_text = on_text
        headers = {
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        try:
            self.ws = await websockets.connect(
                self.url,
                additional_headers=headers,
                max_size=1000000000,
            )
        except TypeError:
            self.ws = await websockets.connect(
                self.url,
                extra_headers=headers,
                max_size=1000000000,
            )

        request_params = {
            "user": {
                "uid": "linxi-local-user"
            },
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": self.sample_rate,
                "sample_rate": self.sample_rate,
                "bits": 16,
                "channel": 1
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
                "result_type": "single"
            }
        }

        payload = json.dumps(request_params, ensure_ascii=False).encode("utf-8")
        frame = make_frame(
            payload=payload,
            message_type=FULL_CLIENT_REQUEST,
            flags=POS_SEQUENCE,
            sequence=self.sequence,
            serial_method=JSON_SERIALIZATION,
            compression_type=GZIP,
        )
        await self.ws.send(frame)

        self.receive_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, pcm_data: bytes, is_last: bool = False):
        if not self.ws or self.closed:
            return

        if is_last:
            self.sequence = -abs(self.sequence + 1)
            flags = NEG_WITH_SEQUENCE
            payload = b""
        else:
            self.sequence += 1
            flags = POS_SEQUENCE
            payload = pcm_data

        frame = make_frame(
            payload=payload,
            message_type=AUDIO_ONLY_REQUEST,
            flags=flags,
            sequence=self.sequence,
            serial_method=NO_SERIALIZATION,
            compression_type=GZIP,
        )
        await self.ws.send(frame)

    async def finish(self):
        if self.closed:
            return

        try:
            await self.send_audio(b"", is_last=True)
            await asyncio.sleep(1.5)
        finally:
            await self.close()

    async def close(self):
        self.closed = True

        if self.receive_task:
            self.receive_task.cancel()
            self.receive_task = None

        if self.ws:
            await self.ws.close()
            self.ws = None

    async def _receive_loop(self):
        try:
            async for message in self.ws:
                if isinstance(message, str):
                    try:
                        payload_json = json.loads(message)
                    except Exception:
                        payload_json = {"raw_text": message}

                    text = extract_text(payload_json)
                    if text and self.on_text:
                        await self.on_text(text, False, payload_json)
                    continue

                parsed = parse_binary_response(message)

                if not parsed.get("ok"):
                    if self.on_text:
                        await self.on_text("", False, {"error": parsed})
                    continue

                if parsed.get("message_type") == SERVER_ERROR_RESPONSE:
                    if self.on_text:
                        await self.on_text("", True, {"server_error": parsed})
                    continue

                payload_json = parsed.get("payload_json") or {}
                text = extract_text(payload_json)
                is_final = bool(parsed.get("is_last"))

                if text and self.on_text:
                    await self.on_text(text, is_final, payload_json)

        except asyncio.CancelledError:
            return
        except Exception as e:
            if self.on_text:
                await self.on_text("", True, {"error": str(e)})
