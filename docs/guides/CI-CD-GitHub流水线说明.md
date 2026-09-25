# CI/CD 流水线说明（GitHub 私有仓库 + 服务器主动拉取）

> 目标仓库：`https://github.com/wuxiaolei-star/service-hub.git`（私有）
> 目标实例：`${HUB_HOST}`（Ubuntu 24.04 / 2 核 3.4 G）
> 编写日期：2026-09-25

## 1. 分工：谁做什么

| 环节 | 执行位置 | 产物 |
| --- | --- | --- |
| 密钥扫描 | GitHub Actions | 阻断带凭证的提交 |
| 后端质量门（ruff / mypy / pytest） | GitHub Actions（ubuntu-latest） | 全绿才允许部署 |
| 前端质量门（eslint / tsc / vitest / build） | GitHub Actions | 同上 |
| 主镜像可构建校验 | GitHub Actions（buildx + GHA 缓存） | 确认 Dockerfile 没坏 |
| **构建与上线** | **服务器本地** | 镜像不跨网传输（1 GB 级） |
| 健康检查、自动回滚、走查 | 服务器本地 `deploy/ci/deploy-pipeline.sh` | 8 阶段流水线 |

服务器**主动拉取**而不是 GitHub 反向 SSH 进来：服务器不需要开放任何入站端口，也不必把 GitHub Actions 的出口 IP 段加进防火墙。代价是延迟约 1-3 分钟（轮询间隔）。

```
git push ──▶ GitHub Actions (质量门) ──▶ 分支前进
                                            │
                      服务器 systemd timer 每 3 分钟轮询
                                            ▼
                    git fetch → 与 .deployed-commit 比对
                                            ▼
              有新提交 → deploy-pipeline.sh <sha>（构建/备份/上线/回滚）
```

## 2. 首次接入（一次性）

### 2.1 本地

```bash
git remote set-url origin git@github.com:wuxiaolei-star/service-hub.git
git push -u origin main
```

推送后立即确认远端不含敏感文件：

```bash
git ls-remote --heads origin          # 连通性
git push --dry-run origin main        # 无待推内容即已同步
```

### 2.2 GitHub 仓库设置

1. **Deploy keys**（Settings → Deploy keys → Add deploy key，只读即可）：填服务器上 `setup-deploy-key.sh` 打印的公钥，见 2.3。
2. **Actions**：仓库创建后默认启用，无需额外配置；本仓库工作流不需要任何自定义 secret（只用内置的 `GITHUB_TOKEN`）。
3. **Branch protection**（建议）：Settings → Branches → `main` → 勾选 *Require a pull request before merging* 与 *Require status checks to pass*（选 `Backend gate`、`Web console gate`、`Secrets scan`）。

### 2.3 服务器（root）

```bash
# 1) 生成专用 deploy key 并把公钥加到 GitHub（脚本会打印出来）
bash /opt/service-hub/src/deploy/ci/setup-deploy-key.sh git@github.com:wuxiaolei-star/service-hub.git

# 公钥加好后验证
ssh -T git@github.com                 # 预期：Hi wuxiaolei-star/service-hub! ...
git -C /opt/service-hub/src fetch origin

# 2) 安装 3 分钟一次的轮询部署定时器
bash /opt/service-hub/src/deploy/ci/install-poller.sh --interval=3min --branch=main
```

若 `/opt/service-hub/src` 当前是解压出来的源码而非 git 检出，`setup-deploy-key.sh` 会自动 clone 到该目录；**先确认 `/opt/service-hub/compose.override.yaml`、`build.sh`、`data/` 在该目录之外**（它们在 `/opt/service-hub/` 下，不受影响）。

## 3. 日常使用

| 场景 | 操作 |
| --- | --- |
| 常规提交 | `git push origin main` → Actions 跑质量门 → 服务器 3 分钟内自动上线 |
| 立即上线（不等轮询） | 服务器执行 `bash /opt/service-hub/deploy/ci/pull-deploy.sh` |
| 只看有无待部署 | 服务器执行 `... pull-deploy.sh --check` |
| 跳过质量门强制部署 | `PIPELINE_ARGS=--skip-gate bash ... pull-deploy.sh --force` |
| 打版本 | `git tag v1.2.0 && git push origin v1.2.0` → Release 工作流发版 |
| 跟随 tag 而非分支 | `install-poller.sh --mode=tag`（改 `/etc/systemd/system/service-hub-deploy.service` 的 `DEPLOY_MODE=tag`） |
| 回滚 | 服务器执行 `bash /opt/service-hub/src/deploy/ci/deploy-pipeline.sh <上一个 sha>`；数据由流水线冷备份自动恢复 |
| 暂停自动部署 | `systemctl disable --now service-hub-deploy.timer` |

观察：

```bash
journalctl -u service-hub-deploy.service -f
tail -f /opt/service-hub/deploy/ci/releases.log   # 每次上线：commit / 耗时 / 结果
```

## 4. 敏感信息处理规范

原则：**明文只留在本机，仓库只放占位符，运行期靠环境变量或服务器本地文件注入。**

| 敏感项 | 本地位置（保留、不入库） | 是否入库 | 运行期来源 | 处理方式 |
| --- | --- | --- | --- | --- |
| admin 初始口令 | `.deploy-tools/*.py` 里的 `HUB_BOOTSTRAP_PASSWORD` 常量、`登录账号密码.txt` | ❌ `.gitignore` | 服务器首次启动写入 `data/bootstrap-admin.json`（用完即删） | ① 本地脚本改为只读环境变量，缺省直接报错、不回退明文；② 轮换一次线上口令；③ 文档统一写 `<ADMIN_PASSWORD>` |
| 服务器 SSH 私钥 | `.deploy-tools/id_ed25519`、`ssh_env.json` | ❌ `.gitignore` | paramiko 本機读取 | 保持忽略；服务器侧另用只读 deploy key，不复用这把 |
| 服务器 IP / 端口 | 本机文档 | ✅（私有仓库） | — | 私有仓库可接受；若日后转公开，先执行 4.1 的脱敏替换 |
| GitHub 凭证 | 本机 `~/.ssh/`、PAT | ❌ | — | 只存本机；Actions 用内置 `GITHUB_TOKEN`，不额外建 secret |
| TLS 自签证书私钥 | 服务器 `/opt/service-hub` | ❌ | 服务器本地文件 | 保持不入库 |
| runner / service token | — | ❌ | 运行时生成写库 | 已是自动生成，无需改动 |
| webhook secret | — | ❌ | 创建时生成写库 | 同上 |

### 4.1 文档脱敏（仅在仓库要转公开时执行）

```bash
grep -rl '39\.96\.194\.156' docs/ | xargs sed -i 's/39\.96\.194\.156/${HUB_HOST}/g'
```

### 4.2 提交前的自动门禁

`deploy/ci/check_secrets.py` 是流水线的第一个 job，本地也能跑：

```bash
python deploy/ci/check_secrets.py                      # 只让 error 阻断
python deploy/ci/check_secrets.py --include-docs       # 连 tests/docs 一起审
HUB_SECRET_LITERALS='Hub-2026-...' python deploy/ci/check_secrets.py
```

- **error**（阻断）：PEM 私钥头、GitHub/AWS/OpenAI 风格的 token、以及通过 `HUB_SECRET_LITERALS` 传入的已知明文。
- **warning**（不阻断）：疑似 `password=/token=` 赋值、公网 IP。`tests/`、`docs/`、`web/` 默认静音，避免测试用例里的假 token 刷屏。

把真实口令放进 `HUB_SECRET_LITERALS` 而不是写进脚本，就能在不泄露明文的前提下验证「这串值绝对没有进仓库」。

## 5. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| Actions 后端 job 报缺依赖 | 工作流按 `packages/*` 逐个 `pip install -e`；新增包名后要同步 `.github/workflows/ci.yml` |
| 服务器轮询日志一直 `already up to date` | 正常；只有 `.deployed-commit` 与远端 tip 不同才部署 |
| `another deployment holds lock` | 上一次部署仍在进行（`flock`），等它结束即可 |
| `ssh -T git@github.com` 报 permission denied | Deploy key 未添加或添加在错误的仓库；`~/.ssh/config` 未指向专用 key |
| 部署失败但没回滚 | 冷备份缺失才会不回滚；检查 `/opt/service-hub/data-cold-*.tar.gz` |
| Actions 里镜像构建超时 | 默认 6 小时限制，冷构建约 10 分钟；GHA 缓存命中后约 1 分钟 |
