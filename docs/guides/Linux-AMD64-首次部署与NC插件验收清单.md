# Linux AMD64 首次部署与 NC 插件验收清单

> **历史文档（旧拓扑，不再作为部署入口）**：本文描述 hub / hub-conda-runner /
> hub-docker-runner 三个独立 Compose 服务的部署方式，仅作历史参考。当前部署请使用
> [单容器部署与脚本插件使用](单容器部署与脚本插件使用.md)，其中 hubctl 与插件构建
> 流程仍然适用。

本文是一份可以直接执行的实施手册，面向以下明确场景：

- 你有一台可联网的 Linux AMD64 测试服务器；
- 你已经取得 Python Service Hub 源码；
- 你需要用 Docker Compose 部署 Service Hub；
- 你需要把示例 NC 转 Shapefile 插件分别构建为 Docker 和 conda-pack 两种运行时包；
- 你需要注册、启用并实际运行插件，最终下载并核验 Shapefile ZIP。

本文默认服务器操作系统为 Ubuntu 22.04/24.04，项目部署目录为 /srv/python-service-hub，持久化数据目录为 /srv/service-hub-data，对外端口为本机 127.0.0.1:8000。

> V1 暂不实现 Hub 自身认证。正式被其他系统调用时，应由外部 Nginx 提供 TLS、认证、访问控制和请求大小限制。Hub 端口不应直接暴露到公网。

---

## 1. 最终完成标准

只有下面所有项目均通过，才算首次实施完成：

- [ ] docker、docker compose、curl、jq、unzip 可用；
- [ ] Service Hub 三个容器均正常运行；
- [ ] 健康检查返回 HTTP 200；
- [ ] Hub 只监听服务器回环地址 127.0.0.1:8000；
- [ ] Docker Runner 是唯一挂载 Docker Socket 的 Hub 组件；
- [ ] 已生成 Docker 运行时插件包；
- [ ] 已生成 conda-pack 运行时插件包；
- [ ] 两个插件包的 SHA256 已保存；
- [ ] Docker 插件包注册、启用成功；
- [ ] conda-pack 插件包注册、启用成功；
- [ ] 示例 NC 文件上传成功；
- [ ] Docker 运行时 Job 成功并产出 ZIP；
- [ ] conda-pack 运行时 Job 成功并产出 ZIP；
- [ ] 两份 ZIP 通过结构和要素数量核验；
- [ ] 实施证据、日志和最终配置已归档。

实施过程的数据流如下：

~~~mermaid
flowchart LR
    A[源码] --> B[构建 Hub 镜像]
    A --> C[构建 Docker 插件包]
    A --> D[构建 conda-pack 插件包]
    B --> E[启动 Hub 与两个 Runner]
    C --> F[注册并启用 PluginBuild]
    D --> F
    G[示例 NC] --> H[上传为 File]
    H --> I[创建 Docker Runtime Job]
    H --> J[创建 conda-pack Runtime Job]
    F --> I
    F --> J
    I --> K[下载 Shapefile ZIP]
    J --> L[下载 Shapefile ZIP]
    K --> M[比较和验收]
    L --> M
~~~

---

## 2. 先理解实际部署结构

Docker Compose 会启动三个长期运行的容器：

| 服务 | 职责 | 是否暴露端口 | 是否挂载 Docker Socket |
|---|---|---:|---:|
| hub | REST API、插件/文件/任务元数据、调度 | 仅 127.0.0.1:8000 | 否 |
| hub-conda-runner | 解包并执行 conda-pack 插件环境 | 否 | 否 |
| hub-docker-runner | 请求 Docker Engine 启动插件任务容器 | 否 | 是 |

插件不是永久运行的 Web 服务：

- Docker 运行时：Docker Runner 按 Job 临时启动一个插件任务容器，任务结束后退出；
- conda-pack 运行时：Conda Runner 在自己的容器内解包目标平台环境，并按 Job 启动插件进程；
- Hub 镜像不内置插件依赖，也不负责编译插件依赖；
- 插件发布者必须提前为目标 Linux AMD64 平台构建完整运行包。

因此，Hub 服务镜像与插件构建包是两类独立产物。以后迁移到 Linux ARM64 时，Hub 使用 ARM64 镜像，插件也必须重新在 ARM64 环境构建。

---

## 3. 服务器和文件准备

### 3.1 建议硬件

首次构建和双运行时测试建议至少：

- 4 核 CPU；
- 16 GB 内存；
- 80 GB 可用磁盘；
- 能访问 Docker Hub、GHCR、GitHub 和 conda-forge。

若服务器无法联网，应先在一台同架构、可联网的 Linux AMD64 构建机上完成镜像和插件包构建，再按第 14 节离线导入。

### 3.2 本文路径约定

在服务器上设置本次操作变量：

~~~bash
export HUB_SRC=/srv/python-service-hub
export HUB_DATA=/srv/service-hub-data
export HUB_EVIDENCE=/srv/python-service-hub/evidence
export HUB_SAMPLE=/srv/python-service-hub/fixtures/sample.nc
~~~

后续每次重新登录，都需要重新执行这四行，或者直接使用文中的绝对路径。

### 3.3 源文件清单

上传到服务器前，应至少拥有：

- 完整项目源码；
- 示例文件 20260828213102_生成hdf5结果详情信息.nc；
- 足够的服务器 sudo 权限；
- 能够使用 Docker 的可信发布账号。

---

## 4. 安装服务器基础软件

### 4.1 安装 Docker Engine 和 Compose 插件

Docker 应按 [Docker Engine 官方 Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/) 配置仓库并安装：

~~~bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl jq unzip git openssl

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
~~~

验证：

~~~bash
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
~~~

### 4.2 允许可信发布账号使用 Docker

插件构建脚本会直接调用 docker 命令。把当前可信账号加入 docker 组：

~~~bash
sudo usermod -aG docker "$USER"
~~~

然后退出 SSH 并重新登录，再验证：

~~~bash
docker version
docker run --rm hello-world
~~~

> docker 组接近 root 权限，只允许可信运维或发布账号加入。不要通过 chmod 666 /var/run/docker.sock 规避权限问题。

---

## 5. 将源码和示例 NC 上传到服务器

### 5.1 在 Windows 源码目录导出 main 分支

在 Windows PowerShell 中进入项目目录，先确认 main 分支和工作区：

~~~powershell
git branch --show-current
git status --short
git log -1 --oneline
git archive --format=tar.gz --output python-service-hub-main.tar.gz main
~~~

git branch --show-current 应输出 main。git status --short 应无输出；若有输出，先确认未提交内容是否属于本次交付。

### 5.2 从 Windows 上传

将 SERVER_USER 和 SERVER_IP 替换为实际值：

~~~powershell
scp .\python-service-hub-main.tar.gz SERVER_USER@SERVER_IP:/tmp/
scp "D:\SuperMap\task\会商临时任务-20260829\20260828213102_生成hdf5结果详情信息.nc" SERVER_USER@SERVER_IP:/tmp/sample.nc
~~~

### 5.3 在 Linux 解压并创建目录

~~~bash
sudo mkdir -p /srv/python-service-hub
sudo tar -xzf /tmp/python-service-hub-main.tar.gz \
  -C /srv/python-service-hub
sudo chown -R "$USER":"$USER" /srv/python-service-hub

mkdir -p /srv/python-service-hub/fixtures
mkdir -p /srv/python-service-hub/evidence
mv /tmp/sample.nc /srv/python-service-hub/fixtures/sample.nc

cd /srv/python-service-hub
test -f compose.yaml
test -f config/hub.yaml.example
test -f packages/nc-to-shp-plugin/scripts/package_build.py
ls -lh fixtures/sample.nc
~~~

若服务器直接使用 Git 拉取代码，可用 git clone 替代本节，但必须确认检出的提交就是交付版本。

---

## 6. 配置持久化目录和密钥

### 6.1 创建宿主机数据目录

~~~bash
sudo mkdir -p /srv/service-hub-data
sudo chmod 0750 /srv/service-hub-data
~~~

HUB_DATA_DIR 必须使用宿主机绝对路径。Docker Runner 会把任务目录传给宿主机 Docker Engine，不能使用相对路径。

### 6.2 创建 Hub 配置

~~~bash
cd /srv/python-service-hub
cp config/hub.yaml.example config/hub.yaml
sudo chown root:65532 config/hub.yaml
sudo chmod 0640 config/hub.yaml
~~~

检查配置中的容器内路径：

~~~bash
grep -nE 'root:|database:' config/hub.yaml
~~~

配置应继续使用容器内的 /data 路径，不要把 config/hub.yaml 中的 root 改成 /srv/service-hub-data。宿主机路径由 Compose 挂载完成。

### 6.3 创建 Runner 内部认证令牌

~~~bash
cd /srv/python-service-hub
umask 077
printf 'HUB_RUNNER_TOKEN=%s\nHUB_DATA_DIR=/srv/service-hub-data\n' \
  "$(openssl rand -hex 32)" > .env
sudo chown root:root .env
sudo chmod 0600 .env
~~~

确认变量已存在，但不要把令牌打印到日志：

~~~bash
sudo grep -q '^HUB_RUNNER_TOKEN=.\+' .env
sudo grep -q '^HUB_DATA_DIR=/srv/service-hub-data$' .env
~~~

---

## 7. 构建并启动 Service Hub

### 7.1 构建三项服务镜像

~~~bash
cd /srv/python-service-hub
mkdir -p evidence
sudo docker compose build 2>&1 | tee evidence/01-compose-build.log
~~~

构建成功后检查镜像：

~~~bash
sudo docker compose images
~~~

### 7.2 启动

~~~bash
sudo docker compose up -d
sudo docker compose ps
~~~

预期 hub、hub-conda-runner、hub-docker-runner 均处于 Up 状态；应用是否健康以后一小节的 HTTP 健康检查为准。

### 7.3 健康和系统信息检查

~~~bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/v1/system/health | jq .

curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/v1/system/info | jq . \
  | tee evidence/02-system-info.json
~~~

### 7.4 验证端口和 Docker Socket 隔离

~~~bash
sudo ss -lntp | grep ':8000'

for service in hub hub-conda-runner hub-docker-runner; do
  container_id=$(sudo docker compose ps -q "$service")
  echo "===== $service ====="
  sudo docker inspect "$container_id" \
    --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
done
~~~

验收要求：

- 8000 端口监听在 127.0.0.1，而不是 0.0.0.0；
- 只有 hub-docker-runner 出现 /var/run/docker.sock；
- hub 和 hub-conda-runner 不挂载 Docker Socket。

若健康检查失败：

~~~bash
sudo docker compose logs --tail=200 hub
sudo docker compose logs --tail=200 hub-conda-runner
sudo docker compose logs --tail=200 hub-docker-runner
~~~

在本节通过前，不要继续注册插件。

---

## 8. 安装插件构建工具

Docker 插件包只要求 Docker；conda-pack 插件包必须在原生 Linux AMD64 环境中构建。

### 8.1 安装 Miniforge

安装器来自 [conda-forge Miniforge 官方发布仓库](https://github.com/conda-forge/miniforge)：

~~~bash
cd /tmp
curl -L -o Miniforge3-Linux-x86_64.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash Miniforge3-Linux-x86_64.sh -b -p "$HOME/miniforge3"
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda --version
~~~

建议不要以 root 身份构建插件。插件构建产物由可信发布账号生成。

### 8.2 创建发布工具环境

~~~bash
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda create -y -n hub-publisher -c conda-forge \
  python=3.12 conda-pack zstandard pyyaml
conda activate hub-publisher
python -m pip install --upgrade pip build hatchling
python -m pip install -e packages/hub-contracts
python --version
conda-pack --version
~~~

---

## 9. 构建两种 NC→SHP 插件包

### 9.1 清理本次产物目录

只清理插件专用 dist 目录，不删除 Hub 数据目录：

~~~bash
cd /srv/python-service-hub
rm -rf packages/nc-to-shp-plugin/dist
mkdir -p packages/nc-to-shp-plugin/dist
~~~

### 9.2 构建 Docker 运行时包

~~~bash
cd /srv/python-service-hub
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate hub-publisher

python packages/nc-to-shp-plugin/scripts/package_build.py \
  --runtime docker \
  --arch amd64 \
  --output packages/nc-to-shp-plugin/dist \
  2>&1 | tee evidence/03-build-plugin-docker.log
~~~

该命令会构建插件任务镜像，并生成包含构建描述的 .pypkg。

### 9.3 构建 conda-pack 运行时包

~~~bash
python packages/nc-to-shp-plugin/scripts/package_build.py \
  --runtime conda-pack \
  --arch amd64 \
  --output packages/nc-to-shp-plugin/dist \
  2>&1 | tee evidence/04-build-plugin-conda.log
~~~

该命令会创建目标平台环境并打包。Hub 安装时只校验和解包，不联网安装依赖。

### 9.4 记录产物和校验值

~~~bash
find packages/nc-to-shp-plugin/dist -maxdepth 1 -type f \
  -name '*.pypkg' -printf '%f %s bytes\n' | sort

sha256sum packages/nc-to-shp-plugin/dist/*.pypkg \
  | tee evidence/05-plugin-sha256.txt
~~~

分别找出两种运行时包：

~~~bash
export DOCKER_PKG=$(find packages/nc-to-shp-plugin/dist -maxdepth 1 \
  -type f -name '*docker*.pypkg' | head -n 1)
export CONDA_PKG=$(find packages/nc-to-shp-plugin/dist -maxdepth 1 \
  -type f -name '*conda*.pypkg' | head -n 1)

test -n "$DOCKER_PKG" && test -f "$DOCKER_PKG"
test -n "$CONDA_PKG" && test -f "$CONDA_PKG"
printf 'Docker package: %s\nConda package: %s\n' "$DOCKER_PKG" "$CONDA_PKG"
~~~

如果实际文件名不含 docker 或 conda，请用 ls 查看文件名后手工 export 正确路径。

---

## 10. 注册并启用两个 PluginBuild

同一插件版本可以有多个 PluginBuild。Docker 和 conda-pack 包应注册成两个独立 Build。Job 创建时通过 plugin_id、version 和 runtime_type 选择对应 Build。

### 10.1 定义等待安装完成函数

~~~bash
wait_build() {
  build_id="$1"
  for attempt in $(seq 1 900); do
    response=$(curl --fail --silent --show-error \
      "http://127.0.0.1:8000/api/v1/plugin-builds/$build_id") || return 1
    status=$(printf '%s' "$response" | jq -r '.status')
    printf 'build=%s status=%s attempt=%s\n' "$build_id" "$status" "$attempt" >&2
    case "$status" in
      READY|ENABLED) printf '%s' "$response"; return 0 ;;
      FAILED) printf '%s\n' "$response" | jq .; return 1 ;;
    esac
    sleep 2
  done
  echo "等待 PluginBuild 安装超时" >&2
  return 1
}
~~~

### 10.2 注册 Docker 包

~~~bash
DOCKER_INSTALL=$(curl --fail --silent --show-error \
  --max-time 1800 \
  -X POST http://127.0.0.1:8000/api/v1/plugins/install \
  -F "file=@$DOCKER_PKG")

printf '%s\n' "$DOCKER_INSTALL" | jq . \
  | tee evidence/06-docker-install.json

export DOCKER_BUILD_ID=$(printf '%s' "$DOCKER_INSTALL" | jq -r '.build_id')
test -n "$DOCKER_BUILD_ID" && test "$DOCKER_BUILD_ID" != null
wait_build "$DOCKER_BUILD_ID" | jq . \
  | tee evidence/07-docker-build-ready.json
~~~

### 10.3 启用 Docker Build

~~~bash
curl --fail --silent --show-error \
  -X POST \
  "http://127.0.0.1:8000/api/v1/plugin-builds/$DOCKER_BUILD_ID/enable" \
  | jq . | tee evidence/08-docker-build-enabled.json
~~~

### 10.4 注册 conda-pack 包

~~~bash
CONDA_INSTALL=$(curl --fail --silent --show-error \
  --max-time 1800 \
  -X POST http://127.0.0.1:8000/api/v1/plugins/install \
  -F "file=@$CONDA_PKG")

printf '%s\n' "$CONDA_INSTALL" | jq . \
  | tee evidence/09-conda-install.json

export CONDA_BUILD_ID=$(printf '%s' "$CONDA_INSTALL" | jq -r '.build_id')
test -n "$CONDA_BUILD_ID" && test "$CONDA_BUILD_ID" != null
wait_build "$CONDA_BUILD_ID" | jq . \
  | tee evidence/10-conda-build-ready.json
~~~

### 10.5 启用 conda-pack Build

~~~bash
curl --fail --silent --show-error \
  -X POST \
  "http://127.0.0.1:8000/api/v1/plugin-builds/$CONDA_BUILD_ID/enable" \
  | jq . | tee evidence/11-conda-build-enabled.json
~~~

### 10.6 核对插件列表

~~~bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/v1/plugins | jq . \
  | tee evidence/12-plugin-list.json
~~~

如果第二个包返回 409，先确认是否已经注册过相同、不可变的 Build。不要反复注册相同包；直接从列表获取已有 build_id，或在全新的验收数据目录中执行第 13 节自动测试。

---

## 11. 上传 NC 并创建两个 Job

### 11.1 上传 NC 文件

~~~bash
test -s /srv/python-service-hub/fixtures/sample.nc

FILE_RESPONSE=$(curl --fail --silent --show-error \
  --max-time 1800 \
  -X POST http://127.0.0.1:8000/api/v1/files \
  -F "file=@/srv/python-service-hub/fixtures/sample.nc")

printf '%s\n' "$FILE_RESPONSE" | jq . \
  | tee evidence/13-file-upload.json

export INPUT_FILE_ID=$(printf '%s' "$FILE_RESPONSE" | jq -r '.file_id')
test -n "$INPUT_FILE_ID" && test "$INPUT_FILE_ID" != null
~~~

### 11.2 生成 Job 请求

示例 NC 的已验证参数是：

- group_name：1；
- metrics：depth、stage；
- start_time：1；
- end_time：20；
- target_crs：null。

~~~bash
jq -n \
  --arg file_id "$INPUT_FILE_ID" \
  '{
    plugin_id: "nc_to_shp",
    version: "1.0.0",
    runtime_type: "docker",
    inputs: {source_nc: $file_id},
    params: {
      group_name: "1",
      metrics: ["depth", "stage"],
      start_time: 1,
      end_time: 20,
      target_crs: null
    }
  }' > /tmp/docker-job.json

jq -n \
  --arg file_id "$INPUT_FILE_ID" \
  '{
    plugin_id: "nc_to_shp",
    version: "1.0.0",
    runtime_type: "conda-pack",
    inputs: {source_nc: $file_id},
    params: {
      group_name: "1",
      metrics: ["depth", "stage"],
      start_time: 1,
      end_time: 20,
      target_crs: null
    }
  }' > /tmp/conda-job.json
~~~

### 11.3 定义 Job 等待函数

~~~bash
wait_job() {
  job_id="$1"
  for attempt in $(seq 1 2100); do
    response=$(curl --fail --silent --show-error \
      "http://127.0.0.1:8000/api/v1/jobs/$job_id") || return 1
    status=$(printf '%s' "$response" | jq -r '.status')
    printf 'job=%s status=%s attempt=%s\n' "$job_id" "$status" "$attempt" >&2
    case "$status" in
      SUCCESS) printf '%s' "$response"; return 0 ;;
      FAILED|CANCELLED|TIMED_OUT)
        printf '%s\n' "$response" | jq .
        return 1
        ;;
    esac
    sleep 2
  done
  echo "等待 Job 完成超时" >&2
  return 1
}
~~~

### 11.4 运行 Docker Build

~~~bash
DOCKER_JOB_RESPONSE=$(curl --fail --silent --show-error \
  -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/docker-job.json)

printf '%s\n' "$DOCKER_JOB_RESPONSE" | jq . \
  | tee evidence/14-docker-job-created.json

export DOCKER_JOB_ID=$(printf '%s' "$DOCKER_JOB_RESPONSE" | jq -r '.job_id')
test -n "$DOCKER_JOB_ID" && test "$DOCKER_JOB_ID" != null
wait_job "$DOCKER_JOB_ID" | jq . \
  | tee evidence/15-docker-job-succeeded.json
~~~

### 11.5 运行 conda-pack Build

~~~bash
CONDA_JOB_RESPONSE=$(curl --fail --silent --show-error \
  -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/conda-job.json)

printf '%s\n' "$CONDA_JOB_RESPONSE" | jq . \
  | tee evidence/16-conda-job-created.json

export CONDA_JOB_ID=$(printf '%s' "$CONDA_JOB_RESPONSE" | jq -r '.job_id')
test -n "$CONDA_JOB_ID" && test "$CONDA_JOB_ID" != null
wait_job "$CONDA_JOB_ID" | jq . \
  | tee evidence/17-conda-job-succeeded.json
~~~

---

## 12. 下载日志、输出并验收

### 12.1 保存 Job 日志和输出清单

~~~bash
curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/jobs/$DOCKER_JOB_ID/logs" \
  -o evidence/18-docker-job.log

curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/jobs/$CONDA_JOB_ID/logs" \
  -o evidence/19-conda-job.log

curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/jobs/$DOCKER_JOB_ID/outputs" \
  | jq . | tee evidence/20-docker-outputs.json

curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/jobs/$CONDA_JOB_ID/outputs" \
  | jq . | tee evidence/21-conda-outputs.json
~~~

从输出清单中取第一个输出 file_id：

~~~bash
export DOCKER_OUTPUT_FILE_ID=$(jq -r \
  '.items[0].file_id // .outputs[0].file_id // .[0].file_id' \
  evidence/20-docker-outputs.json)

export CONDA_OUTPUT_FILE_ID=$(jq -r \
  '.items[0].file_id // .outputs[0].file_id // .[0].file_id' \
  evidence/21-conda-outputs.json)

test -n "$DOCKER_OUTPUT_FILE_ID" && test "$DOCKER_OUTPUT_FILE_ID" != null
test -n "$CONDA_OUTPUT_FILE_ID" && test "$CONDA_OUTPUT_FILE_ID" != null
~~~

### 12.2 下载两个 ZIP

~~~bash
curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/files/$DOCKER_OUTPUT_FILE_ID/download" \
  -o evidence/docker-result.zip

curl --fail --silent --show-error \
  "http://127.0.0.1:8000/api/v1/files/$CONDA_OUTPUT_FILE_ID/download" \
  -o evidence/conda-result.zip

unzip -t evidence/docker-result.zip
unzip -t evidence/conda-result.zip
sha256sum evidence/docker-result.zip evidence/conda-result.zip \
  | tee evidence/22-output-sha256.txt
~~~

### 12.3 创建验收工具环境

~~~bash
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda create -y -n hub-accept -c conda-forge \
  python=3.12 pytest=8.3 httpx=0.28 gdal=3.10.3
conda activate hub-accept
~~~

### 12.4 比较两种运行时结果

~~~bash
cd /srv/python-service-hub
python packages/nc-to-shp-plugin/scripts/compare_shapefile_zip.py \
  evidence/docker-result.zip \
  evidence/conda-result.zip \
  | tee evidence/23-output-compare.log
~~~

比较命令只接收两个 ZIP 路径，不存在 --expected-features 参数。

再执行带预期要素数量的严格核验。当前完整示例 NC 的预期空间要素数为 39464：

~~~bash
python - <<'PY' | tee evidence/24-feature-count-check.log
import sys
from pathlib import Path

sys.path.insert(0, str(Path("packages/nc-to-shp-plugin").resolve()))
from scripts.compare_shapefile_zip import compare_archives

differences = compare_archives(
    "evidence/docker-result.zip",
    "evidence/conda-result.zip",
    expected_features=39464,
)
if differences:
    raise SystemExit("\n".join(differences))
print("通过：两种运行时输出语义一致，每个 Shapefile 均为 39464 个要素。")
PY
~~~

验收要求：

- 两个 ZIP 均可完整解压；
- 两种运行时输出结构一致；
- 清单、字段、图层和要素统计符合比较器要求；
- 要素数量为 39464；
- 两个 Job 状态均为 SUCCESS；
- Job 日志无 Python traceback、依赖缺失或权限错误。

> ZIP 的二进制 SHA256 不一定相同，因为 ZIP 元数据或写入顺序可能变化。应以比较脚本的语义结果为准。

---

## 13. 可选：一条命令运行仓库自动双运行时验收

仓库集成测试会自行安装两个插件包，因此必须使用一个全新的、空的 Hub 数据目录。如果对已经手工注册插件的数据库再次执行，可能得到 409，这不是运行时失败。

### 13.1 停止当前实例

~~~bash
cd /srv/python-service-hub
sudo docker compose stop
~~~

### 13.2 用临时空数据目录启动

~~~bash
export ACCEPT_DATA=/srv/service-hub-acceptance-data
sudo mkdir -p "$ACCEPT_DATA"
sudo chmod 0750 "$ACCEPT_DATA"

sudo env HUB_DATA_DIR="$ACCEPT_DATA" docker compose up -d

until curl --fail --silent \
  http://127.0.0.1:8000/api/v1/system/health >/dev/null; do
  sleep 2
done
~~~

ACCEPT_DATA 必须是一个从未注册过这些 Build 的目录。若要重新执行，不要直接删除旧目录；先将旧证据归档，再创建另一个全新目录。

### 13.3 执行集成测试

~~~bash
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate hub-accept
cd /srv/python-service-hub

HUB_NC_SAMPLE_PATH=/srv/python-service-hub/fixtures/sample.nc \
HUB_PLUGIN_PACKAGE_DIR=/srv/python-service-hub/packages/nc-to-shp-plugin/dist \
HUB_BASE_URL=http://127.0.0.1:8000 \
python -m pytest -m integration \
  tests/integration/test_dual_runtime_nc_to_shp.py -v \
  2>&1 | tee evidence/25-automated-integration.log
~~~

### 13.4 恢复正式测试数据目录

无论测试成功或失败，都执行：

~~~bash
sudo env HUB_DATA_DIR="$ACCEPT_DATA" docker compose stop
sudo docker compose up -d
curl --fail --silent --show-error \
  http://127.0.0.1:8000/api/v1/system/health | jq .
~~~

第二条 compose 命令会重新读取 .env 中的 /srv/service-hub-data。

---

## 14. 离线服务器实施方法

如果最终服务器不能访问外网，原则是：在同架构 Linux AMD64 构建机上构建一切，离线服务器只导入和启动。

### 14.1 在线构建机准备

在在线 Linux AMD64 构建机完成：

1. 构建三个 Hub Compose 镜像；
2. 构建 Docker 与 conda-pack 两个 .pypkg；
3. 导出三个 Hub 镜像；Docker 插件任务镜像已经封装在 Docker .pypkg 中；
4. 保存源码包、插件包和 SHA256 清单。

查看实际镜像名：

~~~bash
docker compose images
docker image ls
~~~

按实际镜像名导出：

~~~bash
docker save IMAGE_NAME_1 IMAGE_NAME_2 IMAGE_NAME_3 \
  | gzip > service-hub-linux-amd64-images.tar.gz
sha256sum service-hub-linux-amd64-images.tar.gz \
  packages/nc-to-shp-plugin/dist/*.pypkg
~~~

### 14.2 离线服务器导入

~~~bash
sha256sum -c transferred-files.sha256
gunzip -c service-hub-linux-amd64-images.tar.gz | sudo docker load
cd /srv/python-service-hub
sudo docker compose up -d
~~~

然后继续执行第 10 至 12 节。conda-pack 包安装和运行不应访问公网；Docker 插件运行依赖的任务镜像必须已经导入本机 Docker。

---

## 15. 供其他系统访问

### 15.1 临时调试：SSH 隧道

在调用方机器执行：

~~~bash
ssh -L 8000:127.0.0.1:8000 SERVER_USER@SERVER_IP
~~~

随后调用 http://127.0.0.1:8000。

### 15.2 正式接入：Nginx

建议 Nginx 与 Hub 在同一服务器通信：

~~~nginx
server {
    listen 443 ssl;
    server_name service-hub.example.com;

    client_max_body_size 2g;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 在此配置公司统一认证、IP 白名单或 mTLS。
    }
}
~~~

不要只依赖“端口不公开”作为认证。上线前应由 Nginx 明确实现身份认证、TLS 和访问策略。

---

## 16. 日常启停、升级和备份

### 16.1 常用命令

~~~bash
cd /srv/python-service-hub
sudo docker compose ps
sudo docker compose logs -f --tail=200
sudo docker compose restart
sudo docker compose stop
sudo docker compose up -d
~~~

不要使用 docker compose down -v，除非明确要删除 Compose 卷。不要手工删除 /srv/service-hub-data。

### 16.2 源码升级

~~~bash
cd /srv/python-service-hub
sudo docker compose stop
sudo tar -czf "/srv/service-hub-backup-$(date +%Y%m%d-%H%M%S).tar.gz" \
  /srv/service-hub-data

# 将新源码覆盖到新的发布目录，完成配置核对后再执行：
sudo docker compose build
sudo docker compose up -d
curl --fail http://127.0.0.1:8000/api/v1/system/health
~~~

生产操作建议采用新的版本目录加固定数据目录，而不是直接覆盖当前目录。

---

## 17. 常见故障定位

### 17.1 Hub 无法启动

~~~bash
sudo docker compose ps
sudo docker compose logs --tail=300 hub
sudo ls -ld /srv/service-hub-data
sudo ls -l config/hub.yaml .env
~~~

重点检查：

- .env 是否含 HUB_RUNNER_TOKEN 和绝对 HUB_DATA_DIR；
- config/hub.yaml 是否能被容器 UID 65532 读取；
- 8000 端口是否已占用；
- SQLite 和数据目录是否可写。

### 17.2 插件包构建失败

~~~bash
docker version
docker buildx version
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda info
df -h
~~~

重点检查：

- 当前用户是否有 Docker 权限；
- GHCR、Docker Hub、conda-forge 是否可达；
- 构建机是不是 Linux AMD64；
- 磁盘是否足够；
- Docker 插件基础镜像是否已经拉取或离线导入。

### 17.3 Build 长时间停留在 INSTALLING

~~~bash
sudo docker compose logs --tail=300 hub
sudo docker compose logs --tail=300 hub-conda-runner
sudo docker compose logs --tail=300 hub-docker-runner
~~~

检查插件包 SHA256、目标 os/arch、内部 Runner Token、环境解包权限和任务镜像是否存在。

### 17.4 Job FAILED

~~~bash
curl --fail \
  "http://127.0.0.1:8000/api/v1/jobs/$DOCKER_JOB_ID" | jq .
curl --fail \
  "http://127.0.0.1:8000/api/v1/jobs/$DOCKER_JOB_ID/logs"
sudo docker compose logs --tail=300 hub-docker-runner
~~~

conda-pack Job 则查看 hub-conda-runner。常见原因包括输入字段名错误、NC group_name 错误、运行包架构错误、任务镜像缺失或数据目录挂载权限不足。

### 17.5 输出接口解析不到 file_id

先查看原始结构：

~~~bash
cat evidence/20-docker-outputs.json | jq .
cat evidence/21-conda-outputs.json | jq .
~~~

根据实际响应选择对应数组项，不要猜测文件系统路径。所有输出应通过 Hub File API 下载。

---

## 18. 实施记录模板

将以下内容复制到 evidence/实施记录.md，完成一项填写一项：

~~~markdown
# Python Service Hub 首次实施记录

- 实施日期：
- 实施人员：
- 服务器主机名：
- Linux 版本：
- CPU 架构：
- 源码提交：
- Docker 版本：
- Docker Compose 版本：
- Hub 数据目录：

## Hub

- compose build：通过 / 未通过
- 三个容器：通过 / 未通过
- health：通过 / 未通过
- system/info：通过 / 未通过
- 端口仅监听 127.0.0.1：通过 / 未通过
- Docker Socket 隔离：通过 / 未通过

## 插件

- Docker .pypkg 文件名：
- Docker .pypkg SHA256：
- Docker build_id：
- conda-pack .pypkg 文件名：
- conda-pack .pypkg SHA256：
- conda-pack build_id：

## 测试

- 输入 NC SHA256：
- input file_id：
- Docker job_id：
- Docker output file_id：
- Docker Job：SUCCESS / 未通过
- conda-pack job_id：
- conda-pack output file_id：
- conda-pack Job：SUCCESS / 未通过
- 输出比较：通过 / 未通过
- 预期 39464 个要素：通过 / 未通过

## 异常与处理

- 无 / 详细记录：

## 最终结论

- 可以进入下一阶段 / 不可以
- 遗留问题：
~~~

创建记录并补充机器信息：

~~~bash
uname -a | tee evidence/26-uname.txt
docker version | tee evidence/27-docker-version.txt
docker compose version | tee evidence/28-compose-version.txt
sha256sum fixtures/sample.nc | tee evidence/29-input-sha256.txt
sudo docker compose ps | tee evidence/30-compose-ps.txt
~~~

---

## 19. 你实际应按什么顺序执行

首次实施严格按下列顺序：

1. 第 4 节：安装 Docker 与工具；
2. 第 5 节：上传源码和 NC；
3. 第 6 节：配置持久化目录、hub.yaml 和 .env；
4. 第 7 节：构建并启动 Hub，完成基础检查；
5. 第 8 节：安装 Miniforge 和发布环境；
6. 第 9 节：构建两个 Linux AMD64 插件包；
7. 第 10 节：注册并启用两个 Build；
8. 第 11 节：上传 NC，分别创建两个 Job；
9. 第 12 节：下载结果并严格核验；
10. 第 18 节：保存完整实施证据。

第 13 节是可选的自动回归，不替代第 10 至 12 节的人工流程理解。完成以上步骤后，你就已经走通了“部署 Service Hub → 注册插件 → 上传输入 → 调度运行 → 下载输出”的完整 V1 主链路。

更完整的运行时设计与包格式说明见 [双运行时插件构建与部署](双运行时插件构建与部署.md)，项目模块与数据流说明见 [项目总览与实施部署](项目总览与实施部署.md)。
