> **镜像说明**
> 本仓库由自动化任务每 6 小时同步自上游 `cnb.cool/astral-weird-egg/astral-party-mod-manager-assets`。
> 仓库内只保存清单与说明文件，安装包 / 预览包 / 工坊资源包以附件形式存放在
> [Releases](https://github.com/qwqqvq123456/astral-party-mod-manager-assets/releases) 页面。需要完整历史镜像请查看 `upstream-full` 分支。

# APMM 下载与资源

## 应用下载

| 适用场景 | 下载 |
| --- | --- |
| 便携版 | [APMM 0.4.11 便携版](https://github.com/qwqqvq123456/astral-party-mod-manager-assets/releases/download/v0.4.11/APMM-0.4.11-windows-x86_64-portable.zip)：解压后打开 `APMM-0.4.11` 文件夹运行 |
| 固定安装 | [APMM 0.4.11 安装版](https://github.com/qwqqvq123456/astral-party-mod-manager-assets/releases/download/v0.4.11/APMM-0.4.11-windows-x86_64.msi) |

旧版自动更新使用 [兼容包](https://github.com/qwqqvq123456/astral-party-mod-manager-assets/releases/tag/v0.4.11)，每个发行包附带 ".sha256" 校验文件。

## 原图预览

[完整 preview-v4 预览包](https://github.com/qwqqvq123456/astral-party-mod-manager-assets/releases/tag/preview-v4)由官方资源提取后以质量 50 编码为有损 WebP，保留尺寸和透明度，包含角色、卡面、怪物、芯片和鼠标。完整清单为 `preview-manifest-v4.json`；旧版通过 `preview-manifest.json` 获取固定资源集合的兼容包。PNG 原图不进入预览下载包。

预览更新独立于软件更新。8 个当前游戏目录已移除的历史项目保留旧预览，其他原图来自游戏 3.3.0 的本次提取。

## 目录

- `releases/<版本>/windows-x86_64/`：标准应用发行。
- `releases/<版本>/compatibility/`：旧程序升级兼容包。
- `bundles/preview-v4/`：完整原图预览包。
- `screenshots/<版本>/`：界面展示图，与原图预览包分别维护。
- `workshop/`：签名工坊目录和作品资源。

根目录四份 JSON 是客户端固定读取入口。`releases/`、`bundles/` 中既有的平铺文件只保留旧直链兼容，不再作为新标准包的发布位置。旧文件保持原摘要，避免旧清单或下载中断恢复失效。
