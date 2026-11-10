"""
Load configuration from config/settings.yaml and the environment.

Secrets (API key, database URL) come only from the environment.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


def repo_root() -> Path:
    """Repository root (the code/ folder: pyproject.toml and packages/)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return parent
    return here.parents[4]


_ROOT = repo_root()
load_dotenv(_ROOT / ".env")


def _read_yaml() -> dict:
    path = _ROOT / "config" / "settings.yaml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Settings file must be a mapping: {path}")
    return loaded


_SETTINGS = _read_yaml()


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _nested(name: str) -> dict:
    block = _SETTINGS.get(name) or {}
    return block if isinstance(block, dict) else {}


def _path_setting(env_key: str, yaml_value: str, default: Path) -> Path:
    raw = os.environ.get(env_key, "").strip() or str(yaml_value or "").strip()
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        path = (_ROOT / path).resolve()
    return path


_ocr = _nested("ocr")
_qa = _nested("qa")
_chunk = _nested("chunking")
_embed = _nested("embedding")
_chat = _nested("chat")
_rag = _nested("rag")
_log = _nested("logging")

KB_VERSION: str = str(_SETTINGS.get("kb_version") or "0.1")
KNOWLEDGE_BASE: Path = _path_setting(
    "KNOWLEDGE_BASE",
    str(_SETTINGS.get("knowledge_base") or ""),
    (_ROOT.parent / "knowledge-base").resolve(),
)
PORTAL_MAP_URL: str = str(
    _SETTINGS.get("portal_map_url") or "https://www.landis.org.uk/soilscapes/"
)

SOURCE_RAW: Path = KNOWLEDGE_BASE / "01_Source_raw"
SOURCE_CLEANED: Path = KNOWLEDGE_BASE / "02_Source_cleaned"
STRUCTURED_DATA: Path = KNOWLEDGE_BASE / "03_Structured_data"
ONTOLOGY: Path = KNOWLEDGE_BASE / "04_Ontology"
METADATA_DIR: Path = KNOWLEDGE_BASE / "05_Metadata"
EXCLUDED: Path = KNOWLEDGE_BASE / "06_Excluded"
TEST_QUESTIONS: Path = KNOWLEDGE_BASE / "07_Test_questions"
REGISTER_PATH: Path = METADATA_DIR / "corpus_register.csv"
REGISTER_WORKBOOK_PATH: Path = METADATA_DIR / "corpus_register.xlsx"
INDEX_MANIFEST_PATH: Path = METADATA_DIR / "index_manifest.json"
OCR_LOG_PATH: Path = SOURCE_CLEANED / "_logs" / "ocr-runs.jsonl"
OCR_JOBS_DIR: Path = SOURCE_CLEANED / "_jobs"

EXAMPLE_REGISTER_PATH: Path = _ROOT / "data" / "corpus_register.example.csv"
SQL_DIR: Path = _ROOT / "sql"

DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()
MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "").strip()

OCR_MODEL: str = os.environ.get("MISTRAL_OCR_MODEL", str(_ocr.get("model") or "mistral-ocr-latest"))
OCR_TABLE_FORMAT: str = str(_ocr.get("table_format") or "html")
OCR_EXTRACT_HEADER: bool = bool(_ocr.get("extract_header", True))
OCR_EXTRACT_FOOTER: bool = bool(_ocr.get("extract_footer", True))
OCR_INCLUDE_BLOCKS: bool = bool(_ocr.get("include_blocks", True))
OCR_CONFIDENCE_GRANULARITY: str = str(_ocr.get("confidence_scores_granularity") or "word")
OCR_INCLUDE_IMAGE_BASE64: bool = bool(_ocr.get("include_image_base64", False))
OCR_BATCH_USD_PER_PAGE: float = float(_ocr.get("batch_usd_per_page") or 0.002)
OCR_STANDARD_USD_PER_PAGE: float = float(_ocr.get("standard_usd_per_page") or 0.004)

QA_LOW_CONFIDENCE: float = float(_qa.get("low_confidence") or 0.6)
QA_MAX_LOW_WORDS: int = int(_qa.get("max_low_confidence_words") or 5)
QA_NEAR_EMPTY_WORDS: int = int(_qa.get("near_empty_words") or 8)

CHUNK_MIN_WORDS: int = int(_chunk.get("target_min_words") or 300)
CHUNK_MAX_WORDS: int = int(_chunk.get("target_max_words") or 800)
CHUNK_FALLBACK_WORDS: int = int(_chunk.get("fallback_words") or 500)
CHUNK_OVERLAP_WORDS: int = int(_chunk.get("overlap_words") or 100)

MISTRAL_EMBED_MODEL: str = os.environ.get(
    "MISTRAL_EMBED_MODEL", str(_embed.get("model") or "mistral-embed")
)
EMBEDDING_DIMENSION: int = int(
    os.environ.get("EMBEDDING_DIMENSION", str(_embed.get("dimension") or 1024))
)
EMBED_BATCH_SIZE: int = int(_embed.get("batch_size") or 32)

MISTRAL_CHAT_MODEL: str = os.environ.get(
    "MISTRAL_CHAT_MODEL", str(_chat.get("model") or "mistral-small-latest")
)
CHAT_TEMPERATURE: float = float(_chat.get("temperature") or 0.2)

RAG_TOP_K: int = int(os.environ.get("RAG_TOP_K", str(_rag.get("top_k") or 8)))
RAG_MMR_ENABLED: bool = _env_bool("RAG_MMR_ENABLED", bool(_rag.get("mmr_enabled", True)))
RAG_MMR_LAMBDA: float = min(1.0, max(0.0, float(os.environ.get("RAG_MMR_LAMBDA", str(_rag.get("mmr_lambda") or 0.58)))))
RAG_MMR_FETCH_MULT: int = min(8, max(2, int(os.environ.get("RAG_MMR_FETCH_MULT", str(_rag.get("mmr_fetch_mult") or 3)))))
RAG_MMR_FETCH_CAP: int = min(64, max(12, int(os.environ.get("RAG_MMR_FETCH_CAP", str(_rag.get("mmr_fetch_cap") or 40)))))
RAG_RRF_K: int = int(_rag.get("rrf_k") or 60)

LOG_CONVERSATIONS: bool = _env_bool("LOG_CONVERSATIONS", bool(_log.get("conversations", True)))
CONVERSATION_RETENTION_DAYS: int = max(
    0, int(os.environ.get("CONVERSATION_RETENTION_DAYS", str(_log.get("retention_days") or 365)))
)
CHAT_HISTORY_ENABLED: bool = _env_bool(
    "CHAT_HISTORY_ENABLED", bool(_log.get("history_enabled", True))
)
CHAT_HISTORY_TURNS: int = max(0, int(os.environ.get("CHAT_HISTORY_TURNS", str(_log.get("history_turns") or 3))))
CHAT_HISTORY_MAX_ANSWER_CHARS: int = max(
    200,
    int(os.environ.get("CHAT_HISTORY_MAX_ANSWER_CHARS", str(_log.get("history_max_answer_chars") or 1500))),
)
CHAT_HISTORY_EXPAND_RETRIEVAL: bool = _env_bool(
    "CHAT_HISTORY_EXPAND_RETRIEVAL", bool(_log.get("history_expand_retrieval", True))
)
CHAT_HISTORY_RETRIEVAL_MAX_CHARS: int = max(
    400,
    int(
        os.environ.get(
            "CHAT_HISTORY_RETRIEVAL_MAX_CHARS",
            str(_log.get("history_retrieval_max_chars") or 2000),
        )
    ),
)

CONVERSATIONS_TABLE: str = "landis_conversations"

OCR_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".docx",
    ".pptx",
)
TEXT_EXTENSIONS: tuple[str, ...] = (".txt", ".csv", ".html", ".md")
IMAGE_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")

KNOWLEDGE_FOLDERS: tuple[str, ...] = (
    "01_Source_raw",
    "02_Source_cleaned",
    "03_Structured_data",
    "04_Ontology",
    "05_Metadata",
    "06_Excluded",
    "07_Test_questions",
)
