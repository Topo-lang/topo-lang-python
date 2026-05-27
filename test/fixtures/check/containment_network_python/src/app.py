import socket

def fetch_data(endpoint):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("localhost", 8080))
    s.close()
    return 0
