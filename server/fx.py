"""Exchange-rate lookup (external API) with validation, timeout and a small cache."""
import math
import re
import time

import httpx

API_URL = "https://open.er-api.com/v6/latest/{base}"   # free, no API key
CODE_RE = re.compile(r"^[A-Za-z]{3}$")
CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, dict]] = {}


class FxError(Exception):
    """A failure the model can understand and act on (bad input or service down)."""


def _check_code(code: str) -> str:
    if not CODE_RE.match(code or ""):
        raise FxError(f"Invalid currency code '{code}'. Use a 3-letter code such as USD, EUR or ETB.")
    return code.upper()


def _get_rates(base: str) -> dict:
    now = time.time()
    hit = _cache.get(base)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        resp = httpx.get(API_URL.format(base=base), timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        raise FxError("The exchange-rate service timed out. Try again in a moment.") from None
    except (httpx.HTTPError, ValueError):
        raise FxError("The exchange-rate service is unavailable (network or server problem). "
                      "Try again later, or ask the user for the amount in their main currency.") from None
    if data.get("result") != "success":
        raise FxError(f"Unsupported base currency '{base}'.")
    rates = data["rates"]
    _cache[base] = (now, rates)
    return rates


def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    if not math.isfinite(amount) or amount <= 0 or amount > 1e12:
        raise FxError("Amount must be a positive number.")
    base, target = _check_code(from_currency), _check_code(to_currency)
    if base == target:
        return {"amount": amount, "from": base, "to": target, "rate": 1.0, "converted": round(amount, 2)}
    rates = _get_rates(base)
    if target not in rates:
        raise FxError(f"Unsupported target currency '{target}'.")
    rate = rates[target]
    return {"amount": amount, "from": base, "to": target,
            "rate": round(rate, 6), "converted": round(amount * rate, 2)}
