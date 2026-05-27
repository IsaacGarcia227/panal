from flask import Flask, request, jsonify
import csv
import os

app = Flask(__name__)

BEE_HISTORY = os.path.join(os.path.dirname(__file__),
             "..", "bee-smart-iot", "bee_history.csv")

def init_csv():
    if not os.path.exists(BEE_HISTORY) or os.path.getsize(BEE_HISTORY) == 0:
        with open(BEE_HISTORY, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(["timestamp", "peso_kg", "temperatura_c",
                                    "humedad_pct", "presion_hpa", "label"])

@app.route('/data', methods=['POST'])
def receive_data():
    try:
        data = request.get_json()
        ip = request.remote_addr
        print(f"[POST /data] desde {ip} | peso={data.get('peso')} | temp={data.get('temperatura')} | ts={data.get('timestamp','')[:19]}")

        peso        = data.get('peso', 0)
        temperatura = data.get('temperatura', 0)
        humedad     = data.get('humedad', 0)
        presion     = data.get('presion', 0)
        timestamp   = data.get('timestamp', '')
        # Guardar en bee_history.csv (formato del dashboard IA)
        with open(BEE_HISTORY, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp,
                f"{peso:.3f}",
                f"{temperatura:.2f}",
                f"{humedad:.2f}",
                f"{presion:.2f}",
                0          # label=0 placeholder; el modelo predice el estado real
            ])

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == '__main__':
    init_csv()
    print("Servidor corriendo en http://0.0.0.0:8081")
    print("bee_history:   " + os.path.abspath(BEE_HISTORY))
    app.run(host='0.0.0.0', port=8081, debug=False)