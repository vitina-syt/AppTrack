# 🎙️ 语音转文字优化指南

## 问题症状诊断

| 症状 | 原因 | 解决方案 |
|------|------|--------|
| **转录文本频繁被割裂** | 静音检测太敏感 | ↑ `silence_thresh` / `silence_secs` |
| **微弱语音无法识别** | 麦克风音量太小 | 使用 `/api/util/mic-check` 检查 |
| **背景噪音导致误触发** | RMS阈值过低 | ↑ `silence_thresh` 至 0.010-0.015 |
| **完整句子被分成5段** | `silence_secs` 太短 | ↑ `silence_secs` 至 2.5-3.0 秒 |

---

## 📋 三层优化方案

### 🟢 方案 1：快速参数调整（已应用）

**已自动调整的参数**（在 `autocad_agent.py` 中）：

```python
VoiceCapture(
    silence_thresh=0.010,      # ↑ 从 0.004 提高（减少噪音误触）
    silence_secs=2.5,          # ↑ 从 1.5 延长（保留自然停顿）
    max_segment_secs=60.0,     # ↑ 从 30 增加（支持长叙述）
)
```

**预期效果**：
- ✅ 减少碎片化转录
- ✅ 保持句子连贯性
- ✅ 降低噪音触发概率

---

### 🟡 方案 2：高级 VAD（语音活动检测）

**已实现的改进**（在 `voice_capture.py` 中）：

```python
def _compute_speech_energy(raw: bytes) -> tuple[float, float]:
    """
    计算 RMS 和语音分数
    - Speech (正常说话): RMS 0.01-0.2，speech_score > 0.01
    - Silence (寂静): RMS < 0.008，speech_score < 0.008
    - Noise (背景噪音): RMS 0.005-0.01，speech_score 中等
    """
```

**改进点**：
- 不再依赖单个 frame 的 RMS
- 用 **5-frame 平均**判断（约 256ms 窗口）
- 自动区分语音、寂静和噪音

**工作原理**：
```
Frame 1: RMS=0.012 ✓ (speech)
Frame 2: RMS=0.006 ✗ (quiet)
Frame 3: RMS=0.008 ✗ (quiet)
Frame 4: RMS=0.007 ✗ (breath)
Frame 5: RMS=0.011 ✓ (speech again)

avg = 0.0088 < 0.010 → 判定为停顿，但等待更多确认
```

---

### 🔵 方案 3：短段自动合并

**已实现的合并逻辑**（在 `voice_capture.py` 中）：

```python
# 配置
_min_segment_frames = 0.5秒 = 512帧

流程：
┌─────────────────────────┐
│  录音段 (300ms)         │  ← 太短！
└─────────────────────────┘
           ↓ 缓冲
┌─────────────────────────┐
│  下一个段 (2秒)         │
└─────────────────────────┘
           ↓ 合并
┌─────────────────────────┐
│  合并段 (2.3秒)         │  ← 发送转录
└─────────────────────────┘
```

**优势**：
- 避免 "um"、"uh" 这样的微弱音被单独转录
- 自动补救检测参数不完美的情况
- 转录文本更自然

---

## 🔧 高级微调

### 场景 1：噪音环境

```python
# 麦克风旁有风扇、空调等背景噪音
VoiceCapture(
    silence_thresh=0.015,   # ↑ 更高的阈值
    silence_secs=3.0,       # ↑ 更长的停顿
    max_segment_secs=60.0,
)
```

### 场景 2：说话很轻

```python
# 需要识别低声说话
VoiceCapture(
    silence_thresh=0.006,   # ↓ 稍低的阈值
    silence_secs=2.0,       
    max_segment_secs=60.0,
)

# 同时确保麦克风设备正确
# → 运行 GET /api/util/mic-check 诊断
```

### 场景 3：自然对话（有停顿）

```python
# 演讲中有意的停顿（思考、强调）
VoiceCapture(
    silence_thresh=0.009,   
    silence_secs=3.5,       # ↑ 很长的停顿容限
    max_segment_secs=90.0,  # ↑ 更长的单段
)
```

---

## 🧪 测试和验证

### 使用前测试麦克风

```bash
curl "http://localhost:8000/api/util/mic-check?duration_ms=3000"
```

**响应示例**：
```json
{
  "pyaudio_available": true,
  "whisper_available": true,
  "devices": [
    {
      "index": 0,
      "name": "Microphone (USB Device)",
      "channels": 1,
      "sample_rate": 48000,
      "default": true
    }
  ],
  "rms": 0.042,              # ← 关键指标
  "peak": 0.087,
  "has_speech": true,        # ← 应该是 true
  "silence_thresh": 0.01
}
```

**诊断方法**：

| RMS 值 | 状态 | 动作 |
|---------|------|------|
| < 0.005 | 太弱 | 靠近麦克风或调高系统音量 |
| 0.01-0.05 | ✓ 正常 | 可以开始录制 |
| 0.05-0.15 | 强 | 可能过响，检查是否有噪音 |
| > 0.15 | 太强 | 降低系统音量或话筒位置 |

---

## 📊 监控转录质量

查看数据库以评估转录段的分布：

```sql
-- 看转录段的时间分布
SELECT 
    voice_text,
    voice_confidence,
    LENGTH(voice_text) as text_len,
    timestamp
FROM scribe_events
WHERE event_type = 'voice_segment'
ORDER BY timestamp DESC
LIMIT 20;

-- 统计
SELECT 
    COUNT(*) as segment_count,
    AVG(LENGTH(voice_text)) as avg_text_length,
    AVG(voice_confidence) as avg_confidence
FROM scribe_events
WHERE event_type = 'voice_segment' 
  AND session_id = ?;
```

**理想情况**：
- `segment_count` = 3-5（一段3分钟视频）
- `avg_text_length` > 10 字符
- `avg_confidence` > 0.90

---

## 🚀 进一步优化建议

### 1. 可选：安装 Silero VAD

如果想要最先进的语音检测（需要 PyTorch）：

```bash
pip install torch -i https://pypi.tsinghua.edu.cn/simple
pip install silero-vad
```

代码已预留接口，自动激活（见 `_SILERO_VAD` 标志）。

### 2. 离线转录（超低延迟）

考虑本地 Whisper 模型：

```bash
pip install openai-whisper
whisper audio.wav --language Chinese --model small
```

### 3. 实时转录反馈

前端可以通过 WebSocket 监听转录事件：

```javascript
// (伪代码)
ws.on('voice_segment', (seg) => {
    console.log(`📝 转录：${seg.voice_text}`);
});
```

---

## 📞 调试快速清单

- [ ] 运行 `GET /api/util/mic-check`，确认 RMS > 0.01
- [ ] 参数已应用：`silence_thresh=0.010`, `silence_secs=2.5`
- [ ] 开始录制，说一句话（如 "我喜欢这个功能"）
- [ ] 查看 `scribe_events` 表，确认转录文本完整且准确
- [ ] 如仍有问题，收集诊断数据：
  - 音频文件（可选）
  - RMS/峰值指标
  - 当前的 Whisper API 响应
