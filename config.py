"""Shared configuration for the Business Card Extractor."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Extraction fields (order matters: UI table columns + CSV/XLSX headers)
# ---------------------------------------------------------------------------
FIELDS = [
    "First Name",
    "Last Name",
    "Position / Job Title",
    "Company",
    "Location",
    "Phone Number",
    "Email Address",
]

# ---------------------------------------------------------------------------
# llama.cpp (llama-server) settings — overridable via environment variables
# or the in-app "Backend settings" dialog (persisted to settings.json)
# ---------------------------------------------------------------------------
DEFAULT_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8080")
DEFAULT_MODEL = os.environ.get("LLAMA_MODEL", "default")
DEFAULT_MAX_SIZE = int(os.environ.get("LLAMA_MAX_SIZE", "1024"))
DEFAULT_TIMEOUT = int(os.environ.get("LLAMA_TIMEOUT", "600"))

# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------
APP_NAME = "Business Card Extractor"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8090"))  # llama-server owns 8080

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
SETTINGS_FILE = BASE_DIR / "settings.json"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Prompt — strict, JSON-only, zero-hallucination
# ---------------------------------------------------------------------------
PROMPT = """
Analyze the provided business card image and extract the requested contact and professional information.

Strictly adhere to the following rules:
1. No Hallucination: Only extract text explicitly visible on the card. Do not guess, infer, or complete partial information.
2. Null Values: If a field is missing, illegible, or cannot be confidently determined, set its value exactly to "Null".
3. Name Splitting: Carefully separate the full name. Include prefixes (e.g., Dr., Mr.) and middle initials in the "First Name". Place multi-word surnames entirely in the "Last Name".
4. Multiple Numbers/Emails: If there are multiple phone numbers (e.g., office, mobile, fax) or emails, combine them into a single string separated by commas.
5. Location: Combine multi-line addresses into a single, comma-separated string.
6. Formatting: Output absolutely nothing but raw, valid JSON. Do not wrap the output in markdown blocks (e.g., no ```json) and do not include conversational text.

Expected JSON schema:
{
  "First Name": "",
  "Last Name": "",
  "Position / Job Title": "",
  "Company": "",
  "Location": "",
  "Phone Number": "",
  "Email Address": ""
}
"""
