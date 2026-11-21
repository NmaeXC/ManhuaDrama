# Ubuntu 定时自动更新部署

定时脚本调用 `build_site.py` 完成「累计抓取 → 去重 → 生成索引 → 生成 search.html / privacy.html」，可再把生成物 git 推送到静态托管仓库，实现全自动更新。

## 一、准备

把项目放到固定目录（下文以 `/opt/drama-search` 为例，请按实际路径替换）：

```bash
sudo mkdir -p /opt/drama-search
# 把本项目文件放到 /opt/drama-search 后：
chmod +x /opt/drama-search/scripts/cron_update.sh
```

确认 Python 3 可用：`python3 --version`（本项目零第三方依赖也能跑）。

先手动跑一次，验证脚本与构建都正常：

```bash
/opt/drama-search/scripts/cron_update.sh
tail -f /opt/drama-search/log/cron_$(date +%Y%m%d).log
```

## 二、二选一：注册定时任务

### 方案 A：crontab（简单、通用）

```bash
crontab -e
```

粘贴（路径按实际替换）：

```
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
10 2 * * * /opt/drama-search/scripts/cron_update.sh >> /opt/drama-search/log/crontab_stdout.log 2>&1
```

表示每天 02:10 更新一次。更多变体见 `crontab.example`。

### 方案 B：systemd timer（推荐，日志可查、可补跑）

```bash
# 1. 两个文件里的 /opt/drama-search 按实际路径替换
sudo nano /opt/drama-search/scripts/drama-update.service
sudo nano /opt/drama-search/scripts/drama-update.timer

# 2. 安装并启用
sudo cp /opt/drama-search/scripts/drama-update.service /etc/systemd/system/
sudo cp /opt/drama-search/scripts/drama-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now drama-update.timer

# 3. 验证
systemctl list-timers drama-update.timer
sudo systemctl start drama-update.service   # 手动触发一次
journalctl -u drama-update.service -f
```

## 三、GitHub + Cloudflare Pages 全自动部署（推荐闭环）

完整链路：**定时抓取 → build_site.py 生成 → prepare_deploy.py 打包 dist/ → git push → Cloudflare 自动重新部署**。

### 首次准备（只需一次）

```bash
cd /opt/drama-search
# 若还没仓库：
git init && git add -A && git commit -m "init"
git remote add origin https://github.com/<用户>/<仓库>.git

# 确保 dist/ 已生成（Cloudflare 的输出目录）
python3 build_site.py --skip-crawl && python3 prepare_deploy.py
git add -A && git commit -m "chore: 生成静态产物"
git push -u origin main

# 配置免密推送（服务器无人值守必须）：SSH key 或带权限的 token，二选一
# 然后开启定时脚本的推送开关（在 crontab 行或 service 的 Environment 里）：
#   DRAMA_GIT_PUSH=1  DRAMA_GIT_BRANCH=main
```

### Cloudflare Pages 建站

1. Dashboard → Workers & Pages → Create → Pages → **Connect to Git**，选该 GitHub 仓库。
2. 构建设置：Framework preset = None；**Build command 留空**；**Output directory = `dist`**；Root directory 留空。
3. Deploy。以后服务器每次 push 新产物，Cloudflare 自动重新部署，无需手动上传。
4. 可选：Custom domains 绑定自己的域名（自动签 SSL）。

### 关键说明

- `dist/` 含 4 个文件：`index.html`（首页，fetch `drama_index.json`）、`drama_index.json`（主索引，`meta.site` 内含合规配置）、`privacy.html`、`search.html`（自包含单文件备用）。
- 合规配置在 `site.json`，`build_site.py` 会写入 `meta.site`；Cloudflare 首页读的是它，改动后需重新 build + prepare_deploy + push。
- `.gitignore` 已排除缓存/日志/备份；`dist/`、各产物、累计池会入库供部署。
- 脚本仅在构建成功后、暂存区有变化时才 commit + push；push 失败只记日志、不影响构建结果。

## 四、运维说明

- **日志**：按日存 `log/cron_YYYYMMDD.log`（保留 30 天）；构建详情见 `log/build.log`；crontab 标准输出见 `log/crontab_stdout.log`；systemd 用 `journalctl -u drama-update.service`。
- **并发保护**：`flock` 锁文件 `/tmp/drama_search_update.lock`，上一轮没跑完时新任务自动跳过，不会重叠抓取。可用 `DRAMA_LOCK_FILE` 改路径。
- **失败策略**：构建失败时退出码非零、写日志，但不删除旧产物，线上继续用上一版本；`build_site.py` 已内置网络失败降级（累计池 → 缓存）。
- **限速合规**：脚本本身不额外发请求，抓取限速在 `builder/config.py`（间隔 1-3s、单域名并发 2、遵守 robots.txt），请勿为求快调小间隔。
- **离线服务器**：网络不通时设 `DRAMA_EXTRA_ARGS="--offline"`，只从 `data/raw_pool.json` 重建，数据不会新增但站点可正常发布。
- **环境变量速查**：`PYTHON`、`DRAMA_EXTRA_ARGS`、`DRAMA_GIT_PUSH`、`DRAMA_GIT_BRANCH`、`DRAMA_LOG_DIR`、`DRAMA_LOCK_FILE`（详见脚本头部注释）。
