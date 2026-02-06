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

# ========== DEPARTMENT CONFIGURATION ==========
DEPARTMENT_MANAGERS = {
    "produce": "Kevin Taylor",
    "dairy": "Matteo Hermani",
    "meat": "Kevin Taylor",
    "cosmetics": "Matteo Hermani",
    "pets": "Kevin Taylor",
    "grocery": "Matteo Hermani"
}

DEPARTMENTS = list(DEPARTMENT_MANAGERS.keys())

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
            
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "data": result,
                    "item_count": len(result.get("items", [])),
                    "filename": file.filename,
                },
            )
        except Exception as db_error:
            logger.error(f"Database save failed: {db_error}")
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(db_error)}")
        finally:
            db.close()

    except AzureOCRError as e:
        logger.error(f"OCR processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")


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


# ========== HELPER FUNCTION ==========
def format_invoice(invoice):
    """Format invoice object for API response"""
    return {
        "id": invoice.id,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "vendor_name": invoice.vendor_name,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "total_amount": invoice.total_amount,
        "subtotal": invoice.subtotal,
        "filename": invoice.filename,
        
        # Tax breakdown
        "gst": invoice.gst,
        "pst": invoice.pst,
        "hst": invoice.hst,
        "qst": invoice.qst,
        "us_tax": invoice.us_tax,
        "tax_total": invoice.tax_total,
        "tax_notes": invoice.tax_notes,
        
        # Workflow fields
        "current_stage": invoice.current_stage,
        "stage_status": invoice.stage_status,
        
        # Pre-coding
        "gl_account": invoice.gl_account,
        "cost_center": invoice.cost_center,
        "department": invoice.department,
        "po_number": invoice.po_number,
        "precoder": invoice.precoder,
        "precoding_date": invoice.precoding_date.isoformat() if invoice.precoding_date else None,
        
        # Department review
        "dept_reviewer": invoice.dept_reviewer,
        "dept_status": invoice.dept_status,
        "dept_review_date": invoice.dept_review_date.isoformat() if invoice.dept_review_date else None,
        "dept_review_notes": invoice.dept_review_notes,
        
        "items": json.loads(invoice.items_json) if invoice.items_json else []
    }


# ========== STAGE 2: PRE-CODING ENDPOINTS ==========

@app.get("/api/invoices/precoding-queue")
async def get_precoding_queue():
    """Get all invoices waiting for pre-coding (stage 1)"""
    db = next(get_db())
    try:
        invoices = db.query(Invoice).filter(
            Invoice.current_stage == 1,
            Invoice.stage_status == "captured"
        ).order_by(Invoice.created_at.desc()).all()
        
        return {
            "success": True,
            "count": len(invoices),
            "invoices": [format_invoice(inv) for inv in invoices]
        }
    finally:
        db.close()


@app.post("/api/invoices/{invoice_id}/precode")
async def precode_invoice(
    invoice_id: int,
    gl_account: str,
    cost_center: str,
    department: str,
    po_number: str = None,
    receipt_number: str = None,
    precoder: str = None,
    notes: str = None,
    gst: float = None,
    pst: float = None,
    hst: float = None,
    qst: float = None,
    us_tax: float = None,
    tax_total: float = None,
    tax_notes: str = None
):
    """Complete pre-coding and move to Stage 3 (Dept Review)"""
    
    if department not in DEPARTMENTS:
        raise HTTPException(400, f"Invalid department. Must be one of: {', '.join(DEPARTMENTS)}")
    
    db = next(get_db())
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        # Update pre-coding fields
        invoice.gl_account = gl_account
        invoice.cost_center = cost_center
        invoice.department = department
        invoice.po_number = po_number
        invoice.receipt_number = receipt_number
        invoice.precoder = precoder
        invoice.precoding_date = datetime.utcnow()
        invoice.precoding_notes = notes
        
        # Update tax fields
        invoice.gst = gst
        invoice.pst = pst
        invoice.hst = hst
        invoice.qst = qst
        invoice.us_tax = us_tax
        invoice.tax_total = tax_total
        invoice.tax_notes = tax_notes
        
        # Advance to Stage 3
        invoice.current_stage = 3
        invoice.stage_status = "dept_review"
        
        # Auto-assign to department manager
        invoice.dept_reviewer = DEPARTMENT_MANAGERS[department]
        invoice.dept_assigned_date = datetime.utcnow()
        invoice.dept_status = "pending"
        
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = precoder
        
        db.commit()
        db.refresh(invoice)
        
        return {
            "success": True,
            "message": f"Invoice pre-coded and assigned to {invoice.dept_reviewer}",
            "invoice": format_invoice(invoice)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Pre-coding failed: {e}")
        raise HTTPException(500, f"Pre-coding failed: {str(e)}")
    finally:
        db.close()


# ========== STAGE 3: DEPARTMENT REVIEW ENDPOINTS ==========

@app.get("/api/invoices/dept-queue/{reviewer_name}")
async def get_dept_queue(reviewer_name: str):
    """Get invoices pending review for a specific manager"""
    db = next(get_db())
    try:
        invoices = db.query(Invoice).filter(
            Invoice.current_stage == 3,
            Invoice.stage_status == "dept_review",
            Invoice.dept_reviewer == reviewer_name,
            Invoice.dept_status == "pending"
        ).order_by(Invoice.dept_assigned_date.desc()).all()
        
        return {
            "success": True,
            "reviewer": reviewer_name,
            "count": len(invoices),
            "invoices": [format_invoice(inv) for inv in invoices]
        }
    finally:
        db.close()


@app.post("/api/invoices/{invoice_id}/dept-approve")
async def dept_approve_invoice(
    invoice_id: int,
    reviewer: str,
    notes: str = None
):
    """Department manager approves invoice"""
    db = next(get_db())
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        if invoice.dept_reviewer != reviewer:
            raise HTTPException(403, "You are not assigned to review this invoice")
        
        # Approve
        invoice.dept_status = "approved"
        invoice.dept_review_date = datetime.utcnow()
        invoice.dept_review_notes = notes
        invoice.stage_status = "approved"  # Final status for stages 1-3
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = reviewer
        
        db.commit()
        db.refresh(invoice)
        
        return {
            "success": True,
            "message": "Invoice approved by department manager",
            "invoice": format_invoice(invoice)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Approval failed: {e}")
        raise HTTPException(500, f"Approval failed: {str(e)}")
    finally:
        db.close()


@app.post("/api/invoices/{invoice_id}/dept-reject")
async def dept_reject_invoice(
    invoice_id: int,
    reviewer: str,
    notes: str
):
    """Department manager rejects invoice - sends back to pre-coding"""
    db = next(get_db())
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        if invoice.dept_reviewer != reviewer:
            raise HTTPException(403, "You are not assigned to review this invoice")
        
        # Reject and send back to Stage 2
        invoice.dept_status = "rejected"
        invoice.dept_review_date = datetime.utcnow()
        invoice.dept_review_notes = notes
        invoice.current_stage = 2
        invoice.stage_status = "precoding"
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = reviewer
        
        db.commit()
        db.refresh(invoice)
        
        return {
            "success": True,
            "message": "Invoice rejected and sent back for re-coding",
            "invoice": format_invoice(invoice)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Rejection failed: {e}")
        raise HTTPException(500, f"Rejection failed: {str(e)}")
    finally:
        db.close()


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception", exc_info=True)
    return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
