import os

# Tests never send traces, even when .env turns LangSmith tracing on.
# Set before config.py's load_dotenv(), which doesn't override existing vars.
os.environ["LANGSMITH_TRACING"] = "false"
