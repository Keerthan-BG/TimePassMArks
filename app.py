from flask import Flask, render_template

app = Flask(__name__)


def render_dashboard(data, anomaly=False, attack=False):

    return render_template(
        "index.html",
        data=data,
        anomaly=anomaly,
        attack=attack
    )


@app.route("/")
def home():

    return render_dashboard(
        {
            "temperature": 50,
            "vibration": 10,
            "current": 4
        }
    )


@app.route("/normal")
def normal():

    return render_dashboard(
        {
            "temperature": 50,
            "vibration": 10,
            "current": 4
        }
    )


@app.route("/fault")
def fault():

    return render_dashboard(
        {
            "temperature": 110,
            "vibration": 30,
            "current": 12
        },
        anomaly=True
    )


@app.route("/attack")
def attack():

    return render_dashboard(
        {
            "temperature": 60,  # manipulated value
            "vibration": 30,
            "current": 12
        },
        attack=True
    )


if __name__ == "__main__":
    app.run(debug=True)