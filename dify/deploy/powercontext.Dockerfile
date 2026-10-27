FROM python:3.12-slim
RUN pip install --no-cache-dir "powercontext[cli,server]==1.4.1"
WORKDIR /data
ENV PYTHONUNBUFFERED=1 POWERCONTEXT_HOME=/data
EXPOSE 8000
CMD ["powercontext", "server", "run"]
