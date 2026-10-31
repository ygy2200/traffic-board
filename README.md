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
python scripts/test_all.py        # 串行跑全部套件，退出码说真话（别用 shell 管道看 rc）
python scripts/test_all.py --match release   # 只跑文件名含 release 的套件
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
python tests/adv_release_tools.py # 发布链闸门：占位符先于 token / digest 前缀 / zip 只数文件 / exe 内版本 / 桌面入口对齐当前版本
python tests/adv_test_isolation.py # 静态闸门：所有 render 套件必须 start_worker=False，不许拉起真实采集线程
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
python tests/render_v171_contrast.py # 真实像素文字对比度闸门（五页逐屏滚动，量 106 处，非离屏）
python tests/render_v172_infobar_words.py # 真实像素折行闸门（提醒条不许把路径/URL 劈开，非离屏）
python tests/render_v173_badge_gate.py # 悬浮速率条闸门（半透明合成后的对比度 + 最长速率串不越界 + 悬浮说明，非离屏）
python tests/render_v174_focus_chain.py # 键盘闸门（初始焦点/Tab 链可达/表格不困住 Tab/页签空格翻页/Ctrl+1..5 与 Ctrl+Tab 切页）
python tests/render_v175_isolation.py # 测试隔离运行时证据：start_worker=False 时 PollWorker 未运行且 stop/wait/set_quota 安全
```

`tests/_*.py`（`_qt_harness.py` / `_exit_driver.py` / `_instance_driver.py`）是被上面这些
套件调用的辅助件，不是独立套件：`_instance_driver.py` 少了命令行参数就必然 IndexError 退 1，
所以串行回归跑 `python scripts/test_all.py`（或 `tests/adv_*.py tests/render_*.py`），
别用 `tests/*.py`。不要用 `for f in ...; do python "$f" | tail; done` 看结果：
管道最后一段是 tail，rc 永远是 0，测试失败也会显示全绿（2026-10-31 实测踩过）。
`scripts/log_round.py` 负责把每轮写进 `docs/iteration-ledger.jsonl`，轮次只由台账决定。
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
大字 3:1），量的是五页逐屏滚动下所有完整可见的标签（当前 106 处，帮助页加了键盘那 6 条）。

R21 把这道闸门自己修了两个漏，因为它当时是全场唯一会**偶发**红的套件（全量回归 rc=1、单独跑 rc=0）：
① 数据漏：`agg.start = lambda: None` 只挡住 Aggregator 自己的线程，`MainWindow.__init__` 另起了
一个 `PollWorker`，它每秒 `agg.build()` 拿的是**这台机器真实的连接**——现场量到排行榜 16 行、
行里写着「FlClash 内核」「22 条」，`_board()` 喂进去那 6 个假软件早被盖掉了。闸门量渲染像素，
数据是谁的不要紧，但行数和文字长度一变，被裁的标签就跟着变，红绿就成了看当时网络状态的抽奖。
现在建完窗口先 `worker.stop() + wait()`，三次连跑数字与留图字节数完全一致。
② 采样漏：被滚出视口的子件 `isVisibleTo()` 依然为真、`mapTo(page)` 也仍落在页面矩形里，
但它一个像素都没画，于是量到的是"视口上方那块屏幕"（别人的底色）。旧代码只有一道越界检查兜着，
恰好能拦住滚到**下面**的；滚到上面的会误报成"与背景同色的隐形文字"——那条红就是这么来的。
现在 `_measure` 先按所属滚动区视口过滤，并加了第 2b 步：把第一行正好推到"裁出视口 154px、
整块却仍在页面内"的空档，断言它被跳过（`tmp/probe_rank_ghost.py` 量到这个位置的墨点是 0）。
把过滤器删掉重跑必须红（实测 `FAIL: 被滚出视口的标签还是进了账`），所以这一步是承重的、不是装饰。
其余 render_* 套件同样被 PollWorker 喂真数据，本轮只修了出问题这一道闸门——它们没有断言行数，
暂时没看到这个漏的后果。

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
页签排在最后是库的装配顺序决定的（内容区先进窗口）——R21 就是冲这条来的，见下。

`tests/render_v16*.png`、`render_v17*.png` 这些留图**不是基线**：同一份代码连跑两次就差 3.7 万个像素
（`render_v168_small_screen.py` 实测，逐点最大差 226），是字形亚像素定位的不确定量，没有任何套件读它们。
以后重跑回归看到它们集体变脏，别当成视觉回归，也别为它们起一个提交。

R21（同一道闸门加到 11 步）先把 R20 留的那条"页签要按 8 次 Tab"量成实话：逐页量下来，
从页内焦点走到页签的跳数是 体检 1 / 历史 2 / 帮助 3 / 首页搜索框 5 / **设置页从滚动区 15**
（`tmp/probe_tab_cost.py` 复测，定义钉死：起点=该页第一个可 Tab 的控件，按真 Tab 键逐跳走到焦点落进页签为止）。想靠重排链条解决不掉：Qt 的 Tab 链是**单进单出**的循环，一个控件只能有
一个后继，`setTabOrder` 能把冷启动那一页的页签提到第 1 跳（`tmp/probe_reach.py` 实测），
代价是把搜索框的清除键挤到表格后面、而且其它四页根本提不上去。所以不动链，
给「切页」本身加一条不依赖焦点位置的路：**Ctrl+1..5** 直接跳到第 N 页、
**Ctrl+Tab / Ctrl+Shift+Tab** 前后翻且绕回。加之前这些键实测全是空的（不改页、不挪焦点、
Ctrl+1 也不会往搜索框里插字符），走库自己的 `switchTo()` 所以左侧高亮和 qrouter 跟着一起变。
闸门第 11 步把这几条都钉住，并加了反向对照：把快捷键 setEnabled(False) 之后必须翻不动页。

同一轮把 `_ToastBar` 里那个写死的 154 换成实测：图标+边距+关闭按钮那一圈按库的常数几何是 102
（iconWidget 36 + closeButton 36 + 外边距 6+6 + 文本内边距 1 + addSpacing(12) + spacing 5），
但真条子量出来是 167 且与父宽无关——154 是当年凑的，比实测少 13px，于是第一轮几乎总能差一点点、
要靠循环再收一次。现在第一轮用常数几何、之后每轮用上一次量到的那一圈，
极限 456 宽那条提醒从 433 收到 419（`render_v169_infobar_fit.py` 第 6 步）。

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
python scripts/package.py --check        # 只报现状，一个字都不写
python scripts/package.py --link-fix     # 打包 → 拷 out/<版本> → 打 zip → 把 sha256 填进发布说明
                                         #   → 从 exe 里自证版本 → 把桌面 .lnk 指到新产物
python scripts/release.py --tag v1.6.6 --dry-run  # 只做本地预检并打印计划：不取 token、不联网
python scripts/release.py --tag v1.6.6   # 外发：建 Release → 传附件 → digest → 公开页 → 附件列表
```

也可一条走完：`python scripts/package.py --link-fix --publish`（本地检查全绿才允许外发）。
`scripts/package.py` 里几条是硬的：`out/<版本>` 已存在要显式 `--force`（旧目录改名进 `out/_discard/` 保留，
不删）；检测到有 `TrafficBoard.exe` 正从 `out/` 里跑就停下来让人自己关——**脚本绝不代杀进程**；
从 exe 的 PYZ 里读出的 `core.version` 与 `src/core/version.py` 不一致直接拒（防「bump 了版本号却拿旧产物发布」）。

历史：这两半边以前是四步手敲命令（PyInstaller、`cp -r` 到 `out/`、一段内联 zip 脚本、手填 sha256），
`scripts/tbbuild.py` 收着所有判据函数、两个脚本共用，`tests/adv_release_tools.py` 13 例钉住它们。

版本号只在 `src/core/version.py` 定义（关于页、out/ 目录名、zip 名、Release 标签都取它）。
发布前先跑全套 `adv_*.py` + `render_*.py`（全部要求 rc=0），再打 tag、建 Release、传 zip，
最后按 sha256 核对附件——GitHub 附件的 `digest` 字段就是它。

发布说明写在 `docs/releases/<版本>.md`，`release.py --notes` 可省（默认就取这份，缺文件就拒发）。
以前它只躺在 gitignore 的 `tmp/` 里，等于 GitHub 上那份公开文本本机没有留档，
改了什么、发给用户的是什么，下次无从对照。归档与线上有一处已知差异：v1.6.4 那份里一个折行示例
带了本机家目录，归档时把用户名换成 `<用户名>`——不是保密（流水里本来就写着这类路径），
是真实用户名对读者没有信息量。

`scripts/release.py` 取代了以前每轮 sed 复制出来的一份 `publishXXX.py`，顺序是硬的：
先扫发布说明里没填完的 `@XXX@` 占位符（**在拿 token 之前**拒，避免发半份说明）→
`git credential fill` 取 token（只打印长度与后 4 位，从不打印明文）→ 建 Release
（422「已存在」就复用那个 tag）→ 传附件 → 按本地 sha256 比对 digest（GitHub 惰性算摘要，重试 6 次）→
抓公开的 `/releases` 页面 HTML，里面必须能看见这个 tag →
**抓 `/releases/expanded_assets/<tag>` 片段，里面必须列出这个 zip**。
前面几步全绿但公开列表看不见，对用户来说就是没发布。

最后一步是 v1.6.5 发布时补上的：Releases 页的附件列表改成前端懒加载了，tag 页 HTML 里搜不到 zip 文件名
（实测：标题、正文句子、SHA 都在，zip 名 0 命中）——只验「版本可见」会漏掉「用户点不到那个包」。
v1.6.5 当时是手工抓片段验的（HTTP 200、列出 55.8 MB 的 zip；直链匿名 302 跟随后 200），现在进脚本了。


## 已知边界（如实说明）

- 进程级流量字节统计来自 Clash 内核数据；纯系统视角下 Windows 不提供按进程字节计数，显示"—"
- UDP 连接的远端地址 Windows 系统表不提供（系统限制），UDP 明细依赖 Clash 数据源
- TUN 模式下进程归因依赖内核 `find-process-mode`（默认 strict 可用；被关掉时体检页会明确告知）
- 未提权时部分系统进程的归属信息拿不全

## License

MIT
