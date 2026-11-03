# 新手行情看板（market-dashboard）

给投资新手的**单文件行情看板**：一个 HTML 文件，双击就能打开，能离线看，填的数据存在自己电脑上。
也是 [WorkBuddy](https://www.workbuddy.cn) 的一个 Skill——说一句「帮我生成一个行情看板」就能装好。

**在线版**：<https://newbie-market-dashboard.app.workbuddy.host/>

> ⚠️ 本项目只做**记录和提示**，不给任何买卖建议，不出现目标价。行情数据来自公开免费接口，不保证实时。

## 它长什么样

整页分成三块（点左侧页签切换），一段只回答一个问题：

| 页 | 段 | 回答的问题 |
|---|---|---|
| 今天 | 我的持仓 | 跟我有关的今天怎么样 |
| 今天 | 全球市场 | 外面在发生什么 |
| 今天 | 市场情绪 | 普涨还是普跌 |
| 今天 | 板块题材 | 谁在涨 |
| 今天 | 资金流向 | 钱是净进还是净出 |
| 今天 | 今天发生了什么 | 按市场把当天复盘一遍 |
| 历史与位置 | 指数位置 | 处在近 60 个交易日什么位置 |
| 历史与位置 | 成交额 | 今天算大还是算小 |
| 历史与位置 | 资金 | 这几天钱进还是出 |
| 历史与位置 | 全球长周期 | 外面这几个月怎么走 |
| 我的账本 | 交易台账 | 我买卖过什么 |
| 我的账本 | 持仓与净值 | 现在值多少、怎么变 |
| 我的账本 | 复盘日记 | 当时我是怎么想的 |

## 三种用法

1. **在线版**：直接打开 <https://newbie-market-dashboard.app.workbuddy.host/>（数据只存本机浏览器，或登录后同步到自己的云端）。
2. **本地离线版**：克隆本仓库后跑一键脚本（macOS）：
   ```bash
   python3 scripts/make_dashboard.py
   ```
   会生成 `./行情看板/` 目录并自动打开浏览器；以后双击目录里的「启动看板」即可。加 `--no-fetch` 用仓库自带的出厂快照，断网也能生成。
3. **作为 WorkBuddy Skill**：把仓库内容放进 `~/.workbuddy/skills/market-dashboard/`，对 Agent 说「生成行情看板」即可。

## 数据从哪来

- 东方财富 / 腾讯的**公开行情接口**（无需 API Key），交易时段内每 60 秒自动刷新；收盘后显示当天收盘数据。
- 仓库自带一份出厂快照（`assets/data/raw.json`），页面先用快照秒开，再尝试拉实时覆盖——所以**断网也能打开**，右上角徽标会如实告诉你数据到几点。
- 资金流向口径：沪深两市 = 沪市 + 深市，两个来源按时间戳取交集相加；只有单边数据时会如实标注「只算沪市」。

## 数据存哪

- 本地版（`make_dashboard.py` 生成的）**不连任何云端**，只存这台电脑的浏览器里，可用「导出 / 导入」搬家。
- 在线版不登录也只存本机；登录 WorkBuddy 后同步到你自己的云端，换设备登录同一账号即可。

## 质量自检

改过代码后跑三件套（都在 `assets/`）：

```bash
python3 assets/verify.py         # 离线结构断言，387 条
python3 assets/run_selftest.py   # 无头 Chrome 真跑页面，343 条（连不上行情接口的项会跳过）
python3 assets/check_narrow.py   # 375/390/414/768 四档窄屏溢出检查
```

## 目录结构

```
SKILL.md               WorkBuddy Skill 定义
使用说明.md / 安装须知.md / 维护说明.md
references/新手导读.md  页面每一段在讲什么
scripts/make_dashboard.py  一键生成本地版
assets/build.py        页面源码（HTML/CSS/JS 全在这一个文件里）
assets/fetch_public.py 快照抓取（40 个公开接口）
assets/data/raw.json   出厂快照
assets/verify.py / selftest.py / run_selftest.py / check_narrow.py
```

## 免责声明

本项目用于个人学习与记录。行情数据来自第三方公开接口，可能延迟、缺失或有误；页面上的任何数字都不构成投资建议。市场有风险，投资需谨慎。

## License

[MIT](LICENSE)
