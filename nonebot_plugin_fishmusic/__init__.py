"""nonebot-plugin-fishmusic —— 网易云点歌（直接发语音条），零 LLM 开销。"""
from __future__ import annotations

import base64
import os
import re
import tempfile
import time

import httpx
from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="鱼式点歌台",
    description="网易云点歌，直接把歌当语音条发出来（零 LLM 开销）",
    usage="/点歌 歌名 或 /点歌 歌手 - 歌名",
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

# nonebot 2.1 / 2.5 都能用：老版本没有 get_plugin_config
def _load_config():
    cfg = get_driver().config
    dump = getattr(cfg, "model_dump", None)
    return Config(**(dump() if callable(dump) else cfg.dict()))
plugin_config = _load_config()
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fishmusic/0.1",
       "Referer": "https://music.163.com/"}
_rate: dict[str, list[float]] = {}


def _chat_key(event: MessageEvent) -> str:
    if getattr(event, "group_id", None):
        return "group:%s" % event.group_id
    return "private:%s" % event.user_id


def _rate_ok(event: MessageEvent) -> bool:
    if plugin_config.fishmusic_per_min <= 0:
        return True
    key = _chat_key(event)
    now = time.time()
    hits = [t for t in _rate.get(key, []) if now - t < 60]
    if len(hits) >= plugin_config.fishmusic_per_min:
        _rate[key] = hits
        return False
    hits.append(now)
    _rate[key] = hits
    return True


async def search(keyword: str) -> list[dict]:
    """网易云搜索（公开接口）。返回 [{id,name,artist}]。"""
    async with httpx.AsyncClient(timeout=12, headers=_UA) as c:
        r = await c.get("https://music.163.com/api/search/get/web",
                        params={"s": keyword, "type": 1, "limit": 5, "offset": 0})
    if r.status_code != 200:
        return []
    try:
        d = r.json()
    except ValueError:
        return []
    songs = ((d.get("result") or {}).get("songs") or [])
    out = []
    for s in songs[:5]:
        artists = "、".join(a.get("name", "") for a in (s.get("artists") or [])[:2])
        out.append({"id": s.get("id"), "name": s.get("name") or "", "artist": artists})
    return out


async def download(song_id: int) -> str | None:
    """下 mp3 到临时文件；超过上限就放弃。"""
    limit = int(plugin_config.fishmusic_max_mb * 1024 * 1024)
    url = "https://music.163.com/song/media/outer/url?id=%s.mp3" % song_id
    try:
        async with httpx.AsyncClient(timeout=30, headers=_UA, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                ctype = r.headers.get("content-type", "")
                if "audio" not in ctype and "octet-stream" not in ctype:
                    return None
                fd, tmp = tempfile.mkstemp(suffix=".mp3")
                got = 0
                with os.fdopen(fd, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        got += len(chunk)
                        if got > limit:
                            return None
                        f.write(chunk)
        return tmp if os.path.getsize(tmp) > 20000 else None
    except Exception as e:
        logger.debug("[fishmusic] 下载失败 %r" % (e,))
        return None


def _silk(path: str) -> str:
    """可选：转成 QQ 原生 SILK（配了 FISHMUSIC_SILK_BIN 才走）。"""
    bin_path = plugin_config.fishmusic_silk_bin
    if not bin_path or not os.path.exists(bin_path):
        return ""
    import subprocess

    out = path.rsplit(".", 1)[0] + ".silk"
    try:
        subprocess.run([plugin_config.fishmusic_silk_py or "python3", bin_path, path, out, "24000"],
                       check=True, timeout=90,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out if os.path.exists(out) and os.path.getsize(out) > 2000 else ""
    except Exception:
        return ""


music_cmd = on_command("点歌", aliases={"music", "来首歌"}, priority=15, block=True)


@music_cmd.handle()
async def _play(bot: Bot, event: MessageEvent):
    if not plugin_config.fishmusic_enabled:
        await music_cmd.finish("点歌台关着门呢。")
    kw = re.sub(r"^\s*/?(点歌|music|来首歌)\s*", "", event.get_plaintext() or "").strip()
    if not kw:
        await music_cmd.finish("想听什么？/点歌 歌名")
    if not _rate_ok(event):
        await music_cmd.finish("别点啦，一分钟最多 %d 首。" % plugin_config.fishmusic_per_min)
    songs = await search(kw)
    if not songs:
        await music_cmd.finish("没搜到，换个说法试试？")
    s = songs[0]
    tmp = await download(s["id"])
    if not tmp:
        await music_cmd.finish("这首下不下来（可能是 VIP/版权），把链接给你自己听吧：\n"
                               "https://music.163.com/song?id=%s" % s["id"])
    blob = ""
    silk = _silk(tmp)
    send_path = silk or tmp
    with open(send_path, "rb") as f:
        blob = base64.b64encode(f.read()).decode()
    try:
        await music_cmd.send(MessageSegment("record", {"file": "base64://" + blob}))
        logger.info("[fishmusic] 发了 %s - %s（%d 字节）" % (s["artist"], s["name"], len(blob)))
    except Exception as e:
        logger.warning("[fishmusic] 发语音失败 %r" % (e,))
        await music_cmd.finish("歌下好了但发不出去，链接给你：\nhttps://music.163.com/song?id=%s" % s["id"])
    finally:
        for p in (tmp, silk):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
