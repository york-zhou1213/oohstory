from __future__ import annotations


UNIT_ALLOWLIST: dict[str, str] = {
    "oohstory-reader.service": "OOHStory 阅读服务",
    "oohstory-deconstruction-sync.service": "公开拆书同步任务",
    "oohstory-deconstruction-sync.timer": "公开拆书同步定时器",
    "oohstory-library-authorized-catalog-sync.service": "授权书源目录同步",
    "oohstory-library-authorized-catalog-sync.timer": "授权书源目录定时器",
    "oohstory-library-authorized-download.service": "授权正文同步",
    "oohstory-library-authorized-download.timer": "授权正文同步定时器",
    "oohstory-library-cover-sync.service": "AI 封面生成/同步",
    "oohstory-library-xbiquge-cover-sync.service": "新笔趣阁封面同步",
    "oohstory-library-ixdzs-cover-sync.service": "爱下封面同步",
    "oohstory-library-shubaow-cover-sync.service": "书宝封面同步",
    "oohstory-library-linovelib-cover-sync.service": "轻小说封面同步",
    "oohstory-library-local-source-upgrade.service": "本地书源/封面升级",
    "oohstory-library-index-refresh.service": "OOHStory 书籍轻量索引",
    "oohstory-library-ingestion-index.service": "OOHStory 新书可见索引",
    "oohstory-library-derived-index.service": "OOHStory 派生索引管道",
    "oohstory-library-derived-index-probe.service": "OOHStory 派生索引探针",
    "oohstory-shubaow-browser.service": "书宝爬虫浏览器",
}

ALLOWED_ACTIONS = frozenset({"start", "restart", "stop", "enable", "disable"})
