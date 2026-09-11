# Signal Map 后端（FastAPI + SQLite）。
#
# 构建上下文是**仓库根目录**，不是 signal_map/ —— 因为 config/ 在根目录下，
# 而圈层与分类体系都从那里读。Railway 里对应 railway.json 的 dockerfilePath。
FROM python:3.11-slim

WORKDIR /app

COPY signal_map/requirements.txt signal_map/requirements.txt
# Railway 的构建网络偶尔会在大 wheel 上卡住，给足超时和重试。
RUN pip install --no-cache-dir --timeout 600 --retries 5 -r signal_map/requirements.txt

COPY signal_map/backend signal_map/backend
COPY signal_map/scripts signal_map/scripts
COPY signal_map/__init__.py signal_map/__init__.py
COPY config config

# 种子库由 build_deploy_seed.py 生成：176MB 的工作库裁到 35MB。
# 裁掉的是客户接口永远走不到的关注边与账号，接口结果逐项比对过，完全一致。
COPY signal_map/data/deploy_seed.db /app/seed/signal_map.seed.db

COPY signal_map/entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

# 挂载卷的位置。**不要**写 Docker 的 VOLUME 指令 —— Railway 会拒绝。
ENV SIGNAL_MAP_DB_PATH=/data/signal_map.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["./entrypoint.sh"]
