import logging

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.business_time import as_business_time
from app.models.appointment import Appointment
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.schemas.appointment import AppointmentCreate
from app.schemas.user import UserCreate, UserUpdate
from app.services.appointment_service import AppointmentService
from app.services.asaas_client import AsaasIntegrationError
from app.services.availability_service import AvailabilityService
from app.services.payment_service import PaymentService
from app.services.product_service import ProductService
from app.services.professional_service_offering_service import ProfessionalOfferingService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "buscar_cliente_por_telefone",
        "description": "Busca um cliente cadastrado pelo número de telefone/WhatsApp, nome completo ou CPF/CNPJ",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Número de telefone ou WhatsApp (opcional)"},
                "name": {"type": "string", "description": "Nome completo ou parcial do cliente (opcional)"},
                "cpf_cnpj": {"type": "string", "description": "CPF ou CNPJ do cliente (opcional)"},
            },
        },
    },
    {
        "name": "cadastrar_cliente",
        "description": "Cadastra um novo cliente",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome completo do cliente"},
                "phone": {"type": "string", "description": "Número de telefone no formato 55XXXXXXXXXXX"},
                "email": {"type": "string", "description": "Email do cliente (opcional)"},
                "whatsapp_number": {"type": "string", "description": "Número do WhatsApp no formato 55XXXXXXXXXXX (opcional)"},
                "cpf_cnpj": {"type": "string", "description": "CPF ou CNPJ do cliente (opcional, apenas números ou formatado)"},
            },
            "required": ["name", "phone"],
        },
    },
    {
        "name": "atualizar_cliente",
        "description": "Atualiza dados de um cliente existente",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "ID do cliente"},
                "name": {"type": "string", "description": "Nome completo (opcional)"},
                "phone": {"type": "string", "description": "Telefone (opcional)"},
                "email": {"type": "string", "description": "Email (opcional)"},
                "whatsapp_number": {"type": "string", "description": "WhatsApp (opcional)"},
                "cpf_cnpj": {"type": "string", "description": "CPF ou CNPJ (opcional)"},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "vincular_whatsapp",
        "description": "Vincula um número de WhatsApp a um cliente existente",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "ID do cliente"},
                "whatsapp_number": {"type": "string", "description": "Número do WhatsApp no formato 55XXXXXXXXXXX"},
            },
            "required": ["user_id", "whatsapp_number"],
        },
    },
    {
        "name": "listar_profissionais",
        "description": "Lista todos os profissionais cadastrados na plataforma com seus IDs, especialidades e serviços que realizam",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome ou parte do nome do profissional (opcional)"},
                "service_id": {"type": "integer", "description": "ID do serviço para filtrar profissionais que o realizam (opcional)"},
            },
        },
    },
    {
        "name": "listar_servicos",
        "description": "Lista serviços disponíveis por profissional ou categoria",
        "inputSchema": {
            "type": "object",
            "properties": {
                "professional_id": {"description": "ID do profissional (número) ou nome do profissional (opcional)"},
                "category": {"type": "string", "description": "Categoria do serviço (opcional)"},
            },
        },
    },
    {
        "name": "listar_produtos",
        "description": "Lista produtos físicos disponíveis para venda no estabelecimento por categoria ou termo de busca, com preço e estoque",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Categoria do produto (ex: 'Cabelo', 'Barba', 'Homecare') (opcional)"},
                "search": {"type": "string", "description": "Termo de busca pelo nome ou descrição (opcional)"},
                "in_stock_only": {"type": "boolean", "description": "Se verdadeiro, lista apenas itens com estoque > 0 (padrão: true)"},
            },
        },
    },
    {
        "name": "consultar_produto",
        "description": "Consulta detalhes, preço e estoque de um produto específico por ID ou nome",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "ID do produto (opcional)"},
                "name": {"type": "string", "description": "Nome ou parte do nome do produto (opcional)"},
            },
        },
    },
    {
        "name": "verificar_disponibilidade",
        "description": "Verifica horários livres de um profissional em uma data específica. Pode informar o professional_id ou o professional_name, e opcionalmente o service_id para listar os horários exatos de início",
        "inputSchema": {
            "type": "object",
            "properties": {
                "professional_id": {"description": "ID do profissional (número) ou nome do profissional (opcional se professional_name informado)"},
                "professional_name": {"type": "string", "description": "Nome ou parte do nome do profissional (ex: 'Marilde Vieira')"},
                "date": {"type": "string", "description": "Data no formato YYYY-MM-DD (ex: 2026-09-23)"},
                "service_id": {"description": "ID ou nome do serviço (opcional). Quando informado, calcula e retorna os horários de início exatos para esse serviço."},
            },
            "required": ["date"],
        },
    },
    {
        "name": "criar_reserva",
        "description": "Cria uma nova reserva (agendamento) para um cliente",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "ID do cliente"},
                "professional_id": {"type": "integer", "description": "ID do profissional"},
                "service_id": {"type": "integer", "description": "ID do serviço"},
                "start_time": {"type": "string", "description": "Horário início (ISO 8601)"},
                "end_time": {"type": "string", "description": "Horário fim (ISO 8601)"},
                "notes": {"type": "string", "description": "Observações (opcional)"},
            },
            "required": ["user_id", "professional_id", "service_id", "start_time", "end_time"],
        },
    },
    {
        "name": "cancelar_reserva",
        "description": "Cancela uma reserva existente",
        "inputSchema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "ID do agendamento"},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "criar_cobranca_asaas",
        "description": "Cria uma cobrança no Asaas para um agendamento e retorna o link de pagamento",
        "inputSchema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "ID do agendamento"},
                "billing_type": {
                    "type": "string",
                    "description": "Tipo: PIX, BOLETO, CREDIT_CARD ou UNDEFINED",
                    "default": "UNDEFINED",
                },
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "verificar_pagamentos_recentes",
        "description": "Verifica pagamentos pendentes no Asaas e atualiza status dos que foram confirmados",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "listar_pendentes_notificacao",
        "description": "Lista agendamentos com pagamento confirmado que ainda não foram notificados ao cliente",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "marcar_notificado",
        "description": "Legado: a confirmação de pagamento é registrada automaticamente após entrega pelo WhatsApp.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "ID do agendamento"},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "meus_agendamentos",
        "description": "Lista os agendamentos de um cliente por ID, número de telefone/WhatsApp, nome ou CPF/CNPJ (oculta automaticamente reservas canceladas sem pagamento)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "ID do cliente (opcional se phone, name ou cpf_cnpj informado)"},
                "phone": {"type": "string", "description": "Número do telefone/WhatsApp do cliente (opcional)"},
                "name": {"type": "string", "description": "Nome completo ou parcial do cliente (opcional)"},
                "cpf_cnpj": {"type": "string", "description": "CPF ou CNPJ do cliente (opcional)"},
                "include_unpaid_cancelled": {"type": "boolean", "description": "Se verdadeiro, inclui reservas canceladas sem pagamento (padrão: false)"},
            },
        },
    },
    {
        "name": "verificar_status_pagamento",
        "description": "Verifica em tempo real o status de pagamento de um agendamento junto ao Asaas, confirma a reserva se pago e retorna mensagem com detalhes completos",
        "inputSchema": {
            "type": "object",
            "properties": {
                "appointment_id": {"type": "integer", "description": "ID do agendamento"},
            },
            "required": ["appointment_id"],
        },
    },
    {
        "name": "triar_mensagem",
        "description": "Realiza triagem ultrarrápida de uma mensagem de cliente usando o TypeSafe Jev. Detecta intenção, pedido de atendimento humano, afirmação de pagamento PIX e nível de frustração.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Texto da mensagem enviada pelo cliente"},
                "phone": {"type": "string", "description": "Telefone/WhatsApp do cliente (opcional)"},
            },
            "required": ["message"],
        },
    },
]


async def handle_tool_call(name: str, arguments: dict, db: Session) -> dict:
    try:
        if name == "buscar_cliente_por_telefone":
            user_service = UserService(db)
            phone = arguments.get("phone")
            name_arg = arguments.get("name")
            cpf_cnpj = arguments.get("cpf_cnpj")
            query = arguments.get("query")
            if not any([phone, name_arg, cpf_cnpj, query]):
                return {"content": [{"type": "text", "text": "Informe o telefone, nome ou CPF do cliente para a busca."}]}
            user = user_service.find_by_identifier(
                query=query, phone=phone, name=name_arg, cpf_cnpj=cpf_cnpj
            )
            if not user or not getattr(user, "active", True):
                return {"content": [{"type": "text", "text": "Cliente não encontrado."}]}
            return {"content": [{"type": "text", "text": _format_cliente(user)}]}

        elif name == "cadastrar_cliente":
            user_service = UserService(db)
            try:
                user_create = UserCreate(
                    name=arguments["name"],
                    phone=arguments["phone"],
                    email=arguments.get("email"),
                    whatsapp_number=arguments.get("whatsapp_number"),
                    cpf_cnpj=arguments.get("cpf_cnpj"),
                )
                user = user_service.create(user_create)
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Cliente cadastrado com sucesso!\n{_format_cliente(user)}",
                        }
                    ]
                }
            except ValueError as e:
                # Se já existe por telefone ou CPF, retornar o cadastro existente amigavelmente
                existing = user_service.find_by_identifier(
                    phone=arguments.get("phone"),
                    cpf_cnpj=arguments.get("cpf_cnpj"),
                    name=arguments.get("name"),
                )
                if existing:
                    return {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Aviso: {e!s}.\n"
                                    f"O cliente já possui cadastro ativo no sistema:\n{_format_cliente(existing)}"
                                ),
                            }
                        ],
                    }
                raise e

        elif name == "atualizar_cliente":
            user_service = UserService(db)
            fields = {"name", "phone", "email", "whatsapp_number", "cpf_cnpj"}
            user_update = UserUpdate(
                **{key: arguments[key] for key in fields if key in arguments}
            )
            user = user_service.update(arguments["user_id"], user_update)
            if not user:
                return {"content": [{"type": "text", "text": "Cliente não encontrado."}]}
            return {"content": [{"type": "text", "text": f"Cliente atualizado!\n{_format_cliente(user)}"}]}

        elif name == "vincular_whatsapp":
            user_service = UserService(db)
            user = user_service.link_whatsapp(arguments["user_id"], arguments["whatsapp_number"])
            if not user:
                return {"content": [{"type": "text", "text": "Cliente não encontrado."}]}
            return {"content": [{"type": "text", "text": f"WhatsApp vinculado com sucesso!\n{_format_cliente(user)}"}]}

        elif name == "listar_profissionais":
            query = (
                db.query(Professional)
                .options(
                    joinedload(Professional.service_offerings).joinedload(ProfessionalService.service)
                )
                .filter(Professional.active.is_(True))
            )

            search_name = arguments.get("name")
            if search_name:
                query = query.filter(func.lower(Professional.name).like(f"%{search_name.strip().lower()}%"))

            service_id = arguments.get("service_id")
            if service_id:
                query = query.join(Professional.service_offerings).filter(
                    ProfessionalService.service_id == service_id,
                    ProfessionalService.active.is_(True),
                )

            professionals = query.order_by(Professional.id).all()
            if not professionals:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Nenhum profissional encontrado com os critérios informados.",
                        }
                    ]
                }

            lines = ["Profissionais disponíveis:"]
            for p in professionals:
                servs = [
                    f"{off.service.name} (#{off.service_id})"
                    for off in p.service_offerings
                    if off.active and off.service and off.service.active
                ]
                servs_str = ", ".join(servs) if servs else "Nenhum serviço vinculado"
                lines.append(f"• ID #{p.id}: {p.name} | Serviços: {servs_str}")

            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "listar_servicos":
            prof_id = arguments.get("professional_id")
            if isinstance(prof_id, str):
                if prof_id.isdigit():
                    prof_id = int(prof_id)
                else:
                    prof_match = db.query(Professional).filter(
                        func.lower(Professional.name).like(f"%{prof_id.strip().lower()}%")
                    ).first()
                    prof_id = prof_match.id if prof_match else None

            offerings = ProfessionalOfferingService(db).list_active(
                professional_id=prof_id,
                category=arguments.get("category"),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": _format_servicos(
                            offerings,
                            include_professional=prof_id is None,
                        ),
                    }
                ]
            }

        elif name == "listar_produtos":
            product_service = ProductService(db)
            category = arguments.get("category")
            search = arguments.get("search")
            in_stock_only = arguments.get("in_stock_only", True)

            products = product_service.list_products(
                category=category,
                search=search,
                in_stock_only=in_stock_only,
            )

            if not products:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Nenhum produto encontrado com os filtros informados.",
                        }
                    ]
                }

            lines = ["Produtos disponíveis para venda:"]
            for prod in products:
                price = f"R$ {prod.price_cents / 100:.2f}".replace(".", ",")
                cat_tag = f" [{prod.category}]" if prod.category else ""
                lines.append(
                    f"  #{prod.id} {prod.name} - {price} (Estoque: {prod.stock_quantity}){cat_tag}"
                )
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "consultar_produto":
            product_service = ProductService(db)
            product_id = arguments.get("product_id")
            name_arg = arguments.get("name")

            product = None
            if product_id:
                product = product_service.get_by_id(int(product_id))
            elif name_arg:
                matches = product_service.find_by_name(name_arg)
                if matches:
                    product = matches[0]

            if not product:
                return {
                    "content": [
                        {"type": "text", "text": "Produto não encontrado no catálogo."}
                    ]
                }

            price = f"R$ {product.price_cents / 100:.2f}".replace(".", ",")
            desc = f"\n  Descrição: {product.description}" if product.description else ""
            cat = f"\n  Categoria: {product.category}" if product.category else ""
            status_est = (
                f"{product.stock_quantity} unidades disponíveis"
                if product.stock_quantity > 0
                else "Esgotado no momento"
            )

            text = (
                f"Produto #{product.id}: {product.name}\n"
                f"  Preço: {price}\n"
                f"  Estoque: {status_est}"
                f"{cat}"
                f"{desc}"
            )
            return {"content": [{"type": "text", "text": text}]}

        elif name == "verificar_disponibilidade":
            availability_service = AvailabilityService(db)
            prof_id = arguments.get("professional_id")
            prof_name = arguments.get("professional_name")

            if isinstance(prof_id, str):
                if prof_id.isdigit():
                    prof_id = int(prof_id)
                elif not prof_name:
                    prof_name = prof_id
                    prof_id = None

            prof = None
            if prof_id is not None:
                prof = db.get(Professional, prof_id)
            elif prof_name:
                prof = db.query(Professional).filter(
                    func.lower(Professional.name).like(f"%{prof_name.strip().lower()}%")
                ).first()
                if prof:
                    prof_id = prof.id

            if not prof or prof_id is None:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Profissional '{prof_name or prof_id}' não encontrado.",
                        }
                    ]
                }

            # Resolver service_id se fornecido
            service_val = arguments.get("service_id")
            service_id = None
            service_obj = None
            if service_val is not None:
                if isinstance(service_val, int) or (isinstance(service_val, str) and service_val.strip().isdigit()):
                    service_id = int(service_val)
                    service_obj = db.get(Service, service_id)
                else:
                    service_obj = db.query(Service).filter(
                        func.lower(Service.name).like(f"%{str(service_val).strip().lower()}%")
                    ).first()
                    if service_obj:
                        service_id = service_obj.id

            if service_id and service_obj:
                slots = availability_service.get_time_slots_for_service(
                    professional_id=prof_id,
                    service_id=service_id,
                    date_str=arguments["date"],
                )
                if not slots:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Não há horários disponíveis para {prof.name} em {arguments['date']} para o serviço '{service_obj.name}' ({service_obj.duration_minutes} min).",
                            }
                        ]
                    }
                start_times = [as_business_time(s.start).strftime("%H:%M") for s in slots]
                last_time = start_times[-1]
                text = (
                    f"Horários de início disponíveis para {prof.name} (ID: {prof.id}) em {arguments['date']}\n"
                    f"Serviço: {service_obj.name} (duração: {service_obj.duration_minutes} min):\n"
                    f"  " + ", ".join(start_times) + "\n"
                    f"(Atenção: o último horário de início disponível no dia é às {last_time}, pois o expediente encerra após este atendimento)."
                )
                return {"content": [{"type": "text", "text": text}]}

            slots = availability_service.check_availability(
                professional_id=prof_id,
                date_str=arguments["date"],
            )
            if not slots:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Nenhum horário de atendimento para {prof.name} (ID: {prof.id}) na data {arguments['date']}.",
                        }
                    ]
                }
            lines = [f"Expediente/Horários de atendimento para {prof.name} (ID: {prof.id}) em {arguments['date']}:"]
            for s in slots:
                start_fmt = as_business_time(s.start).strftime("%H:%M")
                end_fmt = as_business_time(s.end).strftime("%H:%M")
                lines.append(f"  Das {start_fmt} às {end_fmt} (Atenção: o expediente encerra pontualmente às {end_fmt}; o agendamento deve iniciar antes das {end_fmt}, respeitando a duração do serviço).")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "criar_reserva":
            appointment_service = AppointmentService(db)
            appointment_create = AppointmentCreate(
                user_id=arguments["user_id"],
                professional_id=arguments["professional_id"],
                service_id=arguments["service_id"],
                start_time=arguments["start_time"],
                end_time=arguments["end_time"],
                notes=arguments.get("notes"),
            )
            appointment = appointment_service.create(appointment_create)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Reserva criada! ID: {appointment.id}\n"
                            f"Status: {appointment.status}\n"
                            f"Expira em: {appointment.expires_at}"
                        ),
                    }
                ]
            }

        elif name == "cancelar_reserva":
            appointment_service = AppointmentService(db)
            appointment = appointment_service.cancel(arguments["appointment_id"])
            if not appointment:
                return {"content": [{"type": "text", "text": "Reserva não encontrada."}]}
            return {"content": [{"type": "text", "text": f"Reserva {appointment.id} cancelada com sucesso."}]}

        elif name == "criar_cobranca_asaas":
            payment_service = PaymentService(db)
            payment = await payment_service.create_charge(
                appointment_id=arguments["appointment_id"],
                billing_type=arguments.get("billing_type", "UNDEFINED"),
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Cobrança criada!\n"
                            f"ID Asaas: {payment.asaas_payment_id}\n"
                            f"Valor: R$ {payment.amount_cents / 100:.2f}\n"
                            f"Link: {payment.invoice_url}"
                        ),
                    }
                ]
            }

        elif name == "verificar_pagamentos_recentes":
            payment_service = PaymentService(db)
            updated = await payment_service.verify_recent_payments()
            unnotified = payment_service.list_unnotified_confirmed()

            lines = []
            if updated:
                lines.append("Pagamentos recém-atualizados no gateway:")
                lines.extend(f"  • Pagamento {p.id}: reserva {p.appointment_id} - {p.status}" for p in updated)
            if unnotified:
                lines.append(f"Reservas confirmadas aguardando aviso ao cliente ({len(unnotified)}):")
                for a in unnotified:
                    cli_nome = a.user.name if a.user else "Cliente"
                    cli_tel = getattr(a.user, "whatsapp_number", None) or getattr(a.user, "phone", "")
                    lines.append(f"  • Agendamento #{a.id} ({cli_nome} - {cli_tel}): {a.start_time}")

            if not lines:
                return {"content": [{"type": "text", "text": "Nenhum pagamento novo confirmado e nenhuma notificação pendente."}]}
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "listar_pendentes_notificacao":
            payment_service = PaymentService(db)
            unnotified = payment_service.list_unnotified_confirmed()
            if not unnotified:
                return {"content": [{"type": "text", "text": "Nenhum agendamento pendente de notificação."}]}
            lines = []
            for a in unnotified:
                cliente_nome = a.user.name if a.user else "Desconhecido"
                cliente_tel = getattr(a.user, "whatsapp_number", None) or getattr(a.user, "phone", "Sem telefone")
                servico = a.service.name if a.service else "Serviço"
                prof = a.professional.name if a.professional else "Profissional"
                lines.append(
                    f"• Agendamento #{a.id}: Cliente {cliente_nome} ({cliente_tel}) | "
                    f"Serviço: {servico} ({prof}) | Data/Hora: {a.start_time}"
                )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Encontrado(s) {len(unnotified)} agendamento(s) confirmado(s) aguardando notificação:\n" + "\n".join(lines),
                    }
                ]
            }

        elif name == "marcar_notificado":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "A confirmação é registrada automaticamente somente após o WhatsApp aceitar a mensagem.",
                    }
                ]
            }

        elif name == "meus_agendamentos":
            user_id = arguments.get("user_id")
            if not user_id:
                user_service = UserService(db)
                user = user_service.find_by_identifier(
                    query=arguments.get("query"),
                    phone=arguments.get("phone"),
                    name=arguments.get("name"),
                    cpf_cnpj=arguments.get("cpf_cnpj"),
                )
                if user:
                    user_id = user.id
            if not user_id:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "Não foi possível localizar o cliente. Por favor, forneça o número de telefone, nome ou CPF do cliente.",
                        }
                    ]
                }

            include_unpaid_cancelled = bool(arguments.get("include_unpaid_cancelled", False))
            appointments = (
                db.query(Appointment)
                .options(
                    joinedload(Appointment.service),
                    joinedload(Appointment.professional),
                    joinedload(Appointment.payments),
                )
                .filter(Appointment.user_id == user_id)
                .order_by(Appointment.start_time.desc())
                .all()
            )

            # Ocultar do histórico reservas canceladas sem pagamento recebido/confirmado
            visible_appointments = []
            for a in appointments:
                has_paid_payment = any(
                    p.status in {"received", "confirmed", "refunded"}
                    for p in (a.payments or [])
                )
                if a.status == "cancelled" and not has_paid_payment and not include_unpaid_cancelled:
                    continue
                visible_appointments.append(a)

            if not visible_appointments:
                return {"content": [{"type": "text", "text": "Nenhum agendamento encontrado."}]}

            lines = ["Seus agendamentos:"]
            for a in visible_appointments:
                serv_name = a.service.name if a.service else "Serviço"
                prof_name = a.professional.name if a.professional else "Profissional"
                data_hora = a.start_time.strftime("%d/%m/%Y às %H:%M")

                latest_payment = a.payments[-1] if a.payments else None
                pay_info = ""
                if latest_payment:
                    if latest_payment.status in {"received", "confirmed"}:
                        pay_info = " | Pagamento: Confirmado"
                    elif latest_payment.status in {"pending", "awaiting_payment"}:
                        link = latest_payment.invoice_url or "Link pendente"
                        pay_info = f" | Aguardando Pagamento (Link: {link})"
                    else:
                        pay_info = f" | Pagamento: {latest_payment.status}"

                status_label = "Confirmado" if a.status == "confirmed" else ("Cancelado" if a.status == "cancelled" else a.status)
                serv_id_str = f"Serviço #{a.service_id}" if a.service_id else "Sem serviço"
                prof_id_str = f"Profissional #{a.professional_id}" if a.professional_id else "Sem profissional"
                lines.append(f"• #{a.id}: {serv_name} ({serv_id_str}) com {prof_name} ({prof_id_str}) em {data_hora} | Status: {status_label}{pay_info}")

            return {"content": [{"type": "text", "text": "\n".join(lines)}]}

        elif name == "verificar_status_pagamento":
            appointment_id = arguments["appointment_id"]
            payment_service = PaymentService(db)
            payment = payment_service.get_payment_by_appointment(appointment_id)
            if not payment:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Nenhuma cobrança encontrada para o agendamento #{appointment_id}.",
                        }
                    ]
                }

            if payment.status in {"pending", "awaiting_payment"} and payment.asaas_payment_id:
                try:
                    await payment_service.check_payment_status(payment)
                except Exception as e:
                    logger.warning("Erro ao consultar status no Asaas para pagamento %s: %s", payment.id, e)

            appointment = (
                db.query(Appointment)
                .options(
                    joinedload(Appointment.user),
                    joinedload(Appointment.service),
                    joinedload(Appointment.professional),
                )
                .filter(Appointment.id == appointment_id)
                .first()
            )

            if payment.status in {"received", "confirmed"}:
                if appointment:
                    from app.services.notification_service import NotificationService

                    notification_service = NotificationService(db)
                    delivery = notification_service.queue_payment_confirmed(appointment.id, payment.id)
                    db.commit()
                    await notification_service.dispatch_payment_confirmed(delivery.id)

                cli_name = appointment.user.name if appointment and appointment.user else "Cliente"
                serv_name = appointment.service.name if appointment and appointment.service else "Serviço"
                prof_name = appointment.professional.name if appointment and appointment.professional else "Profissional"
                data_hora = appointment.start_time.strftime("%d/%m/%Y às %H:%M") if appointment else ""
                val = f"R$ {payment.amount_cents / 100:.2f}"
                forma = (payment.billing_type or "PIX").upper()

                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"✅ Pagamento CONFIRMADO com sucesso!\n\n"
                                f"• Cliente: {cli_name}\n"
                                f"• Agendamento: #{appointment_id}\n"
                                f"• Serviço: {serv_name}\n"
                                f"• Profissional: {prof_name}\n"
                                f"• Data e Horário: {data_hora}\n"
                                f"• Valor Pago: {val} ({forma})\n\n"
                                f"A reserva está 100% confirmada e garantida na agenda!"
                            ),
                        }
                    ]
                }
            elif payment.status in {"pending", "awaiting_payment"}:
                val = f"R$ {payment.amount_cents / 100:.2f}"
                invoice = payment.invoice_url or "Link não disponível"
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"⏳ Pagamento PENDENTE para o agendamento #{appointment_id}.\n"
                                f"• Valor: {val}\n"
                                f"• Link de pagamento: {invoice}\n\n"
                                f"Assim que o pagamento for concluído no banco, a confirmação será processada."
                            ),
                        }
                    ]
                }
            else:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Status do pagamento para o agendamento #{appointment_id}: {payment.status.upper()}.",
                        }
                    ]
                }

        elif name == "triar_mensagem":
            from app.services.triage_service import TriageService
            message_text = arguments.get("message", "")
            phone = arguments.get("phone")
            triage_service = TriageService()
            result = await triage_service.evaluate(message=message_text, phone=phone)
            human_str = "Sim" if result.is_human_handoff >= 0.8 else "Não"
            pix_str = "Sim" if result.is_payment_claim >= 0.75 else "Não"
            resp_str = f"- Resposta Rápida Sugerida: {result.fast_response_text}\n" if result.fast_response_text else ""
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Triagem Concluída:\n"
                            f"- Intenção: {result.intent} (confiança: {result.intent_confidence})\n"
                            f"- Pedido de Atendente Humano: {human_str} ({result.is_human_handoff})\n"
                            f"- Afirmação de Pagamento PIX: {pix_str} ({result.is_payment_claim})\n"
                            f"- Nível de Frustração: {result.frustration_level} (nota {result.frustration_score}/2.0)\n"
                            f"- Ação Recomendada: {result.recommended_action}\n"
                            + resp_str
                        ),
                    }
                ]
            }

        else:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Ferramenta desconhecida: {name}"}],
            }

    except (ValueError, AsaasIntegrationError) as e:
        return {
            "isError": True,
            "content": [{"type": "text", "text": str(e)}],
        }
    except Exception:
        logger.exception("MCP tool failed", extra={"tool_name": name})
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": "Erro interno ao processar a solicitação.",
                }
            ],
        }


def _format_servicos(offerings, include_professional: bool = True) -> str:
    if not offerings:
        return "Nenhum serviço disponível."

    lines = ["Serviços disponíveis:"]
    for offering in offerings:
        service = offering.service
        price = f"R$ {service.price_cents / 100:.2f}".replace(".", ",")
        if include_professional:
            line = (
                f"  #{service.id} {service.name} - {price} "
                f"({service.duration_minutes}min) — {offering.professional.name}"
            )
        else:
            line = (
                f"  #{service.id} {service.name} - {price} "
                f"({service.duration_minutes}min)"
            )
        lines.append(line)
    return "\n".join(lines)


def _format_cliente(user) -> str:
    whatsapp = getattr(user, "whatsapp_number", None) or "-"
    cpf = getattr(user, "cpf_cnpj", None) or "-"
    return (
        f"  ID: {user.id}\n"
        f"  Nome: {user.name}\n"
        f"  Telefone: {user.phone}\n"
        f"  CPF/CNPJ: {cpf}\n"
        f"  Email: {user.email or '-'}\n"
        f"  WhatsApp: {whatsapp}"
    )
