"""Inventory photo model for user-uploaded item images."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InventoryPhoto(Base):
    __tablename__ = "inventory_photos"

    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    item: Mapped["InventoryItem"] = relationship(back_populates="photos")


from app.models.inventory import InventoryItem  # noqa: E402
