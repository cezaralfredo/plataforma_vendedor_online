from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.company_settings import CompanySettings
from app.models.product import Product
from tests.test_admin_login_flow import login, session_data


def test_products_and_settings_require_admin_authentication(client: TestClient):
    # GET /admin/products sem login
    res_prod = client.get("/admin/products", follow_redirects=False)
    assert res_prod.status_code in {303, 307, 401, 403}

    # GET /admin/settings sem login
    res_sett = client.get("/admin/settings", follow_redirects=False)
    assert res_sett.status_code in {303, 307, 401, 403}


def test_admin_products_page_renders_with_sidebar_and_data(
    client: TestClient, db_session: Session
):
    login(client)

    # Cria um produto no banco
    product = Product(
        name="Shampoo Hidratante Premium",
        description="Fórmula exclusiva",
        category="Cabelo",
        price_cents=4500,
        stock_quantity=10,
        image_url="https://imagens.exemplo.com/shampoo.jpg",
        active=True,
    )
    db_session.add(product)
    db_session.commit()

    res = client.get("/admin/products")
    assert res.status_code == 200
    assert "Catálogo de Produtos" in res.text
    assert "Shampoo Hidratante Premium" in res.text
    assert "https://imagens.exemplo.com/shampoo.jpg" in res.text
    assert "URL da Imagem / Foto do Produto" in res.text
    assert "href=\"/admin/products\"" in res.text
    assert "href=\"/admin/settings\"" in res.text


def test_admin_settings_page_renders_with_tabs(
    client: TestClient, db_session: Session
):
    login(client)

    res = client.get("/admin/settings")
    assert res.status_code == 200
    assert "Configurações da Empresa" in res.text
    assert "Financeiro & Pagamentos" in res.text
    assert "Atendimento & IA" in res.text
    assert "Canais WhatsApp" in res.text
    assert "Comissões" in res.text


def test_admin_product_mutations_require_csrf_protection(
    client: TestClient, db_session: Session
):
    login(client)

    # Tentativa de criar produto sem cabeçalho X-CSRF-Token deve ser rejeitada com 403
    res = client.post(
        "/admin/api/products",
        json={"name": "Produto Hacker", "price_cents": 1000, "stock_quantity": 5},
    )
    assert res.status_code == 403

    # Tentativa de alterar settings sem cabeçalho X-CSRF-Token deve ser rejeitada com 403
    res_sett = client.post(
        "/admin/api/settings",
        json={"company_name": "Empresa Invadida"},
    )
    assert res_sett.status_code == 403


def test_admin_product_lifecycle_with_valid_csrf(
    client: TestClient, db_session: Session
):
    login(client)
    csrf = session_data(client)["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    # 1. Criar produto
    create_payload = {
        "name": "Pomada Modeladora Matte",
        "description": "Fixação média",
        "category": "Cabelo",
        "price_cents": 5000,
        "stock_quantity": 20,
        "sku": "POM-001",
        "image_url": "https://imagens.exemplo.com/pomada.jpg",
    }
    res_create = client.post(
        "/admin/api/products",
        headers=headers,
        json=create_payload,
    )
    assert res_create.status_code == 200
    created_id = res_create.json()["id"]
    assert created_id is not None
    assert res_create.json()["name"] == "Pomada Modeladora Matte"
    assert res_create.json()["image_url"] == "https://imagens.exemplo.com/pomada.jpg"

    # 2. Atualizar produto
    update_payload = {
        "price_cents": 5500,
        "name": "Pomada Modeladora Matte Gold",
        "image_url": "https://imagens.exemplo.com/pomada-gold.jpg",
    }
    res_update = client.put(
        f"/admin/api/products/{created_id}",
        headers=headers,
        json=update_payload,
    )
    assert res_update.status_code == 200
    assert res_update.json()["price_cents"] == 5500
    assert res_update.json()["name"] == "Pomada Modeladora Matte Gold"
    assert res_update.json()["image_url"] == "https://imagens.exemplo.com/pomada-gold.jpg"

    # 3. Ajustar estoque (+5)
    res_stock = client.post(
        f"/admin/api/products/{created_id}/stock",
        headers=headers,
        json={"quantity_delta": 5},
    )
    assert res_stock.status_code == 200
    assert res_stock.json()["stock_quantity"] == 25

    # 4. Alternar status ativo/arquivado
    res_toggle = client.post(
        f"/admin/api/products/{created_id}/toggle-active",
        headers=headers,
    )
    assert res_toggle.status_code == 200
    assert res_toggle.json()["active"] is False


def test_admin_settings_update_persists_in_database(
    client: TestClient, db_session: Session
):
    login(client)
    csrf = session_data(client)["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    update_payload = {
        "company_name": "Studio & Barbearia Elite",
        "assistant_name": "Helena",
        "tone": "formal",
        "require_cpf": True,
        "payment_required": False,
        "default_gateway": "manual",
        "whatsapp_provider": "meta",
        "meta_phone_number_id": "9988776655",
        "commission_model": "percentage",
        "default_commission_rate": 35.5,
    }

    res = client.post(
        "/admin/api/settings",
        headers=headers,
        json=update_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["company_name"] == "Studio & Barbearia Elite"
    assert data["assistant_name"] == "Helena"
    assert data["payment_required"] is False
    assert data["whatsapp_provider"] == "meta"

    # Verifica persistência real no banco
    db_setting = db_session.query(CompanySettings).first()
    assert db_setting.company_name == "Studio & Barbearia Elite"
    assert db_setting.payment_required is False
    assert db_setting.default_gateway == "manual"


def test_admin_settings_update_with_bank_modalities_and_pix_copy_paste(
    client: TestClient, db_session: Session
):
    login(client)
    csrf = session_data(client)["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    update_payload = {
        "default_gateway": "inter",
        "accepted_payment_methods": ["pix", "credit_card", "debit_card", "boleto", "in_person", "cash"],
        "pix_copy_paste": "00020126330014br.gov.bcb.pix0111123456789005204000053039865802BR5912Anaue Design6009Sao Paulo62070503***630493E2",
        "pix_key": "12345678900",
        "pix_key_type": "cpf",
        "pix_merchant_name": "Anaue Design",
        "pix_bank_name": "Banco Inter",
        "pix_merchant_city": "Sao Paulo",
    }

    res = client.post(
        "/admin/api/settings",
        headers=headers,
        json=update_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["default_gateway"] == "inter"
    assert "debit_card" in data["accepted_payment_methods"]
    assert "boleto" in data["accepted_payment_methods"]
    assert data["pix_copy_paste"].startswith("00020126")
    assert data["pix_key"] == "12345678900"
    assert data["pix_bank_name"] == "Banco Inter"

    # Confirma que na página GET /admin/settings os dados chegam serializados no HTML
    get_res = client.get("/admin/settings")
    assert get_res.status_code == 200
    assert "Banco Inter" in get_res.text
    assert "00020126" in get_res.text


def test_admin_settings_waha_update_and_html_render(
    client: TestClient, db_session: Session
):
    login(client)
    csrf = session_data(client)["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    waha_payload = {
        "whatsapp_provider": "waha",
        "waha_base_url": "https://waha.anaue.com.br",
        "waha_session": "loja_principal",
        "waha_api_key": "waha-secret-token-999",
    }

    res = client.post(
        "/admin/api/settings",
        headers=headers,
        json=waha_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["whatsapp_provider"] == "waha"
    assert data["waha_base_url"] == "https://waha.anaue.com.br"
    assert data["waha_session"] == "loja_principal"
    assert data["waha_api_key"] == "waha-secret-token-999"

    # Confirma renderização do WAHA na página de configurações
    get_res = client.get("/admin/settings")
    assert get_res.status_code == 200
    assert "WAHA" in get_res.text
    assert "https://waha.anaue.com.br" in get_res.text


def test_admin_settings_ai_and_human_support_update_and_html_render(
    client: TestClient, db_session: Session
):
    login(client)
    csrf = session_data(client)["csrf_token"]
    headers = {"X-CSRF-Token": csrf}

    ai_payload = {
        "ai_provider": "anthropic",
        "ai_model": "claude-3-5-haiku-20241022",
        "ai_api_key": "sk-ant-api03-testkey123",
        "ai_base_url": "https://api.anthropic.com/v1",
        "ai_temperature": 0.45,
        "ai_system_prompt": "Você é a assistente virtual especializada do Salão Elegance.",
        "human_support_phone": "5585999887766",
        "human_support_name": "Recepção VIP",
        "human_support_message": "Vou te transferir para nossa recepcionista agora mesmo!",
    }

    res = client.post(
        "/admin/api/settings",
        headers=headers,
        json=ai_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ai_provider"] == "anthropic"
    assert data["ai_model"] == "claude-3-5-haiku-20241022"
    assert data["ai_api_key"] == "sk-ant-api03-testkey123"
    assert float(data["ai_temperature"]) == 0.45
    assert data["ai_system_prompt"] == "Você é a assistente virtual especializada do Salão Elegance."
    assert data["human_support_phone"] == "5585999887766"
    assert data["human_support_name"] == "Recepção VIP"
    assert data["human_support_message"] == "Vou te transferir para nossa recepcionista agora mesmo!"

    # Verifica persistência no banco
    db_setting = db_session.query(CompanySettings).first()
    assert db_setting.ai_provider == "anthropic"
    assert db_setting.ai_model == "claude-3-5-haiku-20241022"
    assert db_setting.human_support_phone == "5585999887766"
    assert db_setting.human_support_name == "Recepção VIP"

    # Confirma que na página GET /admin/settings os dados chegam no HTML
    get_res = client.get("/admin/settings")
    assert get_res.status_code == 200
    assert "anthropic" in get_res.text
    assert "claude-3-5-haiku-20241022" in get_res.text
    assert "5585999887766" in get_res.text
    assert "Recep" in get_res.text
    assert "Provedor de Inteligência Artificial &amp; Modelo LLM" in get_res.text or "Provedor de Inteligência Artificial & Modelo LLM" in get_res.text
    assert "Contato para Atendimento Humano (Transbordo &amp; Suporte)" in get_res.text or "Contato para Atendimento Humano (Transbordo & Suporte)" in get_res.text

