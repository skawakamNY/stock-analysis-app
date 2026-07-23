import os
import logging
from utils.pdf_generator import save_reports_to_pdf

logger = logging.getLogger("PDFGeneratorTool")

def generate_pdf_report_tool(ticker: str, company_name: str, state: dict) -> str:
    """
    Generates a PDF report from the accumulated state of all agent reports.
    
    Args:
        ticker (str): The stock ticker symbol.
        company_name (str): The name of the company.
        state (dict): The shared state containing all the agent reports.
        
    Returns:
        str: The file path to the generated PDF file.
    """
    try:
        pdf_path = save_reports_to_pdf(ticker, company_name, state)
        logger.info(f"PDF report successfully generated via tool: {pdf_path}")
        return pdf_path
    except Exception as e:
        logger.error(f"Failed to generate PDF report via tool: {e}")
        raise e
