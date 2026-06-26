FROM python:3.14-slim

ENV TZ=Asia/Shanghai

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ uv
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen

COPY main.py ./
COPY gunicorn.py ./
COPY services ./services

EXPOSE 5000

CMD ["./.venv/bin/gunicorn", "-c", "gunicorn.py", "main:app"]
