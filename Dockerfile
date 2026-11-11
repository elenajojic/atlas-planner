FROM python:3.12-slim
WORKDIR /app
COPY requirements-remote.txt .
RUN pip install --no-cache-dir -r requirements-remote.txt
COPY planner ./planner
COPY data ./data
COPY profile.example.yaml run_remote.py ./
ENV PYTHONUNBUFFERED=1 ATLAS_PLANNER_WORKDIR=/tmp/atlas-planner
EXPOSE 8000
CMD ["python", "run_remote.py"]
