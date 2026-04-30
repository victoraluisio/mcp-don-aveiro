"""MCP server para a API BEMP (v1).

Expoe as operacoes de agendamento da BEMP como tools MCP. Foi desenhado
para ser consumido pelo no MCP Client do n8n (langchain) rodando no
mesmo host (via docker-compose, por exemplo), mas pode ser chamado por
qualquer cliente MCP que fale streamable HTTP.

Rodar localmente para teste:
    uv run fastmcp run server.py --transport streamable-http

Rodar em producao na VPS (ver docker-compose.yml):
    python server.py
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from bemp_client import BempApiError, BempClient

# ---------------------------------------------------------------------------
# inicializacao
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="bemp-mcp",
    instructions=(
        "Tools para a API BEMP (v1) - barbearia Don Aveiro. "
        "Use SEMPRE estas tools para obter servicos, profissionais, "
        "horarios disponiveis e criar/cancelar agendamentos. NUNCA invente "
        "ids, nomes de profissionais ou horarios. Todos os dados devem vir "
        "estritamente da resposta das tools."
    ),
)

_client: BempClient | None = None


def get_client() -> BempClient:
    """Lazy-init do cliente para nao explodir quando o .env ainda nao foi lido."""
    global _client
    if _client is None:
        _client = BempClient()
    return _client


# ---------------------------------------------------------------------------
# validacoes
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIGITS_RE = re.compile(r"^\d+$")


def _normalize_date(value: str) -> str:
    """Aceita YYYY-MM-DD. Normaliza pequenas variacoes."""
    s = value.strip()
    if not _DATE_RE.match(s):
        raise ValueError(
            f"Data invalida: {value!r}. Use o formato YYYY-MM-DD (ex: 2026-04-23)."
        )
    # valida que e uma data real
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Data invalida: {value!r}. {exc}") from exc
    return s


def _validate_iso8601(value: str, field_name: str) -> str:
    """Aceita ISO 8601 com timezone. Ex: 2026-04-23T13:30:00.000-03:00"""
    s = value.strip()
    try:
        # fromisoformat aceita o formato completo desde python 3.11.
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{field_name} invalido: {value!r}. "
            "Use ISO 8601 com timezone, ex: 2026-04-23T13:30:00.000-03:00. "
            f"Detalhe: {exc}"
        ) from exc
    return s


def _validate_phone_part(value: Any, field_name: str) -> str:
    s = str(value).strip()
    if not s:
        raise ValueError(f"{field_name} nao pode ser vazio.")
    if not _DIGITS_RE.match(s):
        raise ValueError(
            f"{field_name} deve conter apenas digitos. Recebido: {value!r}."
        )
    return s


def _format_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, BempApiError):
        return {
            "ok": False,
            "error_type": "bemp_api_error",
            "status_code": exc.status_code,
            "url": exc.url,
            "detail": exc.detail,
            "message": (
                f"A BEMP retornou HTTP {exc.status_code}. "
                "Nao reafirme sucesso para o cliente. Ofereca nova tentativa "
                "ou alternativa (outro horario/profissional)."
            ),
        }
    return {
        "ok": False,
        "error_type": type(exc).__name__,
        "message": str(exc),
    }


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
@mcp.tool
def list_salons() -> Any:
    """Lista todas as unidades (saloes) da conta BEMP.

    Use apenas se voce ainda nao sabe o id da unidade. Em instalacoes
    com uma unidade unica, o id ja vem configurado via BEMP_SALON_ID e
    esta tool raramente e necessaria.

    Retorno: lista de objetos com ao menos id e name de cada unidade.
    """
    try:
        return get_client().list_salons()
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def list_services(
    salon_id: Annotated[
        int | None,
        Field(
            description=(
                "ID da unidade. Se omitido, usa BEMP_SALON_ID do ambiente."
            ),
            ge=1,
        ),
    ] = None,
) -> Any:
    """Lista os servicos disponiveis da unidade (ex: corte, barba, visagismo).

    Sempre chame esta tool antes de apresentar servicos ao cliente. NAO
    invente servicos nem precos.
    """
    try:
        return get_client().list_services(salon_id=salon_id)
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def find_services(
    queries: Annotated[
        list[str],
        Field(
            description=(
                "Lista de nomes (ou partes do nome) dos servicos desejados. "
                "Ex: ['sobrancelha pinca', 'progressiva']. "
                "A busca e parcial e case-insensitive."
            ),
            min_length=1,
        ),
    ],
    salon_id: Annotated[
        int | None,
        Field(description="ID da unidade. Default: BEMP_SALON_ID.", ge=1),
    ] = None,
) -> Any:
    """Resolve nomes de servicos para IDs sem exigir leitura de lista longa.

    Use SEMPRE que precisar do service_id de um servico especifico.
    Passe os nomes (ou fragmentos de nome) que o cliente mencionou e
    receba os IDs corretos prontos para usar em list_slots,
    list_multi_service_slots e create_appointment.

    NUNCA invente service_ids. Use exclusivamente os IDs retornados
    por esta tool ou por list_services.
    """
    try:
        return get_client().find_services_by_name(queries=queries, salon_id=salon_id)
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def list_professionals(
    service_id: Annotated[
        int,
        Field(description="ID do servico escolhido pelo cliente.", ge=1),
    ],
    salon_id: Annotated[
        int | None,
        Field(description="ID da unidade. Default: BEMP_SALON_ID.", ge=1),
    ] = None,
) -> Any:
    """Lista os profissionais que executam o servico informado.

    SEMPRE chame esta tool antes de oferecer um profissional ao cliente.
    NUNCA mencione profissionais que nao estao nesta resposta.
    """
    try:
        return get_client().list_professionals(
            service_id=service_id, salon_id=salon_id
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def list_slots(
    service_id: Annotated[
        int,
        Field(description="ID do servico escolhido.", ge=1),
    ],
    date: Annotated[
        str,
        Field(
            description=(
                "Data desejada no formato YYYY-MM-DD (ex: 2026-05-03)."
            ),
        ),
    ],
    professional_id: Annotated[
        int | None,
        Field(
            description=(
                "ID do profissional. Opcional: se omitido, retorna horarios "
                "agregados da unidade para o servico."
            ),
            ge=1,
        ),
    ] = None,
    salon_id: Annotated[
        int | None,
        Field(description="ID da unidade. Default: BEMP_SALON_ID.", ge=1),
    ] = None,
) -> Any:
    """Lista os horarios disponiveis para servico + data (+ profissional).

    Use apos o cliente ter confirmado servico, profissional (opcional) e
    data. Ofereca ao cliente APENAS horarios desta resposta.
    """
    try:
        d = _normalize_date(date)
        return get_client().list_slots(
            service_id=service_id,
            date=d,
            professional_id=professional_id,
            salon_id=salon_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def list_multi_service_slots(
    service_ids: Annotated[
        list[int],
        Field(
            description=(
                "Lista ordenada dos IDs dos servicos desejados. "
                "A ordem define a sequencia de execucao: o 2o servico "
                "comeca imediatamente apos o 1o terminar, e assim por diante."
            ),
            min_length=2,
        ),
    ],
    date: Annotated[
        str,
        Field(
            description="Data desejada no formato YYYY-MM-DD (ex: 2026-05-03)."
        ),
    ],
    professional_id: Annotated[
        int | None,
        Field(
            description=(
                "ID do profissional escolhido. Obrigatorio quando o cliente "
                "ja escolheu um profissional — garante que os slots retornados "
                "sejam da agenda desse profissional especificamente."
            ),
            ge=1,
        ),
    ] = None,
    salon_id: Annotated[
        int | None,
        Field(description="ID da unidade. Default: BEMP_SALON_ID.", ge=1),
    ] = None,
) -> Any:
    """Lista horarios disponiveis para MULTIPLOS servicos consecutivos.

    Use quando o cliente deseja realizar mais de um servico no mesmo dia.
    Retorna apenas blocos de horario onde TODOS os servicos podem ser
    agendados de forma consecutiva (sem intervalo entre eles), na ordem
    informada em service_ids.

    SEMPRE passe professional_id quando o cliente ja escolheu um profissional.

    Cada item de 'available_chains' contem:
      - start: inicio do primeiro servico
      - end: fim do ultimo servico
      - services: lista com service_id, start e end de cada servico

    Para confirmar o agendamento, chame create_appointment para cada
    servico separadamente usando os start/end correspondentes em 'services'.
    NUNCA use horarios que nao estejam nesta resposta.
    """
    try:
        d = _normalize_date(date)
        return get_client().list_multi_service_slots(
            service_ids=list(service_ids),
            date=d,
            professional_id=professional_id,
            salon_id=salon_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def create_appointment(
    service_id: Annotated[int, Field(description="ID do servico.", ge=1)],
    start: Annotated[
        str,
        Field(
            description=(
                "Inicio do atendimento em ISO 8601 com timezone. "
                "Ex: 2026-04-23T13:30:00.000-03:00."
            )
        ),
    ],
    end: Annotated[
        str,
        Field(
            description=(
                "Fim do atendimento em ISO 8601 com timezone. "
                "Ex: 2026-04-23T14:00:00.000-03:00."
            )
        ),
    ],
    name: Annotated[
        str,
        Field(description="Nome completo do cliente.", min_length=1),
    ],
    phone_country_code: Annotated[
        str,
        Field(
            description="Codigo do pais, apenas digitos (ex: '55').",
            min_length=1,
        ),
    ],
    phone_area_code: Annotated[
        str,
        Field(
            description="DDD, apenas digitos (ex: '61').", min_length=2
        ),
    ],
    phone_number: Annotated[
        str,
        Field(
            description=(
                "Numero do telefone, apenas digitos (ex: '999999999')."
            ),
            min_length=8,
        ),
    ],
    professional_id: Annotated[
        int | None,
        Field(description="ID do profissional (opcional).", ge=1),
    ] = None,
    salon_id: Annotated[
        int | None,
        Field(description="ID da unidade. Default: BEMP_SALON_ID.", ge=1),
    ] = None,
) -> Any:
    """Cria um agendamento na BEMP.

    Regras importantes:
      - Todos os ids (salon, service, professional) devem vir das tools
        de listagem - NUNCA inventar.
      - start/end em ISO 8601 com timezone real (ex: -03:00).
      - Em caso de erro (ok=false), NAO afirme sucesso ao cliente. Chame
        list_slots novamente e ofereca horarios validos.
    """
    try:
        _validate_iso8601(start, "start")
        _validate_iso8601(end, "end")
        cc = _validate_phone_part(phone_country_code, "phone_country_code")
        ac = _validate_phone_part(phone_area_code, "phone_area_code")
        num = _validate_phone_part(phone_number, "phone_number")
        return get_client().create_appointment(
            service_id=service_id,
            start=start,
            end=end,
            name=name.strip(),
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
            professional_id=professional_id,
            salon_id=salon_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def get_customer(
    phone_country_code: Annotated[
        str, Field(description="Codigo do pais (ex: '55').", min_length=1)
    ],
    phone_area_code: Annotated[
        str, Field(description="DDD (ex: '61').", min_length=2)
    ],
    phone_number: Annotated[
        str, Field(description="Numero do telefone, apenas digitos.", min_length=8)
    ],
) -> Any:
    """Consulta o cadastro de um cliente pelo telefone.

    Substitui o sub-workflow 'Verifica cadastro'. Retorna os dados do
    cliente se ele existir no sistema, ou uma resposta indicando que
    nao existe cadastro.
    """
    try:
        cc = _validate_phone_part(phone_country_code, "phone_country_code")
        ac = _validate_phone_part(phone_area_code, "phone_area_code")
        num = _validate_phone_part(phone_number, "phone_number")
        return get_client().get_customer(
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def list_customer_appointments(
    phone_country_code: Annotated[
        str, Field(description="Codigo do pais (ex: '55').", min_length=1)
    ],
    phone_area_code: Annotated[
        str, Field(description="DDD (ex: '61').", min_length=2)
    ],
    phone_number: Annotated[
        str, Field(description="Numero do telefone, apenas digitos.", min_length=8)
    ],
) -> Any:
    """Lista os agendamentos ABERTOS (futuros/ativos) do cliente.

    Use quando o cliente pedir para ver, alterar ou cancelar agendamentos.
    """
    try:
        cc = _validate_phone_part(phone_country_code, "phone_country_code")
        ac = _validate_phone_part(phone_area_code, "phone_area_code")
        num = _validate_phone_part(phone_number, "phone_number")
        return get_client().list_customer_appointments(
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


@mcp.tool
def cancel_appointment(
    appointment_id: Annotated[
        int, Field(description="ID do agendamento a cancelar.", ge=1)
    ],
    phone_country_code: Annotated[
        str, Field(description="Codigo do pais (ex: '55').", min_length=1)
    ],
    phone_area_code: Annotated[
        str, Field(description="DDD (ex: '61').", min_length=2)
    ],
    phone_number: Annotated[
        str, Field(description="Numero do telefone, apenas digitos.", min_length=8)
    ],
) -> Any:
    """Cancela um agendamento existente.

    O appointment_id DEVE vir de list_customer_appointments - nao
    invente. Use para reagendamento: cancele o antigo, depois chame
    create_appointment com o novo horario.
    """
    try:
        cc = _validate_phone_part(phone_country_code, "phone_country_code")
        ac = _validate_phone_part(phone_area_code, "phone_area_code")
        num = _validate_phone_part(phone_number, "phone_number")
        return get_client().cancel_appointment(
            appointment_id=appointment_id,
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
        )
    except Exception as exc:  # noqa: BLE001
        return _format_error(exc)


# ---------------------------------------------------------------------------
# health check (para o EasyPanel/Docker)
# ---------------------------------------------------------------------------
from starlette.requests import Request
from starlette.responses import JSONResponse


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "bemp-mcp"})


# ---------------------------------------------------------------------------
# REST endpoint para cancelamento via n8n (automacao de confirmacoes)
# ---------------------------------------------------------------------------
def _parse_phone(full_phone: str) -> tuple[str, str, str]:
    """Converte numero completo em (country_code, area_code, number).

    Espera formato sem formatacao: 5561996800868
    Para numeros brasileiros (prefix 55): cc=55, ac=2 digitos, num=resto.
    """
    s = re.sub(r"\D", "", full_phone)
    if s.startswith("55") and len(s) >= 12:
        return "55", s[2:4], s[4:]
    # Fallback: assume 2 digitos de pais, 2 de area, resto eh numero
    if len(s) >= 6:
        return s[:2], s[2:4], s[4:]
    raise ValueError(f"Telefone invalido para parse: {full_phone!r}")


@mcp.custom_route("/api/services", methods=["GET"])
async def services_rest(request: Request) -> JSONResponse:
    """Retorna lista de servicos da unidade via REST — usado pelo n8n.

    Query param opcional: salon_id
    Retorna {"services": [...]} (objeto, nao array) para o n8n nao dividir em N itens.
    """
    salon_id = request.query_params.get("salon_id")
    try:
        result = get_client().list_services(salon_id=int(salon_id) if salon_id else None)
        return JSONResponse({"services": result})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_format_error(exc), status_code=500)


@mcp.custom_route("/api/customer_appointments", methods=["POST"])
async def customer_appointments_rest(request: Request) -> JSONResponse:
    """Retorna agendamentos abertos do cliente via REST — usado pelo n8n.

    Body JSON esperado:
        { "phone": "5511959707203" }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Body JSON invalido."}, status_code=400)

    phone = str(body.get("phone", "")).strip()
    if not phone:
        return JSONResponse({"ok": False, "error": "phone e obrigatorio."}, status_code=400)

    try:
        cc, ac, num = _parse_phone(phone)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    try:
        result = get_client().list_customer_appointments(
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_format_error(exc), status_code=500)


@mcp.custom_route("/api/cancel_appointment", methods=["POST"])
async def cancel_appointment_rest(request: Request) -> JSONResponse:
    """Cancela agendamento via chamada REST — usado pelo workflow n8n de confirmacoes.

    Body JSON esperado:
        {
            "appointment_id": 12345,
            "phone": "5561996800868"
        }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Body JSON invalido."}, status_code=400)

    appointment_id = body.get("appointment_id")
    phone = str(body.get("phone", "")).strip()

    if not appointment_id:
        return JSONResponse({"ok": False, "error": "appointment_id e obrigatorio."}, status_code=400)
    if not phone:
        return JSONResponse({"ok": False, "error": "phone e obrigatorio."}, status_code=400)

    try:
        cc, ac, num = _parse_phone(phone)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    try:
        result = get_client().cancel_appointment(
            appointment_id=int(appointment_id),
            phone_country_code=cc,
            phone_area_code=ac,
            phone_number=num,
        )
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(_format_error(exc), status_code=500)


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    # streamable-http e o transport consumido pelo no MCP Client do n8n.
    mcp.run(transport="streamable-http", host=host, port=port)
