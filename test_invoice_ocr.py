"""
Test script for Azure Document Intelligence invoice OCR.

Usage:
    python test_invoice_ocr.py path/to/invoice.pdf
"""
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

from azure_ocr import analyze_invoice_from_bytes, AzureOCRError


def test_invoice_ocr(pdf_path: str):
    """Test invoice OCR with a local PDF file."""

    # Load environment variables
    load_dotenv()

    pdf_file = Path(pdf_path)

    if not pdf_file.exists():
        print(f"❌ Error: File not found: {pdf_path}")
        return False

    if not pdf_file.suffix.lower() == ".pdf":
        print(f"❌ Error: File must be a PDF: {pdf_path}")
        return False

    print("=" * 70)
    print("Testing Azure Document Intelligence Invoice OCR")
    print("=" * 70)
    print(f"\n📄 File: {pdf_file.name}")
    print(f"📏 Size: {pdf_file.stat().st_size:,} bytes")
    print("\n⏳ Analyzing invoice with Azure...")
    print("-" * 70)

    try:
        with open(pdf_file, "rb") as f:
            pdf_bytes = f.read()

        result = analyze_invoice_from_bytes(pdf_bytes)

        print("\n✅ Analysis Complete!\n")

        print("📋 INVOICE HEADER")
        print("-" * 70)
        print(f"Vendor:          {result.get('vendor_name')}")
        print(f"Invoice Number:  {result.get('invoice_id')}")
        print(f"Invoice Date:    {result.get('invoice_date')}")
        print(f"Due Date:        {result.get('due_date')}")
        print(f"Customer:        {result.get('customer_name')}")

        print(f"\n💰 AMOUNTS")
        print("-" * 70)
        currency = result.get("currency") or ""
        print(f"Subtotal:        {result.get('subtotal')} {currency}")
        print(f"Tax:             {result.get('tax_total')} {currency}")
        print(f"Total:           {result.get('total')} {currency}")

        print(f"\n📦 LINE ITEMS ({len(result.get('items') or [])} items)")
        print("-" * 70)

        items = result.get("items") or []
        if items:
            for i, item in enumerate(items, 1):
                print(f"\nItem {i}:")
                print(f"  Description:   {item.get('description')}")
                if item.get("sku"):
                    print(f"  SKU:           {item.get('sku')}")
                if item.get("quantity"):
                    print(f"  Quantity:      {item.get('quantity')} {item.get('unit') or ''}")
                if item.get("unit_price"):
                    print(f"  Unit Price:    {item.get('unit_price')}")
                if item.get("line_total"):
                    print(f"  Line Total:    {item.get('line_total')}")
                if item.get("tax_amount"):
                    print(f"  Tax:           {item.get('tax_amount')}")
        else:
            print("  No line items detected")

        print("\n" + "=" * 70)
        print("✅ Test completed successfully!")
        print("=" * 70)

        # Save JSON (minus raw)
        output_file = pdf_file.with_suffix(".json")
        result_copy = dict(result)
        result_copy.pop("raw", None)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result_copy, f, indent=2, default=str)

        print(f"\n💾 Full result saved to: {output_file}")

        return True

    except AzureOCRError as e:
        print(f"\n❌ Azure OCR Error: {e}")
        return False

    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_invoice_ocr.py <path_to_invoice.pdf>")
        sys.exit(1)

    ok = test_invoice_ocr(sys.argv[1])
    sys.exit(0 if ok else 1)
