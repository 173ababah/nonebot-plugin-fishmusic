"""nonebot-plugin-fishmusic —— 网易云点歌（直接发语音条），零 LLM 开销。

会员歌曲的处理（2026-10-29 加）：
    网易云的「会员/付费」歌（fee=1 或 4）走 `music.163.com/song/media/outer/url` 只会返回一张
    版权提示 HTML，下不到音频。这时候改去**别的曲子源**拿同一首（就是「聆听岛」那类 App 干的事）：
        ① 酷我  search.kuwo.cn → antiserver.kuwo.cn/anti.s   （实测最稳，直接给 mp3）
        ② 海棠  musicserver.haitangw.cc/v1/music/resolve-url（APK 里扒出来的，POST JSON）
        ③ GD Studio music-api.gdstudio.xyz/api.php?types=url   （Meting 那套）
    先按「歌名 + 歌手」找最像的一条，再取播放地址；都不行就老实回「这首拿不到」。
"""
from __future__ import annotations

import base64
import html
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
    description="网易云点歌，直接把歌当语音条发出来（会员歌走别的源，零 LLM 开销）",
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
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}
_NETEASE_HEADERS = dict(_UA, Referer="https://music.163.com/")
_rate: dict[str, list[float]] = {}
_reject = {"why": ""}      # 上一次没拿到音频的原因："" / preview（只有试听）/ size（太大）


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


# ------------------------------------------------------------------ 歌名比对
def norm_text(s: str) -> str:
    """归一化：去括号里的版本说明、空格、标点，转小写。"""
    s = html.unescape(s or "")
    s = re.sub(r"[（(【\[].*?[）)】\]]", "", s)
    s = re.sub(r"&nbsp;?", " ", s)
    s = s.replace("\u3000", " ")
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", s)
    return s.lower()


def score(name: str, artist: str, cname: str, cartist: str) -> int:
    """给候选打分：歌名对不对最重要，歌手是加分项。"""
    n1, n2 = norm_text(name), norm_text(cname)
    a1, a2 = norm_text(artist), norm_text(cartist)
    if not n1 or not n2:
        return -1
    if n1 == n2:
        sc = 60
    elif n1 in n2 or n2 in n1:
        sc = 35
    else:
        same = len(set(n1) & set(n2))
        if same < max(2, len(set(n1)) // 2):
            return -1
        sc = int(20 * same / max(len(set(n1)), 1))
    if a1 and a2:
        if a1 == a2:
            sc += 40
        elif a1 in a2 or a2 in a1:
            sc += 30
        else:
            common = len(set(a1) & set(a2))
            sc += min(15, common * 5)
    return sc


def pick_best(song: dict, cands: list[dict], key_name: str, key_artist: str) -> dict | None:
    """从候选里挑最像的一首（分数太低就认没有）。"""
    best, best_sc = None, -1
    for c in cands or []:
        sc = score(song.get("name", ""), song.get("artist", ""),
                   c.get(key_name, ""), c.get(key_artist, ""))
        if sc > best_sc:
            best, best_sc = c, sc
    return best if best_sc >= 45 else None


# ------------------------------------------------------------------ 网易云
async def search(keyword: str) -> list[dict]:
    """网易云搜索（公开接口）。返回 [{id,name,artist,fee}]。fee: 0免费 1会员 4付费 8低音质试听"""
    async with httpx.AsyncClient(timeout=12, headers=_NETEASE_HEADERS) as c:
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
        out.append({"id": s.get("id"), "name": s.get("name") or "",
                    "artist": artists, "fee": int(s.get("fee") or 0),
                    "dur": round((s.get("duration") or 0) / 1000.0, 1)})
    return out


def is_vip(song: dict) -> bool:
    return int(song.get("fee") or 0) in (1, 4)


async def netease_url(song_id) -> str:
    return "https://music.163.com/song/media/outer/url?id=%s.mp3" % song_id


# ------------------------------------------------------------------ 盗版源：酷我
async def kuwo_search(keyword: str, rn: int = 8) -> list[dict]:
    url = "https://search.kuwo.cn/r.s"
    params = {"client": "kt", "all": keyword, "pn": 0, "rn": rn, "uid": 0,
              "ver": "kwplayer_ar_9.2.2.1", "vipver": 1, "show_copyright_off": 1,
              "newver": 1, "ft": "music", "cluster": 0, "strategy": 2012,
              "encoding": "utf8", "rformat": "json", "vermerge": 1, "mobi": 1}
    try:
        async with httpx.AsyncClient(timeout=12, headers=_UA) as c:
            r = await c.get(url, params=params)
        d = r.json()
    except Exception:
        return []
    out = []
    for it in (d.get("abslist") or []):
        rid = str(it.get("DC_TARGETID") or it.get("MUSICRID") or "").replace("MUSIC_", "")
        if not rid:
            continue
        out.append({"rid": rid,
                    "name": html.unescape(str(it.get("SONGNAME") or it.get("NAME") or "")),
                    "artist": html.unescape(str(it.get("ARTIST") or it.get("FARTIST") or ""))})
    return out


async def kuwo_url(rid: str) -> str:
    url = "https://antiserver.kuwo.cn/anti.s"
    params = {"type": "convert_url", "rid": "MUSIC_" + str(rid), "format": "mp3",
              "response": "url"}
    try:
        async with httpx.AsyncClient(timeout=12, headers=_UA) as c:
            r = await c.get(url, params=params)
        t = (r.text or "").strip()
        return t if t.startswith("http") else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ 盗版源：海棠 / GD Studio
async def haitangw_url(song: dict) -> str:
    """「聆听岛」APK 里扒出来的接口：POST {"source":"wy","rid":"<网易云id>","level":"standard"}。"""
    body = {"source": "wy", "rid": str(song.get("id") or ""),
            "level": plugin_config.fishmusic_pirate_level or "standard"}
    for host in ("https://musicserver.haitangw.cc", "https://qt.haitangw.net"):
        try:
            async with httpx.AsyncClient(timeout=12, headers=dict(_UA, **{"Content-Type": "application/json"})) as c:
                r = await c.post(host + "/v1/music/resolve-url", json=body)
            d = r.json()
        except Exception:
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if isinstance(data, str) and data.startswith("http"):
            return data
        if isinstance(data, dict):
            for k in ("url", "playUrl", "play_url"):
                v = data.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v
        for k in ("url", "playUrl"):
            v = d.get(k) if isinstance(d, dict) else None
            if isinstance(v, str) and v.startswith("http"):
                return v
    return ""


async def gdstudio_url(song: dict) -> str:
    """GD Studio 的 Meting 风格接口（有时 503，留着当备胎）。"""
    params = {"types": "url", "source": "netease", "id": str(song.get("id") or "")}
    try:
        async with httpx.AsyncClient(timeout=12, headers=_UA, follow_redirects=True) as c:
            r = await c.get("https://music-api.gdstudio.xyz/api.php", params=params)
        d = r.json()
    except Exception:
        return ""
    u = d.get("url") if isinstance(d, dict) else None
    return u if isinstance(u, str) and u.startswith("http") else ""


async def pirate_urls(song: dict) -> list[tuple[str, str]]:
    """按「先稳后奇」排：酷我 → 海棠 → GD Studio。返回 [(url, 源名)]。"""
    out: list[tuple[str, str]] = []
    kw = "%s %s" % (song.get("name") or "", song.get("artist") or "")
    cands = await kuwo_search(kw)
    best = pick_best(song, cands, "name", "artist")
    if best:
        u = await kuwo_url(best["rid"])
        if u:
            out.append((u, "酷我"))
    u = await haitangw_url(song)
    if u:
        out.append((u, "海棠"))
    u = await gdstudio_url(song)
    if u:
        out.append((u, "GD"))
    return out


# ------------------------------------------------------------------ 下载 / 转码
def audio_duration(path: str) -> float:
    """用 ffprobe 量一下音频多长（秒）；量不到返回 -1。"""
    import subprocess
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path], timeout=20).decode().strip()
        return float(out)
    except Exception:
        return -1.0


async def fetch_audio(url: str, referer: str = "", want_dur: float = 0.0) -> str | None:
    """下音频到临时文件；不是音频 / 超过上限 / **只有试听片段** 都返回 None。

    试听片段是真实存在的坑：周杰伦这种独家版权的歌，别的源也只给 10~30 秒。
    拿 want_dur（网易云那边的时长）一比，短太多就当没拿到。
    """
    limit = int(plugin_config.fishmusic_max_mb * 1024 * 1024)
    headers = dict(_UA)
    if referer:
        headers["Referer"] = referer
    _reject["why"] = ""
    tmp = ""
    try:
        async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as c:
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
                            _reject["why"] = "size"
                            os.remove(tmp)
                            return None
                        f.write(chunk)
        if os.path.getsize(tmp) <= 20000:
            os.remove(tmp)
            return None
        if want_dur and want_dur > 30:
            real = audio_duration(tmp)
            if 0 < real < want_dur * 0.55:
                logger.info("[fishmusic] 只拿到 %.0f 秒试听（原曲 %.0f 秒），丢掉" % (real, want_dur))
                _reject["why"] = "preview"
                os.remove(tmp)
                return None
        return tmp
    except Exception as e:
        logger.debug("[fishmusic] 下载失败 %r" % (e,))
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None


def to_silk(path: str) -> str:
    """可选：转成 QQ 原生 SILK（配了 FISHMUSIC_SILK_BIN 才走）。"""
    bin_path = plugin_config.fishmusic_silk_bin
    if not bin_path or not os.path.exists(bin_path):
        return ""
    import subprocess

    out = path.rsplit(".", 1)[0] + ".silk"
    try:
        subprocess.run([plugin_config.fishmusic_silk_py or "python3", bin_path, path, out, "24000"],
                       check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out if os.path.exists(out) and os.path.getsize(out) > 2000 else ""
    except Exception as e:
        logger.debug("[fishmusic] silk 转换失败 %r" % (e,))
        return ""


async def get_song_audio(song: dict) -> tuple[str | None, str, str]:
    """拿音频：会员歌优先走盗版源，其它的先试网易云。返回 (临时文件, 源名, 失败原因)。"""
    tries: list[str]
    if plugin_config.fishmusic_pirate and is_vip(song):
        tries = ["pirate", "official"]
    else:
        tries = ["official"] + (["pirate"] if plugin_config.fishmusic_pirate else [])
    want = float(song.get("dur") or 0)
    why = ""      # 记住失败原因（"preview" 比 "" 有用，别被后一次 attempt 覆盖掉）

    for how in tries:
        if how == "official":
            url = await netease_url(song.get("id"))
            tmp = await fetch_audio(url, "https://music.163.com/", want)
            if tmp:
                return tmp, "网易云", ""
            why = _reject["why"] or why
        else:
            for url, src in await pirate_urls(song):
                tmp = await fetch_audio(url, "", want)
                if tmp:
                    logger.info("[fishmusic] 会员歌 %s - %s 从「%s」拿到" % (song.get("artist"), song.get("name"), src))
                    return tmp, src, ""
                why = _reject["why"] or why
    return None, "", why


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
    tmp, src, why = await get_song_audio(s)
    if not tmp:
        if why == "preview":
            await music_cmd.finish("这首几个源都只给试听片段（独家版权，比如周杰伦的）：\n"
                                   "https://music.163.com/song?id=%s" % s["id"])
        if why == "size":
            await music_cmd.finish("这首太大（超过 %s MB），链接给你自己听：\n"
                                   "https://music.163.com/song?id=%s"
                                   % (plugin_config.fishmusic_max_mb, s["id"]))
        await music_cmd.finish("这首下不下来（会员/版权，几个源都没给）：\n"
                               "https://music.163.com/song?id=%s" % s["id"])
    silk = to_silk(tmp)
    send_path = silk or tmp
    with open(send_path, "rb") as f:
        blob = base64.b64encode(f.read()).decode()
    try:
        await music_cmd.send(MessageSegment("record", {"file": "base64://" + blob}))
        logger.info("[fishmusic] 发了 %s - %s（%s，%d 字节）"
                    % (s["artist"], s["name"], src, len(blob)))
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
