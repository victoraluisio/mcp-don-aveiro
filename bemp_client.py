"""Cliente HTTP para a API da BEMP (v1).

Encapsula autenticacao, URLs base e tratamento de erros para que o
servidor MCP trabalhe apenas com dados Python tipados.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class BempApiError(RuntimeError):
    """Erro levantado quando a BEMP retorna um status >= 400."""

    def __init__(self, status_code: int, detail: Any, url: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.url = url
        super().__init__(f"BEMP {status_code} em {url}: {detail}")


class BempClient:
    """Cliente para a API v1 da BEMP.

    Variaveis de ambiente esperadas:
      BEMP_TOKEN          - token de acesso (obrigatorio)
      BEMP_SALON_ID       - id da unidade padrao (obrigatorio para tools
                             que nao recebem salon_id explicito)
      BEMP_API_BASE       - base da API de consulta.
                             default: https://donaveiro.bemp.app
      BEMP_WEBHOOKS_BASE  - base dos webhooks (agendamento, cliente,
                             cancelamento).
                             default: https://donaveiro.bemp.app
      BEMP_TIMEOUT        - timeout em segundos (default 20)
    """

    def __init__(
        self,
        token: str | None = None,
        salon_id: int | str | None = None,
        api_base: str | None = None,
        webhooks_base: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.token = token or os.environ.get("BEMP_TOKEN", "").strip()
        if not self.token:
            raise RuntimeError(
                "BEMP_TOKEN nao definido. Defina a variavel de ambiente "
                "antes de iniciar o servidor."
            )

        raw_salon = (
            str(salon_id)
            if salon_id is not None
            else os.environ.get("BEMP_SALON_ID", "").strip()
        )
        self.default_salon_id: int | None = int(raw_salon) if raw_salon else None

        self.api_base = (
            api_base or os.environ.get("BEMP_API_BASE", "https://donaveiro.bemp.app")
        ).rstrip("/")
        self.webhooks_base = (
            webhooks_base
            or os.environ.get("BEMP_WEBHOOKS_BASE", "https://donaveiro.bemp.app")
        ).rstrip("/")
        self.timeout = timeout or float(os.environ.get("BEMP_TIMEOUT", "20"))

        self._headers = {
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bemp-mcp/1.0",
        }
        # Webhooks publicos nao exigem autenticacao
        self._webhook_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "bemp-mcp/1.0",
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _resolve_salon(self, salon_id: int | None) -> int:
        if salon_id is not None:
            return int(salon_id)
        if self.default_salon_id is None:
            raise RuntimeError(
                "salon_id nao informado e BEMP_SALON_ID nao esta definido."
            )
        return self.default_salon_id

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        headers = dict(self._headers if auth else self._webhook_headers)
        # Nao enviar Content-Type em requests sem body (GET/DELETE sem JSON).
        # A BEMP rejeita com 401 quando recebe Content-Type: application/json
        # em requests que nao tem body.
        if json_body is None:
            headers.pop("Content-Type", None)
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise BempApiError(response.status_code, detail, url)
        if response.status_code == 204 or not response.content:
            return {"ok": True}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # ------------------------------------------------------------------
    # API v1 - consultas
    # ------------------------------------------------------------------
    def list_salons(self) -> Any:
        return self._request("GET", f"{self.api_base}/api/salons")

    def list_services(self, salon_id: int | None = None) -> Any:
        sid = self._resolve_salon(salon_id)
        raw = self._request("GET", f"{self.api_base}/api/salons/{sid}/services")
        # Retorna apenas os campos essenciais para o LLM mapear corretamente
        # nome → id. A resposta completa tem ~30 campos por servico (imagem,
        # preco, moeda, descricao...) que aumentam o risco de o modelo associar
        # o ID errado ao nome do servico.
        if isinstance(raw, list):
            result = []
            for svc in raw:
                if not isinstance(svc, dict) or not svc.get("id") or not svc.get("name"):
                    continue
                price_type = str(svc.get("price_type") or "").upper()
                base_price = svc.get("price_currency") or svc.get("price") or ""
                price_display = (
                    f"A partir de {base_price}" if price_type == "VARIABLE" and base_price
                    else "A combinar" if price_type == "VARIABLE"
                    else base_price
                )
                entry: dict = {
                    "id": svc.get("id"),
                    "name": svc.get("name"),
                    "duration": svc.get("duration"),
                    "price_display": price_display,
                    "price_type": price_type,
                }
                # DEBUG TEMPORARIO: expoe campos brutos de servicos variaveis
                if price_type == "VARIABLE":
                    entry["_debug_raw_keys"] = {k: v for k, v in svc.items() if "price" in k.lower() or "value" in k.lower() or "amount" in k.lower()}
                result.append(entry)
            return result
        return raw

    def list_professionals(
        self, service_id: int, salon_id: int | None = None
    ) -> Any:
        sid = self._resolve_salon(salon_id)
        return self._request(
            "GET",
            f"{self.api_base}/api/salons/{sid}/services/{service_id}/professionals",
        )

    def list_slots(
        self,
        service_id: int,
        date: str,
        professional_id: int | None = None,
        salon_id: int | None = None,
    ) -> Any:  # noqa: D102
        sid = self._resolve_salon(salon_id)
        if professional_id is not None:
            url = (
                f"{self.api_base}/api/salons/{sid}/services/{service_id}"
                f"/professionals/{professional_id}/slots/{date}"
            )
        else:
            url = (
                f"{self.api_base}/api/salons/{sid}/services/{service_id}"
                f"/slots/{date}"
            )
        return self._request("GET", url)

    def list_multi_service_slots(  # noqa: C901
        self,
        service_ids: list[int],
        date: str,
        professional_id: int | None = None,
        salon_id: int | None = None,
    ) -> Any:
        """Retorna horarios onde todos os servicos cabem consecutivamente."""
        from datetime import datetime as _dt, timedelta as _td

        if not service_ids:
            return {"available_chains": [], "total": 0}
        if len(service_ids) == 1:
            return self.list_slots(
                service_ids[0], date, professional_id=professional_id, salon_id=salon_id
            )

        def _extract(raw: Any) -> list[dict]:
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict):
                for key in ("slots", "data", "results", "available"):
                    if isinstance(raw.get(key), list):
                        return raw[key]
            return []

        def _parse(ts: str) -> _dt:
            return _dt.fromisoformat(ts.strip().replace("Z", "+00:00"))

        # Busca duracoes oficiais dos servicos via list_services (campo duration
        # em segundos). Mais confiavel que inferir pelos slots, pois servicos
        # curtos podem retornar start == end nos slots da API.
        _resolved_salon = self._resolve_salon(salon_id)
        _service_dur_map: dict[int, _td] = {}
        try:
            _svc_list = self.list_services(salon_id=_resolved_salon)
            _items = (
                _svc_list
                if isinstance(_svc_list, list)
                else next(
                    (
                        _svc_list[k]
                        for k in ("services", "data", "results")
                        if isinstance(_svc_list.get(k), list)
                    ),
                    [],
                )
            )
            for _svc in _items:
                if isinstance(_svc, dict) and "id" in _svc and "duration" in _svc:
                    try:
                        dur_s = int(_svc["duration"])
                        if dur_s > 0:
                            _service_dur_map[int(_svc["id"])] = _td(seconds=dur_s)
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass  # usa fallback por slots se list_services falhar

        # Valida que todos os service_ids existem nesta unidade.
        # Evita que IDs incorretos (ex: vindos de memoria de sessao anterior)
        # passem silenciosamente e causem erro 500 no create_appointment.
        if _service_dur_map:
            _invalid = [sid for sid in service_ids if sid not in _service_dur_map]
            if _invalid:
                return {
                    "ok": False,
                    "error": "service_ids_invalidos",
                    "message": (
                        f"Os service_ids {_invalid} nao existem nesta unidade. "
                        "Chame list_services agora para obter os IDs corretos "
                        "e repita a chamada com os IDs validos."
                    ),
                    "ids_validos": list(_service_dur_map.keys()),
                }
            # Reordena do mais demorado para o menos demorado. Servicos curtos
            # no final nao quebram o alinhamento de 15 minutos da grade de slots.
            service_ids = sorted(
                service_ids,
                key=lambda s: _service_dur_map.get(s, _td(0)),
                reverse=True,
            )

        def _duration(service_id: int, slots: list[dict]) -> _td:
            """Retorna duracao do servico: prioriza campo 'duration' da API de
            servicos; cai para inferencia por slots como ultimo recurso."""
            api_dur = _service_dur_map.get(service_id)
            if api_dur:
                return api_dur
            # fallback: infere pelo primeiro slot com start/end distintos
            _min = _td(minutes=15)
            for s in slots:
                if isinstance(s, dict) and "start" in s and "end" in s:
                    try:
                        d = _parse(s["end"]) - _parse(s["start"])
                        return d if d > _td(0) else _min
                    except Exception:
                        pass
            return _td(minutes=30)

        # Busca slots disponiveis de cada servico
        all_slots: list[list[dict]] = []
        for sid in service_ids:
            raw = self.list_slots(
                sid, date, professional_id=professional_id, salon_id=salon_id
            )
            slots = _extract(raw)
            if not slots:
                return {
                    "available_chains": [],
                    "total": 0,
                    "message": (
                        f"Sem disponibilidade para o servico {sid} em {date}."
                    ),
                }
            all_slots.append(slots)

        # Duracoes reais de cada servico (em segundos via API, nao inferidas)
        durations = [_duration(service_ids[i], all_slots[i]) for i in range(len(service_ids))]

        # Indexa horarios disponiveis de cada servico (em UTC) para
        # verificar se o slot calculado existe de fato na agenda.
        from datetime import timezone as _tz

        def _to_utc_ts(ts: str) -> float:
            """Converte ISO 8601 para timestamp UTC (segundos)."""
            return _parse(ts).astimezone(_tz.utc).timestamp()

        available_utc: list[set[float]] = []
        for slots in all_slots:
            idx: set[float] = set()
            for s in slots:
                if isinstance(s, dict) and "start" in s:
                    try:
                        idx.add(_to_utc_ts(s["start"]))
                    except Exception:
                        pass
            available_utc.append(idx)

        # Monta chains: para cada slot do 1o servico, encadeia os demais
        # verificando que o horario calculado esta disponivel na agenda real.
        chains: list[dict] = []
        for first in all_slots[0]:
            if not isinstance(first, dict) or "start" not in first or "end" not in first:
                continue
            try:
                services: list[dict] = [
                    {
                        "service_id": service_ids[0],
                        "start": first["start"],
                        "end": first["end"],
                    }
                ]
                cur = _parse(first["end"])
                valid = True
                for j in range(1, len(service_ids)):
                    cur_ts = cur.astimezone(_tz.utc).timestamp()
                    # Valida contra o servico ancora (indice 0): seus slots
                    # refletem corretamente quando o profissional esta livre
                    # em blocos longos, evitando artefatos da API em servicos
                    # de curta duracao que podem ter disponibilidade incorreta.
                    if cur_ts not in available_utc[0]:
                        valid = False
                        break
                    svc_end = cur + durations[j]
                    services.append(
                        {
                            "service_id": service_ids[j],
                            "start": cur.isoformat(),
                            "end": svc_end.isoformat(),
                        }
                    )
                    cur = svc_end
                if valid:
                    chains.append(
                        {
                            "start": first["start"],
                            "end": cur.isoformat(),
                            "services": services,
                        }
                    )
            except Exception:
                continue

        return {
            "date": date,
            "service_ids": service_ids,
            "available_chains": chains,
            "total": len(chains),
        }

    def find_services_by_name(
        self, queries: list[str], salon_id: int | None = None
    ) -> Any:
        """Resolve nomes de servicos para seus IDs.

        Para cada string em `queries`, busca correspondencia parcial
        case-insensitive nos servicos da unidade. Retorna os IDs
        corretos sem exigir que o agente leia e interprete uma lista
        longa de servicos.
        """
        services = self.list_services(salon_id=salon_id)
        if not isinstance(services, list):
            return services

        # Indice rapido nome -> service (case-insensitive)
        results: list[dict] = []
        unmatched: list[str] = []

        for q in queries:
            q_lower = q.lower().strip()
            matches = [
                {
                    "id": svc["id"],
                    "name": svc["name"],
                    "duration": svc.get("duration"),
                    "price_display": svc.get("price_display"),
                    "price_type": svc.get("price_type"),
                }
                for svc in services
                if isinstance(svc, dict) and q_lower in (svc.get("name") or "").lower()
            ]
            if matches:
                results.append({"query": q, "matches": matches})
            else:
                unmatched.append(q)

        response: dict[str, Any] = {"resolved": results}
        if unmatched:
            # Retorna lista completa para o agente escolher manualmente
            response["unmatched_queries"] = unmatched
            response["all_services"] = services
            response["hint"] = (
                "As queries acima nao tiveram correspondencia. "
                "Consulte 'all_services' para encontrar o servico correto."
            )
        return response

    # ------------------------------------------------------------------
    # API v1 - webhooks (agendamento e cliente)
    # ------------------------------------------------------------------
    def create_appointment(
        self,
        service_id: int,
        start: str,
        end: str,
        name: str,
        phone_country_code: str,
        phone_area_code: str,
        phone_number: str,
        professional_id: int | None = None,
        salon_id: int | None = None,
    ) -> Any:
        sid = self._resolve_salon(salon_id)
        body: dict[str, Any] = {
            "salon_id": int(sid),
            "service_id": int(service_id),
            "start": start,
            "end": end,
            "name": name,
            "phone_country_code": str(phone_country_code),
            "phone_area_code": str(phone_area_code),
            "phone_number": str(phone_number),
        }
        if professional_id is not None:
            body["professional_id"] = int(professional_id)
        return self._request(
            "POST",
            f"{self.webhooks_base}/webhooks/whatsapp_schedule",
            json_body=body,
        )

    def get_customer(
        self,
        phone_country_code: str,
        phone_area_code: str,
        phone_number: str,
    ) -> Any:
        params = {
            "phone_country_code": str(phone_country_code),
            "phone_area_code": str(phone_area_code),
            "phone_number": str(phone_number),
        }
        return self._request(
            "GET",
            f"{self.webhooks_base}/webhooks/whatsapp_customer",
            params=params,
        )

    def list_customer_appointments(
        self,
        phone_country_code: str,
        phone_area_code: str,
        phone_number: str,
    ) -> Any:
        params = {
            "phone_country_code": str(phone_country_code),
            "phone_area_code": str(phone_area_code),
            "phone_number": str(phone_number),
        }
        return self._request(
            "GET",
            f"{self.webhooks_base}/webhooks/whatsapp_schedule",
            params=params,
        )

    def cancel_appointment(
        self,
        appointment_id: int,
        phone_country_code: str,
        phone_area_code: str,
        phone_number: str,
    ) -> Any:
        params = {
            "id": int(appointment_id),
            "phone_country_code": str(phone_country_code),
            "phone_area_code": str(phone_area_code),
            "phone_number": str(phone_number),
        }
        return self._request(
            "DELETE",
            f"{self.webhooks_base}/webhooks/whatsapp_schedule",
            params=params,
        )
