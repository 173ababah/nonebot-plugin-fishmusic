"""配置：FISHMUSIC_ 前缀。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    fishmusic_enabled: bool = Field(default=True, description="启用点歌")
    fishmusic_max_mb: float = Field(default=8.0, description="单首歌上限（MB），超了只给链接")
    fishmusic_per_min: int = Field(default=2, description="每会话每分钟最多点几首，0=不限")
    fishmusic_silk_bin: str = Field(default="", description="可选的 to_silk.py 路径（转了才发原生语音）")
    fishmusic_silk_py: str = Field(default="python3", description="跑 to_silk.py 用的解释器")
    # 会员歌（网易云 fee=1/4）走别的曲子源：酷我 → 海棠 → GD Studio
    fishmusic_pirate: bool = Field(default=True, description="会员歌去别的源找同一首")
    fishmusic_pirate_level: str = Field(default="standard",
                                        description="海棠接口的音质档：standard / high / lossless")
