# bemp-mcp

Servidor MCP (Model Context Protocol) que unifica as chamadas da API
BEMP (v1) em 8 tools. Foi desenhado para substituir os nós `HTTP
Request Tool` e o sub-workflow `Verifica cadastro` do fluxo n8n
**Chat automatizado (Whatsapp) - Don Aveiro v1.5**, reduzindo
alucinações do agente de IA (ele passa a receber schemas tipados e
mensagens de erro claras).

## Tools expostas

| Tool | Método / endpoint | Substitui no n8n |
| ---- | ----------------- | ---------------- |
| `list_salons` | GET `/api/salons` | - |
| `list_services` | GET `/api/salons/{id}/services` | Tool "Serviço" |
| `list_professionals` | GET `/api/salons/{id}/services/{sid}/professionals` | Tool "Barbeiros" |
| `list_slots` | GET `/api/salons/{id}/services/{sid}/professionals/{pid}/slots/{data}` | Tool "Horários" |
| `create_appointment` | POST `/webhooks/whatsapp_schedule` | Tool "Agendamento" (sub-workflow) |
| `get_customer` | GET `/webhooks/whatsapp_customer` | Sub-workflow "Consulta Cliente Don Aveiro" |
| `list_customer_appointments` | GET `/webhooks/whatsapp_schedule` | (novo) |
| `cancel_appointment` | DELETE `/webhooks/whatsapp_schedule` | (novo) |

As duas últimas permitem que o agente faça reagendamento sozinho
(cancela o antigo, cria o novo), eliminando o fallback atual que
empurra a conversa para um atendente humano.

## Deploy na VPS

Pré-requisito: `docker` e `docker compose` já instalados (o mesmo
host que roda o n8n serve perfeitamente).

```bash
# 1. Enviar os arquivos para a VPS
scp -r bemp-mcp/ usuario@vps:/opt/

# 2. Na VPS
cd /opt/bemp-mcp
cp .env.example .env
nano .env            # conferir BEMP_TOKEN e BEMP_SALON_ID

# 3. Subir
docker compose up -d --build
docker compose logs -f bemp-mcp
```

O servidor vai escutar em `http://127.0.0.1:8000/mcp` (bind em
localhost por padrão, por segurança). Se o n8n roda no mesmo
docker-compose ou em rede docker compartilhada, prefira conectar o
`bemp-mcp` à rede do n8n e acessar por hostname interno
(`http://bemp-mcp:8000/mcp`) em vez de expor porta.

### Rodando na mesma rede do n8n

Descubra o nome da rede:

```bash
docker network ls | grep n8n
```

No `docker-compose.yml` do bemp-mcp, descomente o bloco `networks` e
use o nome encontrado. Com isso o n8n acessa o MCP em
`http://bemp-mcp:8000/mcp/` sem abrir nenhuma porta pública.

### Testes rápidos (curl)

```bash
# Handshake MCP (deve retornar serverInfo + capabilities + instructions)
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"curl","version":"1"}}}'
```

Para rodar `tools/call` via curl é necessário manter o session ID
retornado no handshake — na prática o `MCP Client Tool` do n8n faz
isso sozinho. Se retornar `serverInfo.name = "bemp-mcp"`, o servidor
está no ar.

## Conectando o n8n ao MCP

No n8n, adicione um nó **MCP Client Tool** (Langchain) como tool do
seu AI Agent:

- **Endpoint**: `http://bemp-mcp:8000/mcp` (rede docker compartilhada)
  ou `http://127.0.0.1:8000/mcp` (n8n no mesmo host, fora de container)
- **Server Transport**: `HTTP Streamable`
- **Authentication**: None (o token da BEMP está no .env do MCP, o
  n8n não precisa carregar segredos)
- **Tools to Include**: `All`

Em seguida:

1. **Apague** as tools atuais do AI Agent: `Serviço`, `Barbeiros`,
   `Horários`, `Verifica cadastro`, `Agendamento`.
2. Conecte o novo nó `MCP Client Tool` na entrada `Tool` do AI Agent.
3. **Mantenha** as tools que o MCP não cobre: `Falar com atendente`
   (Redis SET), `Notifica atendente` (WAHA sendText).

### System message enxuto sugerido

Como o MCP já carrega as descrições das tools e valida inputs, o
`systemMessage` do agente pode ficar muito menor. Sugestão:

```
# Persona: Tony — Atendente da Barbearia Don Aveiro
Sempre simpático, profissional, objetivo. Respostas curtas.
Endereço: Sudoeste, CLSW 301 Bloco B loja 64, Brasília-DF. Seg–Sáb 08h–20h.

## Regras
1. Hoje é {{ $now.format('yyyy-MM-dd EEEE', {locale:'pt-br'}) }}.
2. NUNCA invente id, nome de profissional, serviço ou horário. Tudo vem das tools.
3. Refira-se ao barbeiro como "profissional".
4. Não use emojis.
5. Se o cliente pedir atendente humano, chame `Falar com atendente` + `Notifica atendente`.

## Fluxo
1. Chame `get_customer` com país/DDD/número. Se existir, chame pelo nome;
   se não, peça nome completo.
2. Chame `list_services`. Ofereça os serviços relevantes ao que o cliente pediu.
3. Após o cliente confirmar o serviço, chame `list_professionals` com o id.
4. Após escolha do profissional, peça a data. Converta para YYYY-MM-DD.
5. Chame `list_slots` com service_id, professional_id, data. Liste os
   horários disponíveis.
6. Apresente resumo (serviço, profissional, data, horário) e peça confirmação.
7. Ao confirmar, chame `create_appointment`. Se ok, responda iniciando com
   "Agendamento Confirmado". Se erro, chame `list_slots` de novo e ofereça
   outros horários.
8. Reagendamento: `list_customer_appointments` → `cancel_appointment` →
   voltar ao passo 2/3 para o novo horário.
```

Observe como as regras sobre "antes de oferecer profissional, chame a
tool X" e "não invente o que a tool não retornou" ficam muito mais
curtas — elas estão embutidas nas docstrings do MCP.

## Estrutura do projeto

```
bemp-mcp/
├── server.py            # FastMCP + 8 tools (entrypoint)
├── bemp_client.py       # cliente HTTP httpx para a API BEMP
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Nota sobre versão da API

A doc oficial avisa que a **API v1 da BEMP será descontinuada em 2025**.
Abra um chamado no menu AJUDA da BEMP pedindo a documentação da v2.
Migrar este MCP é uma tarefa pequena (ajustar URLs e payloads em
`bemp_client.py`), as assinaturas das tools podem permanecer iguais.
