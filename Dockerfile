FROM python:3.14-slim

WORKDIR /app

COPY . /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request,sys; s=urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).getcode(); sys.exit(0 if s==200 else 1)"

CMD ["python3", "app.py"]
