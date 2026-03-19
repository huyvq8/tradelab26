
import datetime, json, os

LOG = "storage/trade_log.json"

def log_trades(trades):

    entry = {
        "time": str(datetime.datetime.utcnow()),
        "trades": trades
    }

    os.makedirs("storage", exist_ok=True)

    if os.path.exists(LOG):
        data = json.load(open(LOG))
    else:
        data = []

    data.append(entry)
    json.dump(data, open(LOG,"w"), indent=2)
