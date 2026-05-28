"""
document_loaders.py
-------------------
Extracts plain text from every supported source type:

  - PDF      (.pdf)
  - Word     (.docx, .doc)
  - Text     (.txt)
  - Image    (.png, .jpg, .jpeg, .gif, .bmp) — via Tesseract OCR
  - Excel    (.xlsx, .xls, .csv)
  - Database (SQL Server query result)
  - URL      (web page via requests + BeautifulSoup)

Each loader returns a list of text strings (one per logical page / row /
section).  The indexing pipeline handles chunking.
"""

import logging
import tempfile
import os
from pathlib import Path
from typing import Any

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def load_pdf(file_path: str) -> list[str]:
    """Extract text from a PDF file, one string per page."""
    texts: list[str] = []
    try:
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    texts.append(text.strip())
    except ImportError:
        logger.error("pdfplumber not installed. Cannot load PDF: %s", file_path)
    except Exception as exc:
        logger.error("load_pdf failed for %s: %s", file_path, exc)
    return texts


# ---------------------------------------------------------------------------
# Word Document
# ---------------------------------------------------------------------------

def load_docx(file_path: str) -> list[str]:
    """Extract text from a .docx file (paragraph-level chunks)."""
    texts: list[str] = []
    try:
        import docx

        doc = docx.Document(file_path)
        current_block: list[str] = []
        for para in doc.paragraphs:
            if para.text.strip():
                current_block.append(para.text.strip())
            elif current_block:
                texts.append(" ".join(current_block))
                current_block = []
        if current_block:
            texts.append(" ".join(current_block))
    except ImportError:
        logger.error("python-docx not installed. Cannot load DOCX: %s", file_path)
    except Exception as exc:
        logger.error("load_docx failed for %s: %s", file_path, exc)
    return texts


# ---------------------------------------------------------------------------
# Plain Text
# ---------------------------------------------------------------------------

def load_txt(file_path: str) -> list[str]:
    """Read a plain text file and return it as a single-element list."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read().strip()
        return [content] if content else []
    except Exception as exc:
        logger.error("load_txt failed for %s: %s", file_path, exc)
        return []


# ---------------------------------------------------------------------------
# Image (OCR via Tesseract)
# ---------------------------------------------------------------------------

def load_image(file_path: str) -> list[str]:
    """
    OCR an image file and return extracted text.

    Requires: pytesseract + Tesseract-OCR installed on the system.
    Fallback: returns empty list with a warning.
    """
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(file_path)
        text = pytesseract.image_to_string(img).strip()
        return [text] if text else []
    except ImportError:
        logger.warning(
            "pytesseract or Pillow not installed. Image OCR skipped for: %s", file_path
        )
        return []
    except Exception as exc:
        logger.error("load_image failed for %s: %s", file_path, exc)
        return []


# ---------------------------------------------------------------------------
# Excel / CSV
# ---------------------------------------------------------------------------

def load_excel(file_path: str) -> list[str]:
    """
    Load an Excel or CSV file.  Each sheet/table is converted to a
    Markdown-style text block for effective embedding.
    """
    texts: list[str] = []
    try:
        import pandas as pd

        path = Path(file_path)
        ext = path.suffix.lower()

        if ext == ".csv":
            df_map = {"Sheet1": pd.read_csv(file_path, dtype=str)}
        else:
            df_map = pd.read_excel(file_path, sheet_name=None, dtype=str)

        for sheet_name, df in df_map.items():
            df = df.fillna("")
            # Convert each row to "col1: val1 | col2: val2 | ..." format
            rows_text = []
            for _, row in df.iterrows():
                row_str = " | ".join(
                    f"{col}: {val}" for col, val in row.items() if str(val).strip()
                )
                if row_str:
                    rows_text.append(row_str)

            if rows_text:
                block = f"[Sheet: {sheet_name}]\n" + "\n".join(rows_text)
                texts.append(block)

    except ImportError:
        logger.error("pandas/openpyxl not installed. Cannot load Excel: %s", file_path)
    except Exception as exc:
        logger.error("load_excel failed for %s: %s", file_path, exc)
    return texts


# ---------------------------------------------------------------------------
# Database Query
# ---------------------------------------------------------------------------

def load_database_query(
    query_sql: str, role_column: str | None = None
) -> list[dict[str, Any]]:
    """
    Execute a SQL query against the configured SQL Server.

    Args:
        query_sql:   The SELECT statement to execute.
        role_column: Optional column name whose value maps to a role.

    Returns:
        List of row dicts.  The caller decides how to format them.
    """
    from app.db.db_manager import execute_raw

    try:
        rows = execute_raw(query_sql)
        logger.info("Database source loaded %d rows", len(rows))
        return rows
    except Exception as exc:
        logger.error("load_database_query failed: %s", exc)
        return []


def database_rows_to_texts(
    rows: list[dict[str, Any]],
    role_column: str | None = None,
    user_roles: list[str] | None = None,
) -> list[str]:
    """
    Convert database rows to text blocks, optionally filtered by user roles.

    If role_column is specified, only rows where that column's value matches
    one of the user's roles are included.
    """
    texts: list[str] = []
    for row in rows:
        if role_column and user_roles is not None:
            row_role = str(row.get(role_column, "")).strip().lower()
            if row_role and row_role not in [r.lower() for r in user_roles]:
                continue  # Skip rows the user is not authorised to see

        row_text = " | ".join(
            f"{k}: {v}" for k, v in row.items() if v is not None and str(v).strip()
        )
        if row_text:
            texts.append(row_text)
    return texts


# ---------------------------------------------------------------------------
# URL (Web Page)
# ---------------------------------------------------------------------------

def load_url(url: str) -> list[str]:
    """
    Fetch a web page and extract its main text content.

    Uses BeautifulSoup to strip navigation, scripts, and boilerplate.
    """
    texts: list[str] = []
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (EnterpriseRAGBot/1.0)"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove unwanted tags
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Extract meaningful text blocks
        for element in soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "td", "th"]):
            text = element.get_text(separator=" ", strip=True)
            if len(text) > 30:  # Skip tiny fragments
                texts.append(text)

    except ImportError:
        logger.error("requests or beautifulsoup4 not installed for URL loading: %s", url)
    except Exception as exc:
        logger.error("load_url failed for %s: %s", url, exc)
    return texts


# ---------------------------------------------------------------------------
# In-memory file loader (for chat-time upload)
# ---------------------------------------------------------------------------

def load_uploaded_file(file_bytes: bytes, filename: str) -> list[str]:
    """
    Load content from an in-memory uploaded file.

    Saves to a temp file then delegates to the appropriate loader.
    Uses pathlib.Path throughout for platform independence (Windows + Linux).
    Returns extracted text segments.
    """
    suffix = Path(filename).suffix.lower()
    tmp_path: str | None = None

    try:
        # delete=False so we can pass the path to loaders that open by path
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        logger.debug("Processing uploaded file: %s (type=%s)", filename, suffix)

        loader_map = {
            ".pdf":  load_pdf,
            ".docx": load_docx,
            ".doc":  load_docx,
            ".txt":  load_txt,
            ".png":  load_image,
            ".jpg":  load_image,
            ".jpeg": load_image,
            ".gif":  load_image,
            ".bmp":  load_image,
            ".xlsx": load_excel,
            ".xls":  load_excel,
            ".csv":  load_excel,
        }

        loader = loader_map.get(suffix)
        if loader is None:
            logger.warning("No loader for extension '%s' (file: %s)", suffix, filename)
            return []

        return loader(tmp_path)

    except Exception as exc:
        logger.error("load_uploaded_file failed for %s: %s", filename, exc)
        return []
    finally:
        # Only attempt deletion if the temp file was actually created
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
