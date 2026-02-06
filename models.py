from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
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
    tax = Column(Float, nullable=True)
    
    # Line items stored as JSON string
    items_json = Column(Text, nullable=True)
    
    # Raw OCR data for reference
    raw_ocr_data = Column(Text, nullable=True)
    
    # ========== APPROVAL WORKFLOW ==========
    
    # Stage tracking (1-3 for now)
    current_stage = Column(Integer, default=1)  # 1: Captured, 2: Pre-coding, 3: Dept Review
    stage_status = Column(String, default="captured")  # captured, precoding, dept_review, approved, rejected
    
    # Stage 2: Pre-coding
    gl_account = Column(String, nullable=True)  # General Ledger account code
    cost_center = Column(String, nullable=True)  # Cost center code
    department = Column(String, nullable=True)  # grocery, dairy, meat, cosmetics, pets, produce
    po_number = Column(String, nullable=True)  # Purchase Order number
    receipt_number = Column(String, nullable=True)  # Receipt/receiving doc number
    precoder = Column(String, nullable=True)  # Who did the pre-coding
    precoding_date = Column(DateTime, nullable=True)  # When pre-coding completed
    precoding_notes = Column(Text, nullable=True)  # Notes from pre-coder
    
    # Stage 3: Department Review
    dept_reviewer = Column(String, nullable=True)  # Assigned department manager (Kevin/Matteo)
    dept_assigned_date = Column(DateTime, nullable=True)  # When assigned to dept manager
    dept_review_date = Column(DateTime, nullable=True)  # When dept manager acted
    dept_review_notes = Column(Text, nullable=True)  # Manager's notes
    dept_status = Column(String, default="pending")  # approved, pending, rejected
    
    # Audit trail
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_updated_by = Column(String, nullable=True)
