"""btw_engine.llm — model-agnostic LLM client with a cost ledger.

Architecture M2.5, decisions D8/D9. Code asks for a *role*; engine/models.yaml
decides which model serves it. Every call writes a row to llm_call (model,
tokens, computed USD cost via LiteLLM's price table, purpose, document).

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, plus provider keys
(ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY — only for roles used).

CLI smoke test:
  python -m btw_engine.llm --smoke
  python -m btw_engine.llm --role cross_checker --prompt "Say OK."
"""

import argparse
import json
import os
import sys
import warnings

import httpx
import litellm
import yaml

litellm.suppress_debug_info = True
litellm.drop_params = True  # silently drop params a provider doesn't support

_KEY_FOR_PREFIX = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def load_roles(path: str | None = None) -> dict:
    path = path or os.environ.get("BTW_MODELS_YAML") or os.path.join(
        os.path.dirname(__file__), "..", "..", "models.yaml")
    with open(path) as fp:
        return yaml.safe_load(fp)["roles"]


def provider_key_present(model: str) -> bool:
    prefix = model.split("/", 1)[0]
    env = _KEY_FOR_PREFIX.get(prefix)
    return bool(env and os.environ.get(env))


def _ledger(row: dict) -> None:
    """Best-effort: a ledger outage must not kill the pipeline."""
    try:
        base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/llm_call"
        key = os.environ["SUPABASE_SERVICE_KEY"]
        httpx.post(base, headers={"apikey": key, "Authorization": f"Bearer {key}"},
                   json=[row], timeout=15).raise_for_status()
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"llm_call ledger write failed: {e}", stacklevel=2)


def complete(role: str, messages: list[dict], purpose: str,
             document_id: str | None = None, roles: dict | None = None,
             response_format: dict | None = None) -> str:
    cfg = (roles or load_roles())[role]
    model = cfg["model"]
    extra = {"response_format": response_format} if response_format else {}
    resp = litellm.completion(
        model=model,
        messages=messages,
        temperature=cfg.get("temperature", 0),
        max_tokens=cfg.get("max_tokens", 4096),
        **extra,
    )
    try:
        cost = litellm.completion_cost(completion_response=resp)
    except Exception:  # noqa: BLE001 — unknown model in price table
        cost = None
    usage = getattr(resp, "usage", None)
    _ledger({
        "role": role,
        "model": model,
        "purpose": purpose,
        "document_id": document_id,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "cost_usd": round(cost, 6) if cost is not None else None,
    })
    return resp.choices[0].message.content


def smoke() -> int:
    """One tiny call per role whose provider key is present; skip the rest."""
    roles = load_roles()
    failures = 0
    for role, cfg in roles.items():
        model = cfg["model"]
        if not provider_key_present(model):
            print(f"SKIP {role} ({model}): provider key not set")
            continue
        try:
            out = complete(role, [{"role": "user",
                                   "content": "Reply with exactly: OK"}],
                           purpose="smoke-test")
            print(f"OK   {role} ({model}): {out.strip()[:40]!r}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {role} ({model}): {e}")
    return failures


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--role")
    ap.add_argument("--prompt")
    args = ap.parse_args()

    if args.smoke:
        sys.exit(smoke())
    if args.role and args.prompt:
        print(complete(args.role, [{"role": "user", "content": args.prompt}],
                       purpose="cli"))
        return
    ap.error("need --smoke or --role + --prompt")


if __name__ == "__main__":
    main()
