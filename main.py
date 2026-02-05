# file: main.py
"""
FastAPI backend for retail/grocery invoice management with Azure OCR processing and price change tracking."""

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
import json
from datetime import datetime
from sqlalchemy.orm import Session
from database import engine, get_db
from models import Invoice
from init_db import init_database

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
    title="Retail Invoice Management API",
    description="Backend API for retail/grocery invoice processing with OCR and price change tracking",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    try:
        init_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "message": "Retail Invoice Management API is running",
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
        
        # Save to database
        db = next(get_db())
        try:
            invoice = Invoice(
                source="ocr",
                filename=file.filename,
                status="success",
                vendor_name=result.get("vendor_name"),
                invoice_date=result.get("invoice_date"),
                invoice_number=result.get("invoice_id"),
                total_amount=result.get("total"),
                subtotal=result.get("subtotal"),
                tax=result.get("total_tax"),
                items_json=json.dumps(result.get("items", [])),
                raw_ocr_data=json.dumps(result)
            )
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            logger.info(f"Invoice saved to database with ID: {invoice.id}")
        except Exception as db_error:
            logger.error(f"Database save failed: {db_error}")
            db.rollback()
        finally:
            db.close()

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "data": result,
                "item_count": len(result.get("items", [])),
                "filename": file.filename,
            },
        )

@app.get("/recent-invoices")
async def get_recent_invoices(limit: int = 100):
    """Fetch recent invoice uploads from database"""
    db = next(get_db())
    try:
        invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(limit).all()
        
        result = []
        for inv in invoices:
            result.append({
                "id": inv.id,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "status": inv.status,
                "error_message": inv.error_message,
                "source": inv.source,
                "filename": inv.filename,
                "vendor_name": inv.vendor_name,
                "invoice_date": inv.invoice_date,
                "invoice_number": inv.invoice_number,
                "total_amount": inv.total_amount,
                "subtotal": inv.subtotal,
                "tax": inv.tax,
                "items": json.loads(inv.items_json) if inv.items_json else [],
            })
        
        return JSONResponse(
            status_code=200,
            content={"success": True, "count": len(result), "invoices": result}
        )
    except Exception as e:
        logger.error(f"Failed to fetch invoices: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch invoices")
    finally:
        db.close()

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
