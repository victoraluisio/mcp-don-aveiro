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
    ) -> Any:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(
                method,
                url,
                headers=self._headers,
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
    ) -> Any:
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
