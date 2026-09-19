FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.txt README.md /app/
COPY job_agent /app/job_agent
COPY profile /app/profile
COPY templates /app/templates
COPY config.example.yaml /app/config.example.yaml
RUN pip install --no-cache-dir -e .
CMD ["job-agent","scan","--config","config.yaml"]
