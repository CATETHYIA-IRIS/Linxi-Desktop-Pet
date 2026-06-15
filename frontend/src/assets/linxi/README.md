# 林曦状态素材目录

把以下素材放到此目录，App.vue 会自动识别并播放：

| 文件名 | 状态 | 说明 |
|---|---|---|
| avatar_idle.png | 默认图片 | 没有视频时显示的静态立绘 |
| idle.mp4 | 待机 | 循环播放 |
| listening.mp4 | 聆听 | 用户录音时 |
| thinking.mp4 | 思考 | 等待 LLM 回复时 |
| speaking.mp4 | 说话 | TTS 播放时 |
| happy.mp4 | 开心 | TTS 播放完成后短暂显示 |
| error.mp4 | 出错 | 发生错误时 |

素材放好后，在 App.vue 的 `stateVideos` 对象里把 null 替换为对应的 import 路径即可。

示例：
```ts
import idleVideo from './assets/linxi/idle.mp4'

const stateVideos: Record<CharacterState, string | null> = {
  idle: idleVideo,
  ...
}
```
