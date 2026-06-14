<script setup lang="ts">
import { ref } from 'vue'

type PetStatus = 'idle' | 'listening' | 'error'

const status = ref<PetStatus>('idle')
const volume = ref(0)
const message = ref('林曦正在待机')

const backendStatus = ref('后端未连接')
const backendMessage = ref('暂无后端消息')
const pcmChunkCount = ref(0)

const TARGET_SAMPLE_RATE = 16000

let audioContext: AudioContext | null = null
let analyser: AnalyserNode | null = null
let microphone: MediaStreamAudioSourceNode | null = null
let mediaStream: MediaStream | null = null
let processor: ScriptProcessorNode | null = null
let animationId: number | null = null

let socket: WebSocket | null = null

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

async function startListening() {
  try {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      backendMessage.value = '请先点击“连接后端”'
      return
    }

    status.value = 'listening'
    message.value = '林曦正在听你说话...'
    pcmChunkCount.value = 0

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

  // 必须连接到 destination，浏览器才会持续触发 onaudioprocess
  // 这里没有把输入写到输出，所以不会产生回放
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
  message.value = '林曦正在待机'
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

      <div class="volume-box">
        <div class="volume-label">实时音量</div>
        <div class="volume-bar">
          <div class="volume-inner" :style="{ width: volume + '%' }"></div>
        </div>
        <div class="volume-number">{{ volume }}</div>
      </div>

      <div class="buttons">
        <button @click="connectBackend">
          连接后端
        </button>

        <button @click="startListening" :disabled="status === 'listening'">
          开始对话
        </button>

        <button @click="stopListening" :disabled="status !== 'listening'">
          结束对话
        </button>
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
  height: 100vh;
  background: radial-gradient(circle at top, #263455, #111827 60%, #050816);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-family: "Microsoft YaHei", sans-serif;
}

.pet-card {
  width: 410px;
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

.pet-avatar.error {
  background: linear-gradient(135deg, #f87171, #fb923c);
  box-shadow: 0 0 32px rgba(248, 113, 113, 0.8);
}

.status-text {
  font-size: 18px;
  margin-bottom: 24px;
}

.volume-box {
  margin-bottom: 24px;
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

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: center;
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

button:nth-child(1) {
  background: #7c3aed;
}

button:nth-child(3) {
  background: #475569;
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