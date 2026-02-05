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
