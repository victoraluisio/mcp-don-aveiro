# Deploy do bemp-mcp no EasyPanel (Hostinger) via GitHub

Este guia assume: Windows local, VPS Hostinger com EasyPanel, n8n já
rodando no Project **producao**.

---

## Parte 1 — Subir os arquivos para o GitHub

Você vai criar um repositório privado e fazer upload dos arquivos
pela UI web (não precisa ter Git instalado no Windows).

### 1.1. Criar repositório

1. Acesse https://github.com e faça login (crie conta se não tiver).
2. Clique no **+** no canto superior direito → **New repository**.
3. Preencha:
   - **Repository name**: `bemp-mcp`
   - **Visibility**: **Private** (importante — o token BEMP não vai
     ficar aqui, mas é bom ser privado mesmo assim)
   - Deixe o resto em branco e clique **Create repository**.

### 1.2. Fazer upload dos arquivos

1. Baixe o arquivo `bemp-mcp.zip` desta sessão e extraia no Windows.
2. Na página do repositório recém-criado, clique em
   **"uploading an existing file"** (link azul no meio da tela) ou
   **Add file → Upload files**.
3. Arraste **todo o conteúdo** da pasta extraída (não a pasta em si
   — só os arquivos `Dockerfile`, `README.md`, `bemp_client.py`,
   `docker-compose.yml`, `requirements.txt`, `server.py`, `.env.example`,
   `DEPLOY-EASYPANEL.md`).
4. No campo "Commit changes", deixe a mensagem padrão e clique
   **Commit changes**.

**NÃO suba o arquivo `.env`** (se você tiver criado um). O `.env.example`
é seguro porque não tem o token real.

### 1.3. Criar token de acesso do GitHub (para o EasyPanel)

Como o repo é privado, o EasyPanel precisa de uma credencial para clonar.

1. Em https://github.com → seu avatar → **Settings**.
2. Lado esquerdo, role até o final → **Developer settings**.
3. **Personal access tokens → Tokens (classic)** → **Generate new token (classic)**.
4. Configurações:
   - **Note**: `easypanel-bemp-mcp`
   - **Expiration**: 90 dias (ou No expiration se preferir)
   - **Scopes**: marque **`repo`** (dá acesso de leitura a repos privados)
5. Clique **Generate token** e **copie o token agora** (não vai aparecer de novo).

---

## Parte 2 — Criar o App no EasyPanel

### 2.1. Conectar o GitHub ao EasyPanel

1. Acesse seu EasyPanel (https://<seu-easypanel>.easypanel.host).
2. Entre no Project **producao**.
3. Clique **+ Service** → **App**.
4. Em **Name** coloque `bemp-mcp` (esse é o hostname interno que o
   n8n vai usar).
5. Clique **Create**.

### 2.2. Configurar o source do App

Dentro do App recém-criado:

1. Aba **Source** → escolha **GitHub**.
2. Se for a primeira vez, clique em **"Connect GitHub account"** —
   use o token que você gerou na etapa 1.3 (ou a integração OAuth
   que o EasyPanel oferecer).
3. Selecione:
   - **Owner**: seu usuário/organização
   - **Repository**: `bemp-mcp`
   - **Branch**: `main` (ou `master`, o que o GitHub criou)
   - **Path**: deixe em branco (raiz do repo)
4. Salve.

### 2.3. Configurar o Build

1. Aba **Build** → escolha **Dockerfile**.
2. **Dockerfile path**: `Dockerfile` (padrão).
3. Salve.

### 2.4. Configurar variáveis de ambiente

1. Aba **Environment** → cole:

   ```
   BEMP_TOKEN=wv5J0XGOCgkjMziPXK6t14EOa7aGmtBXpug9qaAEJ842ZpCvYO05oX5
   BEMP_SALON_ID=3115
   BEMP_API_BASE=https://donaveiro.bemp.app
   BEMP_WEBHOOKS_BASE=https://donaveiro.bemp.app
   BEMP_TIMEOUT=20
   MCP_HOST=0.0.0.0
   MCP_PORT=8000
   ```

2. Salve.

### 2.5. Configurar porta interna (sem expor publicamente)

1. Aba **Domains** → **deixe vazio** (não queremos domínio público
   para o MCP).
2. Aba **Ports** → adicione:
   - **Internal port**: `8000`
   - **Public port / Protocol**: deixe em branco / não exponha

O EasyPanel automaticamente cria um hostname interno `bemp-mcp` na
rede docker do project `producao`. O n8n no mesmo project vai
acessar via `http://bemp-mcp:8000/mcp`.

### 2.6. Fazer o primeiro deploy

1. Aba **Deployments** → **Deploy**.
2. Acompanhe o log. Etapas esperadas:
   - `Cloning repository from GitHub...`
   - `Building Docker image... (FROM python:3.12-slim, COPY, pip install)`
   - `Starting container... bemp-mcp is running`
3. Quando aparecer algo como `Uvicorn running on http://0.0.0.0:8000`
   nos logs, o MCP está no ar.

---

## Parte 3 — Testar dentro da VPS

Abra um terminal EasyPanel (botão **Terminal** num app qualquer do
mesmo project, ou no próprio bemp-mcp):

```bash
curl -s -X POST http://bemp-mcp:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"curl","version":"1"}}}'
```

Deve retornar algo como:

```json
{"jsonrpc":"2.0","id":1,"result":{"serverInfo":{"name":"bemp-mcp",...}}}
```

Se retornar isso, o MCP está acessível de dentro da rede do project.

---

## Parte 4 — Conectar o n8n

1. No n8n, abra o workflow **Chat automatizado (Whatsapp) - Don Aveiro v1.5**.
2. Apague os nós-tool: `Serviço`, `Barbeiros`, `Horários`,
   `Verifica cadastro`, `Agendamento`.
3. Adicione um novo nó-tool no AI Agent: **MCP Client Tool**
   (dentro da categoria Langchain / AI).
4. Configure:
   - **Endpoint**: `http://bemp-mcp:8000/mcp`
   - **Server Transport**: `HTTP Streamable`
   - **Authentication**: `None`
   - **Tools to Include**: `All`
5. Conecte esse nó na entrada `Tool` do AI Agent.
6. Troque o `systemMessage` do AI Agent pela versão enxuta (ver README.md).
7. Salve e ative.

---

## Parte 5 — Migrar para o project do cliente (depois dos testes)

Quando validar em `producao`:

1. No EasyPanel, abra o app `bemp-mcp` → menu de 3 pontinhos → **Clone**.
2. Selecione o project do cliente como destino.
3. Revise/atualize variáveis (especialmente `BEMP_TOKEN` e
   `BEMP_SALON_ID` se o cliente tiver valores próprios).
4. Deploy.
5. No n8n do project do cliente, aponte o MCP Client Tool para
   `http://bemp-mcp:8000/mcp` (mesma URL, pois o hostname é
   relativo ao project).

---

## Troubleshooting

**Deploy falha com "git clone authentication error"**
→ Token do GitHub expirado ou sem scope `repo`. Gere novo token e
reconecte em Source.

**Deploy builda mas container fica reiniciando**
→ Veja logs. O mais comum é `BEMP_TOKEN nao definido` — confira a
aba Environment.

**curl retorna "connection refused"**
→ Verifique se a porta interna está configurada como 8000 (não 8080).

**n8n não enxerga http://bemp-mcp:8000/mcp**
→ Os dois serviços precisam estar no MESMO project EasyPanel. Se o
n8n está em outro project, você precisa expor o MCP com domínio
público (aba Domains do app) e proteger com token no reverse proxy
do EasyPanel (ou migrar o n8n para o mesmo project).

**Quero atualizar o código do MCP depois**
→ Faça upload do arquivo novo no GitHub (substitui) → no EasyPanel,
aba Deployments → **Deploy** (ele faz pull + rebuild automático).
Nenhum downtime perceptível (EasyPanel faz rolling).
