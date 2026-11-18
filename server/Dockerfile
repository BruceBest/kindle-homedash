FROM python:3.12-slim
RUN pip install --no-cache-dir pillow==11.*
WORKDIR /app
COPY app.py remote.py pages.py nativeui.py ./
USER 1000:1000
CMD ["python", "-u", "app.py"]
