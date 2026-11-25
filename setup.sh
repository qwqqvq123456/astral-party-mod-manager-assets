#!/usr/bin/env bash
# =============================================================================
# APMM 资产仓库一键初始化
#
#   bash setup.sh <github-owner> [repo-name]
#
# 做的事：
#   1. 检查 git / gh / python3 与登录状态
#   2. 把 template/ 里的 Actions 配置铺到仓库根
#   3. 初始化 git 仓库并指向 GitHub
#   4. 创建远程仓库（已存在则跳过）
#   5. 打开 Actions 的写入权限
#   6. 执行首次同步：拉上游 → 提交清单 → 推送 → 发布全部 Release
#
# 可重复执行，不会重复建仓或重复上传附件。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OWNER="${1:-}"
REPO="${2:-astral-party-mod-manager-assets}"

info()  { printf '\033[36m[信息]\033[0m %s\n' "$*"; }
ok()    { printf '\033[32m[完成]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[注意]\033[0m %s\n' "$*"; }
fail()  { printf '\033[31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 依赖检查 ---
for cmd in git gh python3; do
  command -v "$cmd" >/dev/null 2>&1 || fail "缺少命令：$cmd"
done
info "git $(git --version | awk '{print $3}') / gh $(gh --version | head -1 | awk '{print $3}') / $(python3 --version)"

if ! gh auth status >/dev/null 2>&1; then
  fail "gh 未登录，请先执行：gh auth login（需要 repo + workflow 权限）"
fi

if [ -z "$OWNER" ]; then
  OWNER="$(gh api user --jq .login)"
  info "未指定账号，使用当前登录账号：$OWNER"
fi

SLUG="$OWNER/$REPO"
info "目标仓库：$SLUG"

# ------------------------------------------------------------- 铺设骨架 ---
if [ -d "$ROOT/template" ]; then
  cp -R "$ROOT/template/." "$ROOT/"
  info "已铺设 Actions 配置与 .gitattributes"
else
  info "template/ 不存在，沿用仓库内既有配置"
fi

cd "$ROOT"

# ------------------------------------------------------------- git 初始化 ---
if [ ! -d .git ]; then
  git init -q -b main
  info "已初始化 git 仓库（main 分支）"
fi

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "https://github.com/$SLUG.git"
else
  git remote add origin "https://github.com/$SLUG.git"
fi

# ------------------------------------------------------------- 创建远程仓库 ---
if gh repo view "$SLUG" >/dev/null 2>&1; then
  info "远程仓库已存在，跳过创建"
else
  gh repo create "$SLUG" --public \
    --description "APMM 发布资产镜像：清单进仓库，安装包与资源包自动发布到 Releases" \
    --disable-wiki >/dev/null
  ok "已创建公开仓库 https://github.com/$SLUG"
fi

# --------------------------------------------------------- Actions 权限 ---
if gh api -X PUT "repos/$SLUG/actions/permissions/workflow" \
     -f default_workflow_permissions=write >/dev/null 2>&1; then
  info "Actions 默认权限已设为可写（GITHUB_TOKEN 能推送和建 Release）"
else
  warn "无法自动设置 Actions 权限，请手动确认："
  warn "  Settings → Actions → General → Workflow permissions → Read and write"
fi

# --------------------------------------------------------------- 首次同步 ---
info "开始首次同步（拉取上游约 330MB，请稍候）"
python3 scripts/sync.py --mode lite --push

echo
ok "全部完成：https://github.com/$SLUG"
echo "  仓库首页  https://github.com/$SLUG"
echo "  发布页面  https://github.com/$SLUG/releases"
echo "  自动同步  https://github.com/$SLUG/actions"
