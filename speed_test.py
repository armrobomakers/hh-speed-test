#!/usr/bin/env python3
"""Measure sequential HTTP(S) download throughput using only the stdlib."""

from __future__ import annotations

import argparse
import http.client
import math
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


REQUEST_COUNT = 10
CHUNK_SIZE = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "SequentialSpeedTest/1.0"


@dataclass(frozen=True)
class RequestResult:
    number: int
    elapsed_seconds: float
    bytes_downloaded: int
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SpeedTestResult:
    url: str
    requests: tuple[RequestResult, ...]
    elapsed_seconds: float

    @property
    def successful_requests(self) -> int:
        return sum(result.succeeded for result in self.requests)

    @property
    def failed_requests(self) -> int:
        return len(self.requests) - self.successful_requests

    @property
    def total_bytes_downloaded(self) -> int:
        return sum(result.bytes_downloaded for result in self.requests)

    @property
    def average_request_seconds(self) -> float:
        if not self.requests:
            return 0.0
        return sum(result.elapsed_seconds for result in self.requests) / len(self.requests)

    @property
    def speed_mbit_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.total_bytes_downloaded * 8 / self.elapsed_seconds / 1_000_000


def validate_url(url: str) -> str:
    """Accept only absolute HTTP(S) URLs with a host and a valid port."""
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Укажи полный HTTP- или HTTPS-адрес, например https://example.com/file.jpg")
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError("В адресе указан некорректный порт") from exc
    return value


def _consume_response(response: object) -> tuple[int, str | None]:
    """Read the complete response body in chunks and verify Content-Length when present."""
    downloaded = 0
    try:
        while True:
            chunk = response.read(CHUNK_SIZE)  # type: ignore[attr-defined]
            if not chunk:
                break
            downloaded += len(chunk)
    except http.client.IncompleteRead as exc:
        downloaded += len(exc.partial)
        return downloaded, f"неполный ответ: получено {downloaded} байт, соединение оборвалось"
    except (OSError, http.client.HTTPException) as exc:
        return downloaded, f"ошибка чтения ответа ({type(exc).__name__}): {exc}"

    content_length = response.headers.get("Content-Length")  # type: ignore[attr-defined]
    if content_length is not None:
        try:
            expected = int(content_length)
        except ValueError:
            return downloaded, "сервер прислал некорректный Content-Length"
        if expected != downloaded:
            return downloaded, f"неполный ответ: ожидалось {expected} байт, получено {downloaded}"
    return downloaded, None


def _download_once(url: str, number: int, timeout_seconds: float) -> RequestResult:
    started = time.perf_counter()
    downloaded = 0
    error_message: str | None = None
    request = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.getcode()
            downloaded, body_error = _consume_response(response)
            if not 200 <= status < 300:
                error_message = f"HTTP {status}"
            if body_error:
                error_message = f"{error_message}; {body_error}" if error_message else body_error
    except HTTPError as exc:
        try:
            # HTTPError is also a file-like response. Consume its body before the next request.
            downloaded, body_error = _consume_response(exc)
            error_message = f"HTTP {exc.code} {exc.reason}"
            if body_error:
                error_message += f"; {body_error}"
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        error_message = f"{type(exc).__name__}: {reason}"

    elapsed = time.perf_counter() - started
    return RequestResult(
        number=number,
        elapsed_seconds=elapsed,
        bytes_downloaded=downloaded,
        error=error_message,
    )


def run_speed_test(
    url: str,
    request_count: int = REQUEST_COUNT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> SpeedTestResult:
    """Run requests one at a time; continue to the next attempt after a failure."""
    target = validate_url(url)
    if request_count < 1:
        raise ValueError("Количество запросов должно быть не меньше одного")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Тайм-аут должен быть больше нуля")

    started = time.perf_counter()
    results = tuple(
        _download_once(target, number, timeout_seconds)
        for number in range(1, request_count + 1)
    )
    elapsed = time.perf_counter() - started
    return SpeedTestResult(url=target, requests=results, elapsed_seconds=elapsed)


def _display_host(url: str) -> str:
    """Show only the host so URL query tokens are not echoed to the terminal."""
    parts = urlsplit(url)
    host = parts.hostname or "unknown host"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}"


def _format_bytes(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MB"
    if value >= 1_000:
        return f"{value / 1_000:.2f} KB"
    return f"{value} B"


def print_report(result: SpeedTestResult) -> None:
    print(f"Цель: {_display_host(result.url)}")
    print(
        f"Запросы: {len(result.requests)} "
        f"(успешно: {result.successful_requests}, ошибок: {result.failed_requests})"
    )
    print(f"Среднее время попытки: {result.average_request_seconds:.3f} с")
    print(
        f"Всего получено: {_format_bytes(result.total_bytes_downloaded)} "
        f"({result.total_bytes_downloaded} байт)"
    )
    print(f"Полное время теста: {result.elapsed_seconds:.3f} с")
    if result.successful_requests:
        print(
            "Средняя скорость за весь тест: "
            f"{result.speed_mbit_per_second:.2f} Mbit/s "
            f"({result.speed_mbit_per_second / 8:.2f} MB/s)"
        )
    else:
        print("Скорость не рассчитана: нет успешных ответов")

    for request in result.requests:
        if request.error:
            print(f"Запрос {request.number}: ошибка — {request.error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Последовательно скачивает один URL 10 раз и оценивает скорость."
    )
    parser.add_argument("url", help="URL файла по HTTP или HTTPS")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"тайм-аут каждого запроса в секундах (по умолчанию {DEFAULT_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args(argv)

    try:
        result = run_speed_test(args.url, timeout_seconds=args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    print_report(result)
    return 1 if result.failed_requests else 0


if __name__ == "__main__":
    raise SystemExit(main())

