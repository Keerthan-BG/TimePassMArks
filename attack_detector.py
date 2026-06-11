def detect_attack(data):
    temp = data["temperature"]
    vib = data["vibration"]
    curr = data["current"]

    return temp < 70 and vib > 25 and curr > 10
