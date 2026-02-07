# file: models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from datetime import datetime
from database import Base

class Invoice(Base):
    __tablename__ = "invoices"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Processing status
    status = Column(String, default="success")  # success, failed, manual
    error_message = Column(Text, nullable=True)
    
    # Source
    source = Column(String)  # ocr or manual
    filename = Column(String, nullable=True)
    
    # Invoice data
    vendor_name = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    total_amount = Column(Float, nullable=True)
    subtotal = Column(Float, nullable=True)
    
    # Canadian tax breakdown
    gst = Column(Float, nullable=True)
    pst = Column(Float, nullable=True)
    hst = Column(Float, nullable=True)
    qst = Column(Float, nullable=True)
    us_tax = Column(Float, nullable=True)
    tax_total = Column(Float, nullable=True)
    tax_notes = Column(String, nullable=True)
    
    # Line items stored as JSON string
    items_json = Column(Text, nullable=True)
    
    # Raw OCR data for reference
    raw_ocr_data = Column(Text, nullable=True)
    
    # ========== APPROVAL WORKFLOW ==========
    
    # Stage tracking (1-4)
    current_stage = Column(Integer, default=1)  # 1: Captured, 2: Pre-coding, 3: Dept Review, 4: Price Review
    stage_status = Column(String, default="captured")  # captured, precoding, dept_review, price_review, approved, rejected
    
    # Stage 2: Pre-coding
    gl_account = Column(String, nullable=True)
    cost_center = Column(String, nullable=True)
    department = Column(String, nullable=True)
    po_number = Column(String, nullable=True)
    receipt_number = Column(String, nullable=True)
    precoder = Column(String, nullable=True)
    precoding_date = Column(DateTime, nullable=True)
    precoding_notes = Column(Text, nullable=True)
    
    # Stage 3: Department Review
    dept_reviewer = Column(String, nullable=True)
    dept_assigned_date = Column(DateTime, nullable=True)
    dept_review_date = Column(DateTime, nullable=True)
    dept_review_notes = Column(Text, nullable=True)
    dept_status = Column(String, default="pending")
    
    # Stage 4: Price Review tracking
    price_changes_detected = Column(Boolean, default=False)
    price_change_count = Column(Integer, default=0)
    
    # Audit trail
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_updated_by = Column(String, nullable=True)


class PriceHistory(Base):
    """Stores every line item price for historical comparison.
    Populated each time an invoice is approved (end of Stage 3)."""
    __tablename__ = "price_history"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Link to source invoice
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    
    # Vendor + item identification (used for matching)
    vendor_name = Column(String, nullable=False, index=True)
    item_description = Column(String, nullable=False, index=True)
    item_sku = Column(String, nullable=True)
    
    # Pricing
    unit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    line_total = Column(Float, nullable=True)
    
    # Invoice context
    invoice_date = Column(String, nullable=True)
    department = Column(String, nullable=True)


class PriceChange(Base):
    """Flagged price differences between consecutive invoices from the same vendor.
    Created automatically after Stage 3 approval. Reviewed by GM in Stage 4."""
    __tablename__ = "price_changes"
    
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Which invoice triggered this flag
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    
    # The previous invoice we compared against
    previous_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    
    # Item identification
    vendor_name = Column(String, nullable=False, index=True)
    item_description = Column(String, nullable=False)
    item_sku = Column(String, nullable=True)
    department = Column(String, nullable=True)
    
    # Price comparison
    previous_price = Column(Float, nullable=False)
    new_price = Column(Float, nullable=False)
    price_difference = Column(Float, nullable=False)  # new - old (positive = increase)
    percent_change = Column(Float, nullable=False)     # e.g., 11.8 for 11.8% increase
    
    # Previous invoice context
    previous_invoice_date = Column(String, nullable=True)
    new_invoice_date = Column(String, nullable=True)
    
    # GM Review
    review_status = Column(String, default="pending")  # pending, acknowledged, escalated
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)