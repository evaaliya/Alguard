"""
Heuristic text-based category inference. NOT ground truth: the authoritative
source of category is the MCC (Merchant Category Code) of the payment rail.
These rules exist to catch obvious evasions from a prompt-injected agent.
False positives are possible; severity floor + unknown-merchant rule provide
a second layer of defense.

See "What is still NOT solved" in README.
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
UNKNOWN_MERCHANT_CAP = float(os.environ.get("ALGUARD_UNKNOWN_MERCHANT_CAP", "250"))
SEVERITY_ORDER = {"Low": 0, "Medium": 1, "High": 2}
HIGH_RISK_CATEGORIES = tuple(c for c, s in CATEGORY_SEVERITY.items() if s == "High")

# Merchants you vouch for. Override with ALGUARD_TRUSTED_MERCHANTS="a,b,c".
_DEFAULT_TRUSTED = {"amazon", "amazon_basics", "whole_foods", "audible", "prime_video"}
# Merchant trust is decided per-user from human APPROVED resolutions in
# risk_evaluator / session_profiler, NOT from a static allowlist. The list
# below is kept only as UI/documentation hint; it does not lower severity.
TRUSTED_MERCHANTS = (
    {m.strip().lower() for m in os.environ["ALGUARD_TRUSTED_MERCHANTS"].split(",") if m.strip()}
    if os.environ.get("ALGUARD_TRUSTED_MERCHANTS") else _DEFAULT_TRUSTED
)

# Heuristic keyword rules over item + merchant text. NOT ground truth — the
# authoritative source of category is the MCC of the payment rail (see
# "What is still NOT solved" in README). These rules exist to catch the
# obvious evasions a prompt-injected agent would try.
#
# Design principles:
#   * `crypto` must not fire on the word "cryptography".
#   * `slots` must not fire on "time slot".
#   * `voucher` alone is a generic discount word — only fires with
#     gift/prepaid/cash/store/credit context.
#   * Gift-card evasions (steam wallet, itunes card, top-up, reload, ...)
#     are explicitly enumerated because they are the highest-severity case.
_TEXT_RULES = [
    # Require gift-card *intent* — bare "gift" / "gift bag" must NOT match.
    # Platform + card/code/wallet still catches common evasions.
    (re.compile(
        r"gift[\s_\-]*card|giftcard|prepaid[\s_\-]*card|"
        r"\bapple\s+gift(\s+card)?\b|"
        r"\bvoucher\b(?=[^.]*\b(gift|prepaid|cash|store|credit|code|card)\b)|"
        r"\b(steam|google[\s_\-]*play|itunes|app[\s_\-]*store|roblox|xbox|"
        r"playstation|nintendo[\s_\-]*eshop)\b[^.]*\b(card|code|wallet|credit|gift)\b|"
        r"\bamazon\b[^.]*\b(gift[\s_\-]*card|giftcard|wallet[\s_\-]*code)\b|"
        r"wallet[\s_\-]*code|\btop[\s_\-]*up\b|"
        r"\breload\b|store[\s_\-]*credit",
        re.I
    ), "gift_card"),
    (re.compile(
        r"\bcrypto(currenc(y|ies))?\b|\bbitcoin\b|\bbtc\b|\bethereum\b|\beth\b|"
        r"\busdt\b|\bbinance\b|\bcoinbase\b|\bnft\b",
        re.I
    ), "crypto"),
    (re.compile(
        r"casino|poker|lotter|sportsbook|gambl|"
        r"\bbet(s|ting)?\b|\bslot\s+machines?\b|\bslots\b",
        re.I
    ), "gambling"),
    (re.compile(
        r"wire[\s_\-]*transfer|western[\s_\-]*union|moneygram|remittance|\biban\b",
        re.I
    ), "wire_transfer"),
    (re.compile(
        r"\bloan\b|payday|cash[\s_\-]*advance",
        re.I
    ), "loan"),
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
