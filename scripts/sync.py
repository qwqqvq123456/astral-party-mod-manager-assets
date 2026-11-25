#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APMM 资产仓库同步工具

把 cnb.cool 上的 APMM 发布资产同步到自己的 GitHub 仓库。

两种模式：

  lite（默认）
      git 里只保留清单、README、截图、工坊目录等轻量文件（几十 KB），
      安装包 / 预览包 / 工坊资源包发布为 GitHub Release 附件。
      仓库永久保持很小，克隆秒开，下载走 GitHub Release CDN。

  full
      完整复制上游 git 历史（含全部二进制）到 upstream-full 分支，
      不推送 tag，因此不会破坏 lite 模式创建的 Release 标签。

用法示例：

  python3 scripts/sync.py --mode lite --dry-run     # 只看计划，不写任何东西
  python3 scripts/sync.py --mode lite --push        # 同步 + 提交 + 推送 + 发布 Release
  python3 scripts/sync.py --mode full --push        # 完整镜像到 upstream-full 分支

环境变量：
  GITHUB_REPOSITORY   owner/repo，CI 中自动注入；本地从 origin 推断
  GH_TOKEN            gh CLI 凭据，发布 Release 必需
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_UPSTREAM = os.environ.get(
    "UPSTREAM_URL",
    "https://cnb.cool/astral-weird-egg/astral-party-mod-manager-assets.git",
)

# 走 Release 的二进制扩展名：这些文件不进 git
BINARY_EXT = {
    ".zip", ".msi", ".msix", ".exe", ".dmg", ".pkg", ".iso",
    ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst",
}
# 校验文件跟随安装包一起走 Release
CHECKSUM_EXT = {".sha256", ".sha1", ".md5"}

# 即便扩展名命中也要强制纳入 git 的文件（工坊目录签名是客户端校验入口）
FORCE_INCLUDE = {"workshop/manifest.sig"}

# 这些路径属于我们的工具链，同步时不许删除
PROTECTED_PREFIXES = (".github/", "scripts/", "template/", ".git/")
PROTECTED_FILES = {".gitignore", ".gitattributes", "setup.sh", "MIRROR.md"}

CNB_RAW_RE = re.compile(r"https?://cnb\.cool/[^\s\"'<>]+?/-/git/raw/(?:main|master)/([^\s\"'<>]+)")
MD_LINK_RE = re.compile(r"\]\((releases/[^)\s]+|bundles/[^)\s]+)\)")
# 上游旧版清单指向作者的独立预览仓库，改写到本仓库以做到完全自托管
PREVIEW_REPO_RE = re.compile(
    r"https?://github\.com/[^/\s\"']+/AstralPartyModManager-Previews/releases/download/"
    r"([^/\s\"']+)/([^\s\"'<>)]+)"
)
SEMVER_DIR_RE = re.compile(r"^\d+\.\d+\.\d+$")


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def run(cmd: list[str], cwd: Path | str | None = None, check: bool = True,
        quiet: bool = False) -> subprocess.CompletedProcess:
    """执行命令；失败时打印完整输出并终止。"""
    printable = " ".join(str(c) for c in cmd)
    if not quiet:
        print(f"  $ {printable}", flush=True)
    proc = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        sys.stderr.write((proc.stdout or "") + (proc.stderr or ""))
        raise SystemExit(f"命令失败（退出码 {proc.returncode}）：{printable}")
    return proc


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def resolve_repo(work: Path) -> tuple[str, str]:
    """确定目标 GitHub 仓库 owner/repo。"""
    env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in env:
        owner, repo = env.split("/", 1)
        return owner, repo
    proc = run(["git", "remote", "get-url", "origin"], cwd=work, check=False, quiet=True)
    url = (proc.stdout or "").strip()
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if match:
        return match.group(1), match.group(2)
    raise SystemExit(
        "无法确定目标仓库：请先 git remote add origin https://github.com/<owner>/<repo>.git\n"
        "或设置环境变量 GITHUB_REPOSITORY=<owner>/<repo>"
    )


def should_track(rel: str) -> bool:
    """判断上游的某个相对路径是否应该进入 git（lite 模式）。"""
    if rel.startswith(".git/"):
        return False
    if rel in FORCE_INCLUDE:
        return True
    suffix = Path(rel).suffix.lower()
    if suffix in BINARY_EXT or suffix in CHECKSUM_EXT:
        return False
    return True


def is_protected(rel: str) -> bool:
    return rel in PROTECTED_FILES or rel.startswith(PROTECTED_PREFIXES)


# --------------------------------------------------------------------------- #
# 上游拉取
# --------------------------------------------------------------------------- #
def clone_upstream(upstream: str) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="apmm-upstream-"))
    print(f"拉取上游 {upstream}")
    run(["git", "clone", "--depth", "1", "--quiet", upstream, str(tmp / "repo")])
    return tmp / "repo"


def get_upstream(args) -> tuple[Path, bool]:
    """返回（上游目录, 是否临时目录）。--upstream-dir 可复用本地已有副本。"""
    if args.upstream_dir:
        path = Path(args.upstream_dir).resolve()
        if not (path / "update-manifest.json").is_file():
            raise SystemExit(f"--upstream-dir 不是有效的上游副本：{path}")
        print(f"复用本地上游副本 {path}")
        return path, False
    return clone_upstream(args.upstream), True


def read_current_version(up: Path) -> str:
    """从 update-manifest.json 读取当前版本号。"""
    manifest = up / "update-manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            version = str(data.get("version", "")).strip()
            if version:
                return version
        except (json.JSONDecodeError, OSError):
            pass
    return "0.0.0"


def read_current_notes(up: Path) -> str:
    manifest = up / "update-manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return str(data.get("notes", "") or "").strip()
        except (json.JSONDecodeError, OSError):
            pass
    return ""


# --------------------------------------------------------------------------- #
# 链接改写
# --------------------------------------------------------------------------- #
def map_path_to_url(path: str, gh: str) -> str | None:
    """把仓库内相对路径映射到 GitHub Release 地址。"""
    path = path.strip()
    if not path:
        return None

    match = re.match(r"^releases/(\d+\.\d+\.\d+)/(.*)$", path)
    if match:
        version, rest = match.group(1), match.group(2)
        if not rest or rest.endswith("/"):
            return f"{gh}/releases/tag/v{version}"
        return f"{gh}/releases/download/v{version}/{rest.rsplit('/', 1)[-1]}"

    match = re.match(r"^releases/([^/]+)$", path)
    if match and Path(match.group(1)).suffix.lower() in (BINARY_EXT | CHECKSUM_EXT):
        return f"{gh}/releases/download/legacy/{match.group(1)}"

    if path.startswith("bundles/"):
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if path.endswith("/"):
            tag = "preview-v4" if name == "preview-v4" else "preview-legacy"
            return f"{gh}/releases/tag/{tag}"
        # 新版 APMM-* 预览包和根目录的 v4 兼容包统一挂在 preview-v4 标签下
        if name.startswith("APMM-") or "preview-v4" in name:
            return f"{gh}/releases/download/preview-v4/{name}"
        return f"{gh}/releases/download/preview-legacy/{name}"

    return None


def rewrite_text(text: str, owner: str, repo: str) -> str:
    """把 cnb.cool 裸链接和仓库内相对链接改写成 GitHub Release 地址。"""
    gh = f"https://github.com/{owner}/{repo}"

    def cnb_repl(match: re.Match) -> str:
        tail = ""
        rest = match.group(1)
        while rest.endswith(")"):
            rest, tail = rest[:-1], tail + ")"
        mapped = map_path_to_url(rest, gh)
        return (mapped + tail) if mapped else match.group(0)

    text = CNB_RAW_RE.sub(cnb_repl, text)

    def preview_repl(match: re.Match) -> str:
        tag = "preview-v4" if match.group(1) == "preview-v4" else "preview-legacy"
        return f"{gh}/releases/download/{tag}/{match.group(2)}"

    text = PREVIEW_REPO_RE.sub(preview_repl, text)

    def md_repl(match: re.Match) -> str:
        mapped = map_path_to_url(match.group(1), gh)
        return f"]({mapped})" if mapped else match.group(0)

    return MD_LINK_RE.sub(md_repl, text)


# --------------------------------------------------------------------------- #
# lite 模式：同步文件树
# --------------------------------------------------------------------------- #
def sync_tree(up: Path, work: Path) -> tuple[int, int]:
    """把上游轻量文件同步进工作树，返回（写入数, 删除数）。"""
    sources = [p for p in up.rglob("*") if p.is_file()
               and not p.relative_to(up).as_posix().startswith(".git/")
               and should_track(p.relative_to(up).as_posix())]

    wanted: set[str] = set()
    for src in sources:
        rel = src.relative_to(up).as_posix()
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        wanted.add(rel)

    tracked_raw = run(["git", "ls-files", "-z"], cwd=work, quiet=True).stdout or ""
    removed = 0
    for rel in tracked_raw.split("\0"):
        if not rel or rel in wanted or is_protected(rel):
            continue
        target = work / rel
        if target.is_file():
            target.unlink()
            removed += 1
        parent = target.parent
        while parent != work and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    return len(wanted), removed


def rewrite_in_place(work: Path, owner: str, repo: str) -> list[str]:
    """改写工作树里 JSON / Markdown 的内链，返回被改动的文件。"""
    changed: list[str] = []
    for pattern in ("*.json", "*.md", "**/*.json", "**/*.md"):
        for path in sorted(work.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(work).as_posix()
            if is_protected(rel) or ".git/" in rel:
                continue
            try:
                original = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            updated = rewrite_text(original, owner, repo)
            if updated != original:
                path.write_text(updated, encoding="utf-8")
                changed.append(rel)
    return sorted(set(changed))


MIRROR_BANNER = """> **镜像说明**
> 本仓库由自动化任务每 6 小时同步自上游 `cnb.cool/astral-weird-egg/astral-party-mod-manager-assets`。
> 仓库内只保存清单与说明文件，安装包 / 预览包 / 工坊资源包以附件形式存放在
> [Releases]({gh}/releases) 页面。需要完整历史镜像请查看 `upstream-full` 分支。
> 同步机制、手动操作与常见问题见 [MIRROR.md]({gh}/blob/main/MIRROR.md)。

"""


def inject_banner(work: Path, owner: str, repo: str) -> None:
    readme = work / "README.md"
    if not readme.is_file():
        return
    gh = f"https://github.com/{owner}/{repo}"
    banner = MIRROR_BANNER.format(gh=gh)
    text = readme.read_text(encoding="utf-8")

    if text.startswith("> **镜像说明**"):
        # 已有时替换旧的 banner（覆盖到第一行正文之前），便于后续调整文案
        lines = text.splitlines(keepends=True)
        index = 0
        while index < len(lines) and (lines[index].startswith(">") or not lines[index].strip()):
            index += 1
        text = banner + "".join(lines[index:])
    elif "**镜像说明**" in text:
        return
    else:
        text = banner + text

    readme.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# lite 模式：构建发布计划
# --------------------------------------------------------------------------- #
def sort_key(item: dict):
    """让 Release 页面按「旧版兼容 → 预览 → 最新版本」顺序排列。"""
    tag = item["tag"]
    fixed = {"legacy": 0, "preview-legacy": 1, "preview-v4": 2}
    if tag in fixed:
        return (fixed[tag], ())
    match = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", tag)
    if match:
        return (3, tuple(int(x) for x in match.groups()))
    return (4, ())


def build_plan(up: Path, current_version: str, current_notes: str) -> list[dict]:
    """扫描上游，生成 Release 发布计划（按创建顺序排列，最新版本最后）。"""
    plan: list[dict] = []
    releases_dir = up / "releases"

    # 旧版平铺包 → legacy
    if releases_dir.is_dir():
        legacy = sorted(
            f for f in releases_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (BINARY_EXT | CHECKSUM_EXT)
        )
        if legacy:
            plan.append({
                "tag": "legacy",
                "title": "旧版兼容包（legacy 直链）",
                "notes": "上游 `releases/` 根目录下的历史直链安装包，仅为兼容旧下载地址保留。\n新增版本请使用对应的版本化 Release。",
                "files": legacy,
            })

    # 版本化发行目录 → v<version>
    if releases_dir.is_dir():
        version_dirs = sorted(
            (d for d in releases_dir.iterdir() if d.is_dir() and SEMVER_DIR_RE.match(d.name)),
            key=lambda d: tuple(int(x) for x in d.name.split(".")),
        )
        for directory in version_dirs:
            files = sorted(f for f in directory.rglob("*") if f.is_file())
            if not files:
                continue
            notes = current_notes if directory.name == current_version else ""
            plan.append({
                "tag": f"v{directory.name}",
                "title": f"APMM {directory.name}",
                "notes": notes or f"APMM {directory.name} 发行包（镜像自上游）。",
                "files": files,
            })

    # 预览包 → preview-v4 / preview-legacy
    # 根目录的 v4 兼容包也要并入 preview-v4，否则旧清单里的链接会 404
    bundles_dir = up / "bundles"
    if bundles_dir.is_dir():
        v4_files: list[Path] = []
        pv4 = bundles_dir / "preview-v4"
        if pv4.is_dir():
            v4_files += [f for f in pv4.rglob("*") if f.is_file()]

        legacy_files: list[Path] = []
        for entry in sorted(bundles_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in (BINARY_EXT | CHECKSUM_EXT):
                continue
            if "preview-v4" in entry.name:
                v4_files.append(entry)
            else:
                legacy_files.append(entry)

        if v4_files:
            plan.append({
                "tag": "preview-v4",
                "title": "预览包 preview-v4",
                "notes": "完整原图预览包（quality 50 有损 WebP，保留尺寸与透明度），"
                         "含新版 8 个分包与旧版 3 个兼容包。\n"
                         "清单见仓库根目录 `preview-manifest-v4.json` 与 `preview-manifest.json`。",
                "files": sorted(v4_files),
            })
        if legacy_files:
            plan.append({
                "tag": "preview-legacy",
                "title": "预览包（旧版兼容）",
                "notes": "上游 `bundles/` 根目录保留的 v3 预览兼容包，仅为兼容旧客户端清单。",
                "files": sorted(legacy_files),
            })

    plan.sort(key=sort_key)
    return plan


# --------------------------------------------------------------------------- #
# Release 发布
# --------------------------------------------------------------------------- #
def list_assets(tag: str, slug: str) -> set[str] | None:
    proc = run(["gh", "release", "view", tag, "--repo", slug, "--json", "assets"],
               check=False, quiet=True)
    if proc.returncode != 0:
        return None
    try:
        return {item["name"] for item in json.loads(proc.stdout or "{}").get("assets", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def check_links(work: Path, plan: list[dict], owner: str, repo: str) -> list[str]:
    """校验清单里引用的 Release 附件是否都会真实发布，返回缺失项。"""
    available = {(item["tag"], Path(f).name) for item in plan for f in item["files"]}
    prefix = f"https://github.com/{owner}/{repo}/releases/download/"
    missing: list[str] = []
    for path in sorted(work.glob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in re.finditer(re.escape(prefix) + r"([^/\"]+)/([^\"]+)", text):
            ref = (match.group(1), match.group(2))
            if ref not in available:
                missing.append(f"{path.name} → {ref[0]}/{ref[1]}")
    return missing


VERSION_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def limit_versions(plan: list[dict], keep: int) -> list[dict]:
    """只保留最新 keep 个版本类 Release，其余从计划中剔除（不再上传）。
    keep <= 0 表示全部保留。预览包、旧版兼容包不受影响。"""
    if keep <= 0:
        return plan
    versions = [item for item in plan if VERSION_TAG_RE.match(item["tag"])]
    if len(versions) <= keep:
        return plan
    versions.sort(
        key=lambda item: tuple(int(x) for x in VERSION_TAG_RE.match(item["tag"]).groups()),
        reverse=True,
    )
    keep_tags = {item["tag"] for item in versions[:keep]}
    return [item for item in plan
            if not VERSION_TAG_RE.match(item["tag"]) or item["tag"] in keep_tags]


def prune_releases(slug: str, keep: int, dry_run: bool) -> int:
    """删除超出保留数量的历史版本 Release，连 tag 一并清理。"""
    if keep <= 0:
        return 0
    proc = run(["gh", "release", "list", "--repo", slug, "--limit", "200",
                "--json", "tagName"], check=False, quiet=True)
    if proc.returncode != 0:
        print("  无法列出已有 Release，跳过清理")
        return 0
    try:
        tags = [row["tagName"] for row in json.loads(proc.stdout or "[]")]
    except (json.JSONDecodeError, KeyError, TypeError):
        return 0

    versions = [t for t in tags if VERSION_TAG_RE.match(t)]
    if len(versions) <= keep:
        return 0
    versions.sort(key=lambda t: tuple(int(x) for x in VERSION_TAG_RE.match(t).groups()),
                  reverse=True)

    stale = versions[keep:]
    for tag in stale:
        if dry_run:
            print(f"  [计划] 删除历史 Release {tag}（连 tag）")
            continue
        run(["gh", "release", "delete", tag, "--repo", slug, "--yes", "--cleanup-tag"],
            quiet=True)
        print(f"  - 已删除历史 Release {tag}")
    return len(stale)


def publish(plan: list[dict], slug: str, force: bool, dry_run: bool) -> int:
    """创建 / 补全 Release，返回上传的文件数。"""
    uploaded = 0
    for item in plan:
        tag, files = item["tag"], [str(f) for f in item["files"]]
        total = sum(Path(f).stat().st_size for f in files)
        existing = list_assets(tag, slug)

        if dry_run:
            state = "已存在，仅补缺失" if existing is not None else "将新建"
            print(f"  [计划] {tag:<14} {len(files):>2} 个附件 {human(total):>9}  {state}")
            for f in files:
                print(f"          - {Path(f).name}")
            uploaded += len(files)
            continue

        if existing is None:
            print(f"创建 Release {tag}（{len(files)} 个附件，{human(total)}）")
            with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(item["notes"])
                notes_path = handle.name
            try:
                run(["gh", "release", "create", tag, "--repo", slug,
                     "--title", item["title"], "--notes-file", notes_path, *files], quiet=True)
                uploaded += len(files)
                print(f"  ✓ {tag} 发布完成")
            finally:
                Path(notes_path).unlink(missing_ok=True)
        else:
            missing = files if force else [f for f in files if Path(f).name not in existing]
            if not missing:
                print(f"  = {tag} 已是最新，跳过")
                continue
            print(f"补传 {tag} 缺失的 {len(missing)} 个附件")
            run(["gh", "release", "upload", tag, "--repo", slug, "--clobber", *missing], quiet=True)
            uploaded += len(missing)
            print(f"  ✓ {tag} 已补全")
    return uploaded


# --------------------------------------------------------------------------- #
# git 提交与推送
# --------------------------------------------------------------------------- #
def ensure_identity(work: Path) -> None:
    """CI 与全新仓库里没有 git 身份，缺省时补一个，否则 commit 会失败。"""
    email = run(["git", "config", "user.email"], cwd=work, check=False, quiet=True).stdout.strip()
    if not email:
        name = os.environ.get("GITHUB_ACTOR", "APMM Sync Bot")
        run(["git", "config", "user.email",
             f"{name}@users.noreply.github.com"], cwd=work, quiet=True)
        run(["git", "config", "user.name", name], cwd=work, quiet=True)


def current_branch(work: Path) -> str:
    """确定分支名。Actions 里 schedule 触发时是 detached HEAD，
    必须回退到 GITHUB_REF_NAME，否则会 push 到 refs/heads/HEAD。"""
    for candidate in (os.environ.get("GITHUB_REF_NAME", "").strip(),
                      os.environ.get("GITHUB_DEFAULT_BRANCH", "").strip()):
        if candidate and candidate != "HEAD":
            return candidate
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                 cwd=work, check=False, quiet=True).stdout.strip()
    return branch if branch and branch != "HEAD" else "main"


def commit_and_push(work: Path, message: str, push: bool) -> bool:
    ensure_identity(work)
    run(["git", "add", "-A"], cwd=work, quiet=True)
    status = run(["git", "status", "--porcelain"], cwd=work, quiet=True).stdout or ""
    if not status.strip():
        print("工作树无变化，跳过提交")
        return False

    run(["git", "commit", "-m", message], cwd=work, quiet=True)
    print(f"已提交：{message}")
    if push:
        branch = current_branch(work)
        run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=work)
        print(f"已推送到 origin/{branch}")
    return True


# --------------------------------------------------------------------------- #
# 模式实现
# --------------------------------------------------------------------------- #
def run_lite(args, owner: str, repo: str, slug: str) -> None:
    up, temp = get_upstream(args)
    try:
        version = read_current_version(up)
        notes = read_current_notes(up)
        print(f"上游当前版本：{version}")

        if args.dry_run:
            plan = limit_versions(build_plan(up, version, notes), args.keep_releases)
            tracked = [p for p in up.rglob("*") if p.is_file()
                       and should_track(p.relative_to(up).as_posix())]
            size = sum(p.stat().st_size for p in tracked)
            print(f"\n将纳入 git 的轻量文件：{len(tracked)} 个，合计 {human(size)}")
            for p in sorted(tracked)[:20]:
                print(f"  + {p.relative_to(up).as_posix()}")
            if len(tracked) > 20:
                print(f"  ... 其余 {len(tracked) - 20} 个")
            print(f"\nRelease 计划（共 {len(plan)} 个）：")
            publish(plan, slug, args.force, dry_run=True)
            if args.keep_releases > 0:
                print("\n历史版本清理：")
                prune_releases(slug, args.keep_releases, dry_run=True)
            print("\n以上为 --dry-run 结果，未写入任何文件。")
            return

        if not args.publish_only:
            written, removed = sync_tree(up, args.work)
            print(f"文件树同步完成：写入 {written} 个，移除 {removed} 个")

            if not args.no_rewrite:
                changed = rewrite_in_place(args.work, owner, repo)
                if changed:
                    print(f"已改写内链的文件：{', '.join(changed)}")
                inject_banner(args.work, owner, repo)

            message = f"sync: 上游 APMM {version}"
            commit_and_push(args.work, message, args.push)
        else:
            print("仅发布模式：跳过文件树同步与提交")

        if not args.no_publish:
            plan = limit_versions(build_plan(up, version, notes), args.keep_releases)
            missing = check_links(args.work, plan, owner, repo)
            if missing:
                print("\n警告：以下清单引用没有对应的附件，发布后会 404：")
                for ref in missing:
                    print(f"  - {ref}")
            print(f"\n发布 Release（共 {len(plan)} 个）")
            count = publish(plan, slug, args.force, dry_run=False)
            print(f"Release 处理完毕，涉及 {count} 个附件")

            if args.keep_releases > 0:
                removed = prune_releases(slug, args.keep_releases, dry_run=False)
                if removed:
                    print(f"已清理 {removed} 个历史版本 Release")
    finally:
        if temp:
            shutil.rmtree(up.parent, ignore_errors=True)


def run_full(args, owner: str, repo: str, slug: str) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="apmm-mirror-"))
    bare = tmp / "mirror.git"
    try:
        print(f"完整克隆上游 {args.upstream}")
        run(["git", "clone", "--mirror", "--quiet", args.upstream, str(bare)])

        branch = args.full_branch
        if args.dry_run:
            head = run(["git", "rev-parse", "HEAD"], cwd=bare, quiet=True).stdout.strip()
            log = run(["git", "log", "-1", "--pretty=%h %ad %s", "--date=short"],
                      cwd=bare, quiet=True).stdout.strip()
            print(f"  [计划] 将把 main 全量推送到 {slug} 的 {branch} 分支")
            print(f"          HEAD {head[:7]} · {log}")
            print("以上为 --dry-run 结果，未推送任何内容。")
            return

        url = f"https://x-access-token:{args.token}@github.com/{slug}.git" if args.token \
            else f"https://github.com/{slug}.git"
        run(["git", "push", "--force", url, f"refs/heads/main:refs/heads/{branch}"], cwd=bare)
        print(f"完整镜像已推送到 {slug} 的 {branch} 分支")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="APMM 资产仓库同步工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode", choices=("lite", "full"), default="lite",
                        help="lite=清单进 git + 二进制走 Release（默认）；full=完整镜像到独立分支")
    parser.add_argument("--work", default=".", help="本地仓库根目录，默认当前目录")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="上游仓库地址")
    parser.add_argument("--full-branch", default="upstream-full",
                        help="full 模式推送的目标分支名")
    parser.add_argument("--push", action="store_true", help="同步后提交并推送")
    parser.add_argument("--no-publish", action="store_true", help="只同步文件，不创建 Release")
    parser.add_argument("--publish-only", action="store_true",
                        help="跳过文件树同步与提交，只创建 / 补全 Release")
    parser.add_argument("--upstream-dir", default="",
                        help="复用本地已有的上游副本，免去重新克隆")
    parser.add_argument("--no-rewrite", action="store_true",
                        help="保持清单原样，不改写 cnb.cool 链接")
    parser.add_argument("--keep-releases", type=int, default=1, metavar="N",
                        help="版本类 Release 只保留最新 N 个，旧的连 tag 一起删除；0=全部保留（默认 1）")
    parser.add_argument("--force", action="store_true", help="强制重传已存在的 Release 附件")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件不推送")
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN", ""),
                        help="full 模式推送用的 token，默认读 GH_TOKEN")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    if not work.is_dir():
        raise SystemExit(f"工作目录不存在：{work}")
    args.work = work

    owner, repo = resolve_repo(work)
    slug = f"{owner}/{repo}"
    print(f"目标仓库：{slug}")
    print(f"运行模式：{args.mode}{'（dry-run）' if args.dry_run else ''}\n")

    if args.mode == "full":
        run_full(args, owner, repo, slug)
    else:
        run_lite(args, owner, repo, slug)


if __name__ == "__main__":
    main()
