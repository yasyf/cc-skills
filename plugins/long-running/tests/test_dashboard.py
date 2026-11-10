import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from drive import dashboard

NET = ("box", ["100.64.0.1", "fd7a::1"])


def test_links_lead_with_the_tailnet_label() -> None:
    assert dashboard.links_of(41330, NET) == ["http://box:41330/", "http://127.0.0.1:41330/"]
    assert dashboard.links_of(41330, None) == ["http://127.0.0.1:41330/"]


def test_page_answers_its_own_tailnet_names_and_refuses_any_other_host() -> None:
    board = dashboard.Board("demo", [])
    board.version, board.body = 1, b"{}"
    server = ThreadingHTTPServer((dashboard.LOOPBACK, 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = dashboard.handler(port, NET, board)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    statuses = {}
    for host in ("127.0.0.1", "box", "100.64.0.1", "[fd7a::1]", "box.tail1.ts.net", "evil.example"):
        connection = http.client.HTTPConnection(dashboard.LOOPBACK, port, timeout=5)
        connection.request("GET", "/api/view", headers={"Host": f"{host}:{port}"})
        statuses[host] = connection.getresponse().status
        connection.close()
    server.shutdown()
    assert statuses == {
        "127.0.0.1": 200,
        "box": 200,
        "100.64.0.1": 200,
        "[fd7a::1]": 200,
        "box.tail1.ts.net": 403,
        "evil.example": 403,
    }
