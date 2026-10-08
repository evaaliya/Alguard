"""
The agent-declared `category` is UNTRUSTED input (a prompt-injected agent can say
"other" for a crypto purchase). The server therefore derives its own view:

  1. infer_category_from_text(): keyword rules over item + merchant text;
  2. a merchant that is neither on the trusted list nor already trusted from this
     user's own history gets a Medium severity FLOOR (unknown != Low);
  3. effective severity = max(declared, inferred, floor).

This is a heuristic, not a ground truth. The real fix is MCC / merchant data from the
payment rail -- see "what's still not solved" in the README.
"""
import os
import re
from typing import List, Optional

CATEGORY_VALUES = [
    "electronics", "groceries", "dining", "entertainment", "travel",
    "subscription", "luxury", "healthcare", "prescription",
    "financial", "loan", "wire_transfer", "gift_card", "gambling", "crypto",
    "other",
]

CATEGORY_SEVERITY = {
    "wire_transfer": "High", "gift_card": "High", "gambling": "High", "crypto": "High", "loan": "High",
    "financial": "Medium", "healthcare": "Medium", "prescription": "Medium", "luxury": "Medium",
    "subscription": "Low", "electronics": "Low", "groceries": "Low", "dining": "Low",
    "entertainment": "Low", "travel": "Low", "other": "Low",
}
SEVERITY_ORDER = {"Low": 0, "Medium": 1, "High": 2}
HIGH_RISK_CATEGORIES = tuple(c for c, s in CATEGORY_SEVERITY.items() if s == "High")

# Merchants you vouch for. Override with ALGUARD_TRUSTED_MERCHANTS="a,b,c".
_DEFAULT_TRUSTED = {"amazon", "amazon_basics", "whole_foods", "audible", "prime_video"}
TRUSTED_MERCHANTS = (
    {m.strip().lower() for m in os.environ["ALGUARD_TRUSTED_MERCHANTS"].split(",") if m.strip()}
    if os.environ.get("ALGUARD_TRUSTED_MERCHANTS") else _DEFAULT_TRUSTED
)

_TEXT_RULES = [
    (re.compile(r"gift[\s_\-]*card|giftcard|prepaid[\s_\-]*card|\bvoucher", re.I), "gift_card"),
    (re.compile(r"crypto|bitcoin|\bbtc\b|ethereum|\beth\b|\busdt\b|binance|coinbase|\bnft\b", re.I), "crypto"),
    (re.compile(r"casino|poker|lotter|sportsbook|gambl|\bbet(s|ting)?\b|slots?\b", re.I), "gambling"),
    (re.compile(r"wire[\s_\-]*transfer|western[\s_\-]*union|moneygram|remittance|\biban\b", re.I), "wire_transfer"),
    (re.compile(r"\bloan\b|payday|cash[\s_\-]*advance", re.I), "loan"),
]


def infer_category_from_text(item: str, merchant: str) -> Optional[str]:
    text = f"{item or ''} {merchant or ''}"
    for rx, cat in _TEXT_RULES:
        if rx.search(text):
            return cat
    return None


def classify_category_severity(category: str) -> str:
    return CATEGORY_SEVERITY.get(category, "Low")


def max_severity(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEVERITY_ORDER[s])


def detect_risky_category(category: str) -> List[str]:
    return [category] if category in HIGH_RISK_CATEGORIES else []
