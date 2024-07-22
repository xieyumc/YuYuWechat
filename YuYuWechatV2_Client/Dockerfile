# 使用Python官方镜像
FROM python:3.11.9-slim-bullseye

# 设置工作目录
WORKDIR /app


# 复制requirements.txt并安装依赖
COPY requirements.txt .

RUN pip3 install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y procps


# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 7000

# 启动Django应用
CMD ["python", "manage.py", "runserver", "0.0.0.0:7500"]