"""Eugene's gateway, as Workbench reaches it: an ordinary OpenAI client.

**The same doors as everyone else** (`workbench.md` call 6). `GET
/v1/models` and `POST /v1/chat/completions`, with the app's client key as
the bearer. The key is minted by the registry at install and read from
the file it names; nothing else the hub has is reachable from here.

**A refusal is said in plain words** (W11): the key turned off, the key at
its limit, the gateway unreachable -- each names what happened and what to
do, because every person using this Workbench shares the one key and none
of them can see Eugene's console.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from ._http import ssl_context


class HubError(Exception):
    """The gateway could not give an answer, in words a person reads."""

    def __init__(
        self, message: str, *, status: int = 502, kind: str = "hub", param: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.kind = kind
        #: The request field the gateway named, so a form can mark it (§2.5).
        self.param = param


def _gateway_words(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:400] or f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(body, dict) and body.get("detail"):
        detail = body["detail"]
        return str(detail.get("detail") if isinstance(detail, dict) else detail)
    return f"HTTP {response.status_code}"


def _gateway_param(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    param = error.get("param") if isinstance(error, dict) else None
    return str(param) if param else None


def refusal(status: int, words: str, param: str | None = None) -> HubError:
    """The gateway's refusal, said for a person who cannot see the console."""
    if status in (401, 403):
        return HubError(
            "Eugene refused the key this Workbench uses, so it cannot reach any model "
            f"({words}). If the key was turned off in Eugene's console, the owner can "
            "uninstall and install Workbench again to make a new one.",
            status=status,
            kind="key",
        )
    if status == 429:
        return HubError(
            f"This Workbench's key is at its limit ({words}). Everyone using this Workbench "
            "shares that key; the owner can raise its limits on the key in Eugene's console.",
            status=429,
            kind="limit",
        )
    return HubError(words, status=status, kind="gateway", param=param)


class Hub:
    """The gateway, with the app's key. One HTTP client for the process."""

    def __init__(self, gateway_url: str | None, key_file: Path | None) -> None:
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else None
        self._key_file = key_file
        self._key: tuple[float, str] | None = None
        # The gateway is this install's, on loopback or the LAN: never
        # through a proxy the person's environment names. A stream may go
        # quiet while a model reads a long prompt, but not for 15 minutes:
        # past that the gateway's own deadline (600 s) has long fired.
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=900.0), trust_env=False, verify=ssl_context()
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def why_not(self) -> str | None:
        if not self.gateway_url:
            return (
                "Eugene's agent found no gateway for this Workbench when it started it. Start "
                "the gateway, then restart Workbench from its page in Eugene's console."
            )
        if self._key_file is None:
            return (
                "This Workbench was started without a key. Install it from Eugene's app catalogue."
            )
        return None

    def _token(self) -> str:
        reason = self.why_not()
        if reason is not None:
            raise HubError(reason, status=503, kind="setup")
        assert self._key_file is not None
        try:
            mtime = self._key_file.stat().st_mtime
            if self._key is None or self._key[0] != mtime:
                self._key = (mtime, self._key_file.read_text(encoding="utf-8").strip())
        except OSError as exc:
            raise HubError(
                f"This Workbench's key file cannot be read ({exc}). Uninstall and install "
                "Workbench again from Eugene's console.",
                status=503,
                kind="setup",
            ) from exc
        return self._key[1]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def _unreachable(self, exc: Exception) -> HubError:
        return HubError(
            f"Workbench cannot reach Eugene's gateway at {self.gateway_url}: "
            f"{str(exc) or type(exc).__name__}",
            status=503,
            kind="unreachable",
        )

    async def models(self) -> dict[str, Any]:
        headers = self._headers()
        try:
            response = await self._http.get(f"{self.gateway_url}/v1/models", headers=headers)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response))
        body: dict[str, Any] = response.json()
        return body

    async def images(
        self, body: dict[str, Any], *, edit: bool
    ) -> tuple[dict[str, Any], str | None]:
        """One image request (P4), and the gateway's request id. A request
        with reference images goes to `/edits` as JSON with `data:` URLs,
        the shape the gateway takes from a server. Raises `HubError`."""
        headers = self._headers()
        url = f"{self.gateway_url}/v1/images/{'edits' if edit else 'generations'}"
        try:
            response = await self._http.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if response.status_code != 200:
            raise refusal(response.status_code, _gateway_words(response), _gateway_param(response))
        try:
            answer = response.json()
        except ValueError as exc:
            raise HubError(
                "The gateway answered the image request with something that is not JSON "
                f"({response.headers.get('content-type') or 'no type'}).",
                kind="gateway",
            ) from exc
        if not isinstance(answer, dict):
            raise HubError("The gateway's answer to the image request was not an object.")
        return answer, response.headers.get("x-request-id")

    async def stream_chat(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """The chunks of one streamed answer, parsed. Raises `HubError`."""
        headers = self._headers()
        body = dict(body)
        repetition_mode = body.pop("_repetition_mode", None)
        if repetition_mode is not None:
            headers["X-Eugene-Repetition-Mode"] = repetition_mode
        url = f"{self.gateway_url}/v1/chat/completions"
        try:
            async with self._http.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise refusal(response.status_code, _gateway_words(response))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(chunk, dict) and isinstance(chunk.get("error"), dict):
                        error = chunk["error"]
                        raise HubError(
                            str(error.get("message") or "the answer stopped part-way"),
                            kind="repetition"
                            if error.get("code") == "repetition_detected"
                            else "stream",
                        )
                    if isinstance(chunk, dict):
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        # A stream that ends with neither [DONE] nor an error is a backend
        # that stopped mid-answer, not one that finished (M10's lesson).
        raise HubError("The answer stopped part-way: the gateway closed the stream.", kind="stream")
