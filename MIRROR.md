# APMM 资产镜像仓库

把 [上游 cnb.cool 仓库](https://cnb.cool/astral-weird-egg/astral-party-mod-manager-assets)
同步到自己的 GitHub，并让安装包自动发布成 GitHub Release。

上游是**纯发布产物仓库**（安装包、预览包、工坊资源），没有源码，因此这里的「构建」
指把 `releases/<版本>/` 的产物打包上传为 Release 附件，而不是编译。

---

## 仓库长什么样

| 位置 | 内容 | 体积 |
| --- | --- | --- |
| `main` 分支 | README、4 个清单 JSON、工坊目录与签名、截图、封面 | 约 1.7 MB |
| `Releases` | 所有安装包、预览包、旧版兼容包 | 约 330 MB |
| `upstream-full` 分支 | 需要时按需生成的完整镜像（含全部二进制与 52 个提交） | 约 328 MB |

`main` 只存清单的好处：克隆秒开、历史永远干净、不会被 GitHub 的体积限制找上门。
二进制放 Release 是 GitHub 官方推荐的分发方式，上游作者自己的预览包也是这么发的。

### Release 标签规则

| 标签 | 对应内容 | 是否保留历史 |
| --- | --- | --- |
| `v0.4.11` | `releases/<版本>/` 下的标准发行包（便携版 / 安装版 / 兼容包 + sha256） | **只保留最新一个** |
| `preview-v4` | `bundles/preview-v4/` 完整原图预览包 | 长期保留 |
| `preview-legacy` | `bundles/` 根目录的 v3 兼容预览包 | 长期保留 |
| `legacy` | `releases/` 根目录的历史直链安装包 | 长期保留 |

**版本只保留最新。** 上游放出 0.4.12 后，`v0.4.11` 的 Release 和 tag 会被自动删除，
页面上只留 `v0.4.12`。这样 Releases 页面始终只有一套可用安装包，不会被十几个旧版本
淹掉，也不会累积占用空间。

预览包和旧版兼容包不是「版本」，是三条并行的分发通道，所以不受清理影响。

想保留几个历史版本，用 `--keep-releases N` 调整（`0` 表示全部保留）。

---

## 自动同步怎么工作

`.github/workflows/sync-upstream.yml` 每 6 小时（UTC `0 */6 * * *`）执行一次：

1. 浅克隆上游，读取 `update-manifest.json` 里的 `version`
2. 把轻量文件同步进 `main`，删除上游已移除的旧文件
3. 把清单里的 `cnb.cool` 下载链接改写成 GitHub Release 地址
4. 有变化就提交并推送
5. 扫描 `releases/<版本>/`、`bundles/`，**只上传最新版本**的附件
6. 删除超出保留数量的历史版本 Release 与 tag

整个过程**幂等**：已存在的 Release 只补传缺失的附件，不会重复上传几百 MB。
上游没有新版本时，同步在几秒内结束，不产生空提交。

### 手动触发

Actions → 同步上游资产 → Run workflow：

- `mode = lite`：常规同步（默认）
- `mode = full`：完整镜像到 `upstream-full` 分支，包含全部二进制和历史，用于
  「我需要 `git clone` 拿到完整文件」的场景。因为完整克隆上游约 330 MB，单次运行
  需要几分钟。
- `keep = 1`：版本类 Release 保留最近几个，默认 1（只留最新），填 0 则全部保留
- `force = true`：强制重传所有附件（上游静默替换了同名文件时用）

---

## 本地操作

```bash
# 只看计划，不写文件、不推送、不发 Release
python3 scripts/sync.py --mode lite --dry-run

# 同步并推送 + 发布 Release（默认只保留最新版本）
python3 scripts/sync.py --mode lite --push

# 保留最近 3 个历史版本
python3 scripts/sync.py --mode lite --push --keep-releases 3

# 全部历史版本都保留（关闭清理）
python3 scripts/sync.py --mode lite --push --keep-releases 0

# 只同步文件，不发 Release
python3 scripts/sync.py --mode lite --push --no-publish

# 跳过文件同步，只创建 / 补全 Release（配合本地已有副本可省去重新克隆）
python3 scripts/sync.py --publish-only --upstream-dir ../上游副本

# 保持清单原样，不改写 cnb.cool 链接
python3 scripts/sync.py --mode lite --push --no-rewrite

# 完整镜像到 upstream-full 分支
python3 scripts/sync.py --mode full --push --token "$(gh auth token)"

# 换账号重建整个仓库
bash setup.sh <你的用户名> [仓库名]
```

依赖：`git`、`gh`（已 `gh auth login`，需要 `repo` + `workflow` 权限）、`python3`。

---

## 清单链接改写说明

上游 `update-manifest.json` 里 11 处下载地址指向 `cnb.cool` 的 raw 链接。同步时会
自动替换成对应的 GitHub Release 地址，例如：

```
https://cnb.cool/.../-/git/raw/main/releases/0.4.11/compatibility/APMM-...-legacy-bridge.zip
  ↓
https://github.com/<你>/<仓库>/releases/download/v0.4.11/APMM-...-legacy-bridge.zip
```

`sha256` 字段保持不变，客户端校验逻辑不受影响。这样客户端可以完全脱离 cnb.cool。
如果不希望改写，加 `--no-rewrite`，或把 workflow 里的 `--no-rewrite` 打开。

---

## 注意事项

**旧版本删除后不可恢复。** Release 和 tag 一起删掉，GitHub 无法找回附件。如果需要
能回滚的历史版本，把 `keep` 调大或设成 0。上游的旧安装包仍在 `cnb.cool` 上，随时可以
重新同步回来——把 `keep` 改大再手动触发一次即可。

**Actions 会被自动停用。** 仓库连续 60 天没有任何提交时，GitHub 会暂停定时任务。
发一封邮件提醒，去 Actions 页面点一下 Enable 即可恢复。上游理论上会持续发版，
正常情况下不会触发。

**改仓库名或换账号**，需要同步改两处：`git remote set-url origin <新地址>`，以及
`scripts/sync.py` 里的链接改写依赖 `origin`/`GITHUB_REPOSITORY` 推断，不用改代码。

**上游仓库若改为私有**，定时同步会失败，需要在 workflow 里配置访问凭据。

**Release 附件上限**是单文件 2 GB、单仓库不限总量，当前最大文件 60.8 MB，余量充足。

**仓库体积**：`main` 稳定在 2 MB 以内。`upstream-full` 分支会随上游每版增长几十 MB，
若长期不需要可删除该分支：`git push origin --delete upstream-full`。
