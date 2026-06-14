<script setup lang="ts">
import { ref } from 'vue'

type PetStatus = 'idle' | 'listening' | 'thinking' | 'recognizing' | 'error'
type ModelKey = 'pro' | 'lite' | 'mini'

const status = ref<PetStatus>('idle')
const volume = ref(0)
const message = ref('林曦正在待机')

const backendStatus = ref('后端未连接')
const backendMessage = ref('暂无后端消息')
const pcmChunkCount = ref(0)

const userText = ref('')
const aiReply = ref('你好，我是林曦。你可以先用文字和我说话，也可以录一段语音让我识别。')
const isChatLoading = ref(false)

const asrText = ref('暂无语音识别结果')
const isAsrLoading = ref(false)

const selectedModel = ref<ModelKey>('lite')

const modelCards = [
  {
    key: 'pro' as ModelKey,
    name: 'Pro',
    model: 'doubao-seed-2-0-pro-260215',
    desc: '最强效果，适合最终展示'
  },
  {
    key: 'lite' as ModelKey,
    name: 'Lite',
    model: 'doubao-seed-2-0-lite-260428',
    desc: '日常开发推荐，均衡稳定'
  },
  {
    key: 'mini' as ModelKey,
    name: 'Mini',
    model: 'doubao-seed-2-0-mini-260428',
    desc: '最快最省，适合测试'
  }
]

const TARGET_SAMPLE_RATE = 16000

let audioContext: AudioContext | null = null
let analyser: AnalyserNode | null = null
let microphone: MediaStreamAudioSourceNode | null = null
let mediaStream: MediaStream | null = null
let processor: ScriptProcessorNode | null = null
let animationId: number | null = null

let socket: WebSocket | null = null

function selectModel(model: ModelKey) {
  selectedModel.value = model
}

function connectBackend() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    backendStatus.value = '后端已经连接'
    return
  }

  socket = new WebSocket('ws://127.0.0.1:8000/ws')

  socket.onopen = () => {
    backendStatus.value = '后端已连接'
    backendMessage.value = 'WebSocket 连接成功'
    socket?.send('你好，后端，我是林曦前端')
  }

  socket.onmessage = (event) => {
    backendMessage.value = event.data
  }

  socket.onerror = () => {
    backendStatus.value = '后端连接出错'
    backendMessage.value = '请检查 Python 后端是否正在运行'
  }

  socket.onclose = () => {
    backendStatus.value = '后端已断开'
  }
}

async function sendChat() {
  const text = userText.value.trim()
  if (!text) {
    aiReply.value = '你还没有输入内容。'
    return
  }

  try {
    isChatLoading.value = true
    status.value = 'thinking'
    message.value = '林曦正在思考...'
    aiReply.value = '林曦正在思考中...'

    const response = await fetch('http://127.0.0.1:8000/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        text,
        model: selectedModel.value
      })
    })

    const data = await response.json()

    if (data.ok) {
      aiReply.value = data.reply
      message.value = '林曦回复完成'
    } else {
      aiReply.value = data.reply || '林曦调用模型失败。'
      message.value = '模型调用失败'
    }
  } catch (error) {
    console.error(error)
    aiReply.value = '请求后端失败，请检查 Python 后端是否正在运行。'
    message.value = '请求后端失败'
  } finally {
    isChatLoading.value = false
    status.value = 'idle'
  }
}

async function recognizeLastAudio() {
  try {
    isAsrLoading.value = true
    status.value = 'recognizing'
    message.value = '林曦正在识别刚才的语音...'
    asrText.value = '识别中...'

    const response = await fetch('http://127.0.0.1:8000/asr/recognize-last', {
      method: 'POST'
    })

    const data = await response.json()

    if (data.ok) {
      asrText.value = data.text
      userText.value = data.text
      message.value = '语音识别完成'
    } else {
      asrText.value = data.message || '语音识别失败'
      message.value = '语音识别失败'
    }
  } catch (error) {
    console.error(error)
    asrText.value = '请求 ASR 接口失败，请检查 Python 后端是否正在运行。'
    message.value = '请求 ASR 失败'
  } finally {
    isAsrLoading.value = false
    status.value = 'idle'
  }
}

async function startListening() {
  try {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      backendMessage.value = '请先点击“连接后端”'
      return
    }

    status.value = 'listening'
    message.value = '林曦正在听你说话...'
    pcmChunkCount.value = 0
    asrText.value = '录音中，结束后可以点击“识别刚才录音”'

    socket.send('START_PCM')

    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    })

    audioContext = new AudioContext()
    microphone = audioContext.createMediaStreamSource(mediaStream)

    startVolumeMonitor()
    startPcmStreaming()
  } catch (error) {
    console.error(error)
    status.value = 'error'
    message.value = '麦克风权限获取失败，请检查浏览器权限'
  }
}

function startVolumeMonitor() {
  if (!audioContext || !microphone) return

  analyser = audioContext.createAnalyser()
  analyser.fftSize = 256
  microphone.connect(analyser)

  const dataArray = new Uint8Array(analyser.frequencyBinCount)

  function updateVolume() {
    if (!analyser) return

    analyser.getByteFrequencyData(dataArray)

    let sum = 0
    for (let i = 0; i < dataArray.length; i++) {
      sum += dataArray[i]
    }

    const average = sum / dataArray.length
    volume.value = Math.min(100, Math.round(average * 2))

    animationId = requestAnimationFrame(updateVolume)
  }

  updateVolume()
}

function startPcmStreaming() {
  if (!audioContext || !microphone) return

  const sourceSampleRate = audioContext.sampleRate

  processor = audioContext.createScriptProcessor(4096, 1, 1)

  processor.onaudioprocess = (event) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return

    const inputData = event.inputBuffer.getChannelData(0)

    const resampled = resampleTo16k(inputData, sourceSampleRate)
    const pcm16 = float32ToInt16(resampled)

    socket.send(pcm16.buffer)
    pcmChunkCount.value += 1
  }

  microphone.connect(processor)
  processor.connect(audioContext.destination)

  backendMessage.value = `开始发送 PCM 音频流，原始采样率：${sourceSampleRate}Hz，目标采样率：16000Hz`
}

function resampleTo16k(input: Float32Array, sourceSampleRate: number): Float32Array {
  if (sourceSampleRate === TARGET_SAMPLE_RATE) {
    return input
  }

  const ratio = sourceSampleRate / TARGET_SAMPLE_RATE
  const newLength = Math.round(input.length / ratio)
  const result = new Float32Array(newLength)

  for (let i = 0; i < newLength; i++) {
    const sourceIndex = i * ratio
    const leftIndex = Math.floor(sourceIndex)
    const rightIndex = Math.min(leftIndex + 1, input.length - 1)
    const weight = sourceIndex - leftIndex

    result[i] = input[leftIndex] * (1 - weight) + input[rightIndex] * weight
  }

  return result
}

function float32ToInt16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length)

  for (let i = 0; i < input.length; i++) {
    let sample = input[i]

    if (sample > 1) sample = 1
    if (sample < -1) sample = -1

    output[i] = sample < 0
      ? sample * 0x8000
      : sample * 0x7fff
  }

  return output
}

function stopListening() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send('STOP_PCM')
  }

  status.value = 'idle'
  message.value = '录音已结束，可以识别刚才录音'
  volume.value = 0

  if (processor) {
    processor.disconnect()
    processor.onaudioprocess = null
    processor = null
  }

  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = null
  }

  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop())
    mediaStream = null
  }

  if (audioContext) {
    audioContext.close()
    audioContext = null
  }

  analyser = null
  microphone = null
}
</script>

<template>
  <main class="page">
    <section class="pet-card">
      <div class="pet-avatar" :class="status">
        林曦
      </div>

      <div class="status-text">
        {{ message }}
      </div>

      <div class="model-box">
        <div class="section-title">选择大模型</div>

        <div class="model-cards">
          <button
            v-for="item in modelCards"
            :key="item.key"
            class="model-card"
            :class="{ active: selectedModel === item.key }"
            @click="selectModel(item.key)"
          >
            <div class="model-name">{{ item.name }}</div>
            <div class="model-id">{{ item.model }}</div>
            <div class="model-desc">{{ item.desc }}</div>
          </button>
        </div>
      </div>

      <div class="chat-box">
        <div class="section-title">文字对话测试</div>

        <textarea
          v-model="userText"
          class="chat-input"
          placeholder="和林曦说句话，例如：你好，你是谁？"
        ></textarea>

        <button class="send-button" @click="sendChat" :disabled="isChatLoading">
          {{ isChatLoading ? '思考中...' : '发送给林曦' }}
        </button>

        <div class="reply-box">
          <div class="reply-title">林曦回复</div>
          <div class="reply-text">{{ aiReply }}</div>
        </div>
      </div>

      <div class="voice-box">
        <div class="section-title">语音识别测试</div>

        <div class="voice-buttons">
          <button @click="connectBackend">
            连接后端
          </button>

          <button @click="startListening" :disabled="status === 'listening'">
            开始录音
          </button>

          <button @click="stopListening" :disabled="status !== 'listening'">
            结束录音
          </button>

          <button @click="recognizeLastAudio" :disabled="isAsrLoading || status === 'listening'">
            {{ isAsrLoading ? '识别中...' : '识别刚才录音' }}
          </button>
        </div>

        <div class="asr-box">
          <div class="reply-title">ASR 识别结果</div>
          <div class="reply-text">{{ asrText }}</div>
        </div>
      </div>

      <div class="volume-box">
        <div class="volume-label">实时音量</div>
        <div class="volume-bar">
          <div class="volume-inner" :style="{ width: volume + '%' }"></div>
        </div>
        <div class="volume-number">{{ volume }}</div>
      </div>

      <div class="backend-box">
        <div class="backend-title">后端状态</div>
        <div>{{ backendStatus }}</div>
        <div>{{ backendMessage }}</div>
        <div>已发送 PCM 片段：{{ pcmChunkCount }}</div>
        <div>音频格式：PCM16 / 16kHz / 单声道</div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.page {
  width: 100vw;
  min-height: 100vh;
  background: radial-gradient(circle at top, #263455, #111827 60%, #050816);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  color: white;
  font-family: "Microsoft YaHei", sans-serif;
  padding: 32px 0;
}

.pet-card {
  width: 760px;
  padding: 32px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.08);
  box-shadow: 0 0 40px rgba(90, 170, 255, 0.25);
  text-align: center;
  backdrop-filter: blur(16px);
}

.pet-avatar {
  width: 150px;
  height: 150px;
  border-radius: 50%;
  margin: 0 auto 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
  font-weight: bold;
  background: linear-gradient(135deg, #7dd3fc, #c084fc);
  box-shadow: 0 0 32px rgba(125, 211, 252, 0.8);
  transition: all 0.3s ease;
}

.pet-avatar.listening {
  transform: scale(1.06);
  box-shadow: 0 0 48px rgba(34, 211, 238, 1);
}

.pet-avatar.thinking {
  transform: scale(1.03);
  box-shadow: 0 0 48px rgba(250, 204, 21, 0.8);
}

.pet-avatar.recognizing {
  transform: scale(1.03);
  box-shadow: 0 0 48px rgba(45, 212, 191, 0.8);
}

.pet-avatar.error {
  background: linear-gradient(135deg, #f87171, #fb923c);
  box-shadow: 0 0 32px rgba(248, 113, 113, 0.8);
}

.status-text {
  font-size: 18px;
  margin-bottom: 24px;
}

.section-title {
  font-weight: bold;
  margin-bottom: 12px;
  text-align: left;
}

.model-box,
.chat-box,
.voice-box,
.volume-box {
  margin-bottom: 24px;
}

.model-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}

.model-card {
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 16px;
  padding: 14px;
  cursor: pointer;
  text-align: left;
  color: white;
  background: rgba(255, 255, 255, 0.08);
}

.model-card.active {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.18);
  box-shadow: 0 0 20px rgba(56, 189, 248, 0.35);
}

.model-name {
  font-size: 20px;
  font-weight: bold;
  margin-bottom: 6px;
}

.model-id {
  font-size: 11px;
  opacity: 0.7;
  word-break: break-all;
  margin-bottom: 8px;
}

.model-desc {
  font-size: 13px;
  opacity: 0.9;
}

.chat-input {
  width: 100%;
  min-height: 80px;
  border: none;
  outline: none;
  resize: vertical;
  border-radius: 14px;
  padding: 14px;
  box-sizing: border-box;
  color: white;
  font-size: 15px;
  background: rgba(255, 255, 255, 0.12);
}

.chat-input::placeholder {
  color: rgba(255, 255, 255, 0.55);
}

.send-button {
  margin-top: 12px;
  width: 100%;
  background: #2563eb;
}

.reply-box,
.asr-box {
  margin-top: 14px;
  padding: 14px;
  border-radius: 14px;
  text-align: left;
  background: rgba(255, 255, 255, 0.12);
}

.reply-title {
  font-weight: bold;
  margin-bottom: 8px;
}

.reply-text {
  line-height: 1.7;
  white-space: pre-wrap;
}

.voice-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: center;
}

.volume-label {
  font-size: 14px;
  opacity: 0.8;
  margin-bottom: 8px;
}

.volume-bar {
  width: 100%;
  height: 16px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.18);
  overflow: hidden;
}

.volume-inner {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #38bdf8, #a78bfa);
  transition: width 0.08s linear;
}

.volume-number {
  margin-top: 8px;
  font-size: 14px;
  opacity: 0.8;
}

button {
  border: none;
  border-radius: 999px;
  padding: 10px 18px;
  cursor: pointer;
  font-size: 15px;
  color: white;
  background: #2563eb;
}

button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.voice-buttons button:nth-child(1) {
  background: #7c3aed;
}

.voice-buttons button:nth-child(3) {
  background: #475569;
}

.voice-buttons button:nth-child(4) {
  background: #059669;
}

.backend-box {
  margin-top: 20px;
  padding: 12px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.8;
  background: rgba(255, 255, 255, 0.12);
}

.backend-title {
  font-weight: bold;
  margin-bottom: 4px;
}
</style>
