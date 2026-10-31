# Deploy no Portainer com Nginx Proxy Manager (NPM) como SSL

> **Plataforma Vendedor Online — Anauê Design (`ria.anauedesign.com.br`)**  
> O Nginx Proxy Manager (NPM) gerencia as portas 80 e 443 e emite os certificados SSL (Let's Encrypt) automaticamente. A aplicação roda isolada internamente na rede Docker e recebe tráfego seguro por proxy reverso.

---

## 1. Topologia da Rede e Portas

```
[ Usuário / WhatsApp ]
         │ (HTTPS : 443 - SSL Seguro)
         ▼
[ Nginx Proxy Manager (NPM) ] ──── SSL Terminado
         │ (HTTP rede interna 'npm')
         ├─────────────────────────────────────────┐
         ▼                                         ▼
[ vendedor-api : 8000 ]                   [ n8n : 5678 ]
(FastAPI - Loja & Painel Admin)     (Orquestrador Omnichannel IA)
         │                                         │
         ▼                                         ▼
[ PostgreSQL : 5432 ]                  [ Evolution / Meta API ]
```

* **Nenhuma porta 80/443 exposta na aplicação:** As portas públicas pertencem exclusivamente ao NPM.
* **Rede externa compartilhada:** `npm` (ou `nginx-proxy_default`).
* **Aliases internos da API:** `vendedor-api`, `ria-api`, `ria-anauedesign`, `loja-anauedesign`.

---

## 2. Implantação da Stack no Portainer

1. Acesse o **Portainer → Stacks → Add stack**.
2. Nome: `vendedor-online` (ou `anauedesign`).
3. Cole o conteúdo de [`docker-compose.portainer-npm.yml`](docker-compose.portainer-npm.yml).
4. No campo **Environment variables**, defina:

```env
POSTGRES_PASSWORD=defina-uma-senha-forte-para-o-banco
API_KEY=sua-api-key-secreta-com-pelo-menos-32-chars
ADMIN_API_KEY=sua-admin-key-secreta-com-pelo-menos-32-chars
ADMIN_USERNAME=admin_anaue
ADMIN_BOOTSTRAP_PASSWORD=DefinaSuaSenhaSegura123!
ADMIN_SESSION_SECRET=gere-com-openssl-rand-hex-32-64-caracteres
ADMIN_RECOVERY_KEY=gere-com-openssl-rand-hex-32-64-caracteres
APP_NAME=Anauê Design - Vendedor Online
APP_TIMEZONE=America/Sao_Paulo
REGISTRY=ghcr.io
GITHUB_REPOSITORY=cezaralfredo/plataforma_vendedor_online
NPM_NETWORK=nginx-proxy_default

# Gateway Financeiro (Asaas ou Manual/Balcão)
ASAAS_API_KEY=sua-chave-asaas-producao
ASAAS_BASE_URL=https://api.asaas.com/v3
ASAAS_WEBHOOK_TOKEN=token-webhook-seguro-asaas

# MCP Gateway
MCP_GATEWAY_KEY=chave-secreta-gateway-mcp-64-chars
MCP_GATEWAY_IMAGE_TAG=latest
IMAGE_TAG=latest
```

5. Clique em **Deploy the stack**.

---

## 3. Configuração do Proxy Host no Nginx Proxy Manager (NPM)

Após subir a stack no Portainer, acesse o painel do seu NPM (`http://seu-ip:81`):

### Proxy Host 1: Loja e Painel Administrativo

1. Vá em **Hosts → Proxy Hosts → Add Proxy Host**.
2. **Details:**
   - **Domain Names:** `ria.anauedesign.com.br` *(e opcionalmente `loja.anauedesign.com.br`)*
   - **Scheme:** `http`
   - **Forward Hostname / IP:** `vendedor-api` *(ou `ria-anauedesign`)*
   - **Forward Port:** `8000`
   - Marque:
     - [x] **Cache Assets**
     - [x] **Block Common Exploits**
     - [x] **Websockets Support**
3. **SSL Tab:**
   - **SSL Certificate:** *Request a new SSL Certificate*
   - Marque:
     - [x] **Force SSL**
     - [x] **HTTP/2 Support**
     - [x] **HSTS Enabled**
     - [x] **I Agree to the Let's Encrypt Terms of Service**
4. Clique em **Save**.

---

### Proxy Host 2: n8n (Orquestrador WhatsApp IA - opcional)

Se utilizar o n8n para a orquestração da Evolution API / Meta:
1. **Domain Names:** `n8n.anauedesign.com.br`
2. **Scheme:** `http`
3. **Forward Hostname / IP:** `n8n`
4. **Forward Port:** `5678`
5. **Websockets Support:** [x] Marcado
6. **SSL:** Solicite certificado Let's Encrypt com Force SSL.

---

## 4. Testes de Validação Pós-Deploy

Valide pelo navegador ou via curl:

```bash
# 1. Checagem de integridade da API e Banco
curl -k https://ria.anauedesign.com.br/ready

# 2. Acesso ao Painel Administrativo
# Abra no navegador: https://ria.anauedesign.com.br/login
# (ou https://ria.anauedesign.com.br/admin/login)
```

---

## 5. Gerenciando Senha e Acesso Administrativo

* **Primeiro acesso:** Entre com `ADMIN_USERNAME` e a senha definida em `ADMIN_BOOTSTRAP_PASSWORD`.
* **Alterar senha:** Acesse `https://ria.anauedesign.com.br/admin/password` para trocar por senha exclusiva.
* **Criar/Resetar senha via terminal:**
  ```bash
  docker exec -it <container_api> python scripts/create_or_reset_admin.py --username admin_anaue --generate
  ```
