FROM python:3.12-slim
WORKDIR /app
COPY requirements-remote.txt .
RUN pip install --no-cache-dir -r requirements-remote.txt
COPY planner ./planner
COPY data ./data
COPY profile.example.yaml run_remote.py ./
ENV PYTHONUNBUFFERED=1 ATLAS_PLANNER_WORKDIR=/tmp/atlas-planner SOLVER_WORKERS=1 SOLVER_MAX_MB=120 WEEK_BUDGET_SECONDS=35 ALIGN_ROUNDS=2 WEEK_MAX_COURSES=20
EXPOSE 8000
CMD ["python", "run_remote.py"]
