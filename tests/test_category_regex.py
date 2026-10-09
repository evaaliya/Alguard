"""
1.5 — category regex hygiene:
  * no false positives on benign text ("Applied Cryptography", "time slot",
    "discount voucher for pizza", "gift bag", "Amazon gift wrap")
  * catches gift-card evasions (steam wallet code, itunes card, apple gift,
    top-up, reload, store credit)
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils.category_risk import infer_category_from_text


# --- positives: must be classified as gift_card ---

@pytest.mark.parametrize("text", [
    "Steam wallet code",
    "Google Play card",
    "iTunes card",
    "App Store code",
    "Roblox gift card",
    "Xbox card",
    "PlayStation Store card",
    "Nintendo eShop card",
    "Apple gift",
    "Apple gift card",
    "Amazon gift card",
    "Amazon top-up",
    "Mobile reload",
    "Store credit",
])
def test_gift_card_evasions_are_caught(text):
    cat = infer_category_from_text(text, "")
    assert cat == "gift_card", f"{text!r} → {cat!r}, expected 'gift_card'"


# --- negatives: must NOT be classified as a risky category ---

@pytest.mark.parametrize("text,merchant", [
    ("Applied Cryptography", "bookstore"),
    ("Cryptography fundamentals", "coursera"),
    ("Book a time slot", "calendly"),
    ("Time slot reservation", "opentable"),
    ("Discount voucher for pizza", "pizzahut"),
    ("Voucher-based loyalty", "starbucks"),
    ("Birthday gift bag", "partycity"),
    ("Gift wrapping paper", "amazon"),
    ("Amazon Basics HDMI cable", "amazon"),
])
def test_benign_text_not_flagged(text, merchant):
    cat = infer_category_from_text(text, merchant)
    assert cat is None, f"{text!r} / {merchant!r} → {cat!r}, expected None"
