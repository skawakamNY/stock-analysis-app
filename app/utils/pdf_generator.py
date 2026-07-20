import os
import datetime
from fpdf import FPDF

class PDFReport(FPDF):
    def header(self):
        self.set_font('helvetica', 'B', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, 'Equity Research Consensus Report', 0, 0, 'R')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')

def save_reports_to_pdf(ticker: str, company_name: str, state: dict) -> str:
    pdf = PDFReport()
    pdf.alias_nb_pages()
    pdf.set_margins(15, 15, 15)
    
    # Cover / Header page
    pdf.add_page()
    pdf.set_font("helvetica", "B", 20)
    pdf.set_text_color(10, 25, 47)
    pdf.cell(0, 15, f"{company_name} ({ticker})", ln=True, align="L")
    
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pdf.cell(0, 8, f"Consensus Investment Report | Generated: {date_str}", ln=True, align="L")
    pdf.ln(5)
    
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(5)
    
    # Render basic summary details
    pdf.set_font("helvetica", "B", 11)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(45, 8, "Current Price:", ln=False)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, str(state.get("current_price", "$0.00")), ln=True)
    
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(45, 8, "Market Capitalization:", ln=False)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, str(state.get("market_cap", "N/A")), ln=True)
    
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(45, 8, "Shares Outstanding:", ln=False)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, str(state.get("shares_outstanding", "N/A")), ln=True)
    
    pdf.set_font("helvetica", "B", 11)
    pdf.cell(45, 8, "Forward P/E:", ln=False)
    pdf.set_font("helvetica", "", 11)
    pdf.cell(0, 8, str(state.get("forward_pe", "N/A")), ln=True)
    pdf.ln(10)
    
    # List of reports to print
    report_keys = [
        ("Executive Investment Committee Decision", "committee_decision"),
        ("Investment Thesis & Summary", "investment_summary"),
        ("Business & Operations Research", "research_report"),
        ("Financial Statement Analysis", "financial_report"),
        ("Investment Risk Assessment", "risk_report"),
        ("Latest News & Public Sentiment Analysis", "latest_news_report"),
        ("Valuation & Margin of Safety Analysis", "valuation_report")
    ]
    
    for title, key in report_keys:
        content = state.get(key, "")
        if not content:
            continue
            
        pdf.add_page()
        pdf.set_font("helvetica", "B", 14)
        pdf.set_text_color(10, 25, 47)
        pdf.cell(0, 10, title, ln=True, align="L")
        pdf.ln(3)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(5)
        
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(50, 50, 50)
        
        # Clean text to prevent FPDF encode errors for special characters
        cleaned_content = (
            content.replace("’", "'")
            .replace("‘", "'")
            .replace("“", '"')
            .replace("”", '"')
            .replace("—", "-")
            .replace("–", "-")
            .replace("\u2022", "* ")
            .replace("\u201c", '"')
            .replace("\u201d", '"')
            .replace("\u2019", "'")
            .replace("\u2013", "-")
            .replace("\u2014", "-")
        )
        
        # Convert utf-8 string to latin-1 to avoid fpdf unicode characters errors
        # (fpdf default fonts use latin-1 encoding map)
        encoded_content = cleaned_content.encode("latin-1", "replace").decode("latin-1")
        
        pdf.multi_cell(0, 5, encoded_content)
        pdf.ln(5)
        
    # Get app directory path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docs_dir = os.path.join(base_dir, "documents")
    os.makedirs(docs_dir, exist_ok=True)
    
    # yyyymmddhhss format: year-month-day-hour-minute-second (using standard %Y%m%d%H%M%S)
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{ticker}_{timestamp}.pdf"
    file_path = os.path.join(docs_dir, filename)
    
    pdf.output(file_path)
    return file_path
