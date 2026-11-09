import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate

logger = logging.getLogger(__name__)


class ProductService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: ProductCreate) -> Product:
        product = Product(**data.model_dump())
        self.db.add(product)
        self.db.commit()
        self.db.refresh(product)
        return product

    def get_by_id(self, product_id: int) -> Product | None:
        return self.db.query(Product).filter(Product.id == product_id).first()

    get_product = get_by_id

    def find_by_name(self, name: str) -> list[Product]:
        query = f"%{name.strip().lower()}%"
        return (
            self.db.query(Product)
            .filter(func.lower(Product.name).like(query), Product.active.is_(True))
            .all()
        )

    def list_products(
        self,
        category: str | None = None,
        search: str | None = None,
        active_only: bool = True,
        in_stock_only: bool = False,
    ) -> list[Product]:
        q = self.db.query(Product)
        if active_only:
            q = q.filter(Product.active.is_(True))
        if in_stock_only:
            q = q.filter(Product.stock_quantity > 0)
        if category:
            q = q.filter(func.lower(Product.category) == category.strip().lower())
        if search:
            search_pattern = f"%{search.strip().lower()}%"
            q = q.filter(
                (func.lower(Product.name).like(search_pattern))
                | (func.lower(Product.description).like(search_pattern))
            )
        return q.order_by(Product.category, Product.name).all()

    def list_categories(self) -> list[str]:
        rows = (
            self.db.query(Product.category)
            .filter(Product.active.is_(True), Product.category.isnot(None))
            .distinct()
            .order_by(Product.category)
            .all()
        )
        return [row[0] for row in rows if row[0]]

    def update(self, product_id: int, data: ProductUpdate) -> Product | None:
        product = self.get_by_id(product_id)
        if not product:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(product, key, value)
        self.db.commit()
        self.db.refresh(product)
        return product

    def adjust_stock(self, product_id: int, quantity_delta: int) -> Product:
        product = self.get_by_id(product_id)
        if not product:
            raise ValueError(f"Produto #{product_id} não encontrado")
        new_stock = product.stock_quantity + quantity_delta
        if new_stock < 0:
            raise ValueError(
                f"Estoque insuficiente para o produto '{product.name}'. "
                f"Disponível: {product.stock_quantity}, Solicitado: {abs(quantity_delta)}"
            )
        product.stock_quantity = new_stock
        self.db.commit()
        self.db.refresh(product)
        return product
