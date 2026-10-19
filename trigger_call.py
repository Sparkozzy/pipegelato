import requests

res = requests.post("http://127.0.0.1:8000/call", json={"numero": "+5547997804224", "nome": "Ivan"})
print("STATUS:", res.status_code)
print("RESPONSE:", res.json())
