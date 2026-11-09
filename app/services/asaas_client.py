from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings


class AsaasIntegrationError(RuntimeError):
    """The provider rejected a request or could not be reached safely."""


class AsaasNotFoundError(AsaasIntegrationError):
    """The requested resource was not found on Asaas (HTTP 404)."""


class AsaasUncertainResultError(AsaasIntegrationError):
    """A mutating request may have reached Asaas, so it must be reconciled."""


class _AsaasRetryableError(AsaasIntegrationError):
    pass


class AsaasClient:
    def __init__(self, timeout_seconds: float = 15.0):
        self.base_url = settings.asaas_base_url.rstrip("/")
        self.api_key = settings.asaas_api_key
        self.headers = {
            "access_token": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": f"{settings.app_name}/1.0",
        }
        self.timeout = httpx.Timeout(timeout_seconds, connect=5.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list):
                descriptions = [
                    description
                    for item in errors
                    if isinstance(item, dict)
                    and isinstance((description := item.get("description")), str)
                ]
                if descriptions:
                    return "; ".join(descriptions)
            if isinstance(body.get("message"), str):
                return body["message"]
        return f"Asaas returned HTTP {response.status_code}"

    @classmethod
    def _validate_response(cls, response: httpx.Response) -> None:
        if response.is_success:
            return
        message = cls._error_message(response)
        if response.status_code == 404:
            raise AsaasNotFoundError(message)
        if response.status_code == 429 or response.status_code >= 500:
            raise _AsaasRetryableError(message)
        raise AsaasIntegrationError(message)

    @retry(
        wait=wait_exponential(multiplier=0.1, min=0.1, max=1),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.ConnectError, _AsaasRetryableError)
        ),
        reraise=True,
    )
    async def _get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        if client is self._client:
            response = await client.get(f"{self.base_url}{path}", params=params)
        else:
            async with client:
                response = await client.get(f"{self.base_url}{path}", params=params)
        self._validate_response(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise AsaasIntegrationError("Asaas returned an invalid JSON object")
        return payload

    async def _safe_get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._get(path, params)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise AsaasIntegrationError("Unable to reach Asaas") from exc
        except _AsaasRetryableError as exc:
            raise AsaasIntegrationError(str(exc)) from exc

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        try:
            if client is self._client:
                response = await client.post(f"{self.base_url}{path}", json=payload)
            else:
                async with client:
                    response = await client.post(f"{self.base_url}{path}", json=payload)
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise AsaasUncertainResultError(
                "The Asaas operation has an uncertain result and must be reconciled"
            ) from exc

        try:
            self._validate_response(response)
        except _AsaasRetryableError as exc:
            raise AsaasUncertainResultError(
                "The Asaas operation has an uncertain result and must be reconciled"
            ) from exc

        body = response.json()
        if not isinstance(body, dict):
            raise AsaasIntegrationError("Asaas returned an invalid JSON object")
        return body

    async def list_customers(self, external_reference: str) -> list[dict]:
        data = await self._safe_get(
            "/customers",
            {"limit": 10, "externalReference": external_reference},
        )
        return data.get("data", [])

    async def get_customer(self, customer_id: str) -> dict:
        return await self._safe_get(f"/customers/{customer_id}")

    async def create_customer(
        self,
        name: str,
        external_reference: str,
        phone: str | None = None,
        email: str | None = None,
        cpf_cnpj: str | None = None,
    ) -> dict:
        payload: dict[str, str] = {
            "name": name,
            "externalReference": external_reference,
        }
        if phone:
            payload["phone"] = phone
        if email:
            payload["email"] = email
        if cpf_cnpj:
            payload["cpfCnpj"] = cpf_cnpj
        return await self._post("/customers", payload)

    async def update_customer(
        self,
        customer_id: str,
        name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        cpf_cnpj: str | None = None,
    ) -> dict:
        payload: dict[str, str] = {}
        if name:
            payload["name"] = name
        if phone:
            payload["phone"] = phone
        if email:
            payload["email"] = email
        if cpf_cnpj:
            payload["cpfCnpj"] = cpf_cnpj
        return await self._post(f"/customers/{customer_id}", payload)

    async def create_payment(
        self,
        customer_id: str,
        value: float,
        due_date: str,
        external_reference: str,
        description: str | None = None,
        billing_type: str = "UNDEFINED",
    ) -> dict:
        payload: dict[str, str | float] = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": value,
            "dueDate": due_date,
            "externalReference": external_reference,
        }
        if description:
            payload["description"] = description
        return await self._post("/payments", payload)

    async def get_payment(self, payment_id: str) -> dict:
        return await self._safe_get(f"/payments/{payment_id}")

    async def list_payments(
        self,
        status: str | None = None,
        limit: int = 10,
        external_reference: str | None = None,
    ) -> list[dict]:
        params: dict[str, str | int] = {"limit": limit}
        if status:
            params["status"] = status
        if external_reference:
            params["externalReference"] = external_reference
        data = await self._safe_get("/payments", params)
        return data.get("data", [])

    async def get_pix_qr_code(self, payment_id: str) -> dict:
        return await self._safe_get(f"/payments/{payment_id}/pixQrCode")

    async def refund_payment(
        self,
        payment_id: str,
        value: float | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> dict:
        body: dict[str, Any] = {}
        if value is not None:
            body["value"] = value
        if description:
            body["description"] = description
        return await self._post(f"/payments/{payment_id}/refund", body)
