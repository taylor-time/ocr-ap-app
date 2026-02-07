# file: main.py
"""
FastAPI backend for retail/grocery invoice management with Azure OCR processing and price change tracking."""

import os
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from azure_ocr import analyze_invoice_from_bytes, AzureOCRError
import json
import csv
import io
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import engine, SessionLocal
from models import Invoice, PriceHistory, PriceChange
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
    "grocery": "Matteo Hermani",
    "bakery": "Kevin Taylor",
    "contractor": "Kevin Taylor"
}

DEPARTMENTS = list(DEPARTMENT_MANAGERS.keys())


# ========== PYDANTIC REQUEST MODELS ==========
class PrecodeRequest(BaseModel):
    gl_account: str
    cost_center: str
    department: str
    po_number: Optional[str] = None
    receipt_number: Optional[str] = None
    precoder: Optional[str] = None
    notes: Optional[str] = None
    gst: Optional[float] = None
    pst: Optional[float] = None
    hst: Optional[float] = None
    qst: Optional[float] = None
    us_tax: Optional[float] = None
    tax_total: Optional[float] = None
    tax_notes: Optional[str] = None


class DeptApproveRequest(BaseModel):
    reviewer: str
    notes: Optional[str] = None


class DeptRejectRequest(BaseModel):
    reviewer: str
    notes: str


class PriceChangeReviewRequest(BaseModel):
    reviewed_by: str
    review_status: str  # "acknowledged" or "escalated"
    review_notes: Optional[str] = None


def get_db_session() -> Session:
    """Create a new database session (caller must close it)."""
    return SessionLocal()


def clean_price(value) -> Optional[float]:
    """Convert OCR price strings like '$1,533.48' to float.
    Returns None if the value cannot be parsed."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # Remove $, commas, spaces, and other currency symbols
        cleaned = value.strip().replace("$", "").replace(",", "").replace(" ", "")
        # Remove other common currency prefixes/suffixes
        for sym in ["CAD", "USD", "EUR", "GBP", "£", "€"]:
            cleaned = cleaned.replace(sym, "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            logger.warning(f"Could not parse price value: {value!r}")
            return None
    return None


def clean_line_items(items: list) -> list:
    """Clean all price fields in OCR line items so they are proper floats."""
    cleaned = []
    for item in items:
        cleaned_item = dict(item)  # shallow copy
        cleaned_item["unit_price"] = clean_price(item.get("unit_price"))
        cleaned_item["line_total"] = clean_price(item.get("line_total"))
        cleaned_item["tax_amount"] = clean_price(item.get("tax_amount"))
        # Clean quantity too (sometimes comes as string)
        qty = item.get("quantity")
        if qty is not None:
            try:
                cleaned_item["quantity"] = float(str(qty).replace(",", ""))
            except (ValueError, TypeError):
                cleaned_item["quantity"] = None
        cleaned.append(cleaned_item)
    return cleaned


def save_price_history(db: Session, invoice: Invoice):
    """Save all line items from an approved invoice into price_history for future comparisons."""
    items = json.loads(invoice.items_json) if invoice.items_json else []
    if not items or not invoice.vendor_name:
        return

    count = 0
    for item in items:
        description = (item.get("description") or "").strip()
        if not description:
            continue

        ph = PriceHistory(
            invoice_id=invoice.id,
            vendor_name=invoice.vendor_name.strip(),
            item_description=description,
            item_sku=item.get("sku") or None,
            unit_price=clean_price(item.get("unit_price")),
            quantity=float(item["quantity"]) if item.get("quantity") else None,
            unit=item.get("unit") or None,
            line_total=clean_price(item.get("line_total")),
            invoice_date=invoice.invoice_date,
            department=invoice.department,
        )
        db.add(ph)
        count += 1

    logger.info(f"Saved {count} price history records for invoice {invoice.id} ({invoice.vendor_name})")


def detect_price_changes(db: Session, invoice: Invoice) -> int:
    """Compare line items against the most recent previous invoice from the same vendor.
    Creates PriceChange records for any differences. Returns count of changes found."""
    items = json.loads(invoice.items_json) if invoice.items_json else []
    if not items or not invoice.vendor_name:
        return 0

    vendor = invoice.vendor_name.strip()

    # Find the most recent previous invoice ID from this vendor (not the current one)
    prev_invoice = db.query(Invoice).filter(
        Invoice.vendor_name == vendor,
        Invoice.id != invoice.id,
        Invoice.stage_status.in_(["approved", "price_review", "complete"])
    ).order_by(Invoice.created_at.desc()).first()

    if not prev_invoice:
        logger.info(f"No previous invoice found for vendor '{vendor}' - skipping price comparison")
        return 0

    # Build a lookup of previous prices: description -> PriceHistory record
    prev_prices = {}
    prev_history = db.query(PriceHistory).filter(
        PriceHistory.invoice_id == prev_invoice.id
    ).all()

    for ph in prev_history:
        key = ph.item_description.strip().lower()
        prev_prices[key] = ph

    # Compare each current item against previous
    changes_found = 0
    for item in items:
        description = (item.get("description") or "").strip()
        if not description:
            continue

        current_price = clean_price(item.get("unit_price"))
        if current_price is None:
            continue

        key = description.lower()
        prev = prev_prices.get(key)

        if not prev or prev.unit_price is None:
            continue  # New item or no previous price — not a change

        if abs(current_price - prev.unit_price) < 0.001:
            continue  # Same price — no change

        # Price changed — create record
        diff = current_price - prev.unit_price
        pct = (diff / prev.unit_price) * 100 if prev.unit_price != 0 else 0

        pc = PriceChange(
            invoice_id=invoice.id,
            previous_invoice_id=prev_invoice.id,
            vendor_name=vendor,
            item_description=description,
            item_sku=item.get("sku") or None,
            department=invoice.department,
            previous_price=prev.unit_price,
            new_price=current_price,
            price_difference=round(diff, 2),
            percent_change=round(pct, 2),
            previous_invoice_date=prev_invoice.invoice_date,
            new_invoice_date=invoice.invoice_date,
        )
        db.add(pc)
        changes_found += 1

        logger.info(
            f"Price change detected: {vendor} / {description}: "
            f"${prev.unit_price:.2f} -> ${current_price:.2f} ({pct:+.1f}%)"
        )

    return changes_found


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
        
        # Auto-migrate: add any missing columns to existing tables
        from sqlalchemy import text, inspect
        with engine.connect() as conn:
            inspector = inspect(engine)
            
            # Check invoices table for new columns
            existing_cols = {col["name"] for col in inspector.get_columns("invoices")}
            
            migrations = {
                "price_changes_detected": "ALTER TABLE invoices ADD COLUMN price_changes_detected BOOLEAN DEFAULT FALSE",
                "price_change_count": "ALTER TABLE invoices ADD COLUMN price_change_count INTEGER DEFAULT 0",
            }
            
            for col_name, sql in migrations.items():
                if col_name not in existing_cols:
                    conn.execute(text(sql))
                    logger.info(f"Added missing column: invoices.{col_name}")
            
            conn.commit()
        
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

    except AzureOCRError as e:
        logger.error(f"OCR processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")

    # Clean price values from OCR (they come back as "$1,533.48" strings)
    total_amount = clean_price(result.get("total"))
    subtotal = clean_price(result.get("subtotal"))

    # Extract tax from OCR result (azure_ocr.py returns "tax_total" from Azure's TotalTax field)
    ocr_tax = clean_price(result.get("tax_total"))

    # Auto-detect tax type from raw OCR text content
    # Azure returns the full text — look for GST/HST/PST keywords to classify
    ocr_gst = None
    ocr_pst = None
    ocr_hst = None
    ocr_tax_notes = None
    
    if ocr_tax and ocr_tax > 0:
        raw_text = json.dumps(result).upper()  # search all OCR output
        
        if "HST" in raw_text:
            ocr_hst = ocr_tax
            ocr_tax_notes = "HST (auto-detected from OCR)"
        elif "GST" in raw_text and "PST" in raw_text:
            # GST+PST province — estimate split (GST=5%, PST varies)
            # Use subtotal to calculate if available
            if subtotal and subtotal > 0:
                ocr_gst = round(subtotal * 0.05, 2)
                ocr_pst = round(ocr_tax - ocr_gst, 2)
                if ocr_pst < 0:
                    ocr_pst = None
                    ocr_gst = ocr_tax
            else:
                ocr_gst = ocr_tax  # fallback: put it all in GST
            ocr_tax_notes = "GST+PST (auto-detected from OCR)"
        elif "GST" in raw_text:
            ocr_gst = ocr_tax
            ocr_tax_notes = "GST (auto-detected from OCR)"
        else:
            # Can't determine type — store as total only
            ocr_tax_notes = "Tax type unknown (review needed)"
    
    logger.info(f"Tax auto-detect: total={ocr_tax}, gst={ocr_gst}, pst={ocr_pst}, hst={ocr_hst}, notes={ocr_tax_notes}")

    # Clean line item prices too
    raw_items = result.get("items", [])
    cleaned_items = clean_line_items(raw_items)

    logger.info(f"Cleaned prices - total: {result.get('total')!r} -> {total_amount}, subtotal: {result.get('subtotal')!r} -> {subtotal}")

    # Save to database (separate from OCR try/except)
    db = get_db_session()
    try:
        invoice = Invoice(
            source="ocr",
            filename=file.filename,
            status="success",
            vendor_name=result.get("vendor_name"),
            invoice_date=result.get("invoice_date"),
            invoice_number=result.get("invoice_id"),
            total_amount=total_amount,
            subtotal=subtotal,
            tax_total=ocr_tax,
            gst=ocr_gst,
            pst=ocr_pst,
            hst=ocr_hst,
            tax_notes=ocr_tax_notes,
            items_json=json.dumps(cleaned_items),
            raw_ocr_data=json.dumps(result)
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        logger.info(f"Invoice saved to database with ID: {invoice.id}")
    except Exception as db_error:
        db.rollback()
        logger.error(f"Database save failed: {db_error}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(db_error)}")
    finally:
        db.close()

    # Return OUTSIDE the try/except/finally so db.close() has already run cleanly
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "data": result,
            "item_count": len(cleaned_items),
            "filename": file.filename,
        },
    )


@app.delete("/api/invoices/{invoice_id}")
async def delete_invoice(invoice_id: int):
    """Delete an invoice and its related price history and price change records"""
    db = get_db_session()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        # Delete related records first (foreign key constraints)
        db.query(PriceHistory).filter(PriceHistory.invoice_id == invoice_id).delete()
        db.query(PriceChange).filter(
            (PriceChange.invoice_id == invoice_id) | (PriceChange.previous_invoice_id == invoice_id)
        ).delete(synchronize_session=False)
        
        vendor = invoice.vendor_name
        inv_num = invoice.invoice_number
        db.delete(invoice)
        db.commit()
        
        response = {
            "success": True,
            "message": f"Deleted invoice #{inv_num} from {vendor}"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Delete failed: {e}")
        raise HTTPException(500, f"Delete failed: {str(e)}")
    finally:
        db.close()
    
    return response


@app.get("/recent-invoices")
async def get_recent_invoices(limit: int = 100):
    """Fetch recent invoice uploads from database"""
    db = get_db_session()
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
                "tax": inv.tax_total,
                "items": json.loads(inv.items_json) if inv.items_json else [],
            })
    except Exception as e:
        logger.error(f"Failed to fetch invoices: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch invoices")
    finally:
        db.close()

    return JSONResponse(
        status_code=200,
        content={"success": True, "count": len(result), "invoices": result}
    )


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
        
        # Price change info
        "price_changes_detected": invoice.price_changes_detected,
        "price_change_count": invoice.price_change_count,
        
        "items": json.loads(invoice.items_json) if invoice.items_json else []
    }


# ========== STAGE 2: PRE-CODING ENDPOINTS ==========

@app.get("/api/invoices/precoding-queue")
async def get_precoding_queue():
    """Get all invoices waiting for pre-coding (stage 1)"""
    db = get_db_session()
    try:
        invoices = db.query(Invoice).filter(
            Invoice.current_stage == 1,
            Invoice.stage_status == "captured"
        ).order_by(Invoice.created_at.desc()).all()
        
        response = {
            "success": True,
            "count": len(invoices),
            "invoices": [format_invoice(inv) for inv in invoices]
        }
    finally:
        db.close()

    return response


@app.post("/api/invoices/{invoice_id}/precode")
async def precode_invoice(invoice_id: int, body: PrecodeRequest):
    """Complete pre-coding and move to Stage 3 (Dept Review)"""
    
    if body.department not in DEPARTMENTS:
        raise HTTPException(400, f"Invalid department. Must be one of: {', '.join(DEPARTMENTS)}")
    
    db = get_db_session()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        # Update pre-coding fields
        invoice.gl_account = body.gl_account
        invoice.cost_center = body.cost_center
        invoice.department = body.department
        invoice.po_number = body.po_number
        invoice.receipt_number = body.receipt_number
        invoice.precoder = body.precoder
        invoice.precoding_date = datetime.utcnow()
        invoice.precoding_notes = body.notes
        
        # Update tax fields
        invoice.gst = body.gst
        invoice.pst = body.pst
        invoice.hst = body.hst
        invoice.qst = body.qst
        invoice.us_tax = body.us_tax
        invoice.tax_total = body.tax_total
        invoice.tax_notes = body.tax_notes
        
        # Advance to Stage 3
        invoice.current_stage = 3
        invoice.stage_status = "dept_review"
        
        # Auto-assign to department manager
        invoice.dept_reviewer = DEPARTMENT_MANAGERS[body.department]
        invoice.dept_assigned_date = datetime.utcnow()
        invoice.dept_status = "pending"
        
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = body.precoder
        
        db.commit()
        db.refresh(invoice)
        
        response = {
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

    return response


# ========== STAGE 3: DEPARTMENT REVIEW ENDPOINTS ==========

@app.get("/api/invoices/dept-queue/{reviewer_name}")
async def get_dept_queue(reviewer_name: str):
    """Get invoices pending review for a specific manager"""
    db = get_db_session()
    try:
        invoices = db.query(Invoice).filter(
            Invoice.current_stage == 3,
            Invoice.stage_status == "dept_review",
            Invoice.dept_reviewer == reviewer_name,
            Invoice.dept_status == "pending"
        ).order_by(Invoice.dept_assigned_date.desc()).all()
        
        response = {
            "success": True,
            "reviewer": reviewer_name,
            "count": len(invoices),
            "invoices": [format_invoice(inv) for inv in invoices]
        }
    finally:
        db.close()

    return response


@app.post("/api/invoices/{invoice_id}/dept-approve")
async def dept_approve_invoice(invoice_id: int, body: DeptApproveRequest):
    """Department manager approves invoice — triggers price history save + change detection"""
    db = get_db_session()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        if invoice.dept_reviewer != body.reviewer:
            raise HTTPException(403, "You are not assigned to review this invoice")
        
        # Approve the invoice
        invoice.dept_status = "approved"
        invoice.dept_review_date = datetime.utcnow()
        invoice.dept_review_notes = body.notes
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = body.reviewer
        
        # ===== STAGE 4: PRICE TRACKING (runs automatically) =====
        # 1. Save all line item prices to history
        save_price_history(db, invoice)
        
        # 2. Detect price changes vs. previous invoice from same vendor
        change_count = detect_price_changes(db, invoice)
        
        if change_count > 0:
            # Price changes found — move to Stage 4 for GM review
            invoice.current_stage = 4
            invoice.stage_status = "price_review"
            invoice.price_changes_detected = True
            invoice.price_change_count = change_count
            message = f"Invoice approved. {change_count} price change(s) detected — sent to GM for review."
        else:
            # No changes — invoice is fully complete
            invoice.stage_status = "approved"
            invoice.price_changes_detected = False
            invoice.price_change_count = 0
            message = "Invoice approved. No price changes detected."
        
        db.commit()
        db.refresh(invoice)
        
        response = {
            "success": True,
            "message": message,
            "price_changes_found": change_count,
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

    return response


@app.post("/api/invoices/{invoice_id}/dept-reject")
async def dept_reject_invoice(invoice_id: int, body: DeptRejectRequest):
    """Department manager rejects invoice - sends back to pre-coding"""
    db = get_db_session()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            raise HTTPException(404, "Invoice not found")
        
        if invoice.dept_reviewer != body.reviewer:
            raise HTTPException(403, "You are not assigned to review this invoice")
        
        # Reject and send back to Stage 2
        invoice.dept_status = "rejected"
        invoice.dept_review_date = datetime.utcnow()
        invoice.dept_review_notes = body.notes
        invoice.current_stage = 2
        invoice.stage_status = "precoding"
        invoice.last_updated = datetime.utcnow()
        invoice.last_updated_by = body.reviewer
        
        db.commit()
        db.refresh(invoice)
        
        response = {
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

    return response


# ========== STAGE 4: PRICE CHANGE REVIEW ENDPOINTS ==========

@app.get("/api/price-changes/pending")
async def get_pending_price_changes():
    """Get all pending price changes for GM review, grouped by vendor"""
    db = get_db_session()
    try:
        changes = db.query(PriceChange).filter(
            PriceChange.review_status == "pending"
        ).order_by(PriceChange.vendor_name, PriceChange.created_at.desc()).all()
        
        # Group by vendor
        vendors = {}
        for pc in changes:
            if pc.vendor_name not in vendors:
                vendors[pc.vendor_name] = {
                    "vendor_name": pc.vendor_name,
                    "department": pc.department,
                    "change_count": 0,
                    "total_impact": 0.0,
                    "changes": []
                }
            vendors[pc.vendor_name]["change_count"] += 1
            vendors[pc.vendor_name]["total_impact"] += pc.price_difference
            vendors[pc.vendor_name]["changes"].append({
                "id": pc.id,
                "item_description": pc.item_description,
                "item_sku": pc.item_sku,
                "previous_price": pc.previous_price,
                "new_price": pc.new_price,
                "price_difference": pc.price_difference,
                "percent_change": pc.percent_change,
                "previous_invoice_date": pc.previous_invoice_date,
                "new_invoice_date": pc.new_invoice_date,
                "invoice_id": pc.invoice_id,
                "previous_invoice_id": pc.previous_invoice_id,
                "review_status": pc.review_status,
            })
        
        response = {
            "success": True,
            "vendor_count": len(vendors),
            "total_changes": len(changes),
            "vendors": list(vendors.values())
        }
    finally:
        db.close()

    return response


@app.post("/api/price-changes/{change_id}/review")
async def review_price_change(change_id: int, body: PriceChangeReviewRequest):
    """GM acknowledges or escalates a single price change"""
    if body.review_status not in ("acknowledged", "escalated"):
        raise HTTPException(400, "review_status must be 'acknowledged' or 'escalated'")
    
    db = get_db_session()
    try:
        pc = db.query(PriceChange).filter(PriceChange.id == change_id).first()
        if not pc:
            raise HTTPException(404, "Price change not found")
        
        pc.review_status = body.review_status
        pc.reviewed_by = body.reviewed_by
        pc.reviewed_at = datetime.utcnow()
        pc.review_notes = body.review_notes
        
        # Check if all changes for this invoice are now reviewed
        remaining = db.query(PriceChange).filter(
            PriceChange.invoice_id == pc.invoice_id,
            PriceChange.review_status == "pending"
        ).count()
        
        # If this was the last pending change, mark invoice as complete
        if remaining == 0:
            invoice = db.query(Invoice).filter(Invoice.id == pc.invoice_id).first()
            if invoice:
                invoice.stage_status = "complete"
                invoice.last_updated = datetime.utcnow()
                invoice.last_updated_by = body.reviewed_by
                logger.info(f"All price changes reviewed for invoice {invoice.id} — marked complete")
        
        db.commit()
        
        response = {
            "success": True,
            "message": f"Price change {body.review_status}",
            "remaining_pending": remaining
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Price change review failed: {e}")
        raise HTTPException(500, f"Review failed: {str(e)}")
    finally:
        db.close()

    return response


@app.post("/api/price-changes/review-bulk")
async def review_price_changes_bulk(invoice_id: int, body: PriceChangeReviewRequest):
    """GM acknowledges or escalates ALL pending price changes for an invoice at once"""
    if body.review_status not in ("acknowledged", "escalated"):
        raise HTTPException(400, "review_status must be 'acknowledged' or 'escalated'")
    
    db = get_db_session()
    try:
        changes = db.query(PriceChange).filter(
            PriceChange.invoice_id == invoice_id,
            PriceChange.review_status == "pending"
        ).all()
        
        if not changes:
            raise HTTPException(404, "No pending price changes found for this invoice")
        
        for pc in changes:
            pc.review_status = body.review_status
            pc.reviewed_by = body.reviewed_by
            pc.reviewed_at = datetime.utcnow()
            pc.review_notes = body.review_notes
        
        # Mark invoice as complete
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice:
            invoice.stage_status = "complete"
            invoice.last_updated = datetime.utcnow()
            invoice.last_updated_by = body.reviewed_by
        
        db.commit()
        
        response = {
            "success": True,
            "message": f"{len(changes)} price change(s) {body.review_status}",
            "count": len(changes)
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Bulk review failed: {e}")
        raise HTTPException(500, f"Bulk review failed: {str(e)}")
    finally:
        db.close()

    return response


@app.get("/api/price-changes/history")
async def get_price_change_history(vendor_name: Optional[str] = None, limit: int = 50):
    """View reviewed price changes (history). Optionally filter by vendor."""
    db = get_db_session()
    try:
        query = db.query(PriceChange).filter(
            PriceChange.review_status != "pending"
        )
        if vendor_name:
            query = query.filter(PriceChange.vendor_name == vendor_name)
        
        changes = query.order_by(PriceChange.reviewed_at.desc()).limit(limit).all()
        
        result = []
        for pc in changes:
            result.append({
                "id": pc.id,
                "vendor_name": pc.vendor_name,
                "item_description": pc.item_description,
                "previous_price": pc.previous_price,
                "new_price": pc.new_price,
                "price_difference": pc.price_difference,
                "percent_change": pc.percent_change,
                "review_status": pc.review_status,
                "reviewed_by": pc.reviewed_by,
                "reviewed_at": pc.reviewed_at.isoformat() if pc.reviewed_at else None,
                "review_notes": pc.review_notes,
                "new_invoice_date": pc.new_invoice_date,
                "previous_invoice_date": pc.previous_invoice_date,
            })
        
        response = {
            "success": True,
            "count": len(result),
            "changes": result
        }
    finally:
        db.close()

    return response


# ========== CSV IMPORT ENDPOINT ==========

@app.post("/api/import-csv")
async def import_csv(file: UploadFile = File(...)):
    """Import invoices from CSV file. Groups rows by invoice_number,
    creates Invoice records with line items, and populates price_history.
    Invoices are imported as fully approved so price history is available for comparisons."""
    
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are accepted")
    
    content = await file.read()
    text = content.decode("utf-8-sig")  # handle BOM if present
    reader = csv.DictReader(io.StringIO(text))
    
    # Group rows by invoice_number
    invoice_groups = {}
    for row in reader:
        inv_num = row.get("invoice_number", "").strip()
        if not inv_num:
            continue
        if inv_num not in invoice_groups:
            invoice_groups[inv_num] = {
                "header": row,
                "items": []
            }
        invoice_groups[inv_num]["items"].append(row)
    
    if not invoice_groups:
        raise HTTPException(400, "No valid invoice data found in CSV")
    
    db = get_db_session()
    created_count = 0
    skipped_count = 0
    
    try:
        # Sort by invoice_date so price history is chronological
        sorted_invoices = sorted(
            invoice_groups.items(),
            key=lambda x: x[1]["header"].get("invoice_date", "")
        )
        
        for inv_num, group in sorted_invoices:
            header = group["header"]
            items = group["items"]
            
            # Check if invoice already exists
            existing = db.query(Invoice).filter(Invoice.invoice_number == inv_num).first()
            if existing:
                skipped_count += 1
                continue
            
            # Build line items JSON
            line_items = []
            for item in items:
                line_items.append({
                    "description": item.get("item_description", "").strip(),
                    "sku": item.get("item_code", "").strip(),
                    "quantity": float(item.get("quantity", 0) or 0),
                    "unit": item.get("uom", "").strip(),
                    "unit_price": float(item.get("unit_price_cad", 0) or 0),
                    "line_total": float(item.get("line_total_cad", 0) or 0),
                    "tax_amount": None,
                    "date": None,
                })
            
            # Parse tax amounts
            gst_amt = float(header.get("gst_amount_cad", 0) or 0)
            pst_amt = float(header.get("pst_amount_cad", 0) or 0)
            hst_amt = float(header.get("hst_amount_cad", 0) or 0)
            tax_total = float(header.get("total_tax_cad", 0) or 0)
            
            vendor_name = header.get("vendor_name", "").strip()
            department = header.get("invoice_department", "").strip().lower()
            
            # Determine department manager
            dept_reviewer = DEPARTMENT_MANAGERS.get(department)
            
            # Create invoice as fully approved (Stage 3 complete)
            invoice = Invoice(
                source="csv_import",
                filename=header.get("filename", "").strip(),
                status="success",
                vendor_name=vendor_name,
                invoice_date=header.get("invoice_date", "").strip(),
                invoice_number=inv_num,
                total_amount=float(header.get("invoice_total_cad", 0) or 0),
                subtotal=float(header.get("subtotal_cad", 0) or 0),
                items_json=json.dumps(line_items),
                
                # Tax
                gst=gst_amt if gst_amt > 0 else None,
                pst=pst_amt if pst_amt > 0 else None,
                hst=hst_amt if hst_amt > 0 else None,
                tax_total=tax_total if tax_total > 0 else None,
                tax_notes=header.get("tax_notes", "").strip() or None,
                
                # Pre-coding (auto-filled from CSV)
                department=department,
                gl_account="CSV-IMPORT",
                cost_center="CSV-IMPORT",
                precoder="CSV Import",
                precoding_date=datetime.utcnow(),
                
                # Dept review (auto-approved)
                dept_reviewer=dept_reviewer,
                dept_status="approved",
                dept_review_date=datetime.utcnow(),
                dept_review_notes="Auto-approved via CSV import",
                
                # Stage: fully approved
                current_stage=3,
                stage_status="approved",
            )
            
            db.add(invoice)
            db.flush()  # get the invoice.id
            
            # Save price history for each line item
            for item in line_items:
                desc = item.get("description", "").strip()
                if not desc:
                    continue
                ph = PriceHistory(
                    invoice_id=invoice.id,
                    vendor_name=vendor_name,
                    item_description=desc,
                    item_sku=item.get("sku") or None,
                    unit_price=item.get("unit_price"),
                    quantity=item.get("quantity"),
                    unit=item.get("unit") or None,
                    line_total=item.get("line_total"),
                    invoice_date=invoice.invoice_date,
                    department=department,
                )
                db.add(ph)
            
            created_count += 1
        
        db.commit()
        
        response = {
            "success": True,
            "message": f"Imported {created_count} invoice(s), skipped {skipped_count} duplicate(s)",
            "created": created_count,
            "skipped": skipped_count,
            "total_in_csv": len(invoice_groups)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"CSV import failed: {e}")
        raise HTTPException(500, f"CSV import failed: {str(e)}")
    finally:
        db.close()
    
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception", exc_info=True)
    return JSONResponse(status_code=500, content={"success": False, "error": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
