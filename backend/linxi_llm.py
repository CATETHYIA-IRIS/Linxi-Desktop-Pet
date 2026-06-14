from pathlib import Path
from dotenv import load_dotenv
import os
import requests

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
load_dotenv(ROOT_DIR / ".env")

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


def get_model_id(model_key: str) -> tuple[str, str]:
    if model_key not in AVAILABLE_MODELS:
        model_key = "lite"

    return model_key, AVAILABLE_MODELS[model_key]["id"]


def get_linxi_system_prompt() -> str:
    return (
        "你叫林曦，是一个运行在用户电脑桌面上的 AI 桌宠。"
        "你的语气自然、简短、亲切，不要像客服，不要长篇大论。"
        "你现在处于早期原型阶段，已经具备文字聊天、语音识别和语音回复能力。"
        "后续你会接入音色克隆、记忆海、数据海、桌面形象和更多技能。"
        "回答时尽量像一个真实桌宠角色，而不是普通助手。"
    )


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


def chat_with_linxi(user_text: str, model_key: str = "lite") -> dict:
    api_key = os.getenv("ARK_API_KEY")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

    selected_model_key, model_id = get_model_id(model_key)

    if not api_key:
        return {
            "ok": False,
            "reply": "没有读取到 ARK_API_KEY，请检查项目根目录的 .env 文件。",
            "model": model_id,
            "model_key": selected_model_key
        }

    user_text = user_text.strip()
    if not user_text:
        return {
            "ok": False,
            "reply": "用户输入为空，林曦不知道该回复什么。",
            "model": model_id,
            "model_key": selected_model_key
        }

    url = f"{base_url}/responses"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model_id,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"{get_linxi_system_prompt()}\n\n"
                            f"用户说：{user_text}\n\n"
                            "请以林曦的身份回复："
                        )
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
            "model_key": selected_model_key
        }

    except requests.exceptions.HTTPError as e:
        return {
            "ok": False,
            "reply": f"LLM HTTP 请求失败：{str(e)}。请检查 API Key、模型 ID 和接口地址。",
            "model": model_id,
            "model_key": selected_model_key
        }
    except Exception as e:
        return {
            "ok": False,
            "reply": f"LLM 调用异常：{str(e)}",
            "model": model_id,
            "model_key": selected_model_key
        }
