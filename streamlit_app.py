"""
Streamlit Frontend for Crew Management Invoice OCR
Connects to the FastAPI backend deployed on Render
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import bcrypt
# Configure the page
st.set_page_config(
    page_title="Invoice OCR - Crew Management",
    page_icon="📄",
    layout="wide"
)

# Authentication - simplified approach
credentials = {
    "usernames": {
        "ktaylor": {
            "name": "Kevin Taylor",
            "password": "$2b$12$ExZaYVK1fsbw1ZfbX30XePaWxn96p36WQoeG6Lruj3vjP6ga311W"  # admin123
        },
        "mhermani": {
            "name": "Matteo Hermani", 
            "password": "$2b$12$nBzKp3lV9L1GvH8YxR2fPujZ3BUiVTfNm9mKp4pGa6WnXqLm5Hf2"  # horses123
        }
    }
}

# Initialize session state
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'username' not in st.session_state:
    st.session_state.username = None
if 'name' not in st.session_state:
    st.session_state.name = None

# Authentication function
def check_password(username, password):
    if username in credentials["usernames"]:
        user_data = credentials["usernames"][username]
        hashed_pw = user_data["password"]
                        return bcrypt.checkpw(password.encode('utf-8'), hashed_pw.encode('utf-8'))
    return False

# Login form
if not st.session_state.authenticated:
    st.title("🔐 Login")
    
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")
        
        if submit:
            if check_password(username, password):
                st.session_state.authenticated = True
                st.session_state.username = username
                st.session_state.name = credentials["usernames"][username]["name"]
                st.rerun()
            else:
                st.error("Username/password is incorrect")
    st.stop()

# Show logout button in sidebar
with st.sidebar:
    st.write(f"Welcome **{st.session_state.name}**")
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.username = None
        st.session_state.name = None
        st.rerun()

    
    # API Configuration
    API_URL = "https://ocr-ap-app.onrender.com"
    
    # Custom CSS for better styling
    st.markdown("""
        <style>
        .main-header {
            font-size: 2.5rem;
            font-weight: bold;
            color: #1f77b4;
            margin-bottom: 1rem;
        }
        .success-box {
            padding: 1rem;
            border-radius: 0.5rem;
            background-color: #d4edda;
            border: 1px solid #c3e6cb;
            margin: 1rem 0;
        }
        .info-box {
            padding: 1rem;
            border-radius: 0.5rem;
            background-color: #d1ecf1;
            border: 1px solid #bee5eb;
            margin: 1rem 0;
        }
        .metric-card {
            background-color: #f8f9fa;
            padding: 1rem;
            border-radius: 0.5rem;
            border: 1px solid #dee2e6;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Header
    st.markdown('<div class="main-header">📄 Invoice OCR System</div>', unsafe_allow_html=True)
    st.markdown("Upload invoice PDFs and extract structured data automatically using Azure AI")
    
    # Sidebar
    with st.sidebar:
        st.header("⚙️ System Status")
        
        # Check API health
        try:
            health_response = requests.get(f"{API_URL}/health", timeout=5)
            
            if health_response.status_code == 200:
                health_data = health_response.json()
                
                if health_data.get("azure_configured"):
                    st.success("✅ API Connected")
                    st.success("✅ Azure Configured")
                else:
                    st.warning("⚠️ API Connected")
                    st.error("❌ Azure Not Configured")
            else:
                st.error("❌ API Unavailable")
        except Exception as e:
            st.error("❌ Cannot Connect to API")
            st.caption(f"Error: {str(e)}")
        
        st.divider()
        
        st.header("📚 About")
        st.markdown("""
        This app uses **Azure Document Intelligence** to automatically extract:
        - Vendor information
        - Invoice numbers & dates
        - Line items & quantities
        - Amounts & totals
        """)
        
        st.divider()
        
        st.header("🔗 Quick Links")
        st.markdown(f"[API Documentation]({API_URL}/docs)")
        st.markdown(f"[API Health Check]({API_URL}/health)")
    
    # Main content
    tab1, tab2 = st.tabs(["📤 Upload Invoice", "📋 Recent Uploads"])
    
    with tab1:
        st.header("📤 Upload Invoice PDF")
        
        # File uploader
        uploaded_file = st.file_uploader(
            "Choose a PDF invoice",
            type=["pdf"],
            help="Upload a PDF invoice to extract data automatically"
        )
        
        if uploaded_file is not None:
            # Display file info
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("📄 Filename", uploaded_file.name)
            with col2:
                file_size_kb = len(uploaded_file.getvalue()) / 1024
                st.metric("💾 File Size", f"{file_size_kb:.1f} KB")
            with col3:
                st.metric("🕐 Upload Time", datetime.now().strftime("%H:%M:%S"))
            
            # Process button
            if st.button("🚀 Process Invoice", type="primary", use_container_width=True):
                with st.spinner("Processing invoice with Azure AI..."):
                    try:
                        # Send to API
                        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "application/pdf")}
                        response = requests.post(
                            f"{API_URL}/upload-invoice-pdf",
                            files=files,
                            timeout=30
                        )
                        
                        if response.status_code == 200:
                            result = response.json()
                            
                            if result.get("success"):
                                data = result.get("data", {})
                                
                                # Success message
                                st.success("✅ Invoice processed successfully!")
                                
                                # Invoice Header Information
                                st.subheader("📋 Invoice Details")
                                col1, col2, col3, col4 = st.columns(4)
                                
                                with col1:
                                    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                                    st.markdown("**Vendor**")
                                    st.markdown(f"{data.get('vendor_name', 'N/A')}")
                                    st.markdown('</div>', unsafe_allow_html=True)
                                
                                with col2:
                                    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                                    st.markdown("**Invoice Number**")
                                    st.markdown(f"{data.get('invoice_id', 'N/A')}")
                                    st.markdown('</div>', unsafe_allow_html=True)
                                
                                with col3:
                                    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                                    st.markdown("**Invoice Date**")
                                    st.markdown(f"{data.get('invoice_date', 'N/A')}")
                                    st.markdown('</div>', unsafe_allow_html=True)
                                
                                with col4:
                                    st.markdown('<div class="metric-card">', unsafe_allow_html=True)
                                    st.markdown("**Customer**")
                                    st.markdown(f"{data.get('customer_name', 'N/A')}")
                                    st.markdown('</div>', unsafe_allow_html=True)
                                
                                st.divider()
                                
                                # Financial Summary
                                st.subheader("💰 Financial Summary")
                                col1, col2, col3 = st.columns(3)
                                
                                currency = data.get("currency", "")
                                
                                with col1:
                                    subtotal = data.get("subtotal", "N/A")
                                    st.metric("Subtotal", f"{subtotal} {currency.strip()}" if subtotal != "N/A" else "N/A")
                                
                                with col2:
                                    tax = data.get("tax_total", "N/A")
                                    st.metric("Tax", f"{tax} {currency.strip()}" if tax != "N/A" else "N/A")
                                
                                with col3:
                                    total = data.get("total", "N/A")
                                    st.metric("Total", f"{total} {currency.strip()}" if total != "N/A" else "N/A", delta=None)
                                
                                st.divider()
                                
                                # Line Items
                                st.subheader("📝 Line Items")
                                items = data.get("items", [])
                                
                                if items:
                                    st.caption(f"Found {len(items)} line items")
                                    
                                    # Convert to DataFrame for nice display
                                    items_data = []
                                    for idx, item in enumerate(items, 1):
                                        items_data.append({
                                            "#": idx,
                                            "Description": item.get("description", ""),
                                            "Quantity": item.get("quantity", ""),
                                            "Unit Price": item.get("unit_price", ""),
                                            "Line Total": item.get("line_total", ""),
                                            "Tax": item.get("tax_amount", ""),
                                            "SKU": item.get("sku", "")
                                        })
                                    
                                    df = pd.DataFrame(items_data)
                                    
                                    # Display as table
                                    st.dataframe(df, use_container_width=True, hide_index=True)
                                else:
                                    st.info("ℹ️ No line items detected in this invoice")
                                
                                st.divider()
                                
                                # Additional Information
                                with st.expander("ℹ️ Additional Information"):
                                    col1, col2 = st.columns(2)
                                    
                                    with col1:
                                        st.markdown("**Vendor Address**")
                                        vendor_addr = data.get("vendor_address", "N/A")
                                        st.text(vendor_addr if vendor_addr else "N/A")
                                        
                                        st.markdown("**Due Date**")
                                        st.text(data.get("due_date", "N/A"))
                                    
                                    with col2:
                                        st.markdown("**Customer Address**")
                                        customer_addr = data.get("customer_address", "N/A")
                                        st.text(customer_addr if customer_addr else "N/A")
                                
                                st.divider()
                                
                                # Download option
                                st.download_button(
                                    label="⬇️ Download JSON Data",
                                    data=response.text,
                                    file_name=f"invoice_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                                    mime="application/json",
                                    use_container_width=True
                                )
                                
                                # Store in session state for recent uploads
                                if 'recent_uploads' not in st.session_state:
                                    st.session_state.recent_uploads = []
                                
                                st.session_state.recent_uploads.insert(0, {
                                    "filename": uploaded_file.name,
                                    "vendor": data.get("vendor_name", "N/A"),
                                    "invoice_id": data.get("invoice_id", "N/A"),
                                    "total": f"{data.get('total', 'N/A')} {currency.strip()}",
                                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "data": data
                                })
                                
                                # Keep only last 10
                                st.session_state.recent_uploads = st.session_state.recent_uploads[:10]
                            else:
                                st.error("❌ Failed to process invoice")
                                st.error(result.get("error", "Unknown error"))
                        else:
                            st.error(f"❌ API Error: {response.status_code}")
                            try:
                                error_detail = response.json()
                                st.error(f"Details: {error_detail.get('detail', 'Unknown error')}")
                            except:
                                st.error(f"Response: {response.text}")
                    
                    except requests.exceptions.Timeout:
                        st.error("⏱️ Request timed out. The API might be slow or unavailable.")
                    except requests.exceptions.ConnectionError:
                        st.error("🔌 Cannot connect to API. Please check if the service is running.")
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")
                        st.exception(e)
    
    with tab2:
        st.header("📋 Recent Uploads")
        
        if 'recent_uploads' in st.session_state and st.session_state.recent_uploads:
            st.caption(f"Showing {len(st.session_state.recent_uploads)} recent invoices")
            
            for idx, upload in enumerate(st.session_state.recent_uploads):
                with st.expander(f"📄 {upload['filename']} - {upload['vendor']} ({upload['timestamp']})"):
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.metric("Vendor", upload['vendor'])
                    with col2:
                        st.metric("Invoice #", upload['invoice_id'])
                    with col3:
                        st.metric("Total", upload['total'])
                    
                    # Display items if available
                    items = upload['data'].get('items', [])
                    if items:
                        st.markdown("**Line Items:**")
                        for item_idx, item in enumerate(items, 1):
                            st.markdown(f"{item_idx}. {item.get('description', 'N/A')} - {item.get('line_total', 'N/A')}")
            
            # Clear history button
            if st.button("🗑️ Clear History", use_container_width=True):
                st.session_state.recent_uploads = []
                st.rerun()
        else:
            st.info("ℹ️ No recent uploads. Upload an invoice to see it here!")
    
    # Footer
    st.divider()
    st.caption("🤖 Powered by Azure Document Intelligence & FastAPI | Built with Streamlit")
