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
        return self._request("GET", f"{self.api_base}/api/salons/{sid}/services")

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

    def list_multi_service_slots(
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

        def _duration(slots: list[dict]) -> _td:
            """Infere duracao do servico a partir do primeiro slot disponivel."""
            for s in slots:
                if isinstance(s, dict) and "start" in s and "end" in s:
                    try:
                        return _parse(s["end"]) - _parse(s["start"])
                    except Exception:
                        pass
            return _td(minutes=30)  # fallback

        # Busca slots de cada servico
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

        # Calcula duracao de cada servico (inferida dos proprios slots)
        durations = [_duration(slots) for slots in all_slots]
        extra_duration = sum(durations[1:], _td())  # soma dos servicos apos o 1o

        # Tenta encadeamento exato primeiro (slots com limites alinhados)
        def _norm(ts: str) -> str:
            try:
                return _parse(ts).isoformat()
            except Exception:
                return ts.strip()

        indices: list[dict[str, dict]] = []
        for slots in all_slots:
            idx: dict[str, dict] = {}
            for s in slots:
                if isinstance(s, dict) and "start" in s:
                    idx[_norm(s["start"])] = s
            indices.append(idx)

        exact_chains: list[dict] = []
        for first in all_slots[0]:
            if not isinstance(first, dict) or "start" not in first or "end" not in first:
                continue
            chain = [first]
            end_cur = _norm(first["end"])
            ok = True
            for i in range(1, len(service_ids)):
                nxt = indices[i].get(end_cur)
                if nxt is None:
                    ok = False
                    break
                chain.append(nxt)
                end_cur = _norm(nxt.get("end", ""))
            if ok:
                exact_chains.append(
                    {
                        "start": chain[0]["start"],
                        "end": chain[-1]["end"],
                        "services": [
                            {
                                "service_id": service_ids[j],
                                "start": chain[j]["start"],
                                "end": chain[j]["end"],
                            }
                            for j in range(len(chain))
                        ],
                    }
                )

        if exact_chains:
            return {
                "date": date,
                "service_ids": service_ids,
                "available_chains": exact_chains,
                "total": len(exact_chains),
            }

        # Fallback: slots do 1o servico com end ajustado pela duracao total
        # Usado quando os limites de slot nao se alinham entre servicos
        # (ex: corte 1h + sobrancelha 10min + hidratacao 15min)
        fallback_chains: list[dict] = []
        for first in all_slots[0]:
            if not isinstance(first, dict) or "start" not in first or "end" not in first:
                continue
            try:
                t_start = _parse(first["start"])
                t_end = _parse(first["end"])  # fim do 1o servico
                services: list[dict] = [
                    {
                        "service_id": service_ids[0],
                        "start": first["start"],
                        "end": first["end"],
                    }
                ]
                cur = t_end
                for j in range(1, len(service_ids)):
                    svc_end = cur + durations[j]
                    services.append(
                        {
                            "service_id": service_ids[j],
                            "start": cur.isoformat(),
                            "end": svc_end.isoformat(),
                        }
                    )
                    cur = svc_end
                fallback_chains.append(
                    {
                        "start": first["start"],
                        "end": cur.isoformat(),
                        "services": services,
                        "note": (
                            "Horarios dos servicos adicionais calculados com base "
                            "na duracao — confirme disponibilidade ao agendar."
                        ),
                    }
                )
            except Exception:
                continue

        return {
            "date": date,
            "service_ids": service_ids,
            "available_chains": fallback_chains,
            "total": len(fallback_chains),
            "mode": "estimated",
        }

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
