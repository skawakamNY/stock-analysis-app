# Set PYTHONPATH to include the app directory
$env:PYTHONPATH="app"

# Run the standalone Document Search MCP Server
Write-Host "Starting Standalone Document Search MCP Server on http://127.0.0.1:8010/sse..."
.venv/Scripts/python app/mcp_server/document_search.py
