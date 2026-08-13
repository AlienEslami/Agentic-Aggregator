from __future__ import annotations

import copy
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any


class OptimizerBackend(ABC):
    @abstractmethod
    def optimize(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class DirectOptimizerBackend(OptimizerBackend):
    """Invoke app_rt.py synchronously while preserving its production result contract."""

    def optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        import app_rt

        job_id = str(uuid.uuid4())
        app_rt.save_job(job_id, {"status": "running"})
        app_rt.run_optimization(
            job_id,
            copy.deepcopy(payload["input"]),
            copy.deepcopy(payload.get("price_guidance", {})),
            copy.deepcopy(payload.get("disturbances", [])),
            payload.get("optimization_mode", "real_time"),
            int(payload.get("current_timestep", 1)),
        )
        result = app_rt.get_job(job_id)
        if result is None:
            raise RuntimeError(f"Direct optimizer produced no result for job {job_id}")
        return result


class HttpOptimizerBackend(OptimizerBackend):
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 2.0,
    ):
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install 'httpx' to use the HTTP optimizer backend") from exc
        self.httpx = httpx
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        with self.httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/optimize", json=payload)
            response.raise_for_status()
            submitted = response.json()
            job_id = submitted.get("job_id")
            if not job_id:
                raise RuntimeError(f"Optimizer returned no job_id: {submitted}")
            while time.monotonic() < deadline:
                polled = client.get(f"{self.base_url}/result/{job_id}")
                polled.raise_for_status()
                result = polled.json()
                if result.get("status") not in {"running", "pending"}:
                    return result
                time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"Optimizer job {job_id} did not finish within {self.timeout_seconds}s")


def create_optimizer_backend(
    name: str,
    *,
    url: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> OptimizerBackend:
    if name == "direct":
        return DirectOptimizerBackend()
    if name == "http":
        return HttpOptimizerBackend(
            url,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    raise ValueError(f"Unsupported optimizer backend: {name}")
