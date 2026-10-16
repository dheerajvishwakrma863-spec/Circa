from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base

def now(): return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(10), default="USER", index=True)   # USER | ADMIN
    is_active: Mapped[bool] = mapped_column(default=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    material: Mapped[str] = mapped_column(String(30))
    weight_g: Mapped[float] = mapped_column(Float)          # new primary container
    refill_weight_g: Mapped[float] = mapped_column(Float)   # refill pouch/cartridge
    co2_per_kg: Mapped[float] = mapped_column(Float)        # kg CO2e per kg material

class PackagingSerial(Base):
    __tablename__ = "packaging_serials"
    id: Mapped[int] = mapped_column(primary_key=True)
    qr_code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    batch_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE", index=True)  # ACTIVE|REDEEMED|VOID
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    product: Mapped[Product] = relationship()

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("serial_id", name="uq_one_claim_per_serial"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    serial_id: Mapped[int] = mapped_column(ForeignKey("packaging_serials.id"))
    plastic_saved_g: Mapped[float] = mapped_column(Float)
    co2_saved_g: Mapped[float] = mapped_column(Float)
    points: Mapped[int] = mapped_column(Integer, default=0)
    claimed: Mapped[bool] = mapped_column(default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    serial: Mapped[PackagingSerial] = relationship()
