# file: azure_ocr.py
"""
Azure Document Intelligence (Form Recognizer v3) helper for invoice OCR.
Uses azure-ai-formrecognizer SDK.
"""
import os
from typing import Dict, Any, List, Optional

from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential


class AzureOCRError(Exception):
    """Custom exception for Azure OCR operations."""
    pass


def _clean_env(value: Optional[str]) -> str:
    return (value or "").strip()


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if not endpoint:
        return endpoint
    if not endpoint.startswith("http://") and not endpoint.startswith("https://"):
        endpoint = "https://" + endpoint
    if not endpoint.endswith("/"):
        endpoint += "/"
    return endpoint


def get_azure_client() -> DocumentAnalysisClient:
    endpoint = _normalize_endpoint(_clean_env(os.getenv("AZURE_DOC_INTEL_ENDPOINT")))
    key = _clean_env(os.getenv("AZURE_DOC_INTEL_KEY"))

    if not endpoint:
        raise AzureOCRError(
            "AZURE_DOC_INTEL_ENDPOINT is not set. Example:\n"
            "AZURE_DOC_INTEL_ENDPOINT=https://<resource-name>.cognitiveservices.azure.com/"
        )
    if not key:
        raise AzureOCRError("AZURE_DOC_INTEL_KEY is not set.")

    return DocumentAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))


def _field_content(field: Any, default=None):
    if field is None:
        return default

    content = getattr(field, "content", None)
    if content not in (None, ""):
        return content

    for attr in ("value", "value_string", "value_number", "value_date"):
        if hasattr(field, attr):
            v = getattr(field, attr)
            if v not in (None, ""):
                return str(v)

    return default


def _parse_items(items_field: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not items_field:
        return items

    value = getattr(items_field, "value", None)
    if not value:
        return items

    for it in value:
        obj = getattr(it, "value", None) or {}
        items.append(
            {
                "description": _field_content(obj.get("Description"), ""),
                "quantity": _field_content(obj.get("Quantity")),
                "unit": _field_content(obj.get("Unit")),
                "unit_price": _field_content(obj.get("UnitPrice")),
                "line_total": _field_content(obj.get("Amount")),
                "tax_amount": _field_content(obj.get("Tax")),
                "sku": _field_content(obj.get("ProductCode"), ""),
                "date": _field_content(obj.get("Date")),
            }
        )
    return items


def analyze_invoice_from_bytes(file_bytes: bytes) -> Dict[str, Any]:
    """
    Analyze invoice PDF using the prebuilt invoice model.
    Returns a normalized dict.
    """
    try:
        client = get_azure_client()

        # Call begin_analyze_document with document as keyword argument
        # content_type is auto-detected for byte streams
        poller = client.begin_analyze_document(
            model_id="prebuilt-invoice",
            document=file_bytes,
        )
        result = poller.result()

        if not result.documents:
            raise AzureOCRError("No invoice document detected in the PDF")

        doc = result.documents[0]
        fields = getattr(doc, "fields", {}) or {}

        normalized = {
            "vendor_name": _field_content(fields.get("VendorName"), ""),
            "invoice_id": _field_content(fields.get("InvoiceId"), ""),
            "invoice_date": _field_content(fields.get("InvoiceDate")),
            "due_date": _field_content(fields.get("DueDate")),
            "currency": _field_content(fields.get("CurrencyCode")),
            "subtotal": _field_content(fields.get("SubTotal")),
            "tax_total": _field_content(fields.get("TotalTax")),
            "total": _field_content(fields.get("InvoiceTotal")),
            "customer_name": _field_content(fields.get("CustomerName"), ""),
            "customer_address": _field_content(fields.get("CustomerAddress")),
            "vendor_address": _field_content(fields.get("VendorAddress")),
            "items": _parse_items(fields.get("Items")),
        }

        return normalized

    except Exception as e:
        raise AzureOCRError(str(e)) from e
