"""Seed FMCG packaging data + bootstrap admin. Run: python -m backend.seed
Emission factors are indicative public-range values (kg CO2e per kg material); replace with supplier LCA data."""
import os
from backend.database import Base, SessionLocal, engine
from backend.models import PackagingSerial, Product, User
from backend.security import PROD, hash_pw
from backend.serials import make_code

PRODUCTS = [  # sku, name, material, new_g, refill_g, co2_per_kg
    ("SRM-30", "Vitamin C Serum 30ml", "Glass", 78.0, 14.0, 1.1),
    ("SHP-250", "Repair Shampoo 250ml", "HDPE", 32.0, 9.0, 1.9),
    ("CRM-50", "Day Cream 50ml", "PP", 45.0, 12.0, 2.0),
    ("LIP-4", "Lipstick Case", "Aluminium", 18.0, 4.0, 8.2),
    ("BDY-400", "Body Lotion 400ml", "PET", 28.0, 8.0, 2.3),
]

def ensure_admin(db):
    email, pw = os.getenv("ADMIN_EMAIL", "").lower(), os.getenv("ADMIN_PASSWORD", "")
    if not email or not pw: return False
    if len(pw) < 12: raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters")
    if not db.query(User).filter(User.email == email).first():
        db.add(User(name="Brand Admin", email=email, password_hash=hash_pw(pw), role="ADMIN")); db.commit()
    return True

def run(per_product=60, images=False):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        ensure_admin(db)
        if db.query(Product).count(): return 0
        n = 0
        for i, (sku, name, mat, w, rw, f) in enumerate(PRODUCTS, 1):
            p = Product(sku=sku, name=name, material=mat, weight_g=w, refill_weight_g=rw, co2_per_kg=f)
            db.add(p); db.flush()
            for _ in range(per_product):
                n += 1; db.add(PackagingSerial(qr_code=make_code(n), product_id=p.id, batch_id=f"B{i}-2026-10"))
        if not PROD and not db.query(User).filter(User.email == "demo@circa.example").first():
            db.add(User(name="Demo User", email="demo@circa.example", password_hash=hash_pw("DemoPass2026!")))
        db.commit()
        if images:
            import qrcode
            os.makedirs("sample_qr", exist_ok=True)
            for s in db.query(PackagingSerial).limit(5): qrcode.make(s.qr_code).save(f"sample_qr/{s.qr_code}.png")
        return n
    finally:
        db.close()

if __name__ == "__main__":
    print("serials created:", run(images=True), "| dev login: demo@circa.example / DemoPass2026! | QR images in ./sample_qr")
