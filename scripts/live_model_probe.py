#!/usr/bin/env python3
"""Live OpenRouter model probe through the local ImpressionistLLM server."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh")
LATEST_ALIAS_PREFIXES = (
    "~openai/",
    "~anthropic/",
    "~google/",
    "~moonshotai/",
)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _first_prompt_model(path: Path) -> str:
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if value and not value.startswith(";"):
            return value
    return ""


def configured_models() -> set[str]:
    models: set[str] = set()
    for prompt_path in (REPO_ROOT / "prompts").glob("*.txt"):
        model = _first_prompt_model(prompt_path)
        if model:
            models.add(model)

    actions_path = REPO_ROOT / "prompts" / "processors" / "screenshot_actions.json"
    if actions_path.exists():
        try:
            actions = _read_json(actions_path)
            for item in actions.get("actions") or []:
                model = str(item.get("model") or "").strip()
                if model:
                    models.add(model)
            pre = actions.get("preprocessor") or {}
            model = str(pre.get("model") or "").strip()
            if model:
                models.add(model)
        except Exception:
            pass

    return models


def fetch_openrouter_models() -> dict[str, dict]:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return {
        str(item.get("id") or ""): item
        for item in payload.get("data") or []
        if isinstance(item, dict) and item.get("id")
    }


def select_models(openrouter_models: dict[str, dict], include_grok: bool, include_latest: bool) -> list[str]:
    selected = set(configured_models())
    selected.add("openai/gpt-chat-latest")

    if include_latest:
        for model_id in openrouter_models:
            if model_id.startswith(LATEST_ALIAS_PREFIXES) and model_id.endswith("-latest"):
                selected.add(model_id)

    if include_grok:
        for model_id in openrouter_models:
            if model_id.startswith("x-ai/grok"):
                selected.add(model_id)

    return sorted(selected)


def _has_text_output(model_data: dict | None) -> bool:
    if not model_data:
        return False
    arch = model_data.get("architecture") or {}
    output_modalities = arch.get("output_modalities") or []
    modality = str(arch.get("modality") or "").lower()
    return "text" in output_modalities or "->text" in modality


def _post_json(url: str, payload: dict, timeout: int) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(text)
            except Exception:
                return resp.status, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except Exception:
            return exc.code, text


def _extract_response_text(data) -> str:
    if isinstance(data, dict):
        for key in ("response", "text", "content", "output_text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else ""
            if isinstance(content, str):
                return content.strip()
    return ""


def run_case(
    server_url: str,
    model_id: str,
    effort: str,
    timeout: int,
    retries: int,
    max_tokens: int,
) -> dict:
    request = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": "Return exactly this single word and nothing else: OK",
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_p": 1,
        "stream": False,
    }
    if effort != "off":
        request["reasoning"] = {"enabled": True, "effort": effort, "exclude": True}

    started = time.perf_counter()
    attempts = 0
    status = 0
    data: dict | str = {}
    for attempt in range(max(1, retries + 1)):
        attempts = attempt + 1
        status, data = _post_json(
            urllib.parse.urljoin(server_url.rstrip("/") + "/", "api/llm/complete"),
            {"request": request},
            timeout,
        )
        local_busy = (
            status == 429
            and isinstance(data, dict)
            and str(data.get("error") or "").strip().lower() == "server busy, please retry shortly"
        )
        if not local_busy:
            break
        time.sleep(min(0.5 * (attempt + 1), 5.0))

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    text = _extract_response_text(data)
    error = ""
    if isinstance(data, dict):
        error = str(data.get("error") or "").strip()
    elif isinstance(data, str) and status >= 400:
        error = data[:500]

    return {
        "model": model_id,
        "effort": effort,
        "status": status,
        "ok": status == 200 and bool(text),
        "attempts": attempts,
        "elapsed_ms": elapsed_ms,
        "response_preview": text[:120],
        "error": error[:1000],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8080")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--include-grok", action="store_true", default=True)
    parser.add_argument("--include-latest", action="store_true", default=True)
    parser.add_argument("--models", default="")
    parser.add_argument("--efforts", default=",".join(DEFAULT_EFFORTS))
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    openrouter_models = fetch_openrouter_models()
    if args.models.strip():
        models = [item.strip() for item in args.models.split(",") if item.strip()]
    else:
        models = select_models(openrouter_models, args.include_grok, args.include_latest)

    efforts = [item.strip() for item in args.efforts.split(",") if item.strip()]
    skipped = []
    runnable = []
    for model_id in models:
        model_data = openrouter_models.get(model_id)
        if not model_data:
            skipped.append({"model": model_id, "reason": "missing_from_openrouter_models"})
            continue
        if not _has_text_output(model_data):
            skipped.append({"model": model_id, "reason": "non_text_output"})
            continue
        runnable.append(model_id)

    cases = [(model_id, effort) for model_id in runnable for effort in efforts]
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                run_case,
                args.server_url,
                model_id,
                effort,
                args.timeout,
                args.retries,
                args.max_tokens,
            ): (model_id, effort)
            for model_id, effort in cases
        }
        for future in as_completed(futures):
            model_id, effort = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "model": model_id,
                    "effort": effort,
                    "status": 0,
                    "ok": False,
                    "attempts": 1,
                    "elapsed_ms": 0,
                    "response_preview": "",
                    "error": str(exc)[:1000],
                }
            results.append(result)
            marker = "OK" if result["ok"] else "FAIL"
            print(f"{marker} {result['model']} effort={result['effort']} status={result['status']} {result['elapsed_ms']}ms")

    results.sort(key=lambda item: (item["model"], item["effort"]))
    failures = [item for item in results if not item["ok"]]
    summary = {
        "server_url": args.server_url,
        "efforts": efforts,
        "counts": {
            "models_requested": len(models),
            "models_runnable": len(runnable),
            "cases": len(results),
            "ok": len(results) - len(failures),
            "failed": len(failures),
            "skipped": len(skipped),
        },
        "skipped": skipped,
        "failures": failures,
        "results": results,
    }

    out_path = Path(args.out) if args.out else REPO_ROOT / "temp" / f"live_model_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"SUMMARY {json.dumps(summary['counts'], sort_keys=True)}")
    print(f"OUT {out_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
