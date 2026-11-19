import csv
import io
import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.user import (
    UserImportItem,
    UserImportRequest,
    UserImportResultItem,
    UserImportSummary,
)
from app.utils.sanitizers import clean_digits


def normalize_phone_number(raw_phone: str | None) -> str | None:
    """Normaliza telefone para formato brasileiro canônico E.164 (ex: 5585999999999)."""
    if not raw_phone:
        return None
    digits = clean_digits(str(raw_phone))
    if not digits:
        return None

    # Se começa com 0, remove o zero à esquerda do DDD (ex: 085999999999 -> 85999999999)
    if digits.startswith("0") and len(digits) in (11, 12):
        digits = digits[1:]

    # DDD + 8 ou 9 dígitos sem DDI 55
    if len(digits) in (10, 11):
        digits = f"55{digits}"
    elif len(digits) in (8, 9):
        # Número local sem DDD não pode ser normalizado seguramente
        return None

    # Deve ter 12 ou 13 dígitos com DDI 55
    if len(digits) in (12, 13) and digits.startswith("55"):
        return digits

    # Outros formatos internacionais (mínimo 10 dígitos)
    if len(digits) >= 10:
        return digits

    return None


def parse_date_flexible(val: Any) -> date | None:
    """Converte datas em formatos ISO ou brasileiros para date."""
    if not val:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()

    val_str = str(val).strip()
    if not val_str:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val_str, fmt).date()
        except ValueError:
            continue
    return None


def parse_tags_flexible(raw_tags: Any) -> list[str]:
    """Converte strings ou listas de tags em lista limpa e deduplicada."""
    if not raw_tags:
        return []
    if isinstance(raw_tags, list):
        items = raw_tags
    else:
        # Divide por vírgula, ponto e vírgula ou pipe
        items = re.split(r"[,;|]", str(raw_tags))

    result: list[str] = []
    seen = set()
    for item in items:
        cleaned = str(item).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return result


def _extract_col_value(normalized_row: dict[str, str], *aliases: str) -> str | None:
    """Extrai valor da linha normalizada com base nos apelidos semânticos."""
    for a in aliases:
        cleaned_alias = re.sub(r"[_\s\-]+", "", a.lower())
        if normalized_row.get(cleaned_alias):
            return normalized_row[cleaned_alias]
    return None


class ClientImportService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = UserRepository(db)

    def import_items(self, request: UserImportRequest) -> UserImportSummary:
        batch_id = request.import_batch_id or f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        created = 0
        updated = 0
        skipped = 0
        errors = 0
        details: list[UserImportResultItem] = []

        for idx, item in enumerate(request.items, start=1):
            name = (item.name or "").strip()
            if not name:
                errors += 1
                details.append(
                    UserImportResultItem(
                        index=idx,
                        phone=item.phone,
                        name=name,
                        status="error",
                        message="Nome do cliente é obrigatório",
                    )
                )
                continue

            normalized_phone = normalize_phone_number(item.phone)
            if not normalized_phone:
                errors += 1
                details.append(
                    UserImportResultItem(
                        index=idx,
                        phone=item.phone,
                        name=name,
                        status="error",
                        message=f"Telefone inválido: '{item.phone}'. Deve conter DDD e número válido.",
                    )
                )
                continue

            # Limpeza de campos opcionais
            email = (item.email or "").strip().lower() or None
            cpf_cnpj = clean_digits(item.cpf_cnpj or "") or None
            whatsapp_number = normalize_phone_number(item.whatsapp_number) or normalized_phone
            birth_date = parse_date_flexible(item.birth_date)
            tags = parse_tags_flexible(item.tags)
            notes = (item.notes or "").strip() or None
            custom_fields = item.custom_fields or {}
            source = (item.source or request.source or "import").strip()
            external_id = (item.external_id or "").strip() or None

            # Localiza se já existe pelo telefone ou external_id (quando mesmo source)
            existing_user = self.repo.find_by_phone(normalized_phone)
            if not existing_user and external_id:
                existing_user = (
                    self.db.query(User)
                    .filter(User.source == source, User.external_id == external_id)
                    .first()
                )

            if existing_user:
                if request.deduplication_strategy == "skip":
                    skipped += 1
                    details.append(
                        UserImportResultItem(
                            index=idx,
                            phone=normalized_phone,
                            name=name,
                            status="skipped",
                            user_id=existing_user.id,
                            message=f"Cliente já existe (ID #{existing_user.id})",
                        )
                    )
                    continue

                if request.deduplication_strategy == "error":
                    errors += 1
                    details.append(
                        UserImportResultItem(
                            index=idx,
                            phone=normalized_phone,
                            name=name,
                            status="error",
                            user_id=existing_user.id,
                            message=f"Telefone já cadastrado para o cliente '{existing_user.name}' (ID #{existing_user.id})",
                        )
                    )
                    continue

                # Estratégia 'update' (Upsert / Mescla)
                # Verifica conflito de email
                if email and email != existing_user.email:
                    email_in_use = (
                        self.db.query(User)
                        .filter(User.email == email, User.id != existing_user.id)
                        .first()
                    )
                    if email_in_use:
                        email = existing_user.email  # Preserva o original para não colidir

                # Mescla tags
                existing_tags = parse_tags_flexible(existing_user.tags)
                merged_tags = parse_tags_flexible(existing_tags + tags)

                # Mescla custom fields
                merged_custom_fields = dict(existing_user.custom_fields or {})
                merged_custom_fields.update(custom_fields)

                # Mescla notas
                merged_notes = existing_user.notes
                if notes:
                    merged_notes = f"{existing_user.notes}\n{notes}".strip() if existing_user.notes else notes

                existing_user.name = name or existing_user.name
                existing_user.email = email or existing_user.email
                existing_user.whatsapp_number = whatsapp_number or existing_user.whatsapp_number
                existing_user.cpf_cnpj = cpf_cnpj or existing_user.cpf_cnpj
                existing_user.birth_date = birth_date or existing_user.birth_date
                existing_user.gender = item.gender or existing_user.gender
                existing_user.postal_code = item.postal_code or existing_user.postal_code
                existing_user.city = item.city or existing_user.city
                existing_user.state = item.state or existing_user.state
                existing_user.address = item.address or existing_user.address
                existing_user.address_number = item.address_number or existing_user.address_number
                existing_user.address_complement = item.address_complement or existing_user.address_complement
                existing_user.neighborhood = item.neighborhood or existing_user.neighborhood
                existing_user.delivery_instructions = item.delivery_instructions or existing_user.delivery_instructions
                existing_user.preferred_bank = item.preferred_bank or existing_user.preferred_bank
                if item.opt_in_whatsapp is not None:
                    existing_user.opt_in_whatsapp = item.opt_in_whatsapp
                existing_user.tags = merged_tags
                existing_user.notes = merged_notes
                existing_user.custom_fields = merged_custom_fields
                existing_user.import_batch_id = batch_id
                if external_id:
                    existing_user.external_id = external_id

                self.db.commit()
                updated += 1
                details.append(
                    UserImportResultItem(
                        index=idx,
                        phone=normalized_phone,
                        name=name,
                        status="updated",
                        user_id=existing_user.id,
                        message="Cliente atualizado com sucesso",
                    )
                )
            else:
                # Criação de novo cliente
                # Verifica unicidade do email
                if email:
                    email_in_use = self.db.query(User).filter(User.email == email).first()
                    if email_in_use:
                        email = None  # Evita quebrar o insert

                new_user = User(
                    name=name,
                    phone=normalized_phone,
                    email=email,
                    whatsapp_number=whatsapp_number,
                    cpf_cnpj=cpf_cnpj,
                    source=source,
                    external_id=external_id,
                    import_batch_id=batch_id,
                    tags=tags,
                    notes=notes,
                    custom_fields=custom_fields,
                    birth_date=birth_date,
                    gender=item.gender,
                    postal_code=item.postal_code,
                    city=item.city,
                    state=item.state,
                    address=item.address,
                    address_number=item.address_number,
                    address_complement=item.address_complement,
                    neighborhood=item.neighborhood,
                    delivery_instructions=item.delivery_instructions,
                    preferred_bank=item.preferred_bank,
                    opt_in_whatsapp=item.opt_in_whatsapp if item.opt_in_whatsapp is not None else True,
                )
                self.db.add(new_user)
                self.db.commit()
                self.db.refresh(new_user)
                created += 1
                details.append(
                    UserImportResultItem(
                        index=idx,
                        phone=normalized_phone,
                        name=name,
                        status="created",
                        user_id=new_user.id,
                        message="Cliente criado com sucesso",
                    )
                )

        return UserImportSummary(
            total=len(request.items),
            created=created,
            updated=updated,
            skipped=skipped,
            errors=errors,
            import_batch_id=batch_id,
            details=details,
        )

    def import_csv_content(
        self,
        csv_text: str,
        source: str = "import_csv",
        deduplication_strategy: str = "update",
        import_batch_id: str | None = None,
    ) -> UserImportSummary:
        """Processa texto CSV de planilhas exportadas de outras plataformas."""
        if not csv_text or not csv_text.strip():
            return UserImportSummary(
                total=0,
                created=0,
                updated=0,
                skipped=0,
                errors=0,
                import_batch_id=import_batch_id or "",
                details=[],
            )

        # Detecta delimitador (, ou ; ou \t)
        sample = csv_text[:2048]
        delimiter = ","
        for cand in [";", "\t", ","]:
            if cand in sample:
                delimiter = cand
                break

        reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)

        # Mapeamento semântico flexível de colunas
        items: list[UserImportItem] = []
        for row in reader:
            normalized_row = {
                re.sub(r"[_\s\-]+", "", str(k).strip().lower()): str(v).strip()
                for k, v in row.items()
                if k is not None
            }

            name = _extract_col_value(normalized_row, "nome", "name", "clientenome", "contato", "razaosocial") or ""
            phone = _extract_col_value(normalized_row, "telefone", "celular", "phone", "whatsapp", "mobile", "tel") or ""
            email = _extract_col_value(normalized_row, "email", "e-mail", "correioeletronico")
            cpf_cnpj = _extract_col_value(normalized_row, "cpf", "cnpj", "cpfcnpj", "documento")
            notes = _extract_col_value(normalized_row, "observacoes", "observacao", "notes", "nota", "historico")
            tags = _extract_col_value(normalized_row, "tags", "etiquetas", "marcadores", "categorias")
            city = _extract_col_value(normalized_row, "cidade", "city", "municipio")
            state = _extract_col_value(normalized_row, "estado", "state", "uf")
            postal_code = _extract_col_value(normalized_row, "cep", "postalcode", "zipcode")
            address = _extract_col_value(normalized_row, "endereco", "address", "logradouro", "rua")
            address_number = _extract_col_value(normalized_row, "numero", "number", "num")
            address_complement = _extract_col_value(normalized_row, "complemento", "complement", "comp", "apto")
            neighborhood = _extract_col_value(normalized_row, "bairro", "neighborhood", "district")
            delivery_instructions = _extract_col_value(normalized_row, "instrucoes", "deliveryinstructions", "pontoreferencia")
            preferred_bank = _extract_col_value(normalized_row, "banco", "preferredbank", "bancopreferido")
            birth_date = _extract_col_value(normalized_row, "nascimento", "datanascimento", "birthdate", "aniversario")
            gender = _extract_col_value(normalized_row, "genero", "gender", "sexo")
            external_id = _extract_col_value(normalized_row, "id", "codigo", "externalid", "idexterno", "codigocliente")

            items.append(
                UserImportItem(
                    name=name,
                    phone=phone,
                    email=email,
                    cpf_cnpj=cpf_cnpj,
                    notes=notes,
                    tags=tags,
                    city=city,
                    state=state,
                    postal_code=postal_code,
                    address=address,
                    address_number=address_number,
                    address_complement=address_complement,
                    neighborhood=neighborhood,
                    delivery_instructions=delivery_instructions,
                    preferred_bank=preferred_bank,
                    birth_date=birth_date,
                    gender=gender,
                    external_id=external_id,
                    source=source,
                )
            )

        import_req = UserImportRequest(
            source=source,
            import_batch_id=import_batch_id,
            deduplication_strategy=deduplication_strategy,  # type: ignore
            items=items,
        )
        return self.import_items(import_req)
