# file: main.py
"""
FastAPI backend for crew management application with Azure OCR invoice processing.
"""

import os
import logging
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from azure_ocr import analyze_invoice_from_bytes, AzureOCRError

# --- Load .env explicitly from this project folder (Windows-safe) ---
PROJECT_DIR = Path(__file__).resolve().parent
DOTENV_PATH = PROJECT_DIR / ".env"
DOTENV_LOADED = load_dotenv(dotenv_path=DOTENV_PATH, override=False)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Crew Management API",
    description="Backend API for crew management with invoice OCR processing",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "message": "Crew Management API is running",
        "endpoints": {"health": "/health", "upload_invoice": "/upload-invoice-pdf"},
    }


@app.get("/health")
async def health_check():
    endpoint_raw = (os.getenv("AZURE_DOC_INTEL_ENDPOINT") or "")
    key_raw = (os.getenv("AZURE_DOC_INTEL_KEY") or "")

    endpoint = endpoint_raw.strip()
    key = key_raw.strip()

    host = ""
    try:
        if endpoint:
            if not endpoint.startswith("http"):
                endpoint = "https://" + endpoint
            host = urlparse(endpoint).netloc
    except Exception:
        host = ""

    return {
        "status": "healthy",
        "azure_configured": bool(endpoint) and bool(key),
        "azure_endpoint_set": bool(endpoint),
        "azure_key_set": bool(key),
        # diagnostics (no secrets):
        "dotenv_found": DOTENV_PATH.exists(),
        "dotenv_loaded": bool(DOTENV_LOADED),
        "endpoint_host": host,
        "cwd": os.getcwd(),
        "project_dir": str(PROJECT_DIR),
    }


@app.post("/upload-invoice-pdf")
async def upload_invoice_pdf(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid file. Only PDF files are accepted.")

    logger.info(f"Received invoice upload: {file.filename}")

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        result = analyze_invoice_from_bytes(file_bytes)

        # Remove raw SDK result from response (not JSON serializable)
        result.pop("raw", None)

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": result,
                "item_count": len(result.get("items", [])),
                "filename": file.filename,
            },
        )

    except AzureOCRError as e:
        logger.error(f"Azure OCR error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        logger.error("Unexpected error", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception", exc_info=True)
    return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
