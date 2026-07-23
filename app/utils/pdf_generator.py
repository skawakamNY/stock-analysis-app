import os
import datetime
import re
from fpdf import FPDF

def render_pdf_table(pdf, table_rows):
    # Filter out separator rows
    valid_rows = []
    for row in table_rows:
        cells = [c.strip() for c in row.split("|")[1:-1]]
        if all(re.match(r"^:?\-+:?$", cell) for cell in cells if cell):
            continue
        valid_rows.append(cells)
        
    if not valid_rows:
        return
        
    num_cols = max(len(row) for row in valid_rows)
    if num_cols == 0:
        return
        
    page_width = 180
    if num_cols > 1:
        first_col_width = 75
        other_col_width = (page_width - first_col_width) / (num_cols - 1)
        col_widths = [first_col_width] + [other_col_width] * (num_cols - 1)
    else:
        col_widths = [page_width]
        
    pdf.ln(2)
    for row_idx, cells in enumerate(valid_rows):
        is_header = (row_idx == 0)
        if is_header:
            pdf.set_font("helvetica", "B", 8)
            pdf.set_fill_color(240, 240, 240)
            pdf.set_text_color(10, 25, 47)
        else:
            pdf.set_font("helvetica", "", 8)
            pdf.set_fill_color(255, 255, 255)
            pdf.set_text_color(50, 50, 50)
            
        for col_idx in range(num_cols):
            val = cells[col_idx] if col_idx < len(cells) else ""
            w = col_widths[col_idx]
            align = "L" if col_idx == 0 else "R"
            clean_val = val.encode("latin-1", "replace").decode("latin-1")
            # Border 1 is full box, fill True uses background color
            pdf.cell(w, 7, clean_val, border=1, ln=(1 if col_idx == num_cols - 1 else 0), align=align, fill=True)
            
    pdf.ln(4)

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
        
        # Split into lines and render each line by parsing simple markdown headings, bold text, & tables
        lines = encoded_content.split("\n")
        sanitized_lines = []
        import re
        for line in lines:
            if " "*100 in line:
                line = re.sub(r" {2,}", " ", line)
            sanitized_lines.append(line)
        lines = sanitized_lines

        in_table = False
        table_rows = []
        
        for line in lines:
            strip_line = line.strip()
            if strip_line.startswith("|") and strip_line.endswith("|"):
                if not in_table:
                    in_table = True
                    table_rows = []
                table_rows.append(strip_line)
                continue
                
            # If we were in a table and hit a non-table line, render the table
            if in_table:
                render_pdf_table(pdf, table_rows)
                in_table = False
                table_rows = []
                
            if not strip_line:
                pdf.ln(4)
                continue
                
            # Handle Headings
            if strip_line.startswith("####"):
                text = strip_line.replace("####", "").replace("**", "").strip()
                pdf.set_font("helvetica", "B", 11)
                pdf.set_text_color(10, 25, 47)
                pdf.write(6, text)
                pdf.ln(8)
                pdf.set_text_color(50, 50, 50)
            elif strip_line.startswith("###"):
                text = strip_line.replace("###", "").replace("**", "").strip()
                pdf.set_font("helvetica", "B", 12)
                pdf.set_text_color(10, 25, 47)
                pdf.write(7, text)
                pdf.ln(9)
                pdf.set_text_color(50, 50, 50)
            elif strip_line.startswith("##"):
                text = strip_line.replace("##", "").replace("**", "").strip()
                pdf.set_font("helvetica", "B", 13)
                pdf.set_text_color(10, 25, 47)
                pdf.write(8, text)
                pdf.ln(10)
                pdf.set_text_color(50, 50, 50)
            elif strip_line.startswith("#"):
                text = strip_line.replace("#", "").replace("**", "").strip()
                pdf.set_font("helvetica", "B", 14)
                pdf.set_text_color(10, 25, 47)
                pdf.write(9, text)
                pdf.ln(11)
                pdf.set_text_color(50, 50, 50)
            else:
                # Check for bullet points and replace them with a proper bullet character
                import re
                line_str = line.rstrip()
                bullet_match = re.match(r"^(\s*)([\*\-])(\s+)", line_str)
                if bullet_match:
                    indent = bullet_match.group(1)
                    space = bullet_match.group(3)
                    line_str = indent + chr(149) + space + line_str[bullet_match.end():]

                # Regular line with potential **bold** text
                parts = line_str.split("**")
                for i, part in enumerate(parts):
                    if i % 2 == 1:
                        pdf.set_font("helvetica", "B", 10)
                    else:
                        pdf.set_font("helvetica", "", 10)
                    pdf.write(5, part)
                pdf.ln(5)
                
        # If we reach the end of the loop and are still in a table, render it
        if in_table:
            render_pdf_table(pdf, table_rows)
            
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
