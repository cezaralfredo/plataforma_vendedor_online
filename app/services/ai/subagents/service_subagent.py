import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.professional import Professional
from app.models.service import Service
from app.models.user import User
from app.schemas.appointment import AppointmentCreate
from app.services.ai.providers.base import ToolDefinition
from app.services.ai.subagents.base import BaseSubagent
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService
from app.services.professional_service import ProfessionalService
from app.services.service_service import ServiceService

logger = logging.getLogger(__name__)


class ServiceSubagent(BaseSubagent):
    """
    Subagente especialista em Serviços de Salão de Beleza, Estética,
    Profissionais, Horários de Disponibilidade e Agendamentos.
    """

    def __init__(self, db: Session, user: User | None = None):
        self.db = db
        self.user = user
        self.service_service = ServiceService(db)
        self.professional_service = ProfessionalService(db)
        self.availability_service = AvailabilityService(db)
        self.appointment_service = AppointmentService(db)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="listar_servicos_salao",
                description="Lista os serviços disponíveis no salão de beleza e estética (cabelo, manicure, estética, etc.) com preço e duração.",
                parameters={
                    "type": "object",
                    "properties": {
                        "categoria": {
                            "type": "string",
                            "description": "Categoria opcional para filtrar os serviços (ex: 'Cabelo', 'Unhas', 'Estética', 'Depilação').",
                        },
                        "termo_busca": {
                            "type": "string",
                            "description": "Nome ou termo parcial do serviço procurado.",
                        },
                    },
                },
            ),
            ToolDefinition(
                name="listar_profissionais_salao",
                description="Lista os profissionais do salão de beleza (cabeleireiros, esteticistas, manicures) e suas especialidades.",
                parameters={
                    "type": "object",
                    "properties": {
                        "servico_id": {
                            "type": "integer",
                            "description": "ID numérico opcional do serviço para listar apenas profissionais que o realizam.",
                        },
                    },
                },
            ),
            ToolDefinition(
                name="verificar_horarios_disponiveis",
                description="Verifica os horários livres na agenda de um profissional para uma data específica.",
                parameters={
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "string",
                            "description": "Data desejada no formato AAAA-MM-DD (ex: 2026-10-25).",
                        },
                        "profissional_id": {
                            "type": "integer",
                            "description": "ID numérico do profissional. Se não informado, verifica os profissionais disponíveis.",
                        },
                        "servico_id": {
                            "type": "integer",
                            "description": "ID numérico do serviço para calcular os horários de início exatos conforme a duração.",
                        },
                    },
                    "required": ["data"],
                },
            ),
            ToolDefinition(
                name="agendar_servico",
                description="Realiza a reserva/agendamento de um serviço para o cliente no salão.",
                parameters={
                    "type": "object",
                    "properties": {
                        "servico_id": {
                            "type": "integer",
                            "description": "ID numérico do serviço escolhido.",
                        },
                        "profissional_id": {
                            "type": "integer",
                            "description": "ID numérico do profissional selecionado.",
                        },
                        "horario_inicio": {
                            "type": "string",
                            "description": "Horário de início no formato ISO (ex: '2026-10-25T14:00:00') ou 'AAAA-MM-DD HH:MM'.",
                        },
                        "observacoes": {
                            "type": "string",
                            "description": "Observações adicionais ou preferências informadas pelo cliente.",
                        },
                    },
                    "required": ["servico_id", "profissional_id", "horario_inicio"],
                },
            ),
            ToolDefinition(
                name="meus_agendamentos",
                description="Consulta os agendamentos já realizados pelo cliente atual.",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            ToolDefinition(
                name="cancelar_agendamento",
                description="Cancela um agendamento existente do cliente.",
                parameters={
                    "type": "object",
                    "properties": {
                        "agendamento_id": {
                            "type": "integer",
                            "description": "ID numérico do agendamento a ser cancelado.",
                        },
                    },
                    "required": ["agendamento_id"],
                },
            ),
        ]

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            if tool_name == "listar_servicos_salao":
                categoria = (arguments.get("categoria") or "").strip().lower()
                termo = (arguments.get("termo_busca") or "").strip().lower()
                services = self.service_service.list(active_only=True)

                if categoria:
                    services = [s for s in services if s.category and categoria in s.category.lower()]
                if termo:
                    services = [s for s in services if termo in s.name.lower() or (s.description and termo in s.description.lower())]

                if not services:
                    return json.dumps({"status": "vazio", "mensagem": "Nenhum serviço encontrado para os critérios informados."}, ensure_ascii=False)

                res = [
                    {
                        "id": s.id,
                        "nome": s.name,
                        "categoria": s.category,
                        "descricao": s.description or "",
                    }
                    for s in services
                ]
                return json.dumps({"status": "sucesso", "total": len(res), "servicos": res}, ensure_ascii=False)

            elif tool_name == "listar_profissionais_salao":
                servico_id = arguments.get("servico_id")
                professionals = self.professional_service.list(active_only=True)

                res = []
                for p in professionals:
                    offerings = getattr(p, "service_offerings", [])
                    if servico_id:
                        matched = any(ps.service_id == int(servico_id) for ps in offerings if ps.active)
                        if not matched:
                            continue
                    servicos_nomes = [ps.service.name for ps in offerings if ps.active and getattr(ps, "service", None)]
                    res.append({
                        "id": p.id,
                        "nome": p.name,
                        "especialidade": p.bio or "Profissional da Beleza",
                        "servicos_ofertados": servicos_nomes,
                    })

                return json.dumps({"status": "sucesso", "total": len(res), "profissionais": res}, ensure_ascii=False)

            elif tool_name == "verificar_horarios_disponiveis":
                data_str = arguments.get("data")
                prof_id = arguments.get("profissional_id")
                serv_id = arguments.get("servico_id")

                if not data_str:
                    return json.dumps({"erro": "A data é obrigatória (AAAA-MM-DD)."}, ensure_ascii=False)

                if prof_id:
                    slots = self.availability_service.get_available_slots(
                        professional_id=int(prof_id),
                        date_str=data_str,
                        service_id=int(serv_id) if serv_id else None,
                    )
                    return json.dumps({
                        "status": "sucesso",
                        "data": data_str,
                        "profissional_id": prof_id,
                        "horarios_disponiveis": slots,
                    }, ensure_ascii=False)
                else:
                    # Se não informou profissional, busca o primeiro ativo ou todos
                    profs = self.professional_service.list_professionals(active_only=True)
                    all_avail = {}
                    for p in profs:
                        s_slots = self.availability_service.get_available_slots(
                            professional_id=p.id,
                            date_str=data_str,
                            service_id=int(serv_id) if serv_id else None,
                        )
                        if s_slots:
                            all_avail[p.name] = {"profissional_id": p.id, "horarios": s_slots}

                    return json.dumps({
                        "status": "sucesso",
                        "data": data_str,
                        "disponibilidade_por_profissional": all_avail,
                    }, ensure_ascii=False)

            elif tool_name == "agendar_servico":
                if not self.user:
                    return json.dumps({"erro": "Cliente não identificado para o agendamento."}, ensure_ascii=False)

                serv_id = int(arguments["servico_id"])
                prof_id = int(arguments["profissional_id"])
                inicio_raw = arguments["horario_inicio"]
                notas = arguments.get("observacoes")

                # Converte horário para datetime no fuso horário do salão
                from app.business_time import as_business_time

                start_dt = datetime.fromisoformat(inicio_raw.replace(" ", "T"))
                start_dt = as_business_time(start_dt)

                service = self.db.get(Service, serv_id)
                duration = service.duration_minutes if service and service.duration_minutes else 60
                from datetime import timedelta
                end_dt = start_dt + timedelta(minutes=duration)

                payload = AppointmentCreate(
                    user_id=self.user.id,
                    professional_id=prof_id,
                    service_id=serv_id,
                    start_time=start_dt,
                    end_time=end_dt,
                    notes=notas,
                )
                appointment = self.appointment_service.create(payload)
                prof = self.db.get(Professional, prof_id)

                return json.dumps({
                    "status": "sucesso",
                    "mensagem": "Agendamento realizado com sucesso!",
                    "agendamento_id": appointment.id,
                    "servico": service.name if service else "Serviço",
                    "profissional": prof.name if prof else "Profissional",
                    "inicio": appointment.start_time.isoformat(),
                    "fim": appointment.end_time.isoformat(),
                    "status_reserva": appointment.status,
                    "valor_reais": f"R$ {(appointment.service_price_cents or 0) / 100:.2f}",
                }, ensure_ascii=False)

            elif tool_name == "meus_agendamentos":
                if not self.user:
                    return json.dumps({"status": "vazio", "mensagem": "Nenhum cliente identificado."}, ensure_ascii=False)

                from app.models.appointment import Appointment
                apts = (
                    self.db.query(Appointment)
                    .filter(Appointment.user_id == self.user.id)
                    .order_by(Appointment.start_time.desc())
                    .limit(10)
                    .all()
                )
                if not apts:
                    return json.dumps({"status": "vazio", "mensagem": "Você não possui agendamentos cadastrados."}, ensure_ascii=False)

                res = []
                for a in apts:
                    res.append({
                        "id": a.id,
                        "servico": a.service.name if a.service else "N/A",
                        "profissional": a.professional.name if a.professional else "N/A",
                        "data_hora": a.start_time.strftime("%d/%m/%Y às %H:%M") if a.start_time else "N/A",
                        "status": a.status,
                        "valor_reais": f"R$ {(a.service_price_cents or 0) / 100:.2f}",
                    })
                return json.dumps({"status": "sucesso", "agendamentos": res}, ensure_ascii=False)

            elif tool_name == "cancelar_agendamento":
                apt_id = int(arguments["agendamento_id"])
                res = self.appointment_service.cancel_appointment(apt_id)
                if res:
                    return json.dumps({"status": "sucesso", "mensagem": f"Agendamento #{apt_id} cancelado com sucesso."}, ensure_ascii=False)
                return json.dumps({"erro": f"Não foi possível cancelar o agendamento #{apt_id}."}, ensure_ascii=False)

            return json.dumps({"erro": f"Ferramenta desconhecida: {tool_name}"}, ensure_ascii=False)

        except Exception as exc:
            logger.exception("Erro ao executar ferramenta %s no ServiceSubagent: %s", tool_name, exc)
            return json.dumps({"erro": f"Erro interno ao processar {tool_name}: {exc!s}"}, ensure_ascii=False)
