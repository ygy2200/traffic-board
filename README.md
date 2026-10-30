# 流量看板 Traffic Board

一个开源的 Windows 桌面工具：**实时看清每台电脑上"哪个软件在联网、连去了哪、走代理还是直连、用了多少流量"**。

> An open-source Windows dashboard that shows, in real time, which apps are connecting where — proxy or direct, and how much traffic they use.

## 功能

- **软件视角总览**：每个软件一张卡片，展示它的全部实时连接、渠道（代理/直连/本地）与流量
- **实时速率排行**：一键切换「累计流量 ↔ 当前速率」两种排序——后者直接回答"现在谁在偷跑流量"（速率来自代理字节差分，停流自动归零，不虚构数据）
- **代理节点明细**：检测到本机 Clash/mihomo 内核（FlClash、Clash Verge 等）的外部控制器时，自动显示每条连接走的节点与完整代理链；检测不到则自动降级为纯系统视角，**软件依然完整可用**
- **TUN 单源模式**：内核 TUN 开启时直接用 mihomo 全系统连接表（覆盖直连应用、UDP 明细更全、跳过系统表全扫描更快）；TUN 状态不明时自动回退传统双源
- **网络体检**：13 项一键检查（代理总状态 / 节点可用性 / TUN×系统代理互斥 / 进程归因 / UWP loopback / DNS 劫持 / 默认路由…），红黄绿分级 + 修复指引，进页自动跑
- **域名翻译**：通过 Windows 系统 DNS 缓存把目标 IP 翻译成可读域名（无需驱动、无需抓包）
- **连接明细表**：全部连接的表格视图，支持按软件/域名/IP 即时搜索；右键可复制目标、用 Wireshark 抓包、断开代理连接、导出 CSV（含公式注入防护）
- **中文软件名**：chrome.exe → Chrome 浏览器，常见软件自动翻译（内置映射表，可在 `core/appnames.py` 扩充）
- **历史流量**：今日 / 本周 / 本月总览 + 24 小时 / 7 天柱状图 + 软件流量回看（SQLite 本地留存 30 天）
- **月流量上限**：按计费周期统计经代理的流量，80% 托盘预警、超限再提醒（本地流量不计入，可设重置日）
- **托盘常驻 + 悬浮速率条**：关窗最小化到托盘；悬浮条可拖动、可置顶、可锁定位置
- **陌生连接提醒**：某软件第一次连接某个目标时弹托盘通知（同软件每小时限 1 条）
- **单实例**：程序在托盘里时再双击图标，会把已有窗口叫到前台而不是另开一份（双份会多一个托盘图标、两条采集线程并发写同一个历史库）

## 使用方法

1. 双击 `TrafficBoard.exe`（若弹出管理员确认请选"是"，可看到更全的系统级进程）
2. 完事。顶部标题栏会显示 Clash 连接状态、当前数据源与本次运行累计流量
3. 关闭窗口即最小化到托盘（可在设置里改为直接退出）
4. 再双击图标 = 把窗口叫回前台，不会多开一份

## 技术架构

```
界面层   PySide6 + PySide6-Fluent-Widgets（Fluent 风格，深色主题）
聚合层   进程 ↔ 连接 ↔ 域名 ↔ 渠道 关联；进程级单调累计 + 实时速率（EMA）
采集层   ① 系统连接表（psutil）——所有软件的 TCP/UDP 连接与进程归属
         ② Windows DNS 缓存（Get-DnsClientCache）——IP→域名（仅传统模式启用）
         ③ Clash API 自动发现（9090 等常见端口）——代理节点/域名/字节（可选）
         ④ TUN 判定（fake-ip DNS 行为探测 + API 自报）——决定数据源模式
```

数据源调度：TUN 单源（mihomo 连接表即全系统表）/ 传统双源（三源合并），
任一数据源缺席自动降级，**软件永远可用**。渠道判定：Clash chains 含真节点 → 代理，
否则直连；传统模式下系统表另按目标端口/网段判定。

## 开发与测试

```bash
pip install -r requirements.txt
python src/main.py                # 直接运行
python tests/adv_dns_cache.py     # 对抗性测试（畸形输入/并发/边界）
python tests/adv_clash_api.py
python tests/adv_aggregator.py    # 含 TUN 单源/模式切换/累计单调/速率 EMA
python tests/adv_tun_detect.py
python tests/adv_health.py        # 含 13 项体检矩阵 + 真机冒烟
python tests/adv_history.py
python tests/adv_exit.py
python tests/adv_nics.py          # 物理网卡甄别（TUN 双重计数防护）
python tests/adv_quota.py         # 计费周期/阈值/告警状态机
python tests/adv_secret.py        # 控制器密钥发现与 401 语义
python tests/adv_process_exit.py  # 真实子进程退出：3 模式 × 8 轮断言 rc=0
python tests/adv_single_instance.py  # 单实例：真子进程 + 命名管道 + 并发抢注
python tests/render_v10_smoke.py  # 离屏渲染冒烟（五页导航 + 退出路径）
python tests/render_v161.py       # 离屏渲染验收（排行双模式）
python tests/render_v162.py       # 离屏渲染验收（总览/配额设置）
python tests/render_v16_health.py # 离屏渲染验收（体检页三态）
python tests/render_v163_badge.py # 离屏渲染验收（悬浮条位置跨启动保留）
python tests/render_v164_feedback.py # 离屏渲染验收（右键动作的成功/失败回执）
python tests/render_v165_geometry.py # 离屏渲染验收（主窗口几何跨启动保留，含真重启读回）
python tests/render_v166_alerts.py  # 离屏渲染验收（提醒开关跨重启 + 前台可见 + 双层限流）
python tests/render_v167_detail_empty.py # 离屏渲染验收（明细表三种空态 + 右键不误伤）
python tests/render_v168_small_screen.py # 离屏渲染验收（1366x720 小屏：最小尺寸 + 省略号，喂数据测量）
python tests/render_v169_infobar_fit.py # 离屏渲染验收（前台提醒条宽度不超窗口，含半屏贴靠）
python tests/render_v170_settings_fit.py # 离屏渲染验收（设置页极限宽度无横向滚动条，配额行自动折行）
python tests/render_v171_contrast.py # 真实像素文字对比度闸门（五页逐屏滚动，量 89 处，非离屏）
python tests/render_v172_infobar_words.py # 真实像素折行闸门（提醒条不许把路径/URL 劈开，非离屏）
python tests/render_v173_badge_gate.py # 悬浮速率条闸门（半透明合成后的对比度 + 最长速率串不越界 + 悬浮说明，非离屏）
python tests/render_v174_focus_chain.py # 键盘焦点链闸门（初始焦点/Tab 链可达/表格不困住 Tab/页签能空格翻页）
```

`tests/_*.py`（`_qt_harness.py` / `_exit_driver.py` / `_instance_driver.py`）是被上面这些
套件调用的辅助件，不是独立套件：`_instance_driver.py` 少了命令行参数就必然 IndexError 退 1，
所以串行回归跑 `tests/adv_*.py tests/render_*.py`，别用 `tests/*.py`。
Qt 套件之间也不要并发跑——它们靠父进程按注册表快照还原 QSettings，
同时开两三个会互相把快照踩掉，表现为随机的 rc=127（实测踩过）。

设置页的「月流量上限」一行里有两个数字输入框，横向最小需求 550px，
比 456 宽窗口的视口（407）还大一截——以前必须横向拖滚动条才看得全「重置日」。
现在这一行自己会折：宽窗口保持一行，窄窗口把「重置日」挪到第二行并与上一行的
输入框左对齐。判断依据是滚动区视口宽度而不是这一行自己的宽度——
按自己的宽度判断会死锁：横向滚动条一出现，布局就把这行撑到它要求的最小宽度，
于是"放得下"永远成立，滚动条再也收不掉。

`render_v171_contrast.py`、`render_v172_infobar_words.py`、`render_v173_badge_gate.py`
是不跑在离屏平台上的渲染套件，因为它们量的都是"真实像素"：离屏平台没有中文字形（标签区一个墨点都没有，闸门会永远绿灯），
grab 出来的卡片底是 Qt 默认浅灰（qfluentwidgets 的主题背景根本没画进去），
而且 ASCII 字符宽度是假的——同一条 60 字符的 URL 离屏量到 840px、真字体只有 377px，
用假宽度选出来的用例根本碰不到"窗口里放得下却被断了"这条病。
两个套件都不 `show()` 窗口，只 `grab()` 位图——实测 windows 平台下不弹窗也能拿到真图。
对比度套件的前景取「离背景最远的 2% 像素的众数」而不是墨色众数：小字号中文全是抗锯齿边，墨色众数量到的是灰边，
实测把白字算成 (120,120,120)、对比度直接虚降一半。阈值取 WCAG AA（正文 4.5:1、
大字 3:1），量的是五页逐屏滚动下所有完整可见的标签（当前 89 处，帮助页加了键盘那 6 条）。

`render_v174_focus_chain.py` 是唯一**必须** `show()` 的渲染套件，因为 Qt 只给可见窗口设焦点：
在隐藏窗口上量焦点链会量出一条假链——隐藏时所有子件 `isVisible()` 都是 False，
Qt 的跳过逻辑只看可见性，于是链路钻进从没上过屏的按钮里（R13 那条"隐藏标题栏按钮吃掉 Tab"
的猜测就是这么来的，本轮在全新窗口里精确摘掉 `TitleBar`/`FluentWidgetTitleBar` 重测：
链 11 跳一字不差、最小尺寸不变、标题栏照常跟随宽度，**残留标题栏是无害的**，那条遗留结案）。
它默认跑 offscreen（show() 也不弹窗，焦点链由 QtWidgets 算、与平台无关），
`FOCUS_GATE_NATIVE=1` 时改跑 windows 平台核对真实初始焦点——两个平台都过，
但最小尺寸基线不同（离屏 456x510，本机 DPR=1.25 真平台 457x540），闸门按平台各取一个实测值。

同一轮把键盘这条路的四个真问题修掉了（都是逐跳按真 Tab 键量出来的，不是读代码猜的）：
① 窗口一打开初始焦点落在标题栏的**最小化按钮**上（qfluentwidgets 把
最小化→关闭→最大化排在 Tab 链最前），按第一下回车把窗口收进任务栏、第二下缩进托盘，
看着像程序没了——三个按钮改成 NoFocus，原生 Windows 的窗口按钮本就不参与 Tab；
出路没被夺走：实测本窗口 HWND 仍带 `WS_SYSMENU`、系统菜单里 `SC_CLOSE`/`SC_MINIMIZE` 都在，
Alt+F4 与 Alt+空格 照常（`tmp/probe_hwnd_style.py`）。
② 明细表是只读的，但 `QAbstractItemView` 默认把 Tab 当"下一个单元格"：有行时每按一次 Tab
只在第 0 行四列里绕圈（0,1→0,2→0,3→0,0…），既到不了别的行也走不出控件——键盘用户被关在里面，
`setTabKeyNavigation(False)` 之后 Tab 回到"下一个控件"的语义，行内仍用上下方向键。
③ 导航页签一个都不在链上（`NavigationWidget` 继承 QWidget、默认 NoFocus，库里没写 keyPressEvent），
键盘根本切不了页；现在 5 个页签都可 Tab、空格/回车都能翻页。
翻页不是调库里的 `click()` 完成的——它只发 `clicked` 信号，实测 index 纹丝不动，
真鼠标按下+松开才切页，所以键盘激活走的是一对合成鼠标事件（`tmp/probe_nav_click.py`）。
④ 页签聚焦原本**一个像素都不变**：库里的悬浮底色分支写死了
`globalRect.contains(QCursor.pos())`（`navigation_widget.py:200`），只认真鼠标位置，
手动把 `isEnter` 设 True 实测 0/168 采样点变化——键盘用户是盲操作。
于是加了 `_FocusRing`：盖在页签上的透明描边子控件，FocusIn 显示、FocusOut 隐藏、Resize 跟随，
闸门逐点比色确认真的画出来了（离屏 34/168、真平台 57/255，且变化点全部贴边）。

链序也被闸门钉住（两个平台量出来一样）：搜索框第 1 跳 → 明细表第 7 跳 → 5 个页签第 8–12 跳。
页签排在最后是库的装配顺序决定的（内容区先进窗口），从首页走到页签要按 8 次 Tab——
这条如实写进帮助页，改它要重写 `focusNextPrevChild`，风险大于收益，暂不动。

`tests/render_v16*.png`、`render_v17*.png` 这些留图**不是基线**：同一份代码连跑两次就差 3.7 万个像素
（`render_v168_small_screen.py` 实测，逐点最大差 226），是字形亚像素定位的不确定量，没有任何套件读它们。
以后重跑回归看到它们集体变脏，别当成视觉回归，也别为它们起一个提交。

顺带把"要不要开浅色主题"量清楚了：本机 Windows 处于深色模式，Qt 的基础调色板就是
`#1e1e1e`，而 `setTheme(Theme.LIGHT)` 只重刷 qfluentwidgets 自己注册的控件，
不动全局 palette——冷启动浅色实测 71 处文字里 48 处与背景同色（字还在，只是看不见了）。
所以浅色不是把 `setTheme(Theme.DARK)` 改成 `AUTO` 就能开的，要开得先做全局 palette
引导 + 把 `src/ui/*.py` 里三十来处写死的深色字面量收进一个调色板模块。

悬浮速率条是主窗口之外的另一个顶层窗口，而且是 `WA_TranslucentBackground` + `paintEvent` 自绘——
上面那套「grab 主窗口逐屏量标签」根本扫不到它。它的胶囊底色 alpha=215（84% 不透明），
桌面会透进来，所以字色对不对必须按**合成后的像素**算：`render_v173_badge_gate.py`
把悬浮条分别合成到深色/浅色/中灰桌面上量，实测白字对合成底
15.69 / 9.64 / 11.53:1，全部过 WCAG AA（本轮没有颜色要修，闸门是用来拦以后改一个字节的人）。
同一个套件还钉两条只在这个控件上成立的契约：最长速率串（`↓ 1023.9GB/s`，86px）
必须整个画在 210 宽的胶囊里——自绘没有省略号，越界就是数字被裁掉半截；
以及悬浮说明必须在（它没有标题栏也没有菜单入口，拖动、双击回主界面、右键锁定/退出
全靠用户猜，本轮补上一句 tooltip）。

打包后没有控制台，所以"点了没反应"必须变成看得见的话：明细表右键的
导出 CSV / 起 Wireshark / 断开代理连接三条动作都走 `_toast()`——
窗口在前台用 InfoBar（成功绿色、失败红色，含行数与路径），
收进托盘时退回托盘气泡。导出失败会区分"文件被占用/无权限"和其他原因，
且失败时绝不把你原来那份 CSV 清空。
主动推送的两条（月流量配额、陌生连接）走同一个 `_toast()`，
所以盯着窗口看的时候提醒会出现在窗口顶部，而不是藏在托盘里；
陌生连接限流两层——同一进程每小时 1 条、全局每分钟 1 条，
首次启动几十条连接一起"陌生"时也只弹一条，连接照常记账。
设置页的"新连接提醒"开关现在真的跨重启生效（以前构造函数把它写死成开）。
提醒条本身的宽度也跟着窗口走：qfluentwidgets 估算折行按「9 像素一个字符」，
中文一个字实际 13~14 像素，所以一条普通提醒在 683 宽（Win11 半屏贴靠）的窗口里
会撑到 769 宽、左右两头各被切掉几十像素——看着就像提醒少写了半句话。
现在按真实字体像素宽度折行并收敛到窗口内（456 宽的极限窗口里提醒 433 宽），
窗口中途改变大小时活着的提醒会重新贴合，截断只发生在折行而不是丢字。

折行这件事本身还有一层坑：中文没有空格，逐字试宽度是对的；但提醒正文里最多的恰恰是
路径、URL、文件名，逐字试会把它们从单词中间劈开——实测 420px 可用宽下
`C:\Users\y\Downloads\traffic_detail_2026-10-30.csv`（317px，本来就放得下）被折成
`...\Download` + `s\traffic_detail_...`，用户照着抄就少一个 s；
`.../tag/v1.6.3` 被折成 `.../tag/v1.6.` + `3`，版本号断成两截。
现在按"断行单元"折：中日韩一个字是一个单元，连续 ASCII 是一整块，
放不下才在 `/ \ - _ . : ; , ? & % =` 这些分隔符后面断，数字之间的点不算分隔符
（`v1.6.3` 不许断），实在断不开的长串（60 位哈希）仍然硬拆——宽度契约优先于词完整性。

连接明细表"没东西"时不再是一格白表，三种情况各说各话：
没点过任何软件 → 提示点左侧；选中的软件已不在本次采集里 → 说明进程结束并让你点别的；
搜索框过滤到 0 条 → 把关键词和该进程连接总数说出来，并说明清空搜索框可以看全部。
空态同时会清掉右键菜单的目标行，所以在提示行上右键不会误伤一条已经不显示的活连接。

小屏笔记本（1366x720 可用区）曾经很难受：窗口最小尺寸被顶到 885x806，
比屏幕可用高度还高，底部一截永远在屏幕外，而且缩到一半就会"卡住"再也缩不动。
现在设置页整体放进滚动区、长说明文字一律允许换行、排行行的软件名与流量明细
放不下就走省略号（完整内容在鼠标悬浮提示里）、连接明细标题与顶栏状态文字可换行，
窗口最小尺寸降到 456x510——喂上真实数据后仍然是 456x510，
缩到极限就停在极限上，不会再被布局反撑回去。

跑测试不用先关程序：`tests/_qt_harness.py` 把两个用户态入口都隔离掉了——
数据目录走 `TB_DATA_DIR`（临时目录，测试不会往你的 30 天历史里写假数据），
QSettings 由父进程按注册表快照精确还原（进程内"还原"实测会被控件自己的
QSettings 实例缓存覆盖，曾经抹掉过用户设置）。新增 Qt 冒烟请用
`run_isolated()` + `use_temp_data_dir()` + `shoot()`，不要再写嵌套 `app.exec()`。

## 构建与发布

```bash
python -m PyInstaller --noconfirm --distpath dist --workpath build TrafficBoard.spec
cp -r dist/TrafficBoard out/<版本号>/          # 桌面快捷方式指向 out/<版本号>/TrafficBoard/TrafficBoard.exe
python - <<'PY'                                # 打包成发布用 zip（根目录即 TrafficBoard/）
import os, zipfile, pathlib
src = pathlib.Path('out/<版本号>/TrafficBoard'); zp = pathlib.Path(f'TrafficBoard-v<版本号>-win64.zip')
with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in sorted(src.rglob('*')):
        if p.is_file(): z.write(p, os.path.relpath(p, src.parent))
PY
```

版本号只在 `src/core/version.py` 定义（关于页、out/ 目录名、zip 名、Release 标签都取它）。
发布前先跑全套 `adv_*.py` + `render_*.py`（全部要求 rc=0），再打 tag、建 Release、传 zip，
最后按 sha256 核对附件——GitHub 附件的 `digest` 字段就是它。

## 已知边界（如实说明）

- 进程级流量字节统计来自 Clash 内核数据；纯系统视角下 Windows 不提供按进程字节计数，显示"—"
- UDP 连接的远端地址 Windows 系统表不提供（系统限制），UDP 明细依赖 Clash 数据源
- TUN 模式下进程归因依赖内核 `find-process-mode`（默认 strict 可用；被关掉时体检页会明确告知）
- 未提权时部分系统进程的归属信息拿不全

## License

MIT
