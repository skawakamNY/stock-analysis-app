@echo off
set PYTHONPATH=app
echo Starting Standalone Document Search MCP Server on http://127.0.0.1:8010/sse...
.venv\Scripts\python.exe app/mcp_server/document_search.py
