from sqlalchemy import String, JSON, Double, ForeignKey, UniqueConstraint, BigInteger
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class PrivacyBudget(Base):
    """按数据批次共享的隐私预算总额（ε 与 δ 分别记账）。"""
    __tablename__ = 'privacy_budgets'
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), unique=True)
    total_epsilon: Mapped[float] = mapped_column(Double)
    total_delta: Mapped[float] = mapped_column(Double)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class NoiseCache(Base):
    """同批次 + 同隐私参数只取一次噪声，换账号、换阈值均复用本行。"""
    __tablename__ = 'dp_noise_caches'
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    epsilon: Mapped[float] = mapped_column(Double)
    delta: Mapped[float] = mapped_column(Double)
    mechanism: Mapped[str] = mapped_column(String(20))
    salt: Mapped[str] = mapped_column(String(32))
    rng_seed: Mapped[int] = mapped_column(BigInteger)
    noises: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('dataset_id', 'epsilon', 'delta', name='uq_noise_cache_params'),)


class PrivacyRelease(Base):
    """一次差分隐私发布；reserved 落库后即可凭幂等键恢复，重试不重复扣费。"""
    __tablename__ = 'privacy_releases'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    budget_id: Mapped[int | None] = mapped_column(ForeignKey('privacy_budgets.id'), nullable=True)
    cache_id: Mapped[int] = mapped_column(ForeignKey('dp_noise_caches.id'))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    epsilon: Mapped[float] = mapped_column(Double)
    delta: Mapped[float] = mapped_column(Double)
    mechanism: Mapped[str] = mapped_column(String(20))
    min_support: Mapped[float] = mapped_column(Double)
    min_confidence: Mapped[float] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(20), default='reserved')
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_filename: Mapped[str] = mapped_column(String(200), default='')
    error: Mapped[str] = mapped_column(String(1000), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    published_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class PrivacyLedger(Base):
    """预算扣减流水：charge 为真实扣费，reuse 为复用噪声的零扣费记录。"""
    __tablename__ = 'privacy_ledger'
    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey('privacy_budgets.id'))
    release_id: Mapped[int | None] = mapped_column(ForeignKey('privacy_releases.id'), nullable=True)
    cache_id: Mapped[int | None] = mapped_column(ForeignKey('dp_noise_caches.id'), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    epsilon_spend: Mapped[float] = mapped_column(Double)
    delta_spend: Mapped[float] = mapped_column(Double)
    actor: Mapped[str] = mapped_column(String(150))
    note: Mapped[str] = mapped_column(String(200), default='')
    created_at: Mapped[float] = mapped_column(Double)
