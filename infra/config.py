import os
from dotenv import load_dotenv

load_dotenv()

def env_str(key: str, default: str | None = None) -> str:
    v = os.getenv(key, default)
    if v is None:
        raise SystemExit(f"Falta variable de entorno: {key}")
    return v

def env_int(key: str, default: int) -> int:
    return int(os.getenv(key, default))

def env_float(key: str, default: float) -> float:
    return float(os.getenv(key, default))
