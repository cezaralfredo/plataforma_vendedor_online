from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.config import settings


def test_migration_e9f0a1b2c3d4_upgrade_and_downgrade(tmp_path, monkeypatch):
    db_file = tmp_path / "test_phase3_migration.db"
    database_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    project_root = Path(__file__).resolve().parents[1]

    config = Config()
    config.set_main_option("script_location", str(project_root / "alembic"))
    monkeypatch.setattr(settings, "database_url", database_url)

    # 1. Cria schema base equivalente ao estado d8e9f0a1b2c3
    with engine.begin() as conn:
        conn.execute(
            text(
                """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            phone VARCHAR(20) NOT NULL UNIQUE,
            email VARCHAR(255) UNIQUE,
            whatsapp_number VARCHAR(20),
            cpf_cnpj VARCHAR(20),
            asaas_customer_id VARCHAR(50),
            active BOOLEAN NOT NULL DEFAULT 1,
            source VARCHAR(50) NOT NULL DEFAULT 'direct',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        """
            )
        )
        conn.execute(
            text(
                """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            category VARCHAR(100),
            price_cents INTEGER NOT NULL,
            stock_quantity INTEGER NOT NULL DEFAULT 0,
            sku VARCHAR(50),
            image_url VARCHAR(500),
            active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT check_product_price CHECK (price_cents >= 0),
            CONSTRAINT check_product_stock CHECK (stock_quantity >= 0)
        );
        """
            )
        )
        conn.execute(
            text(
                """
        CREATE TABLE company_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name VARCHAR(100) NOT NULL DEFAULT 'Minha Empresa',
            payment_required BOOLEAN NOT NULL DEFAULT 1,
            default_gateway VARCHAR(30) NOT NULL DEFAULT 'asaas',
            gateway_credentials JSON NOT NULL DEFAULT '{}',
            accepted_payment_methods JSON NOT NULL DEFAULT '["pix"]',
            commission_model VARCHAR(30) NOT NULL DEFAULT 'percentage',
            default_commission_rate NUMERIC(5, 2) NOT NULL DEFAULT 0.0,
            split_enabled BOOLEAN NOT NULL DEFAULT 0,
            whatsapp_provider VARCHAR(20) NOT NULL DEFAULT 'evolution',
            assistant_name VARCHAR(50) NOT NULL DEFAULT 'Assistente',
            tone VARCHAR(20) NOT NULL DEFAULT 'casual',
            require_cpf BOOLEAN NOT NULL DEFAULT 0,
            welcome_message TEXT,
            custom_settings JSON NOT NULL DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        """
            )
        )
        conn.execute(
            text(
                """
        CREATE TABLE appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            start_time DATETIME NOT NULL,
            end_time DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        """
            )
        )
        conn.execute(
            text(
                """
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appointment_id INTEGER NOT NULL REFERENCES appointments(id),
            asaas_payment_id VARCHAR(50) UNIQUE,
            amount_cents INTEGER NOT NULL,
            billing_type VARCHAR(20) NOT NULL DEFAULT 'pix',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            invoice_url VARCHAR(500),
            received_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT check_payment_status CHECK (status IN ('pending', 'received', 'confirmed', 'overdue', 'refunded', 'cancelled')),
            CONSTRAINT check_payment_amount CHECK (amount_cents >= 0),
            CONSTRAINT check_billing_type CHECK (billing_type IN ('pix', 'boleto', 'credit_card', 'undefined'))
        );
        """
            )
        )

    # 2. Marca banco na revisão d8e9f0a1b2c3
    command.stamp(config, "d8e9f0a1b2c3")

    # 3. Executa upgrade para e9f0a1b2c3d4
    command.upgrade(config, "e9f0a1b2c3d4")

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert "chat_messages" in table_names

    chat_cols = {c["name"] for c in inspector.get_columns("chat_messages")}
    assert {"id", "phone", "role", "content", "provider", "created_at"}.issubset(chat_cols)

    product_cols = {c["name"] for c in inspector.get_columns("products")}
    assert "weight_kg" in product_cols

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    assert {"address_number", "neighborhood", "delivery_instructions"}.issubset(user_cols)

    payment_cols = {c["name"] for c in inspector.get_columns("payments")}
    assert {"product_id", "user_id", "description"}.issubset(payment_cols)

    settings_cols = {c["name"] for c in inspector.get_columns("company_settings")}
    assert {"coexistence_enabled", "waha_base_url", "ai_provider"}.issubset(settings_cols)

    # 4. Testa inserção de dados em tabela chat_messages e payment avulso de produto
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_messages (phone, role, content, provider) "
                "VALUES ('5585999998888', 'user', 'Olá, gostaria de agendar', 'waha')"
            )
        )
        row = conn.execute(text("SELECT phone, role, content, provider FROM chat_messages")).one()
        assert row == ("5585999998888", "user", "Olá, gostaria de agendar", "waha")

        conn.execute(
            text(
                "INSERT INTO products (name, price_cents, stock_quantity, weight_kg) "
                "VALUES ('Máscara Capilar', 6500, 5, 0.500)"
            )
        )
        prod_row = conn.execute(text("SELECT name, weight_kg FROM products WHERE id = 1")).one()
        assert prod_row[0] == "Máscara Capilar"
        assert abs(float(prod_row[1]) - 0.5) < 0.001

        # Pagamento direto de produto sem appointment_id
        conn.execute(
            text(
                "INSERT INTO payments (product_id, amount_cents, billing_type, status, description) "
                "VALUES (1, 6500, 'pix', 'confirmed', 'Compra de Produto')"
            )
        )
        pay_row = conn.execute(text("SELECT product_id, appointment_id, description FROM payments WHERE id = 1")).one()
        assert pay_row[0] == 1
        assert pay_row[1] is None
        assert pay_row[2] == "Compra de Produto"

    # 5. Executa downgrade de volta para d8e9f0a1b2c3
    command.downgrade(config, "d8e9f0a1b2c3")

    inspector_after = inspect(engine)
    assert "chat_messages" not in inspector_after.get_table_names()
    assert "weight_kg" not in {c["name"] for c in inspector_after.get_columns("products")}
