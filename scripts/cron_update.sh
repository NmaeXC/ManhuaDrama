#!/usr/bin/env bash
# 短剧索引定时更新脚本（Ubuntu / crontab 用）
#
# 功能：定位项目根目录 → 加文件锁防并发 → 调用 build_site.py 累计抓取并发布 →
#       记录按日日志 → 可选把生成物 git 提交/推送（供静态托管自动更新）。
#
# 设计要点：
#   - 全部路径由脚本自身位置推导，不依赖 crontab 的工作目录与环境变量。
#   - flock 防止上一轮未结束就重叠运行（抓取限速约 1-2 分钟）。
#   - 构建失败不删除旧产物，站点继续用上一版本；失败会写日志并返回非零退出码。
#   - 默认不联网失败即降级（build_site.py 内置累计池/缓存兜底）。
#
# 用法：
#   ./scripts/cron_update.sh                 # 正常定时更新
#   DRAMA_EXTRA_ARGS="--offline" ./scripts/cron_update.sh   # 离线从累计池重建
#   DRAMA_GIT_PUSH=1 ./scripts/cron_update.sh               # 成功后 git 提交并推送
#
# 可覆盖的环境变量：
#   PYTHON              Python 解释器，默认自动探测 python3/python
#   DRAMA_EXTRA_ARGS    透传给 build_site.py 的额外参数，如 "--limit 100 --offline"
#   DRAMA_GIT_PUSH      1=构建成功后 git commit + push，默认 0
#   DRAMA_GIT_BRANCH    推送分支，默认 main
#   DRAMA_LOG_DIR       日志目录，默认 <项目>/log
#   DRAMA_LOCK_FILE     锁文件路径，默认 /tmp/drama_search_update.lock

set -uo pipefail

# ---------- 1. 定位项目根目录（不依赖调用者 cwd）----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------- 2. 可配置项 ----------
PYTHON_BIN="${PYTHON:-}"
EXTRA_ARGS="${DRAMA_EXTRA_ARGS:-}"
GIT_PUSH="${DRAMA_GIT_PUSH:-0}"
GIT_BRANCH="${DRAMA_GIT_BRANCH:-main}"
LOG_DIR="${DRAMA_LOG_DIR:-${PROJECT_ROOT}/log}"
LOCK_FILE="${DRAMA_LOCK_FILE:-/tmp/drama_search_update.lock}"

mkdir -p "${LOG_DIR}" || { echo "无法创建日志目录 ${LOG_DIR}" >&2; exit 1; }
LOG_FILE="${LOG_DIR}/cron_$(date +%Y%m%d).log"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "${LOG_FILE}"
}

log "===== 定时更新开始 ====="
log "项目目录: ${PROJECT_ROOT}"

# ---------- 3. 探测 Python ----------
if [[ -n "${PYTHON_BIN}" ]]; then
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
        log "错误：指定的 PYTHON=${PYTHON_BIN} 不存在，退出。"
        exit 1
    fi
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    log "错误：未找到 python3/python，请先安装 Python 3。"
    exit 1
fi
log "Python: ${PYTHON_BIN} ($(${PYTHON_BIN} --version 2>&1))"

# ---------- 4. 文件锁：防止并发重入 ----------
if ! command -v flock >/dev/null 2>&1; then
    log "警告：系统缺少 flock（Ubuntu 通常自带，属 util-linux 包），本次不启用并发保护。"
    LOCK_FD=""
else
    # 用 fd 9 持有锁；flock -n 非阻塞，拿不到锁说明上一轮仍在运行
    exec 9>"${LOCK_FILE}" || { log "错误：无法打开锁文件 ${LOCK_FILE}"; exit 1; }
    if ! flock -n 9; then
        log "另一轮更新正在运行（锁 ${LOCK_FILE}），本次跳过，避免重叠抓取。"
        log "===== 定时更新跳过 ====="
        exit 0
    fi
    log "已获取文件锁: ${LOCK_FILE}"
    LOCK_FD=9
fi

# ---------- 5. 执行一键发布 ----------
cd "${PROJECT_ROOT}" || { log "错误：无法进入项目目录 ${PROJECT_ROOT}"; exit 1; }
if [[ ! -f "${PROJECT_ROOT}/build_site.py" ]]; then
    log "错误：未找到 ${PROJECT_ROOT}/build_site.py，请检查部署目录。"
    exit 1
fi

# 把额外参数按空格拆成数组（仅用于显示与传参，不做 eval）
read -r -a EXTRA_ARR <<< "${EXTRA_ARGS}"
CMD=("${PYTHON_BIN}" "${PROJECT_ROOT}/build_site.py" "${EXTRA_ARR[@]}")
log "执行命令: ${CMD[*]}"

START_TS=$(date +%s)
"${CMD[@]}" >>"${LOG_FILE}" 2>&1
RC=$?
END_TS=$(date +%s)
log "构建退出码: ${RC}，耗时 $((END_TS - START_TS))s"

if [[ ${RC} -ne 0 ]]; then
    log "错误：build_site.py 执行失败（详见 ${LOG_FILE} 与 ${LOG_DIR}/build.log）。"
    log "旧产物未删除，线上仍是上一版本。本次定时更新终止。"
    log "===== 定时更新失败 ====="
    exit "${RC}"
fi

# ---------- 5b. 生成静态部署目录 dist/（供 Cloudflare Pages 等发布）----------
if [[ -f "${PROJECT_ROOT}/prepare_deploy.py" ]]; then
    "${PYTHON_BIN}" "${PROJECT_ROOT}/prepare_deploy.py" >>"${LOG_FILE}" 2>&1
    PREP_RC=$?
    if [[ ${PREP_RC} -ne 0 ]]; then
        log "警告：prepare_deploy.py 失败（退出码 ${PREP_RC}），dist/ 未更新。"
    else
        log "静态部署目录 dist/ 已更新。"
    fi
else
    log "提示：未找到 prepare_deploy.py，跳过 dist/ 打包。"
fi

# ---------- 6. 可选：git 提交并推送生成物（GitHub Pages 等静态托管）----------
if [[ "${GIT_PUSH}" == "1" ]]; then
    if git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        log "git：暂存生成物…"
        git -C "${PROJECT_ROOT}" add \
            dist/ search.html drama_index.json privacy.html \
            hongguo_crawl.json data/raw_pool.json 2>>"${LOG_FILE}"
        if git -C "${PROJECT_ROOT}" diff --cached --quiet; then
            log "git：无变化，跳过提交。"
        else
            git -C "${PROJECT_ROOT}" commit \
                -m "chore: 定时更新短剧索引 $(date '+%Y-%m-%d %H:%M:%S')" \
                >>"${LOG_FILE}" 2>&1
            if git -C "${PROJECT_ROOT}" push origin "${GIT_BRANCH}" >>"${LOG_FILE}" 2>&1; then
                log "git：已推送到 ${GIT_BRANCH}。"
            else
                log "警告：git push 失败，请检查远程仓库与凭据配置。"
            fi
        fi
    else
        log "警告：DRAMA_GIT_PUSH=1 但项目目录不是 git 仓库，跳过推送。"
    fi
fi

# ---------- 7. 日志轮转：只保留最近 30 天按日日志（build.log 由程序自身管理）----------
find "${LOG_DIR}" -maxdepth 1 -name 'cron_*.log' -type f -mtime +30 -delete 2>/dev/null || true

log "===== 定时更新完成 ====="
exit 0
