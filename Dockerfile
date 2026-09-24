# PharmaCost-AI 整合单镜像 Dockerfile
# 在同一个镜像中运行 FastAPI 主应用(:8000) 与模拟 RPA 服务(:8090)，
# 内置竞赛数据包、已构建的知识库索引(app/chroma_db) 与 AI 大模型配置
# （app/settings/model_config.json + app/.env），开箱即用、功能无缺失。
#
# 说明：
#   - 嵌入已切换为百炼(DashScope) API，无需再打包 torch/sentence-transformers，
#     依赖体积与构建时间相较本地模型版本大幅下降。
#   - 多阶段构建：编译工具链仅在 builder 阶段使用，最终镜像精简。
#   - 全程国内源（阿里云 pypi / debian），不依赖 BuildKit，兼容所有 Docker 版本。

# === 构建阶段：编译 + 拉取全部 wheel ===
FROM python:3.11-slim AS builder

# 系统依赖走阿里云 Debian 镜像（python:3.11-slim 基于 bookworm）
RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g; s@security.debian.org@mirrors.aliyun.com/debian-security@g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/pharmacost

# 只先复制依赖清单：清单未变时，下面 wheel 层直接复用 Docker 层缓存
COPY app/requirements.txt ./

# 按依赖解析一次性拉取全部 wheel（无 torch，速度较快）
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# === 运行阶段：精简，不含编译工具链 ===
FROM python:3.11-slim
WORKDIR /opt/pharmacost
COPY --from=builder /wheels /wheels
# 离线安装，避免运行时访问外网；临时 wheel 目录用完即删
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels

# 主应用代码（含 .env、settings/model_config.json、chroma_db 知识库）
COPY app/ ./app/

# 模拟 RPA 服务（主应用启动时会探测并从本目录拉起）
COPY rpa_mock/ ./rpa_mock/

# 竞赛数据包作为内置运行数据，DATA_DIR 指向此处
COPY data/ ./data/

# 未提供 app/.env 时用示例兜底（正常情况下 .env 已随镜像带入）
RUN test -f app/.env || cp app/.env.example app/.env

# 运行时持久化目录（chroma_db 已在 COPY 阶段带入，此处在非特权用户下保持可写）
RUN mkdir -p \
        app/output \
        app/chroma_db \
        app/settings/data_uploads \
        app/settings/knowledge_uploads \
        app/settings/template_uploads

# 以非特权用户运行，并确保全目录可写（含 chroma_db 索引）
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /opt/pharmacost
USER appuser

# 运行参数：数据目录指向内置数据包；嵌入/模型走百炼 API（密钥在 app/.env）；
# 显式固定 chroma 索引路径与 RPA 地址。
ENV DATA_DIR=/opt/pharmacost/data \
    RPA_BASE_URL=http://127.0.0.1:8090 \
    CHROMA_DB_PATH=/opt/pharmacost/app/chroma_db \
    PYTHONUNBUFFERED=1

# 应用以 app/ 为包根运行（import config / routers 均为顶层导入）
EXPOSE 8000 8090
WORKDIR /opt/pharmacost/app

# 先在后台启动模拟 RPA，再以前台启动主应用
CMD ["sh", "-c", "\
    nohup python /opt/pharmacost/rpa_mock/mock_rpa_server.py > /tmp/rpa.log 2>&1 & \
    exec uvicorn main:app --host 0.0.0.0 --port 8000"]