"""CIRCA Enterprise API. Run from repo root: uvicorn backend.app:app --reload"""
import csv, io, os, re
from datetime import datetime, timezone
from typing import Optional
import cv2, jwt, numpy as np
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from backend import seed
from backend.database import Base, engine, get_db
from backend.models import PackagingSerial, Product, Transaction, User
from backend.security import DUMMY_HASH, PROD, decode_token, hash_pw, limiter, make_token, verify_pw
from backend.serials import coupon_for, find_code, is_authentic, make_code

try:
    from pyzbar.pyzbar import decode as zbar_decode
except Exception:            # libzbar missing: OpenCV-only mode
    zbar_decode = None

MAX_IMG = 8 * 1024 * 1024
DEMO = os.getenv("DEMO_MODE", "false").lower() == "true"
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"
TIERS = [(600, "Bloom"), (200, "Sprout"), (0, "Seed")]
RULES = {"/auth/login": (5, 60), "/auth/register": (5, 600), "/verify": (30, 60), "/rewards/claim": (30, 60)}

class ApiError(Exception):
    def __init__(self, status, code, message): self.status, self.code, self.message = status, code, message

app = FastAPI(title="CIRCA Enterprise API", version="3.0", docs_url=None if PROD else "/docs", redoc_url=None, openapi_url=None if PROD else "/openapi.json")
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "" if PROD else "*").split(",") if o.strip()]
if PROD and "*" in origins: raise RuntimeError("CORS_ORIGINS must not be '*' in production")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

def err(status, code, msg, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": msg}}, headers=headers)

@app.exception_handler(ApiError)
async def _api(_: Request, e: ApiError): return err(e.status, e.code, e.message)

@app.exception_handler(RequestValidationError)
async def _val(_: Request, e: RequestValidationError): return err(422, "validation", "Invalid input: " + "; ".join(f"{'.'.join(map(str, x['loc'][1:]))} {x['msg']}" for x in e.errors())[:300])

@app.exception_handler(Exception)
async def _any(_: Request, e: Exception): return err(500, "internal", "Unexpected server error")

def client_ip(r: Request):
    if TRUST_PROXY and r.headers.get("x-forwarded-for"): return r.headers["x-forwarded-for"].split(",")[-1].strip()   # last hop = added by our proxy
    return r.client.host if r.client else "unknown"

@app.middleware("http")
async def guard(request: Request, call_next):
    path, ip = request.url.path, client_ip(request)
    lim, win = RULES.get(path, (120, 60))
    key = f"{ip}:{path}" if path in RULES else f"{ip}:*"
    if request.method != "OPTIONS" and not limiter.hit(key, lim, win):
        resp = err(429, "rate_limited", "Too many requests. Slow down.", {"Retry-After": str(win)})
    else:
        resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(self)", "Cache-Control": "no-store" if path != "/" and not path.endswith(".html") else "no-cache",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"})
    if PROD: resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp

@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)         # use Alembic migrations for schema changes
    if os.getenv("SEED_ON_START", "false").lower() == "true": seed.run()

bearer = HTTPBearer(auto_error=False)

def current_user(c: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not c: raise ApiError(401, "unauthenticated", "Login required.")
    try: claims = decode_token(c.credentials)
    except jwt.PyJWTError: raise ApiError(401, "bad_token", "Invalid or expired token.")
    u = db.get(User, int(claims["sub"]))
    if not u or not u.is_active: raise ApiError(401, "bad_token", "Account not available.")
    return u        # role is read from the DB, never trusted from the token

def admin_user(u: User = Depends(current_user)) -> User:
    if u.role != "ADMIN": raise ApiError(403, "forbidden", "Admin role required.")
    return u

def tier(p): return next(n for t, n in TIERS if p >= t)

def decode_image(data: bytes) -> str:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None: raise ApiError(400, "bad_image", "Could not read the image file.")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    texts = []
    if zbar_decode:
        for v in (gray, otsu, big):
            texts += [r.data.decode("utf-8", "ignore") for r in zbar_decode(v)]
    det = cv2.QRCodeDetector()
    for v in (img, gray, otsu, big):
        t, _, _ = det.detectAndDecode(v)
        if t: texts.append(t)
    for t in texts:
        c = find_code(t)
        if c: return c
    raise ApiError(422, "no_code", "No CIRCA QR code found. Move closer, improve light, keep the code flat.")

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1)); return {"status": "ok"}

class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=200)
    password: str = Field(min_length=10, max_length=72)

class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=72)

def pub(u: User): return {"user_id": u.id, "name": u.name, "email": u.email, "role": u.role, "points": u.points, "tier": tier(u.points)}

@app.post("/auth/register", status_code=201)
def register(b: RegisterIn, db: Session = Depends(get_db)):
    email = b.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): raise ApiError(422, "bad_email", "Invalid email.")
    if not (re.search(r"[A-Za-z]", b.password) and re.search(r"\d", b.password)) or len(b.password.encode()) > 72:
        raise ApiError(422, "weak_password", "Password needs 10-72 bytes with letters and digits.")
    if db.scalar(select(User.id).where(User.email == email)): raise ApiError(409, "email_taken", "Email already registered.")
    u = User(name=b.name.strip(), email=email, password_hash=hash_pw(b.password), role="USER")   # self-registration is always USER
    db.add(u)
    try: db.commit()
    except IntegrityError: db.rollback(); raise ApiError(409, "email_taken", "Email already registered.")
    return {"token": make_token(u.id, u.role), "user": pub(u)}

@app.post("/auth/login")
def login(b: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == b.email.strip().lower()))
    ok = verify_pw(b.password, u.password_hash if u else DUMMY_HASH)
    if not u or not ok or not u.is_active: raise ApiError(401, "bad_credentials", "Incorrect email or password.")
    return {"token": make_token(u.id, u.role), "user": pub(u)}

@app.get("/auth/me")
def me(u: User = Depends(current_user)): return pub(u)

@app.post("/verify")
async def verify(code: Optional[str] = Form(None, max_length=200), image: Optional[UploadFile] = File(None),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    if limiter.count(f"bad:{user.id}", 600) >= 10: raise ApiError(429, "locked", "Too many invalid codes. Try again in 10 minutes.")
    source = "manual"
    if image is not None:
        if image.content_type not in ("image/jpeg", "image/png", "image/webp"): raise ApiError(415, "bad_type", "Use JPEG, PNG or WebP.")
        data = await image.read(MAX_IMG + 1)
        if len(data) > MAX_IMG: raise ApiError(413, "too_large", "Image over 8 MB.")
        code, source = decode_image(data), "image"
    code = find_code(code or "")
    if not code: raise ApiError(422, "no_code", "Provide an image or a CIRCA code.")
    if not is_authentic(code):
        limiter.hit(f"bad:{user.id}", 10, 600); raise ApiError(403, "counterfeit", "Serial failed authenticity check.")
    s = db.scalar(select(PackagingSerial).where(PackagingSerial.qr_code == code).with_for_update())
    if not s:
        limiter.hit(f"bad:{user.id}", 10, 600); raise ApiError(404, "unknown_serial", "Serial not in registry.")
    if s.status != "ACTIVE": raise ApiError(409, "already_redeemed" if s.status == "REDEEMED" else "void", f"Serial status: {s.status}.")
    p = s.product
    plastic = round(max(p.weight_g - p.refill_weight_g, 0.0), 2)
    co2 = round(plastic * p.co2_per_kg, 2)     # g CO2e = g plastic x (kg CO2e / kg)
    s.status, s.redeemed_at = "REDEEMED", datetime.now(timezone.utc)
    tx = Transaction(user_id=user.id, serial_id=s.id, plastic_saved_g=plastic, co2_saved_g=co2)
    db.add(tx)
    try: db.commit()
    except IntegrityError: db.rollback(); raise ApiError(409, "already_redeemed", "Serial was just redeemed.")
    return {"verified": True, "source": source, "transaction_id": tx.id, "serial": code, "product": p.name,
            "material": p.material, "plastic_saved_g": plastic, "co2_saved_g": co2}

class ClaimIn(BaseModel):
    transaction_id: int

@app.post("/rewards/claim")
def claim(b: ClaimIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tx = db.scalar(select(Transaction).where(Transaction.id == b.transaction_id, Transaction.user_id == user.id).with_for_update())
    if not tx: raise ApiError(404, "not_found", "Transaction not found.")
    if tx.claimed: raise ApiError(409, "already_claimed", "Reward already claimed.")
    pts = 50 + int(tx.plastic_saved_g)
    u = db.scalar(select(User).where(User.id == user.id).with_for_update()); u.points += pts; tx.points, tx.claimed = pts, True
    db.commit()
    return {"points_awarded": pts, "total_points": u.points, "tier": tier(u.points),
            "discount_pct": min(25.0, round(u.points / 40, 1)), "coupon": coupon_for(tx.id)}

@app.get("/user/{user_id}/footprint")
def footprint(user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user_id != user.id and user.role != "ADMIN": raise ApiError(403, "forbidden", "You can only view your own footprint.")
    u = db.get(User, user_id)
    if not u: raise ApiError(404, "unknown_user", "User not found.")
    plastic, co2, n = db.execute(select(func.coalesce(func.sum(Transaction.plastic_saved_g), 0),
        func.coalesce(func.sum(Transaction.co2_saved_g), 0), func.count(Transaction.id)).where(Transaction.user_id == user_id)).one()
    recent = db.execute(select(Transaction, Product.name).join(PackagingSerial, Transaction.serial_id == PackagingSerial.id)
        .join(Product, PackagingSerial.product_id == Product.id).where(Transaction.user_id == user_id)
        .order_by(Transaction.timestamp.desc()).limit(8)).all()
    return {**pub(u), "packs": n, "plastic_saved_g": round(plastic, 1), "co2_saved_g": round(co2, 1),
            "recent": [{"product": nm, "plastic_saved_g": t.plastic_saved_g, "points": t.points, "timestamp": t.timestamp.isoformat()} for t, nm in recent]}

@app.get("/metrics/public")
def metrics_public(db: Session = Depends(get_db)):
    plastic, co2, packs = db.execute(select(func.coalesce(func.sum(Transaction.plastic_saved_g), 0),
        func.coalesce(func.sum(Transaction.co2_saved_g), 0), func.count(Transaction.id))).one()
    return {"plastic_saved_g": round(plastic, 1), "co2_saved_g": round(co2, 1), "verified_packs": packs}

@app.get("/metrics")
def metrics(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    month = func.to_char(Transaction.timestamp, "YYYY-MM") if db.get_bind().dialect.name == "postgresql" \
        else func.strftime("%Y-%m", Transaction.timestamp)
    plastic, co2, packs, pts = db.execute(select(func.coalesce(func.sum(Transaction.plastic_saved_g), 0),
        func.coalesce(func.sum(Transaction.co2_saved_g), 0), func.count(Transaction.id), func.coalesce(func.sum(Transaction.points), 0))).one()
    total = db.scalar(select(func.count(PackagingSerial.id))) or 0
    monthly = db.execute(select(month.label("m"), func.sum(Transaction.plastic_saved_g), func.sum(Transaction.co2_saved_g)).group_by("m").order_by("m")).all()
    by_prod = db.execute(select(Product.sku, Product.name, func.count(Transaction.id), func.sum(Transaction.plastic_saved_g), func.sum(Transaction.co2_saved_g))
        .join(PackagingSerial, PackagingSerial.product_id == Product.id).join(Transaction, Transaction.serial_id == PackagingSerial.id)
        .group_by(Product.sku, Product.name)).all()
    return {"totals": {"plastic_saved_g": round(plastic, 1), "co2_saved_g": round(co2, 1), "verified_packs": packs, "reward_points": int(pts),
                       "active_users": db.scalar(select(func.count(func.distinct(Transaction.user_id)))) or 0},
            "serials": {"total": total, "redeemed": packs, "redemption_rate_pct": round(100 * packs / total, 1) if total else 0},
            "monthly": [{"month": m, "plastic_saved_g": round(p, 1), "co2_saved_g": round(c, 1)} for m, p, c in monthly],
            "by_product": [{"sku": a, "name": b, "packs": c, "plastic_saved_g": round(d, 1), "co2_saved_g": round(e, 1)} for a, b, c, d, e in by_prod]}

class ProductIn(BaseModel):
    sku: str = Field(pattern=r"^[A-Za-z0-9\-]{2,40}$")
    name: str = Field(min_length=1, max_length=120)
    material: str = Field(min_length=1, max_length=30)
    weight_g: float = Field(gt=0, le=5000)
    refill_weight_g: float = Field(ge=0, le=5000)
    co2_per_kg: float = Field(gt=0, le=100)

@app.post("/admin/products", status_code=201)
def add_product(b: ProductIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    if b.refill_weight_g >= b.weight_g: raise ApiError(422, "bad_weights", "refill_weight_g must be less than weight_g.")
    p = Product(**b.model_dump())
    db.add(p)
    try: db.commit()
    except IntegrityError: db.rollback(); raise ApiError(409, "sku_exists", "SKU already exists.")
    return {"id": p.id, "sku": p.sku}

@app.get("/admin/products")
def list_products(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [{"sku": p.sku, "name": p.name, "material": p.material, "weight_g": p.weight_g, "refill_weight_g": p.refill_weight_g, "co2_per_kg": p.co2_per_kg}
            for p in db.scalars(select(Product).order_by(Product.id))]

class BatchIn(BaseModel):
    sku: str
    batch_id: str = Field(pattern=r"^[A-Za-z0-9\-_]{2,40}$")
    count: int = Field(ge=1, le=5000)

@app.post("/admin/serials", status_code=201)
def issue_serials(b: BatchIn, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    p = db.scalar(select(Product).where(Product.sku == b.sku))
    if not p: raise ApiError(404, "unknown_sku", "SKU not found.")
    start = (db.scalar(select(func.max(PackagingSerial.id))) or 0) + 1
    codes = [make_code(start + i) for i in range(b.count)]
    db.add_all([PackagingSerial(qr_code=c, product_id=p.id, batch_id=b.batch_id) for c in codes])
    try: db.commit()
    except IntegrityError: db.rollback(); raise ApiError(409, "collision", "Concurrent issuance collision; retry.")
    return {"issued": b.count, "batch_id": b.batch_id, "sample": codes[:5]}

@app.get("/admin/serials/export")
def export_serials(batch_id: str, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.execute(select(PackagingSerial.qr_code, Product.sku, PackagingSerial.status).join(Product).where(PackagingSerial.batch_id == batch_id)).all()
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(["qr_code", "sku", "status"]); w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{re.sub(r"[^A-Za-z0-9_-]", "", batch_id)}.csv"'})

@app.get("/admin/qr/{code}.png")
def qr_png(code: str, _: User = Depends(admin_user)):
    import qrcode
    if not is_authentic(code): raise ApiError(404, "not_found", "Unknown code.")
    buf = io.BytesIO(); qrcode.make(code).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")

if DEMO and not PROD:
    @app.get("/demo/serials")
    def demo_serials(_: User = Depends(current_user), db: Session = Depends(get_db)):
        return [{"code": s.qr_code, "product": s.product.name} for s in
                db.scalars(select(PackagingSerial).where(PackagingSerial.status == "ACTIVE").order_by(func.random()).limit(6))]

_dash = os.path.join(os.path.dirname(__file__), "..", "dashboard")
if os.path.isdir(_dash):
    app.mount("/", StaticFiles(directory=_dash, html=True), name="dashboard")
