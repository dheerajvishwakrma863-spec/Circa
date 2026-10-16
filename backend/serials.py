"""Serial authenticity: CIRC-<8 digits>-<10 hex HMAC>. Forged codes fail before any DB lookup."""
import hashlib, hmac, os, re
from dotenv import load_dotenv
load_dotenv()
_SECRET = os.getenv("SERIAL_SECRET", "dev-secret-change-me").encode()
PATTERN = re.compile(r"CIRC-\d{8}-[0-9A-F]{10}")

def _mac(s: str, n: int) -> str:
    return hmac.new(_SECRET, s.encode(), hashlib.sha256).hexdigest()[:n].upper()

def make_code(n: int) -> str:
    body = f"CIRC-{n:08d}"
    return f"{body}-{_mac(body, 10)}"

def find_code(text: str):
    m = PATTERN.search((text or "").upper())
    return m.group(0) if m else None

def is_authentic(code: str) -> bool:
    return bool(PATTERN.fullmatch(code)) and hmac.compare_digest(make_code(int(code[5:13])), code)

def coupon_for(tx_id: int) -> str:
    return f"CIRCA-{_mac(f'tx{tx_id}', 8)}"
