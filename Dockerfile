FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY app/requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码。竞赛数据包在容器运行时以只读卷挂载到 /data。
COPY app/ ./app/

# 创建运行时持久化目录
RUN mkdir -p /app/app/output /app/app/chroma_db /app/app/settings

# Run the application as an unprivileged user while keeping mounted data writable.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/app

EXPOSE 8000

CMD ["sh", "-c", "test -d /data/01_成本明细数据 || { echo 'ERROR: 竞赛数据包未挂载。请将创灵境_考题模拟数据挂载到容器 /data。' >&2; exit 1; }; exec uvicorn main:app --host 0.0.0.0 --port 8000"]
