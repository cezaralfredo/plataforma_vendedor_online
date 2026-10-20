# Plataforma Atende Agenda

API REST completa para gestão de agendamentos, profissionais, serviços e pagamentos — construída com **FastAPI**, **SQLAlchemy** e **PostgreSQL**. Inclui integração com **Asaas** para pagamentos e suporte a **MCP (Model Context Protocol)** para agentes de IA.

---

## Funcionalidades Principais

### Gestão de Usuários
- CRUD completo de usuários (clientes)
- Busca por telefone com validação de duplicidade
- Vinculação de número WhatsApp

### Gestão de Profissionais
- CRUD de profissionais (prestadores de serviço)
- Filtro por profissionais ativos/inativos

### Gestão de Serviços
- Catálogo único de serviços, com nome, descrição e categoria
- Oferta por profissional com preço, duração e comissão configuráveis
- Listagens comerciais retornam somente ofertas ativas

### Disponibilidade e Agenda
- Definição de horários de trabalho por profissional (dias da semana, intervalos)
- Consulta de slots disponíveis para data específica
- Verificação de conflitos de horário
- Geração automática de *time slots* baseada na duração do serviço

### Agendamentos (Appointments)
- Criação de reservas com validação de disponibilidade
- Estados: `pending` → `confirmed` → `completed` / `cancelled`
- Expiração automática de reservas pendentes (30 min)
- Confirmação e cancelamento via API
- Filtros por usuário, profissional e status

### Pagamentos (Integração Asaas)
- Criação de cobranças (PIX, Boleto, Cartão)
- Webhook para atualização automática de status
- Consulta e atualização manual de status de pagamento
- Sincronização: pagamento confirmado → agendamento confirmado

### Webhooks
- Endpoint `/webhooks/asaas` para receber notificações do Asaas
- Validação de assinatura HMAC
- Prevenção de processamento duplicado

### MCP (Model Context Protocol)
- Endpoint `/mcp` para integração com agentes de IA
- Ferramentas disponíveis:
  - `buscar_cliente_por_telefone` — busca cliente por telefone/WhatsApp
  - `cadastrar_cliente` — cadastra novo cliente
  - `atualizar_cliente` — atualiza dados do cliente
  - `vincular_whatsapp` — vincula número WhatsApp ao cliente
  - `listar_servicos` — lista serviços com filtros opcionais
  - `verificar_disponibilidade` — verifica horários livres de um profissional em uma data
  - `criar_reserva` — cria agendamento (valida disponibilidade)
  - `cancelar_reserva` — cancela agendamento existente
  - `criar_cobranca_asaas` — cria cobrança no Asaas (PIX, Boleto, Cartão)
  - `verificar_pagamentos_recentes` — verifica pagamentos pendentes no Asaas
  - `marcar_notificado` — marca agendamento como notificado
  - `meus_agendamentos` — lista agendamentos de um cliente
- Autenticação via Bearer Token (`API_KEY`)

### Camada de IA & Orquestração Nativa (Python)
- Orquestrador inteligente (`SalesOrchestrator` / `SalonAIOrchestrator`) integrado nativamente em Python
- Subagentes especializados de salão de beleza e estética:
  - **Serviços & Agendamento** — cálculo dinâmico de slots, agendamento e cancelamento
  - **Catálogo de Produtos** — produtos de beleza/homecare e envio de fotos
  - **Checkout & Pagamento** — geração de PIX Copia e Cola / QR Code Asaas
  - **Status de Pedidos & Agendamentos** — acompanhamento em tempo real
  - **Suporte & Transbordo Humano** — detecção de intenção e link para atendente humano
- Suporte a múltiplos provedores LLM com fallback automático (OpenAI, Anthropic, Gemini, Groq, Ollama)
- Integração nativa com WhatsApp (WAHA, Evolution API, Meta Cloud API) e transcrição de áudio com Whisper

---

## Tecnologias

| Camada | Tecnologia |
|--------|------------|
| Framework | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 |
| Banco | PostgreSQL 16 |
| Migrações | Alembic |
| Pagamentos | Asaas API (sandbox/produção) |
| IA/Agentes | Orquestrador Nativo Python + Subagentes + MCP |
| Testes | pytest |
| Container | Docker Compose |

---

## Estrutura do Projeto

```
plataforma_atende_agenda/
├── app/
│   ├── api/              # Rotas da API (FastAPI routers)
│   │   ├── appointments.py
│   │   ├── availability.py
│   │   ├── health.py
│   │   ├── payments.py
│   │   ├── professionals.py
│   │   ├── services.py
│   │   ├── users.py
│   │   └── webhooks.py
│   ├── config.py         # Configurações (Pydantic Settings)
│   ├── database.py       # Engine SQLAlchemy + sessão
│   ├── main.py           # App FastAPI + inclusão de routers
│   ├── mcp/              # Model Context Protocol
│   │   ├── router.py     # Endpoint /mcp
│   │   └── tools.py      # Definição das ferramentas MCP
│   ├── models/           # Models SQLAlchemy
│   │   ├── appointment.py
│   │   ├── availability.py
│   │   ├── notification_log.py
│   │   ├── payment.py
│   │   ├── professional.py
│   │   ├── service.py
│   │   ├── user.py
│   │   └── webhook_event.py
│   ├── repositories/     # Camada de acesso a dados
│   │   ├── appointment_repo.py
│   │   ├── availability_repo.py
│   │   ├── base.py
│   │   ├── payment_repo.py
│   │   ├── professional_repo.py
│   │   ├── service_repo.py
│   │   └── user_repo.py
│   ├── schemas/          # Pydantic schemas (request/response)
│   │   ├── appointment.py
│   │   ├── availability.py
│   │   ├── payment.py
│   │   ├── professional.py
│   │   ├── service.py
│   │   └── user.py
│   └── services/         # Lógica de negócio
│       ├── appointment_service.py
│       ├── asaas_client.py
│       ├── availability_service.py
│       ├── payment_service.py
│       ├── payment_state_service.py
│       ├── professional_service.py
│       ├── service_service.py
│       ├── user_service.py
│       ├── ai/               # Orquestrador e subagentes inteligentes
│       └── whatsapp/         # Gateways de mensageria (WAHA, Evolution, Meta)
├── alembic/              # Migrações de banco
├── tests/                # Testes automatizados
├── docker-compose.prod.yml
├── requirements.txt
└── .env                  # Variáveis de ambiente (não versionado)
```

---

## Endpoints da API

### Health
```
GET  /health
```

### Usuários
```
POST   /api/users
GET    /api/users
GET    /api/users/{id}
PUT    /api/users/{id}
DELETE /api/users/{id}
```

### Profissionais
```
POST   /api/professionals
GET    /api/professionals
GET    /api/professionals/{id}
PUT    /api/professionals/{id}
DELETE /api/professionals/{id}
```

### Serviços
```
POST   /api/services
GET    /api/services
GET    /api/services/{id}
PUT    /api/services/{id}
DELETE /api/services/{id}
```

### Disponibilidade
```
POST   /api/availability
GET    /api/availability
GET    /api/availability/{id}
PUT    /api/availability/{id}
DELETE /api/availability/{id}
GET    /api/availability/check/{professional_id}?date=YYYY-MM-DD
GET    /api/availability/slots/{professional_id}/{service_id}?date=YYYY-MM-DD
```

### Agendamentos
```
POST   /api/appointments
GET    /api/appointments
GET    /api/appointments/{id}
PUT    /api/appointments/{id}
POST   /api/appointments/{id}/cancel
POST   /api/appointments/{id}/confirm
DELETE /api/appointments/{id}
```

### Pagamentos
```
POST   /api/payments
GET    /api/payments/{id}
POST   /api/payments/{id}/refresh
POST   /api/payments/verify-recent
```

### Webhooks
```
POST   /webhooks/asaas
```

### MCP
```
POST   /mcp
```

---

## Configuração e Execução

### 1. Variáveis de Ambiente
Crie um arquivo `.env` na raiz:

```env
DATABASE_URL=postgresql+psycopg://agenda_user:agenda_pass@localhost:5432/agenda_atende
API_KEY=sua-chave-secreta-aqui
ADMIN_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=SUA_SENHA_DE_BOOTSTRAP_ADMIN
ADMIN_SESSION_SECRET=SUA_CHAVE_DE_SESSAO_ADMIN_64_CHARS
ADMIN_RECOVERY_KEY=SUA_CHAVE_DE_RECUPERACAO_ADMIN_64_CHARS
APP_NAME=Agenda Atende
DEBUG=true

ASAAS_API_KEY=sua-chave-asaas
ASAAS_BASE_URL=https://api-sandbox.asaas.com/v3
ASAAS_WEBHOOK_TOKEN=token-do-webhook-asaas
```

### 2. Subir Banco de Dados (Docker)
```bash
docker-compose up -d
```

### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 4. Executar Migrações
```bash
alembic upgrade head
```

### 5. Iniciar API
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Acesse a documentação interativa: **http://localhost:8000/docs**

Antes de iniciar a API pela primeira vez, substitua os placeholders de acesso
administrativo seguindo a seção [Acesso administrativo](#acesso-administrativo).

---

## Testes

```bash
pytest tests/ -v
```

---

## Integração com Asaas

1. Crie conta no [Asaas](https://www.asaas.com/)
2. Configure `ASAAS_API_KEY` e `ASAAS_WEBHOOK_TOKEN` no `.env`
3. No painel Asaas, configure webhook para: `https://seu-dominio.com/webhooks/asaas`
4. Eventos suportados: `PAYMENT_RECEIVED`, `PAYMENT_CONFIRMED`, `PAYMENT_OVERDUE`, `PAYMENT_REFUNDED`, `PAYMENT_CANCELLED`

---

## MCP + Agentes IA

### Configuração local do Cliente MCP
```json
{
  "mcpServers": {
    "agenda_atende": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer sua-api-key"
      }
    }
  }
}
```

### Ferramentas Disponíveis
| Ferramenta | Descrição |
|------------|-----------|
| `buscar_cliente_por_telefone` | Busca cliente por telefone/WhatsApp |
| `cadastrar_cliente` | Cadastra novo cliente |
| `atualizar_cliente` | Atualiza dados do cliente |
| `vincular_whatsapp` | Vincula número WhatsApp ao cliente |
| `listar_servicos` | Lista serviços com filtros opcionais |
| `verificar_disponibilidade` | Verifica horários livres de um profissional em uma data |
| `criar_reserva` | Cria agendamento (valida disponibilidade) |
| `cancelar_reserva` | Cancela agendamento existente |
| `criar_cobranca_asaas` | Cria cobrança no Asaas (PIX, Boleto, Cartão) |
| `verificar_pagamentos_recentes` | Verifica pagamentos pendentes no Asaas |
| `marcar_notificado` | Marca agendamento como notificado |
| `meus_agendamentos` | Lista agendamentos de um cliente |

### Mensageria WhatsApp & Orquestração de IA Nativa

A plataforma integra nativamente recepção de webhooks, transcrição de áudio via Whisper, Coexistence Mode (atendente humano) e orquestração de subagentes especialistas:

```
WhatsApp (WAHA / Evolution / Meta)
      │
      ▼  Webhook HTTP POST
FastAPI Webhook Handler (/api/whatsapp/webhook/*)
      │
      ▼  Background Tasks
SellerAIService & Coexistence Guard
      │
      ▼
SalesOrchestrator (Multi-Provider LLM: OpenAI / Anthropic / Gemini / Groq / Ollama)
      │
      ├── ServiceSubagent (Agendamentos, Slots Livres, Profissionais)
      ├── CatalogSubagent (Produtos de Beleza & Estética, Envio de Fotos)
      ├── CheckoutSubagent (PIX Copia e Cola / QR Code Asaas)
      ├── OrderStatusSubagent (Status de Agendamentos e Compras)
      └── SupportSubagent (Transbordo Humano Inteligente)
```

**Webhooks Suportados:**
- **WAHA:** `/api/whatsapp/webhook/waha`
- **Evolution API:** `/api/whatsapp/webhook/evolution`
- **Meta Cloud API:** `/api/whatsapp/webhook/meta`

---

## Modelo de Dados (Resumo)

```
User → Appointment (1:N)
Service → ProfessionalService ← Professional
Professional → Availability (1:N)
Professional → Appointment (1:N)
Service → Appointment (1:N)
Appointment → Payment (1:1)
WebhookEvent (tabela de idempotência para webhooks)
```

---

## Operação segura

- Clientes da API devem enviar `Authorization: Bearer <API_KEY>`
- No navegador, abra `/admin/login` e entre com usuário e senha próprios. O painel usa cookie de sessão e proteção CSRF nas alterações.
- Automações técnicas administrativas continuam enviando `X-Admin-Key: <ADMIN_API_KEY>`. Guarde essa chave no servidor da automação; não a coloque no HTML, JavaScript ou URL do painel.
- Em produção, a documentação interativa é desativada e `/metrics` também exige Bearer. `/health` indica vida do processo e `/ready` confirma acesso ao PostgreSQL.
- O sandbox atual do Asaas é `https://api-sandbox.asaas.com/v3` e a produção usa `https://api.asaas.com/v3`. Clientes e cobranças usam referências externas estáveis para reconciliação.
- Horários sem offset são interpretados em `APP_TIMEZONE=America/Sao_Paulo`; reservas não pagas vencidas liberam automaticamente o slot.
- A CI executa testes no PostgreSQL, migrações, Ruff e MyPy antes de publicar as imagens principal e `-backup`. A migração aborta se já existirem agendamentos ativos sobrepostos.
- Após cada publicação, valide `/ready` da API e o backup mais recente. Faça um teste de restauração periodicamente em ambiente isolado.

## Deploy com Portainer

- Para a variante com PostgreSQL local e Nginx Proxy Manager, siga [DEPLOY_PORTAINER_NPM.md](DEPLOY_PORTAINER_NPM.md).
- Para a topologia com PostgreSQL externo no Neon, siga [DEPLOY_PORTAINER_NEON.md](DEPLOY_PORTAINER_NEON.md).

## Acesso administrativo

O painel atende uma única empresa e uma única conta. Acesse `/admin/login`
com o usuário e a senha inicial configurados abaixo. Em produção, use HTTPS:
o cookie dura oito horas e usa `HttpOnly`, `Secure` e `SameSite=Lax`. As telas
enviam o token CSRF nas alterações. Após cinco tentativas inválidas consecutivas,
a conta fica bloqueada por quinze minutos.

| Configuração | Finalidade e conservação |
| --- | --- |
| `ADMIN_USERNAME` | Nome da conta criada no primeiro start. Não pode ser vazio. Alterar a variável depois não renomeia a conta existente. |
| `ADMIN_BOOTSTRAP_PASSWORD` | Senha exclusiva de pelo menos 12 caracteres para criar a conta quando ainda não existe. Não sobrescreve a senha salva no banco. |
| `ADMIN_SESSION_SECRET` | Chave aleatória de pelo menos 32 caracteres para assinar sessões. Guarde no cofre e mantenha o mesmo valor entre workers, reinícios, publicações e rollback. |
| `ADMIN_RECOVERY_KEY` | Chave aleatória independente, de pelo menos 32 caracteres, para recuperar acesso em emergência. Guarde no cofre com acesso restrito ao responsável. |

Gere a senha inicial no gerenciador de senhas. Gere cada uma das duas chaves
separadamente com `openssl rand -hex 32` em um terminal privado e salve os
resultados diretamente no cofre; não reutilize `API_KEY` nem `ADMIN_API_KEY`.
Os valores dos arquivos `.env.example` e `.env.prod.example` são placeholders,
nunca credenciais utilizáveis. Com `DEBUG=false`, configurações de segurança
ausentes, fracas ou conhecidas como padrão impedem a inicialização.

### Configurar pelo Portainer

1. Abra o Portainer por HTTPS em uma sessão privada, sem gravação ou
   compartilhamento de tela. Nas stacks `docker-compose.portainer-npm.yml`
   e `docker-compose.portainer-neon.yml`, cadastre as quatro variáveis na
   tabela de ambiente da stack a partir do cofre. Não cole valores no editor
   YAML, em comentários, tickets, chat ou capturas de tela. Usuários com acesso
   administrativo ao Portainer/Docker podem consultar variáveis de ambiente;
   restrinja esse acesso. Essas stacks usam variáveis, não Docker Secrets.
2. No ambiente com suporte a secrets externos do Docker Swarm, a composição
   `docker-compose.prod.yml` referencia `admin_username`,
   `admin_bootstrap_password`, `admin_session_secret` e `admin_recovery_key`.
   Crie os quatro secrets em **Secrets → Add secret**, preenchendo os valores
   a partir do cofre sem mostrá-los. O container recebe somente caminhos
   `ADMIN_*_FILE=/run/secrets/...`; o entrypoint carrega os arquivos antes das
   migrações e da API. Não use secrets externos em Docker Compose standalone;
   escolha a stack NPM/Neon por ambiente ou a VPS com `.env.prod` protegido.
3. Publique a stack, confirme `/ready` e entre em `/admin/login`. Vá a
   `/admin/password` e troque a senha inicial por outra exclusiva, confirmando
   a senha atual. A alteração invalida as sessões anteriores e renova a sessão
   que efetuou a troca. **Sair** encerra a sessão do navegador.
4. Após confirmar o acesso com a senha nova e a persistência do banco, retire
   o valor do bootstrap: nas stacks por ambiente, mantenha a variável declarada
   como `ADMIN_BOOTSTRAP_PASSWORD=` (vazia). As stacks exigem sua declaração
   explícita, mas permitem vazio após a criação da conta. Em Docker Secrets,
   remova do serviço `api` tanto a entrada `ADMIN_BOOTSTRAP_PASSWORD_FILE`
   quanto a montagem `admin_bootstrap_password`, e remova sua declaração no
   bloco `secrets` da stack; publique a atualização e só então exclua esse
   secret do Portainer. Preserve esse ajuste nos próximos deploys. Um banco
   novo, sem a conta, voltará a exigir uma senha de bootstrap.

Na VPS, preencha `.env.prod` a partir de `.env.prod.example`, restrinja a
leitura do arquivo ao operador e siga a mesma retirada do valor de bootstrap.
Para verificar configuração sem exibir credenciais, use
`docker compose -f docker-compose.portainer-npm.yml config --quiet` no ambiente
controlado. A saída completa de `config`, inspeções do container e exportações
da stack podem revelar variáveis; não as compartilhe com valores reais.

### Recuperação e rollback

Se perder a senha, abra `/admin/recover` por HTTPS e informe o usuário salvo,
`ADMIN_RECOVERY_KEY` e uma nova senha de pelo menos 12 caracteres com confirmação.
As sessões existentes são invalidadas; faça login com a senha nova. A aplicação
não retorna nem grava a chave de recuperação no banco. Não envie a chave em URL,
logs ou mensagens. Recupere seu valor do cofre, evitando expor variáveis do
container no console do Portainer.

**`ADMIN_SESSION_SECRET` precisa permanecer estável em rollback.** Reutilize a
configuração guardada no cofre e preserve a tabela `admin_accounts`, que contém
o hash da senha e a versão de autenticação. Não restaure uma senha de bootstrap
para tentar alterar uma conta existente. Reverter para uma imagem anterior à
autenticação por sessão remove essa proteção; valide a compatibilidade da imagem
e da migration antes de efetuar o rollback. A rotação deliberada da chave de
sessão encerra as sessões ativas e exige novo login.

## Licença

Projeto privado — Plataforma Atende Agenda.
