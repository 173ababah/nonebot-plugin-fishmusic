"""网易云点歌自测（要联网，只搜索和探 URL，不下载整首）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nonebot  # noqa: E402

nonebot.init()
import httpx  # noqa: E402
from nonebot_plugin_fishmusic import search  # noqa: E402

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [OK]   %s" % name if cond else "  [FAIL] %s %s" % (name, extra))


async def main():
    songs = await search("稻香 周杰伦")
    ck("搜到结果", bool(songs), str(songs)[:120])
    if songs:
        ck("有 id 和歌名", bool(songs[0]["id"]) and bool(songs[0]["name"]), str(songs[0]))
        ck("最多 5 条", len(songs) <= 5)
        # 探一下 outer url 能不能拿到音频（只读响应头）
        url = "https://music.163.com/song/media/outer/url?id=%s.mp3" % songs[0]["id"]
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0",
                                              "Referer": "https://music.163.com/"}) as c:
            r = await c.get(url, headers={"Range": "bytes=0-1023"})
        ck("直链能出音频（HTTP %s）" % r.status_code,
           r.status_code in (200, 206) and ("audio" in r.headers.get("content-type", "")
                                            or "octet" in r.headers.get("content-type", "")),
           r.headers.get("content-type", ""))
    print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))