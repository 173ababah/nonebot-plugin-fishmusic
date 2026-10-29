"""点歌自测：离线测歌名比对，联网测「会员歌走盗版源」。

跑法：python tests/test_music.py
会员歌样例：晴天 - 周杰伦（网易云 fee=1，官方直链只返回版权提示 HTML）
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nonebot  # noqa: E402

nonebot.init()
import httpx  # noqa: E402
from nonebot_plugin_fishmusic import (  # noqa: E402
    audio_duration, get_song_audio, kuwo_search, kuwo_url, norm_text, pick_best,
    pirate_urls, score, search,
)

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [OK]   %s" % name if cond else "  [FAIL] %s %s" % (name, extra))


def _dur(path):
    return audio_duration(path)


def has_ffprobe():
    import shutil
    return shutil.which("ffprobe") is not None


async def _probe(url):
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url, headers={"Range": "bytes=0-1023"})
        ct = r.headers.get("content-type", "")
        return r.status_code in (200, 206) and ("audio" in ct or "octet" in ct)
    except Exception:
        return False


async def main():
    print("① 歌名归一化 / 打分（离线）")
    ck("去版本括号", norm_text("晴天（Live版）") == "晴天", norm_text("晴天（Live版）"))
    ck("去空格标点", norm_text(" Hello, World! ") == "helloworld", norm_text(" Hello, World! "))
    ck("HTML 实体", norm_text("A&amp;B") == "ab", norm_text("A&amp;B"))
    ck("同名歌手也对 → 高分",
       score("晴天", "周杰伦", "晴天", "周杰伦") >= 100, score("晴天", "周杰伦", "晴天", "周杰伦"))
    ck("同名换个歌手 → 中分",
       45 <= score("晴天", "周杰伦", "晴天", "Lucky小爱") < 100,
       score("晴天", "周杰伦", "晴天", "Lucky小爱"))
    ck("完全不相干 → 排除", score("晴天", "周杰伦", "七里香", "周杰伦") == -1,
       score("晴天", "周杰伦", "七里香", "周杰伦"))
    song = {"name": "晴天", "artist": "周杰伦"}
    cands = [{"name": "晴天(深情版)", "artist": "Lucky小爱"},
             {"name": "晴天", "artist": "周杰伦"},
             {"name": "稻香", "artist": "周杰伦"}]
    best = pick_best(song, cands, "name", "artist")
    ck("挑中正主", best and best["artist"] == "周杰伦", best)

    print("② 网易云搜索（要联网）")
    songs = await search("晴天 周杰伦")
    ck("搜到结果", bool(songs), str(songs)[:100])
    if songs:
        ck("有 id / 歌名 / fee", bool(songs[0]["id"]) and bool(songs[0]["name"])
           and "fee" in songs[0], str(songs[0]))
        ck("最多 5 条", len(songs) <= 5)

    print("③ 酷我搜索 + 取播放地址（要联网）")
    kw = await kuwo_search("晴天 周杰伦")
    ck("酷我有结果", bool(kw), str(kw[:2])[:120])
    picked = pick_best({"name": "晴天", "artist": "周杰伦"}, kw, "name", "artist") if kw else None
    ck("酷我挑中正主", picked is not None and "晴天" in picked["name"], picked)
    if picked:
        u = await kuwo_url(picked["rid"])
        ck("酷我给 mp3 直链", u.startswith("http"), u[:70])
        if u:
            ck("直链真能下到音频（只读前 1KB）", await _probe(u), "拿不到音频")

    print("④ 会员歌整条盗版链（要联网）")
    if songs:
        vip = dict(songs[0], fee=1)      # 强制当会员歌走盗版链
        urls = await pirate_urls(vip)
        ck("至少一个盗版源给了地址", bool(urls), str(urls)[:120])
        if urls:
            ck("第一条能下到音频", await _probe(urls[0][0]), urls[0][0][:70])

    print("⑤ 真·会员歌：十年 - 陈奕迅（网易云 fee=1）")
    ten = await search("十年 陈奕迅")
    if ten:
        s = dict(ten[0], fee=1)
        ck("搜到且是会员歌", s["name"].startswith("十年"), str(s))
        tmp, src, why = await get_song_audio(s)
        ck("盗版链拿到整首", bool(tmp), "why=%s" % why)
        if tmp:
            ck("来源是酷我/海棠/GD之一", src in ("酷我", "海棠", "GD"), src)
            d = _dur(tmp)
            if d < 0:
                print("  [SKIP] 本机没有 ffprobe，跳过时长检查（服务器上有）")
            else:
                ck("时长像正常歌（>100 秒）", d > 100, d)
            ck("大小没超过上限", os.path.getsize(tmp) < 8 * 1048576)
            os.remove(tmp)
    else:
        print("  [SKIP] 搜不到，跳过")

    print("⑥ 独家版权只给试听片段 → 要认得出来（周杰伦《晴天》id=186016）")
    if not has_ffprobe():
        print("  [SKIP] 本机没有 ffprobe，量不了时长，跳过（服务器上会真跑）")
    else:
        tmp, src, why = await get_song_audio(
            {"id": 186016, "name": "晴天", "artist": "周杰伦", "fee": 1, "dur": 269.0})
        ck("不当成整首发出去", tmp is None, tmp)
        ck("原因记成 preview", why == "preview", why)
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
