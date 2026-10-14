class Mailer:
    def __init__(self, client):
        self.client = client

    def send(self, to: str, subject: str, html: str, dedupe_key: str) -> None:
        self.client.post("/messages", json={"to": to, "subject": subject, "html": html, "dedupe_key": dedupe_key})
