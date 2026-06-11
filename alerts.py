from datetime import datetime
from pathlib import Path

def create_alert(message):
    Path("logs").mkdir(exist_ok=True)

    with open("logs/alerts.log", "a") as f:
        f.write(f"{datetime.now()} : {message}\n")

    return message
