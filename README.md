# 流量看板 Traffic Board

一个开源的 Windows 桌面工具：**实时看清每台电脑上"哪个软件在联网、连去了哪、走代理还是直连、用了多少流量"**。

> An open-source Windows dashboard that shows, in real time, which apps are connecting where — proxy or direct, and how much traffic they use.

## 功能

- **软件视角总览**：每个软件一张卡片，展示它的全部实时连接、渠道（代理/直连/本地）与流量
- **代理节点明细**：检测到本机 Clash/mihomo 内核（FlClash、Clash Verge 等）的外部控制器时，自动显示每条连接走的节点与完整代理链；检测不到则自动降级为纯系统视角，**软件依然完整可用**
- **域名翻译**：通过 Windows 系统 DNS 缓存把目标 IP 翻译成可读域名（无需驱动、无需抓包）
- **连接明细表**：全部连接的表格视图，支持按软件/域名/IP 即时搜索
- **中文软件名**：chrome.exe → Chrome 浏览器，常见软件自动翻译（内置映射表，可在 `core/appnames.py` 扩充）

## 使用方法

1. 双击 `TrafficBoard.exe`（若弹出管理员确认请选"是"，可看到更全的系统级进程）
2. 完事。顶部标题栏会显示 Clash 连接状态与数据源情况
3. 关闭窗口即退出，无后台驻留

## 技术架构

```
界面层   PySide6 + PySide6-Fluent-Widgets（Fluent 风格）
聚合层   进程 ↔ 连接 ↔ 域名 ↔ 渠道 关联
采集层   ① 系统连接表（psutil）——所有软件的 TCP/UDP 连接与进程归属
         ② Windows DNS 缓存（Get-DnsClientCache）——IP→域名
         ③ Clash API 自动发现（9090 等常见端口）——代理节点/域名/字节（可选）
```

三个数据源彼此独立，任一缺席自动降级。渠道判定：连接目标是本机代理端口 → 经代理；其余按内网/直连分类；Clash 在线时以其实际节点数据为准。

## 开发与测试

```bash
pip install -r requirements.txt
python src/main.py                # 直接运行
python tests/adv_dns_cache.py     # 对抗性测试（畸形输入/并发）
python tests/adv_clash_api.py
python tests/adv_aggregator.py
```

## 已知边界（如实说明）

- 进程级流量字节统计来自 Clash 内核数据；纯系统视角下 Windows 不提供按进程字节计数，显示"—"
- UDP 连接的远端地址 Windows 系统表不提供（系统限制），UDP 明细依赖 Clash 数据源
- 未提权时部分系统进程的归属信息拿不全

## License

MIT
