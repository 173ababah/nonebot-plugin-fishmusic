# nonebot-plugin-fishmusic 🐟

> `/点歌 歌名` → 网易云搜到、下下来、**当语音条发出去**。**零 LLM 开销。**

`astrbot_plugin_music` 的最小可用版：不建歌单、不做 UI，就一件事——点一首，听一首。

## 特性

- 网易云公开搜索接口找歌（默认取第一条）
- 下载走 `song/media/outer/url`，超过 `FISHMUSIC_MAX_MB` 就不下，直接给链接
- **会员歌（网易云 `fee=1/4`）换个源拿同一首**：先按「歌名 + 歌手」去别的曲库找最像的一条，
  再取播放地址。顺序：**酷我** → **海棠**（`musicserver.haitangw.cc`，从 App 里扒出来的）→ **GD Studio**
- **认得「试听片段」**：独家版权的歌（比如周杰伦），别的源也只给 10~30 秒 ——
  拿原曲时长一比，短太多就丢掉，不会把 10 秒当整首发出去
- 配上 `to_silk.py`（要 `pilk`）就发 QQ 原生语音（更省流量、能倍速）；没有就发 mp3
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
FISHMUSIC_PIRATE=true         # 会员歌去别的源找（默认开）
FISHMUSIC_PIRATE_LEVEL=standard  # 海棠接口音质档：standard / high / lossless
FISHMUSIC_SILK_BIN=           # 可选：to_silk.py 的路径
FISHMUSIC_SILK_PY=python3
```

> SILK 转换要 `pip install pilk`，并且 `FISHMUSIC_SILK_PY` 要指向**装了 pilk 的那个解释器**
> （venv 里装的就用 `/path/to/venv/bin/python`，别指望 systemd 的 PATH）。

## 注意

- 会员歌靠公共曲库兜底，**不是 100% 有**：源挂了、歌太冷门、独家版权（只给试听）都会落到「给链接」。
- 各源的可用性会变（实测 GD Studio 时不时 503），所以按顺序试，谁先给就听谁的。
- 别拿它当批量下载器用。

## License

MIT © 173ababah