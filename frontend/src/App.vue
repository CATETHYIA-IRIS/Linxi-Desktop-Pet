<script setup lang="ts">
import { ref, computed } from 'vue'

type CharacterState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'error'
type ModelKey = 'pro' | 'lite' | 'mini'
type TtsMode = 'whole' | 'sentence'
type SpeakerKey = 'default' | 'S_DDV1VAL22' | 'S_p4f3VAL22'

const characterState = ref<CharacterState>('idle')
const volume = ref(0)
const statusLabel = ref('待机中')

const backendStatus = ref('未连接')
const backendMessage = ref('')
const pcmChunkCount = ref(0)

const userText = ref('')
const aiReply = ref('你好，我是林曦。')
const isChatLoading = ref(false)

const asrText = ref('')
const streamAsrText = ref('')
const streamAsrStatus = ref('')
const isAsrLoading = ref(false)

const isTtsLoading = ref(false)
const ttsSegmentIndex = ref(0)
const ttsSegmentTotal = ref(0)

const autoSpeakEnabled = ref(true)
const ttsMode = ref<TtsMode>('sentence')
const selectedModel = ref<ModelKey>('lite')
const selectedSpeaker = ref<SpeakerKey>('default')

const showSettings = ref(false)

const speakerCards = [
  { key: 'default' as SpeakerKey, name: '默认', id: '', desc: '内置女声' },
  { key: 'S_DDV1VAL22' as SpeakerKey, name: '音色 1', id: 'S_DDV1VAL22', desc: '克隆音色' },
  { key: 'S_p4f3VAL22' as SpeakerKey, name: '音色 2', id: 'S_p4f3VAL22', desc: '克隆音色' }
]

const modelCards = [
  { key: 'pro' as ModelKey, name: 'Pro', model: 'doubao-seed-2-0-pro-260215', desc: '最强' },
  { key: 'lite' as ModelKey, name: 'Lite', model: 'doubao-seed-2-0-lite-260428', desc: '均衡' },
  { key: 'mini' as ModelKey, name: 'Mini', model: 'doubao-seed-2-0-mini-260428', desc: '最快' }
]

// 状态素材映射 — 有文件时替换为实际路径，没有则 null 走 CSS 占位
const stateVideos: Record<CharacterState, string | null> = {
  idle:      null,
  listening: null,
  thinking:  null,
  speaking:  null,
  happy:     null,
  error:     null,
}

const stateImage = '/src/assets/linxi/avatar_idle.png'

const currentVideo = computed(() => stateVideos[characterState.value])

const stateGlowColor: Record<CharacterState, string> = {
  idle:      'rgba(125, 211, 252, 0.5)',
  listening: 'rgba(34, 211, 238, 0.9)',
  thinking:  'rgba(250, 204, 21, 0.7)',
  speaking:  'rgba(244, 114, 182, 0.9)',
  happy:     'rgba(134, 239, 172, 0.8)',
  error:     'rgba(248, 113, 113, 0.8)',
}

const characterGlow = computed(() => stateGlowColor[characterState.value])

const TARGET_SAMPLE_RATE = 16000

let audioContext: AudioContext | null = null
let analyser: AnalyserNode | null = null
let microphone: MediaStreamAudioSourceNode | null = null
let mediaStream: MediaStream | null = null
let processor: ScriptProcessorNode | null = null
let animationId: number | null = null
let socket: WebSocket | null = null
let currentAudio: HTMLAudioElement | null = null

function selectModel(model: ModelKey) { selectedModel.value = model }
function selectSpeaker(speaker: SpeakerKey) { selectedSpeaker.value = speaker }

function handleBackendMessage(rawMessage: string) {
  try {
    const data = JSON.parse(rawMessage)

    if (data.type === 'asr_stream') {
      streamAsrText.value = data.text || ''
      streamAsrStatus.value = data.is_final ? '识别完成' : '识别中...'
      if (data.text) {
        asrText.value = data.text
        userText.value = data.text
      }
      return
    }

    if (data.type === 'asr_stream_status') {
      streamAsrStatus.value = data.message || ''
      backendMessage.value = data.message || backendMessage.value
      return
    }

    if (data.type === 'asr_stream_error') {
      streamAsrStatus.value = data.message || '流式 ASR 出错'
      backendMessage.value = data.message || backendMessage.value
      return
    }
  } catch { /* plain text */ }

  backendMessage.value = rawMessage
}

function connectBackend() {
  if (socket && socket.readyState === WebSocket.OPEN) return

  socket = new WebSocket('ws://127.0.0.1:8000/ws')

  socket.onopen = () => {
    backendStatus.value = '已连接'
    socket?.send('你好，后端，我是林曦前端')
  }

  socket.onmessage = (event) => handleBackendMessage(event.data)

  socket.onerror = () => {
    backendStatus.value = '连接出错'
    backendMessage.value = '请检查 Python 后端是否正在运行'
  }

  socket.onclose = () => { backendStatus.value = '已断开' }
}

async function sendChat(textFromVoice?: string, shouldSpeak = true) {
  const text = (textFromVoice ?? userText.value).trim()
  if (!text) return

  userText.value = text

  try {
    isChatLoading.value = true
    characterState.value = 'thinking'
    statusLabel.value = '思考中'
    aiReply.value = '...'

    const response = await fetch('http://127.0.0.1:8000/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, model: selectedModel.value })
    })

    const data = await response.json()

    if (data.ok) {
      aiReply.value = data.reply
      statusLabel.value = '回复完成'
      if (shouldSpeak && autoSpeakEnabled.value) {
        await speakText(data.reply)
      }
    } else {
      aiReply.value = data.reply || '调用失败'
      statusLabel.value = '调用失败'
      characterState.value = 'error'
    }
  } catch (error) {
    console.error(error)
    aiReply.value = '请求失败，请检查后端'
    statusLabel.value = '连接失败'
    characterState.value = 'error'
  } finally {
    isChatLoading.value = false
    if (!isTtsLoading.value) {
      characterState.value = 'idle'
      statusLabel.value = '待机中'
    }
  }
}

function splitTextIntoSentences(text: string): string[] {
  const cleanText = text.replace(/\r/g, '').replace(/\n+/g, ' ').replace(/\s+/g, ' ').trim()
  if (!cleanText) return []

  const matched = cleanText.match(/[^。！？!?；;\n]+[。！？!?；;]?/g) || [cleanText]
  const result: string[] = []

  for (const item of matched) {
    const sentence = item.trim()
    if (!sentence) continue
    if (sentence.length <= 90) {
      result.push(sentence)
    } else {
      for (let i = 0; i < sentence.length; i += 90) {
        result.push(sentence.slice(i, i + 90))
      }
    }
  }

  return result
}

async function requestTtsAudio(content: string): Promise<string> {
  const speakerId = speakerCards.find(s => s.key === selectedSpeaker.value)?.id || undefined

  const response = await fetch('http://127.0.0.1:8000/tts/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: content, speaker: speakerId })
  })

  const data = await response.json()
  if (!data.ok) throw new Error(data.message || 'TTS 合成失败')

  return `http://127.0.0.1:8000${data.audio_url}`
}

async function playAudioUrl(audioUrl: string): Promise<void> {
  if (currentAudio) {
    currentAudio.pause()
    currentAudio = null
  }

  currentAudio = new Audio(audioUrl)

  await new Promise<void>((resolve, reject) => {
    if (!currentAudio) { reject(new Error('音频对象创建失败')); return }
    currentAudio.onplay = () => { characterState.value = 'speaking'; statusLabel.value = '说话中' }
    currentAudio.onended = () => resolve()
    currentAudio.onerror = () => reject(new Error('浏览器播放音频失败'))
    currentAudio.play().catch(reject)
  })
}

async function speakText(text?: string) {
  const content = (text ?? aiReply.value).trim()
  if (!content) return

  try {
    isTtsLoading.value = true
    characterState.value = 'speaking'

    if (ttsMode.value === 'sentence') {
      const sentences = splitTextIntoSentences(content)
      if (sentences.length === 0) throw new Error('没有可播放的句子')
      ttsSegmentTotal.value = sentences.length

      for (let i = 0; i < sentences.length; i++) {
        ttsSegmentIndex.value = i + 1
        const audioUrl = await requestTtsAudio(sentences[i])
        await playAudioUrl(audioUrl)
      }
    } else {
      ttsSegmentIndex.value = 1
      ttsSegmentTotal.value = 1
      const audioUrl = await requestTtsAudio(content)
      await playAudioUrl(audioUrl)
    }

    characterState.value = 'happy'
    statusLabel.value = '说完了'
    setTimeout(() => {
      characterState.value = 'idle'
      statusLabel.value = '待机中'
    }, 1500)
  } catch (error) {
    console.error(error)
    characterState.value = 'error'
    statusLabel.value = '语音失败'
  } finally {
    isTtsLoading.value = false
  }
}

async function recognizeLastAudio(autoSendToLlm = false) {
  try {
    isAsrLoading.value = true
    characterState.value = 'thinking'
    statusLabel.value = '识别中'
    asrText.value = '识别中...'

    const response = await fetch('http://127.0.0.1:8000/asr/recognize-last', { method: 'POST' })
    const data = await response.json()

    if (data.ok) {
      asrText.value = data.text
      userText.value = data.text

      if (autoSendToLlm) {
        await sendChat(data.text, true)
      } else {
        statusLabel.value = '识别完成'
        characterState.value = 'idle'
      }
    } else {
      asrText.value = data.message || '识别失败'
      statusLabel.value = '识别失败'
      characterState.value = 'error'
    }
  } catch (error) {
    console.error(error)
    asrText.value = '请求 ASR 接口失败'
    characterState.value = 'error'
  } finally {
    isAsrLoading.value = false
    if (!isChatLoading.value && !isTtsLoading.value) {
      if (characterState.value !== 'error') {
        characterState.value = 'idle'
        statusLabel.value = '待机中'
      }
    }
  }
}

async function startListening() {
  try {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      backendMessage.value = '请先点击连接后端'
      return
    }

    characterState.value = 'listening'
    statusLabel.value = '聆听中'
    pcmChunkCount.value = 0
    streamAsrText.value = ''
    streamAsrStatus.value = '正在连接...'

    socket.send('START_PCM')

    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    })

    audioContext = new AudioContext()
    microphone = audioContext.createMediaStreamSource(mediaStream)

    startVolumeMonitor()
    startPcmStreaming()
  } catch (error) {
    console.error(error)
    characterState.value = 'error'
    statusLabel.value = '麦克风失败'
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
    for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]
    volume.value = Math.min(100, Math.round((sum / dataArray.length) * 2))
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
    const audioData = (pcm16.buffer as ArrayBuffer).slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength)
    socket.send(audioData)
    pcmChunkCount.value += 1
  }

  microphone.connect(processor)
  processor.connect(audioContext.destination)
}

function resampleTo16k(input: Float32Array, sourceSampleRate: number): Float32Array {
  if (sourceSampleRate === TARGET_SAMPLE_RATE) return input
  const ratio = sourceSampleRate / TARGET_SAMPLE_RATE
  const newLength = Math.round(input.length / ratio)
  const result = new Float32Array(newLength)
  for (let i = 0; i < newLength; i++) {
    const sourceIndex = i * ratio
    const leftIndex = Math.floor(sourceIndex)
    const rightIndex = Math.min(leftIndex + 1, input.length - 1)
    result[i] = input[leftIndex] * (1 - (sourceIndex - leftIndex)) + input[rightIndex] * (sourceIndex - leftIndex)
  }
  return result
}

function float32ToInt16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]))
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return output
}

function stopListening() {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send('STOP_PCM')

  characterState.value = 'idle'
  statusLabel.value = '录音结束'
  volume.value = 0

  if (processor) { processor.disconnect(); processor.onaudioprocess = null; processor = null }
  if (animationId) { cancelAnimationFrame(animationId); animationId = null }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null }
  if (audioContext) { audioContext.close(); audioContext = null }
  analyser = null
  microphone = null
}
</script>

<template>
  <main class="page">

    <!-- 背景粒子装饰 -->
    <div class="bg-orb bg-orb-1"></div>
    <div class="bg-orb bg-orb-2"></div>
    <div class="bg-orb bg-orb-3"></div>

    <!-- 角色视觉区 -->
    <div class="character-stage" :class="characterState">
      <div class="character-frame" :style="{ '--glow': characterGlow }">

        <!-- 有视频时播放视频，没有时显示图片或 CSS 占位 -->
        <video
          v-if="currentVideo"
          :src="currentVideo"
          autoplay
          loop
          muted
          playsinline
          class="character-media"
        />
        <img
          v-else-if="stateImage"
          :src="stateImage"
          class="character-media character-img"
          @error="(e) => (e.target as HTMLImageElement).style.display = 'none'"
        />
        <div v-else class="character-placeholder">
          <span class="placeholder-kanji">林曦</span>
        </div>

        <!-- 状态光环 -->
        <div class="state-ring" :class="characterState"></div>
      </div>

      <!-- 状态标签 -->
      <div class="state-badge" :class="characterState">{{ statusLabel }}</div>

      <!-- 说话时音量波形 -->
      <div v-if="characterState === 'listening'" class="volume-waves">
        <span v-for="i in 5" :key="i" class="wave-bar" :style="{ animationDelay: `${i * 0.1}s`, height: `${8 + (volume / 100) * 28}px` }"></span>
      </div>
    </div>

    <!-- 实时字幕区 -->
    <div class="subtitle-area" v-if="streamAsrText || aiReply">
      <div v-if="characterState === 'listening' && streamAsrText" class="subtitle-user">
        {{ streamAsrText }}
      </div>
      <div v-if="characterState === 'speaking' || characterState === 'thinking' || characterState === 'happy'" class="subtitle-linxi">
        {{ aiReply }}
      </div>
    </div>

    <!-- 主操作栏 -->
    <div class="action-bar">
      <button class="action-btn btn-connect" @click="connectBackend" :title="backendStatus">
        <span class="btn-dot" :class="backendStatus === '已连接' ? 'dot-on' : 'dot-off'"></span>
        连接
      </button>

      <button
        class="action-btn btn-mic"
        :class="{ active: characterState === 'listening' }"
        @click="characterState === 'listening' ? stopListening() : startListening()"
        :disabled="isChatLoading || isTtsLoading"
      >
        {{ characterState === 'listening' ? '停止' : '录音' }}
      </button>

      <button
        class="action-btn btn-ask"
        @click="recognizeLastAudio(true)"
        :disabled="isAsrLoading || isChatLoading || isTtsLoading || characterState === 'listening'"
      >
        {{ isAsrLoading || isChatLoading || isTtsLoading ? '处理中' : '语音问' }}
      </button>

      <button
        class="action-btn btn-send"
        @click="sendChat()"
        :disabled="isChatLoading || isTtsLoading"
      >
        {{ isChatLoading ? '思考中' : '发送' }}
      </button>

      <button
        class="action-btn btn-speak"
        @click="speakText()"
        :disabled="isTtsLoading"
      >
        {{ isTtsLoading ? `${ttsSegmentIndex}/${ttsSegmentTotal}` : '朗读' }}
      </button>

      <button class="action-btn btn-settings" @click="showSettings = !showSettings">
        设置
      </button>
    </div>

    <!-- 文字输入区 -->
    <div class="input-area">
      <textarea
        v-model="userText"
        class="chat-input"
        placeholder="输入消息，或点击录音后点语音问…"
        rows="2"
        @keydown.enter.exact.prevent="sendChat()"
      ></textarea>
    </div>

    <!-- 回复展示区（说话或思考时高亮） -->
    <div class="reply-area" v-if="aiReply && aiReply !== '...'">
      <div class="reply-text">{{ aiReply }}</div>
      <div v-if="isTtsLoading" class="tts-progress">{{ ttsSegmentIndex }} / {{ ttsSegmentTotal }}</div>
    </div>

    <!-- 设置面板 -->
    <transition name="panel-slide">
      <div v-if="showSettings" class="settings-panel">
        <div class="panel-section">
          <div class="panel-label">大模型</div>
          <div class="chip-row">
            <button
              v-for="item in modelCards"
              :key="item.key"
              class="chip"
              :class="{ active: selectedModel === item.key }"
              @click="selectModel(item.key)"
              :title="item.model"
            >{{ item.name }}</button>
          </div>
        </div>

        <div class="panel-section">
          <div class="panel-label">音色</div>
          <div class="chip-row">
            <button
              v-for="item in speakerCards"
              :key="item.key"
              class="chip chip-speaker"
              :class="{ active: selectedSpeaker === item.key }"
              @click="selectSpeaker(item.key)"
            >{{ item.name }}</button>
          </div>
        </div>

        <div class="panel-section">
          <div class="panel-label">播放模式</div>
          <div class="chip-row">
            <button class="chip" :class="{ active: ttsMode === 'sentence' }" @click="ttsMode = 'sentence'">分句</button>
            <button class="chip" :class="{ active: ttsMode === 'whole' }" @click="ttsMode = 'whole'">整段</button>
          </div>
        </div>

        <div class="panel-section">
          <label class="toggle-row">
            <input type="checkbox" v-model="autoSpeakEnabled" />
            <span>回复后自动朗读</span>
          </label>
        </div>

        <div class="panel-section panel-debug">
          <div class="debug-line">后端：{{ backendStatus }}</div>
          <div class="debug-line" v-if="backendMessage">{{ backendMessage }}</div>
          <div class="debug-line" v-if="streamAsrStatus">ASR：{{ streamAsrStatus }}</div>
          <div class="debug-line" v-if="characterState === 'listening'">PCM：{{ pcmChunkCount }} 段</div>
        </div>
      </div>
    </transition>

  </main>
</template>

<style scoped>
*, *::before, *::after { box-sizing: border-box; }

.page {
  position: relative;
  width: 100vw;
  min-height: 100vh;
  background: radial-gradient(ellipse at 50% 0%, #1a2a4a 0%, #0d1420 55%, #050810 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  overflow: hidden;
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  color: #e8f0ff;
  padding-bottom: 32px;
}

/* 背景光球 */
.bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(80px);
  pointer-events: none;
  z-index: 0;
}
.bg-orb-1 {
  width: 500px; height: 500px;
  top: -120px; left: -100px;
  background: rgba(56, 100, 200, 0.18);
}
.bg-orb-2 {
  width: 400px; height: 400px;
  top: 20%; right: -80px;
  background: rgba(160, 80, 220, 0.12);
}
.bg-orb-3 {
  width: 300px; height: 300px;
  bottom: 10%; left: 30%;
  background: rgba(30, 180, 200, 0.10);
}

/* ── 角色视觉区 ── */
.character-stage {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-top: 40px;
  margin-bottom: 12px;
}

.character-frame {
  position: relative;
  width: 300px;
  height: 380px;
  border-radius: 24px;
  overflow: hidden;
  background: linear-gradient(160deg, rgba(40, 60, 100, 0.6), rgba(20, 30, 60, 0.8));
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 0 60px var(--glow, rgba(125, 211, 252, 0.3)), inset 0 0 30px rgba(0,0,0,0.4);
  transition: box-shadow 0.4s ease;
}

.character-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
}

.character-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(100, 160, 255, 0.15), rgba(180, 100, 255, 0.15));
}

.placeholder-kanji {
  font-size: 64px;
  font-weight: 900;
  background: linear-gradient(135deg, #7dd3fc, #c084fc);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 4px;
}

/* 状态光环 */
.state-ring {
  position: absolute;
  inset: 0;
  border-radius: 24px;
  pointer-events: none;
  transition: box-shadow 0.4s ease, border-color 0.4s ease;
  border: 2px solid transparent;
}
.state-ring.listening { border-color: rgba(34, 211, 238, 0.7); box-shadow: inset 0 0 20px rgba(34, 211, 238, 0.2); }
.state-ring.thinking  { border-color: rgba(250, 204, 21, 0.6);  box-shadow: inset 0 0 20px rgba(250, 204, 21, 0.15); }
.state-ring.speaking  { border-color: rgba(244, 114, 182, 0.7); box-shadow: inset 0 0 20px rgba(244, 114, 182, 0.2); }
.state-ring.happy     { border-color: rgba(134, 239, 172, 0.7); box-shadow: inset 0 0 20px rgba(134, 239, 172, 0.2); }
.state-ring.error     { border-color: rgba(248, 113, 113, 0.7); box-shadow: inset 0 0 20px rgba(248, 113, 113, 0.2); }

/* 状态标签 */
.state-badge {
  margin-top: 12px;
  padding: 4px 16px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  transition: all 0.3s ease;
}
.state-badge.listening { background: rgba(34, 211, 238, 0.15); border-color: rgba(34, 211, 238, 0.4); color: #67e8f9; }
.state-badge.thinking  { background: rgba(250, 204, 21, 0.15);  border-color: rgba(250, 204, 21, 0.4);  color: #fde047; }
.state-badge.speaking  { background: rgba(244, 114, 182, 0.15); border-color: rgba(244, 114, 182, 0.4); color: #f9a8d4; }
.state-badge.happy     { background: rgba(134, 239, 172, 0.15); border-color: rgba(134, 239, 172, 0.4); color: #86efac; }
.state-badge.error     { background: rgba(248, 113, 113, 0.15); border-color: rgba(248, 113, 113, 0.4); color: #fca5a5; }

/* 音量波形 */
.volume-waves {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 10px;
  height: 40px;
}
.wave-bar {
  display: block;
  width: 4px;
  border-radius: 2px;
  background: #22d3ee;
  animation: wave-pulse 0.6s ease-in-out infinite alternate;
  transition: height 0.08s linear;
}
@keyframes wave-pulse {
  from { opacity: 0.5; transform: scaleY(0.7); }
  to   { opacity: 1.0; transform: scaleY(1.0); }
}

/* ── 字幕区 ── */
.subtitle-area {
  z-index: 1;
  width: min(580px, 90vw);
  min-height: 36px;
  text-align: center;
  margin-bottom: 4px;
}

.subtitle-user {
  font-size: 15px;
  color: #93c5fd;
  padding: 6px 12px;
  border-radius: 8px;
  background: rgba(59, 130, 246, 0.1);
  margin-bottom: 4px;
}

.subtitle-linxi {
  font-size: 15px;
  color: #f0abfc;
  padding: 6px 12px;
  border-radius: 8px;
  background: rgba(192, 132, 252, 0.1);
  line-height: 1.6;
  max-height: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
}

/* ── 主操作栏 ── */
.action-bar {
  z-index: 1;
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: center;
  margin: 12px 0 8px;
}

.action-btn {
  border: none;
  border-radius: 999px;
  padding: 10px 22px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  color: white;
  transition: all 0.2s ease;
  letter-spacing: 0.5px;
}

.action-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.btn-connect  { background: rgba(124, 58, 237, 0.6); border: 1px solid rgba(167,139,250,0.3); }
.btn-mic      { background: rgba(6, 182, 212, 0.6);   border: 1px solid rgba(34,211,238,0.3); }
.btn-mic.active { background: rgba(239, 68, 68, 0.7); border-color: rgba(252,165,165,0.4); animation: pulse-mic 1.2s infinite; }
.btn-ask      { background: rgba(234, 88, 12, 0.7);   border: 1px solid rgba(253,186,116,0.3); }
.btn-send     { background: rgba(37, 99, 235, 0.7);   border: 1px solid rgba(147,197,253,0.3); }
.btn-speak    { background: rgba(219, 39, 119, 0.7);  border: 1px solid rgba(249,168,212,0.3); }
.btn-settings { background: rgba(71, 85, 105, 0.6);   border: 1px solid rgba(148,163,184,0.2); }

.action-btn:not(:disabled):hover { filter: brightness(1.25); transform: translateY(-1px); }

@keyframes pulse-mic {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.5); }
  50%       { box-shadow: 0 0 0 8px rgba(239, 68, 68, 0); }
}

.btn-dot {
  display: inline-block;
  width: 7px; height: 7px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}
.dot-on  { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
.dot-off { background: #94a3b8; }

/* ── 输入区 ── */
.input-area {
  z-index: 1;
  width: min(580px, 90vw);
  margin-top: 4px;
}

.chat-input {
  width: 100%;
  padding: 12px 16px;
  border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.07);
  color: #e8f0ff;
  font-size: 14px;
  font-family: inherit;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
  line-height: 1.5;
}
.chat-input::placeholder { color: rgba(255,255,255,0.3); }
.chat-input:focus { border-color: rgba(125, 211, 252, 0.4); }

/* ── 回复区 ── */
.reply-area {
  z-index: 1;
  width: min(580px, 90vw);
  margin-top: 8px;
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.08);
  font-size: 14px;
  line-height: 1.75;
  white-space: pre-wrap;
  max-height: 180px;
  overflow-y: auto;
}

.tts-progress {
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.5;
  text-align: right;
}

/* ── 设置面板 ── */
.settings-panel {
  z-index: 2;
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: min(580px, 100vw);
  background: rgba(10, 18, 36, 0.95);
  backdrop-filter: blur(20px);
  border-top: 1px solid rgba(255,255,255,0.1);
  border-radius: 24px 24px 0 0;
  padding: 20px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-section { display: flex; flex-direction: column; gap: 8px; }
.panel-label { font-size: 12px; font-weight: 600; opacity: 0.5; letter-spacing: 1px; text-transform: uppercase; }

.chip-row { display: flex; gap: 8px; flex-wrap: wrap; }

.chip {
  border: 1px solid rgba(255,255,255,0.15);
  border-radius: 999px;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 600;
  color: rgba(255,255,255,0.7);
  background: rgba(255,255,255,0.06);
  cursor: pointer;
  transition: all 0.2s;
}
.chip.active {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.18);
  color: #7dd3fc;
}
.chip-speaker.active {
  border-color: #a78bfa;
  background: rgba(167, 139, 250, 0.18);
  color: #c4b5fd;
}

.toggle-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  cursor: pointer;
}

.panel-debug {
  border-top: 1px solid rgba(255,255,255,0.06);
  padding-top: 12px;
  gap: 4px;
}
.debug-line { font-size: 12px; opacity: 0.45; }

/* 面板动画 */
.panel-slide-enter-active,
.panel-slide-leave-active { transition: transform 0.3s ease; }
.panel-slide-enter-from,
.panel-slide-leave-to { transform: translateX(-50%) translateY(100%); }
</style>
