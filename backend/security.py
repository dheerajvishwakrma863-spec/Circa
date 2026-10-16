"""JWT + bcrypt + in-memory rate limiter. Fails closed in production if secrets are missing/weak."""
import os, time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import bcrypt, jwt
from dotenv import load_dotenv
load_dotenv()

ENV = os.getenv("ENV", "development").lower()
PROD = ENV == "production"
SECRET = os.getenv("JWT_SECRET", "")
if not SECRET:
    if PROD: raise RuntimeError("JWT_SECRET is required when ENV=production")
    SECRET = "dev-only-jwt-secret-change-me-0123456789"
if PROD and len(SECRET) < 32: raise RuntimeError("JWT_SECRET must be at least 32 characters")
if PROD and os.getenv("SERIAL_SECRET", "dev-secret-change-me") == "dev-secret-change-me":
    raise RuntimeError("SERIAL_SECRET must be set when ENV=production")
TTL_MIN = int(os.getenv("JWT_TTL_MIN", "60"))

def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode()[:72], bcrypt.gensalt(12)).decode()

def verify_pw(p: str, h: str) -> bool:
    try: return bcrypt.checkpw(p.encode()[:72], h.encode())
    except Exception: return False

DUMMY_HASH = hash_pw("not-a-real-password")     # equalises login timing for unknown emails

def make_token(uid: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(uid), "role": role, "iat": now, "exp": now + timedelta(minutes=TTL_MIN)}, SECRET, algorithm="HS256")

def decode_token(t: str) -> dict:
    return jwt.decode(t, SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})

class Limiter:
    """Sliding window per key. Per-process memory: use Redis (e.g. slowapi/redis) when running >1 instance."""
    def __init__(self): self.h = defaultdict(deque); self.n = 0
    def _prune(self, k, win):
        q, t = self.h[k], time.monotonic()
        while q and t - q[0] > win: q.popleft()
        return q
    def count(self, k, win): return len(self._prune(k, win))
    def hit(self, k, limit, win) -> bool:
        q = self._prune(k, win)
        self.n += 1
        if self.n % 2000 == 0:                      # periodic cleanup of idle keys
            for key in [x for x, d in self.h.items() if not d or time.monotonic() - d[-1] > 3600]: self.h.pop(key, None)
        if len(q) >= limit: return False
        q.append(time.monotonic()); return True

limiter = Limiter()
