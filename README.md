# 短剧别名检索站｜静态构建完整开发方案

> 方案定位：**离线爬虫定时构建静态索引包 + 纯前端本地检索**
> 核心特点：前端无后端服务、无数据库；所有抓取、清洗、合并、校验在独立定时任务完成，输出静态 JSON 给前端消费；仅做剧目别名索引 + 官方跳转，不存储视频、不提供播放。
> 适用阶段：MVP 上线，低成本、易托管，后续可平滑升级服务端检索。

## 一、项目目标与边界

### 目标

解决 AI 短剧别名、错名、营销标题、换皮重名检索困难；用户输入剧名 / 错名 / 角色名，快速找到对应官方平台入口（红果、番茄、河马短剧等），点击直达官方分享链接，支持 Scheme 拉起 App。

### 功能范围（MVP）

✅ 剧名 / 别名 / 角色名模糊检索，支持错别字容错
✅ 剧目卡片：标准剧名、别名列表、封面、简介、主演、题材标签、AI 短剧标记、平台跳转按钮
✅ 移动端优先，优先拉起 App，失败跳转 H5 官方页
✅ 爬虫离线定时采集、清洗、别名合并、链接有效性校验
✅ 静态产物自动上传至静态托管，前端自动加载最新索引

### 暂不纳入 MVP（后续迭代）

❌ 用户提交别名表单（需要接收接口 /webhook）
❌ 剧情自然语言语义检索（需要 LLM 接口）
❌ 截图 OCR 搜剧
❌ 评论、收藏、用户中心
❌ 在线后台管理面板（爬虫脚本本地 / 服务器控制台管理）

### 合规边界（硬性约束）

1. 站点定位：**短剧信息导航索引站**，页面显著声明：本站仅收录剧目元信息与官方跳转链接，不存储音视频，版权归属对应平台；提供侵权投诉邮箱，收到通知后在构建脚本中删除对应条目（应急手动移除机制见第七节风险 5）。
2. 爬虫仅采集公开榜单、达人后台公开剧目元数据；限速、设置 UA、遵守 robots.txt，禁止逆向播放接口。
3. 只收录平台原生官方分享链接，不收录解析站、盗版、网盘链接。平台准入白名单见 `data/platforms.json`：仅收录平台自有正规产品（红果、番茄、河马、九州、麦芽、喜番等），第三方聚合导航站、网盘搜索站、解析站一律排除；官网域名与 Scheme 未经官方渠道核实的标注 `need_verify`，不得写入跳转链接。
4. 国内上线必须 ICP 备案，底部放置备案号 + 隐私政策 + 版权声明。

## 二、整体架构

```
[定时任务层（独立Python脚本，crontab/github action/云函数）]
        ↓ 采集 → 清洗 → 别名合并 → 去重 → 链接校验
        ↓ 输出静态索引包
drama_index.json + cover/封面（可选OSS托管）
        ↓ 自动上传到静态资源地址（OSS/Vercel仓库）
[纯前端静态站点]
        ↓ 浏览器加载 drama_index.json
        ↓ Fuse.js / MiniSearch 前端本地检索
        ↓ 渲染结果卡片，跳转官方链接
用户（手机/PC）
```

### 分层说明

1. **构建层（离线任务，不属于 Web 服务）**
   - 职责：采集、数据归一化、别名库合并、链接有效性检测、生成索引文件
   - 运行时机：定时触发（例如每日凌晨 2 点，或 4 小时一次）；失败重试、日志落地
   - 运行载体：轻量云服务器（crontab）、腾讯云函数、GitHub Actions（免费，适合小数据量）
2. **资源层**
   - drama_index.json：主索引数据
   - 封面图：不内联 JSON，存放于对象存储 OSS，脚本只保存图片 URL；建议脚本只下载缩略图，不存原图
3. **前端层（100% 静态，无接口）**
   - 静态 HTML+JS+CSS，可托管在阿里云 OSS、Vercel、Netlify、Cloudflare Pages
   - 浏览器加载索引文件，内存内完成检索，所有搜索逻辑在客户端
   - 优点：无服务带宽压力、没有接口暴露；缺点：数据总量受浏览器内存限制（MVP 控制在≤10000 条）

## 三、数据模型设计

### 单条剧目结构（输出给前端 JSON 结构）

```
{
  "id": "dr_10001",
  "standard_title": "标准官方剧名",
  "aliases": ["营销标题","错名版本","简称","切片标题","曾用名"],
  "record_no": "微短剧备案号（可选）",
  "actors": ["男主","女主"],
  "tags": ["AI短剧","逆袭","甜宠"],
  "is_ai_drama": true,
  "cover_url": "https://xxx.oss-cn-beijing.aliyuncs.com/cover/dr_10001.jpg",
  "intro": "简短剧情简介",
  "platform_links": [
    {
      "platform": "红果短剧",
      "scheme": "hongguo://xxx",
      "h5_url": "https://xxx官方分享H5链接",
      "valid": true
    },
    {
      "platform": "番茄短剧",
      "scheme": "fanqie://xxx",
      "h5_url": "https://xxx官方分享H5链接",
      "valid": true
    },
    {
      "platform": "河马短剧",
      "scheme": "hema://xxx",
      "h5_url": "https://xxx官方分享H5链接",
      "valid": true
    }
  ],
  "updated_at": "2026-11-21"
}
```

> `valid`标记：脚本定时访问链接，404 / 失效标记为 false，前端不展示失效入口。

### 脚本内部工作模型（Python）

脚本内部维护两套结构：

1. `drama_store`：主剧目池，合并策略统一为——**有备案号时按 `record_no` 合并；无备案号时默认分开记录，不做自动模糊合并**；仅当剧情相似度与主演匹配同时超过设定阈值、且经人工确认后，才作为同一剧目合并，防止换皮剧误合并（与第七节风险 2 保持一致）
2. `alias_pool`：别名池，支持分类标记：`typo错别字` / `marketing营销标题` / `short简称` / `old_name曾用名`

清洗规则（脚本内置）

- 去除：全集、大结局、完整版、一口气看完等营销后缀
- 全角转半角、去除标点、繁体转简体
- 同音 / 形近纠错词表（内置简单词表，后期可扩充）

## 四、技术栈选型

### 1. 离线构建脚本（Python）

- 运行环境：Python3.10+
- 网络请求：`httpx`（异步，限速控制）
- 数据建模校验：`pydantic v2`
- 爬虫任务调度：`APScheduler`（本地调试），线上使用 crontab
- 文本清洗：`jieba`分词、`zhconv`繁简转换
- 链接存活检测：HEAD 请求，超时控制
- 输出：json；图片上传：`oss2`（阿里云 OSS SDK）
- 日志：`logging`，输出构建日志、新增剧目数、失效链接数量

> 爬虫策略：单域名并发≤2，请求间隔 1~3s；禁止高频爬取。优先使用番茄 / 红果达人后台公开榜单，不直接爬播放页。

### 2. 前端检索方案二选一

- 方案 A（推荐 MVP，上手简单）：**Fuse.js**
  - 优势：开箱即用，多字段权重配置，天然适合别名模糊搜索、错别字容错；适合 5000 条以内。
  - 权重配置建议：`aliases(权重2.0) > standard_title(1.5) > actors(0.8) > intro(0.3)`
- 方案 B（数据量接近 1w）：**MiniSearch**
  - 优势：预构建倒排索引，索引体积更小、检索速度更快；脚本阶段提前生成 minisearch 索引 json，前端直接加载索引，不用现场构建。

### 3. 前端页面技术

- 基础：原生 HTML + Tailwind CSS（CDN 引入，无需构建工具）
- JS：原生 JS，不引入 Vue/React，降低复杂度；移动端适配优先
- 托管：国内 OSS（备案） / Cloudflare Pages（海外访问）
- 跳转逻辑：优先尝试打开 Scheme；手机端 Scheme 唤起失败或超时（判定不可靠，见第七节风险 7），自动打开 h5_url 并保留显式“打开 H5”按钮兜底；PC 端直接打开 H5 链接

### 4. 配套工具（可选）

- 存储：阿里云 OSS（存放封面图 + 静态站点）
- 定时：轻量机 crontab；免费备选 GitHub Actions（适合每日构建）
- 版本备份：每次构建后，把旧 json 备份一份，防止单次构建炸库

## 五、开发阶段计划（分 4 阶段）

### 阶段 1：本地验证（3 天）

1. 编写 Python 基础脚本：
   - 剧目数据结构 Pydantic 模型
   - 清洗函数、别名合并逻辑
   - 手动导入一批样例数据（几百条热门 AI 短剧 + 别名）
   - 输出 drama_index.json
2. 编写单文件前端页面：HTML+Tailwind+Fuse.js，本地打开直接搜索
3. 本地测试：错名搜索、别名命中、跳转逻辑、移动端唤起 App

> 交付物：本地可跑 Demo（前端单页 + Python 导出脚本）

### 阶段 2：爬虫接入 & 自动构建（5 天）

1. 接入源：优先达人榜单公开列表，实现增量采集
2. 增加链接存活检测逻辑，标记失效链接
3. 封面下载、缩略图压缩、上传 OSS 逻辑
4. 增加去重合并逻辑，基于备案号 / 标题相似度合并别名
5. 本地调试定时构建，输出完整索引包
6. 构建脚本同时产出 MiniSearch 预索引文件（`minisearch_index.json`），前端按数据量（5000 条阈值）切换检索实现
7. 增加黑名单：广电通报下架剧目，构建时自动过滤

### 阶段 3：部署上线 & 自动化（3 天）

1. 服务器部署 Python 脚本，配置 crontab 定时任务
2. 编写自动上传脚本：构建完成后把 json、封面上传 OSS
3. 前端页面上传 OSS，配置静态网站托管
4. ICP 备案，页面增加版权声明、隐私政策、投诉邮箱
5. 监控：构建失败日志告警（简单版本：日志输出，进阶：邮件 / 企业微信通知）

### 阶段 4：观察迭代（长期）

1. 持续扩充别名词表，人工定期修正合并错误
2. 当条目稳定超过 1w 条：评估迁移到服务端检索（Meilisearch）
3. 按需新增可选模块：UGC 提交（云函数接收）、LLM 剧情检索接口

## 六、性能与容量规划

| 数据规模 | 方案 | 说明 |
| --- | --- | --- |
| ≤5000 条 | Fuse.js 单 JSON | MVP 首选，几百 KB，浏览器加载无感 |
| 5000~10000 条 | MiniSearch 预索引 | 脚本预生成倒排索引，检索更快 |
| >10000 条 | 升级服务端检索 | 独立 Meilisearch 服务，前端改为请求接口 |

**方案切换实施说明**：Fuse.js 与 MiniSearch 为同一前端的两套可选实现，切换点为 5000 条。当条目数进入 5000–10000 区间时，构建脚本（`builder/exporter.py`）增加 MiniSearch 索引预生成步骤，输出 `minisearch_index.json`，前端改为直接加载该索引，无需在浏览器现场构建。该能力在阶段 2 随索引产出一并交付，不依赖后期路线图。

> 优化手段：如果后续想继续静态方案，可以做分片：`meta.json`（元信息 + 词表） + chunk_xx.json，搜索词命中词表后，按需加载对应分片，避免一次性加载全量数据。

## 七、风险清单与应对方案

1. **平台分享链接定期失效**
应对：构建脚本内置链接存活检测，失效标记 valid=false，前端隐藏；日志统计失效数量。
2. **同一剧情换皮多平台上架，重名泛滥**
应对：备案号作为第一主键；无备案号时分开记录，在简介标注 “同剧情换皮版本”，不强行合并；仅在高置信度（剧情相似度 + 主演双阈值）且经人工确认后才合并（与第三节 `drama_store` 合并策略一致）。
3. **爬虫被源站限流 / 封禁 IP**
应对：严格限速；不要高频爬；优先达人后台渠道；可以加代理池（成本上升，MVP 不推荐）。
4. **静态 JSON 体积过大，浏览器卡顿**
应对：控制单批数据量，提前切分片、改用 MiniSearch 预索引。
5. **合规投诉**
应对：页面避风港声明；脚本内置黑名单；收到投诉邮件，在下一轮构建直接删除对应剧目；同时提供应急手动移除通道——从黑名单即时屏蔽对应条目并触发一次增量构建，无需等待下一轮定时任务（弥补每日构建下最长约 24 小时的空窗期）。
6. **链接存活检测全量校验开销过大**
应对：1 万条 × 2 平台的全量 HEAD 校验按 1~3s 间隔需数小时至十余小时；链接校验与采集限速策略分离，采用分批轮换（每日校验其中一批），并对超时链接缩短超时时间。
7. **Scheme 唤起失败检测不可靠**
应对：浏览器无标准 API 判断 App 是否安装，仅能通过页面可见性 / 超时启发式推断，存在误判；前端保留显式“打开 H5”按钮作为兜底，Scheme 检测超时（建议 1.5~2s）即自动跳转 h5_url，PC 端直接打开 H5。

## 八、目录结构参考

```
drama-alias-search/
├── builder/
│   ├── models.py          # Pydantic数据模型
│   ├── cleaner.py         # 文本清洗、别名归一化
│   ├── crawler.py         # 采集逻辑（含跨源同名合并开关）
│   ├── store.py           # ★ 累计原始池：跨轮次持久化 + 幂等去重
│   ├── sources.py         # ★ 可插拔平台适配器（配置驱动）
│   ├── hongguo_source.py  # 红果专用解析器（sources.py 复用其 ld+json 能力）
│   ├── link_checker.py    # 链接有效性检测
│   ├── exporter.py        # 导出 drama_index.json
│   ├── oss_upload.py      # 上传json、封面到OSS
│   └── config.py          # 配置：UA、间隔、OSS密钥、黑名单、池/源路径
├── data/
│   ├── platforms.json     # 短剧平台白名单（含排除清单）
│   ├── sources.json       # ★ 可插拔采集源配置（enabled+active 才采集）
│   ├── raw_pool.json      # ★ 累计原始池（生成物，跨轮次累积）
│   ├── blacklist.txt      # 下架剧目黑名单
│   ├── typo_map.txt       # 错名修正词表
│   ├── sample_dramas.json # 样例剧目数据
│   └── backup/            # 历史json备份
├── log/
│   ├── build.log          # 构建日志（程序自身管理）
│   └── cron_YYYYMMDD.log  # ★ 定时更新日志（按日，保留30天）
├── scripts/               # ★ Ubuntu 定时自动更新
│   ├── cron_update.sh     # 定时主脚本：定位+加锁+构建+日志+可选git推送
│   ├── crontab.example    # crontab 配置示例（每天/每6小时/离线等变体）
│   ├── drama-update.service  # systemd service 示例
│   ├── drama-update.timer    # systemd timer 示例（每天 02:10）
│   └── README.md          # 部署步骤与运维说明
├── frontend/
│   ├── index.html         # 主搜索页面（单文件）
│   └── privacy.html       # 隐私政策、版权声明页
├── dist/                  # ★ 生成物：Cloudflare Pages 等静态托管的发布目录
│   ├── index.html         # 首页（fetch ./drama_index.json，含合规配置兜底）
│   ├── drama_index.json   # 主索引（meta.site 内含站点合规配置）
│   ├── privacy.html       # 合规页
│   └── search.html        # 自包含单文件版（备用）
├── site.json              # 站点合规配置（投诉邮箱、ICP备案、隐私/版权文案）
├── prepare_deploy.py      # ★ 把产物打包到 dist/（Cloudflare 发布用）
├── main.py                # 样例数据构建入口
├── crawl_hongguo.py       # 红果单次抓取（旧入口，保留兼容）
├── crawl_all.py           # ★ 累计抓取：多平台→累计池→归一化→导出
├── build_site.py          # ★ 一键发布：累计抓取→索引→搜索页→合规页
├── build_frontend.py      # 把索引+配置内联进模板，产出自包含 HTML
├── index.html             # 搜索页模板（fetch 加载 / file:// 手动选文件）
├── privacy.template.html  # 合规页模板
├── search.html            # 生成物：自包含单文件搜索页（真实数据，双击即用）
├── privacy.html           # 生成物：自包含合规页
├── drama_index.json       # 主索引（真实数据，供 index.html 托管版 fetch）
├── hongguo_crawl.json     # 红果真实抓取原始索引
└── requirements.txt
```

### 累计抓取与多平台适配

采集已从「单次覆盖」升级为「累计 + 去重 + 可插拔多平台」：

- **累计池**：每轮抓取的原始条目写入 `data/raw_pool.json`，以 `(来源, 详情页URL)` 为幂等键——重复抓取同一详情页会更新字段而非新增。历史数据不会丢失，除非显式 `--reset`。
- **去重**：导出时从整个池一次性归一化。合并优先级：备案号 → 人工确认组 → **清洗后标准剧名完全一致**（跨轮次/跨平台精确合并，非相似度模糊匹配）→ 各自独立。链接按 `(平台, URL)` 去重，同一剧目可聚合多个平台入口。严格按 README「无备案号一律分开」时加 `--no-merge-same-title`。
- **多平台**：平台采集规则集中在 `data/sources.json`。新增平台只需追加一条配置（`entry` 决定入口：home/sitemap/api；`parser` 决定解析：ldjson_tvseason / nextjs_bookinfo / json），无需改采集代码。只有 `enabled=true` 且 `status=active` 的平台才会被采集，**不猜测填写域名**。当前接入两个平台：

  | 平台 | 域名 | 核验依据 | 解析器 | 主演 |
  | --- | --- | --- | --- | --- |
  | 红果免费短剧 | hongguoduanju.com | ICP 京ICP备2021028909号-43（笔墨留香），robots 允许 `/detail` | `ldjson_tvseason`（schema.org） | 无 |
  | 河马剧场 | www.kuaikaw.cn | ICP 京ICP备17057640号-55（点众快看），robots `Allow:/`、63 个 `/drama/{id}` 详情页 | `nextjs_bookinfo`（__NEXT_DATA__） | 有（actor/actress） |

  其余平台保持关闭的原因：番茄免费短剧即红果（同主体，避免重复）；爱奇艺/腾讯 robots 专门封禁 AI 爬虫；长视频平台域名未核验；App-only 平台无公开详情页。**接入新平台前须实测官方域名、ICP 备案、robots 与详情页结构四项，任一不满足即不启用。**

命令：

```bash
python3 crawl_all.py                # 累计抓取所有启用平台并导出
python3 crawl_all.py --limit 10     # 每平台最多 10 条
python3 crawl_all.py --offline      # 不联网，仅从累计池重新导出
python3 crawl_all.py --reset        # 清空池后重抓（快照模式）
python3 build_site.py               # 一键：累计抓取 → 索引 → 搜索页 → 合规页
python3 build_site.py --offline     # 离线从累计池重建站点（网络不通时）
```

### 一键发布与运行方式

- **一键发布（推荐）**：`python3 build_site.py` — 累计抓取所有启用平台（限速约 1-2 分钟）→ 从全池去重生成 `drama_index.json` → 内联生成 `search.html` + `privacy.html`。网络失败时自动降级：优先从累计池重建，再退回缓存文件。跳过抓取用 `--skip-crawl`；样例数据用 `--source sample`。
- **离线直接双击**：`search.html` 为自包含单文件（含真实数据），双击即可搜索，无需服务器、无网络依赖、无 CDN 也能用（内置降级检索）。
- **静态托管**：上传 `index.html` + `drama_index.json` + `privacy.html` 到同一目录，页面自动 fetch 索引；`file://` 双击 `index.html` 时因 CORS 限制会提示手动选择索引文件。
- **检索实现**：优先用 CDN 的 Fuse.js（权重 aliases 2.0 > standard_title 1.5 > actors 0.8 > intro 0.3）；CDN 不可用时自动降级为内置零依赖搜索（含别名池错别字反查）。真实数据别名从剧名派生（去季标识 / 数字季号）。
- **跳转**：仅展示 `valid=true` 的官方链接；移动端优先尝试 Scheme，1.8s 超时自动降级 H5，并保留显式按钮兜底；PC 端直接打开 H5。
- **合规配置**：编辑 `site.json` 填写投诉邮箱与 ICP 备案号后，重新运行 `python3 build_site.py --skip-crawl` 即可更新页脚与合规页。未填写时页面会显示醒目提示。

### Ubuntu 定时自动更新（crontab / systemd）

`scripts/cron_update.sh` 封装了「累计抓取 → 发布 → 可选 git 推送」的完整定时任务：用脚本自身位置定位项目根目录（不依赖 crontab 工作目录）、`flock` 防重叠抓取、按日写日志 `log/cron_YYYYMMDD.log`、失败保留旧产物并返回非零退出码。详细步骤见 `scripts/README.md`。

```bash
# 1) 部署到固定目录并赋权（以 /opt/drama-search 为例）
chmod +x /opt/drama-search/scripts/cron_update.sh
/opt/drama-search/scripts/cron_update.sh            # 先手动验证一次

# 2) 方案 A：crontab —— 每天 02:10 更新（详见 scripts/crontab.example）
10 2 * * * /opt/drama-search/scripts/cron_update.sh >> /opt/drama-search/log/crontab_stdout.log 2>&1

# 2) 方案 B：systemd timer —— 更推荐，可查日志、可补跑（详见 scripts/README.md）
sudo cp scripts/drama-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now drama-update.timer
```

- 可选开关：`DRAMA_EXTRA_ARGS="--offline"`（离线服务器只从累计池重建）、`DRAMA_GIT_PUSH=1`（成功后 git 提交推送到静态托管）、`DRAMA_GIT_BRANCH=main`、`PYTHON`、`DRAMA_LOG_DIR`、`DRAMA_LOCK_FILE`。
- 运维：日志见 `log/cron_YYYYMMDD.log`（保留 30 天）与 `log/build.log`；抓取限速由 `builder/config.py` 控制（1-3s 间隔、单域名并发 2、遵守 robots.txt），定时脚本不额外加压目标站。

### GitHub + Cloudflare Pages 全自动部署（推荐）

把整个项目仓库传到 GitHub，用 Cloudflare Pages 连接该仓库，即可实现「定时抓取 → 生成 → git 推送 → Cloudflare 自动重新部署」的无人值守闭环：

```bash
# 首次：把整个仓库传到 GitHub（.gitignore 已排除缓存/日志/备份）
git init && git add -A && git commit -m "init"
git remote add origin https://github.com/<用户>/<仓库>.git
python3 build_site.py --skip-crawl && python3 prepare_deploy.py  # 确保 dist/ 已生成
git add -A && git commit -m "chore: 生成静态产物"
git push -u origin main
```

Cloudflare Pages 建站：**Connect to Git** 选该仓库 → Build command **留空**（纯静态无需构建）→ Output directory 填 **`dist`** → Deploy。之后每次服务器定时任务 `DRAMA_GIT_PUSH=1` 推送新产物，Cloudflare 自动重新部署。

> 合规配置：站点邮箱/备案号在 `site.json`；`build_site.py` 会把它们写入 `drama_index.json` 的 `meta.site`，Cloudflare 托管版首页 `index.html` 从该字段读取（不再显示占位符）。改完记得重新 `build_site.py && prepare_deploy.py` 并推送。

## 九、交付物清单

1. requirements.txt 依赖清单
2. Python 构建脚本集：models / cleaner / crawler / store / sources / exporter / link_checker
3. 累计抓取 crawl_all.py + 一键发布 build_site.py + 内联构建 build_frontend.py
3b. 可插拔采集源配置 data/sources.json + 累计池 data/raw_pool.json
3c. Ubuntu 定时自动更新脚本集 scripts/（cron_update.sh + crontab/systemd 示例 + 部署说明）
3d. 静态部署打包 prepare_deploy.py + dist/ 目录（Cloudflare Pages / GitHub 自动部署）
4. 单文件前端 search.html（真实数据，Fuse.js + 内置降级，双击即用）
5. 合规页 privacy.html + 站点配置 site.json
4. MiniSearch 预索引生成脚本及前端切换逻辑（数据量进入 5000–10000 区间时启用）
5. 隐私政策、版权声明页面模板

## 十、上线检查清单（上线前逐条核对）

- ICP 备案完成，备案号放置页面底部
- 页面避风港版权声明完整
- 侵权投诉邮箱填写，可正常接收邮件
- 爬虫限速、UA 配置完成，不暴力请求
- 所有跳转链接均为平台官方原生分享链接
- 黑名单导入，下架剧目不会被收录
- 定时构建任务测试跑通，成功上传产物
- 移动端测试 Scheme 唤起 App 逻辑，以及唤起失败 / 超时后跳转 H5 的兜底逻辑
- 失效链接标记逻辑验证生效
- JSON 文件大小测试，确认手机端加载流畅

## 十一、扩展路线图

1. 短期（MVP 稳定后）：检索体验优化——分词与字段权重调优、索引分片加载（MiniSearch 预索引能力已在阶段 2 交付）
2. 中期：增加云函数接收 UGC 别名提交，人工审核入库
3. 中长期：接入 LLM，在构建阶段为每条剧目生成剧情向量，支持剧情描述检索
4. 长期：数据量突破 1w 条，迁移 Meilisearch 服务端检索架构，保留原有爬虫构建逻辑
