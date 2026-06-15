<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'

type CharacterState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'error'
type ModelKey = 'pro' | 'lite' | 'mini'
type TtsMode = 'whole' | 'sentence'
type SpeakerKey = 'default' | 'S_DDV1VAL22' | 'S_p4f3VAL22'
type ReplyMode = 'text_only' | 'voice_only' | 'text_and_voice' | 'auto'
type MessageKind = 'text' | 'voice' | 'text_voice'
type MessageStatus = 'sending' | 'thinking' | 'playing' | 'done' | 'error'
type MessageRole = 'user' | 'linxi'

interface Message {
  id: string
  role: MessageRole
  kind: MessageKind
  text: string
  transcript: string
  audioUrl: string | null
  status: MessageStatus
  createdAt: Date
  duration: number | null
  speaker?: SpeakerKey
  model?: ModelKey
}

let msgIdCounter = 0
function newId() { return `msg_${Date.now()}_${++msgIdCounter}` }

// ── State ──
const characterState = ref<CharacterState>('idle')
const volume = ref(0)
const statusLabel = ref('待机中')

const backendStatus = ref('未连接')
const backendMessage = ref('')
const pcmChunkCount = ref(0)

const userText = ref('')
const streamAsrText = ref('')
const streamAsrStatus = ref('')
const isAsrLoading = ref(false)
const isChatLoading = ref(false)
const isTtsLoading = ref(false)
const ttsSegmentIndex = ref(0)
const ttsSegmentTotal = ref(0)

const autoSpeakEnabled = ref(true)
const ttsMode = ref<TtsMode>('sentence')
const selectedModel = ref<ModelKey>('lite')
const selectedSpeaker = ref<SpeakerKey>('default')
const replyMode = ref<ReplyMode>('auto')
const showSettings = ref(false)

const messages = ref<Message[]>([])
const messagesEndRef = ref<HTMLElement | null>(null)

// ── Config ──
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

const replyModeCards = [
  { key: 'auto' as ReplyMode, name: '自动', desc: '智能切换' },
  { key: 'text_only' as ReplyMode, name: '纯文字', desc: '不朗读' },
  { key: 'voice_only' as ReplyMode, name: '纯语音', desc: '语音条' },
  { key: 'text_and_voice' as ReplyMode, name: '文字+语音', desc: '都要' },
]

const stateVideos: Record<CharacterState, string | null> = {
  idle: null, listening: null, thinking: null, speaking: null, happy: null, error: null,
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
let playingMsgId: string | null = null

// ── Scroll ──
async function scrollToBottom() {
  await nextTick()
  messagesEndRef.value?.scrollIntoView({ behavior: 'smooth' })
}

watch(messages, scrollToBottom, { deep: true })

// ── Auto reply mode decision ──
function decideEffectiveMode(userKind: MessageKind, replyText: string): 'text_only' | 'voice_only' | 'text_and_voice' {
  if (replyMode.value !== 'auto') return replyMode.value as 'text_only' | 'voice_only' | 'text_and_voice'

  const replyLen = replyText.length
  const r = Math.random()

  if (userKind === 'voice') {
    // user sent voice → lean toward voice
    if (replyLen > 120) return 'text_and_voice'
    return r < 0.6 ? 'voice_only' : 'text_and_voice'
  }

  const userLen = userText.value.length
  if (userLen > 60 || replyLen > 120) {
    // long text → prefer text or text+voice
    return r < 0.5 ? 'text_only' : 'text_and_voice'
  }

  // short text → random
  if (replyLen <= 30) return r < 0.5 ? 'voice_only' : 'text_only'
  return r < 0.4 ? 'voice_only' : r < 0.7 ? 'text_only' : 'text_and_voice'
}

// ── Backend ──
function handleBackendMessage(rawMessage: string) {
  try {
    const data = JSON.parse(rawMessage)
    if (data.type === 'asr_stream') {
      streamAsrText.value = data.text || ''
      streamAsrStatus.value = data.is_final ? '识别完成' : '识别中...'
      if (data.text) userText.value = data.text
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
  socket.onmessage = (e) => handleBackendMessage(e.data)
  socket.onerror = () => {
    backendStatus.value = '连接出错'
    backendMessage.value = '请检查 Python 后端是否正在运行'
  }
  socket.onclose = () => { backendStatus.value = '已断开' }
}

// ── TTS helpers ──
function splitTextIntoSentences(text: string): string[] {
  const clean = text.replace(/\r/g, '').replace(/\n+/g, ' ').replace(/\s+/g, ' ').trim()
  if (!clean) return []
  const matched = clean.match(/[^。！？!?；;\n]+[。！？!?；;]?/g) || [clean]
  const result: string[] = []
  for (const item of matched) {
    const s = item.trim()
    if (!s) continue
    if (s.length <= 90) { result.push(s) }
    else { for (let i = 0; i < s.length; i += 90) result.push(s.slice(i, i + 90)) }
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
  if (currentAudio) { currentAudio.pause(); currentAudio = null }
  currentAudio = new Audio(audioUrl)
  await new Promise<void>((resolve, reject) => {
    if (!currentAudio) { reject(new Error('音频对象创建失败')); return }
    currentAudio.onplay = () => { characterState.value = 'speaking'; statusLabel.value = '说话中' }
    currentAudio.onended = () => resolve()
    currentAudio.onerror = () => reject(new Error('浏览器播放音频失败'))
    currentAudio.play().catch(reject)
  })
}

async function speakTextAndGetUrl(text: string): Promise<string> {
  if (ttsMode.value === 'sentence') {
    const sentences = splitTextIntoSentences(text)
    if (!sentences.length) throw new Error('没有可播放的句子')
    ttsSegmentTotal.value = sentences.length
    let lastUrl = ''
    for (let i = 0; i < sentences.length; i++) {
      ttsSegmentIndex.value = i + 1
      const url = await requestTtsAudio(sentences[i])
      if (i === 0) lastUrl = url
      await playAudioUrl(url)
    }
    return lastUrl
  } else {
    ttsSegmentIndex.value = 1
    ttsSegmentTotal.value = 1
    const url = await requestTtsAudio(text)
    await playAudioUrl(url)
    return url
  }
}

// manual speak last linxi message
async function speakLastLinxiMessage() {
  const last = [...messages.value].reverse().find(m => m.role === 'linxi' && m.text)
  if (!last) return
  try {
    isTtsLoading.value = true
    characterState.value = 'speaking'
    await speakTextAndGetUrl(last.text)
    characterState.value = 'happy'
    statusLabel.value = '说完了'
    setTimeout(() => { characterState.value = 'idle'; statusLabel.value = '待机中' }, 1500)
  } catch (e) {
    console.error(e)
    characterState.value = 'error'
    statusLabel.value = '语音失败'
  } finally {
    isTtsLoading.value = false
  }
}

// play a specific message audio
async function playMessageAudio(msg: Message) {
  if (!msg.audioUrl) return
  if (playingMsgId === msg.id) {
    if (currentAudio) { currentAudio.pause(); currentAudio = null }
    playingMsgId = null
    msg.status = 'done'
    return
  }
  playingMsgId = msg.id
  msg.status = 'playing'
  try {
    await playAudioUrl(msg.audioUrl)
    msg.status = 'done'
  } catch {
    msg.status = 'error'
  } finally {
    if (playingMsgId === msg.id) playingMsgId = null
  }
}

// ── Send chat (text) ──
async function sendChat(textFromVoice?: string, userMsgKind: MessageKind = 'text') {
  const text = (textFromVoice ?? userText.value).trim()
  if (!text) return
  userText.value = ''

  // add user message
  const userMsg: Message = {
    id: newId(), role: 'user', kind: userMsgKind,
    text: userMsgKind === 'voice' ? '' : text,
    transcript: userMsgKind === 'voice' ? text : '',
    audioUrl: null, status: 'done', createdAt: new Date(), duration: null
  }
  messages.value.push(userMsg)

  // add linxi placeholder
  const linxiMsg: Message = {
    id: newId(), role: 'linxi', kind: 'text',
    text: '', transcript: '', audioUrl: null,
    status: 'thinking', createdAt: new Date(), duration: null,
    speaker: selectedSpeaker.value, model: selectedModel.value
  }
  messages.value.push(linxiMsg)

  try {
    isChatLoading.value = true
    characterState.value = 'thinking'
    statusLabel.value = '思考中'

    const response = await fetch('http://127.0.0.1:8000/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, model: selectedModel.value })
    })
    const data = await response.json()

    if (!data.ok) {
      linxiMsg.text = data.reply || '调用失败'
      linxiMsg.status = 'error'
      linxiMsg.kind = 'text'
      statusLabel.value = '调用失败'
      characterState.value = 'error'
      return
    }

    const replyText = data.reply as string
    const effective = decideEffectiveMode(userMsgKind, replyText)

    linxiMsg.text = replyText
    linxiMsg.kind = effective === 'voice_only' ? 'voice' : effective === 'text_and_voice' ? 'text_voice' : 'text'
    linxiMsg.status = effective === 'text_only' ? 'done' : 'playing'
    statusLabel.value = '回复完成'

    isChatLoading.value = false

    if (effective !== 'text_only') {
      try {
        isTtsLoading.value = true
        characterState.value = 'speaking'
        const firstUrl = await speakTextAndGetUrl(replyText)
        linxiMsg.audioUrl = firstUrl
        linxiMsg.status = 'done'
        characterState.value = 'happy'
        statusLabel.value = '说完了'
        setTimeout(() => { characterState.value = 'idle'; statusLabel.value = '待机中' }, 1500)
      } catch (e) {
        console.error(e)
        linxiMsg.status = 'error'
        characterState.value = 'error'
        statusLabel.value = '语音失败'
      } finally {
        isTtsLoading.value = false
      }
    } else {
      characterState.value = 'idle'
      statusLabel.value = '待机中'
    }

  } catch (error) {
    console.error(error)
    linxiMsg.text = '请求失败，请检查后端'
    linxiMsg.status = 'error'
    statusLabel.value = '连接失败'
    characterState.value = 'error'
  } finally {
    isChatLoading.value = false
  }
}

// ── ASR ──
async function recognizeLastAudio(autoSend = false) {
  try {
    isAsrLoading.value = true
    characterState.value = 'thinking'
    statusLabel.value = '识别中'

    const response = await fetch('http://127.0.0.1:8000/asr/recognize-last', { method: 'POST' })
    const data = await response.json()

    if (data.ok) {
      if (autoSend) {
        await sendChat(data.text, 'voice')
      } else {
        userText.value = data.text
        statusLabel.value = '识别完成'
        characterState.value = 'idle'
      }
    } else {
      statusLabel.value = '识别失败'
      characterState.value = 'error'
      messages.value.push({
        id: newId(), role: 'linxi', kind: 'text',
        text: `ASR 识别失败：${data.message || '未知错误'}`,
        transcript: '', audioUrl: null, status: 'error',
        createdAt: new Date(), duration: null
      })
    }
  } catch {
    characterState.value = 'error'
    statusLabel.value = '识别失败'
  } finally {
    isAsrLoading.value = false
    if (!isChatLoading.value && !isTtsLoading.value && characterState.value !== 'error') {
      characterState.value = 'idle'
      statusLabel.value = '待机中'
    }
  }
}

// ── Microphone / PCM streaming ──
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
  } catch {
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
  function update() {
    if (!analyser) return
    analyser.getByteFrequencyData(dataArray)
    let sum = 0
    for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]
    volume.value = Math.min(100, Math.round((sum / dataArray.length) * 2))
    animationId = requestAnimationFrame(update)
  }
  update()
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
    const buf = (pcm16.buffer as ArrayBuffer).slice(pcm16.byteOffset, pcm16.byteOffset + pcm16.byteLength)
    socket.send(buf)
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
    const si = i * ratio
    const li = Math.floor(si)
    const ri = Math.min(li + 1, input.length - 1)
    result[i] = input[li] * (1 - (si - li)) + input[ri] * (si - li)
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

function selectModel(m: ModelKey) { selectedModel.value = m }
function selectSpeaker(s: SpeakerKey) { selectedSpeaker.value = s }

const isProcessing = computed(() => isChatLoading.value || isTtsLoading.value || isAsrLoading.value)
</script>

<template>
  <main class="page">
    <!-- 背景光球 -->
    <div class="bg-orb bg-orb-1"></div>
    <div class="bg-orb bg-orb-2"></div>
    <div class="bg-orb bg-orb-3"></div>

    <!-- ── 顶部：林曦视觉化身 ── -->
    <header class="avatar-header">
      <div class="character-frame" :style="{ '--glow': characterGlow }">
        <video v-if="currentVideo" :src="currentVideo" autoplay loop muted playsinline class="character-media" />
        <img v-else-if="stateImage" :src="stateImage" class="character-media character-img"
          @error="(e) => (e.target as HTMLImageElement).style.display = 'none'" />
        <div v-else class="character-placeholder"><span class="placeholder-kanji">林曦</span></div>
        <div class="state-ring" :class="characterState"></div>
        <!-- 说话时音量波形叠在头像上 -->
        <div v-if="characterState === 'listening'" class="volume-waves-overlay">
          <span v-for="i in 5" :key="i" class="wave-bar"
            :style="{ animationDelay: `${i * 0.1}s`, height: `${8 + (volume / 100) * 28}px` }"></span>
        </div>
      </div>

      <div class="avatar-meta">
        <div class="avatar-name">林曦</div>
        <div class="state-badge" :class="characterState">{{ statusLabel }}</div>
      </div>

      <!-- 连接状态 + 设置按钮 -->
      <div class="header-actions">
        <button class="icon-btn btn-connect" @click="connectBackend" :title="backendStatus">
          <span class="btn-dot" :class="backendStatus === '已连接' ? 'dot-on' : 'dot-off'"></span>
        </button>
        <button class="icon-btn btn-settings" @click="showSettings = !showSettings" :class="{ active: showSettings }">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
        </button>
      </div>
    </header>

    <!-- 流式 ASR 实时字幕条 -->
    <div v-if="characterState === 'listening' && streamAsrText" class="asr-live-bar">
      <span class="asr-live-dot"></span>
      {{ streamAsrText }}
    </div>

    <!-- ── 中部：消息流 ── -->
    <section class="messages-area">
      <div v-if="messages.length === 0" class="empty-hint">
        <div class="empty-icon">✦</div>
        <div>向林曦发送消息，或点击麦克风说话</div>
      </div>

      <template v-for="msg in messages" :key="msg.id">
        <!-- 用户消息（右侧） -->
        <div v-if="msg.role === 'user'" class="msg-row msg-row-user">
          <div class="msg-content msg-content-user">
            <!-- 文字气泡 -->
            <div v-if="msg.kind === 'text'" class="bubble bubble-user">{{ msg.text }}</div>
            <!-- 语音条（用户录音） -->
            <div v-if="msg.kind === 'voice' || msg.kind === 'text_voice'" class="voice-bar voice-bar-user">
              <div class="voice-bar-inner">
                <svg class="voice-play-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5,3 19,12 5,21"/>
                </svg>
                <div class="voice-waveform">
                  <span v-for="j in 12" :key="j" class="wf-bar" :style="{ height: `${6 + Math.sin(j * 1.3) * 8 + 4}px` }"></span>
                </div>
              </div>
              <div v-if="msg.transcript" class="voice-transcript voice-transcript-user">{{ msg.transcript }}</div>
            </div>
          </div>
        </div>

        <!-- 林曦消息（左侧） -->
        <div v-else class="msg-row msg-row-linxi">
          <div class="msg-avatar-small">
            <img :src="stateImage" class="msg-avatar-img" />
          </div>
          <div class="msg-content msg-content-linxi">
            <!-- 思考中占位 -->
            <div v-if="msg.status === 'thinking'" class="bubble bubble-linxi bubble-thinking">
              <span class="thinking-dot"></span>
              <span class="thinking-dot"></span>
              <span class="thinking-dot"></span>
            </div>

            <!-- 文字气泡 -->
            <div v-else-if="msg.kind === 'text'" class="bubble bubble-linxi" :class="{ 'bubble-error': msg.status === 'error' }">
              {{ msg.text }}
            </div>

            <!-- 语音条（林曦语音） -->
            <div v-else-if="msg.kind === 'voice'" class="voice-bar voice-bar-linxi">
              <div class="voice-bar-inner" @click="playMessageAudio(msg)" style="cursor:pointer">
                <svg class="voice-play-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"
                  :class="{ 'icon-playing': msg.status === 'playing' }">
                  <polygon v-if="msg.status !== 'playing'" points="5,3 19,12 5,21"/>
                  <rect v-else x="6" y="4" width="4" height="16"/><rect v-if="msg.status === 'playing'" x="14" y="4" width="4" height="16"/>
                </svg>
                <div class="voice-waveform" :class="{ 'waveform-playing': msg.status === 'playing' }">
                  <span v-for="j in 12" :key="j" class="wf-bar" :style="{ height: `${6 + Math.sin(j * 1.1) * 8 + 4}px` }"></span>
                </div>
              </div>
              <div v-if="msg.text" class="voice-transcript voice-transcript-linxi">{{ msg.text }}</div>
            </div>

            <!-- 文字 + 语音 -->
            <div v-else-if="msg.kind === 'text_voice'">
              <div class="bubble bubble-linxi" :class="{ 'bubble-error': msg.status === 'error' }">{{ msg.text }}</div>
              <div v-if="msg.audioUrl" class="voice-bar voice-bar-linxi voice-bar-attached">
                <div class="voice-bar-inner" @click="playMessageAudio(msg)" style="cursor:pointer">
                  <svg class="voice-play-icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"
                    :class="{ 'icon-playing': msg.status === 'playing' }">
                    <polygon v-if="msg.status !== 'playing'" points="5,3 19,12 5,21"/>
                    <rect v-else x="6" y="4" width="4" height="16"/><rect v-if="msg.status === 'playing'" x="14" y="4" width="4" height="16"/>
                  </svg>
                  <div class="voice-waveform" :class="{ 'waveform-playing': msg.status === 'playing' }">
                    <span v-for="j in 12" :key="j" class="wf-bar" :style="{ height: `${6 + Math.sin(j * 1.1) * 8 + 4}px` }"></span>
                  </div>
                  <span class="voice-bar-label">朗读</span>
                </div>
              </div>
              <div v-else-if="msg.status === 'playing' || isTtsLoading" class="voice-bar voice-bar-linxi voice-bar-attached">
                <div class="voice-bar-inner">
                  <svg class="voice-play-icon icon-playing" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>
                  </svg>
                  <div class="voice-waveform waveform-playing">
                    <span v-for="j in 12" :key="j" class="wf-bar" :style="{ height: `${6 + Math.sin(j * 1.1) * 8 + 4}px` }"></span>
                  </div>
                  <span class="voice-bar-label tts-progress-label">{{ ttsSegmentIndex }}/{{ ttsSegmentTotal }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div ref="messagesEndRef"></div>
    </section>

    <!-- ── 底部：输入区 ── -->
    <footer class="input-footer">
      <div class="input-row">
        <!-- 录音按钮 -->
        <button
          class="icon-btn input-icon-btn btn-mic"
          :class="{ active: characterState === 'listening' }"
          @click="characterState === 'listening' ? stopListening() : startListening()"
          :disabled="isProcessing && characterState !== 'listening'"
          :title="characterState === 'listening' ? '停止录音' : '开始录音'"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect v-if="characterState === 'listening'" x="6" y="4" width="4" height="16" fill="currentColor" stroke="none"/>
            <rect v-if="characterState === 'listening'" x="14" y="4" width="4" height="16" fill="currentColor" stroke="none"/>
            <path v-else d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
            <path v-if="characterState !== 'listening'" d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line v-if="characterState !== 'listening'" x1="12" y1="19" x2="12" y2="23"/>
            <line v-if="characterState !== 'listening'" x1="8" y1="23" x2="16" y2="23"/>
          </svg>
        </button>

        <!-- 文字输入 -->
        <textarea
          v-model="userText"
          class="chat-input"
          :placeholder="characterState === 'listening' ? '聆听中…' : '发消息给林曦，Enter 发送'"
          rows="1"
          @keydown.enter.exact.prevent="sendChat()"
          @keydown.enter.shift.exact="() => {}"
          :disabled="characterState === 'listening'"
        ></textarea>

        <!-- 语音问 -->
        <button
          class="icon-btn input-icon-btn btn-ask"
          @click="recognizeLastAudio(true)"
          :disabled="isAsrLoading || isChatLoading || isTtsLoading || characterState === 'listening'"
          title="语音问林曦"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
        </button>

        <!-- 朗读 -->
        <button
          class="icon-btn input-icon-btn btn-speak"
          @click="speakLastLinxiMessage()"
          :disabled="isTtsLoading || isChatLoading"
          title="朗读林曦最新回复"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="11,5 6,9 2,9 2,15 6,15 11,19"/>
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
            <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
          </svg>
        </button>

        <!-- 发送 -->
        <button
          class="send-btn"
          @click="sendChat()"
          :disabled="isChatLoading || isTtsLoading || !userText.trim()"
          title="发送"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="22" y1="2" x2="11" y2="13"/>
            <polygon points="22,2 15,22 11,13 2,9"/>
          </svg>
        </button>
      </div>
    </footer>

    <!-- ── 设置面板（slide-up overlay） ── -->
    <transition name="panel-slide">
      <div v-if="showSettings" class="settings-panel">
        <div class="panel-drag-handle" @click="showSettings = false"></div>

        <div class="panel-section">
          <div class="panel-label">回复模式</div>
          <div class="chip-row">
            <button v-for="item in replyModeCards" :key="item.key"
              class="chip" :class="{ active: replyMode === item.key }"
              @click="replyMode = item.key" :title="item.desc">{{ item.name }}</button>
          </div>
        </div>

        <div class="panel-section">
          <div class="panel-label">大模型</div>
          <div class="chip-row">
            <button v-for="item in modelCards" :key="item.key"
              class="chip" :class="{ active: selectedModel === item.key }"
              @click="selectModel(item.key)" :title="item.model">{{ item.name }}</button>
          </div>
        </div>

        <div class="panel-section">
          <div class="panel-label">音色</div>
          <div class="chip-row">
            <button v-for="item in speakerCards" :key="item.key"
              class="chip chip-speaker" :class="{ active: selectedSpeaker === item.key }"
              @click="selectSpeaker(item.key)">{{ item.name }}</button>
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
            <span>回复后自动朗读（text_only 模式下无效）</span>
          </label>
        </div>

        <div class="panel-section panel-debug">
          <div class="debug-line">后端：{{ backendStatus }}</div>
          <div class="debug-line" v-if="backendMessage">{{ backendMessage }}</div>
          <div class="debug-line" v-if="streamAsrStatus">ASR：{{ streamAsrStatus }}</div>
          <div class="debug-line" v-if="characterState === 'listening'">PCM：{{ pcmChunkCount }} 段</div>
          <div class="debug-line">replyMode：{{ replyMode }}</div>
        </div>
      </div>
    </transition>

    <!-- 点击遮罩关闭设置 -->
    <div v-if="showSettings" class="settings-overlay" @click="showSettings = false"></div>
  </main>
</template>

<style scoped>
*, *::before, *::after { box-sizing: border-box; }

.page {
  position: relative;
  width: 100vw;
  height: 100vh;
  background: radial-gradient(ellipse at 50% 0%, #1a2a4a 0%, #0d1420 55%, #050810 100%);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
  color: #e8f0ff;
}

/* 背景光球 */
.bg-orb { position: absolute; border-radius: 50%; filter: blur(80px); pointer-events: none; z-index: 0; }
.bg-orb-1 { width: 500px; height: 500px; top: -120px; left: -100px; background: rgba(56, 100, 200, 0.18); }
.bg-orb-2 { width: 400px; height: 400px; top: 20%; right: -80px; background: rgba(160, 80, 220, 0.12); }
.bg-orb-3 { width: 300px; height: 300px; bottom: 10%; left: 30%; background: rgba(30, 180, 200, 0.10); }

/* ── 顶部头像栏 ── */
.avatar-header {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 18px 12px;
  background: rgba(10, 20, 40, 0.6);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid rgba(255,255,255,0.07);
  flex-shrink: 0;
}

.character-frame {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  overflow: hidden;
  border: 2px solid rgba(255,255,255,0.15);
  box-shadow: 0 0 16px var(--glow, rgba(125,211,252,0.4));
  transition: box-shadow 0.4s ease;
  flex-shrink: 0;
}
.character-media { width: 100%; height: 100%; object-fit: cover; object-position: top center; }
.character-placeholder { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, rgba(100,160,255,0.15), rgba(180,100,255,0.15)); }
.placeholder-kanji { font-size: 18px; font-weight: 900; background: linear-gradient(135deg, #7dd3fc, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }

.state-ring {
  position: absolute; inset: 0; border-radius: 50%; pointer-events: none;
  transition: box-shadow 0.4s, border-color 0.4s; border: 2px solid transparent;
}
.state-ring.listening { border-color: rgba(34,211,238,0.7); }
.state-ring.thinking  { border-color: rgba(250,204,21,0.6); }
.state-ring.speaking  { border-color: rgba(244,114,182,0.7); }
.state-ring.happy     { border-color: rgba(134,239,172,0.7); }
.state-ring.error     { border-color: rgba(248,113,113,0.7); }

.volume-waves-overlay {
  position: absolute; bottom: 2px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: flex-end; gap: 2px; height: 20px;
}
.wave-bar { display: block; width: 3px; border-radius: 2px; background: #22d3ee; animation: wave-pulse 0.6s ease-in-out infinite alternate; transition: height 0.08s linear; }
@keyframes wave-pulse { from { opacity: 0.5; transform: scaleY(0.7); } to { opacity: 1; transform: scaleY(1); } }

.avatar-meta { flex: 1; min-width: 0; }
.avatar-name { font-size: 16px; font-weight: 700; letter-spacing: 1px; color: #e8f0ff; }
.state-badge {
  display: inline-block; margin-top: 3px; padding: 2px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
  background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
  transition: all 0.3s;
}
.state-badge.listening { background: rgba(34,211,238,0.15); border-color: rgba(34,211,238,0.4); color: #67e8f9; }
.state-badge.thinking  { background: rgba(250,204,21,0.15);  border-color: rgba(250,204,21,0.4);  color: #fde047; }
.state-badge.speaking  { background: rgba(244,114,182,0.15); border-color: rgba(244,114,182,0.4); color: #f9a8d4; }
.state-badge.happy     { background: rgba(134,239,172,0.15); border-color: rgba(134,239,172,0.4); color: #86efac; }
.state-badge.error     { background: rgba(248,113,113,0.15); border-color: rgba(248,113,113,0.4); color: #fca5a5; }

.header-actions { display: flex; gap: 8px; align-items: center; }

/* 通用图标按钮 */
.icon-btn {
  width: 36px; height: 36px; border-radius: 50%; border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.07); color: rgba(255,255,255,0.7);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.2s; flex-shrink: 0;
}
.icon-btn:hover:not(:disabled) { background: rgba(255,255,255,0.14); color: #fff; }
.icon-btn.active { background: rgba(56,189,248,0.2); border-color: rgba(56,189,248,0.5); color: #7dd3fc; }
.icon-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.btn-connect {}
.btn-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot-on  { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
.dot-off { background: #94a3b8; }

/* ── 实时字幕条 ── */
.asr-live-bar {
  z-index: 2;
  background: rgba(34,211,238,0.1);
  border-bottom: 1px solid rgba(34,211,238,0.2);
  padding: 6px 18px;
  font-size: 13px;
  color: #67e8f9;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.asr-live-dot {
  width: 7px; height: 7px; border-radius: 50%; background: #22d3ee;
  animation: live-blink 1s infinite;
  flex-shrink: 0;
}
@keyframes live-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

/* ── 消息流 ── */
.messages-area {
  flex: 1;
  overflow-y: auto;
  padding: 16px 16px 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 1;
  scroll-behavior: smooth;
}

.messages-area::-webkit-scrollbar { width: 4px; }
.messages-area::-webkit-scrollbar-track { background: transparent; }
.messages-area::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }

.empty-hint {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: rgba(255,255,255,0.2);
  font-size: 14px;
  text-align: center;
  padding: 40px 0;
}
.empty-icon { font-size: 28px; opacity: 0.3; }

/* 消息行 */
.msg-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  max-width: 100%;
}
.msg-row-user { flex-direction: row-reverse; }
.msg-row-linxi { flex-direction: row; }

.msg-avatar-small { width: 32px; height: 32px; border-radius: 50%; overflow: hidden; flex-shrink: 0; }
.msg-avatar-img { width: 100%; height: 100%; object-fit: cover; object-position: top center; }

.msg-content { max-width: 72%; display: flex; flex-direction: column; gap: 6px; }
.msg-content-user { align-items: flex-end; }
.msg-content-linxi { align-items: flex-start; }

/* 文字气泡 */
.bubble {
  padding: 10px 14px;
  border-radius: 18px;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  max-width: 100%;
}

.bubble-user {
  background: linear-gradient(135deg, rgba(59,130,246,0.5), rgba(99,102,241,0.5));
  border: 1px solid rgba(99,102,241,0.35);
  border-bottom-right-radius: 4px;
  color: #e8f0ff;
}

.bubble-linxi {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.1);
  border-bottom-left-radius: 4px;
  color: #e8f0ff;
  backdrop-filter: blur(8px);
}

.bubble-error {
  background: rgba(248,113,113,0.12);
  border-color: rgba(248,113,113,0.3);
  color: #fca5a5;
}

/* 思考中动画 */
.bubble-thinking {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 12px 18px;
}
.thinking-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: rgba(125,211,252,0.6);
  animation: thinking-bounce 1.2s ease-in-out infinite;
}
.thinking-dot:nth-child(2) { animation-delay: 0.2s; }
.thinking-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes thinking-bounce {
  0%,80%,100% { transform: translateY(0); opacity: 0.5; }
  40% { transform: translateY(-6px); opacity: 1; }
}

/* ── 语音条 ── */
.voice-bar {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 100%;
}

.voice-bar-inner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 18px;
  min-width: 140px;
  max-width: 240px;
}

.voice-bar-user .voice-bar-inner {
  background: linear-gradient(135deg, rgba(59,130,246,0.45), rgba(99,102,241,0.45));
  border: 1px solid rgba(99,102,241,0.3);
  border-bottom-right-radius: 4px;
  flex-direction: row-reverse;
}

.voice-bar-linxi .voice-bar-inner {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.1);
  border-bottom-left-radius: 4px;
  backdrop-filter: blur(8px);
}

.voice-bar-attached { margin-top: 2px; }
.voice-bar-attached .voice-bar-inner { border-radius: 12px; }

.voice-play-icon { flex-shrink: 0; color: rgba(255,255,255,0.8); transition: color 0.2s; }
.icon-playing { color: #f9a8d4; animation: icon-pulse 1s ease-in-out infinite; }
@keyframes icon-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.6; } }

.voice-waveform {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 1;
}
.wf-bar {
  display: block;
  width: 3px;
  border-radius: 2px;
  background: rgba(255,255,255,0.35);
  min-height: 4px;
  transition: background 0.3s;
}
.waveform-playing .wf-bar {
  background: rgba(249,168,212,0.7);
  animation: wf-dance 0.5s ease-in-out infinite alternate;
}
.waveform-playing .wf-bar:nth-child(odd) { animation-delay: 0.1s; }
.waveform-playing .wf-bar:nth-child(3n) { animation-delay: 0.2s; }
@keyframes wf-dance { from { transform: scaleY(0.5); } to { transform: scaleY(1.3); } }

.voice-bar-label {
  font-size: 11px;
  color: rgba(255,255,255,0.4);
  white-space: nowrap;
  flex-shrink: 0;
}
.tts-progress-label { color: #f9a8d4; }

.voice-transcript {
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255,255,255,0.45);
  padding: 0 4px;
  word-break: break-word;
}
.voice-transcript-user { text-align: right; }
.voice-transcript-linxi { text-align: left; }

/* ── 底部输入区 ── */
.input-footer {
  z-index: 2;
  flex-shrink: 0;
  background: rgba(10,20,40,0.75);
  backdrop-filter: blur(16px);
  border-top: 1px solid rgba(255,255,255,0.07);
  padding: 10px 14px 14px;
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  max-width: 720px;
  margin: 0 auto;
}

.input-icon-btn {
  width: 40px; height: 40px;
}

.btn-mic.active {
  background: rgba(239,68,68,0.25);
  border-color: rgba(252,165,165,0.5);
  color: #fca5a5;
  animation: pulse-mic 1.2s infinite;
}
@keyframes pulse-mic {
  0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0.4); }
  50% { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}

.btn-ask { color: rgba(251,191,36,0.8); }
.btn-ask:hover:not(:disabled) { color: #fbbf24; }
.btn-speak { color: rgba(244,114,182,0.8); }
.btn-speak:hover:not(:disabled) { color: #f472b6; }

.chat-input {
  flex: 1;
  padding: 10px 14px;
  border-radius: 20px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.07);
  color: #e8f0ff;
  font-size: 14px;
  font-family: inherit;
  resize: none;
  outline: none;
  transition: border-color 0.2s;
  line-height: 1.5;
  max-height: 100px;
  overflow-y: auto;
}
.chat-input::placeholder { color: rgba(255,255,255,0.28); }
.chat-input:focus { border-color: rgba(125,211,252,0.35); }
.chat-input:disabled { opacity: 0.4; }

.send-btn {
  width: 40px; height: 40px; border-radius: 50%;
  background: linear-gradient(135deg, rgba(56,189,248,0.7), rgba(99,102,241,0.7));
  border: none; color: #fff;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all 0.2s; flex-shrink: 0;
}
.send-btn:hover:not(:disabled) { filter: brightness(1.2); transform: scale(1.05); }
.send-btn:disabled { opacity: 0.3; cursor: not-allowed; }

/* ── 设置面板 ── */
.settings-overlay {
  position: fixed; inset: 0; z-index: 9;
  background: rgba(0,0,0,0.3);
  backdrop-filter: blur(2px);
}

.settings-panel {
  position: fixed;
  bottom: 0; left: 50%;
  transform: translateX(-50%);
  width: min(600px, 100vw);
  z-index: 10;
  background: rgba(8,16,32,0.97);
  backdrop-filter: blur(24px);
  border-top: 1px solid rgba(255,255,255,0.1);
  border-radius: 24px 24px 0 0;
  padding: 8px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-drag-handle {
  width: 40px; height: 4px; border-radius: 2px;
  background: rgba(255,255,255,0.2);
  margin: 0 auto 8px;
  cursor: pointer;
}
.panel-drag-handle:hover { background: rgba(255,255,255,0.4); }

.panel-section { display: flex; flex-direction: column; gap: 8px; }
.panel-label { font-size: 11px; font-weight: 600; opacity: 0.45; letter-spacing: 1.5px; text-transform: uppercase; }

.chip-row { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  border: 1px solid rgba(255,255,255,0.15); border-radius: 999px;
  padding: 6px 16px; font-size: 13px; font-weight: 600;
  color: rgba(255,255,255,0.65); background: rgba(255,255,255,0.06);
  cursor: pointer; transition: all 0.2s;
}
.chip:hover { background: rgba(255,255,255,0.1); color: #fff; }
.chip.active { border-color: #38bdf8; background: rgba(56,189,248,0.18); color: #7dd3fc; }
.chip-speaker.active { border-color: #a78bfa; background: rgba(167,139,250,0.18); color: #c4b5fd; }

.toggle-row { display: flex; align-items: center; gap: 10px; font-size: 14px; cursor: pointer; }

.panel-debug { border-top: 1px solid rgba(255,255,255,0.06); padding-top: 12px; gap: 4px; }
.debug-line { font-size: 12px; opacity: 0.4; }

/* 面板动画 */
.panel-slide-enter-active, .panel-slide-leave-active { transition: transform 0.3s cubic-bezier(0.4,0,0.2,1); }
.panel-slide-enter-from, .panel-slide-leave-to { transform: translateX(-50%) translateY(100%); }
</style>

