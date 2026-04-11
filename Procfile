web: uvicorn src.main:app --host 0.0.0.0 --port $PORT
release: python -c "from src.db import init_db; init_db()"
