# -*- coding: utf-8 -*-
"""常见域名 -> 中文名。给界面里的英文域名加中文注释。"""


def _pairs() -> dict:
    return {
        "google.com": "谷歌", "googleapis.com": "谷歌服务", "googleusercontent.com": "谷歌内容",
        "gstatic.com": "谷歌静态资源", "youtube.com": "油管", "ytimg.com": "油管图片",
        "github.com": "GitHub 代码托管", "githubusercontent.com": "GitHub 内容",
        "githubcopilot.com": "GitHub Copilot",
        "baidu.com": "百度", "bilibili.com": "B站", "biliapi.net": "B站",
        "hdslb.com": "B站图片", "qq.com": "腾讯", "weixin.qq.com": "微信",
        "wechat.com": "微信", "qpic.cn": "QQ 图片",
        "taobao.com": "淘宝", "tmall.com": "天猫", "jd.com": "京东", "pinduoduo.com": "拼多多",
        "zhihu.com": "知乎", "douyin.com": "抖音", "tiktok.com": "抖音国际版",
        "douyinpic.com": "抖音图片", "bytedance.com": "字节跳动", "zijieapi.com": "字节跳动",
        "microsoft.com": "微软", "windows.com": "Windows 系统", "windowsupdate.com": "Windows 更新",
        "office.com": "Office 办公", "officeclient.microsoft.com": "Office 许可检查",
        "live.com": "微软账户", "msn.com": "MSN",
        "apple.com": "苹果", "icloud.com": "苹果云",
        "steampowered.com": "Steam", "steamcommunity.com": "Steam 社区", "steamcontent.com": "Steam 下载",
        "steamchina.com": "蒸汽平台", "epicgames.com": "Epic 游戏",
        "openai.com": "OpenAI", "chatgpt.com": "ChatGPT", "anthropic.com": "Anthropic",
        "claude.ai": "Claude", "x.com": "X(推特)", "twitter.com": "推特", "twimg.com": "推特图片",
        "facebook.com": "脸书", "instagram.com": "照片墙", "whatsapp.com": "WhatsApp",
        "telegram.org": "电报", "discord.com": "Discord 语音",
        "netflix.com": "网飞", "spotify.com": "声破天音乐",
        "nvidia.com": "英伟达", "amd.com": "AMD", "intel.com": "英特尔",
        "dmm.com": "DMM 游戏", "dmm.co.jp": "DMM 游戏", "pixiv.net": "画师站 pixiv",
        "cloudflare.com": "Cloudflare 网络", "workers.dev": "Cloudflare Workers",
        "amazonaws.com": "亚马逊云", "cloudfront.net": "CDN 网络", "akamai.com": "CDN 网络",
        "fastly.net": "CDN 网络", "jsdelivr.net": "CDN 网络",
        "aliyuncs.com": "阿里云", "aliyun.com": "阿里云", "alidns.com": "阿里 DNS",
        "tencentcloud.com": "腾讯云", "myqcloud.com": "腾讯云",
        "digicert.com": "数字证书校验", "lencr.org": "数字证书校验", "pki.goog": "数字证书校验",
        "verisign.com": "数字证书校验", "sectigo.com": "数字证书校验",
        "ntp.org": "网络对时", "aliyun-ntp": "网络对时", "time.edu.cn": "网络对时",
        "leigod.com": "雷神加速器", "uu.163.com": "网易UU加速器",
        "in-addr.arpa": "",
    }


_DOMAIN_CN = _pairs()


def annotate(domain: str) -> str:
    """'google.com' -> '谷歌 (google.com)'；无映射返回原样。"""
    if not domain:
        return domain
    low = domain.lower().rstrip(".")
    for key, name in _DOMAIN_CN.items():
        if low == key or low.endswith("." + key):
            return f"{name} ({domain})" if name else domain
    return domain
