# nonebot-plugin-fishmusic 🐟

> `/点歌 歌名` → 网易云搜到、下下来、**当语音条发出去**。**零 LLM 开销。**

`astrbot_plugin_music` 的最小可用版：不建歌单、不做 UI，就一件事——点一首，听一首。

## 特性

- 网易云公开搜索接口找歌（默认取第一条）
- 下载走 `song/media/outer/url`，超过 `FISHMUSIC_MAX_MB` 就不下，直接给链接
- 配上 `to_silk.py` 就发 QQ 原生语音（更省流量、能倍速）；没有就发 mp3
- 每会话每分钟限流（默认 2 首）

## 安装

```bash
nb plugin install nonebot-plugin-fishmusic
```

## 配置（`.env`）

```ini
FISHMUSIC_ENABLED=true
FISHMUSIC_MAX_MB=8            # 单首上限
FISHMUSIC_PER_MIN=2           # 每分钟最多几首
FISHMUSIC_SILK_BIN=           # 可选：to_silk.py 的路径
FISHMUSIC_SILK_PY=python3
```

## 注意

版权/会员曲目经常下不下来，这时会给网易云链接而不是语音。别拿它当下载器用。

## License

MIT © 173ababah