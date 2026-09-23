"""SQLAlchemy persistence primitives for Orders."""

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Engine, Integer, String, Uuid, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from nexus.contracts import CreateOrderRequest


class Base(DeclarativeBase):
    """Orders SQLAlchemy declarative base."""


class OrderRecord(Base):
    """Persisted order database record."""

    __tablename__ = "orders"

    order_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    item: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class OrdersDatabase:
    """Own the Orders engine and unit-of-work sessions."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def ping(self) -> None:
        """Raise when the database cannot execute a trivial query."""

        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def sessions(self) -> Generator[Session]:
        """Yield a transactional session for a request."""

        with self.session_factory() as session:
            yield session

    def dispose(self) -> None:
        """Release pooled database connections."""

        self.engine.dispose()


class OrderRepository:
    """Persistence operations for order records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, request: CreateOrderRequest) -> OrderRecord:
        record = OrderRecord(**request.model_dump())
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record

    def get(self, order_id: UUID) -> OrderRecord | None:
        return self.session.scalar(select(OrderRecord).where(OrderRecord.order_id == order_id))
