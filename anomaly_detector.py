def detect_anomaly(data):
    return (
        data["temperature"] > 80 or
        data["vibration"] > 25 or
        data["current"] > 10
    )
