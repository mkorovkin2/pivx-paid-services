"""MCP stdio adapter. It never invokes the wallet or spends funds."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .config import safe_url
from .crypto import canonical
from .errors import ServiceError, require
from .sources import NoRedirect


def tool(name, description, properties, required=(), read_only=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}}


TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
TOOLS = [
    tool("list_services", "List services, USD reference prices, payment and refund policies. No payment is sent.", {}),
    tool("get_quote", "Get a ten-minute PIV quote for available data. No payment is sent.",
         {"service_id": {"type": "string", "enum": ["analytics", "digest"]}, "product": {"type": "string", "enum": ["single", "week"]}}, ["service_id"]),
    tool("create_invoice", "Create an idempotent invoice. Pay its amount_to_send_piv via pivx_send with from=private, address and the exact memo returned. Fees are additional. This tool never sends funds.",
         {"quote_id": TEXT, "request_id": TEXT, "use_credit": {"type": "boolean"}}, ["quote_id", "request_id"], False),
    tool("check_invoice", "Check confirmations, credit and access. Poll at most once every 10 seconds. Keep access_token private.",
         {"invoice_id": TEXT}, ["invoice_id"]),
    tool("call_service", "Consume a paid invoice. Reuse request_id for retries; a single purchase permits one distinct request. The same request returns its stored result after restart.",
         {"invoice_id": TEXT, "service_id": {"type": "string", "enum": ["analytics", "digest"]}, "request_id": TEXT,
          "parameters": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["PIVX", "XMR", "ZEC", "agent-payments"]}}, "additionalProperties": False}},
         ["invoice_id", "service_id", "request_id"], False),
]


class Client:
    def __init__(self, base_url, state_path, session_token=None):
        self.base_url, self.state_path = safe_url(base_url), Path(state_path)
        self.token = session_token

    def request(self, method, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.base_url + path, data=canonical(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            with build_opener(NoRedirect).open(request, timeout=45) as response:
                raw = response.read(4_194_305)
                require(len(raw) <= 4_194_304, 502, "response_too_large", "Service response exceeds limit")
                return json.loads(raw)
        except HTTPError as error:
            try:
                raw = error.read(16384)
                error.close()
                result = json.loads(raw)["error"]
                raise ServiceError(error.code, result["code"], result["message"]) from None
            except (ValueError, KeyError, TypeError):
                raise ServiceError(502, "backend_error", "Backend returned an invalid error response") from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise ServiceError(503, "backend_unavailable", "Paid-services backend is unavailable") from None

    def session(self):
        if self.token:
            return self.token
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.state_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            require(not os.fstat(handle.fileno()).st_mode & 0o077, 500, "session_permissions", "MCP session file must be owner-only")
            raw = handle.read()
            if raw:
                try:
                    state = json.loads(raw)
                    require(state["base_url"] == self.base_url, 409, "session_backend_changed", "Use a separate session file for each backend")
                    self.token = state["session_token"]
                except (ValueError, KeyError, TypeError):
                    raise ServiceError(500, "session_invalid", "MCP session file is invalid; preserve it for recovery") from None
            else:
                self.token = self.request("POST", "/v1/sessions", {})["session_token"]
                handle.write(canonical({"base_url": self.base_url, "session_token": self.token}))
                handle.flush()
                os.fsync(handle.fileno())
        return self.token

    def invoke(self, name, arguments):
        selected = next((tool for tool in TOOLS if tool["name"] == name), None)
        require(selected is not None, 400, "unknown_tool", "Unknown tool")
        schema = selected["inputSchema"]
        require(isinstance(arguments, dict) and set(arguments) <= set(schema["properties"]) and set(schema["required"]) <= set(arguments),
                400, "invalid_arguments", "Missing or unknown tool arguments")
        for key, value in arguments.items():
            prop = schema["properties"][key]
            kind = prop["type"]
            require((kind == "string" and isinstance(value, str) and 1 <= len(value) <= 128)
                    or (kind == "boolean" and type(value) is bool) or (kind == "object" and isinstance(value, dict)),
                    400, "invalid_arguments", "Invalid argument type or length")
            require("enum" not in prop or value in prop["enum"], 400, "invalid_arguments", "Invalid argument value")
        if name == "list_services":
            return self.request("GET", "/v1/services")
        token = self.session()
        if name == "get_quote":
            return self.request("POST", "/v1/quotes", arguments, token)
        if name == "create_invoice":
            return self.request("POST", "/v1/invoices", arguments, token)
        if name == "check_invoice":
            return self.request("GET", "/v1/invoices/" + arguments["invoice_id"], token=token)
        invoice = self.request("GET", "/v1/invoices/" + arguments["invoice_id"], token=token)
        require(invoice.get("service_id") == arguments["service_id"], 403, "wrong_service", "Invoice is scoped to another service")
        require("access_token" in invoice, 402, "invoice_unpaid", "Invoice is not paid or access has expired")
        return self.request("POST", "/v1/services/" + arguments["service_id"] + "/call",
                            {"request_id": arguments["request_id"], "parameters": arguments.get("parameters", {})}, invoice["access_token"])


def bounded_lines(stream):
    while True:
        raw = stream.readline(65537)
        if not raw:
            return
        if len(raw) > 65536 and not raw.endswith("\n"):
            tail = raw
            while tail and not tail.endswith("\n"):
                tail = stream.readline(65537)
        yield raw


def serve(client, input_stream=sys.stdin, output_stream=sys.stdout, tools=None, name="pivx-services-mcp", instructions=None):
    tools = TOOLS if tools is None else tools
    initialized = False
    for raw in bounded_lines(input_stream):
        identifier = None
        try:
            if len(raw) > 65536:
                raise ServiceError(-32600, "invalid_request", "Message exceeds 64 KiB")
            try:
                request = json.loads(raw)
            except ValueError:
                raise ServiceError(-32700, "parse_error", "Invalid JSON") from None
            require(isinstance(request, dict) and request.get("jsonrpc") == "2.0" and isinstance(request.get("method"), str),
                    -32600, "invalid_request", "Invalid JSON-RPC request")
            identifier = request.get("id")
            require(identifier is None or type(identifier) in (str, int), -32600, "invalid_request", "Invalid request id")
            method, params = request["method"], request.get("params", {})
            require(isinstance(params, dict), -32602, "invalid_params", "params must be an object")
            if identifier is None:
                # Notifications, including initialized/cancelled, never receive responses.
                continue
            if method == "initialize":
                version = params.get("protocolVersion")
                result = {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18",
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": name, "version": "0.1.0"},
                          "instructions": instructions or "Discover, quote and invoice here; pay testnet6 invoices separately using the dedicated testnet Core wallet tool. Upstream Agent Kit is mainnet-only. Never pay in simulation mode. Reuse request_id on retries."}
                initialized = True
            elif method == "ping":
                result = {}
            else:
                require(initialized, -32002, "not_initialized", "Initialize the MCP connection first")
                if method == "tools/list":
                    result = {"tools": tools}
                elif method == "tools/call":
                    try:
                        value = client.invoke(params.get("name"), params.get("arguments", {}))
                        result = {"content": [{"type": "text", "text": canonical(value)}], "isError": False}
                    except ServiceError as error:
                        result = {"content": [{"type": "text", "text": canonical({"error": {"code": error.code, "message": error.message}})}], "isError": True}
                else:
                    raise ServiceError(-32601, "method_not_found", "Unknown MCP method")
            response = {"jsonrpc": "2.0", "id": identifier, "result": result}
        except ServiceError as error:
            response = {"jsonrpc": "2.0", "id": identifier, "error": {"code": error.status, "message": error.message}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32603, "message": "Internal MCP error"}}
        output_stream.write(canonical(response) + "\n")
        output_stream.flush()


def main():
    parser = argparse.ArgumentParser(description="PIVX services MCP server (stdio; never spends funds)")
    parser.add_argument("--url", default=os.environ.get("PIVX_SERVICES_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--session-file", default=os.environ.get("PIVX_SERVICES_SESSION_FILE", "data/mcp-session.json"))
    args = parser.parse_args()
    serve(Client(args.url, args.session_file, os.environ.get("PIVX_SERVICES_SESSION_TOKEN")))


if __name__ == "__main__":
    main()
