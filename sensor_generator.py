import random

def get_sensor_data():
    return {
        "temperature": random.randint(40, 120),
        "vibration": random.randint(5, 40),
        "current": random.randint(2, 15)
    }
